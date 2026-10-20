#!/usr/bin/env python3
"""Is the objective the problem, or the model?

Everything so far — Bekko 0.4310, Laya 0.4630, a fitted regression 0.5707 — was measured
against ONE target: take profit at 1R, stop at 1.5xATR. At that objective 185 trades gave a 50.8%
base rate: a coin flip, which is why no model can rank above chance. Structure often only shows
up at a target the price can actually reach.

This re-scores the SAME candidate set across a grid of (stop multiple, target R), holding the
candidates fixed and only moving the exit. If some objective produces a base rate the features
separate, the objective was the constraint. If nothing does, the honest conclusion is that these
features do not predict these breakouts at all.

The candidate set is rebuilt with the same parameters as the eval run, so it is identical; only
the outcome changes. The deciders' stored probabilities are joined by (day, code), so the
comparison is on the same rows.
"""
import json
import os
import statistics
import sys

import eval as E
import fit as F

HERE = os.path.dirname(os.path.abspath(__file__))
STOPS = (1.0, 1.5, 2.0, 2.5, 3.0)
TARGETS = (1.0, 1.5, 2.0, 3.0)


def outcome(bars, cut, entry, stop, target):
    """First of stop or target decides, the entry bar never counts — the same no-lookahead rule
    store.resolve() uses. None when the target is still unreached, which is not a loss."""
    at = next((i for i, b in enumerate(bars) if b["t"] >= cut), None)
    if at is None:
        return None
    start = at + 1
    if start >= len(bars):
        return None
    for j in range(start, len(bars)):
        b = bars[j]
        if b["l"] <= stop:
            return 0
        if b["h"] >= target:
            return 1
    return None


def auc(scores, labels):
    return F.auc(scores, labels)


def main():
    # rebuild the identical candidate set; screening is pure local computation on cached bars
    days = E.session_dates(52)
    codes = E.pool(300)
    cands, _stats, _err = E.build_set(days, codes, 4)
    print(f"candidate set: {len(cands)}")

    # decider probabilities, joined by (day, code)
    def load(name):
        try:
            with open(os.path.join(HERE, name)) as fh:
                return {(r["day"], r["code"]): r for r in json.load(fh)["rows"]}
        except OSError as e:
            raise SystemExit(f"cannot read {name}: {e} — run eval.py first") from e
        except json.JSONDecodeError as e:
            raise SystemExit(f"{name} is not valid JSON: {e}") from e
    bekko = load("eval-wide.json")
    laya = load("eval-laya.json")
    common = [c for c in cands if (c["day"], c["code"]) in bekko and (c["day"], c["code"]) in laya]
    print(f"with both deciders' answers: {len(common)}")
    if not common:
        print("no overlap — re-run the decider backtests first")
        return 1

    # the plan, for its ATR-derived risk unit
    for c in common:
        c["plan"] = E.plan_asof(c["bars"])
    base = [c for c in common if c["plan"]]

    screen_feats = [F.design([bekko[(c["day"], c["code"])]], F.SCREEN)[0][0] for c in base]

    print(f"\ndeciders: bekko + laya, fitter refit per objective.  {len(base)} candidates")
    print(f"\n{'stop':>5} {'target':>7} {'decided':>8} {'base':>7} "
          f"{'bekko':>7} {'laya':>7} {'fitted':>7}   (AUC; 0.5 = no ranking ability)")

    for stop_mult in STOPS:
        for target_r in TARGETS:
            ys, be, la, rows_kept = [], [], [], []
            for c, xf in zip(base, screen_feats, strict=True):
                p = c["plan"]
                risk = p["entry"] - p["stop"]            # 1.5xATR by construction
                atr = risk / 1.5
                stop = p["entry"] - stop_mult * atr
                target = p["entry"] + target_r * risk
                w = outcome(c["full"], c["cut"], p["entry"], stop, target)
                if w is None:
                    continue                              # unreached, not a loss
                ys.append(float(w))
                be.append(bekko[(c["day"], c["code"])]["p_enter"])
                la.append(laya[(c["day"], c["code"])]["p_enter"])
                rows_kept.append(xf)
            if len(set(ys)) < 2 or len(ys) < 40:
                continue
            base_rate = statistics.fmean(ys)
            X, _mu, _sd = F.standardise(rows_kept)
            fp = F.cross_val(X, ys, 5, 0.25)
            yi = [int(v) for v in ys]
            print(f"{stop_mult:5.1f} {target_r:7.1f} {len(ys):8d} {base_rate:7.3f} "
                  f"{auc(be, yi):7.4f} {auc(la, yi):7.4f} {auc(fp, ys):7.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
