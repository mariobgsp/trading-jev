#!/usr/bin/env python3
"""Storage — one SQLite file owning every table this project has.

Two tables and no more:
  idx_daily  the IDX end-of-day snapshot, one row per (session, ticker). Doubles as the 24h cache
             for the GetStockSummary call and as the history that accumulates the foreign-flow
             figure, so the actor gate gets stronger the longer this runs.
  decision   the journal. One row per candidate Jev scored, ENTER or SKIP. SKIP rows are the
             point: they are what makes "how often does Jev say no" and "was the no right?"
             answerable at all.

Outcomes are resolved later from the same bars the screen used, so a decision is only ever
scored against data that existed when it was made.

Usage:
  python3 store.py accuracy      # hit rate per p_enter bucket — the metric that keeps or kills Jev
"""
import calendar
import datetime
import decimal
import json
import os
import sqlite3
import threading

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading-jev.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS idx_daily (
  day TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT,
  close REAL, previous REAL, volume REAL, value REAL,
  foreign_buy REAL, foreign_sell REAL,
  listed_shares REAL, tradeble_shares REAL,
  remarks TEXT,
  PRIMARY KEY (day, code)
);

CREATE TABLE IF NOT EXISTS scan (
  session TEXT PRIMARY KEY,
  run_ts TEXT NOT NULL,
  eligible INTEGER NOT NULL,
  examined INTEGER NOT NULL,
  complete INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS decision (
  id INTEGER PRIMARY KEY,
  run_ts TEXT NOT NULL,
  session TEXT NOT NULL,
  ticker TEXT NOT NULL,
  rendered TEXT NOT NULL,
  p_enter REAL,
  verdict TEXT,
  momentum REAL,
  conviction REAL,
  risk TEXT,
  action TEXT NOT NULL,
  entry REAL, stop REAL, tp1 REAL, tp2 REAL,
  evidence TEXT,
  outcome TEXT,
  outcome_pct REAL,
  bars_held INTEGER
);

CREATE INDEX IF NOT EXISTS decision_session ON decision(session);
CREATE INDEX IF NOT EXISTS decision_ticker ON decision(ticker);
"""

_local = threading.local()


def conn():
    """One connection per thread.

    A single module-level connection is a trap here: sqlite3 refuses to use a connection from a
    thread other than the one that opened it, and serve.py is a ThreadingHTTPServer, so every
    request arrives on a fresh thread. The first request worked and every one after it failed.
    Thread-local avoids the whole class of problem, and WAL plus a busy timeout keeps concurrent
    readers from tripping over each other."""
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.executescript(SCHEMA)
        c.commit()
        _local.conn = c
    return c


# ---------- IDX snapshot ----------
def put_idx_day(day, rows):
    """rows: list of dicts with the field names from idx.py.normalise()."""
    db = conn()
    db.executemany(
        "INSERT OR REPLACE INTO idx_daily (day, code, name, close, previous, volume, value,"
        " foreign_buy, foreign_sell, listed_shares, tradeble_shares, remarks)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(day, r["code"], r["name"], r["close"], r["previous"], r["volume"], r["value"],
          r["foreign_buy"], r["foreign_sell"], r["listed_shares"], r["tradeble_shares"],
          r["remarks"]) for r in rows],
    )
    db.commit()
    return len(rows)


def idx_day(day):
    return [dict(r) for r in conn().execute(
        "SELECT * FROM idx_daily WHERE day=?", (day,))]


def idx_sessions(limit=30):
    return [r[0] for r in conn().execute(
        "SELECT DISTINCT day FROM idx_daily ORDER BY day DESC LIMIT ?", (limit,))]


def since_sessions(n=20):
    """Oldest day among the last n sessions. The foreign-flow window can only be as long as the
    history we have, so this grows toward n as the project accumulates days."""
    days = idx_sessions(n)
    return days[-1] if days else None


def flow_history(code, since):
    """Foreign net buy for one ticker, oldest first, across the sessions from `since` onward.
    A range query rather than an IN list: the window grows without any dynamic SQL."""
    rows = conn().execute(
        "SELECT day, foreign_buy, foreign_sell, tradeble_shares FROM idx_daily"
        " WHERE code=? AND day>=? ORDER BY day", (code, since)).fetchall()
    out = []
    for r in rows:
        net = (r["foreign_buy"] or 0) - (r["foreign_sell"] or 0)
        shares = r["tradeble_shares"] or 0
        out.append({"day": r["day"], "net": net,
                    "pct_float": net / shares * 100 if shares else None})
    return out


# ---------- scan coverage ----------
def record_scan(session, eligible, examined):
    """What a run looked at. `complete` marks a scan that examined every eligible candidate.

    This is what makes 'excluded from the watchlist' a sound claim. Without it, a ticker missing
    from a later run is ambiguous: it may have been dropped on merit, or it may simply have sat
    outside a --limit sample. Only a complete scan can tell those apart."""
    conn().execute(
        "INSERT OR REPLACE INTO scan (session, run_ts, eligible, examined, complete)"
        " VALUES (?, datetime('now'), ?, ?, ?)",
        (session, eligible, examined, 1 if examined >= eligible else 0))
    conn().commit()


def complete_sessions():
    return [r[0] for r in conn().execute(
        "SELECT session FROM scan WHERE complete=1 ORDER BY session")]


# ---------- watchlist lifecycle ----------
def watchlist():
    """Every candidate the gate has ever surfaced, with the span it was on the list.

    The decision table IS the watchlist: only gate-passed candidates are ever journalled, so this
    is a grouping, not a second source of truth that could drift from it."""
    rows = [dict(r) for r in conn().execute(
        "SELECT ticker, MIN(session) first_seen, MAX(session) last_seen, COUNT(*) seen,"
        " AVG(p_enter) avg_p, SUM(action='ENTER') entered FROM decision"
        " GROUP BY ticker ORDER BY first_seen, ticker")]
    done = complete_sessions()
    latest = done[-1] if done else None
    for r in rows:
        r["avg_p"] = round(r["avg_p"] or 0, 3)
        if latest is None:
            # No complete scan has ever been recorded, so absence proves nothing. Saying
            # EXCLUDED here would be a guess dressed as a fact.
            r["status"] = "UNKNOWN"
            r["exit_session"] = None
        elif r["last_seen"] == latest:
            r["status"] = "IN"
            r["exit_session"] = None
        else:
            r["status"] = "EXCLUDED"
            nxt = [s for s in done if s > r["last_seen"]]
            r["exit_session"] = nxt[0] if nxt else None
    return rows


# ---------- journal ----------
def record(session, ticker, rendered, answers, action, plan, evidence):
    """One scored candidate. `answers` is Jev's full response; `plan` is None on a SKIP."""
    db = conn()
    db.execute(
        "INSERT INTO decision (run_ts, session, ticker, rendered, p_enter, verdict, momentum,"
        " conviction, risk, action, entry, stop, tp1, tp2, evidence)"
        " VALUES (datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (session, ticker, rendered, answers["verdict"]["probabilities"]["enter"],
         answers["verdict"]["choice"], answers["momentum_confirmed"]["noul"],
         answers["conviction"]["score"], answers["risk"]["choice"], action,
         plan and plan["entry"], plan and plan["stop"], plan and plan["tp1"], plan and plan["tp2"],
         json.dumps(evidence, separators=(",", ":"), default=str)),
    )
    db.commit()


