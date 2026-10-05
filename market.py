#!/usr/bin/env python3
"""Every IDX exchange rule and every cost this program charges, in one file.

Nothing else in the repo is allowed to hardcode a tick size, a band, a lot, a floor or a
fee rate: a backtest that quietly assumes the wrong tick or the wrong cost is worse than no
backtest, because it looks rigorous. If a rule changes, it changes here and the change is
one diff.

Provenance is per constant, not per file. VERIFIED means the number was confirmed against a
named external source on VERIFIED_ON; ASSUMED means it is the best available reading and
someone should confirm it. The ASSUMED list is `unverified()`, so a report can print exactly
what it is standing on.

Sources, from docs/DECISIONS.md:
  * bands, min price, min lot - IDX rule change 28 Sep 2026, via HBT Law's regulatory
    summary, corroborated by Phintraco Sekuritas in the Jakarta Globe.
  * costs - Ajaib's published all-in retail rates.
  * tick ladder below Rp5,000 - the published fraksi harga table.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, replace

VERIFIED_ON = "2026-10-05"

# ======================================================================================
# IDX EXCHANGE RULES
# ======================================================================================

# --- fraksi harga (tick ladder). VERIFIED below Rp5,000; the band above it is ASSUMED. ---
# (EXCLUSIVE upper bound, tick in rupiah): a price of exactly Rp200 sits in the
# Rp2 band, not the Rp1 one. Monotonic, walked in order by tick_size().
TICK_LADDER = (
    (200.0, 1.0),
    (500.0, 2.0),
    (2000.0, 5.0),
    (5000.0, 10.0),
)
# Above Rp5,000 the sources disagree: the published ladder continues 25/50/100/250/500/
# 1000/2000, one source claims a flat Rp25. A flat Rp25 is the conservative reading for
# anything we round to a tradable price, so that is what this uses.
TICK_ABOVE_5000 = 25.0  # ASSUMED - sources conflict

# --- auto-rejection (ARA/ARB) bands. VERIFIED. ---
# The bands are asymmetric until 2026-12-31: the lower band is wider than the upper one, so
# a stop placed below the lower band often cannot fill at the price it names.
ARA_ARB_FLAT_BAND_TOP = 10.0   # at or below this reference price the band is a flat Rp1
ARA_ARB_FLAT_BAND = 1.0
BANDS_SYMMETRIC_FROM = _dt.date(2027, 1, 1)

# (reference price ceiling, ARA transitional, ARB transitional, ARA permanent, ARB permanent)
# ARA is the floor a sell order may not print under; ARB is the ceiling a buy may not print
# over. Rows are walked in order; the last row has no upper bound.
BAND_TABLE = (
    (200.0, 0.35, 0.15, 0.35, 0.35),
    (5000.0, 0.25, 0.15, 0.25, 0.25),
    (float("inf"), 0.20, 0.15, 0.20, 0.20),
)
# --- lot and price floors. VERIFIED. ---
MIN_LOT = 100                 # one board lot, in shares
MIN_PRICE = 1.0               # Rp50 until 2026-09-27, Rp1 from 2026-09-28
ODD_LOT_MAX_SHARES = 100      # an odd lot is a partial board lot, tradable sell-side only

# ======================================================================================
# MODEL UNIVERSE
# ======================================================================================
# The screen shows every listed name. The *model* gets a narrower set, because two things
# break a signal below these lines: at Rp1-Rp10 a single tick is a 10-100% move, so a
# percentage stop is meaningless; and below ~Rp200 a name's ATR band is inside the noise of
# its own bid/ask spread, so the backtest buys fills that do not exist.
MODEL_MIN_PRICE = 200.0       # VERIFIED as the band boundary DECISIONS gates on
MODEL_MIN_TURNOVER_IDR = 5_000_000_000.0  # ASSUMED: Rp5bn/day average traded value
# A suspected unexplained ex-date gap makes the vendor's price levels untrustworthy. The
# ticker stays out of the model until a month of clean sessions has printed after it.
QUARANTINE_SESSIONS = 20

# ======================================================================================
# TRANSACTION COSTS - all-in retail rates, VERIFIED
# ======================================================================================
# Ajaib publishes the all-in rate rather than the pieces, but the pieces it lists reconcile
# exactly with the all-in figures, so both are kept and the decomposition is checked below.
LEVY_RATE = 0.000433           # BEI + KPEI + KSEI, 0.0433%
PPN_ON_BROKER_RATE = 0.11      # value-added tax, on the broker fee ONLY
PPH_FINAL_SELL_RATE = 0.001    # final income-tax withholding, sells only
BUY_ALL_IN_RATE = 0.001513     # 0.1513%
SELL_ALL_IN_RATE = 0.002513    # 0.2513%
# Implied by the two all-in rates: everything except the levy and the PPh is the broker fee
# plus its PPN, and both sides give the same answer, which is the cross-check.
BROKER_FEE_RATE = 0.000973     # 0.0973%, ASSUMED-but-reconciled: see _check_cost_decomposition
ROUND_TRIP_RATE = 0.004026     # 0.4026%, ASSUMED-but-reconciled, matches "approx 0.40%"


def _check_cost_decomposition():
    """The published all-in rates and the published components must agree to 1e-6 of notional.

    That is tight enough to catch a mistyped digit and loose enough to allow for the broker
    fee being quoted rounded to four decimals - 0.0973% published against 0.097297% implied.
    If a broker's levy or tax changes, whichever side is wrong has to fail loudly here rather
    than quietly skew every expectancy number in the project.
    """
    buy = BROKER_FEE_RATE * (1.0 + PPN_ON_BROKER_RATE) + LEVY_RATE
    sell = buy + PPH_FINAL_SELL_RATE
    for got, want, what in (
        (buy, BUY_ALL_IN_RATE, "buy"),
        (sell, SELL_ALL_IN_RATE, "sell"),
        (buy + sell, ROUND_TRIP_RATE, "round trip"),
    ):
        if abs(got - want) > 1e-6:
            raise ValueError(
                f"cost model does not reconcile: {what} components give "
                f"{got:.6f} but the published all-in rate is {want:.6f}"
            )


_check_cost_decomposition()

ASSUMED_CONSTANTS = (
    ("TICK_ABOVE_5000", TICK_ABOVE_5000,
     "sources conflict above Rp5,000: published ladder vs flat Rp25"),
    ("MODEL_MIN_TURNOVER_IDR", MODEL_MIN_TURNOVER_IDR,
     "chosen for a swing horizon, not derived from a source"),
)


def unverified():
    """[(name, value, why)] for every constant this file is standing on an assumption for."""
    return [(n, v, w) for n, v, w in ASSUMED_CONSTANTS]


# ======================================================================================
# TICK AND LOT
# ======================================================================================

def tick_size(price):
    """The rupiah increment a price at `price` must sit on."""
    for ceiling, tick in TICK_LADDER:
        if price < ceiling:
            return tick
    return TICK_ABOVE_5000


def round_to_tick(price, how="nearest"):
    """Snap a price onto the fraksi harga ladder. Returns whole rupiah.

    Rounding a level onto a tick is not cosmetic: an off-tick order is not a price the
    exchange will accept, so a backtest that fills it is filling a trade nobody could make.
    `how` is "nearest", "down" or "up" - "down" is the conservative direction for a stop,
    because a stop that rounds up is closer to entry than the level it claims to be.
    """
    if price is None or price <= 0:
        return 0
    tick = tick_size(price)
    steps = price / tick
    if how == "down":
        whole = int(steps)
    elif how == "up":
        whole = int(-(-steps // 1))
    else:
        whole = int(steps + 0.5)
    snapped = whole * tick
    if snapped <= 0:
        snapped = tick
    return int(round(snapped))


def round_to_lot(shares, lot=MIN_LOT):
    """Floor a share count to whole board lots.

    Floors rather than rounds: rounding up invents a lot the account may not be able to buy,
    and the resulting average entry is one that cannot be reproduced in the market.
    """
    if not shares or shares <= 0:
        return 0
    return int(shares // lot) * lot


def is_odd_lot(shares):
    """True when the count is a partial board lot - sell-side only on IDX."""
    return bool(shares) and 0 < shares < ODD_LOT_MAX_SHARES


def shares_for(price, capital=None, lots=None):
    """Board-lot share count for a budget or a lot count. Never an odd lot."""
    if lots is not None:
        return round_to_lot(lots * MIN_LOT)
    if capital is None or price is None or price <= 0:
        return 0
    return round_to_lot(int(capital // price))


# ======================================================================================
# AUTO-REJECTION BANDS
# ======================================================================================

@dataclass(frozen=True)
class Band:
    """The tradable band around a reference price on one date."""
    reference: float
    lower: float      # ARA - a sell may not print under it
    upper: float      # ARB - a buy may not print over it
    ara: float
    arb: float
    flat: bool        # True when the band is a fixed rupiah amount, not a percentage
    symmetric: bool   # True once the permanent table is in force

    def contains(self, price):
        return self.lower <= price <= self.upper

    def clamp(self, price, how="down"):
        """Pull a price inside the band. A stop outside the band cannot fill as priced."""
        if price > self.upper:
            return round_to_tick(self.upper, how)
        if price < self.lower:
            return round_to_tick(self.lower, "up")
        return round_to_tick(price, how)


def band_for(reference, on=None):
    """The ARA/ARB band around `reference` as it stood on `on`.

    `on` decides which table applies. Historical backtests must ask for the historical date:
    the bands widened for one year and then narrowed again, so a backtest run with today's
    table silently misprices every 2026 fill.
    """
    if reference is None or reference <= 0:
        return None
    day = _as_date(on)
    symmetric = day >= BANDS_SYMMETRIC_FROM
    if reference <= ARA_ARB_FLAT_BAND_TOP:
        return Band(reference, reference - ARA_ARB_FLAT_BAND,
                    reference + ARA_ARB_FLAT_BAND, ARA_ARB_FLAT_BAND,
                    ARA_ARB_FLAT_BAND, flat=True, symmetric=symmetric)
    for floor, ara_t, arb_t, ara_p, arb_p in BAND_TABLE:
        if reference <= floor:
            ara, arb = (ara_p, arb_p) if symmetric else (ara_t, arb_t)
            return Band(reference, reference * (1.0 - ara), reference * (1.0 + arb),
                        ara, arb, flat=False, symmetric=symmetric)
    raise AssertionError("BAND_TABLE has no unbounded final row")


def in_band(price, reference, on=None):
    """True when `price` could actually have printed on the tape that day."""
    band = band_for(reference, on)
    return False if band is None else band.contains(price)


def band_violation(price, previous_close, on=None):
    """Why `price` is not tradeable against `previous_close`, or None when it is.

    `previous_close` is the exchange's own reference price. Tick validity is checked too:
    an off-tick price is not tradeable for an unrelated reason, and conflating the two would
    hide a rounding bug inside a band explanation.
    """
    band = band_for(previous_close, on)
    if band is None:
        return None
    if price > band.upper:
        side, limit = "ARB", band.upper
    elif price < band.lower:
        side, limit = "ARA", band.lower
    else:
        side = limit = None
    on_tick = abs(price - round_to_tick(price)) < 1e-9
    if side is None and on_tick:
        return None
    return {
        "side": side, "limit": limit, "price": price,
        "reference": previous_close, "on": _as_date(on).isoformat(),
        "symmetric": band.symmetric,
        "reason": (f"{side}: {price:.0f} beyond the {limit:.0f} band"
                   if side else f"{price:.0f} is off the Rp{tick_size(price):.0f} tick"),
    }


def _as_date(on):
    if on is None:
        return _dt.date.today()
    if isinstance(on, _dt.datetime):
        return on.date()
    if isinstance(on, _dt.date):
        return on
    return _dt.date.fromisoformat(str(on)[:10])


# ======================================================================================
# COST MODEL
# ======================================================================================

@dataclass(frozen=True)
class CostModel:
    """All-in buy/sell cost, as a rate on traded notional.

    Frozen and default-constructed from the constants above, so `CostModel()` is the one
    real model and `at(2)` is the stress case rather than a second set of literals.
    """
    broker_fee: float = BROKER_FEE_RATE
    levy: float = LEVY_RATE
    ppn_on_broker: float = PPN_ON_BROKER_RATE
    pph_final_sell: float = PPH_FINAL_SELL_RATE
    multiple: float = 1.0

    @property
    def buy_rate(self):
        """Fraction of notional lost buying. Levy plus broker fee plus its PPN."""
        return (self.broker_fee * (1.0 + self.ppn_on_broker) + self.levy) * self.multiple

    @property
    def sell_rate(self):
        """Same, plus the final income-tax withholding, which is sells-only."""
        return (self.buy_rate + self.pph_final_sell * self.multiple)

    @property
    def round_trip_rate(self):
        return self.buy_rate + self.sell_rate

    def at(self, multiple):
        return replace(self, multiple=self.multiple * multiple)

    def cost(self, notional, side="buy"):
        return notional * (self.sell_rate if side == "sell" else self.buy_rate)

    def round_trip_cost(self, notional):
        return notional * self.round_trip_rate

    def trade(self, capital, price):
        """One whole-lot entry: shares, notional, and what the entry alone costs."""
        shares = shares_for(price, capital=capital)
        notional = shares * price
        return {
            "shares": shares, "lots": shares // MIN_LOT if shares else 0,
            "notional": notional, "buy_cost": self.cost(notional, "buy"),
            "odd_lot": is_odd_lot(shares),
        }

    def sensitivity(self, multiples=(0.5, 1.0, 2.0)):
        """Cost at 0.5x / 1x / 2x. Always reported, never optional: a result that only
        holds at 1x has not survived anything."""
        return [(m, self.at(m).round_trip_rate) for m in multiples]


COSTS = CostModel()


def cost_sensitivity(multiples=(0.5, 1.0, 2.0)):
    """[(multiple, round-trip fraction)] at the published rates."""
    return COSTS.sensitivity(multiples)


# ======================================================================================
# PRICE-SERIES HYGIENE
# ======================================================================================
# KNOWN LIMITATION, and the reason this file exists half as much as the DECISIONS record
# assumes. Our only price source is the vendor's daily bars, and it hands us a series whose
# CLOSES are adjusted for corporate actions while its OPEN/HIGH/LOW are not. For a plain
# split or dividend that rescale is proportional and harmless. For an IDX rights issue it is
# not: a rights subscription at a discount is not a proportional rescale, the vendor applies
# it silently, and it back-adjusts every earlier level. The consequence is that an older
# price in the series may be off by the size of the discount, which is exactly the sort of
# error that a tick rule, a band rule and a Rp200 price gate all inherit silently.
#
# There is no free IDX corporate-action feed to repair this from - the exchange 403s
# automated fetches and no other free source exists. So this module does the two things it
# can do honestly:
#
#   1. Detect where the vendor's adjustment disagrees with the same bar's own raw range,
#      and where a one-day move breaks a band the exchange says cannot break. Either one is
#      a corporate action we were never told about, or bad data.
#   2. Refuse to guess. A rights discount cannot be inferred from the tape, so no rescale is
#      applied to the raw levels; the ticker is quarantined out of the model universe for
#      QUARANTINE_SESSIONS instead, and the screen still shows it on raw levels.

def _bar_date(bar):
    """The bar's session date. Vendor bars carry a Unix epoch, not a date."""
    t = bar.get("t")
    if isinstance(t, (int, float)):
        return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).date()
    return _as_date(t)


