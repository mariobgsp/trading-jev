#!/usr/bin/env python3
# pyright: reportMissingImports=false
# Modules in this directory import each other (tools.py, pivots.py, screen.py, decider.py, store.py,
# idx.py). Pyright in this setup does not pick up pyrightconfig.json's extraPaths and reports every
# one as missing, while the scripts resolve them fine at runtime. Verified by running the demos.
"""Significant swing highs — ATR-prominence pivots that do not repaint.

"Last H" as a 20-bar high is useless on a ranging stock: the high is noise, so a cross through it
means nothing. A significant high has to stand out from its own noise. A bar qualifies when all
four hold:

  1. dominant   its high is the highest of the `lookback` bars before it
  2. standout   it is >= standout_atr x ATR(14) above the MEAN high of that same prior window
  3. confirmed  no bar in the next `confirm` bars exceeds it
  4. prominence the pullback after it ran >= prom_mult x ATR(14) below it

Test 2 is what a plain prominence threshold cannot do on its own: in a flat chart the biggest
20-bar wiggle is about 1 ATR, so a lone >=1 ATR rule still fires. Requiring the high to stand
above where price has *been* separates a thrust from a wiggle, and it only looks backwards.

**Deviation from the plan's symmetric +/-20 baseline.** Measuring prominence off the pullback
needs 3 confirmation bars, not 20, and test 1 looks backwards only — so a pivot qualifies
lookback bars after the fact, not 20. A symmetric window would make the newest testable pivot 20
bars stale. Both windows are still required to be *complete* before a pivot is returned, so
nothing repaints and nothing peeks ahead.

**Act on the level, never on the bar the high printed.** A pivot without its confirmation bars
can move or vanish, and using its historical bar is look-ahead bias that will make the backtest lie.

Absence of a significant high IS the sideways filter: a ranging stock produces almost none, so it
cannot pass a breakout test. That is the point.

Usage:
  python3 pivots.py demo        # assert-based self-check, no network

Defaults are measured, not guessed. Swept over 886 cached IDX stocks (6mo, no network):
prom_mult 3.5 / standout 2.0 fires on 8.8% of the universe and rejects 28 ranging names that a
naive 20-bar high would have called breakouts. The looser Ta4j default of 1.5/1.0 fires on 27.4%
and rejects only 5 — the opposite of the intent. `prom_mult` is the effective knob; `standout_atr`
is secondary. That sweep measures signal FREQUENCY, not edge: fewer signals is not better signals.
Tighten or loosen from the journal, not from this table.
"""
from tools import screener

atr = screener.atr


def _pivots(bars, is_high, lookback, confirm, prom_mult, standout_atr, n):
    """One definition for both directions. For lows every value is negated, which turns the
    significant-low case into the identical significant-high case — no second rule to drift."""
    if len(bars) < 2 * lookback + confirm + n + 1:
        return []
    a = atr(bars, n)
    s = 1.0 if is_high else -1.0
    pick = (lambda b: b["h"]) if is_high else (lambda b: b["l"])
    # The reversal is measured from the pivot to the OPPOSITE field after it: a high is judged by
    # the lowest low that follows, a low by the highest high. Using `pick` here instead measures
    # high-to-high, which silently loosens the whole rule — that bug shipped briefly in this file.
    other = (lambda b: b["l"]) if is_high else (lambda b: b["h"])
    out = []
    for i in range(lookback, len(bars) - lookback):
        av = a[i]
        if av is None or av <= 0:
            continue
        x = s * pick(bars[i])                       # in "up" space, high and low are one case
        prior = [s * pick(bars[j]) for j in range(i - lookback, i)]
        if x < max(prior):
            continue                                       # 1. not dominant
        if x - sum(prior) / len(prior) < standout_atr * av:
            continue                                       # 2. does not stand out
        if any(s * pick(bars[j]) > x for j in range(i + 1, i + confirm + 1)):
            continue                                       # 3. not a local extreme
        extreme = min(s * other(bars[j]) for j in range(i + 1, i + lookback + 1))
        if x - extreme < prom_mult * av:
            continue                                       # 4. reversal too shallow
        out.append({"index": i, "high" if is_high else "low": x / s,
                    "prominence": round((x - extreme) / av, 2)})
    return out