def unresolved():
    """Every ENTER with no outcome yet, of any age.

    Deliberately not age-filtered. A session-count gate looks tidier but breaks the obvious
    workflow — after one week only ~5 sessions exist, so nothing would ever resolve. resolve()
    already returns 'open' when too few bars have passed, which is the honest answer, so the
    caller can resolve everything and let the outcome say how old the trade really is."""
    return [dict(r) for r in conn().execute(
        "SELECT * FROM decision WHERE action='ENTER' AND outcome IS NULL ORDER BY session")]


def resolve(ticker, session, entry, stop, tp1, bars):
    """-> (outcome, pct, bars_held). Walks forward from the bar AFTER the session: the plan is
    built on the session's close, so that bar can neither stop us out nor take profit. The first
    of stop or tp1 after it decides, otherwise the trade is still open at the last bar we hold.
    Uses only bars that existed after the session, so a decision is never scored against its own
    past."""
    start = None
    for i, b in enumerate(bars):
        if b["t"] >= session_ts(session):
            start = i + 1          # never count the entry bar against the trade
            break
    if start is None or start >= len(bars):
        return None
    for j in range(start, len(bars)):
        b = bars[j]
        if b["l"] <= stop:
            return ("stop", (stop - entry) / entry * 100, j - start + 1)
        if b["h"] >= tp1:
            return ("tp1", (tp1 - entry) / entry * 100, j - start + 1)
    last = bars[-1]
    return ("open", (last["c"] - entry) / entry * 100, len(bars) - start)


