# pyright: reportMissingImports=false
"""The 23 categories: 2 required gates, 4 rank signals, 17 descriptors.

Everything numeric comes from ihsg_screener's verified Wilder primitives and from pivots.py, so
there is one definition per idea. Per candidate this needs: daily bars, the ^JKSE series, the IDX
session row (price, float, foreign flow) and optionally news.

**Why one gate and not five** — measured, not assumed. Over the 231 gorengan names that cleared
the foreign-flow pre-gate, requiring all five of the original gates produced 0.0%. Per-gate pass
rates were: obv_rising 59.6%, macd_cross_bull 13.6%, breakout 5.1%, rsi_bull_divergence 1.5%,
hh_from_last_h 0.5%. Two structural reasons, not bad luck:

  - `rsi_bull_divergence` is a *leading* signal: it fires before price rises, so demanding it in
    the same bar as a `breakout` asks for a contradiction. The 6 names that cleared the three
    core gates had zero bonus signals — they never co-occur.
  - `hh_from_last_h` is a subset of `breakout` (it also requires the prior pivot to be lower), so
    the two count one idea twice.

So: `breakout` off a *significant* high is required — that is the setup, and the pivot rule is
what makes the level mean something. The other four rank candidates and travel to Jev as context.
Descriptors exist so the journal's NO is readable: a probability of 0.31 means nothing until you
can see which categories fired, which failed, and by how much.

Two additions worth calling out:
  20 conviction_breakout  is `breakout` plus a conviction test: the crossing bar must also expand
                its range and its volume. A drift through an old high is not a breakout.
  21 breakdown  is the mirror of `breakout` — a close below the last confirmed significant low. A
                descriptor, never a gate: this is a long-only system, so a breakdown says where
                price is, not that the stock is tradeable.
  22 / 23 liquid / illiquid  see LIQUID_MIN_ADV below. `liquid` is a required gate because
                it is a tradability constraint, not a signal: an illiquid momentum entry is one
                you cannot exit.
"""
import importlib.util
import os

from pivots import breakout, hh_breakout, lower_low
from tools import screener

# The setup. One structural event off a significant high; the pivot rule is what makes it mean
# something. Measured: 4.8% of the flow-qualified pool, and requiring more than this returns ~0.
REQUIRED = ("breakout", "liquid")
# Confirmation and context. Ranked, not required — see the module docstring for the measurement.
RANKED = ("obv_rising", "macd_cross_bull", "hh_from_last_h", "rsi_bull_divergence")

# Minimum average daily turnover, IDR, over the last 20 bars. Measured, not assumed: across 592
# gorengan names the ADV20 median is IDR 1.12bn (p25 0.24bn, p75 5.29bn, p90 16.4bn) and nine
# names trade literally nothing. IDR 1bn is the median and also means a IDR 100m position is at
# most ~10% of a day's turnover. It is close to free: of the 11 names that cleared the gate on
# 20260925, 10 were above it, and the gate-clearing names run 3-10bn — momentum breakouts
# concentrate in liquid names, so this barely cuts the shortlist while removing the trap.
LIQUID_MIN_ADV = 1.0e9
# Confirmation and context. Ranked, not required — see the module docstring for the measurement.
RANKED = ("obv_rising", "macd_cross_bull", "hh_from_last_h", "rsi_bull_divergence")

# A breakout must expand to count as conviction, not just cross a line.
EXPAND_RANGE = 1.5
EXPAND_VOLUME = 1.5
CROSS_UP = "golden"
CROSS_DOWN = "death"


def liquidity(bars, n=20):
    """Average daily turnover in IDR. Bars carry volume but not value, so value is approximated
    as volume x typical price — the usual OHLCV convention, and the only figure available
    without a second data source."""
    if not bars or len(bars) < n:
        return None
    window = bars[-n:]
    return sum(((b["h"] + b["l"] + b["c"]) / 3) * (b["v"] or 0) for b in window) / n


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def rel_strength(bars, index_bars, n=20):
    """Relative strength vs the index over n bars. The index series comes from Yahoo (^JKSE), not
    IDX, because one request returns the whole history."""
    if len(bars) < n + 1 or len(index_bars) < n + 1:
        return None
    stock = bars[-1]["c"] / bars[-n - 1]["c"] - 1
    index = index_bars[-1]["c"] / index_bars[-n - 1]["c"] - 1
    return {"stock_pct": stock * 100, "index_pct": index * 100, "rel_pct": (stock - index) * 100}


