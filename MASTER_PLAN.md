# trading-jev

**Goal.** Jev decides whether to enter a trade. Momentum swing, daily bars, IHSG universe.

## Layers

| # | Layer | Does | Code |
|---|-------|------|------|
| 1 | Data | Yahoo OHLCV (keyless, 24h cache) + IDX `GetStockSummary` | reuse `trading-tools` + `trading-suite/app/idx.py` |
| 2 | Screen | 19 categories, gates → shortlist of 20–50 | reuse `ihsg_screener.py`, `momentum_ignition.py` |
| 3 | Decide | Jev answers typed questions → probability per candidate | **new, this repo** |
| 4 | Journal | YES → entry plan · NO → evidence supporting the no | **new, this repo** |

Only layers 3 and 4 are new work. Layers 1–2 already exist in three working copies
(`trading-tools` stdlib, `trading-cli` pandas, `trading-suite` FastAPI).

## Universe

**Source of truth: IDX `GetStockSummary`** — one call returns ~963 rows with ticker, name, price,
change, foreign flow and Remarks. It beats the other two copies: `trading-cli`'s list is hardcoded
and was last touched 2026-03-07; `trading-tools`' list is scraped from Wikipedia and carries codes
that have been delisted.

Already implemented in `trading-suite/backend/app/idx.py` — `GetStockSummary` and `suspended_stocks()`.

**Suspended filter:** drop rows whose `Remarks` ends with `X` (IDX's suspended marker). Also drop
UMA names (`Remarks` starts with `--U`) — they cannot be entered.

One call feeds the universe, the suspended filter, the price filter and the actor filter. Do not
fetch these separately.

## Secrets — local only, never pushed

- The the provider key lives in `.env` as `JEV_API_KEY=...`. `.env` is gitignored and never
  committed. Read it with stdlib `os.environ` or a three-line `.env` parse — no `python-dotenv`.
- The app runs only on this laptop. No deploy target, no CI, no container.
- The key was pasted in plaintext into a session transcript. **Rotate it.**

## Significant highs — the pivot rule

"Last 20 bars" is wrong for sideways names: in a ranging stock the 20-bar high is noise, so a
breakout through it means nothing. A **significant high** must clear four tests. Implemented in
`pivots.py`; `ihsg_screener.py` has `swing_lows(xs, order=3)` — lows only, and no significance test.

1. **Dominant** — its high is the highest of the `lookback` (20) bars before it.
2. **Standout** — at least `standout_atr` × ATR(14) above the **mean** high of that same prior
   window. This is the test a bare prominence threshold cannot do: in a flat chart the biggest
   20-bar wiggle is roughly 1 ATR, so a lone ≥1 ATR rule still fires. Requiring the high to stand
   above where price has *been* separates a thrust from a wiggle, and it only looks backwards.
3. **Confirmed** — no bar in the next `confirm` (3) bars exceeds it.
4. **Prominence** — the pullback after it ran ≥ `prom_mult` × ATR(14) below it.

**Measured defaults: `prom_mult=3.5`, `standout_atr=2.0`.** Swept over 886 cached IDX stocks
(6mo, no network):

| prom | stand | 0 pivots | breakouts | ranging names a naive 20-bar high wrongly fired on |
|------|-------|----------|-----------|--------------------------------------------------------|
| 1.5 (Ta4j default) | 1.0 | 10.5% | 243 (27.4%) | 5 |
| 2.5 | 2.0 | 29.5% | 139 (15.7%) | 21 |
| **3.5** | **2.0** | **43.3%** | **78 (8.8%)** | **28** |
| 5.0 | 2.0 | 65.8% | 43 (4.9%) | 50 |

The naive rule fires on 65 (7.3%). At 3.5/2.0 the funnel is the same size (8.8%) but 28 ranging
names are excluded instead of 5. `prom_mult` is the effective knob; `standout_atr` is secondary.

⚠️ This sweep measures signal **frequency, not edge**. Fewer signals is not better signals. It
picks a defensible operating point; the journal decides whether it is a profitable one. It is also
one 6-month snapshot, so the parameters are mildly fitted to it — do not read them as optimal.

This is Ta4j's `ProminenceSwingConfig` defaults (20-bar baseline, 3 confirmation bars, ATR(14),
1 ATR prominence). Chosen because it is the only widely-used formulation that is both
**non-repainting** and **ATR-scaled**: ATR alone adapts to volatility but does *not* reduce false
signals in sideways markets — the bounded baseline is what does that.

⚠️ **Never act on an unconfirmed pivot.** A pivot without its 3 confirmation bars can move or
disappear, and using its historical bar is look-ahead bias that will make the backtest lie. Act on
the confirmed level, never on the bar where the high printed. `pivots.py` proves this: its
self-check asserts that appending 7 more bars never changes or removes a pivot already returned.

**Cost:** a pivot qualifies 20 bars after the fact, because both its windows must be complete
before it counts. That is deliberate — the trade triggers on the *cross*, not on the pivot, so the
lag costs the entry nothing, and it is the price of never cheating. It also means the rule is
conservative: a high with no ≥3.5 ATR decline after it is not significant, which excludes shallow
pullbacks inside strong trends. Raise `prom_mult` only if the journal says pivots are being missed.

