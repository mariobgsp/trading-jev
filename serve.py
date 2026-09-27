#!/usr/bin/env python3
# pyright: reportMissingImports=false
# This directory's modules import each other. Pyright in this setup does not read
# pyrightconfig.json's extraPaths and reports every one as missing, while the scripts resolve them
# fine at runtime. Verified by running every entry point in this file.
"""A local web app for trading-jev. Standard library only, like the rest of the project.

  python3 serve.py            # http://127.0.0.1:8787
  python3 serve.py --port 9000

Bound to 127.0.0.1 on purpose. This is a personal tool for one laptop; exposing it would put a
trading decision surface on the network, and the key it can spend is in the environment.

Three shapes of endpoint, deliberately:

  * Anything the CLI prints as a running log (a scan, a resolve) goes through a **subprocess job**.
    Streaming real stdout means the browser shows genuine progress, the run is isolated in its own
    process, and run.py keeps its CLI behaviour instead of being refactored to serve the web.
  * Anything the UI renders as *data* (a deepdive, a report, the watchlist) is called in-process
    and returns JSON. Synchronous, structured, and easy to reason about.
  * Static files come from a fixed allowlist. No user input ever reaches a filesystem path.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # noqa: E402 - a module run from another cwd still finds its siblings
import idx  # noqa: E402
import jev  # noqa: E402
import screen  # noqa: E402
import store  # noqa: E402
from tools import bars_for  # noqa: E402

WEB = os.path.join(HERE, "web")
# Fixed allowlist. A dict lookup on a constant key — not a path built from the request.
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}
TICKER = re.compile(r"^[A-Z]{1,6}$")
MAX_JOBS = 12

_jobs = {}
_jobs_lock = threading.Lock()


# ---------- jobs ----------
def _prune():
    """Drop the oldest finished jobs so a long session cannot grow the dict without bound."""
    finished = sorted((j for j in _jobs.values() if j["status"] != "running"),
                      key=lambda j: j["started"])
    for j in finished[:max(0, len(_jobs) - MAX_JOBS)]:
        _jobs.pop(j["id"], None)


def start_job(cmd):
    """Run a CLI subcommand in the background, streaming its output. Returns the job id."""
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "status": "running", "started": time.time(), "finished": None,
           "log": [], "result": None, "error": None, "cmd": " ".join(cmd[1:])}
    with _jobs_lock:
        _jobs[jid] = job
        _prune()

    def run():
        try:
            proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            stream = proc.stdout
            if stream is not None:
                for line in stream:
                    job["log"].append(line.rstrip("\n"))
                    if len(job["log"]) > 400:
                        del job["log"][:100]
            code = proc.wait()
            job["status"] = "done" if code == 0 else "failed"
            if code != 0:
                job["error"] = f"exited {code}"
        except Exception as e:  # noqa: BLE001 - a job failing must not kill the server
            job["status"] = "failed"
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            job["finished"] = time.time()

    threading.Thread(target=run, daemon=True).start()
    return jid


def get_job(jid):
    with _jobs_lock:
        job = _jobs.get(jid or "")
    if not job:
        return None
    return {"id": job["id"], "status": job["status"], "log": job["log"],
            "error": job["error"], "cmd": job["cmd"],
            "elapsed": round((job["finished"] or time.time()) - job["started"], 1)}


# ---------- API ----------
def api_health():
    try:
        jev._key()
        has_key = True
    except SystemExit:
        has_key = False
    db = store.conn()
    return {"ok": True, "has_key": has_key,
            "sessions": len(store.idx_sessions(90)),
            "latest_session": (store.idx_sessions() or [None])[0],
            "decisions": db.execute("SELECT COUNT(*) FROM decision").fetchone()[0]}


def api_deepdive(ticker):
    """One ticker, fully evaluated. Does NOT journal: this is a preview. `run` is the journal."""
    if not TICKER.match(ticker):
        raise ValueError(f"{ticker!r} is not an IDX code — letters only, 1-6")
    try:
        bars = bars_for(ticker)
    except Exception as e:  # noqa: BLE001 - a bad or delisted code is a normal user error
        raise ValueError(f"no bars for {ticker}: {type(e).__name__}") from e
    if not bars or len(bars) < 30:
        raise ValueError(f"{ticker} has too little history to evaluate")

    from pivots import last_significant_high, significant_highs
    flow = None
    row = next((r for r in store.idx_day((store.idx_sessions() or [""])[0])
                if r["code"] == ticker), None)
    if row:
        hist = store.flow_history(ticker, store.since_sessions(20))
        if hist:
            flow = {"net": sum(h["net"] for h in hist), "sessions": len(hist)}

    v = screen.evaluate(ticker, bars, index_bars=bars_for("^JKSE"), flow=flow)
    if v is None:
        raise ValueError(f"{ticker} could not be evaluated")

    state = screen.candidate_state(ticker, v)
    prose = jev.render(state)
    answer = None
    try:
        resp = jev.ask(prose)
        jev.check(resp["answers"])
        action, p = jev.decide(resp["answers"])
        answer = {"action": action, "p_enter": p, "answers": resp["answers"],
                  "usage": resp["usage"], "journalled": False}
    except jev.JevError as e:
        answer = {"action": None, "error": str(e), "journalled": False}

    piv = significant_highs(bars)
    plan = None
    if v["passed"]:
        from run import screener_plan
        try:
            plan = screener_plan(ticker)
        except Exception:  # noqa: BLE001
            plan = None

    return {
        "ticker": ticker,
        "name": (row or {}).get("name", ""),
        "session": (store.idx_sessions() or [None])[0],
        "bars": len(bars),
        "closes": [b["c"] for b in bars[-120:]],
        "pivot_level": (last_significant_high(bars, piv) or {}).get("high"),
        "pivots": len(piv),
        "required": {g: v["categories"].get(g) for g in screen.REQUIRED},
        "ranked": v["rank_signals"],
        "rank_score": v["rank_score"],
        "passed": v["passed"],
        "categories": v["categories"],
        "missing": v["missing"],
        "context": v["context"],
        "state": state,
        "prose": prose,
        "plan": plan,
        "jev": answer,
    }


def api_report(since):
    rep = store.report(store.since_ts(since))
    return rep


def api_watchlist(perf, cap=60):
    """The cohort. `perf` adds forward returns per name, which means one bar fetch each, so it
    is capped — bars are cached, but a few hundred names is still a slow response."""
    from run import forward_perf
    rows = store.watchlist()
    for r in rows[:cap]:
        r["perf"] = None
        if not perf:
            continue
        try:
            r["perf"] = forward_perf(bars_for(r["ticker"], "6mo"), r["first_seen"])
        except Exception:  # noqa: BLE001 - one bad ticker must not blank the cohort
            r["perf"] = None
    return {"rows": rows, "perf": bool(perf), "perf_capped": len(rows) > cap,
            "complete_sessions": store.complete_sessions()}


def api_shortlist():
    """The latest session's shortlist, re-evaluated from bars.

    The journal records *which* names cleared the gates; this reports what they looked like, by
    re-running evaluate() over cached bars. Doing it here rather than parsing the scan's stdout
    means the table cannot drift from the same code path the CLI uses."""
    sessions = store.idx_sessions()
    if not sessions:
        return {"session": None, "rows": [], "listed": None, "tradable": None,
                "under_1000": None, "eligible": None, "examined": None, "shortlisted": 0}
    session = sessions[0]
    day = store.idx_day(session)
    tradable = [r for r in day if not idx.suspended(r["remarks"])]
    under = [r for r in tradable if r["close"] and 0 < r["close"] < idx.MAX_PRICE]
    scan = store.conn().execute(
        "SELECT eligible, examined FROM scan WHERE session=?", (session,)).fetchone()
    names = {r["code"]: r["name"] for r in day}

    rows, skipped = [], []
    for w in store.watchlist():
        if w["last_seen"] != session:
            continue
        try:
            bars = bars_for(w["ticker"], "6mo")
            v = screen.evaluate(w["ticker"], bars)
        except Exception as e:  # noqa: BLE001 - reported below, never swallowed
            skipped.append({"ticker": w["ticker"], "error": f"{type(e).__name__}"})
            continue
        if v is None:
            skipped.append({"ticker": w["ticker"], "error": "could not evaluate"})
            continue
        rows.append({"ticker": w["ticker"], "name": names.get(w["ticker"], ""),
                     "close": v["context"]["close"], "rsi": v["context"]["rsi"],
                     "adv20": v["context"]["adv20"], "rank_score": v["rank_score"],
                     "rank_signals": v["rank_signals"], "avg_p": w["avg_p"],
                     "entered": w["entered"], "passed": v["passed"]})
    rows.sort(key=lambda r: (-r["rank_score"], -(r["adv20"] or 0), r["ticker"]))
    return {"session": session, "rows": rows, "listed": len(day), "tradable": len(tradable),
            "under_1000": len(under),
            "eligible": scan["eligible"] if scan else None,
            "examined": scan["examined"] if scan else None,
            "shortlisted": len(rows), "skipped": skipped}


ROUTES = {
    "/api/health": lambda q: api_health(),
    "/api/deepdive": lambda q: api_deepdive((q.get("ticker") or [""])[0].strip().upper()),
    "/api/report": lambda q: api_report((q.get("since") or ["all"])[0]),
    "/api/watchlist": lambda q: api_watchlist((q.get("perf") or ["1"])[0] == "1"),
    "/api/shortlist": lambda q: api_shortlist(),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "trading-jev"

    def log_message(self, format, *args):  # noqa: A002 - the name is fixed by the base class
        pass                                    # keep the console for the app, not for polling

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload, default=str))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path in STATIC:
            name, ctype = STATIC[path]
            full = os.path.join(WEB, name)
            try:
                with open(full, "rb") as fh:
                    payload = fh.read()
            except OSError as e:
                return self._json(500, {"error": f"{name} unreadable: {e}"})
            return self._send(200, payload, ctype)

        if path == "/api/job":
            job = get_job((query.get("id") or [""])[0])
            return self._json(200, job or {"error": "no such job"})

        if path in ROUTES:
            try:
                return self._json(200, ROUTES[path](query))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

        self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)

        if parsed.path == "/api/scan":
            limit = _clamp((query.get("limit") or ["200"])[0], 1, 1000, 200)
            top = _clamp((query.get("top") or ["15"])[0], 1, 100, 15)
            return self._json(202, {"job": start_job(
                [sys.executable, "run.py", "run", "--limit", str(limit), "--top", str(top)])})
        if parsed.path == "/api/resolve":
            return self._json(202, {"job": start_job([sys.executable, "run.py", "resolve"])})
        self._json(404, {"error": "not found"})


def _clamp(raw, lo, hi, default):
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return default


def _bind(port, attempts=20):
    """Fall forward to the next free port. Port 8787 is already held by caveman-proxy on this
    machine, and a bare traceback for 'address in use' is a poor first impression."""
    for p in range(port, port + attempts):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), Handler), p
        except OSError:
            continue
    raise SystemExit(f"no free port in {port}-{port + attempts - 1}")


def main():
    ap = argparse.ArgumentParser(description="Local web app for trading-jev")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    srv, port = _bind(a.port)
    print(f"trading-jev  ->  http://127.0.0.1:{port}   (ctrl-c to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
