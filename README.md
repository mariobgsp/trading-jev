# trading-jev

A momentum screen for the IDX gorengan band where **Jev decides whether to enter**, and a journal
that scores whether Jev was right.

Python fetches the data and computes 23 categories, Jev scores the shortlist through the provider's
structured endpoint, and every decision — ENTER *and* SKIP — is journalled so you can find out
later whether the model earns its place. No orders are placed. A human presses the buy.

`MASTER_PLAN.md` is the design record: why each decision was made, and what the measurements
were. This file is how to run it.

---

## Requirements

- **Python 3.9 or newer** (developed and tested on 3.14). **Standard library only** — there is no
  `pip install` step and no `requirements.txt`, because there is nothing to install.
- **Network access** to `idx.co.id` (universe, suspended list, foreign flow) and Yahoo Finance
  (OHLCV).
- **The `trading-tools` repo** beside this one, at `~/Projects/trading-tools`. This project
  borrows its verified indicators, its Yahoo fetcher and its entry plan rather than
  reimplementing them. If yours lives elsewhere, set `TRADING_TOOLS_DIR`.
- **An the provider zen API key** — the account credential from <https://the provider's account page>. The key
  already in `an environment file you may already have` works (verified), so if you have one you can skip the `.env`
  and just `export JEV_API_KEY=...`. Jev is reached at `/v1/the structured endpoint`; the free tier
  is rejected on `/v1/responses` with "can only be used from within the provider", so an endpoint
  that works for the rest of pi may not be the right one here.

## Setup

```bash
cp .env.example .env      # then put your key in it
$EDITOR .env              # JEV_API_KEY=KEYPREFIX_...
```

Or skip the file entirely if you already have a key in your shell: `export JEV_API_KEY=...`.
`.env` is read only if the variable is not already set.

Check the three moving parts before trusting a run:

```bash
python3 pivots.py demo    # pivot rule, no network
python3 jev.py demo       # one real Jev call, prints both state renderings
python3 idx.py refresh    # the IDX snapshot, prints the filter funnel
```

`python3 run.py --help` lists every command. The database (`trading-jev.db`) is created on first
use; nothing else needs initialising.

---

## Daily use

```bash
python3 run.py run --limit 200 --top 15
```

One pass, six steps:

1. **Universe** — one `GetStockSummary` call returns ~963 rows. Suspended names (trailing `X` in
   `Remarks`) are dropped. This is the whole universe in a single request.
2. **Pre-gates** — price under IDR 1,000 (the goreng band), and foreign net buy positive. Both
   come out of the stored snapshot, so they cost nothing.
3. **Fetch bars** — daily OHLCV for the surviving candidates, through trading-tools' cached,
   politeness-paced fetcher (8 workers max). `--limit` caps this.
4. **23 categories** — two required gates plus four rank signals, from the bars, the `^JKSE`
   series and the IDX float.
5. **Jev** — the shortlist is scored, ranked, and the top `--top` are sent to Jev.
6. **Journal** — every answer is written down, SKIP included.

A run prints the funnel, the shortlist, and one line per decision. On 20260925 it ended:

```text
scored 231, cleared the required gate: 10
  MGNA   p=0.14 SKIP  rank=2 conv=1.5 risk=medium
  ...
journalled 10 decisions (session 20260925)
```

**A SKIP is a real answer, not a failure.** A day where Jev declines everything is a day the
market had nothing. `analyze` reports the veto rate precisely so you can tell a cautious model
from a broken one.

To screen without spending any Jev calls:

```bash
python3 run.py screen --limit 200
```

---

## Reviewing later

```bash
python3 run.py watchlist                 # every name ever surfaced, and what it did after
python3 run.py analyze --since 7d        # this week
python3 run.py analyze --since 30d       # this month
python3 run.py analyze --since all
python3 run.py resolve                   # just refill outcomes
```

`analyze` first resolves anything pending, then reports over the window:

- **scored / entered / vetoed**, and the veto rate
- **hit rate**, counted only over trades that actually resolved, with `open` and `unresolvable`
  shown as their own counts — never folded in, because dropping them flatters the number
- **cumulative R and an equity curve.** R, not percent: a +4% move on a 3% stop and on a 9% stop
  are different trades, so R divides by the risk that was actually planned.
- **Jev's calibration by `p_enter` bucket.** The metric that decides whether Jev stays. If the
  high-probability buckets are no better than the low ones, the model is noise and the honest
  response is to delete layer 3.
- **The cohort** — every surfaced name and its forward returns at +5/+10/+20 bars with MFE and
  MAE, *including the ones Jev vetoed*. This is the part that scores your vetoes.

A name counts as `EXCLUDED` only if a **complete** scan ran without it. A partial scan (see
`--limit`) proves nothing about exclusion, and with no complete scan on record the status reads
`UNKNOWN` rather than guessing.

---

## How a decision is actually made