def set_outcome(decision_id, outcome, pct, bars):
    conn().execute(
        "UPDATE decision SET outcome=?, outcome_pct=?, bars_held=? WHERE id=?",
        (outcome, pct, bars, decision_id))
    conn().commit()


def since_ts(value):
    """'all'/None -> None (no filter). '30d'/'7d' -> that many days back, in UTC to match run_ts,
    which SQLite writes as datetime('now'). YYYY-MM-DD -> that midnight.

    Validated strictly. Anything else raises: a mistyped window used to compare as a string and
    silently match nothing, which reads exactly like 'no trades that week'."""
    if not value or value == "all":
        return None
    text = str(value).strip()
    bad = ValueError(f"--since wants 'all', '7d'/'30d'/'90d', or YYYY-MM-DD, got {value!r}")
    if text.endswith("d"):
        try:
            days = int(text[:-1])
        except ValueError as e:
            raise bad from e
        if days <= 0:
            raise bad
        back = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        return back.strftime("%Y-%m-%d %H:%M:%S")
    try:
        datetime.datetime.strptime(text, "%Y-%m-%d")
    except ValueError as e:
        raise bad from e
    return text + " 00:00:00"


def risk_pct(d):
    """Risk of one trade as a percentage, from the plan that was stored with it. This is the
    denominator for R: without it a % return is not comparable between a 3% stop and a 9% one."""
    if not d.get("entry") or not d.get("stop"):
        return None
    return abs(d["entry"] - d["stop"]) / d["entry"] * 100


