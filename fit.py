#!/usr/bin/env python3
"""Does a *fitted* decision beat the decider's own opinion on the same candidates?

The 17M model answers a momentum question with ~9 points of range, partly inverted, and never
below 0.81 — so it cannot be calibrated into a working decider, because the ordering is wrong
rather than merely shifted. The features it reads, however, are exactly the ones the screen was
built around, and a model fitted on outcomes can learn a monotone map from them that the 17M model
has no capacity to represent.

This fits logistic regression on the numeric features in an eval artifact, against `tp1` before
`stop`, and reports it in the same terms as eval.py so the three are comparable:

    Brier      lower is better; a constant base-rate answer scores ~0.23 on this data
    separation mean(fitted p | tp1) - mean(fitted p | stop); 0 means the fit learned nothing

The headline numbers are out-of-fold. With six features and a few hundred rows an in-sample fit
would flatter itself, so the reported figure comes from K-fold cross-validation — the model is
fitted on K-1 folds and scored on the held-out one, then predictions are pooled before any metric
is computed. `--folds 0` disables that and fits on everything, which is only what you want for the
final model you would actually deploy.

No dependencies, deliberately: this lives in the repo, and the repo has none.

Usage:
  python3 fit.py                       # reads eval-wide.json
  python3 fit.py --artifact eval-last.json
  python3 fit.py --selfcheck           # no artifact, no network: proves the solver
"""
import argparse
import json
import math
import os
import random
import statistics
import sys

SCREEN = ("rsi", "obv_slope_20d", "distance_to_significant_high_pct",
          "rank_score", "adv20_bn", "stop_pct")
# The decider's own heads. The first fit used only SCREEN; the noul/conviction/risk outputs were
# computed on every call and discarded, so they are now read too. This lets a fitted selector
# weight the model's signal against the screen's instead of either alone.
HEADS = ("noul_p_enter", "noul_momentum", "conviction", "risk_high", "risk_low")
FEATURES = SCREEN + HEADS


def auc(scores, labels):
    """Rank AUC with tie handling: does a higher score mean a higher chance of tp1? 0.5 is no
    ranking ability; <0.5 is worse than random, which is what Bekko's verdict head measured."""
    pairs = sorted(zip(scores, labels, strict=True), key=lambda t: t[0])
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):                      # average ranks within a tie so ties are neutral
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = shared
        i = j + 1
    n1 = sum(labels)
    n0 = len(labels) - n1
    if n1 == 0 or n0 == 0:
        return None
    pos = sum(ranks[k] for k, (_, lab) in enumerate(pairs) if lab == 1)
    return (pos - n1 * (n1 + 1) / 2) / (n1 * n0)


