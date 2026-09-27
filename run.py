# pyright: reportMissingImports=false
"""The pipeline: IDX -> pre-gates -> the required gate -> rank -> Jev -> journal.

Layer 1 and 2 of the plan. Only the decision (layer 3) and the journal write (layer 4) are new
code; the data, the indicators and the entry plan all come from trading-tools.

Order matters, cheapest first. The IDX pre-gates cost nothing — they are already in the stored
snapshot — so they run before any Yahoo request. With 611 names under IDR 1,000, the flow gate is
what keeps the polite fetch count sane.

Usage:
  python3 run.py screen --limit 200        # universe -> shortlist, no API calls
  python3 run.py run --limit 200 --top 15  # ...then ask Jev and journal every answer
  python3 run.py resolve                   # fill outcomes for ENTER decisions
  python3 run.py analyze --since 7d        # this week's performance; 30d, 90d, all
"""
import argparse
import concurrent.futures
import sys

import idx
import jev
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


def ask_jev(session, passed, top):
    """Ask Jev about the shortlist and journal every answer, ENTER and SKIP alike. The SKIP rows
    are the journal's real content: they are what makes 'how often does Jev say no' answerable."""
    ranked = sorted(passed, key=_rank_key)[:top]
    print(f"asking Jev about {len(ranked)} of {len(passed)}")
    decided = 0
    for v in ranked:
        code = v["code"]
        state = screen.candidate_state(code, v)
        try:
            resp = jev.ask(jev.render(state))
        except jev.JevError as e:
            print(f"  {code}: {e}")
            continue
        answers = resp["answers"]
        jev.check(answers)
        action, p = jev.decide(answers)
        plan = None
        if action == "ENTER":
            try:
                plan = screener_plan(code)
            except Exception as e:  # noqa: BLE001
                print(f"  {code}: no entry plan ({type(e).__name__})")
        store.record(session, code, "prose", answers, action, plan, {
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
    TP2 2R. Jev never computes this — it cannot return a price."""
    from tools import screener
    return screener.plan(bars_for(code))


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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    for name in ("screen", "run"):
        p = sub.add_parser(name)
        p.add_argument("--limit", type=int, default=200, help="max candidates to fetch bars for")
        p.add_argument("--top", type=int, default=15, help="max to send to Jev")
        p.add_argument("--news", action="store_true", help="also score news (1 request per ticker)")
    sub.add_parser("resolve")
    an = sub.add_parser("analyze")
    an.add_argument("--since", default="all", help="all | 7d | 30d | 90d | YYYY-MM-DD")
    an.add_argument("--no-resolve", action="store_true", help="skip resolving pending outcomes")
    an.add_argument("--short", action="store_true", help="omit the per-decision list")
    a = ap.parse_args()

    if a.cmd == "resolve":
        resolve()          # prints its own counts; its tuple is data, not an exit code
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
        return store.print_report(store.report(since), per_decision=not a.short)
    if a.cmd not in ("screen", "run"):
        return ap.print_help()

    session, passed = screen_universe(a.limit, a.news)
    if not passed:
        return print("no candidate cleared the required gate — nothing for Jev")
    for v in sorted(passed, key=_rank_key)[:10]:
        c = v["context"]
        print(f"  {v['code']:6} {v['name'][:26]:26} close={c['close']:>8,.0f} "
              f"rsi={c['rsi']:.0f} pivot={c['pivot_high']} rank={v['rank_score']} "
              f"{','.join(v['rank_signals'])}")
    if a.cmd == "run":
        n = ask_jev(session, passed, a.top)
        print(f"journalled {n} decisions (session {session}) — "
              f"`run.py analyze --since 7d` scores them")


if __name__ == "__main__":
    sys.exit(main())