def suspicious_bars(bars, epsilon=0.005):
    """Indices of bars whose close cannot be the raw print of their own range.

    The vendor adjusts `c` and leaves `o`/`h`/`l` raw, so a close that falls outside
    its own high/low is a close that was moved by a factor: the day carries a corporate
    action. Epsilon absorbs the bar's own rounding.
    """
    out = []
    for i, b in enumerate(bars):
        c, lo, hi = b.get("c"), b.get("l"), b.get("h")
        if not c or not lo or not hi or hi <= lo:
            continue
        if c < lo * (1.0 - epsilon) or c > hi * (1.0 + epsilon):
            out.append(i)
    return out


def detect_gaps(bars, on=None, suspicious=None):
    """Suspected unexplained ex-date gaps: {index, date, kind, magnitude}.

    Two independent signals, because either alone has a cheap false positive:
      * "adjusted" - the vendor's close sits outside its own raw high/low, so a factor was
        applied to this bar.
      * "band-break" - a one-day close-to-close move larger than the band that price could
        legally have moved in. The exchange would have auto-rejected it, so the tape says
        something happened that we were not told about.
    """
    suspect = set(suspicious_bars(bars) if suspicious is None else suspicious)
    gaps = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        if not prev.get("c") or not cur.get("c") or prev["c"] <= 0:
            continue
        move = cur["c"] / prev["c"] - 1.0
        # Historical bars are checked against the bands that were in force THAT day, never
        # against today's - the table widened for 2026 and narrows again in January.
        band = band_for(prev["c"], _bar_date(prev) if on is None else on)
        if band is None:
            continue
        broke = move > band.arb or move < -band.ara
        adjusted = i in suspect
        if not (broke or adjusted):
            continue
        kind = "band-break" if broke else "adjusted"
        if broke and adjusted:
            kind = "band-break+adjusted"
        gaps.append({
            "index": i, "date": _bar_date(cur).isoformat(),
            "kind": kind, "move": round(move, 4),
            "magnitude": round(abs(move), 4),
        })
    return gaps