# ---------- tiny linear algebra, because this repo has no numpy ----------
def solve(a, b):
    """Gaussian elimination with partial pivoting. `a` is n x n, `b` is length n. Mutates a copy."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("singular matrix — fewer rows than features, or collinear inputs")
        m[col], m[pivot] = m[pivot], m[col]
        pv = m[col][col]
        for r in range(col + 1, n):
            f = m[r][col] / pv
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


def sigmoid(z):
    # branch on sign so exp() never overflows on a badly separated first iteration
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class Logistic:
    """Ridge-penalised logistic regression, fitted by IRLS.

    Ridge is not decoration. With six features and a few hundred correlated rows the plain MLE
    drives a coefficient to infinity on whichever feature happens to separate the training folds;
    the penalty is what makes the out-of-fold estimate mean anything.
    """

    def __init__(self, l2=1.0, iters=25):
        self.l2, self.iters = l2, iters

    def fit(self, X, y):
        d = len(X[0])
        beta = [0.0] * d
        for _ in range(self.iters):
            eta = [[0.0] * d for _ in range(d)]     # X'WX is d x d, not a vector
            rhs = [0.0] * d                          # X'W(y - p)
            for xi, yi in zip(X, y, strict=True):
                z = sum(b * v for b, v in zip(beta, xi, strict=True)) - beta[0]
                p = sigmoid(z)
                w = max(p * (1 - p), 1e-6)
                r = yi - p
                for j in range(d):
                    wj = w * xi[j]
                    rhs[j] += wj * r
                    row = eta[j]
                    for k in range(d):
                        row[k] += wj * xi[k]
            for j in range(1, d):
                eta[j][j] += self.l2     # ridge on the diagonal; the intercept is never penalised
            try:
                step = solve(eta, rhs)
            except ValueError:
                break                                # degenerate block; keep the last usable beta
            converged = max(abs(a - b) for a, b in zip(step, beta, strict=True)) < 1e-8
            beta = step
            if converged:
                break
        self.beta = beta
        return self

    def predict(self, x):
        return sigmoid(sum(b * v for b, v in zip(self.beta, x, strict=True)) - self.beta[0])


# ---------- the data ----------
def design(rows, features=FEATURES):
    """-> (X, y, kept). A row with any missing feature is dropped, not imputed: imputing a median
    invents a value the decider never saw, and the fit would then lean on it. `features` names
    screen keys and/or head keys; a head key reads row["heads"], a screen key reads
    row["features"]."""
    X, y, kept = [], [], []
    for r in rows:
        if r.get("plan_won") is None:
            continue
        screen, heads = r.get("features") or {}, r.get("heads") or {}
        src = {**screen, **heads}
        try:
            vec = [1.0] + [float(src[k]) for k in features]
        except (TypeError, ValueError, KeyError):
            continue
        if any(math.isnan(v) or math.isinf(v) for v in vec[1:]):
            continue
        X.append(vec)
        y.append(float(r["plan_won"]))
        kept.append(r)
    return X, y, kept


def standardise(X):
    """Zero-mean unit-variance on the non-intercept columns, returned with the parameters so a
    later prediction uses the training set's scaling rather than the batch's."""
    cols = list(zip(*X, strict=True))
    mu = [statistics.fmean(c[1:]) for c in cols]
    sd = [statistics.pstdev(c[1:]) or 1.0 for c in cols]
    mu[0], sd[0] = 0.0, 1.0
    out = [[(v - m) / s for v, m, s in zip(row, mu, sd, strict=True)] for row in X]
    return out, mu, sd


def cross_val(X, y, folds, l2, seed=7):
    """Pooled out-of-fold predictions: every row is predicted exactly once, by a model that
    never saw it."""
    idx = list(range(len(X)))
    random.Random(seed).shuffle(idx)
    preds = [0.0] * len(X)
    for k in range(folds):
        test = [i for j, i in enumerate(idx) if j % folds == k]
        train = [i for j, i in enumerate(idx) if j % folds != k]
        if not test or len({y[i] for i in train}) < 2:
            continue
        m = Logistic(l2=l2).fit([X[i] for i in train], [y[i] for i in train])
        for i in test:
            preds[i] = m.predict(X[i])
    return preds


def brier(preds, y):
    return sum((p - t) ** 2 for p, t in zip(preds, y, strict=True)) / len(y) if y else None


def report(label, preds, y):
    """One comparable block. Every scorer is judged on the same rows with the same target, and
    reported on both calibration (Brier) and ranking (AUC), because they can disagree: a model
    can rank well and still say 0.8 on everything."""
    n = len(y)
    if not n:
        print(f"\n{label}: nothing to score")
        return
    base = statistics.fmean(y)
    const = base * (1 - base) ** 2 + (1 - base) * base ** 2
    a = auc(preds, y)
    pos = [p for p, t in zip(preds, y, strict=True) if t == 1]
    neg = [p for p, t in zip(preds, y, strict=True) if t == 0]
    print(f"\n{label}")
    print(f"  n {n}   base rate {base:.3f}")
    print(f"  brier {brier(preds, y):.4f}   (constant base-rate {const:.4f}, constant 0.5 0.2500)")
    if a is not None:
        tag = "  <-- worse than random" if a < 0.5 else ""
        print(f"  AUC   {a:.4f}{tag}")
    if pos and neg:
        print(f"  separation {statistics.fmean(pos) - statistics.fmean(neg):+.4f}   "
              f"(mean p on tp1 {statistics.fmean(pos):.3f}, on stop {statistics.fmean(neg):.3f})")
    print(f"  predicted range {min(preds):.3f} .. {max(preds):.3f}")


def selfcheck():
    """The solver, on data where the answer is known. A regression in solve() would otherwise
    surface as a plausible-looking coefficient and a quietly useless fit."""
    X = [[1.0, float(i), float(i * i)] for i in range(40)]
    y = [1.0 if i % 2 else 0.0 for i in range(40)]
    m = Logistic(l2=1.0).fit(X, y)
    p = [m.predict(x) for x in X]
    assert all(0 < v < 1 for v in p), "probabilities must stay in the open interval"
    assert p[1] > p[0], "the fitted model must order a 1 above a 0"
    Xc = [[1.0, 1.0, float(i % 2)] for i in range(40)]
    mc = Logistic(l2=1.0).fit(Xc, y)
    assert abs(mc.beta[1]) < 0.5, f"a constant column took weight {mc.beta[1]}"
    assert [round(v, 6) for v in solve([[2.0, 1.0], [1.0, 3.0]], [5.0, 10.0])] == [1.0, 3.0]
    Xr, _mu, _sd = standardise(X)
    cv = cross_val(Xr, y, 5, 1.0)
    assert len(cv) == len(y) and all(0 < v < 1 for v in cv), "every row needs one out-of-fold p"
    rows = [{"features": {"rsi": None, "obv_slope_20d": 0.1,
                          "distance_to_significant_high_pct": 0.0, "rank_score": 2,
                          "adv20_bn": 1.0, "stop_pct": 3.0}, "plan_won": 1}]
    assert design(rows)[0] == [], "a row with a missing feature must be dropped"
    assert brier([0.5, 0.5], [1.0, 0.0]) == 0.25
    # AUC on a known ordering, and on its reverse
    perfect = auc([0.1, 0.2, 0.3, 0.4], [0, 0, 1, 1])
    assert perfect == 1.0, f"perfect order must score 1.0, got {perfect}"
    reverse = auc([0.4, 0.3, 0.2, 0.1], [0, 0, 1, 1])
    assert reverse == 0.0, f"reversed order must score 0.0, got {reverse}"
    tied = auc([0.5, 0.5, 0.5, 0.5], [0, 0, 1, 1])
    assert tied is not None and abs(tied - 0.5) < 1e-9, f"all-tied is chance, got {tied}"
    # tie handling: a tied positive cannot outrank a tied negative, so that pair scores 0.5.
    # [0.1, 0.3, 0.3, 0.9] -> 3.5 of 4 cross pairs -> 0.875; a clean order gives 1.0.
    with_tie = auc([0.1, 0.3, 0.3, 0.9], [0, 0, 1, 1])
    clean = auc([0.1, 0.2, 0.4, 0.9], [0, 0, 1, 1])
    assert with_tie is not None and clean is not None, "both label vectors have both classes"
    assert with_tie == 0.875, f"a tie must score the pair 0.5, got {with_tie}"
    assert clean == 1.0, f"an untied correct order must score 1.0, got {clean}"
    # a full tie scores exactly chance
    flat = auc([1.0, 1.0, 1.0, 1.0], [0, 1, 0, 1])
    assert flat == 0.5, f"a full tie is chance, got {flat}"
    print("fit: solver, ridge, cross-val, missing features, brier and auc  OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "eval-wide.json"))
    ap.add_argument("--l2", type=float, default=4.0)
    ap.add_argument("--folds", type=int, default=5, help="0 fits on everything (in-sample)")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        return selfcheck()

    try:
        with open(a.artifact) as fh:
            art = json.load(fh)
    except OSError as e:
        raise SystemExit(f"cannot read {a.artifact}: {e}\n"
                         f"run `python3 eval.py` first to produce it") from e
    except json.JSONDecodeError as e:
        raise SystemExit(f"{a.artifact} is not valid JSON: {e}") from e

    rows = art.get("rows") or []
    X, y, kept = design(rows)
    if len(y) < 20:
        print(f"only {len(y)} usable labelled rows out of {len(rows)} — too few to fit, and "
              f"far too few to trust a fit. Widen the backtest (--codes, --dates) first.")
        return 1
    print(f"{a.artifact}: {len(rows)} rows, {len(y)} labelled and complete, "
          f"{len(rows) - len(y)} dropped for a missing feature or an unresolved outcome")

    # The decider, on exactly these rows, so the comparison is like for like. Note what is being
    # compared: `p_enter` in the artifact is decider.enter_probability(), which is
    # verdict.probabilities.enter — the choice head, not the noul question. Both assert P(this
    # trade works) and both are scored against the same tp1-before-stop outcome, so the fit is a
    # fair opponent; but the noul head was never stored and so is not in this table.
    report("BEKKO 17M verdict head (the decider's own probability of entering)",
           [r["p_enter"] for r in kept], y)
    X, _mu, _sd = standardise(X)
    if a.folds:
        report(f"FITTED logistic regression ({a.folds}-fold out-of-fold, l2={a.l2})",
               cross_val(X, y, a.folds, a.l2), y)
    final = Logistic(l2=a.l2).fit(X, y)
    report(f"FITTED logistic regression (in-sample, l2={a.l2})",
           [final.predict(x) for x in X], y)
    print("\ncoefficients (standardised, intercept first)")
    for name, b in zip(("intercept",) + FEATURES, final.beta, strict=True):
        print(f"  {name:34} {b:+.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