def obv_slope(bars, n=20):
    """Slope of OBV over the last n bars, normalised by average volume so it is comparable
    across a 50-rupiah stock and a 50,000-rupiah one."""
    if len(bars) < n + 2:
        return None
    o = screener.obv(bars)
    span = o[-1] - o[-n - 1]
    vol = _mean([b["v"] for b in bars[-n:]]) or 0
    return span / (vol * n) if vol else None


def expansion(bars, n=20):
    """Last bar's range and volume against the n-bar average."""
    if len(bars) < n + 1:
        return None
    avg_r = _mean([b["h"] - b["l"] for b in bars[-n:]])
    avg_v = _mean([b["v"] for b in bars[-n:]])
    last = bars[-1]
    return {
        "range_ratio": (last["h"] - last["l"]) / avg_r if avg_r else None,
        "volume_ratio": last["v"] / avg_v if avg_v else None,
    }


def _news_score(code, days=7):
    """Positive-news count from trading-tools' Google News RSS scorer, or None if unavailable.
    Loaded lazily: it is one HTTP request per candidate, so only the shortlist should pay it."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "trading-tools", "idx-news", "stock_news.py")
    try:
        spec = importlib.util.spec_from_file_location("stock_news", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        items = mod.parse_rss(mod.fetch_rss(f"{code} saham"))
    except Exception:  # noqa: BLE001 - news is a descriptor, never worth failing a scan over
        return None
    scored = [mod.sentiment(t) for t in items[: days * 3]]
    return {"items": len(scored), "positive": sum(1 for s in scored if s > 0)}


def evaluate(code, bars, index_bars=None, flow=None, news=None):
    """-> the full category set for one candidate. Never raises on missing optional data; an
    absent input becomes False and is reported in `missing` so the gap is visible in the journal."""
    if not bars or len(bars) < 30:
        return None
    closes = [b["c"] for b in bars]
    e20, e50 = screener.ema(closes, 20), screener.ema(closes, 50)
    r = screener.rsi(closes)
    x = screener.crosses(bars)
    base_score, base_ev, _ = screener.s_base(bars)
    bo, bo_p = breakout(bars), hh_breakout(bars)
    ll = lower_low(bars)
    exp = expansion(bars)
    rs = rel_strength(bars, index_bars) if index_bars else None
    oslope = obv_slope(bars)
    adv = liquidity(bars)
    net = (flow or {}).get("net")
    close = closes[-1]

    cat = {
        # 1
        "hh_from_last_h": bo_p is not None,
        # 2 / 3
        "diverging_from_index": None if rs is None else rs["rel_pct"] < 0,
        "following_index": None if rs is None else rs["rel_pct"] > 0,
        # 4 / 5
        "bearish": bool(e50[-1] and e20[-1] and close < e20[-1] < e50[-1]),
        "uptrend": bool(e50[-1] and e20[-1] and close > e20[-1] > e50[-1]),
        # 6
        "lh_from_last_l": ll is not None,
        # 7
        "breakout": bo is not None,
        # 8 / 9
        "macd_cross_bear": x["macd"]["cross"] == CROSS_DOWN,
        "macd_cross_bull": x["macd"]["cross"] == CROSS_UP,
        # 10 / 11
        "stoch_cross_bear": x["stoch"]["cross"] == CROSS_DOWN,
        "stoch_cross_bull": x["stoch"]["cross"] == CROSS_UP,
        # 12 / 13
        "rsi_bull_divergence": screener.bull_divergence(closes, r),
        "rsi_bear_divergence": bool(e50[-1] and close > e50[-1]
                                    and x["rsi"]["cross"] == CROSS_DOWN),
        # 14 / 15
        "institution_accumulation": None if net is None else net > 0,
        "institution_distribution": None if net is None else net < 0,
        # 16 / 17
        "obv_rising": None if oslope is None else oslope > 0,
        "obv_falling": None if oslope is None else oslope < 0,
        # 18
        "sideways_months": bool(base_score >= 50 and (base_ev.get("box_atr") or 99) <= 4.0),
        # 19
        "good_news": None if news is None else news["positive"] > 0,
        # 20 — the crossing bar must also expand, so a drift through an old high is not a breakout
        "conviction_breakout": bool(
            bo is not None and exp
            and (exp["range_ratio"] or 0) >= EXPAND_RANGE
            and (exp["volume_ratio"] or 0) >= EXPAND_VOLUME),
        # 21 — the mirror of 7
        "breakdown": ll is not None,
        # 22 / 23 — tradability, not a signal
        "liquid": None if adv is None else adv >= LIQUID_MIN_ADV,
        "illiquid": None if adv is None else adv < LIQUID_MIN_ADV,
    }

    missing = sorted(k for k, v in cat.items() if v is None)
    required_passed = [g for g in REQUIRED if cat.get(g)]
    ranked = [g for g in RANKED if cat.get(g)]
    return {
        "categories": cat,
        "required_passed": required_passed,
        "rank_signals": ranked,
        "rank_score": len(ranked),
        "passed": len(required_passed) == len(REQUIRED),
        "missing": missing,
        "context": {
            "close": close, "rsi": x["rsi"]["rsi"], "adx": screener.adx(bars)[-1],
            "obv_slope": oslope, "relative_strength": rs, "expansion": exp,
            "foreign_net": net,
            "foreign_net_sessions": (flow or {}).get("sessions"), "base": base_ev, "crosses": x, "adv20": adv,
            "breakout": bo, "lower_low": ll, "pivot_high": (bo or {}).get("level"),
        },
    }


def entry_plan(code):
    """The entry plan for one candidate: entry, stop, tp1, tp2 and the stop as a percent of entry.

    It lives here, next to the state, because the decider is asked to judge the stop and the
    targets. Computing it is pure arithmetic over the bars we already hold, so there is no reason
    to withhold it from the decision and let the prose fall back to a placeholder. The decider
    still never computes a price itself — it only reads this one.
    """
    from tools import bars_for
    return screener.plan(bars_for(code))


def candidate_state(code, verdict, plan=None):
    """The dict handed to the decider. Only the fields the questions actually ask about — a state
    padded with 40 numbers is how a model ends up confident about something nobody asked it.

    `plan` is this candidate's entry plan, so `verdict`'s "stop defines the risk" criterion has a
    real stop to reason about. Absent inputs stay None all the way through to `decider.render`,
    which states the gap in the prose rather than inventing a value: a fabricated 0.0% stop and a
    fabricated flat flow were both reaching the model on every live decision.
    """
    c = verdict["context"]
    lvl = c.get("pivot_high")
    stop_pct = targets_r = None
    if plan:
        entry = plan["entry"]
        risk = entry - plan["stop"]
        if risk:
            stop_pct = plan["risk_pct"]
            targets_r = [round((plan[k] - entry) / risk, 2) for k in ("tp1", "tp2")]
    return {
        "ticker": f"{code}.JK",
        "tf": "1d",
        "close": c["close"],
        "rsi": c["rsi"],
        "obv_slope_20d": c["obv_slope"],
        "distance_to_significant_high_pct": (c["close"] - lvl) / lvl * 100 if lvl else None,
        # IDX foreign net is raw rupiah, so this is hundreds of millions of IDR, not a percent
        "foreign_net_100m_idr": (c["foreign_net"] / 100_000_000
                                 if c["foreign_net"] is not None else None),
        "foreign_net_sessions": c.get("foreign_net_sessions"),
        "stop_pct": stop_pct,
        "targets_r": targets_r,
        "rank_score": verdict["rank_score"],
        "rank_signals": verdict["rank_signals"],
        "adv20_bn": (c["adv20"] or 0) / 1e9,
    }


def selfcheck(code="BBRI"):
    """One real cached ticker, assert the shape. Returns the verdict so a caller can print it."""
    from tools import bars_for
    bars = bars_for(code)
    v = evaluate(code, bars, index_bars=None, flow=None)
    assert v is not None, f"{code} produced no verdict"
    assert len(v["categories"]) == 23, f"expected 23 category keys, got {len(v['categories'])}"
    assert set(REQUIRED) | set(RANKED) <= set(v["categories"]), "a gate or rank signal is not a category"
    assert v["rank_score"] == sum(1 for g in RANKED if v["categories"][g])
    assert v["rank_score"] == len(v["rank_signals"])
    assert v["passed"] == (len(v["required_passed"]) == len(REQUIRED)), "passed must mean the gate"
    assert all(v["categories"][g] is not None for g in REQUIRED), "a required gate is None"
    return v


if __name__ == "__main__":
    out = selfcheck()
    fired = sorted(k for k, val in out["categories"].items() if val)
    print(f"BBRI required={out['required_passed']} rank={out['rank_score']}/{len(RANKED)} "
          f"{out['rank_signals']} -> passed={out['passed']}")
    print(f"fired: {', '.join(fired)}")
    print(f"missing (input absent): {out['missing'] or 'none'}")