def report(since=None, step=0.1):
    """Everything the journal knows about a window, in one pass.

    `open` and `unresolvable` are reported as their own counts, never folded into the win rate.
    An earlier version dropped 'open' silently, which flatters the hit rate by quietly removing
    the trades that have not proved themselves yet."""
    if since:
        rows = [dict(r) for r in conn().execute(
            "SELECT * FROM decision WHERE run_ts >= ? ORDER BY run_ts, id", (since,))]
    else:
        rows = [dict(r) for r in conn().execute("SELECT * FROM decision ORDER BY run_ts, id")]

    entered = [d for d in rows if d["action"] == "ENTER"]
    decided = [d for d in entered if d["outcome"] in ("tp1", "stop")]
    still_open = [d for d in entered if d["outcome"] == "open"]
    unresolvable = [d for d in entered if d["outcome"] is None]

    buckets = {}
    for d in decided:
        # Decimal, not float. p_enter 0.7/0.1 is 6.999... in binary floating point and 0.75/0.1
        # is exactly 7.5, so int() files 0.7 under 0.6 and round() (banker's rounding) files 0.75
        # under 0.8. No single rounding mode fixes both — the division itself is the problem.
        # Decimal on the string form is exact: 0.7->7, 0.75->7, 0.45->4, all lower-bound buckets.
        idx = int(decimal.Decimal(str(d["p_enter"] or 0))
                  // decimal.Decimal(str(step)))
        b = buckets.setdefault(idx, {"n": 0, "wins": 0})
        b["n"] += 1
        b["wins"] += 1 if d["outcome"] == "tp1" else 0

    curve, cum = [], 0.0
    for d in entered:
        r = risk_pct(d)
        if d["outcome"] and r:
            cum += d["outcome_pct"] / r
        curve.append({"session": d["session"], "ticker": d["ticker"],
                      "outcome": d["outcome"], "pct": d["outcome_pct"],
                      "r": round(d["outcome_pct"] / r, 2) if d["outcome"] and r else None,
                      "cumulative_r": round(cum, 2)})

    return {
        "since": since or "all time",
        "scored": len(rows),
        "entered": len(entered),
        "vetoed": len(rows) - len(entered),
        "veto_rate": (len(rows) - len(entered)) / len(rows) if rows else None,
        "resolved": len(decided),
        "wins": sum(1 for d in decided if d["outcome"] == "tp1"),
        "hit_rate": (sum(1 for d in decided if d["outcome"] == "tp1") / len(decided))
                    if decided else None,
        "open": len(still_open),
        "unresolvable": len(unresolvable),
        "total_r": round(cum, 2),
        "avg_r": round(cum / len(curve), 2) if curve else None,
        "buckets": sorted((round(b * step, 1), v["n"], v["wins"]) for b, v in buckets.items()),
        "equity": curve,
        "decisions": [{"session": d["session"], "ticker": d["ticker"], "action": d["action"],
                       "p_enter": d["p_enter"], "conviction": d["conviction"],
                       "risk": d["risk"], "outcome": d["outcome"],
                       "outcome_pct": d["outcome_pct"]} for d in rows],
    }


def print_report(rep, per_decision=True):
    print(f"window: {rep['since']}")
    print(f"scored {rep['scored']}  entered {rep['entered']}  vetoed {rep['vetoed']}"
          + (f" ({rep['veto_rate']:.1%})" if rep["veto_rate"] is not None else ""))
    hr = "n/a" if rep["hit_rate"] is None else f"{rep['hit_rate']:.1%} ({rep['wins']}/{rep['resolved']})"
    print(f"resolved {rep['resolved']}  hit rate {hr}  open {rep['open']}  "
          f"unresolvable {rep['unresolvable']}")
    print(f"R: total {rep['total_r']:+.2f}  avg {rep['avg_r'] if rep['avg_r'] is not None else 'n/a'}")
    if not rep["entered"]:
        print("\nno ENTER in this window, so there is no P&L. Jev vetoed everything it saw.")
    if rep["buckets"]:
        print("\nJev calibration by p_enter bucket:")
        print(f"  {'bucket':>8} {'n':>5} {'wins':>5} {'rate':>7}")
        for b, n, w in rep["buckets"]:
            print(f"  {b:8.1f} {n:5d} {w:5d} {w / n:7.1%}")
    if rep["equity"]:
        print("\nequity (cumulative R):")
        for row in rep["equity"]:
            pct = "  n/a" if row["pct"] is None else f"{row['pct']:+7.2f}%"
            print(f"  {row['session']}  {row['ticker']:8} {str(row['outcome'] or 'unresolved'):10}"
                  f"{pct}  R={str(row['r']):>6}  cum={row['cumulative_r']:+.2f}")
    if per_decision and rep["decisions"]:
        print("\ndecisions:")
        print(f"  {'session':<9} {'ticker':8} {'act':5} {'p':>5} {'conv':>5} {'risk':7} outcome")
        for d in rep["decisions"]:
            print(f"  {d['session']:<9} {d['ticker']:8} {d['action']:5} {d['p_enter']:5.2f} "
                  f"{d['conviction']:5.1f} {str(d['risk']):7} {d['outcome'] or '-'}")


def session_ts(session):
    """IDX 'YYYYMMDD' -> epoch seconds at 00:00 UTC, matching the cached bar timestamps."""
    try:
        y, m, d = int(session[0:4]), int(session[4:6]), int(session[6:8])
    except (ValueError, IndexError, TypeError) as e:
        raise ValueError(f"session must be YYYYMMDD, got {session!r}") from e
    return calendar.timegm((y, m, d, 0, 0, 0, 0, 0, 0))


if __name__ == "__main__":
    print_report(report())
