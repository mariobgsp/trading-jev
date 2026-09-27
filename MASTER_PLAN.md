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

## Categories — 1 required gate, 4 rank signals, 16 descriptors

**Pre-gate 1 — price.** Only stocks priced under IDR 1,000. This is the goreng band, and it is the
band `momentum_ignition.py` was already written for. Price comes from the same `GetStockSummary`
call that builds the universe — no extra fetch.

**Pre-gate 2 — actor filter.** Foreign net buy must be positive. This is a gate because
`trading-cli`'s own research concluded OHLCV momentum has no proven out-of-sample edge without an
actor filter. The window is however many sessions have accumulated, starting at 1 — a 20-day
figure costs 20 calls, so the day's rows are stored instead and the window grows with use.

A stock that fails either pre-gate never reaches Jev and never costs a Jev call.

**Required gate** — must pass to reach Jev:
7. Breakout above the last confirmed significant high. This is the setup; the pivot rule is what
makes the level mean something.

**Rank signals** — confirm and order the shortlist, never block:
9. MACD crossing (bull) · 12. RSI bull divergence · 16. OBV raising · 1. HH from last H

**Descriptors** — context Jev reads, never block:
2. not following the main index · 3. following the main index · 4. currently bearish ·
5. currently uptrend · 6. creating LH from last L · 8. MACD crossing (bear) · 10. Stochastic
crossing (bear) · 11. Stochastic crossing (bull) · 13. RSI bearish divergence · 14. Institution
accumulation · 15. Institution distribution · 17. OBV downside · 18. sideways for N month ·
19. good news a few days/month ago · 20. conviction breakout (7 plus range and volume expansion) ·
21. breakdown (close below the last confirmed significant low)

### Why one gate and not five — measured, not assumed

The plan originally ANDed five gates. Over the **231** gorengan names that cleared the flow
pre-gate, that produced **0.0%** — a system that can never fire. Per-gate pass rates:

| category | pass rate |
|---|---|
| 16 OBV raising | 59.6% |
| 9 MACD bull cross | 13.6% |
| 7 breakout | 5.1% |
| 12 RSI bull divergence | 1.5% |
| 1 HH from last H | 0.5% |

Two structural reasons, not bad luck:

- **12 is temporally opposed to 7.** An RSI bull divergence fires *before* price rises; a
  breakout confirms the move has begun. Requiring both on the same bar asks for a contradiction.
  Confirmed: the 6 names that cleared the three core gates had **zero** bonus signals.
- **1 is a subset of 7** (it also needs the prior pivot to be lower), so the two counted one idea
  twice.

Adding 16 to 7 only moves 4.8% → 3.9%, so 16 is not a gate either — it is context. Hence one
required structural gate, with the other four as rank.

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

## What is built

| file | does |
|------|------|
| `tools.py` | the single owner of the trading-tools dependency (loaded by path) |
| `pivots.py` | significant highs and lows, `breakout`, `hh_breakout`, `lower_low`; self-check proves no repainting |
| `idx.py` | `GetStockSummary` over stdlib http.client — universe, suspended filter, float, foreign flow |
| `store.py` | SQLite: `idx_daily` (also the 24h cache) and `decision` (the journal), plus `report()` |
| `screen.py` | the 21 categories, the required gate, the rank score |
| `jev.py` | the the structured endpoint client and the 5 typed questions |
| `run.py` | the pipeline: `screen` · `run` · `resolve` · `analyze` |

```bash
python3 run.py screen --limit 200        # universe -> shortlist, no API calls
python3 run.py run --limit 200 --top 15  # ...then ask Jev and journal every answer
python3 run.py analyze --since 7d        # this week; 30d, 90d, all, or YYYY-MM-DD
python3 run.py resolve                   # just refill outcomes
python3 pivots.py demo                   # no network
python3 jev.py demo                      # one real call
```

`analyze` resolves anything pending, then reports over the window: scored / entered / vetoed,
hit rate, cumulative R and an equity curve, Jev's calibration by `p_enter` bucket, and the
per-decision list. **R, not percent**, because a 3% stop and a 9% stop make a +4% move different
trades; R divides by the risk that was actually planned.

**Measured funnel, session 20260925:** 963 listed → 804 tradable (159 suspended) → 611 under
IDR 1,000 → 233 with foreign buying → 231 scored → **11 cleared the required gate** → 11 asked,
**11 vetoed**. `curl_cffi` is not needed anywhere: IDX answers a bare stdlib request.

⚠️ `idx_daily` stores `Remarks`, but only the trailing `X` is trusted as suspended. A leading
`--U` is **not** a UMA marker — 611 of 963 rows carry it, including ordinary large caps like
ABBA and ABDA. Filtering on it deletes two thirds of the market.

## Still open — decisions, not blockers

- `THRESHOLD` for `enter`, set at 0.6 provisionally. Day one gave a **100% veto rate** (11
  candidates, `p_enter` 0.00–0.16, conviction 0.8–1.6 of 3). That is a plausible read of a thin
  session, but it is also what an over-confident no looks like. Judge it over ~50 scored
  candidates, not one day.
- Pivot parameters (1×ATR prominence, ±20 baseline, 3 confirm) are swept and defensible; tighten
  or loosen from the journal, not from the sweep.
- The actor filter is currently a **single session** of foreign flow — the weakest input in the
  system, and the one `trading-cli` says matters most. The window grows automatically as
  `idx_daily` accumulates, so re-check the gate once ~20 sessions exist.
- Whether one required gate is too loose or too tight. The shortlist was 11 names on day one.
- The entry plan uses the **decision bar's close** as the entry, not the next open. Realistic
  enough for a swing system, but it flatters results slightly, because in practice you cannot
  fill at a close you only saw at 16:00. Revisit when there is enough resolved history to see
  whether it matters.
- Only one session is stored (20260925), so the foreign-flow gate is a single day and the whole
  journal is one session deep. Everything time-windowed needs a few weeks before it says
  anything.

## Three silent bugs, found and fixed

Recorded because each one failed *quietly* rather than loudly:

1. **`resolve` could never score anything a week old.** It gated on "not among the last 10 stored
   sessions", but a week is ~5 sessions — so the obvious workflow (analyze next week) would
   always have found nothing to score. The gate is gone: `resolve` now attempts everything and
   reports `open` when too few bars have passed, which is the truthful answer.
2. **Trades older than 6 months were silently unscorable.** Resolution fetched a `6mo` range, so
   a decision from last spring had no bars reaching back to its session and was dropped. It now
   fetches `2y`, and anything still unresolvable is counted and printed rather than skipped.
3. **`accuracy` dropped open trades from the hit rate.** Flattering by construction — the
   unproven trades vanished. `open` and `unresolvable` are now their own counts.

Also fixed: the walk started *on* the entry bar, so a trade could be stopped out on the bar it
was entered from. Entry is at that bar's close, so the walk now starts after it. Proven by
differential test against an independent walk, and `p_enter` buckets are bucketed with `Decimal`
because `0.75/0.1` is exactly `7.5` in binary floating point and lands in the wrong bucket.
