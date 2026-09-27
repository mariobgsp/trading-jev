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
import json
import os
import sqlite3

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

_conn = None


def conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


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


def unresolved(min_age_sessions=10):
    return [dict(r) for r in conn().execute(
        "SELECT * FROM decision WHERE action='ENTER' AND outcome IS NULL"
        " AND session NOT IN (SELECT day FROM idx_daily ORDER BY day DESC LIMIT ?)",
        (min_age_sessions,))]


def resolve(ticker, session, entry, stop, tp1, bars):
    """-> (outcome, pct, bars_held). Walks forward from the session bar; the first of stop or
    tp1 decides, otherwise it is still open at the last bar we hold. Uses only bars that existed
    after the session, so a decision is never scored against its own past."""
    start = None
    for i, b in enumerate(bars):
        if b["t"] >= session_ts(session):
            start = i
            break
    if start is None:
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


def accuracy(step=0.1):
    """Hit rate per p_enter bucket, plus the veto rate. The metric that decides whether Jev
    stays: if the high-probability buckets are no better than the low ones, the model is noise
    and the correct response is to delete layer 3."""
    agg = {}
    for r in conn().execute(
            "SELECT CAST(p_enter / ? AS INT) AS bucket, outcome, COUNT(*) n FROM decision"
            " WHERE outcome IS NOT NULL AND outcome <> 'open' GROUP BY bucket, outcome",
            (step,)):
        b = agg.setdefault(r["bucket"], {"n": 0, "wins": 0})
        b["n"] += r["n"]
        b["wins"] += r["n"] if r["outcome"] == "tp1" else 0
    total = conn().execute("SELECT COUNT(*) FROM decision").fetchone()[0]
    if not total:
        print("nothing scored yet")
        return
    entered = conn().execute("SELECT COUNT(*) FROM decision WHERE action='ENTER'").fetchone()[0]
    print(f"{'bucket':>8} {'n':>5} {'wins':>5} {'rate':>7}")
    for b in sorted(agg):
        v = agg[b]
        print(f"{b * step:8.1f} {v['n']:5d} {v['wins']:5d} {v['wins'] / v['n']:7.1%}")
    print(f"\nscored {total}, entered {entered} ({entered / total:.1%}), "
          f"vetoed {total - entered} ({1 - entered / total:.1%})")


def session_ts(session):
    """IDX 'YYYYMMDD' -> epoch seconds at 00:00 UTC, matching the cached bar timestamps."""
    try:
        y, m, d = int(session[0:4]), int(session[4:6]), int(session[6:8])
    except (ValueError, IndexError, TypeError) as e:
        raise ValueError(f"session must be YYYYMMDD, got {session!r}") from e
    return calendar.timegm((y, m, d, 0, 0, 0, 0, 0, 0))


if __name__ == "__main__":
    accuracy()
