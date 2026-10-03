#!/usr/bin/env python3
"""The decider — the only part of this project allowed to have an opinion.

Two backends, one answer contract:

  structured   POST $JEV_API_URL. A decision model that returns typed values plus a full
               probability distribution per question. ~600 in / ~120 out per candidate.
  chat         POST $JEV_CHAT_URL. An ordinary chat-completions model, asked for the same
               object in JSON and coerced into the same typed shape. It spends ~4x the
               output tokens and is visibly less precise. It exists so the project is not
               welded to one vendor and so the two can be compared from the same journal —
               every decision row records which backend scored it.

The vendor, the endpoint and the model id are configuration, not code — see `.env.example`.
Nothing here names a provider. `JEV_BACKEND` picks one; `run.py --backend` overrides it for a
single run. Both backends return the same dict:

    {"model": str, "backend": "structured"|"chat", "answers": {...},
     "usage": {"input_tokens": int, "output_tokens": int}}

The decider does not generate text: it cannot compute a price, so it never writes the entry
plan. Python computes it and it travels with the decision. And `verdict.probabilities` is
continuous, so "no trade" is a threshold on a number rather than the model's mood, and its
accuracy is measurable from the journal alone.

Question types are exactly three: `noul` (yes/no + probability), `choice` (criteria map) and
`score` (rubric array). `str` and `bool` do not exist and are rejected by check().

Usage:
  python3 decider.py demo     # one real call per backend, assert-based self-check
"""
import http.client
import json
import os
import secrets
import urllib.parse

BACKENDS = ("structured", "chat")

# Provisional. Recalibrate from the journal after ~50 scored candidates.
ENTER_THRESHOLD = 0.6

QUESTIONS = {
    "verdict": {
        "type": "choice",
        "instructions": "Should we enter this momentum trade?",
        "criteria": {
            "enter": "Momentum confirmed, foreign flow supportive, stop defines the risk",
            "skip": "Any disqualifier: no thrust, foreign selling, extended, or a vague setup",
        },
    },
    "p_enter": {
        "type": "noul",
        "instructions": "Probability this trade is profitable over the next 10 bars",
        # Both outcomes are authored rather than derived. A decision model is told what yes and
        # no each mean, and "not profitable" is a different claim from "this cannot work".
        "yes": "The trade is profitable over the next 10 bars.",
        "no": "The trade is not profitable over the next 10 bars.",
    },
    "momentum_confirmed": {
        "type": "noul",
        "instructions": "Is price-volume momentum confirmed by the bars themselves?",
        "yes": "The bars themselves confirm price-volume momentum.",
        "no": "The bars themselves do not confirm price-volume momentum.",
    },
    "conviction": {
        "type": "score",
        "instructions": "How strong is the momentum setup?",
        "criteria": ["no edge", "weak", "solid", "textbook"],
    },
    "risk": {
        "type": "choice",
        "instructions": "How risky is this entry?",
        "criteria": {
            "low": "Liquid name, stop close by, target has room",
            "medium": "Normal swing risk",
            "high": "Extended from the level, thin, or right under resistance",
        },
    },
}

_SHAPES = {
    "noul": {"type", "noul"},
    "choice": {"type", "choice", "confidence", "probabilities"},
    "score": {"type", "score", "confidence", "legend", "probabilities"},
}


def _env(name):
    """One value from the environment, then from .env. No dependency, no parsing library.

    Everything provider-specific lives here and in .env, not in this file: the endpoint, the model
    id and the key are all configuration."""
    v = os.environ.get(name)
    if v:
        return v.strip()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    if key.strip() == name:
                        return value.strip()
    except OSError:
        pass
    return None


def _setting(name):
    v = _env(name)
    if not v:
        raise SystemExit(f"no {name} — put it in .env (see .env.example)")
    return v


def configured_backend():
    """Which backend the config selects. One env var switches the whole system."""
    b = (_env("JEV_BACKEND") or BACKENDS[0]).strip().lower()
    if b not in BACKENDS:
        raise DeciderError(f"JEV_BACKEND={b!r} is not one of {', '.join(BACKENDS)}")
    return b


