#!/usr/bin/env python3
"""Does the selector actually make money, or does it just rank?

AUC says a model puts winners above losers. A trading system needs the top of that ranking to
be profitable in R. Those are different claims, and only the second one pays: at 2.0 ATR / 2R the
base rate is 0.453, so an unfiltered book loses money, and a selector that ranks correctly but
picks the wrong magnitude can lose money too.

So: fit the selector on training sessions only, score the held-out sessions, take the top slice,
and report realised R with the arithmetic shown. A top slice that is not profitable ends this —
no threshold tuning rescues it.

The holdout is by session, not by row, because candidates from the same session share a market
week; splitting by row would leak the week into both sides and flatter the result.
"""
import json
import os
import statistics
import sys

import eval as E
import fit as F

HERE = os.path.dirname(os.path.abspath(__file__))
STOP_MULT, TARGET_R = 2.0, 2.0
TOP_FRACTIONS = (0.10, 0.20, 0.30, 0.50, 1.00)


def outcome(bars, cut, entry, stop, target):
    """First of stop or target decides, entry bar excluded, unreached is neither (not a loss)."""
    at = next((i for i, b in enumerate(bars) if b["t"] >= cut), None)
    if at is None or at + 1 >= len(bars):
        return None, None
    start = at + 1
    for j in range(start, len(bars)):
        b = bars[j]
        if b["l"] <= stop:
            return 0, -1.0
        if b["h"] >= target:
            return 1, 1.0
    return None, None


def main():
    days = E.session_dates(52)
    cands, _s, _e = E.build_set(days, E.pool(300), 4)
    try:
        with open(os.path.join(HERE, "eval-wide.json")) as fh:
            bekko = {(r["day"], r["code"]): r for r in json.load(fh)["rows"]}
    except OSError as e:
        raise SystemExit(f"cannot read eval-wide.json: {e} — run eval.py first") from e
    except json.JSONDecodeError as e:
        raise SystemExit(f"eval-wide.json is not valid JSON: {e}") from e
    rows = [c for c in cands if (c["day"], c["code"]) in bekko]
    print(f"candidates: {len(rows)}  exit: stop {STOP_MULT}xATR, target {TARGET_R}R")

    for c in rows:
        c["plan"] = E.plan_asof(c["bars"])
        p = c["plan"]
        risk = p["entry"] - p["stop"]
        atr = risk / 1.5
        c["won"], c["r"] = outcome(c["full"], c["cut"], p["entry"],
                                   p["entry"] - STOP_MULT * atr,
                                   p["entry"] + TARGET_R * risk)
    decided = [c for c in rows if c["won"] is not None]
    undecided = len(rows) - len(decided)
    base = statistics.fmean(c["won"] for c in decided)
    print(f"decided {len(decided)}  (unreached {undecided}, excluded not counted as losses)")
    print(f"base rate {base:.3f}   unfiltered book {2 * base - 1:+.4f} R per trade\n")

    # holdout by session: the last third of sessions is never seen in training
    ordered = sorted({c["day"] for c in decided})
    split = ordered[int(len(ordered) * 0.67):]
    train = [c for c in decided if c["day"] not in split]
    test = [c for c in decided if c["day"] in split]
    print(f"train sessions {len(ordered) - len(split)} ({len(train)} candidates)"
          f"   TEST sessions {len(split)} ({len(test)} candidates)\n")

    def design(sub):
        return F.design([bekko[(c["day"], c["code"])] for c in sub], F.SCREEN)

    Xtr, ytr, _ = design(train)
    Xte, yte, _ = design(test)
    # scale on the TRAIN rows only, then apply that scaling to the test rows — fitting the
    # scaler on all rows would leak the held-out week's distribution into the fit
    Xtr_s, mu, sd = F.standardise(Xtr)
    Xte_s = [[(v - m) / s for v, m, s in zip(row, mu, sd, strict=True)] for row in Xte]
    model = F.Logistic(l2=0.25).fit(Xtr_s, ytr)

    pte = [model.predict(x) for x in Xte_s]
    pbk = [bekko[(c["day"], c["code"])]["p_enter"] for c in test]
    te_r = [c["r"] for c in test]

    print("held-out session ranking, top slice by fitted score")
    print(f"  {'top':>6} {'n':>4} {'win':>6} {'R/trade':>9} {'cum R':>8}")
    ranked = sorted(zip(pte, te_r, strict=True), key=lambda t: -t[0])
    for frac in TOP_FRACTIONS:
        k = max(1, int(len(ranked) * frac))
        sel = ranked[:k]
        w = statistics.fmean(1.0 if r > 0 else 0.0 for _, r in sel)
        mr = statistics.fmean(r for _, r in sel)
        print(f"  {frac:6.0%} {k:4d} {w:6.1%} {mr:+9.4f} {sum(r for _, r in sel):+8.2f}"
              f"{'   <- profitable' if sum(r for _, r in sel) > 0 else ''}")

    print("\nthe same test set, ranked by Bekko's own verdict (for contrast)")
    rbk = sorted(zip(pbk, te_r, strict=True), key=lambda t: -t[0])
    for frac in (0.20, 0.30):
        k = max(1, int(len(rbk) * frac))
        sel = rbk[:k]
        print(f"  {frac:6.0%} {k:4d} "
              f"{statistics.fmean(1.0 if r > 0 else 0.0 for _, r in sel):6.1%} "
              f"{statistics.fmean(r for _, r in sel):+9.4f} {sum(r for _, r in sel):+8.2f}")

    n = len(yte)
    base_te = statistics.fmean(yte)
    print(f"\nheld-out base rate {base_te:.3f} ({n} candidates), "
          f"unfiltered {2 * base_te - 1:+.4f} R per trade")
    auc_te = F.auc(pte, yte)
    verdict = "  MET" if auc_te is not None and auc_te >= 0.60 else "  not met"
    print(f"held-out AUC (fitted, out-of-sample) {auc_te:.4f}   bar 0.60{verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