def significant_highs(bars, lookback=20, confirm=3, prom_mult=3.5, standout_atr=2.0, n=14):
    """Confirmed significant highs, oldest first. Every window is complete, so appending bars
    never changes or removes a pivot that was already returned."""
    return _pivots(bars, True, lookback, confirm, prom_mult, standout_atr, n)


def significant_lows(bars, lookback=20, confirm=3, prom_mult=3.5, standout_atr=2.0, n=14):
    """Confirmed significant lows, same rule and same parameters as the highs."""
    return _pivots(bars, False, lookback, confirm, prom_mult, standout_atr, n)


def last_significant_high(bars, pivots=None, **kw):
    sh = pivots if pivots is not None else significant_highs(bars, **kw)
    return sh[-1] if sh else None


def breakout(bars, pivots=None, **kw):
    """Category 7: close above the last confirmed significant high."""
    p = last_significant_high(bars, pivots, **kw)
    if not p or bars[-1]["c"] <= p["high"]:
        return None
    return {"level": p["high"], "close": bars[-1]["c"], "pivot_index": p["index"]}


def hh_breakout(bars, pivots=None, **kw):
    """Category 1: close above a significant high that is itself a higher high."""
    sh = pivots if pivots is not None else significant_highs(bars, **kw)
    if len(sh) < 2 or sh[-1]["high"] <= sh[-2]["high"]:
        return None
    return breakout(bars, sh, **kw)


def lower_low(bars, pivots=None, **kw):
    """Category 6: close below the last confirmed significant low. A descriptor, never a gate —
    a lower low says where price is, not that the stock is tradeable."""
    sl = pivots if pivots is not None else significant_lows(bars, **kw)
    if not sl or bars[-1]["c"] >= sl[-1]["low"]:
        return None
    return {"level": sl[-1]["low"], "close": bars[-1]["c"], "pivot_index": sl[-1]["index"]}


# ---------- self-check ----------
def _bars(closes, half=0.5, v=1_000_000):
    return [{"o": c, "h": c + half, "l": c - half, "c": c, "v": v} for c in closes]


def _trend_closes():
    out = []
    for base, peak in ((100, 140), (110, 150), (120, 160)):
        out += [base + 1.0 * k for k in range(40)]
        out += [peak - 2.0 * k for k in range(1, 11)]
    out += [160 - 3.0 * k for k in range(1, 26)]
    return out


def _trend():
    """Three legs of impulse up + deep pullback, then a decline. Long enough that the peaks sit
    well inside the data: a pivot needs `lookback` complete bars on each side."""
    return _bars(_trend_closes(), half=1.0)


def _flat(n=200):
    """Ranging: wiggles well under the stock's own bar range."""
    return _bars([100 + 2.0 * (1 if i % 2 else -1) for i in range(n)], half=3.0)


