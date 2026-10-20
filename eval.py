#!/usr/bin/env python3
"""Backtest the decider against realized outcomes, on one fixed candidate set.

This is a measurement harness, not part of the pipeline. It imports screen/store/decider and
modifies none of them, so it cannot change what a live scan does.

The candidate set is built once and scored once. Every candidate is scored by its own forward
return regardless of whether the decider chose to enter it, because comparing deciders by the
rows they happened to enter compares two different populations: the one that says yes more gets
more samples, and gets the easier ones.

Two things are deliberately absent from the reconstructed state, and both are reported in the
output rather than quietly defaulted:

  foreign flow   idx_daily holds one session, so there is no history to accumulate. Every
                 candidate is evaluated with flow=None, and decider.render() states
                 "Foreign flow not available" instead of the flat 0.0 it used to invent.
  news           _news_score() fetches the last 7 days, so in a historical backtest it would
                 feed today's headlines to a decision made months ago. It is not called.

The universe is today's IDX snapshot, so a name delisted since the backtest window is absent.
That is survivorship bias and it is stated, not corrected.

Scoring is forward-only, reusing the no-lookahead rule store.resolve() already enforces: the
bar after the as-of session is the first that can count, and the as-of bar is the entry price.

Usage:
  python3 eval.py                       # pilot: 30 dates x top 4 over a 200-code pool
  python3 eval.py --dates 60 --top 5    # wider
  python3 eval.py --selfcheck           # no network, no decider: checks the scoring arithmetic
"""
import argparse
import datetime
import json
import os
import statistics
import sys

import decider
import screen
import store
import tools

HORIZON = 10          # bars; p_enter asks about "the next 10 bars"
# The numeric inputs a learned scorer is fitted on. Taken from the same candidate_state the
# prose is rendered from, so a fitted decision is made from exactly the information the decider
# was shown. foreign_net_100m_idr is deliberately absent: idx_daily holds one session, so it is
# None in every backtest row and a column that is always empty teaches nothing.
FEATURES = ("rsi", "obv_slope_20d", "distance_to_significant_high_pct",
            "rank_score", "adv20_bn", "stop_pct")
# Not a taste call: screen.s_base walks 1w/3w/1m/3m/6m, the widest being 132 bars, and it only
# evaluates a window once it has w+30 bars — then indexes a[-w-20:-w] for a baseline ATR, which is
# None until ATR(14) has warmed up. So 132+30+4 = 166 bars is the real floor, and a shorter slice
# raises TypeError inside sum() rather than skipping the window.
MIN_BARS = 170
RANGE = "2y"


def slice_asof(bars, cut):
    """Only what existed at the session being reconstructed."""
    return [b for b in bars if b["t"] <= cut]


def forward(bars, cut, horizon=HORIZON):
    """-> {"ret","mfe","mae"} in percent, or None if the horizon has not elapsed yet.

    The as-of bar is the entry price and is never itself counted: it is the close the plan is
    built on, so it can neither confirm nor refute the decision.
    """
    start = next((i for i, b in enumerate(bars) if b["t"] > cut), None)
    if start is None or start + horizon > len(bars):
        return None
    window = bars[start:start + horizon]
    base = bars[start - 1]["c"]
    return {"ret": (window[-1]["c"] / base - 1) * 100,
            "mfe": (max(w["h"] for w in window) / base - 1) * 100,
            "mae": (min(w["l"] for w in window) / base - 1) * 100}


def forward_plan(bars, cut, plan):
    """-> {"outcome","pct","r"} under the project's own rule: the first of stop or tp1 after the
    as-of bar decides, otherwise the trade is still open. This mirrors store.resolve() exactly,
    including the `b["t"] >= cut` boundary, so the backtest and the journal score a day the same
    way. `open` is not a loss and not a win — store.report() excludes it too, and so does this."""
    at = next((i for i, b in enumerate(bars) if b["t"] >= cut), None)
    if at is None:
        return None
    start = at + 1                      # the session bar itself can neither stop nor pay us
    if start >= len(bars):
        return None
    entry, stop, tp1 = plan["entry"], plan["stop"], plan["tp1"]
    for j in range(start, len(bars)):
        b = bars[j]
        if b["l"] <= stop:
            return {"outcome": "stop", "pct": (stop - entry) / entry * 100, "r": -1.0}
        if b["h"] >= tp1:
            return {"outcome": "tp1", "pct": (tp1 - entry) / entry * 100, "r": 1.0}
    last = bars[-1]["c"]
    return {"outcome": "open", "pct": (last - entry) / entry * 100, "r": (last - entry) / entry}