def normalise_series(bars):
    """One pass over a vendor series: keep it, tag it, never rescale it.

    Returns a new list - the vendor's cached list is never mutated, so calling this twice on
    the same fetch gives the same answer rather than applying an adjustment twice. Each bar
    gains:
      * `c_raw` - the raw close, or None on a bar whose raw close the vendor moved and did
        not disclose. Downstream math uses `c`, the vendor's continuous series, which is
        correct for ratios; level rules must use a raw price and say which one they used.
      * `adj`    - True on that same bar, so a level rule can refuse to use it.
      * `gap`    - True where a suspected ex-date gap printed.
    """
    suspicious = suspicious_bars(bars)
    gaps = detect_gaps(bars, suspicious=suspicious)
    flagged = {g["index"] for g in gaps}
    out = []
    for i, b in enumerate(bars):
        nb = dict(b)
        moved = i in suspicious
        nb["adj"] = moved
        nb["c_raw"] = None if moved else b.get("c")
        nb["gap"] = i in flagged
        out.append(nb)
    return out


def quarantine(bars, as_of=None):
    """Whether this series must stay out of the model universe, and why.

    The window runs from the last suspected gap to the end of the series we hold: until a
    month of clean sessions has printed after the gap, nothing downstream can be trusted to
    be working in real rupiah.
    """
    if not bars:
        return {"quarantined": False, "reason": None, "gaps": []}
    gaps = detect_gaps(bars)
    if not gaps:
        return {"quarantined": False, "reason": None, "gaps": []}
    last = gaps[-1]
    since = len(bars) - 1 - last["index"]
    if since >= QUARANTINE_SESSIONS:
        return {"quarantined": False, "reason": None, "gaps": gaps,
                "cleared_on": _bar_date(bars[last["index"]]).isoformat()}
    return {
        "quarantined": True,
        "reason": (f"suspected unexplained gap on {last['date']} "
                   f"({last['kind']}, {last['move']:+.1%}), "
                   f"{since} clean session{'s' if since != 1 else ''} since, "
                   f"needs {QUARANTINE_SESSIONS}"),
        "gaps": gaps, "gap": last,
    }


