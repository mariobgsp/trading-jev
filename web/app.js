/* trading-jev UI. No framework, no build. Fetch, render, and one poll loop for long jobs. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, txt) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined) n.textContent = txt;
    return n;
  };
  const pct = (v, d = 1) => (v === null || v === undefined) ? "n/a" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`;
  const sign = (v) => (v === null || v === undefined) ? "muted" : (v > 0 ? "pos" : v < 0 ? "neg" : "muted");
  const num = (v, d = 2) => (v === null || v === undefined) ? "n/a" : v.toFixed(d);

  async function api(path, opts) {
    const r = await fetch(path, opts);
    let data;
    try { data = await r.json(); } catch { throw new Error(`${r.status} ${r.statusText}`); }
    if (!r.ok) throw new Error(data.error || `${r.status}`);
    return data;
  }

  /* ── in-flight state ────────────────────────────────────────────────────
     One owner for "working". It used to have two shapes: #scan-go toggled disabled by hand and
     #go did not, so a second click on Deepdive fired a second Jev call and wiped the result.
     Every data-bearing control goes through here. */
  function setBusy(control, busy, busyLabel) {
    const label = control && control.firstChild;
    const hasLabel = label && label.nodeType === 3;
    if (busy) {
      if (hasLabel) {
        if (control.dataset.idleLabel === undefined) {
          control.dataset.idleLabel = label.textContent;
        }
        if (busyLabel) label.textContent = busyLabel;
      }
      if (control) control.disabled = true;
      return;
    }
    if (hasLabel && control && control.dataset.idleLabel !== undefined) {
      label.textContent = control.dataset.idleLabel;
      delete control.dataset.idleLabel;
    }
    if (control) control.disabled = false;
  }

  // Mirrors serve.py _clamp. Kept here so a bad value is refused in the browser rather than
  // silently becoming the default with no indication that it did.
  const LIMIT = { lo: 1, hi: 1000, def: 200 };

  function readLimit() {
    const raw = $("#limit").value.trim();
    if (!raw) return LIMIT.def;                       // empty falls back, as the server does
    if (!/^\d+$/.test(raw)) return null;              // parseInt would accept "12abc"; don't
    const n = parseInt(raw, 10);
    if (n < LIMIT.lo || n > LIMIT.hi) return null;
    return n;
  }

  /* ── entry choreography: reveal on intersect, once ───────────────────── */
  const seen = new WeakSet();
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting || seen.has(e.target)) continue;
      seen.add(e.target);
      e.target.classList.add("in");
      io.unobserve(e.target);
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.06 });
  document.querySelectorAll(".reveal").forEach((n) => io.observe(n));

  document.querySelectorAll(".nav a").forEach((a) => {
    a.addEventListener("click", () => {
      document.querySelectorAll(".nav a").forEach((x) => x.classList.remove("on"));
      a.classList.add("on");
    });
  });

  /* ── deepdive ───────────────────────────────────────────────────────── */
  const GATE_KEYS = new Set(["breakout", "liquid"]);

  function bar(value) {
    const row = el("div", "row");
    row.appendChild(el("span", "lbl", value.label));
    const wrap = el("div");
    wrap.style.cssText = "width:5.5rem";
    const p = el("div", "pbar");
    const i = el("i");
    i.style.setProperty("--v", Math.max(0, Math.min(1, value.p)));
    p.appendChild(i);
    wrap.appendChild(p);
    row.appendChild(wrap);
    const v = el("span", "val", num(value.p, 2));
    if (value.p >= 0.6) v.className = "val pos";
    row.appendChild(v);
    return row;
  }

  function sparkline(closes, level) {
    const W = 520, H = 168, pad = 6;
    if (!closes || closes.length < 2) return el("p", "empty", "No price history.");
    const lo = Math.min(...closes), hi = Math.max(...closes);
    const span = (hi - lo) || 1;
    const x = (i) => pad + (i * (W - pad * 2)) / (closes.length - 1);
    const y = (v) => H - pad - ((v - lo) / span) * (H - pad * 2);
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", "100%");
    svg.style.display = "block";

    if (level && level >= lo && level <= hi) {
      const ln = document.createElementNS(ns, "line");
      ln.setAttribute("x1", 0); ln.setAttribute("x2", W);
      ln.setAttribute("y1", y(level)); ln.setAttribute("y2", y(level));
      ln.setAttribute("stroke", "#8d8d96"); ln.setAttribute("stroke-width", "1");
      ln.setAttribute("stroke-dasharray", "3 4");
      svg.appendChild(ln);
      const t = document.createElementNS(ns, "text");
      t.setAttribute("x", 4); t.setAttribute("y", y(level) - 6);
      t.setAttribute("fill", "#8d8d96");
      t.setAttribute("font-size", "10");
      t.setAttribute("font-family", "ui-monospace, monospace");
      t.textContent = `significant high ${Math.round(level).toLocaleString()}`;
      svg.appendChild(t);
    }
    const d = closes.map((c, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(c).toFixed(1)}`).join(" ");
    const area = document.createElementNS(ns, "path");
    area.setAttribute("d", `${d} L${x(closes.length - 1).toFixed(1)},${H - pad} L${pad},${H - pad} Z`);
    area.setAttribute("fill", "rgba(10,10,11,.045)");
    svg.appendChild(area);
    const line = document.createElementNS(ns, "path");
    line.setAttribute("d", d);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#0a0a0b");
    line.setAttribute("stroke-width", "1.4");
    line.setAttribute("stroke-linejoin", "round");
    line.setAttribute("stroke-linecap", "round");
    svg.appendChild(line);
    return svg;
  }

  async function loadShortlist() {
    const tbody = $("#short tbody");
    try {
      const d = await api("/api/shortlist");
      $("#funnel-sub").textContent = d.session
        ? `Session ${d.session} · ${d.eligible} candidates after the pre-gates, ${d.examined} examined.`
        : "No scan on record yet.";
      const f = $("#funnel");
      f.innerHTML = "";
      [["Listed", d.listed], ["Tradable", d.tradable], ["Under 1k", d.under_1000],
       ["Flow positive", d.eligible], ["Scored", d.examined], ["Shortlist", d.shortlisted]]
        .forEach(([k, val]) => { if (val !== null) f.appendChild(tile(k, String(val), "")); });
      if (d.skipped && d.skipped.length) {
        const warn = el("p", "small neg");
        warn.style.marginTop = ".7rem";
        warn.textContent = `${d.skipped.length} name(s) on the list could not be evaluated: `
          + d.skipped.map((s) => s.ticker).join(", ");
        f.parentNode.appendChild(warn);
      }
      tbody.innerHTML = "";
      if (!d.rows.length) {
        const tr = el("tr");
        const td = el("td", "empty", "Run a scan to build the shortlist.");
        td.colSpan = 7; tr.appendChild(td); tbody.appendChild(tr);
        return;
      }
      let stale = 0;
      for (const r of d.rows) {
        const tr = el("tr");
        const cell = (txt, cls) => tr.appendChild(el("td", cls, txt));
        cell(r.ticker, "num");
        cell(r.name || "—");
        cell(num(r.close, 0), "n");
        cell(num(r.rsi, 0), "n");
        cell(num((r.adv20 || 0) / 1e9, 2), "n");
        cell(String(r.rank_score), "n");
        if (r.passed) {
          cell((r.rank_signals || []).join(", ") || "—");
        } else {
          // Journalled under older rules. The gate set changed after this session, so the name
          // would not clear today's gate. Show that rather than quietly dropping it.
          stale += 1;
          cell("no longer clears the gate", "small neg");
        }
        tbody.appendChild(tr);
      }
      if (stale) {
        const tr = el("tr");
        const td = el("td", "small muted",
          `${stale} name(s) were journalled under an earlier gate set and would not qualify today`);
        td.colSpan = 7; tr.appendChild(td); tbody.appendChild(tr);
      }
    } catch (e) {
      tbody.innerHTML = "";
      const tr = el("tr");
      const td = el("td", "err", e.message);
      td.colSpan = 7; tr.appendChild(td); tbody.appendChild(tr);
    }
  }

  async function deepdive(code) {
    const raw = (code || $("#ticker").value).trim().toUpperCase();
    if (!raw) return;
    const go = $("#go");
    if (go.disabled) return;                            // one request per click
    $("#ticker").value = raw;
    const box = $("#dd");
    setBusy(go, true, "Reading…");
    const pending = el("p", "empty", "Evaluating…");
    box.appendChild(pending);
    try {
      const d = await api(`/api/deepdive?ticker=${encodeURIComponent(raw)}`);
      box.innerHTML = "";

      const head = el("div");
      head.style.cssText = "display:flex;align-items:center;gap:.9rem;flex-wrap:wrap;margin:1.1rem 0 .2rem";
      const v = d.jev && d.jev.action ? d.jev.action : "NONE";
      const badge = el("span", `verdict ${v.toLowerCase()}`);
      badge.appendChild(el("span", "dot"));
      badge.appendChild(document.createTextNode(v));
      head.appendChild(badge);
      const name = el("div");
      // textContent, never innerHTML: the name comes from IDX, i.e. from outside this app.
      const title = el("div", null, d.name || d.ticker);
      title.style.cssText = "font-size:1.15rem;font-weight:620;letter-spacing:-.03em";
      name.appendChild(title);
      const meta = el("div", "small muted num");
      meta.textContent = `${d.ticker} · ${d.bars} bars · session ${d.session || "—"}`;
      name.appendChild(meta);
      head.appendChild(name);
      box.appendChild(head);

      const tiles = el("div", "tiles");
      tiles.style.marginTop = "1.1rem";
      const add = (k, val, n) => {
        const t = el("div", "tile");
        t.appendChild(el("div", "k", k));
        t.appendChild(el("div", "v num", val));
        if (n) t.appendChild(el("div", "n", n));
        tiles.appendChild(t);
      };
      add("p_enter", num(d.jev && d.jev.p_enter), "threshold 0.60");
      add("RSI", num(d.context.rsi, 0), d.context.rsi >= 70 ? "overbought" : d.context.rsi <= 30 ? "oversold" : "neutral");
      add("ADV20", num((d.context.adv20 || 0) / 1e9, 2), "IDR bn / day");
      add("Gates", `${Object.values(d.required).filter(Boolean).length}/2`, d.passed ? "passed" : "blocked");
      box.appendChild(tiles);

      const c = $("#cats");
      c.innerHTML = "";
      c.style.marginTop = "";
      const order = [...Object.keys(d.categories)].sort((a, b) => (GATE_KEYS.has(b) ? 1 : 0) - (GATE_KEYS.has(a) ? 1 : 0));
      for (const k of order) {
        const on = d.categories[k] === true;
        const chip = el("span", `chip${GATE_KEYS.has(k) ? " gate" : ""}${on ? " on" : ""}`,
          k.replace(/_/g, " "));
        if (d.categories[k] === null) { chip.style.opacity = ".4"; chip.title = "input absent"; }
        c.appendChild(chip);
      }
      box.classList.add("shown");

      $("#spark-sub").textContent = d.pivots
        ? `${d.bars} bars · ${d.pivots} significant high${d.pivots > 1 ? "s" : ""} found. The rule is the dashed line.`
        : `${d.bars} bars · no significant high in range — this is what the sideways filter looks like.`;
      const sp = $("#spark");
      sp.innerHTML = "";
      sp.appendChild(sparkline(d.closes, d.pivot_level));

      const jb = $("#jev");
      jb.innerHTML = "";
      if (!d.jev) {
        jb.appendChild(el("p", "empty", "Jev was not asked."));
      } else if (d.jev.error) {
        jb.appendChild(el("p", "err", d.jev.error));
      } else {
        const a = d.jev.answers;
        jb.appendChild(bar({ label: "enter the trade", p: a.verdict.probabilities.enter }));
        jb.appendChild(bar({ label: "profitable over 10 bars", p: a.p_enter.noul }));
        jb.appendChild(bar({ label: "momentum confirmed", p: a.momentum_confirmed.noul }));
        const legend = a.conviction.legend[String(Math.round(a.conviction.score))] || "—";
        const cr = el("div", "row");
        cr.appendChild(el("span", "lbl", "conviction"));
        const s2 = el("div", "pbar");
        s2.style.cssText = "width:5.5rem";
        const i2 = el("i");
        i2.style.setProperty("--v", a.conviction.score / 3);
        s2.appendChild(i2);
        cr.appendChild(s2);
        cr.appendChild(el("span", "val", `${a.conviction.score.toFixed(2)} · ${legend}`));
        jb.appendChild(cr);
        jb.appendChild(bar({ label: `risk (${a.risk.choice})`, p: a.risk.probabilities[a.risk.choice] }));
        const foot = el("p", "small muted");
        foot.style.marginTop = ".9rem";
        foot.textContent = `${d.jev.usage.input_tokens} in / ${d.jev.usage.output_tokens} out · preview, not journalled`;
        jb.appendChild(foot);
        jb.classList.add("shown");
      }

      const pl = $("#plan");
      pl.innerHTML = "";
      if (!d.plan) {
        pl.appendChild(el("p", "empty", "No plan — this name has not cleared both gates. Jev cannot return a price; Python computes the plan, and only for a name worth trading."));
      } else {
        const rows = el("div", "rows");
        [["entry", d.plan.entry], ["stop", d.plan.stop], ["target 1", d.plan.tp1], ["target 2", d.plan.tp2]]
          .forEach(([k, val]) => {
            const r = el("div", "row");
            r.appendChild(el("span", "lbl", k));
            r.appendChild(el("span", "val", Number(val).toLocaleString()));
            rows.appendChild(r);
          });
        const risk = el("div", "row");
        risk.appendChild(el("span", "lbl", "risk"));
        risk.appendChild(el("span", "val", `${d.plan.risk_pct}%  ·  1.5 × ATR`));
        rows.appendChild(risk);
        pl.appendChild(rows);
      }
      $("#prose").textContent = d.prose;
    } catch (e) {
      // Whatever is already on screen stays. A typo must not cost the user the analysis they
      // are reading.
      pending.remove();
      box.appendChild(el("p", "err", e.message));
    } finally {
      setBusy(go, false);
    }
  }

  /* ── scan, as a background job with streamed output ─────────────────── */
  let polling = null;

  function log(lines) {
    const box = $("#log");
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
    box.textContent = lines.join("\n");
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  async function watch(jobId, control) {
    if (polling) clearInterval(polling);
    const done = async () => {
      clearInterval(polling);
      polling = null;
      // the control that started the job, not whichever one happens to be on screen
      setBusy(control, false);
      await loadReport();
    };
    polling = setInterval(async () => {
      try {
        const j = await api(`/api/job?id=${jobId}`);
        log(j.log || []);
        if (j.status !== "running") {
          log((j.log || []).concat(j.error ? [`[${j.error}]`] : []));
          await loadShortlist();
          await done();
        }
      } catch (e) {
        clearInterval(polling); polling = null;
        setBusy(control, false);
        log([`error: ${e.message}`]);
      }
    }, 900);
  }

  async function startScan() {
    const go = $("#scan-go");
    if (go.disabled) return;
    const err = $("#limit-err");
    const limit = readLimit();
    if (limit === null) {
      err.textContent = `Limit must be a whole number from ${LIMIT.lo} to ${LIMIT.hi}.`;
      return;
    }
    err.textContent = "";
    setBusy(go, true, "Scanning…");
    log([`$ run.py run --limit ${limit}`]);
    try {
      const r = await api(`/api/scan?limit=${limit}`, { method: "POST" });
      await watch(r.job, go);
    } catch (e) {
      log([`error: ${e.message}`]);
    } finally {
      setBusy(go, false);
    }
  }

  /* ── journal ────────────────────────────────────────────────────────── */
  function tile(k, v, n, cls) {
    const t = el("div", "tile");
    t.appendChild(el("div", "k", k));
    const val = el("div", `v num${cls ? ` ${cls}` : ""}`, v);
    t.appendChild(val);
    if (n) t.appendChild(el("div", "n", n));
    return t;
  }

  function equity(rep) {
    const box = $("#equity");
    box.innerHTML = "";
    const pts = rep.equity.filter((e) => e.cumulative_r !== null);
    if (!pts.length) { box.appendChild(el("p", "empty", "No resolved entry yet, so no curve. Day one vetoed everything.")); return; }
    const W = 460, H = 168, pad = 10;
    const ys = pts.map((p) => p.cumulative_r);
    const lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), span = (hi - lo) || 1;
    const x = (i) => pad + (i * (W - pad * 2)) / Math.max(1, pts.length - 1);
    const y = (val) => H - pad - ((val - lo) / span) * (H - pad * 2);
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("width", "100%");
    const zero = document.createElementNS(ns, "line");
    zero.setAttribute("x1", 0); zero.setAttribute("x2", W);
    zero.setAttribute("y1", y(0)); zero.setAttribute("y2", y(0));
    zero.setAttribute("stroke", "#8d8d96"); zero.setAttribute("stroke-width", "1");
    zero.setAttribute("stroke-dasharray", "3 4");
    svg.appendChild(zero);
    const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.cumulative_r).toFixed(1)}`).join(" ");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", d); path.setAttribute("fill", "none");
    path.setAttribute("stroke", ys[ys.length - 1] >= 0 ? "#0f6e52" : "#9a3b2f");
    path.setAttribute("stroke-width", "1.6"); path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
    const end = document.createElementNS(ns, "circle");
    end.setAttribute("cx", x(pts.length - 1)); end.setAttribute("cy", y(ys[ys.length - 1]));
    end.setAttribute("r", "3.5");
    end.setAttribute("fill", ys[ys.length - 1] >= 0 ? "#0f6e52" : "#9a3b2f");
    svg.appendChild(end);
    box.appendChild(svg);
  }

  function calibration(rep) {
    const box = $("#calib");
    box.innerHTML = "";
    if (!rep.buckets.length) {
      box.appendChild(el("p", "empty", "Nothing resolved yet. Calibration needs trades that have finished."));
      return;
    }
    const best = Math.max(...rep.buckets.map((b) => b[2] / b[1]));
    for (const [b, n, wins] of rep.buckets) {
      const rate = wins / n;
      const r = el("div", "row");
      r.appendChild(el("span", "lbl num", `p ≥ ${b.toFixed(1)}`));
      const wrap = el("div");
      wrap.style.cssText = "width:7rem";
      const p = el("div", "pbar");
      const i = el("i");
      i.style.setProperty("--v", rate);
      if (rate === best) i.style.background = "var(--enter)";
      p.appendChild(i);
      wrap.appendChild(p);
      r.appendChild(wrap);
      const v = el("span", "val", `${(rate * 100).toFixed(0)}%`);
      if (rate === best) v.className = "val pos";
      r.appendChild(v);
      r.appendChild(el("span", "small muted", `${wins}/${n}`));
      box.appendChild(r);
    }
  }

  async function loadCohort() {
    const tbody = $("#cohort tbody");
    tbody.innerHTML = "";
    try {
      const d = await api("/api/watchlist?perf=1");
      if (!d.rows.length) {
        const tr = el("tr");
        const td = el("td", "empty", "Nothing surfaced yet. Run a scan.");
        td.colSpan = 11; tr.appendChild(td); tbody.appendChild(tr);
        return;
      }
      for (const r of d.rows) {
        const tr = el("tr");
        const p = r.perf || {};
        const cell = (txt, cls) => { const c = el("td", cls, txt); tr.appendChild(c); };
        cell(r.ticker, "num");
        cell(r.first_seen, "num");
        cell(r.last_seen, "num");
        cell(String(r.seen), "n");
        cell(num(r.avg_p), "n");
        const st = el("td");
        st.appendChild(el("span", `verdict ${r.status === "IN" ? "enter" : r.status === "EXCLUDED" ? "skip" : "none"}`, r.status));
        tr.appendChild(st);
        cell(pct(p.r5), `n ${sign(p.r5)}`);
        cell(pct(p.r10), `n ${sign(p.r10)}`);
        cell(pct(p.r20), `n ${sign(p.r20)}`);
        cell(pct(p.mfe20), `n ${sign(p.mfe20)}`);
        cell(pct(p.mae20), `n ${sign(p.mae20)}`);
        tbody.appendChild(tr);
      }
      if (d.perf_capped) {
        const tr = el("tr");
        const td = el("td", "small muted", `performance shown for the first 60 of ${d.rows.length} names`);
        td.colSpan = 11; tr.appendChild(td); tbody.appendChild(tr);
      }
    } catch (e) {
      const tr = el("tr");
      const td = el("td", "err", e.message);
      td.colSpan = 11; tr.appendChild(td); tbody.appendChild(td);
    }
  }

  async function loadReport() {
    const since = document.querySelector("#windows .pill.on").dataset.since;
    try {
      const rep = await api(`/api/report?since=${since}`);
      $("#since-note").textContent = rep.since === "all time" ? "Every session on record." : `from ${rep.since}`;
      $("#rep-sub").textContent = rep.entered
        ? `${rep.entered} entered of ${rep.scored} scored.`
        : `Jev vetoed all ${rep.scored} names it saw in this window.`;

      const tiles = $("#tiles");
      tiles.innerHTML = "";
      tiles.appendChild(tile("Scored", String(rep.scored), "candidates"));
      tiles.appendChild(tile("Entered", String(rep.entered), "gates cleared"));
      tiles.appendChild(tile("Vetoed", String(rep.vetoed),
        rep.veto_rate === null ? "" : `${(rep.veto_rate * 100).toFixed(0)}%`));
      tiles.appendChild(tile("Hit rate", rep.hit_rate === null ? "n/a" : `${(rep.hit_rate * 100).toFixed(0)}%`,
        `${rep.resolved} resolved`));
      tiles.appendChild(tile("Still open", String(rep.open), "not counted yet"));
      tiles.appendChild(tile("Unresolvable", String(rep.unresolvable),
        "no forward bars", rep.unresolvable ? "neg" : ""));
      tiles.appendChild(tile("Total R", rep.entered ? `${rep.total_r > 0 ? "+" : ""}${rep.total_r}` : "n/a",
        rep.avg_r === null ? "" : `avg ${rep.avg_r}`, rep.total_r > 0 ? "pos" : rep.total_r < 0 ? "neg" : ""));
      equity(rep);
      calibration(rep);
    } catch (e) {
      $("#rep-sub").classList.add("err");
      $("#rep-sub").textContent = e.message;
    }
    await loadCohort();
  }

  /* ── wiring ─────────────────────────────────────────────────────────── */
  // Wrapped, not passed by reference: addEventListener would hand deepdive the MouseEvent as its
  // `code` argument, and a MouseEvent has no .trim() — the handler threw and the button did
  // nothing. Only an end-to-end click test could find that.
  $("#go").addEventListener("click", () => deepdive());
  $("#ticker").addEventListener("keydown", (e) => { if (e.key === "Enter") deepdive(); });
  $("#scan-go").addEventListener("click", startScan);
  document.querySelector("[data-resolve]").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    setBusy(btn, true, "Resolving…");
    log(["$ run.py resolve"]);
    try { const r = await api("/api/resolve", { method: "POST" }); await watch(r.job, btn); }
    catch (err) { log([`error: ${err.message}`]); }
    finally { setBusy(btn, false); }
  });
  document.querySelectorAll("#windows .pill").forEach((p) => {
    p.addEventListener("click", () => {
      document.querySelectorAll("#windows .pill").forEach((x) => x.classList.remove("on"));
      p.classList.add("on");
      loadReport();
    });
  });

  loadReport();
  loadShortlist();
  deepdive("BBRI");
})();
