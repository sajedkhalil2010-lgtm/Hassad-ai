"""HTTP 429 / rate-limit retry tests — no network, no key, no real sleeping.

Proves the three behaviours the farmer actually depends on:
  1. a 429 is retried with exponential backoff instead of failing instantly,
  2. a non-quota error is NOT retried (never burn quota on a bad request),
  3. when retries are exhausted we return a friendly Arabic error, not a
     traceback and not an empty object.

Uses REAL ``groq.APIStatusError`` instances and the REAL Groq client shape
(``chat.completions.create``) so the assertions hold against production code
paths rather than a lookalike.
"""

from __future__ import annotations

import sys

import httpx
from PIL import Image

import agents.diagnostic_agent as diag_mod
import agents.advisor_agent as adv_mod
from groq import APIError as GroqAPIError
from groq import APIStatusError

from agents.base import QUOTA_MESSAGE_AR, is_quota_error, run_with_quota_retry
from agents.config import Settings
from agents.supervisor_agent import SupervisorAgent

PASS = 0
FAIL = 0

#: Never allow a credential-shaped string to survive into a user-facing error.
FAKE_KEY = "gsk_FakeRetryKey1234567890abcdef"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def make_png() -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (40, 160, 40)).save(buf, format="PNG")
    return buf.getvalue()