| layer | owns |
|---|---|
| data | `idx.py` — IDX snapshot and foreign flow; `tools.py` — Yahoo bars |
| screen | `pivots.py` + `screen.py` — significant highs/lows, the 23 categories |
| decide | `jev.py` — five typed questions, one structured call |
| journal | `store.py` — the SQLite record everything is scored from |

**Two required gates** — both must pass:

- `breakout` — close above the last confirmed **significant** high. "Significant" means the high
  cleared four tests: it dominated the prior 20 bars, stood ≥2×ATR above the *mean* high of that
  window, was not exceeded for 3 bars, and then gave back ≥3.5×ATR. A ranging stock has no
  significant high at all, which is the sideways filter — the absence *is* the signal.
- `liquid` — average daily turnover ≥ IDR 1bn over 20 bars. A tradability constraint, not a
  signal: an illiquid entry is one you cannot exit.

**Four rank signals** order the shortlist but never block: `obv_rising`, `macd_cross_bull`,
`hh_from_last_h`, `rsi_bull_divergence`.

The rest are descriptors — context Jev reads, recorded in the evidence row so a `p_enter` of
0.31 is readable rather than meaningless.

**Jev's five questions**, asked in one call, all of them returning probabilities:

| question | type | asks |
|---|---|---|
| `verdict` | `choice` | enter, or skip |
| `p_enter` | `noul` | probability of profit over 10 bars |
| `momentum_confirmed` | `noul` | is price-volume momentum confirmed |
| `conviction` | `score` | rubric: no edge / weak / solid / textbook |
| `risk` | `choice` | low / medium / high |

Jev **cannot compute a price**. It returns typed values only, so the entry plan is Python's job
(`stop = entry − 1.5·ATR`, TP1 1R, TP2 2R) and travels with the decision. The no-trade rule is
`verdict.probabilities.enter < 0.6` — a number, not a mood.

---

## Troubleshooting

| symptom | cause |
|---|---|
| `no JEV_API_KEY — put it in .env` | `.env` missing or empty. See Setup. |
| `cannot load .../ihsg_screener.py` | `trading-tools` is not where it is expected. `export TRADING_TOOLS_DIR=/path/to/trading-tools` |
| `IDX served a bot wall, not data` | IDX escalated to a JS challenge. Install `curl_cffi` in a venv and add Chrome impersonation; today a bare stdlib request works, so this is a future problem, not a current one. |
| `IDX returned non-JSON` | IDX changed the response, or you are being rate limited. Retry later. |
| `no candidate cleared the required gate` | A genuinely dead session, or a `--limit` too small to reach candidates that pass. Raise `--limit`. |
| Yahoo stalls or 429s | Lower `--limit`; trading-tools caps polite fetching at 8 workers. |
| `--since 7x` | Validated and rejected on purpose — a mistyped window used to match nothing, which reads exactly like "no trades that week". Use `all`, `Nd`, or `YYYY-MM-DD`. |
| `UNKNOWN` status on the watchlist | No complete scan recorded yet. Run without a `--limit` that truncates the pool. |

---

## Files

| file | does |
|---|---|
| `tools.py` | the single owner of the `trading-tools` dependency |
| `idx.py` | IDX end-of-day summary: universe, suspended filter, float, foreign flow |
| `pivots.py` | significant highs and lows; self-check proves no repainting |
| `screen.py` | the 23 categories, the two required gates, the rank score |
| `jev.py` | the structured client and the five questions |
| `store.py` | SQLite: `idx_daily`, `scan`, `decision`; `report()` and `watchlist()` |
| `run.py` | the pipeline: `screen` · `run` · `resolve` · `watchlist` · `analyze` |

`.env` and `trading-jev.db` are gitignored. This app is local-only: no deploy target, no CI, no
container. The git repo exists purely so you can `git diff` a strategy change.

---

## Current state, honestly

The journal is **one session deep** (20260925) and contains **zero ENTER decisions** — Jev vetoed
all 11 names it was shown. So every performance figure in `analyze` is currently `n/a`, and that
is the correct output, not a bug. (Those 11 predate the liquidity gate; today's screen clears
only 10, and HYGN at 0.80bn ADV20 is the one it now drops.)

What this means for reading it:

- The foreign-flow gate is a **single session** of data. It is the weakest input in the system
  and the one the research says matters most. The window grows automatically as sessions
  accumulate.
- The pivot parameters (1×ATR prominence, ±20 baseline, 3 confirm) were swept over 886 stocks for
  *frequency*, not for edge. They are defensible defaults, not optimal ones.
- The entry plan uses the decision bar's close as the entry, not the next open. Slightly
  flattering; revisit when there is enough resolved history to see whether it matters.

**Not here, and deliberately:** no broker integration, no web UI, no backtester. A signal and a
journal is the whole product. Add a broker when the journal says the edge is real — not before.
