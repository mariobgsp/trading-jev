#!/usr/bin/env python3
"""IDX end-of-day summary — universe, suspended filter, and the actor filter's foreign flow.

One GetStockSummary call returns the whole market: ~963 rows with ticker, close, volume,
ListedShares/TradebleShares and ForeignBuy/ForeignSell. That single call is the universe, the
suspended filter, the price filter and the actor filter. Do not fetch them separately.

Plain stdlib, no curl_cffi, no cookie jar and no warm-up request: a bare request to the endpoint
is accepted (verified — 963 rows, 200). trading-suite's app/idx.py uses Chrome impersonation
because it wants to survive a managed JS challenge; that venv is gone and the challenge is not
being served. If IDX ever escalates, the fix is `pip install curl_cffi` in a venv, not a rewrite.

The flow figure is a per-session snapshot, so a 20-day window would cost 20 calls. Instead each
day's rows go into idx_daily and the window grows as history accumulates — store.flow_history.

Usage:
  python3 idx.py refresh        # fetch + store today's summary, print the filter funnel
  python3 idx.py show BBRI      # one ticker's stored row + its flow history
"""
import datetime
import http.client
import json
import sys
import urllib.parse

from store import flow_history, idx_day, idx_sessions, put_idx_day, since_sessions

HOST = "www.idx.co.id"
API_PATH = "/primary/TradingSummary/GetStockSummary"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en-US;q=0.9,id;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}
# Same markers trading-tools/common/net.py watches for. A bot wall is a different answer, not a
# transient error, and must never be parsed as data.
BOT_MARKS = ("Just a moment", "cf-browser-verification", "Attention Required",
             "Enable JavaScript", "Checking your browser")
MAX_PRICE = 1000.0


class IdxError(RuntimeError):
    """IDX refused, or returned something we will not guess at."""


def _get(path, params=None):
    if params:
        path = f"{path}?{urllib.parse.urlencode(params)}"
    conn = http.client.HTTPSConnection(HOST, timeout=40)
    try:
        conn.request("GET", path, headers=HEADERS)
        resp = conn.getresponse()
        status, raw = resp.status, resp.read()
    except (OSError, http.client.HTTPException) as e:
        raise IdxError(f"IDX unreachable: {e}") from e
    finally:
        conn.close()
    if status >= 400:
        raise IdxError(f"IDX {status} for {path}")
    text = raw.decode(errors="replace")
    if any(m in text for m in BOT_MARKS):
        raise IdxError("IDX served a bot wall, not data — needs curl_cffi impersonation")
    return text


def last_weekday():
    d = datetime.date.today()
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None      # drop NaN and inf


def normalise(row):
    return {
        "code": str(row.get("StockCode") or "").strip(),
        "name": str(row.get("StockName") or "").strip(),
        "close": _num(row.get("Close")),
        "previous": _num(row.get("Previous")),
        "volume": _num(row.get("Volume")),
        "value": _num(row.get("Value")),
        "foreign_buy": _num(row.get("ForeignBuy")),
        "foreign_sell": _num(row.get("ForeignSell")),
        "listed_shares": _num(row.get("ListedShares")),
        "tradeble_shares": _num(row.get("TradebleShares")),
        "remarks": str(row.get("Remarks") or "").strip(),
    }


def suspended(remarks):
    """IDX marks a suspended name with a trailing 'X'. UMA names open '--U' and cannot be traded
    either, so they go too. Everything else keeps its Remark for the evidence row."""
    return remarks.endswith("X") or remarks.startswith("--U")


def fetch_summary(day=None):
    """-> (YYYYMMDD, rows). Walks back up to 5 days: IDX publishes EOD after the close."""
    d = day or last_weekday()
    for _ in range(5):
        key = d.strftime("%Y%m%d")
        text = _get(API_PATH, {"length": 9999, "start": 0, "date": key})
        try:
            data = json.loads(text).get("data") or []
        except json.JSONDecodeError as e:
            raise IdxError(f"IDX returned non-JSON for {key}") from e
        if data:
            rows = [normalise(r) for r in data]
            return key, [r for r in rows if r["code"]]
        d -= datetime.timedelta(days=1)
    raise IdxError("no IDX summary in the last 5 days")


def refresh(day=None):
    """Fetch if we do not already hold the session, then store. Prints the funnel so the cost of
    each filter stays visible instead of being assumed."""
    key, rows = fetch_summary(day)
    if not idx_day(key):
        rows = [r for r in rows if r["close"] is not None]
        put_idx_day(key, rows)
    else:
        rows = idx_day(key)
    live = [r for r in rows if not suspended(r["remarks"])]
    cheap = [r for r in live if r["close"] is not None and 0 < r["close"] < MAX_PRICE]
    print(f"session {key}: {len(rows)} with a price -> {len(live)} tradable "
          f"-> {len(cheap)} under IDR {MAX_PRICE:.0f}")
    return key, cheap


def show(code):
    days = idx_sessions()
    if not days:
        return print("nothing stored yet — run: python3 idx.py refresh")
    rows = idx_day(days[0])
    hit = [r for r in rows if r["code"] == code]
    print(json.dumps(hit[0] if hit else f"{code} is not in {days[0]}", indent=1, default=str))
    hist = flow_history(code, since_sessions(20))
    if hist:
        print(f"\nforeign flow over {len(hist)} session(s) from {hist[0]['day']}:")
        for h in hist:
            pct = f"{h['pct_float']:+.3f}% of float" if h["pct_float"] is not None else "n/a"
            print(f"  {h['day']}  net {h['net']:>15,.0f}  {pct}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    if cmd == "refresh":
        refresh()
    elif cmd == "show":
        show(sys.argv[2] if len(sys.argv) > 2 else "BBRI")
    else:
        raise SystemExit(f"unknown command {cmd!r} — use refresh | show CODE")