def plan_asof(bars):
    """The entry plan as it would have been on that date. Pure arithmetic on the sliced bars,
    so nothing from after the session can leak in. screen.entry_plan() would use today's bars."""
    from tools import screener
    return screener.plan(bars)


def session_dates(count, end=None, step=7):
    """`count` weekday sessions, `step` sessions apart, ending at `end` (YYYYMMDD)."""
    end = end or store.session_ts((store.idx_sessions() or [""])[0])
    out, seen = [], set()
    ts = end
    while len(out) < count:
        day = datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y%m%d")
        if day not in seen:
            seen.add(day)
            out.append(day)
        ts -= 86400 * step
    return list(reversed(out))


def pool(limit):
    """IDX codes from the snapshot, the only universe this project has."""
    rows = store.idx_day(store.idx_sessions()[0]) if store.idx_sessions() else []
    return [r["code"] for r in rows][:limit]


def build_set(days, codes, top, index="^JKSE"):
    """-> (candidate list, stats). Ranked exactly as run.py ranks it, so the set resembles what a
    live scan would shortlist rather than every name that clears the gates."""
    out = []
    stats = {"no_bars": 0, "no_history": 0, "unevaluable": 0, "no_forward": 0, "cleared": 0}
    errors = set()          # exception types seen in screen.evaluate, reported rather than swallowed
    idx_full = tools.bars_for(index, RANGE)
    fetched = {}

    def bars(code):
        if code not in fetched:
            try:
                fetched[code] = tools.bars_for(code, RANGE)
            except Exception:  # noqa: BLE001 - a delisted or bad code is normal
                fetched[code] = []
        return fetched[code]

    for day in days:
        cut = store.session_ts(day)
        idx = slice_asof(idx_full, cut)
        passed = []
        for code in codes:
            full = bars(code)
            if not full:
                stats["no_bars"] += 1
                continue
            asof = slice_asof(full, cut)
            if len(asof) < MIN_BARS:
                stats["no_history"] += 1
                continue
            try:
                v = screen.evaluate(code, asof, index_bars=idx, flow=None, news=None)
            except Exception as e:  # noqa: BLE001 - counted and reported, never silently dropped
                stats["unevaluable"] += 1
                errors.add(type(e).__name__)
                continue
            if v is None or not v["passed"]:
                continue
            fwd = forward(full, cut)
            if fwd is None:
                stats["no_forward"] += 1
                continue          # no forward truth yet, so it cannot be scored
            passed.append((code, v, asof, fwd, full))
        stats["cleared"] += len(passed)
        # the same key run.py uses, inlined: (-rank_score, -obv_slope, code)
        passed.sort(key=lambda c: (-c[1]["rank_score"],
                                   -(c[1]["context"]["obv_slope"] or 0), c[0]))
        for code, v, asof, fwd, full in passed[:top]:
            out.append({"day": day, "code": code, "verdict": v, "bars": asof, "full": full,
                        "cut": cut, "fwd": fwd})
        print(f"  {day}: {len(passed)} cleared the gates, scored the top {min(top, len(passed))}")
    return out, stats, errors


