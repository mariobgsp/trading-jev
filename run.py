# pyright: reportMissingImports=false
"""The pipeline: IDX -> pre-gates -> the required gate -> rank -> decider -> journal.

Layer 1 and 2 of the plan. Only the decision (layer 3) and the journal write (layer 4) are new
code; the data, the indicators and the entry plan all come from trading-tools.

Order matters, cheapest first. The IDX pre-gates cost nothing — they are already in the stored
snapshot — so they run before any Yahoo request. With 611 names under IDR 1,000, the flow gate is
what keeps the polite fetch count sane.

Usage:
  python3 run.py screen --limit 200        # universe -> shortlist, no API calls
  python3 run.py run --limit 200 --top 15  # ...then ask the decider and journal every answer
  python3 run.py watchlist                 # every name ever surfaced, and what it did after
  python3 run.py resolve                   # fill outcomes for ENTER decisions
  python3 run.py analyze --since 7d        # this week's performance; 30d, 90d, all
"""
import argparse
import concurrent.futures
import sys

import decider
import idx
import screen
import store
from tools import bars_for

MAX_WORKERS = 8          # trading-tools' README caps polite fetching at 8
NEWS_WINDOW = 7
# 6mo silently failed to score anything older than six months, because there were no bars left
# reaching back to the session. Resolution needs history, not just recent data.
RESOLVE_RANGE = "2y"


def pre_gates(rows, sessions):
    """The two free gates: tradable (from idx.suspended) and price under IDR 1,000, then the
    actor gate on foreign net buy. Returns (kept, funnel) so the cost of each step is visible."""
    funnel = {"listed": len(rows)}
    live = [r for r in rows if not idx.suspended(r["remarks"])]
    funnel["tradable"] = len(live)
    cheap = [r for r in live if r["close"] and 0 < r["close"] < idx.MAX_PRICE]
    funnel["under_1000"] = len(cheap)
    since = store.since_sessions(20)
    kept, no_flow = [], 0
    for r in cheap:
        hist = store.flow_history(r["code"], since)
        if not hist:
            no_flow += 1
            continue
        net = sum(h["net"] for h in hist)
        if net > 0:
            r["flow"] = {"net": net, "sessions": len(hist)}
            kept.append(r)
    funnel["foreign_buying"] = len(kept)
    funnel["no_flow_data"] = no_flow
    return kept, funnel


def _score_one(row, index_bars, with_news):
    """Bars -> categories, for one candidate. Never raises: one bad ticker must not kill a run."""
    code = row["code"]
    try:
        bars = bars_for(code)
        if not bars or len(bars) < 60:
            return None
        news = screen._news_score(code) if with_news else None
        v = screen.evaluate(code, bars, index_bars=index_bars, flow=row.get("flow"), news=news)
        if v is None:
            return None
        v["code"] = code
        v["name"] = row["name"]
        v["flow_sessions"] = (row.get("flow") or {}).get("sessions")   # 1 today, up to 20 later
        return v
    except Exception as e:  # noqa: BLE001 - a bad ticker is a skipped ticker, not a failed run
        return {"code": code, "error": f"{type(e).__name__}: {e}"}


def screen_universe(limit, with_news, workers=MAX_WORKERS):
    session, rows = idx.refresh()
    kept, funnel = pre_gates(rows, store.idx_sessions(20))
    print("  ".join(f"{k}={v}" for k, v in funnel.items()))
    pool = kept if limit is None else kept[:limit]
    store.record_scan(session, len(kept), len(pool))
    print(f"fetching bars for {len(pool)} candidates ({workers} workers)...")
    try:
        index_bars = bars_for("^JKSE")
    except Exception as e:  # noqa: BLE001 - the index only feeds descriptors 2 and 3
        print(f"  index unavailable ({type(e).__name__}), those descriptors will be None")
        index_bars = None
    out = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for v in ex.map(lambda r: _score_one(r, index_bars, with_news), pool):
            if v and "error" not in v:
                out.append(v)
            elif v:
                print(f"  skipped {v['code']}: {v['error']}")
    passed = [v for v in out if v["passed"]]
    print(f"scored {len(out)}, cleared the required gate: {len(passed)}")
    return session, passed


def _rank_key(v):
    """Rank by how many confirmation signals fired, then by volume conviction. Deterministic so
    a rerun on the same session picks the same names."""
    return (-v["rank_score"], -(v["context"]["obv_slope"] or 0), v["code"])