def model_universe_eligible(bars, price=None, turnover=None, on=None):
    """The model-universe gate: liquid enough, big enough, and not quarantined.

    Returns (ok, reason). The screen does not call this - it shows every listed name - so
    the gate only ever narrows what the model is asked to have an opinion about.
    """
    q = quarantine(bars)
    if q["quarantined"]:
        return False, q["reason"]
    if price is None and bars:
        price = bars[-1].get("c_raw") or bars[-1].get("c")
    if price is None or price < MIN_PRICE:
        return False, f"close {price} below the Rp{MIN_PRICE:.0f} exchange floor"
    if price < MODEL_MIN_PRICE:
        return False, f"close Rp{price:,.0f} below the Rp{MODEL_MIN_PRICE:,.0f} model floor"
    if turnover is not None and turnover < MODEL_MIN_TURNOVER_IDR:
        return False, (f"Rp{turnover:,.0f}/day turnover below the "
                       f"Rp{MODEL_MIN_TURNOVER_IDR:,.0f} floor")
    if bars:
        breach = band_violation(price, bars[-2].get("c") or price, on=on)
        if breach:
            return False, f"last close is not tradeable ({breach['reason']})"
    return True, None


def describe():
    """One dict of everything this file asserts, for a report to print."""
    return {
        "verified_on": VERIFIED_ON,
        "tick_ladder": [{"up_to": c, "tick": t} for c, t in TICK_LADDER],
        "tick_above_5000": TICK_ABOVE_5000,
        "bands": [{"from": "above Rp5,000" if f == float("inf") else f"Rp{f:,.0f}",
                   "ara_transitional": a, "arb_transitional": b,
                   "ara_permanent": ap, "arb_permanent": bp}
                  for f, a, b, ap, bp in BAND_TABLE],
        "bands_symmetric_from": BANDS_SYMMETRIC_FROM.isoformat(),
        "min_lot": MIN_LOT, "min_price": MIN_PRICE,
        "costs": {"buy": COSTS.buy_rate, "sell": COSTS.sell_rate,
                  "round_trip": COSTS.round_trip_rate,
                  "sensitivity": COSTS.sensitivity()},
        "model_universe": {"min_price": MODEL_MIN_PRICE,
                           "min_turnover": MODEL_MIN_TURNOVER_IDR,
                           "quarantine_sessions": QUARANTINE_SESSIONS},
        "assumed": unverified(),
    }