def ask_all(candidates, backend=None):
    """One decision per candidate, from the configured decider. A failure is recorded against
    that candidate and never aborts the run — the count of failures is reported, not hidden."""
    for c in candidates:
        plan = plan_asof(c["bars"])
        c["plan"] = plan
        c["plan_fwd"] = forward_plan(c["full"], c["cut"], plan) if plan else None
        c["state"] = screen.candidate_state(c["code"], c["verdict"], plan)
        c["prose"] = decider.render(c["state"])
        try:
            resp = decider.ask(c["prose"], backend=backend)
            c["answers"] = resp["answers"]
            c["model"] = resp.get("model")
            c["backend"] = resp["backend"]
            c["error"] = None
        except decider.DeciderError as e:
            c["answers"] = None
            c["error"] = str(e)
    return candidates


def heads(answers):
    """Every head the decider produced, as scalars a fitted selector can use.

    The first pilot only kept the verdict head (via enter_probability). The two noul questions,
    the conviction rubric and the risk split were computed on every call and discarded — four
    unmeasured signals. They cost nothing extra to read: the model is already producing them.
    """
    return {
        "verdict_enter": decider.enter_probability(answers),
        "noul_p_enter": answers["p_enter"]["noul"],
        "noul_momentum": answers["momentum_confirmed"]["noul"],
        "conviction": answers["conviction"]["score"],
        "risk_high": answers["risk"]["probabilities"].get("high", 0.0),
        "risk_low": answers["risk"]["probabilities"].get("low", 0.0),
    }


def score(candidates):
    """Calibration of p_enter against whether the trade was actually profitable."""
    rows = [c for c in candidates if c.get("answers")]
    out = []
    for c in rows:
        p = decider.enter_probability(c["answers"])
        action, _ = decider.decide(c["answers"])
        f = c["fwd"]
        pf = c.get("plan_fwd")
        out.append({"day": c["day"], "code": c["code"], "p_enter": p, "action": action,
                    "won": 1 if f["ret"] > 0 else 0, "ret": f["ret"],
                    "mfe": f["mfe"], "mae": f["mae"],
                    "features": {k: c["state"].get(k) for k in FEATURES},
                    "heads": heads(c["answers"]),
                    # the screen's full 23-category vector, not just the 6 summary numbers. These
                    # are the signals the screen was built around; a fitted selector may extract
                    # more from them than the decider did.
                    "cat": {k: int(bool(v)) for k, v in (c["verdict"].get("categories") or {}).items()},
                    "plan_outcome": pf["outcome"] if pf else None,
                    "plan_won": (1 if pf["outcome"] == "tp1" else 0) if pf else None,
                    "plan_r": pf["r"] if pf else None})
    return out


def brier(rows):
    """Mean squared error of p_enter against the realised outcome. 0 is perfect, 0.25 is what
    always saying 0.5 scores, so the number only means something next to that line."""
    if not rows:
        return None
    return sum((r["p_enter"] - r["won"]) ** 2 for r in rows) / len(rows)


def buckets(rows, step=0.1):
    table = {}
    for r in rows:
        b = min(int(r["p_enter"] / step), int(1 / step) - 1)
        slot = table.setdefault(round(b * step, 1), {"n": 0, "p": 0.0, "wins": 0})
        slot["n"] += 1
        slot["p"] += r["p_enter"]
        slot["wins"] += r["won"]
    return sorted((k, v) for k, v in table.items())


def brier_over(rows, field):
    """Brier over whichever outcome field is asked for, skipping rows that outcome does not
    resolve — an open trade is neither a win nor a loss, and counting it as one would be a lie."""
    vals = [r for r in rows if r.get(field) is not None]
    if not vals:
        return None, 0
    return sum((r["p_enter"] - r[field]) ** 2 for r in vals) / len(vals), len(vals)


