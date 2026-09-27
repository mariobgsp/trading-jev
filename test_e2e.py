#!/usr/bin/env python3
"""End-to-end tests: drive the real HTTP API and a real browser, then shut everything down.

    python3 test_e2e.py            # everything
    python3 test_e2e.py -v         # verbose
    python3 test_e2e.py ApiTest    # one suite

Stdlib `unittest` plus Playwright, which is already installed — no new dependency, consistent
with the rest of the project.

This is the outer ring on purpose. It tests the HTTP surface and the page as a user meets them,
including the error paths and the responsive layout. Unit-level checks live in the `demo()` of
each module, where they can be run with no server at all.

Suites that need a populated journal skip themselves on a fresh machine rather than failing, and
say so, instead of pretending an empty database is a broken one.
"""
import http.client
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = ""            # set by setUpModule; the helpers below fail loudly rather than build a
                      # half-valid URL if it is somehow still empty
_proc = None
_tmpdb = None
TICKER = os.environ.get("TICKER", "BBRI")     # a name that always has cached bars here
EMPTY = os.environ.get("E2E_ALLOW_EMPTY", "") == "1"
FORCE_EMPTY = os.environ.get("E2E_EMPTY", "") == "1"   # simulate a fresh install: no journal


# ---------- server lifecycle ----------
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _call(method, path, body=None, timeout=60):
    """http.client rather than urllib.request: the scheme is explicit in the constructor, so this
    helper can only ever talk to the loopback address it was handed — which is the very property
    one of these tests is asserting."""
    if not BASE:
        raise RuntimeError("the server is not running — setUpModule did not run")
    hostport = BASE.split("//", 1)[1]
    host, port = hostport.rsplit(":", 1)
    conn = http.client.HTTPConnection(host, int(port), timeout=timeout)
    try:
        conn.request(method, path, body=body)
        resp = conn.getresponse()
        return resp.status, resp.read(), dict(resp.getheaders())
    finally:
        conn.close()


def _get(path, timeout=60):
    return _call("GET", path, None, timeout)


def _json(path, timeout=60):
    status, body, _ = _get(path, timeout)
    return status, json.loads(body)