def ask_decider(session, passed, top, backend=None):
    """Ask the decider about the shortlist and journal every answer, ENTER and SKIP alike. The
    SKIP rows are the journal's real content: they are what makes 'how often does it say no'
    answerable. `backend=None` is whatever JEV_BACKEND selects; --backend overrides it for one
    run, and the row records which one answered so two backends can be compared later."""
    ranked = sorted(passed, key=_rank_key)[:top]
    print(f"asking the decider about {len(ranked)} of {len(passed)}")
    decided = 0
    for v in ranked:
        code = v["code"]
        # The plan comes first: the decider is asked whether the stop defines the risk, so it has
        # to be shown the stop. Pure arithmetic over bars we already hold, so it costs nothing.
        try:
            plan = screener_plan(code)
        except Exception as e:  # noqa: BLE001
            plan = None
            print(f"  {code}: no entry plan ({type(e).__name__})")
        state = screen.candidate_state(code, v, plan)
        try:
            resp = decider.ask(decider.render(state), backend=backend)
        except decider.DeciderError as e:
            print(f"  {code}: {e}")
            continue
        answers = resp["answers"]
        action, p = decider.decide(answers)
        # the journal still records a plan only where a trade was taken, so its meaning is unchanged
        store.record(session, code, "prose", answers, action,
                     plan if action == "ENTER" else None, {
            "backend": resp["backend"],
            "model": resp.get("model"),
            "required_passed": v["required_passed"],
            "rank_signals": v["rank_signals"],
            "rank_score": v["rank_score"],
            "missing": v["missing"],
            "context": v["context"],
            "flow_sessions": v.get("flow_sessions"),
        })
        decided += 1
        print(f"  {code:6} p={p:.2f} {action:5} rank={v['rank_score']} "
              f"conv={answers['conviction']['score']:.1f} risk={answers['risk']['choice']:6} "
              f"{'entry ' + str(plan['entry']) if plan else ''}")
    return decided


def screener_plan(code):
    """The YES branch's entry plan. Already implemented in trading-tools: stop 1.5xATR, TP1 1R,
    TP2 2R. The decider never computes this — it cannot return a price."""
    return screen.entry_plan(code)


def forward_perf(bars, session, horizons=(5, 10, 20)):
    """What a candidate did after it was surfaced. Forward from the signal bar only — the same
    no-lookahead rule resolve() uses, so a watchlisted name is never scored on bars that existed
    before the watchlist decision.

    Reports the return at each horizon plus the best and worst excursion. MFE/MAE matter because
    a veto can be wrong in two different ways: the name went up and the decider missed it (bad), or it
    dipped before running (not bad, just early). Return alone cannot tell those apart.
    """
    start = None
    for i, b in enumerate(bars):
        if b["t"] >= store.session_ts(session):
            start = i + 1
            break
    if start is None or start >= len(bars):
        return None
    base = bars[start - 1]["c"]
    out = {"base": base, "bars_ahead": len(bars) - start, "last": None}
    for h in horizons:
        if start + h - 1 >= len(bars):
            out[f"r{h}"] = out[f"mfe{h}"] = out[f"mae{h}"] = None
            continue
        window = bars[start:start + h]
        out[f"r{h}"] = (window[-1]["c"] / base - 1) * 100
        out[f"mfe{h}"] = (max(w["h"] for w in window) / base - 1) * 100
        out[f"mae{h}"] = (min(w["l"] for w in window) / base - 1) * 100
    out["last"] = (bars[-1]["c"] / base - 1) * 100
    return out


def _cell(perf, key, width=7):
    """One fixed-width performance cell. A helper rather than a closure over the loop variable:
    a nested def would bind it late, and this is a report that must not shift under us."""
    if perf is None or perf.get(key) is None:
        return f"{'n/a':>{width}}"
    return f"{perf[key]:+{width}.2f}"


def print_watchlist(rows, perf=True, horizons=(5, 10, 20)):
    """rows: [(lifecycle, forward_perf_or_None)]. Prints the cohort and, with perf, what each
    name did after it was put on the list."""
    if not rows:
        return print("watchlist is empty — run `run.py run` first")
    inlist = [r for r, _ in rows if r["status"] == "IN"]
    gone = [r for r, _ in rows if r["status"] == "EXCLUDED"]
    print(f"watchlist: {len(rows)} names ever surfaced — {len(inlist)} still on it, "
          f"{len(gone)} excluded")
    head = (f"  {'ticker':8} {'first':9} {'last':9} {'seen':>4} {'avgP':>5} {'ent':>3} status")
    if perf:
        head += f" {'r5':>7} {'r10':>7} {'r20':>7} {'MFE20':>7} {'MAE20':>7}"
    print(head)
    for r, p in rows:
        line = (f"  {r['ticker']:8} {r['first_seen']:9} {r['last_seen']:9} {r['seen']:4d} "
                f"{r['avg_p']:5.2f} {r['entered']:3d} {r['status']:8}")
        if perf:
            line += "".join(f" {_cell(p, k)}"
                            for k in ("r5", "r10", "r20", "mfe20", "mae20"))
        print(line)
    exits = [f"{r['ticker']}@{r['exit_session']}" for r in gone if r["exit_session"]]
    if exits:
        print("\nexcluded after being surfaced: " + ", ".join(exits))