def endpoint(backend=None):
    """The configured URL for a backend, or None. Public because the health probe has to reach
    the endpoint to find out whether it is actually answering."""
    name = "JEV_CHAT_URL" if (backend or configured_backend()) == "chat" else "JEV_API_URL"
    return _env(name)


def status(backend=None, timeout=1.5):
    """-> (ready, detail). Is the decision endpoint actually answering?

    A local model service is a process that can be down, still loading, or wedged, and an env var
    being set says nothing about any of that. A loopback endpoint is therefore really contacted;
    a remote one is only checked for credentials, because probing a third party on every health
    call would be rude and slow. The URL is never echoed back — the health response must not
    disclose the endpoint.
    """
    which = backend or configured_backend()
    url = endpoint(which)
    if not url:
        return False, "no endpoint configured"
    parts = urllib.parse.urlparse(url)
    if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        return has_credentials(which), "remote endpoint: credentials checked, not contacted"
    path = "/health"
    try:
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(parts.netloc, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(parts.netloc, timeout=timeout)
        try:
            conn.request("GET", path, headers={"User-Agent": "trading-jev/1.0"})
            resp = conn.getresponse()
            resp.read()
        finally:
            conn.close()
    except (OSError, http.client.HTTPException) as e:
        return False, f"local decider unreachable: {type(e).__name__}"
    return resp.status < 400, f"local decider: HTTP {resp.status}"


def has_credentials(backend=None):
    """True when the named backend (or the configured one) has everything it needs. The health
    endpoint uses this so the UI can say why the decider is unavailable instead of raising on a
    missing setting."""
    key_name = "JEV_CHAT_KEY" if (backend or configured_backend()) == "chat" else "JEV_API_KEY"
    if not _env(key_name):
        return False
    if (backend or configured_backend()) == "chat":
        return bool(_env("JEV_CHAT_URL") and _env("JEV_CHAT_MODEL"))
    return bool(_env("JEV_API_URL") and _env("JEV_MODEL"))


def _endpoint(name):
    """The only URL this client will ever talk to, audited: https, except loopback.

    A local decision model is a process on this machine speaking plain HTTP on 127.0.0.1, so the
    exception is scoped to exactly that. Anywhere else, the token still cannot leave in the clear.
    """
    url = _setting(name)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http"):
        raise DeciderError(f"refusing {parts.scheme or 'no'} scheme: {url}")
    if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise DeciderError(f"refusing plaintext {parts.scheme} to a non-loopback host: {url}")
    return parts


class DeciderError(RuntimeError):
    """Any failure talking to a decision backend. The body carries the reason, keep it."""


_SESSION = None


def _session_id():
    """The gateway behind this endpoint routes on x-opencode-session and rejects the stdlib's
    default User-Agent. Stable per process: a fresh id per call is just 15x the routing table."""
    global _SESSION
    if _SESSION is None:
        _SESSION = secrets.token_hex(16)
    return _SESSION


def _post(url_name, key_name, body, timeout):
    """One authenticated JSON POST to a configured endpoint. The key travels with its own
    endpoint: a key from one tier is rejected by the other, so a shared variable would be a
    403 waiting to happen.

    http.client rather than urllib.request: the scheme is explicit in the constructor, so this
    helper can only ever talk to the endpoint it was given.
    """
    parts = _endpoint(url_name)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    # HTTPS unless the endpoint is loopback. A local model service speaks plain HTTP, and allowing
    # that only for 127.0.0.1 keeps "never put a bearer token on the wire in the clear to a
    # remote host" a property of the code rather than a convention.
    loopback = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme != "https" and not loopback:
        raise DeciderError(f"{url_name}: refusing to send a key over {parts.scheme}")
    factory = (http.client.HTTPSConnection if parts.scheme == "https"
               else http.client.HTTPConnection)
    conn = factory(parts.netloc, timeout=timeout)
    try:
        conn.request("POST", path, body=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + _setting(key_name),
            "Content-Type": "application/json",
            "User-Agent": "trading-jev/1.0",
            "x-opencode-session": _session_id(),
        })
        resp = conn.getresponse()
        status, raw = resp.status, resp.read()
    except (OSError, http.client.HTTPException) as e:
        raise DeciderError(f"decider call failed: {e}") from e
    finally:
        conn.close()
    if status >= 400:
        raise DeciderError(f"{url_name} {status}: {raw.decode(errors='replace')[:400]}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise DeciderError(f"{url_name} returned non-JSON: {raw[:200]!r}") from e


def ask_structured(state, questions=QUESTIONS, model=None, timeout=60):
    """The typed endpoint. `state` is prose (str) or a JSON-serialisable dict."""
    r = _post("JEV_API_URL", "JEV_API_KEY", {
        "model": model or _setting("JEV_MODEL"),
        "state": state, "questions": questions}, timeout)
    if "answers" not in r:
        raise DeciderError(f"structured reply has no answers: {str(r)[:200]}")
    # every backend guarantees the contract on return, so no caller has to remember to ask
    check(r["answers"])
    return {"model": r.get("model") or model or _setting("JEV_MODEL"),
            "backend": "structured", "answers": r["answers"],
            "usage": r.get("usage") or {}}


def ask_chat(state, questions=QUESTIONS, model=None, timeout=60):
    """A chat model, asked for the same object and coerced into the same typed shape.

    The prompt carries the contract as a literal template, `response_format` keeps the reply
    to one JSON object, and `_contract` builds the typed answer from the question spec — the
    model's own `type` and `legend` are discarded, because the rubric is ours, not its opinion.
    """
    reply = _post("JEV_CHAT_URL", "JEV_CHAT_KEY", {
        "model": model or _setting("JEV_CHAT_MODEL"),
        "messages": [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": _chat_prompt(state, questions)}],
        "response_format": {"type": "json_object"},
        "max_tokens": 2000,
    }, timeout)
    try:
        choice = reply["choices"][0]
        if choice.get("finish_reason") == "length":
            raise DeciderError("chat answer hit the token cap before it finished")
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise DeciderError(f"chat reply has no content: {str(reply)[:200]}") from e
    answers = _contract(_extract(content), questions)
    check(answers)
    u = reply.get("usage") or {}
    return {"model": reply.get("model"), "backend": "chat", "answers": answers,
            "usage": {"input_tokens": u.get("prompt_tokens", 0),
                      "output_tokens": u.get("completion_tokens", 0)}}


def ask(state, questions=QUESTIONS, model=None, backend=None, timeout=60):
    """One decision call, from the selected backend. `state` is prose (str) or a dict."""
    which = backend or configured_backend()
    if which == "chat":
        return ask_chat(state, questions, model, timeout)
    if which == "structured":
        return ask_structured(state, questions, model, timeout)
    raise DeciderError(f"unknown backend {which!r} — one of {', '.join(BACKENDS)}")


# ---------- chat backend: same contract, from text ----------
_SYSTEM = ("You are a decision model. Answer with one JSON object, one key per question name, "
           "and no other text. Your probabilities are your own subjective beliefs, not certainties.")


def _template(questions):
    """The answer contract as a literal template, built from the questions themselves so the
    prompt cannot drift from what check() enforces."""
    out = {}
    for name, q in questions.items():
        if q["type"] == "noul":
            out[name] = {"type": "noul", "noul": "<0..1>"}
        elif q["type"] == "choice":
            out[name] = {"type": "choice",
                         "choice": "<" + "|".join(q["criteria"]) + ">",
                         "confidence": "<0..1>",
                         "probabilities": dict.fromkeys(q["criteria"], "<0..1>")}
        else:
            n = len(q["criteria"])
            out[name] = {"type": "score", "score": f"<0..{n - 1}>", "confidence": "<0..1>",
                         "legend": {str(i): c for i, c in enumerate(q["criteria"])},
                         "probabilities": dict.fromkeys(map(str, range(n)), "<0..1>")}
    return out


def _chat_prompt(state, questions):
    body = state if isinstance(state, str) else json.dumps(state)
    return (f"Candidate state:\n\n{body}\n\n"
            "Answer every question. Each `probabilities` map must sum to 1.0.\n\n"
            f"Questions:\n{json.dumps(questions, indent=1)}\n\n"
            "Reply with one JSON object, one key per question name, exactly this shape and "
            f"nothing else:\n{json.dumps(_template(questions), indent=1)}")


def _extract(text):
    """The first JSON object in the reply, whatever a chat model wrapped it in."""
    if "```" in text:
        text = text.split("```")[1].strip()
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    if start < 0:
        raise DeciderError(f"chat reply holds no JSON object: {text[:200]!r}")
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError as e:
        raise DeciderError(f"chat reply is not JSON: {text[:200]!r}") from e


def _num(v, lo, hi, what):
    """One number out of whatever a chat model wrote: a float, a bool, or `"58%"`."""
    if isinstance(v, bool):
        x = 1.0 if v else 0.0
    elif isinstance(v, str):
        s = v.strip()
        try:
            x = float(s.rstrip("%")) / (100.0 if s.endswith("%") else 1.0)
        except ValueError:
            raise DeciderError(f"{what}: {v!r} is not a number") from None
    else:
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise DeciderError(f"{what}: {v!r} is not a number") from None
    return min(max(x, lo), hi)


def _dist(raw, keys, what):
    """-> {key: probability} over exactly `keys`, summing to 1.

    A distribution that is absent or all-zero is a hard error, not a default: ENTER is a
    threshold on `verdict.probabilities.enter`, so inventing this number invents a trade."""
    if not isinstance(raw, dict):
        raise DeciderError(f"{what}: no probability distribution ({raw!r})")
    p = {k: _num(raw.get(k, 0.0), 0.0, float("inf"), f"{what}.{k}") for k in keys}
    total = sum(p.values())
    if total <= 0:
        raise DeciderError(f"{what}: every probability is zero")
    return {k: v / total for k, v in p.items()}


def _contract(raw, questions):
    """A chat model's JSON -> the exact shape check() enforces."""
    if not isinstance(raw, dict):
        raise DeciderError(f"chat answer is not an object: {str(raw)[:200]}")
    answers = {}
    for name, q in questions.items():
        a = raw.get(name)
        if a is None:
            raise DeciderError(f"chat answer has no {name!r}")
        # a bare value — 0.58, "enter", "solid" — goes in the field that question type holds
        body = a if isinstance(a, dict) else {("score" if q["type"] == "score" else q["type"]): a}
        if q["type"] == "noul":
            answers[name] = {"type": "noul",
                             "noul": _num(body.get("noul"), 0.0, 1.0, f"{name}.noul")}
            continue
        if q["type"] == "choice":
            keys = list(q["criteria"])
        else:
            keys = [str(i) for i in range(len(q["criteria"]))]
        p = _dist(body.get("probabilities"), keys, f"{name}.probabilities")
        out = {"type": q["type"],
               "confidence": _num(body.get("confidence", max(p.values())), 0.0, 1.0,
                                  f"{name}.confidence"),
               "probabilities": p}
        if q["type"] == "choice":
            # the distribution is the contract, so `choice` is its argmax rather than the
            # model's own word — the two can never disagree in a journalled row
            out["choice"] = max(p.items(), key=lambda kv: kv[1])[0]
        else:
            out["score"] = _rubric(body.get("score"), q["criteria"], f"{name}.score")
            out["legend"] = {str(i): c for i, c in enumerate(q["criteria"])}
        answers[name] = out
    return answers


def _rubric(raw, criteria, what):
    """A rubric score: a number 0..n-1, or the label itself as the model wrote it ("solid")."""
    labels = [c.lower() for c in criteria]
    if isinstance(raw, str) and raw.strip().lower() in labels:
        return float(labels.index(raw.strip().lower()))
    return _num(raw, 0.0, len(criteria) - 1.0, what)


def enter_probability(answers):
    """The decider's own probability that we should enter. This is the ranking score."""
    return answers["verdict"]["probabilities"]["enter"]


def decide(answers, threshold=ENTER_THRESHOLD):
    """-> (action, probability). Below the threshold the answer is SKIP, not silence."""
    p = enter_probability(answers)
    return ("ENTER" if p >= threshold else "SKIP"), p


def render(candidate):
    """Prose state: units and comparisons, not bare numbers.

    Measured, one paired call each way: on identical data prose scored enter=0.58 (verdict
    enter) and the raw dict scored enter=0.46 (verdict skip) — a 0.12 spread that straddles
    nothing. An earlier single sample suggested 0.36 vs 0.01 confidence and that did NOT
    replicate, so the render format is worth keeping behind the `ask()` flag but is not
    decisive. Judge it on the journal, not on one pair. See MASTER_PLAN Q11.
    """
    t = candidate

    def n(key, default=0.0):
        """`.get(key, default)` only helps when the key is *absent*. candidate_state() reports a
        genuinely missing input as None — a candidate with no significant high has no distance to
        one — and that reached here as None and blew up on a comparison, crashing the whole run.
        Coerce here instead."""
        v = t.get(key)
        return default if v is None else v

    rsi = n("rsi", 50.0)
    obv = n("obv_slope_20d", 0.0)
    gap = n("distance_to_significant_high_pct", 0.0)
    rsi_word = "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "neutral"
    if t.get("distance_to_significant_high_pct") is None:
        # No pivot at all. Saying "at its last significant high" here would be a plain
        # untruth fed to the model, so say what is actually true: there isn't one.
        where = "with no confirmed significant high in range"
    elif gap <= 0.5:
        where = "at its last confirmed significant high"
    elif gap < 0:
        where = f"{abs(gap):.1f}% ABOVE its last confirmed significant high"
    else:
        where = f"{gap:.1f}% below its last confirmed significant high"
    # Foreign flow and the entry plan are the two inputs most often absent, and both used to be
    # rendered as confident fiction — a flat flow, a 0.0% stop, a generic 1R/2R — which the model
    # then judged `verdict`'s "stop defines the risk" against. A gap is stated, never filled in.
    ff, sessions = t.get("foreign_net_100m_idr"), t.get("foreign_net_sessions")
    if ff is None:
        flow = "Foreign flow not available."
    else:
        window = f" over the last {sessions} sessions" if sessions else ""
        flow = (f"Foreign net buy {ff:+.1f} (100M IDR){window} "
                f"({'accumulation' if ff > 0 else 'distribution' if ff < 0 else 'flat'}).")
    stop, targets = t.get("stop_pct"), t.get("targets_r")
    risk = ("No entry plan was computed for this candidate, so no stop or target is known."
            if stop is None or not targets else
            f"Stop {stop:.1f}% below entry, targets at "
            f"{', '.join(f'{x:g}R' for x in targets)}.")
    return (
        f"{t.get('ticker') or '?'} at {n('close'):,.0f} IDR, {where}. "
        f"RSI {rsi:.0f} ({rsi_word}). OBV slope {obv:+.2f} over 20 days "
        f"({'rising, volume confirming' if obv > 0 else 'falling'}). "
        f"{flow} {risk} "
        f"Recent news: {t.get('news') or 'none tracked'}."
    )


# ---------- self-check ----------
CANDIDATE = {
    "ticker": "BBCA.JK", "tf": "1d", "close": 9800, "rsi": 71,
    "obv_slope_20d": 0.12, "foreign_net_100m_idr": 4.1, "foreign_net_sessions": 20,
    "distance_to_significant_high_pct": 0.0, "stop_pct": 3.1, "targets_r": [1, 2],
    "news": "positive dividend announcement, 3 days ago",
}


def check(answers):
    assert set(answers) == set(QUESTIONS), f"missing answers: {set(QUESTIONS) - set(answers)}"
    for name, a in answers.items():
        want = _SHAPES[QUESTIONS[name]["type"]]
        assert set(a) == want, f"{name}: expected {sorted(want)}, got {sorted(a)}"
    v = answers["verdict"]
    assert v["choice"] in ("enter", "skip"), f"verdict not one of our criteria: {v}"
    assert set(v["probabilities"]) == {"enter", "skip"}, f"unexpected verdict options: {v}"
    assert abs(sum(v["probabilities"].values()) - 1.0) < 0.02, f"probabilities must sum to 1: {v}"
    for q in ("p_enter", "momentum_confirmed"):
        assert 0.0 <= answers[q]["noul"] <= 1.0, f"{q} is not a probability: {answers[q]}"
    c = answers["conviction"]
    assert 0 <= c["score"] <= len(QUESTIONS["conviction"]["criteria"]) - 1, f"score out of rubric: {c}"
    assert answers["risk"]["choice"] in ("low", "medium", "high"), f"risk not one of our criteria: {answers['risk']}"


def _self_check_chat():
    """The chat adapter, offline and free: a flat reply, a fenced reply, weights that do not
    sum to 1, a rubric label where a number belongs, and the two ways it must refuse instead
    of guessing. A wrong number here is a wrong trade, so this runs before any call is made."""
    flat = {"verdict": {"choice": "enter", "probabilities": {"enter": 0.58, "skip": 0.42}},
            "p_enter": 0.6, "momentum_confirmed": True,
            "conviction": {"score": "solid",
                           "probabilities": {"0": 0.04, "1": 0.17, "2": 0.59, "3": 0.2}},
            "risk": {"choice": "high", "confidence": "80%",
                     "probabilities": {"high": 3, "low": 1}}}
    a = _contract(flat, QUESTIONS)
    check(a)
    assert a["verdict"]["choice"] == "enter" and abs(enter_probability(a) - 0.58) < 1e-9, a
    assert a["p_enter"]["noul"] == 0.6 and a["momentum_confirmed"]["noul"] == 1.0, a
    assert a["conviction"]["score"] == 2.0, a                      # a label, not a number
    assert a["conviction"]["legend"] == dict(zip("0123", QUESTIONS["conviction"]["criteria"],
                                                  strict=True)), a
    assert a["risk"]["probabilities"] == {"low": 0.25, "medium": 0.0, "high": 0.75}, a
    assert a["risk"]["confidence"] == 0.8, a                       # "80%"
    assert _extract('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract('sure: {"a": 2} — hope that helps') == {"a": 2}
    for bad in ({"verdict": "enter"}, "no json at all"):
        try:
            _contract(bad if isinstance(bad, dict) else _extract(bad), QUESTIONS)
        except DeciderError:
            continue
        raise AssertionError(f"should have refused rather than invented: {bad!r}")


def demo():
    _self_check_chat()
    # a candidate whose inputs are all absent must still render. candidate_state reports missing
    # inputs as None, and a bug here once made every such candidate crash the whole run.
    partial = dict(CANDIDATE, close=None, rsi=None, obv_slope_20d=None,
                   foreign_net_100m_idr=None, foreign_net_sessions=None,
                   distance_to_significant_high_pct=None,
                   stop_pct=None, targets_r=None, ticker=None)
    assert "?" in render(partial), "a candidate with no ticker must render, not raise"
    assert "no confirmed significant high" in render(partial), (
        "a candidate with no pivot must say so, not claim it is at a significant high")
    assert "at its last confirmed significant high" not in render(partial), (
        "never assert a level the candidate does not have")
    # The fixture above carries every field, so it cannot catch a key that render() reads and
    # screen.candidate_state() never produces. These three did, and every live decision was told
    # the stop was 0.0% and the flow was flat.
    assert "Foreign flow not available" in render(partial), (
        "absent flow must be stated, never rendered as a flat 0.0")
    assert "0.0% below entry" not in render(partial), (
        "an absent stop must not be rendered as a 0.0% stop")
    assert "Beta vs JKSE" not in render(partial), "beta is not measured, so it is not claimed"

    # One real call per backend, on the same state: the point of the demo is that the contract
    # holds whoever answers it. Then the configured backend, on the raw dict, because the prose
    # rendering is not the only state it has to survive.
    prose = render(CANDIDATE)
    for name in BACKENDS:
        if not has_credentials(name):
            print(f"{name:10} not configured — skipped (see .env.example)")
            continue
        r = ask(prose, backend=name)
        check(r["answers"])
        assert r["usage"]["input_tokens"] > 0, f"{name} reported no usage: {r}"
        a = r["answers"]
        print(f"{name:10} enter={enter_probability(a):.2f} verdict={a['verdict']['choice']:4} "
              f"p10={a['p_enter']['noul']:.2f} conv={a['conviction']['score']:.2f} "
              f"risk={a['risk']['choice']:6} in={r['usage']['input_tokens']} "
              f"out={r['usage']['output_tokens']}")
    as_json = ask(CANDIDATE, backend=configured_backend())
    check(as_json["answers"])
    assert set(as_json) == {"model", "backend", "answers", "usage"}, f"contract drifted: {sorted(as_json)}"

if __name__ == "__main__":
    demo()