def api_error(status: int, message: str = "boom") -> APIStatusError:
    """Build a genuine groq.APIStatusError carrying an HTTP status."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    body = {"error": {"message": message, "type": "invalid_request_error"}}
    request = httpx.Request("POST", url)
    response = httpx.Response(status, request=request, json=body)
    return APIStatusError(message, response=response, body=body)


def quota_error() -> APIStatusError:
    return api_error(429, "rate_limit_error: exhausted")


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Completion:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class FlakyClient:
    """Fails with ``fail_with`` for the first ``fail_times`` calls, then succeeds.

    Exposes the same ``chat.completions.create`` surface as ``groq.Groq``.
    """

    def __init__(self, fail_with, fail_times: int, ok_text: str):
        self.calls = 0
        self.fail_with = fail_with
        self.fail_times = fail_times
        self.ok_text = ok_text

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                if outer.calls <= outer.fail_times:
                    raise outer.fail_with
                return _Completion(outer.ok_text)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


GOOD_JSON = (
    '{"is_leaf_image": true, "crop": "Tomato", "disease": "Early blight", '
    '"disease_ar": "\u0627\u0644\u0644\u0641\u062d\u0629 \u0627\u0644\u0645\u0628\u0643\u0631\u0629", '
    '"pathogen": "Alternaria solani", "pathology": "tissue", "symptoms": ["a"], '
    '"severity": "moderate", "severity_pct": 0.12, "confidence": 0.87, '
    '"confidence_basis": "clear", "recommendations": ["remove infected leaves"], '
    '"differential": ["Late blight"], "image_quality": "good"}'
)

# Zero real sleeping: we assert the schedule through the injected `sleep`.
FAST = {"quota_retry_base_delay_s": 0.0}


def main() -> int:
    print("\n=== A. is_quota_error (duck-typed, no SDK import required) ===")
    check("status_code=429 is quota", is_quota_error(quota_error()))
    # Regression guard: Groq sets status_code but NOT .code/.status. If the
    # detector ever regresses to reading only `.code`, the retry silently dies.
    check("detector reads status_code, not just .code",
          getattr(quota_error(), "code", None) is None and is_quota_error(quota_error()))
    check("status RESOURCE_EXHAUSTED is quota",
          is_quota_error(type("E", (Exception,), {"status": "RESOURCE_EXHAUSTED"})()))
    check("string status_code '429' is quota",
          is_quota_error(type("E", (Exception,), {"status_code": "429"})()))
    check("plain ValueError is NOT quota", not is_quota_error(ValueError("nope")))
    check("status_code=500 is NOT quota", not is_quota_error(api_error(500, "boom")))

    print("\n=== B. run_with_quota_retry backoff schedule ===")
    sleeps = []
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise quota_error()
        return "ok"

    result = run_with_quota_retry(flaky, attempts=4, base_delay_s=2.0, sleep=sleeps.append)
    check("recovers after transient 429s", result == "ok")
    check("made 3 calls", attempts["n"] == 3, f"got {attempts['n']}")
    check("backoff is exponential 2s then 4s", sleeps == [2.0, 4.0], f"got {sleeps}")

    print("\n=== C. exhausted retries re-raise the quota error ===")
    seen = []

    def always():
        seen.append(1)
        raise quota_error()

    try:
        run_with_quota_retry(always, attempts=3, base_delay_s=0.0, sleep=lambda _s: None)
        check("raises after exhausting attempts", False)
    except GroqAPIError:
        check("raises after exhausting attempts", True)
    check("honoured the attempt budget", len(seen) == 3, f"got {len(seen)}")

    print("\n=== D. non-quota errors are NOT retried ===")
    calls = {"n": 0}

    def bad_request():
        calls["n"] += 1
        raise api_error(400, "bad image")

    try:
        run_with_quota_retry(bad_request, attempts=3, base_delay_s=0.0, sleep=lambda _s: None)
        check("400 propagates", False)
    except GroqAPIError:
        check("400 propagates", True)
    check("400 is not retried (1 call only)", calls["n"] == 1, f"got {calls['n']}")

    cfg = Settings(_env_file=None, groq_api_key=FAKE_KEY, **FAST)

    print("\n=== E. DiagnosticAgent retries once and recovers ===")
    ok_client = FlakyClient(quota_error(), fail_times=1, ok_text=GOOD_JSON)
    res = diag_mod.DiagnosticAgent(cfg, client=ok_client).analyze(make_png())
    check("diagnosis succeeds after one 429", res.ok is True, res.error.message if res.error else "")
    check("SDK called exactly twice", ok_client.calls == 2, f"got {ok_client.calls}")

    print("\n=== F. DiagnosticAgent exhausted -> friendly Arabic error ===")
    dead = FlakyClient(quota_error(), fail_times=99, ok_text="")
    res2 = diag_mod.DiagnosticAgent(cfg, client=dead).analyze(make_png())
    check("fails cleanly, no exception", res2.ok is False)
    check("kind == quota", res2.ok is False and res2.error.kind == "quota")
    check("message is the Arabic text", res2.ok is False and res2.error.message == QUOTA_MESSAGE_AR,
          f"got {res2.error.message if res2.error else None!r}")
    check("marked retryable", res2.ok is False and res2.error.retryable is True)
    check("attempt budget respected", dead.calls == cfg.quota_retry_attempts,
          f"got {dead.calls}")
    check("no key leaked", FAKE_KEY not in str(res2.error.to_dict()))

    print("\n=== G. AdvisorAgent exhausted -> Arabic, and KB fallback still works ===")
    adv_dead = FlakyClient(quota_error(), fail_times=99, ok_text="")
    cfg_no_fallback = Settings(_env_file=None, groq_api_key=FAKE_KEY,
                               advisor_fallback_enabled=False, **FAST)
    adv_res = adv_mod.AdvisorAgent(cfg_no_fallback, client=adv_dead).build_plan(
        {"crop": "tomato", "disease": "Early blight", "severity": "moderate", "confidence": 0.9})
    check("advisor fails cleanly", adv_res.ok is False)
    check("advisor message is Arabic", adv_res.ok is False and adv_res.error.message == QUOTA_MESSAGE_AR)
    check("advisor retried the full budget", adv_dead.calls == cfg_no_fallback.quota_retry_attempts,
          f"got {adv_dead.calls}")

    kb = adv_mod.AdvisorAgent(cfg, client=FlakyClient(quota_error(), fail_times=99, ok_text="")).build_plan(
        {"crop": "tomato", "disease": "Early blight", "severity": "moderate", "confidence": 0.9})
    check("advisor falls back to local KB on quota", kb.ok is True)
    check("fallback source recorded", kb.ok is True and kb.data.get("meta", {}).get("source") == "local_kb")

    print("\n=== H. End-to-end: HTTP body is a clean Arabic error, never '{}' ===")
    from fastapi.testclient import TestClient

    from main import create_app

    e2e_client = FlakyClient(quota_error(), fail_times=99, ok_text="")
    app = create_app(cfg, supervisor=SupervisorAgent(
        cfg,
        diagnostic=diag_mod.DiagnosticAgent(cfg, client=e2e_client),
        advisor=adv_mod.AdvisorAgent(cfg, client=FlakyClient(quota_error(), fail_times=99, ok_text="")),
    ))
    http = TestClient(app).post("/api/v1/analyze", files={"file": ("leaf.png", make_png(), "image/png")})

    check("HTTP status is 429", http.status_code == 429, f"got {http.status_code}")
    body = http.json()
    check("body is NOT empty", bool(body))
    check("body.ok is false", body.get("ok") is False)
    check("body.error.kind == quota", (body.get("error") or {}).get("kind") == "quota")
    check("body.error.message is Arabic", "\u062a\u0645 \u0627\u0633\u062a\u0647\u0644\u0627\u0643 \u062d\u062f \u0627\u0644\u0637\u0644\u0628\u0627\u062a \u0627\u0644\u0645\u0624\u0642\u062a" in str((body.get("error") or {}).get("message", "")))
    check("no traceback in body", "Traceback" not in http.text)
    check("no key leaked over HTTP", FAKE_KEY not in http.text)

    print(f"\n{'='*52}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