def report(rows, failures, days, universe, out_path, screening=None):
    n = len(rows)
    br, _ = brier_over(rows, "won")
    bplan, nplan = brier_over(rows, "plan_won")
    entered = [r for r in rows if r["action"] == "ENTER"]
    skipped = [r for r in rows if r["action"] == "SKIP"]
    decided = [r for r in rows if r["plan_outcome"] in ("tp1", "stop")]
    mean = statistics.fmean
    base = (mean(r["won"] for r in rows) if n else None)
    base_brier = (base * (1 - base) ** 2 + (1 - base) * base ** 2) if base is not None else None
    rep = {
        "candidates": n, "failed_to_ask": failures,
        "sessions": days, "universe": universe,
        "brier": br, "brier_of_always_0.5": 0.25, "brier_of_always_base_rate": base_brier,
        "brier_plan": bplan, "brier_plan_n": nplan,
        "plan_base_rate": mean(r["plan_won"] for r in decided) if decided else None,
        "plan_outcomes": {k: sum(1 for r in rows if r["plan_outcome"] == k)
                          for k in ("tp1", "stop", "open", None)},
        "plan_hit_rate": mean(r["plan_won"] for r in decided) if decided else None,
        "plan_decided": len(decided),
        "mean_plan_r": mean(r["plan_r"] for r in decided) if decided else None,
        "mean_p_enter": mean(r["p_enter"] for r in rows) if n else None,
        "actual_win_rate": mean(r["won"] for r in rows) if n else None,
        "entered": len(entered),
        "entered_win_rate": mean(r["won"] for r in entered) if entered else None,
        "skipped": len(skipped),
        "skipped_mean_ret": mean(r["ret"] for r in skipped) if skipped else None,
        "mean_mfe": mean(r["mfe"] for r in rows) if n else None,
        "mean_mae": mean(r["mae"] for r in rows) if n else None,
        "omitted_inputs": ["foreign flow (no history: idx_daily holds one session)",
                           "news (would leak current headlines into past decisions)"],
        "survivorship": "universe is the current IDX snapshot; names delisted since are absent",
        "screening": screening or {},
        "rows": rows,
    }
    try:
        with open(out_path, "w") as fh:
            json.dump(rep, fh, indent=1, default=str)
    except OSError as e:
        raise SystemExit(f"cannot write {out_path}: {e}") from e
    return rep


def print_report(rep):
    print(f"\nsessions {rep['sessions']}  universe {rep['universe']}  "
          f"candidates {rep['candidates']}  failed {rep['failed_to_ask']}")
    for k in ("omitted_inputs", "survivorship"):
        print(f"  note: {k}: {rep[k]}")
    if not rep["candidates"]:
        print("  nothing scored — no candidate cleared the gates with a known forward outcome")
        return
    print(f"\ncalibration of p_enter against {HORIZON}-bar outcome")
    print(f"  brier {rep['brier']:.4f}   (always answering 0.5 scores 0.25, "
          f"always answering the base rate scores {rep['brier_of_always_base_rate']:.4f})")
    print(f"  mean p_enter {rep['mean_p_enter']:.3f}   actual win rate {rep['actual_win_rate']:.3f}")
    print(f"  entered {rep['entered']} win rate "
          f"{rep['entered_win_rate'] if rep['entered_win_rate'] is None else round(rep['entered_win_rate'], 3)}")
    if rep["skipped"]:
        print(f"  skipped {rep['skipped']}  mean forward return "
              f"{rep['skipped_mean_ret']:+.2f}%  (a good veto is positive: it spared us losers)")
    print(f"  mean MFE {rep['mean_mfe']:+.2f}%  mean MAE {rep['mean_mae']:+.2f}%")

    print("\nsame probabilities, scored the project's way: stop 1.5xATR against tp1 at 1R")
    print(f"  decided {rep['plan_decided']}  (tp1 {rep['plan_outcomes'].get('tp1', 0)},"
          f" stop {rep['plan_outcomes'].get('stop', 0)},"
          f" still open {rep['plan_outcomes'].get('open', 0)}")
    if rep["plan_hit_rate"] is not None:
        print(f"  hit rate {rep['plan_hit_rate']:.3f}   mean R {rep['mean_plan_r']:+.3f}"
              f"   brier {rep['brier_plan']:.4f} over {rep['brier_plan_n']} decided")
    print("\n  bucket   n   mean p   win rate")
    for b, v in buckets(rep["rows"]):
        print(f"  {b:5.1f} {v['n']:4d}   {v['p'] / v['n']:6.3f}   {v['wins'] / v['n']:8.1%}")