def resolve():
    """Score every ENTER that has no outcome yet. Returns a count so the caller can report
    unresolvable ones instead of dropping them silently."""
    pending = store.unresolved()
    if not pending:
        print("no ENTER decisions waiting on an outcome")
        return 0, 0
    done = stuck = 0
    print(f"resolving {len(pending)} ENTER decisions (bars: {RESOLVE_RANGE})")
    for d in pending:
        try:
            bars = bars_for(d["ticker"].replace(".JK", ""), RESOLVE_RANGE)
        except Exception as e:  # noqa: BLE001
            stuck += 1
            print(f"  {d['ticker']:10} bars unavailable ({type(e).__name__}) — unresolvable")
            continue
        got = store.resolve(d["ticker"], d["session"], d["entry"], d["stop"], d["tp1"], bars)
        if got is None:
            stuck += 1
            print(f"  {d['ticker']:10} session {d['session']} has no forward bars — unresolvable")
            continue
        store.set_outcome(d["id"], *got)
        done += 1
        print(f"  {d['ticker']:10} {got[0]:5} {got[1]:+7.2f}% after {got[2]} bars")
    if stuck:
        print(f"resolved {done}, UNRESOLVABLE {stuck} (these are never counted as wins or losses)")
    else:
        print(f"resolved {done}")
    return done, stuck


def _watchlist_rows(honours, horizons=(5, 10, 20)):
    """Join the lifecycle to forward performance. A ticker whose bars cannot be fetched is
    reported as such rather than quietly dropped."""
    out = []
    for r in store.watchlist():
        perf = None
        if honours:
            try:
                perf = forward_perf(bars_for(r["ticker"].replace(".JK", ""), "6mo"),
                                    r["first_seen"], horizons)
            except Exception:  # noqa: BLE001 - one bad ticker must not blank the watchlist
                perf = None
        out.append((r, perf))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    for name in ("screen", "run"):
        p = sub.add_parser(name)
        p.add_argument("--limit", type=int, default=200, help="max candidates to fetch bars for")
        p.add_argument("--top", type=int, default=15, help="max to send to the decider")
        p.add_argument("--backend", choices=decider.BACKENDS, default=None,
                       help="override JEV_BACKEND for this run")
        p.add_argument("--news", action="store_true", help="also score news (1 request per ticker)")
    sub.add_parser("resolve")
    wl = sub.add_parser("watchlist")
    wl.add_argument("--no-perf", action="store_true", help="lifecycle only, no bar fetching")
    an = sub.add_parser("analyze")
    an.add_argument("--since", default="all", help="all | 7d | 30d | 90d | YYYY-MM-DD")
    an.add_argument("--no-resolve", action="store_true", help="skip resolving pending outcomes")
    an.add_argument("--short", action="store_true", help="omit the per-decision list")
    a = ap.parse_args()

    if a.cmd == "resolve":
        resolve()          # prints its own counts; its tuple is data, not an exit code
        return 0
    if a.cmd == "watchlist":
        print_watchlist(_watchlist_rows(not a.no_perf))
        return 0
    if a.cmd == "analyze":
        try:
            since = store.since_ts(a.since)
        except ValueError as e:
            print(e)
            return 2
        if not a.no_resolve:
            resolve()
            print()
        store.print_report(store.report(since), per_decision=not a.short)
        if not a.short:
            print("\ncohort — what every surfaced name did, including the ones the decider vetoed:")
            print_watchlist(_watchlist_rows(not a.no_perf))
        return 0
    if a.cmd not in ("screen", "run"):
        return ap.print_help()

    session, passed = screen_universe(a.limit, a.news)
    if not passed:
        return print("no candidate cleared the required gate — nothing for the decider")
    for v in sorted(passed, key=_rank_key)[:10]:
        c = v["context"]
        print(f"  {v['code']:6} {v['name'][:26]:26} close={c['close']:>8,.0f} "
              f"rsi={c['rsi']:.0f} pivot={c['pivot_high']} rank={v['rank_score']} "
              f"{','.join(v['rank_signals'])}")
    if a.cmd == "run":
        n = ask_decider(session, passed, a.top, a.backend)
        print(f"journalled {n} decisions (session {session}) — "
              f"`run.py analyze --since 7d` scores them")


if __name__ == "__main__":
    sys.exit(main())