**Rejected alternatives** — kept out of v1, revisit only if the journal shows pivot quality is what
kills trades:

| Approach | Why not |
|----------|---------|
| Fractal strength N (N bars each side) | Cheap, and `swing_lows(order=3)` already exists — but N-bars domination alone still fires constantly in sideways names. Good pre-filter, not the definition. |
| Donchian channel (N-bar high) | Already in `ihsg_screener.py` as the breakout screener. Same N-bars weakness this rule replaces. |
| Volume-at-pivot | Add volume confirmation at the pivot bar. Meaningful for IDX goreng names, but one more parameter to tune on no evidence yet. |
| ZigZag by % threshold | Fixed percent ignores volatility. ATR-scaled is strictly better and already chosen. |

## Jev

- `POST https://$JEV_API_URL` — not the app, not `/v1/responses`
- model `jev-1.13-free` (limited-time free; `jev-1.13` is the paid one)
- key in env `JEV_API_KEY`, never committed
- question types: `noul` (yes/no + probability), `choice` (criteria map), `score` (rubric array).
  `str` and `bool` do not exist. Returns value **+ full probability distribution**.
- **Jev cannot compute a price.** No text, no arithmetic. It picks among plans Python builds.
- ~500 input / ~100 output tokens per call. 1000 tickers × 500 ≈ 500k input tokens per full-universe
  run — that is why the shortlist exists.
- The free tier is blocked on `/v1/responses` ("can only be used from within the provider") but **not**
  on `/the structured endpoint`. Verified working.

Questions to ask per candidate:

| question | type | purpose |
|----------|------|---------|
| `verdict` | `choice` | enter / skip |
| `p_enter` | `noul` | probability profitable over 10 bars |
| `momentum_confirmed` | `noul` | is price-volume momentum confirmed |
| `conviction` | `score` | rubric: no edge / weak / solid / textbook |
| `risk` | `choice` | low / medium / high |

**No-trade rule:** `verdict.probabilities.enter < THRESHOLD` → flat. The threshold is a number, not
the model's mood. Calibrate it from the journal, not by intuition.

## Categories — 5 gates, 14 descriptors

**Pre-gate 1 — price.** Only stocks priced under IDR 1,000. This is the goreng band, and it is the
band `momentum_ignition.py` was already written for. Price comes from the same `GetStockSummary`
call that builds the universe — no extra fetch.

**Pre-gate 2 — actor filter.** Foreign net buy over 20d must be positive. This is a gate because
`trading-cli`'s own research concluded OHLCV momentum has no proven out-of-sample edge without an
actor filter.

A stock that fails either pre-gate never reaches Jev and never costs a Jev call.

**Gates** — must pass to reach Jev:
1. Stock creating a new significant high above the last confirmed significant high
7. Stock breaking out above the last confirmed significant high
9. MACD crossing (bull)
12. RSI bull divergence
16. OBV raising

Categories 1 and 7 use the pivot rule above, not a bar count.

**Descriptors** — context Jev reads, never block:
2. not following the main index · 3. following the main index · 4. currently bearish ·
5. currently uptrend · 6. creating LH from last L · 8. MACD crossing (bear) · 10. Stochastic
crossing (bear) · 11. Stochastic crossing (bull) · 13. RSI bearish divergence · 14. Institution
accumulation · 15. Institution distribution · 17. OBV downside · 18. sideways for N month ·
19. good news a few days/month ago

## Entry plan and the no-case

Layer 3 approves, layer 4 records. Both branches write a row.

- **YES** → one plan per candidate: entry, `stop = entry − 1.5·ATR`, TP1 1R, TP2 2R (already
  implemented in `ihsg_screener.py`). One plan, not three — add a second only if the journal shows
  entry timing is where trades die.
- **NO** → the evidence row: Jev's per-question probabilities **and** which of the 19 categories
  fired or failed, with numbers. A probability of 0.31 is unreadable without the category table.
  This row is also what later answers "was the no right?".

## Validation

1. Indicator math — assert-based `demo` self-checks, no network, copied from `trading-tools`.
2. Strategy edge — `momentum_ignition.py backtest` already exists; run it before trusting anything.
3. **Jev accuracy** — bucket candidates by the probability Jev returned, then hit rate per bucket.
   This is the metric that decides whether Jev stays. The API returns the probabilities; the rest is
   a journal query.

## What this project is not

Not a broker. No orders are placed. Every run writes decisions; a human presses the buy.

## Still open — decisions, not blockers

- `THRESHOLD` for `enter` — set from the journal after the first ~50 scored candidates.
- Pivot parameters (1×ATR prominence, ±20 baseline, 3 confirm) — defensible defaults, not proven
  best. Measure against the alternatives above on real trades; no independent evidence says any one
  method is universally best.
- the structured endpoint rate limits — untested. Measure on the first full run.