def selfcheck():
    """The scoring arithmetic, with no network and no decider. A wrong horizon or an off-by-one
    in the as-of bar would silently inflate every number above, so it is checked here."""
    cut = 1_000_000
    bars = [{"t": cut - 86400 * 5, "c": 100.0, "h": 100.0, "l": 100.0, "v": 1},
            {"t": cut, "c": 100.0, "h": 100.0, "l": 100.0, "v": 1}]
    for i in range(HORIZON):
        bars.append({"t": cut + 86400 * (i + 1), "c": 101.0, "h": 102.0, "l": 99.0, "v": 1})
    f = forward(bars, cut)
    assert f is not None and f["ret"] > 0, f
    assert bars[1]["t"] <= cut and bars[2]["t"] > cut, "the as-of bar must not count as forward"
    assert abs(f["mfe"] - 2.0) < 1e-9 and abs(f["mae"] + 1.0) < 1e-9, f
    assert forward(bars[:-1], cut) is None, "an unelapsed horizon must score as unknown"
    assert forward(bars, cut + 86400 * 99) is None, "no bars ahead must score as unknown"
    assert brier([{"p_enter": 1.0, "won": 1}]) == 0.0
    assert brier([{"p_enter": 0.0, "won": 1}]) == 1.0
    assert brier([{"p_enter": 0.5, "won": 1}, {"p_enter": 0.5, "won": 0}]) == 0.25
    # the plan-based outcome must reproduce store.resolve(): first of stop or tp1 decides, and a
    # trade still open at the last bar is neither. Entry is the as-of bar and never counts.
    plan = {"entry": 100.0, "stop": 90.0, "tp1": 110.0}
    bars2 = [{"t": cut, "c": 100.0, "h": 100.0, "l": 100.0, "v": 1}]
    bars2 += [{"t": cut + 86400 * (i + 1), "c": 100.0, "h": 100.0, "l": 100.0, "v": 1}
              for i in range(3)]
    bars2[2]["l"] = 85.0                       # bar 2 dips through the stop
    r = forward_plan(bars2, cut, plan)
    assert r is not None and r["outcome"] == "stop" and r["r"] == -1.0, r
    bars2[2]["l"] = 95.0
    bars2[2]["h"] = 115.0                      # bar 2 reaches tp1 first instead
    r = forward_plan(bars2, cut, plan)
    assert r is not None and r["outcome"] == "tp1" and r["r"] == 1.0, r
    bars2[2]["h"] = 100.0                      # neither level reached, and no bar is left
    r = forward_plan(bars2, cut, plan)
    assert r is not None and r["outcome"] == "open", r
    assert brier_over([{"p_enter": 0.9, "plan_won": None}], "plan_won") == (None, 0), \
        "an unresolved trade must not be scored as a loss"
    assert [b for b, _ in buckets([{"p_enter": 0.95, "won": 1}])] == [0.9], "0.95 must land in 0.9"
    print(f"eval: forward window, plan outcome and brier arithmetic  OK  (horizon {HORIZON})")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dates", type=int, default=30, help="sessions to reconstruct")
    ap.add_argument("--top", type=int, default=4, help="candidates kept per session")
    ap.add_argument("--codes", type=int, default=200, help="size of the code pool")
    ap.add_argument("--backend", default=None, help="override JEV_BACKEND for this run")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "eval-last.json"))
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        return selfcheck()

    days = session_dates(a.dates)
    codes = pool(a.codes)
    print(f"reconstructing {a.dates} sessions over a {len(codes)}-code pool")
    cands, stats, errors = build_set(days, codes, a.top)
    print(f"candidate set: {len(cands)}  asking the decider about each")
    ask_all(cands, a.backend)
    rows = score(cands)
    rep = report(rows, sum(1 for c in cands if c["error"]), a.dates, len(codes), a.out,
                 screening=dict(stats, unevaluable_kinds=",".join(sorted(errors)) or "none"))
    print_report(rep)
    print(f"\nwritten to {a.out}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