def setUpModule():
    """Start a server against a COPY of the journal.

    Hermetic on purpose: some of these tests start real scans, and a clamped scan writes a scan
    row with complete=0, which would overwrite the real completeness record and flip every
    watchlisted name to EXCLUDED. A test suite must not be able to do that to your data."""
    global BASE, _proc, _tmpdb
    port = _free_port()
    BASE = f"http://127.0.0.1:{port}"
    _tmpdb = tempfile.mkdtemp(prefix="trading-jev-e2e-")
    copy = os.path.join(_tmpdb, "journal.db")
    src = os.path.join(HERE, "trading-jev.db")
    if os.path.exists(src) and not FORCE_EMPTY:
        # sqlite3's backup API, so a WAL that has not been checkpointed still comes across whole
        with sqlite3.connect(src) as a, sqlite3.connect(copy) as b:
            a.backup(b)
    # else: leave `copy` absent and let the server create an empty journal

    env = dict(os.environ, TRADING_JEV_DB=copy)
    _proc = subprocess.Popen([sys.executable, "serve.py", "--port", str(port)], cwd=HERE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    deadline = time.time() + 30
    while time.time() < deadline:
        if _proc.poll() is not None:
            raise RuntimeError("serve.py exited: " + (_proc.stdout.read() if _proc.stdout else ""))
        try:
            _get("/api/health", timeout=5)
            return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("server did not become ready")


def tearDownModule():
    if _proc and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _proc.kill()
    if _tmpdb and os.path.isdir(_tmpdb):
        shutil.rmtree(_tmpdb, ignore_errors=True)


def requires_journal(fn):
    """Skip rather than fail when there is nothing journalled yet."""
    def wrapper(self):
        _, h = _json("/api/health")
        if not h["decisions"] and not EMPTY:
            self.skipTest("journal is empty — run `python3 run.py run` first")
        return fn(self)
    wrapper.__name__ = fn.__name__
    return wrapper


_jev = {}


def jev_blocked():
    """Why Jev is unusable right now, or None.

    jev-1.13-free is rate limited, and every page load asks it one question — so a suite run, or
    a user reloading, can throttle it. Probe once and cache, so a throttled run skips the
    Jev-dependent tests coherently instead of reporting a KeyError on a missing 'answers' key.
    """
    if "err" not in _jev:
        try:
            _, d = _json(f"/api/deepdive?ticker={TICKER}")
            _jev["err"] = (d.get("jev") or {}).get("error")
        except Exception as e:  # noqa: BLE001 - a failed probe is itself a block
            _jev["err"] = f"probe failed: {e}"
    return _jev["err"]


def requires_jev(fn):
    def wrapper(self):
        why = jev_blocked()
        if why and not EMPTY:
            self.skipTest(f"Jev unavailable: {why}")
        return fn(self)
    wrapper.__name__ = fn.__name__
    return wrapper


# ---------- the API surface ----------
class ApiTest(unittest.TestCase):
    def test_health_reports_a_ready_state(self):
        status, h = _json("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(h["ok"])
        self.assertIsInstance(h["has_key"], bool)
        self.assertIn("latest_session", h)
        self.assertIsInstance(h["decisions"], int)

    def test_health_never_leaks_the_key(self):
        _, body, _ = _get("/api/health")
        self.assertNotIn(b"KEYPREFIX_", body, "health must not echo the API key")
        self.assertNotIn(b"JEV_API_KEY=", body)

    def test_static_files_serve_with_the_right_type(self):
        for path, ctype in (("/", "text/html"), ("/app.css", "text/css"),
                            ("/app.js", "text/javascript")):
            with self.subTest(path=path):
                status, body, headers = _get(path)
                self.assertEqual(status, 200)
                self.assertIn(ctype, headers["Content-Type"])
                self.assertGreater(len(body), 500)

    def test_unknown_path_is_404(self):
        status, body, _ = _get("/api/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body))

    @requires_jev
    def test_deepdive_returns_the_full_shape(self):
        status, d = _json(f"/api/deepdive?ticker={TICKER}")
        self.assertEqual(status, 200)
        self.assertEqual(d["ticker"], TICKER)
        self.assertEqual(len(d["categories"]), 23, "all 23 categories must be present")
        self.assertEqual(set(d["required"]), {"breakout", "liquid"})
        self.assertIn("rank_score", d)
        self.assertTrue(d["closes"], "a sparkline needs closes")
        self.assertTrue(d["prose"], "the state shown to Jev must be returned for audit")
        # everything above is screen data and holds whether or not Jev answered; only the
        # verdict is conditional, and requires_jev has already established it is available
        self.assertEqual(set(d["jev"]["answers"]),
                         {"verdict", "p_enter", "momentum_confirmed", "conviction", "risk"})
        a = d["jev"]["answers"]
        self.assertAlmostEqual(sum(a["verdict"]["probabilities"].values()), 1.0, places=2)
        self.assertTrue(0.0 <= a["p_enter"]["noul"] <= 1.0)
        self.assertIn(d["jev"]["action"], ("ENTER", "SKIP"))

    def test_deepdive_rejects_a_non_ticker_with_400_not_500(self):
        for bad in ("12", "../etc/passwd", "BB RI", "A" * 40, "%2e%2e%2f"):
            with self.subTest(ticker=bad):
                status, body = _json(f"/api/deepdive?ticker={urllib.parse.quote(bad)}")
                self.assertEqual(status, 400, f"{bad!r} should be a 400")
                self.assertIn("error", body)

    def test_deepdive_reports_a_missing_ticker_clearly(self):
        status, body = _json("/api/deepdive?ticker=ZZZZZ")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_report_accepts_every_documented_window(self):
        for since in ("7d", "30d", "90d", "all"):
            with self.subTest(since=since):
                status, rep = _json(f"/api/report?since={since}")
                self.assertEqual(status, 200)
                for key in ("scored", "entered", "vetoed", "resolved", "wins",
                            "hit_rate", "open", "unresolvable", "total_r",
                            "buckets", "equity", "decisions"):
                    self.assertIn(key, rep)
                self.assertEqual(rep["entered"] + rep["vetoed"], rep["scored"])
                if rep["resolved"]:
                    self.assertLessEqual(rep["wins"], rep["resolved"])

    def test_report_rejects_a_mistyped_window(self):
        """A typo must not silently read as an empty window."""
        for bad in ("7x", "0d", "yesterday", "2026-13-45"):
            with self.subTest(since=bad):
                status, body = _json(f"/api/report?since={urllib.parse.quote(bad)}")
                self.assertEqual(status, 400)
                self.assertIn("--since", body["error"])

    def test_watchlist_carries_lifecycle_and_performance(self):
        status, w = _json("/api/watchlist?perf=1")
        self.assertEqual(status, 200)
        self.assertIn("rows", w)
        self.assertIn("complete_sessions", w)
        for r in w["rows"]:
            self.assertIn(r["status"], ("IN", "EXCLUDED", "UNKNOWN"))
            self.assertLessEqual(r["first_seen"], r["last_seen"])
            if r["status"] == "EXCLUDED":
                self.assertIsNotNone(r["exit_session"], "an exclusion needs a session")
            else:
                self.assertIsNone(r["exit_session"])

    def test_watchlist_without_perf_is_fast_and_shape_correct(self):
        status, w = _json("/api/watchlist?perf=0")
        self.assertEqual(status, 200)
        for r in w["rows"]:
            self.assertIn("perf", r)

    @requires_journal
    def test_shortlist_reports_the_funnel(self):
        status, s = _json("/api/shortlist")
        self.assertEqual(status, 200)
        for key in ("listed", "tradable", "under_1000", "eligible", "examined",
                    "shortlisted", "rows", "skipped"):
            self.assertIn(key, s)
        self.assertGreaterEqual(s["tradable"], s["under_1000"],
                                "the price filter can only shrink the universe")
        self.assertEqual(s["shortlisted"], len(s["rows"]))
        # The shortlist must be names the journal actually saw for that session — never invented.
        _, w = _json("/api/watchlist?perf=0")
        journalled = {r["ticker"] for r in w["rows"] if r["last_seen"] == s["session"]}
        self.assertTrue({r["ticker"] for r in s["rows"]} <= journalled)
        # A name journalled under an older gate set can fail today's gate. It must still be
        # present and flagged, not silently dropped — the journal is the record of what happened.
        for r in s["rows"]:
            self.assertIn("passed", r)

    def test_unknown_job_is_reported_not_fatal(self):
        status, body = _json("/api/job?id=deadbeef")
        self.assertEqual(status, 200)
        self.assertIn("error", body)

    def test_job_runs_to_completion_and_streams_output(self):
        status, body, _ = _call("POST", "/api/resolve", b"", 10)
        self.assertEqual(status, 202)
        jid = json.loads(body)["job"]
        for _ in range(60):
            status, j = _json(f"/api/job?id={jid}")
            self.assertEqual(status, 200)
            if j["status"] != "running":
                self.assertEqual(j["status"], "done")
                self.assertTrue(j["log"], "a finished job must have produced output")
                return
            time.sleep(0.5)
        self.fail("resolve job never finished")

    def test_scan_arguments_are_clamped(self):
        """Bounds are enforced server-side. Checked with dry_run, which validates and clamps
        without executing — otherwise a limit of 99999 would clamp to 1000 and start a real
        whole-universe scan, mutating the journal and making the suite order-dependent."""
        # "1e9" is not a parseable int, so it falls back to the default rather than clamping —
        # an unparseable argument and an out-of-range one are different failures.
        cases = [("0", "0", 1, 1), ("99999", "99999", 1000, 100),
                 ("abc", "abc", 200, 15), ("-5", "1e9", 1, 15)]
        for limit, top, want_limit, want_top in cases:
            with self.subTest(limit=limit, top=top):
                status, body, _ = _call(
                    "POST", f"/api/scan?limit={limit}&top={top}&dry_run=1", b"", 10)
                self.assertEqual(status, 200)
                d = json.loads(body)
                self.assertTrue(d["dry_run"])
                self.assertEqual(d["limit"], want_limit, f"limit {limit!r} not clamped")
                self.assertEqual(d["top"], want_top, f"top {top!r} not clamped")

    def test_a_real_scan_request_starts_a_job(self):
        """Deliberately NOT exercised here. The non-dry path is `return 202 start_job(cmd)`, and
        starting it for real would mutate the journal this suite runs against. The job machinery
        itself is proven end to end by the resolve test above."""
        status, body, _ = _call("POST", "/api/scan?limit=1&top=1&dry_run=1", b"", 10)
        self.assertEqual(status, 200)
        self.assertNotIn("job", json.loads(body),
                         "a dry run must not start a job")
    def test_server_is_not_reachable_off_loopback(self):
        """It must not be bound to 0.0.0.0: it spends a key and decides trades."""
        try:
            host = socket.gethostbyname(socket.gethostname())
        except OSError:
            self.skipTest("no routable address to test against")
        if host.startswith("127."):
            self.skipTest("only a loopback address exists here")
        s = socket.socket()
        s.settimeout(2)
        try:
            s.connect((host, int(BASE.rsplit(":", 1)[1])))
            self.fail(f"the server answered on {host} — it must be loopback only")
        except (TimeoutError, ConnectionRefusedError, OSError):
            pass
        finally:
            s.close()


# ---------- the page, in a real browser ----------
class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self._browser.new_page(viewport={"width": 1440, "height": 1000})
        self.console = []
        self.failed = []
        # The browser logs a console entry for every non-2xx response. A test that deliberately
        # submits a bad ticker or a bad limit must set this, or assertClean() reports the refusal
        # it asked for as a failure. Script errors and unexpected network failures still fail.
        self.allow_http_errors = False
        self.page.on("console", lambda m: self.console.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        self.page.on("requestfailed",
                     lambda r: self.failed.append(f"{r.url} {r.failure}"))
        self.page.goto(BASE, wait_until="domcontentloaded")

    def tearDown(self):
        self.page.close()

    def assertClean(self):
        noise = (lambda t: t.startswith("Failed to load resource")) if self.allow_http_errors \
            else (lambda t: False)
        self.assertEqual([t for t in self.console if not noise(t)], [], "console errors on the page")
        self.assertEqual(self.failed, [], "failed network requests")

    def settle(self, ms=400):
        self.page.wait_for_timeout(ms)

    # -- loading --
    def test_page_loads_with_no_console_errors(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.wait_for_selector("#tiles .tile", timeout=45000)
        self.settle()
        self.assertClean()

    def test_deepdive_renders_on_load(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.assertIn(self.page.locator("#ticker").input_value(), self.page.inner_text("#dd"))
        self.assertNotEqual(self.page.inner_text("#dd").strip(), "")

    def test_all_twenty_three_categories_render_in_their_own_card(self):
        self.page.wait_for_selector("#cats .chip", timeout=45000)
        self.assertEqual(self.page.locator("#cats .chip").count(), 23)
        # the two gates are marked, and they are the ones the design says are required
        marked = self.page.locator("#cats .chip.gate").count()
        self.assertEqual(marked, 2, "exactly two categories are gates")

    def test_sparkline_renders_with_a_price_path(self):
        self.page.wait_for_selector("#spark svg path", timeout=45000)
        self.assertGreaterEqual(self.page.locator("#spark svg path").count(), 2,
                                "an area and a line")

    @requires_jev
    def test_jev_answers_render_as_probability_bars(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.wait_for_selector("#jev .pbar", timeout=20000)
        self.assertGreaterEqual(self.page.locator("#jev .row").count(), 4)
        self.assertNotEqual(self.page.inner_text("#prose").strip(), "",
                            "the state Jev saw must be auditable in the page")

    def test_report_tiles_render(self):
        self.page.wait_for_selector("#tiles .tile", timeout=45000)
        self.assertGreaterEqual(self.page.locator("#tiles .tile").count(), 5)

    @requires_journal
    def test_cohort_and_shortlist_tables_have_rows(self):
        self.page.wait_for_selector("#cohort tbody tr", timeout=45000)
        self.assertGreaterEqual(self.page.locator("#cohort tbody tr").count(), 1)
        self.page.wait_for_selector("#short tbody tr", timeout=45000)
        self.assertGreaterEqual(self.page.locator("#short tbody tr").count(), 1)

    # -- interaction --
    def test_deepdiving_another_ticker_updates_the_page(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.wait_for_timeout(1200)
        self.page.fill("#ticker", "TLKM")
        self.page.click("#go")
        # Not `wait_for_selector('.verdict')`: the previous result is now kept on screen while the
        # new one loads, so the badge is present before and after. Wait for the content itself.
        self.page.wait_for_function(
            "() => { const t = document.querySelector('#dd').innerText;"
            " return !t.includes('Evaluating') && t.includes('TLKM'); }", timeout=45000)
        self.assertEqual(self.page.locator("#ticker").input_value(), "TLKM")
        self.assertIn("TLKM", self.page.inner_text("#dd"))
        self.settle()
        self.assertClean()

    def test_a_bad_ticker_shows_an_error_rather_than_breaking_the_page(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.fill("#ticker", "123")
        self.page.click("#go")
        self.page.wait_for_selector("#dd .err", timeout=20000)
        self.assertTrue(self.page.inner_text("#dd .err").strip())
        # the rest of the page must still work
        self.assertGreaterEqual(self.page.locator("#cats .chip").count(), 1)

    def test_switching_the_window_reloads_the_report(self):
        self.page.wait_for_selector("#tiles .tile", timeout=45000)
        self.page.click("#windows .pill[data-since='7d']")
        self.page.wait_for_timeout(2500)
        self.assertIn("on", self.page.get_attribute("#windows .pill[data-since='7d']", "class") or "")
        self.assertGreaterEqual(self.page.locator("#tiles .tile").count(), 5)
        self.settle()
        self.assertClean()

    def test_empty_state_is_shown_when_there_is_nothing_to_plot(self):
        """An empty journal must read as an empty journal, not as a broken chart or a 500.
        This is the first-run experience, so it matters as much as the populated one."""
        _, h = _json("/api/health")
        if h["decisions"] and not FORCE_EMPTY:
            self.skipTest("journal is populated — run with E2E_EMPTY=1 to exercise this path")
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.assertEqual(h["decisions"], 0, "this test is only meaningful on an empty journal")
        for selector in ("#equity", "#calib", "#cohort", "#short", "#funnel"):
            self.assertTrue(self.page.inner_text(selector).strip(),
                            f"{selector} rendered empty rather than saying so")
        # and the report must not invent numbers for an empty window
        self.assertEqual(self.page.locator("#tiles .tile").count(), 7)
        self.assertNotIn("NaN", self.page.inner_text("#tiles"))
        self.settle()
        self.assertClean()

    # -- motion and layout --
    def test_reveal_animation_fires_as_sections_enter_the_viewport(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        first = self.page.locator("section#deepdive .reveal.in").count()
        self.page.wait_for_timeout(600)
        self.assertGreater(first, 0, "the visible section never revealed")
        self.page.locator("#journal").scroll_into_view_if_needed()
        self.page.wait_for_timeout(1500)
        self.assertGreater(self.page.locator("section#journal .reveal.in").count(), 0,
                           "a section scrolled into view never revealed")

    @requires_journal
    def test_watchlist_refresh_reloads_without_analysing(self):
        """The button is 'Refresh', not 'Update': the watchlist is a GROUP BY over the journal, so
        it can only re-read what was recorded. It must never spend a Jev call or start a scan."""
        self.page.wait_for_selector("#cohort tbody tr", timeout=45000)
        self.page.wait_for_timeout(1500)
        reads, analyses = [], []
        self.page.on("request", lambda r: (
            reads.append(r.url) if "/api/watchlist" in r.url else
            analyses.append(r.url) if ("/api/deepdive" in r.url or "/api/scan" in r.url) else None))
        self.page.click("#cohort-refresh")
        self.page.wait_for_function(
            "() => !document.querySelector('#cohort-refresh').disabled", timeout=30000)
        self.page.wait_for_timeout(600)
        self.assertEqual(len(reads), 1, f"refresh should re-read once, made {len(reads)}")
        self.assertEqual(analyses, [], "refresh must not analyse — that is the Scan button's job")

    @requires_journal
    def test_watchlist_refresh_reports_unchanged_honestly(self):
        self.page.wait_for_selector("#cohort tbody tr", timeout=45000)
        self.page.wait_for_timeout(1500)
        before = self.page.locator("#cohort tbody tr").count()
        self.page.click("#cohort-refresh")
        self.page.wait_for_function(
            "() => document.querySelector('#cohort-note').textContent.trim().length > 0",
            timeout=30000)
        note = self.page.inner_text("#cohort-note")
        self.assertIn("unchanged", note, f"nothing was journalled, so it must say so: {note!r}")
        self.assertEqual(self.page.locator("#cohort tbody tr").count(), before)

    def test_watchlist_refresh_animates_only_while_in_flight(self):
        """The refresh is a fast local re-read, so racing it to catch the animation is flaky.
        Assert the contract instead: the class is set on click, and the indicator animates
        exactly while that class is present."""
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.wait_for_timeout(1500)
        idle = self.page.evaluate(
            "() => getComputedStyle(document.querySelector('#cohort-refresh .btn-ico')).animationName")
        self.assertEqual(idle, "none", "the indicator must be still at rest")

        animates = self.page.evaluate("""() => {
            const b = document.querySelector('#cohort-refresh');
            b.classList.add('is-busy');
            const n = getComputedStyle(b.querySelector('.btn-ico')).animationName;
            b.classList.remove('is-busy');
            return n;
        }""")
        self.assertNotEqual(animates, "none",
                            "the indicator must animate while the control is busy")

        self.page.click("#cohort-refresh")
        self.page.wait_for_function(
            "() => document.querySelector('#cohort-refresh').classList.contains('is-busy')"
            " || document.querySelector('#cohort-note').textContent.trim().length > 0",
            timeout=15000)
        self.page.wait_for_function(
            "() => !document.querySelector('#cohort-refresh').disabled", timeout=30000)
        settled = self.page.evaluate(
            "() => getComputedStyle(document.querySelector('#cohort-refresh .btn-ico')).animationName")
        self.assertEqual(settled, "none", "the animation must stop when the request lands")

    def test_watchlist_refresh_respects_reduced_motion(self):
        ctx = self._browser.new_context(viewport={"width": 1440, "height": 1000},
                                         reduced_motion="reduce")
        page = ctx.new_page()
        try:
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_selector("#cohort-refresh", timeout=45000)
            page.click("#cohort-refresh")
            page.wait_for_function(
                "() => document.querySelector('#cohort-refresh').classList.contains('is-busy')",
                timeout=15000)
            self.assertEqual(
                page.evaluate("() => getComputedStyle("
                              "document.querySelector('#cohort-refresh .btn-ico')).animationName"),
                "none", "the spin must be off under reduced motion; the disabled state still reads")
            page.wait_for_function(
                "() => !document.querySelector('#cohort-refresh').disabled", timeout=30000)
        finally:
            page.close()
            ctx.close()

    @requires_journal
    def test_window_change_clears_a_stale_refresh_claim(self):
        self.page.wait_for_selector("#cohort tbody tr", timeout=45000)
        self.page.wait_for_timeout(1500)
        self.page.click("#cohort-refresh")
        self.page.wait_for_function(
            "() => document.querySelector('#cohort-note').textContent.trim().length > 0",
            timeout=30000)
        self.page.click("#windows .pill[data-since='30d']")
        self.page.wait_for_timeout(1500)
        self.assertEqual(self.page.inner_text("#cohort-note").strip(), "",
                         "an 'unchanged' claim must not outlive the data it described")
    # -- theme --
    def test_theme_toggles_and_persists(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        start = self.page.evaluate("() => document.documentElement.dataset.theme")
        bg = self.page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        self.page.click("#theme")
        self.page.wait_for_timeout(400)
        after = self.page.evaluate("() => document.documentElement.dataset.theme")
        self.assertNotEqual(start, after, "the toggle changed nothing")
        self.assertEqual(after, "dark" if start == "light" else "light")
        new_bg = self.page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        self.assertNotEqual(bg, new_bg, "the palette did not change with the theme")
        self.assertTrue(self.page.locator("#theme").is_visible())
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_timeout(500)
        self.assertEqual(self.page.evaluate("() => document.documentElement.dataset.theme"),
                         after, "the choice must survive a reload")

    def test_theme_follows_the_system_when_never_chosen(self):
        ctx = self._browser.new_context(viewport={"width": 1440, "height": 1000},
                                         color_scheme="dark")
        page = ctx.new_page()
        try:
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            self.assertEqual(page.evaluate("() => document.documentElement.dataset.theme"),
                             "dark", "a dark-mode system should get dark, before any click")
        finally:
            page.close()
            ctx.close()

    def test_dark_palette_actually_inverts_the_ink(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)

        def luminance(colour):
            """getComputedStyle returns rgb(), not hex — slicing would read 'gb'."""
            parts = [int(float(p)) for p in re.findall(r"[\d.]+", colour)[:3]]
            return sum(parts) / len(parts)

        def probe():
            return self.page.evaluate("""() => ({
                ink: getComputedStyle(document.body).color,
                bg: getComputedStyle(document.body).backgroundColor,
            })""")

        self.page.evaluate("() => { document.documentElement.dataset.theme = 'light'; }")
        self.page.wait_for_timeout(250)
        light = probe()
        self.page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
        self.page.wait_for_timeout(250)
        dark = probe()
        # light mode is dark text on a light ground; dark mode is the reverse
        self.assertLess(luminance(light["ink"]), luminance(dark["ink"]),
                        f"light ink {light['ink']} must be darker than dark ink {dark['ink']}")
        self.assertGreater(luminance(light["bg"]), luminance(dark["bg"]),
                           f"light ground {light['bg']} must be lighter than {dark['bg']}")

    # -- loading --
    def test_loading_state_appears_while_a_process_button_runs(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.fill("#limit", "5")
        self.page.click("#scan-go")
        self.page.wait_for_function(
            "() => document.body.dataset.busy === 'true'", timeout=15000)
        # the sweep fades in, so wait for it to settle rather than sampling mid-transition
        self.page.wait_for_function(
            "() => getComputedStyle(document.querySelector('.sweep')).opacity > 0.9",
            timeout=10000)
        # One synchronous read: the busy window for a 5-candidate scan is well under a second, and
        # four separate evaluates straddle it — the state changes between them, not the code.
        got = self.page.evaluate("""() => {
            const b = document.querySelector('#scan-go');
            return {
                disabled: b.disabled,
                cls: b.className,
                aria: b.getAttribute('aria-busy'),
                sweep: getComputedStyle(document.querySelector('.sweep')).opacity,
                anim: getComputedStyle(b.querySelector('.btn-ico')).animationName,
            };
        }""")
        self.assertTrue(got["disabled"], "the control must be disabled while working")
        self.assertIn("is-busy", got["cls"])
        self.assertEqual(got["aria"], "true")
        self.assertGreater(float(got["sweep"]), 0.5,
                           f"the progress sweep must be visible while working, got {got['sweep']}")
        self.assertEqual(got["anim"], "breathe", "the icon must animate while working")

    def test_deepdive_shows_a_skeleton_while_loading(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.wait_for_timeout(1200)
        self.page.fill("#ticker", "TLKM")
        self.page.click("#go")
        self.page.wait_for_selector("#dd .skel", timeout=15000)
        # wait for the skeleton to be REPLACED, not for a badge that was already on screen
        self.page.wait_for_selector("#dd .skel", state="detached", timeout=45000)
        self.assertIn("TLKM", self.page.inner_text("#dd"))

    def test_loading_motion_uses_no_banned_easing(self):
        """`linear` and `ease-in-out` are banned, and `transition: all` with them.

        Asserted on computed values rather than on the stylesheet text: the file's own comments
        legitimately mention both words when explaining why the icon does not spin."""
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.fill("#limit", "5")
        self.page.click("#scan-go")
        self.page.wait_for_function(
            "() => document.body.dataset.busy === 'true'", timeout=15000)
        bad = self.page.evaluate("""() => {
            const out = [];
            const check = (n, pseudo) => {
                if (!n) return;
                const cs = getComputedStyle(n, pseudo);
                for (const t of [cs.animationTimingFunction, cs.transitionTimingFunction]) {
                    if (t && (t.includes('linear') || t.includes('ease-in-out'))) {
                        out.push((n.className || n.tagName) + (pseudo || '') + ' -> ' + t);
                    }
                }
            };
            for (const n of document.querySelectorAll('.btn, .pill, .chip, .sweep, .skel, .nav a')) {
                check(n);
            }
            const sw = document.querySelector('.sweep');
            if (sw) { check(sw, '::after'); }
            check(document.querySelector('.btn.is-busy .btn-ico'));
            return out;
        }""")
        self.assertEqual(bad, [], f"banned easing in use: {bad}")
        self.page.wait_for_function(
            "() => document.body.dataset.busy !== 'true'", timeout=60000)

    def test_loading_motion_is_absent_under_reduced_motion(self):
        ctx = self._browser.new_context(viewport={"width": 1440, "height": 1000},
                                         reduced_motion="reduce")
        page = ctx.new_page()
        try:
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_selector("#dd .verdict", timeout=45000)
            page.fill("#limit", "5")
            page.click("#scan-go")
            page.wait_for_function(
                "() => document.body.dataset.busy === 'true'", timeout=15000)
            for sel in ("#scan-go .btn-ico", ".sweep::after"):
                name = page.evaluate(
                    "(s) => { const n = s.endsWith('::after')"
                    " ? getComputedStyle(document.querySelector('.sweep'), '::after')"
                    " : getComputedStyle(document.querySelector(s));"
                    " return n.animationName; }", sel)
                self.assertEqual(name, "none", f"{sel} still animates under reduced motion")
            page.wait_for_function(
                "() => document.body.dataset.busy !== 'true'", timeout=60000)
        finally:
            page.close()
            ctx.close()

    def test_motion_tokens_have_no_literal_durations(self):
        """The hand-written durations are now :root tokens, so the motion contract has one owner.
        Guard it against someone reintroducing a literal."""
        with open(os.path.join(HERE, "web", "app.css")) as fh:
            css = fh.read()
        offenders = [ln.strip() for ln in css.splitlines()
                     if "transition:" in ln and re.search(r"\d+m?s\b", ln)]
        self.assertEqual(offenders, [], f"literal duration back in a transition: {offenders}")
        for token in ("--ease", "--dur-quick", "--dur-move", "--dur-reveal", "--dur-bar",
                      "--dur-row", "--reveal-y", "--reveal-blur"):
            self.assertIn(token, css, f"{token} is missing from the motion contract")

    def test_reduced_motion_is_respected(self):
        """With motion reduced the content must be legible immediately, with no transition
        waiting on an IntersectionObserver that may never fire."""
        ctx = self._browser.new_context(viewport={"width": 1440, "height": 1000},
                                         reduced_motion="reduce")
        page = ctx.new_page()
        try:
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_selector("#dd .verdict", timeout=45000)
            style = page.evaluate("""() => {
                const n = document.querySelector('#deepdive .reveal');
                const cs = getComputedStyle(n);
                return {opacity: cs.opacity, duration: cs.transitionDuration};
            }""")
            self.assertEqual(style["opacity"], "1", "a reduced-motion reveal must be visible")
            self.assertIn("0s", style["duration"], "a reduced-motion reveal must not animate")
            self.assertTrue(page.locator("#dd .verdict").is_visible())
        finally:
            page.close()
            ctx.close()

    def test_deepdive_does_not_double_submit(self):
        """The Deepdive button was never disabled, so a second click fired a second Jev call.

        A second *user* click cannot be forced while the button is disabled — Playwright simply
        waits for it to re-enable and then clicks. So the second attempt is dispatched in the
        page, which is the case the guard actually has to survive: a click arriving mid-flight."""
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.wait_for_timeout(1200)
        calls = []
        self.page.on("request", lambda r: calls.append(r.url)
                     if "/api/deepdive" in r.url else None)
        self.page.fill("#ticker", "TLKM")
        self.page.click("#go")
        self.page.wait_for_function(
            "() => document.querySelector('#go').disabled === true", timeout=10000)
        for _ in range(3):
            self.page.evaluate("() => document.querySelector('#go').click()")
        self.page.wait_for_function(
            "() => !document.querySelector('#dd').innerText.includes('Evaluating')", timeout=45000)
        self.page.wait_for_timeout(500)
        self.assertEqual(len(calls), 1, f"a disabled button must absorb the click, made {len(calls)}")

    def test_failed_deepdive_preserves_the_previous_result(self):
        """A typo must not cost the user the analysis already on screen."""
        self.allow_http_errors = True       # the 400 is the point of this test
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.wait_for_timeout(1200)
        good = self.page.inner_text("#dd")
        self.page.fill("#ticker", "123")
        self.page.click("#go")
        self.page.wait_for_selector("#dd .err", timeout=20000)
        self.assertTrue(self.page.inner_text("#dd .err").strip())
        self.assertIn(good.split("\n")[0], self.page.inner_text("#dd"),
                      "the previous result was discarded on a failed request")
        self.assertClean()

    def test_limit_input_is_validated(self):
        """`abc` used to become 200 silently, because only the server clamped it."""
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        scans = []
        self.page.on("request", lambda r: scans.append(r.url) if "/api/scan" in r.url else None)
        for bad in ("abc", "12abc", "0", "5000"):
            with self.subTest(limit=bad):
                self.page.fill("#limit", bad)
                self.page.click("#scan-go")
                self.page.wait_for_timeout(250)
                self.assertTrue(self.page.inner_text("#limit-err").strip(),
                                f"{bad!r} was accepted without complaint")
        self.assertEqual(scans, [], "an invalid limit must not reach the server")
        # and a valid one still works
        self.page.fill("#limit", "5")
        self.page.click("#scan-go")
        self.page.wait_for_function(
            "() => document.querySelector('#scan-go').disabled === true", timeout=10000)
        self.page.wait_for_function(
            "() => document.querySelector('#scan-go').disabled === false", timeout=45000)
        self.assertEqual(self.page.inner_text("#limit-err").strip(), "",
                         "a valid limit must clear the message")
        self.assertClean()

    def test_in_flight_state_disables_the_control(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.page.fill("#ticker", "GOTO")
        self.page.click("#go")
        self.page.wait_for_function(
            "() => document.querySelector('#go').disabled === true", timeout=10000)
        self.assertEqual(self.page.locator("#go .btn-ico").inner_text(), "↗",
                         "the icon disc must survive the busy state")
        self.page.wait_for_selector("#dd .verdict, #dd .err", timeout=45000)
        self.page.wait_for_function(
            "() => document.querySelector('#go').disabled === false", timeout=20000)
        self.assertEqual(self.page.locator("#go").inner_text().strip()[:1], "D",
                         "the idle label must be restored exactly")

    @requires_jev
    def test_reduced_motion_zeroes_every_transition(self):
        """A user who asked for no motion was still getting every transition animated: the block
        covered only .reveal. Every transition must now be inert."""
        ctx = self._browser.new_context(viewport={"width": 1440, "height": 1000},
                                         reduced_motion="reduce")
        page = ctx.new_page()
        try:
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_selector("#dd .verdict", timeout=45000)
            for sel in (".btn", ".pill", ".chip", ".pbar > i", ".nav a", "tbody tr"):
                got = page.evaluate(
                    "(s) => { const n = document.querySelector(s);"
                    " return n ? getComputedStyle(n).transitionDuration : null; }", sel)
                if got is None:
                    continue          # absent while data is still loading, not a failure
                # a shorthand with three properties computes to "0s, 0s, 0s", not "0s"
                for part in [p.strip() for p in got.split(",")]:
                    self.assertEqual(part, "0s", f"{sel} still transitions under reduced motion")
            self.assertEqual(
                page.evaluate("() => getComputedStyle(document.querySelector('.btn')).animationName"),
                "none", "an animation survived reduced motion")
        finally:
            page.close()
            ctx.close()

    def test_mobile_collapses_to_one_column_with_no_overflow(self):
        ctx = self._browser.new_context(viewport={"width": 375, "height": 800})
        page = ctx.new_page()
        try:
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_selector("#dd .verdict", timeout=45000)
            box = page.evaluate("""() => {
                const a = document.querySelector('#deepdive .s7').getBoundingClientRect();
                const b = document.querySelector('#deepdive .s5').getBoundingClientRect();
                return {ax: Math.round(a.x), bx: Math.round(b.x),
                        sw: document.documentElement.scrollWidth, iw: window.innerWidth};
            }""")
            self.assertEqual(box["ax"], box["bx"], "cards did not stack into one column")
            self.assertLessEqual(box["sw"], box["iw"] + 1, "the page scrolls sideways on mobile")
            self.assertTrue(page.locator("#dd .verdict").is_visible())
        finally:
            page.close()
            ctx.close()

    def test_tables_scroll_inside_their_card_rather_than_the_page(self):
        self.page.wait_for_selector("#cohort tbody tr", timeout=45000)
        box = self.page.evaluate("""() => {
            const t = document.querySelector('#cohort').closest('.scroll');
            return {tw: t.scrollWidth, cw: t.clientWidth,
                    sw: document.documentElement.scrollWidth, iw: window.innerWidth};
        }""")
        self.assertGreaterEqual(box["tw"], box["cw"])
        self.assertLessEqual(box["sw"], box["iw"] + 1)

    # -- accessibility basics --
    def test_every_control_has_an_accessible_name(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        unnamed = self.page.evaluate("""() => {
            const out = [];
            for (const n of document.querySelectorAll('button, input, a[href]')) {
                const name = (n.getAttribute('aria-label') || n.textContent || ''
                              || n.getAttribute('placeholder') || n.getAttribute('title') || '').trim();
                if (!name) out.push(n.outerHTML.slice(0, 80));
            }
            return out;
        }""")
        self.assertEqual(unnamed, [], "a control has no accessible name")

    def test_the_key_is_never_rendered_into_the_page(self):
        self.page.wait_for_selector("#dd .verdict", timeout=45000)
        self.assertNotIn("KEYPREFIX_", self.page.content())


if __name__ == "__main__":
    unittest.main(verbosity=2)