def demo():
    t, f = _trend(), _flat()

    # 1. a real trend produces pivots, and they are ordered
    sh = significant_highs(t)
    assert len(sh) >= 2, f"trend must produce significant highs, got {len(sh)}"
    assert [p["index"] for p in sh] == sorted(p["index"] for p in sh), "pivots must be ordered"
    assert all(p["prominence"] >= 3.5 for p in sh), f"prominence must clear the floor: {sh}"

    # 2. the sideways case: the naive 20-bar high finds a level, the significant rule does not
    naive = max(b["h"] for b in f[-21:-1])
    assert naive > f[-1]["c"], "a ranging stock still has a 20-bar high above price"
    assert len(significant_highs(f)) * 3 <= len(sh), (
        f"ranging stock must produce far fewer significant highs, got "
        f"{len(significant_highs(f))} vs {len(sh)}")

    # 3. no repainting: appending bars never changes or removes a pivot already returned
    short = significant_highs(t[:len(t) - 7])
    full = {p["index"]: p for p in significant_highs(t)}
    for p in short:
        assert p["index"] in full and full[p["index"]]["high"] == p["high"], (
            f"pivot {p['index']} changed when more bars arrived: {p} -> {full.get(p['index'])}")

    # 4. windows are complete, so the newest pivot is always lookback bars back
    assert all(p["index"] <= len(t) - 1 - 20 for p in sh), "a pivot was returned with an incomplete window"

    # 4b. prominence is the drop from the pivot HIGH to the lowest LOW after it, never high-to-high.
    # A refactor once measured it high-to-high and the rule quietly loosened; this pins the field
    # semantics so that cannot happen again unnoticed.
    av = atr(t, 14)
    for p in sh:
        trough = min(b["l"] for b in t[p["index"] + 1:p["index"] + 21])
        assert abs(p["prominence"] - round((p["high"] - trough) / av[p["index"]], 2)) < 0.02, (
            f"prominence must be high-to-low, got {p} expected "
            f"{round((p['high'] - trough) / av[p['index']], 2)}")

    # 5. the trend's last two pivots are rising — that is the higher-high structure (category 1)
    assert sh[-1]["high"] > sh[-2]["high"], f"last two pivots must be rising: {sh[-2:]}"

    # 6. breakout only fires above the level
    closes = list(range(100, 140)) + [139 - 2.0 * k for k in range(1, 11)] + [120.0] * 30
    below = _bars(closes, half=1.0)
    pivot = last_significant_high(below)
    assert pivot is not None, "fixture must contain a pivot"
    assert breakout(below) is None, "close below the level must not be a breakout"
    assert hh_breakout(below) is None, "no breakout, no higher-high breakout"

    # 7. above the level it fires, and the level is the pivot high
    up = _bars([b["c"] for b in below[:-1]] + [pivot["high"] + 1.0], half=1.0)
    b = breakout(up)
    assert b is not None, "close above the level must be a breakout"
    assert b["level"] == pivot["high"], f"breakout level wrong: {b}"
    assert b["close"] > b["level"], "breakout close must exceed the level"

    # 8. the low side obeys the same rule: a downtrend makes significant lows, a range makes none
    down = _bars(_trend_closes()[::-1], half=1.0)
    sl = significant_lows(down)
    assert len(sl) >= 2, f"downtrend must produce significant lows, got {len(sl)}"
    assert all(p["prominence"] >= 3.5 for p in sl), f"low prominence floor: {sl}"
    assert sl[-1]["low"] < sl[-2]["low"], f"the two last lows must be falling: {sl[-2:]}"
    assert not significant_lows(f), "a ranging stock must produce no significant lows"
    short_lows = significant_lows(down[:len(down) - 7])
    full_lows = {p["index"]: p for p in sl}
    for p in short_lows:
        assert full_lows.get(p["index"], {}).get("low") == p["low"], (
            f"low pivot {p['index']} repainted: {p} -> {full_lows.get(p['index'])}")

    # 9. lower_low fires when the close is below the last significant low, quiet above it
    last_low = sl[-1]["low"]
    fired = lower_low(down)
    assert fired is not None, f"close {down[-1]['c']} is below the significant low {last_low}"
    assert fired["level"] == last_low, f"lower_low level wrong: {fired} vs {sl[-1]}"
    assert fired["close"] < last_low, "lower_low close must sit below the level"
    quiet = _bars([b["c"] for b in down] + [last_low + 50.0] * 5, half=1.0)
    assert lower_low(quiet) is None, "a close above the significant low is not a lower low"

    print(f"pivots: trend highs={len(sh)} lows={len(sl)} flat={len(significant_highs(f))}  OK")


if __name__ == "__main__":
    demo()
