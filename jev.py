#!/usr/bin/env python3
"""Jev — the decider.

Jev is a structured-decision model reached at a private HTTP endpoint. The endpoint and the
model id are configuration, not code — see `.env.example`. Nothing here names the provider.
It does not generate text: you hand it a `state` and a set of typed questions, and it returns a
typed value plus a full probability distribution for each. Two consequences shape this design:

  - Jev cannot compute a price, so it never writes the entry plan. Python computes it and it
    travels with the decision. Jev scores the candidate.
  - `verdict.probabilities` is continuous, so "no trade" is a threshold on a number rather than
    the model's mood, and Jev's accuracy is measurable from the journal alone.

The free tier this model runs on is rejected on the provider's other, chat-shaped endpoint and
works here. Do not "simplify" this back to it.

Question types are exactly three: `noul` (yes/no + probability), `choice` (criteria map) and
`score` (rubric array). `str` and `bool` do not exist and are rejected with "Invalid request".

Usage:
  python3 jev.py demo     # real call, assert-based self-check, ~1k free tokens
"""
import http.client
import json
import os
import urllib.parse

_MODEL_KEY = "JEV_MODEL"

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
    },
    "momentum_confirmed": {
        "type": "noul",
        "instructions": "Is price-volume momentum confirmed by the bars themselves?",
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


def has_credentials():
    """True when a key is configured. The health endpoint uses this so the UI can say why Jev
    is unavailable instead of raising on a missing setting."""
    return bool(_env("JEV_API_KEY"))


def _endpoint():
    """The only URL this client will ever talk to, audited: https and nothing else."""
    url = _setting("JEV_API_URL")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise JevError(f"refusing {parts.scheme or 'no'} scheme: {url}")
    return parts


class JevError(RuntimeError):
    """Any failure talking to the decision endpoint. The body carries the reason, keep it."""


def ask(state, questions=QUESTIONS, model=None, timeout=60):
    """One decision call. `state` is prose (str) or a JSON-serialisable dict.

    http.client rather than urllib.request: the scheme is explicit in the constructor, so this
    helper can only ever talk to the endpoint it was given.
    """
    parts = _endpoint()
    body = json.dumps({
        "model": model or _setting("JEV_MODEL"),
        "state": state,
        "questions": questions,
    }).encode()
    conn = http.client.HTTPSConnection(parts.netloc, timeout=timeout)
    try:
        conn.request("POST", parts.path, body=body, headers={
            "Authorization": "Bearer " + _setting("JEV_API_KEY"),
            "Content-Type": "application/json",
        })
        resp = conn.getresponse()
        status, raw = resp.status, resp.read()
    except (OSError, http.client.HTTPException) as e:
        raise JevError(f"jev call failed: {e}") from e
    finally:
        conn.close()
    if status >= 400:
        raise JevError(f"jev endpoint {status}: {raw.decode(errors='replace')[:400]}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise JevError(f"jev returned non-JSON: {raw[:200]!r}") from e


def enter_probability(answers):
    """Jev's own probability that we should enter. This is the ranking score."""
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
    ff = n("foreign_net_buy_20d_pct", 0.0)
    gap = n("distance_to_significant_high_pct", 0.0)
    targets = t.get("targets_r") or [1, 2]
    rsi_word = "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "neutral"
    flow_word = "accumulation" if ff > 0 else "distribution" if ff < 0 else "flat foreign flow"
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
    return (
        f"{t.get('ticker') or '?'} at {n('close'):,.0f} IDR, {where}. "
        f"RSI {rsi:.0f} ({rsi_word}). OBV slope {obv:+.2f} over 20 days "
        f"({'rising, volume confirming' if obv > 0 else 'falling'}). "
        f"Foreign net buy {ff:+.1f}% of float over 20 days ({flow_word}). "
        f"Beta vs JKSE {n('beta_vs_jkse', 1.0):.1f}. "
        f"Stop {n('stop_pct'):.1f}% below entry, targets at "
        f"{', '.join(f'{x:g}R' for x in targets)}. "
        f"Recent news: {t.get('news') or 'none tracked'}."
    )


# ---------- self-check ----------
CANDIDATE = {
    "ticker": "BBCA.JK", "tf": "1d", "close": 9800, "rsi": 71,
    "obv_slope_20d": 0.12, "foreign_net_buy_20d_pct": 4.1, "beta_vs_jkse": 1.2,
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


def demo():
    # a candidate whose inputs are all absent must still render. candidate_state reports missing
    # inputs as None, and a bug here once made every such candidate crash the whole run.
    partial = dict(CANDIDATE, close=None, rsi=None, obv_slope_20d=None,
                   foreign_net_buy_20d_pct=None, distance_to_significant_high_pct=None,
                   stop_pct=None, targets_r=None, beta_vs_jkse=None, ticker=None)
    assert "?" in render(partial), "a candidate with no ticker must render, not raise"
    assert "no confirmed significant high" in render(partial), (
        "a candidate with no pivot must say so, not claim it is at a significant high")
    assert "at its last confirmed significant high" not in render(partial), (
        "never assert a level the candidate does not have")

    prose = ask(render(CANDIDATE))
    check(prose["answers"])
    assert prose["usage"]["input_tokens"] > 0, f"no usage reported: {prose}"
    as_json = ask(CANDIDATE)
    check(as_json["answers"])
    for label, r in (("prose", prose), ("json", as_json)):
        a = r["answers"]
        print(f"{label:5} enter={enter_probability(a):.2f} verdict={a['verdict']['choice']:4} "
              f"p10={a['p_enter']['noul']:.2f} conv={a['conviction']['score']:.2f} "
              f"risk={a['risk']['choice']:6} in={r['usage']['input_tokens']} out={r['usage']['output_tokens']}")


if __name__ == "__main__":
    demo()
