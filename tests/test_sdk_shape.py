"""SDK-integration shape tests — no network, no key.

Proves that the agents call the Groq SDK with the right shapes, and that real
``groq.APIStatusError`` instances map onto our failure taxonomy.
"""

from __future__ import annotations

import io
import sys

import httpx
from PIL import Image

import agents.diagnostic_agent as diag_mod
import agents.advisor_agent as adv_mod
from groq import APIStatusError

PASS = 0
FAIL = 0

#: Never allow a credential-shaped string to survive into a user-facing error.
FAKE_KEY = "gsk_FakeShapeKey1234567890abcdef"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def groq_error(status: int, message: str = "boom") -> APIStatusError:
    """Build a REAL groq.APIStatusError carrying an HTTP status.

    Using the genuine exception class (not a lookalike) is what makes these
    tests meaningful: Groq exposes the status as ``status_code``, and our
    classifier must read it from there.
    """
    url = "https://api.groq.com/openai/v1/chat/completions"
    body = {"error": {"message": message, "type": "invalid_request_error"}}
    request = httpx.Request("POST", url)
    response = httpx.Response(status, request=request, json=body)
    return APIStatusError(message, response=response, body=body)


def make_png(color=(40, 160, 40), size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Completion:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class RecordingClient:
    """Stands in for groq.Groq and records the exact chat-completion shape."""

    def __init__(self, response_text=None, raise_exc=None):
        self.calls = []
        self._text = response_text
        self._exc = raise_exc

        class _Completions:
            def create(inner_self, **kwargs):
                self.calls.append(kwargs)
                if self._exc is not None:
                    raise self._exc
                return _Completion(self._text)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()

    @property
    def last_call(self):
        return self.calls[-1] if self.calls else {}


def main():
    from agents.config import Settings

    # quota_retry_base_delay_s=0 keeps the 429 cases instant; retry COUNT is
    # exercised separately in test_quota_retry.py with an injected sleeper.
    cfg = Settings(_env_file=None, groq_api_key=FAKE_KEY, quota_retry_base_delay_s=0.0)

    print("\n=== A. DiagnosticAgent calls the Groq vision API correctly ===")
    good = ('{"is_leaf_image": true, "crop": "Tomato", "disease": "Early blight", '
            '"disease_ar": "\u0627\u0644\u0644\u0641\u062d\u0629 \u0627\u0644\u0645\u0628\u0643\u0631\u0629", '
            '"pathogen": "Alternaria solani", "pathology": "tissue", '
            '"symptoms": ["a","b"], "severity": "moderate", "severity_pct": 0.12, '
            '"confidence": 0.87, "confidence_basis": "clear", '
            '"recommendations": ["rm lower leaves", "stop overhead water"], '
            '"differential": ["Late blight"], "image_quality": "good"}')

    client = RecordingClient(response_text=good)
    agent = diag_mod.DiagnosticAgent(cfg, client=client)
    res = agent.analyze(make_png(), filename="leaf.png")

    check("diagnostic succeeds", res.ok is True, res.error.message if res.error else "")
    check("exactly one SDK call", len(client.calls) == 1)
    call = client.last_call

    check("model == configured vision model", call.get("model") == cfg.vision_model,
          f"got {call.get('model')}")
    check("vision model is the documented live one", cfg.vision_model == "qwen/qwen3.8-27b",
          f"got {cfg.vision_model}")
    check("NOT the retired llama-3.2 vision id",
          "llama-3.2-11b-vision-preview" not in str(call.get("model")))

    # --- message / image payload shape ---------------------------------
    messages = call.get("messages")
    check("messages is a non-empty list", isinstance(messages, list) and len(messages) == 1)
    msg = messages[0] if messages else {}
    check("role is user", msg.get("role") == "user")
    content = msg.get("content")
    check("content is a list of 2 parts", isinstance(content, list) and len(content) == 2,
          f"got {content!r}"[:120])

    text_part = content[0] if isinstance(content, list) and content else {}
    check("first part is text", text_part.get("type") == "text")
    check("prompt carries the schema contract", "recommendations" in str(text_part.get("text", "")))

    img_part = content[1] if isinstance(content, list) and len(content) > 1 else {}
    check("second part is image_url", img_part.get("type") == "image_url")
    url = (img_part.get("image_url") or {}).get("url", "")
    check("image sent as base64 data URI", url.startswith("data:image/jpeg;base64,"),
          f"got {url[:40]!r}")
    check("data URI has real base64 payload", len(url.split(",", 1)[-1]) > 100)

    check("response_format json_object", call.get("response_format") == {"type": "json_object"})
    check("temperature is a float", isinstance(call.get("temperature"), float))
    check("max_completion_tokens set", isinstance(call.get("max_completion_tokens"), int))

    if res.ok:
        d = res.data
        check("disease normalised", d["disease"] == "Early blight")
        check("arabic preserved", d["disease_ar"].startswith("\u0627"))
        check("confidence float 0.87", abs(d["confidence"] - 0.87) < 1e-9)
        check("severity enum", d["severity"] == "moderate")
        check("symptoms list", d["symptoms"] == ["a", "b"])
        check("recommendations list present (frontend key)",
              d.get("recommendations") == ["rm lower leaves", "stop overhead water"],
              f"got {d.get('recommendations')!r}")
        check("low_confidence False", d["low_confidence"] is False)
        check("meta.agent == diagnostic", d["meta"]["agent"] == "diagnostic")
        check("meta.mime captured", d["meta"]["mime"] == "image/jpeg")
        check("meta.filename captured", d["meta"]["filename"] == "leaf.png")
        check("meta.model is the vision model", d["meta"]["model"] == cfg.vision_model)

        # The 5 keys the frontend contract depends on.
        for key in ("disease", "confidence", "severity", "symptoms", "recommendations"):
            check(f"frontend key present: {key}", key in d)

    print("\n=== B. DiagnosticAgent survives malformed model output ===")
    for bad_label, bad_text in [("plain text", "I cannot see an image."),
                                ("empty", ""),
                                ("array not object", '[{"a":1}]'),
                                ("truncated", '{"crop": "Tomato", "dis')]:
        c2 = RecordingClient(response_text=bad_text)
        r2 = diag_mod.DiagnosticAgent(cfg, client=c2).analyze(make_png())
        check(f"malformed ({bad_label}) -> graceful failure", r2.ok is False, "expected failure")
        if r2.ok is False:
            check(f"malformed ({bad_label}) -> kind=api", r2.error.kind == "api", f"got {r2.error.kind}")

    print("\n=== C. Real groq.APIStatusError instances map to the taxonomy ===")
    cases = [
        (401, "auth", False),
        (403, "auth", False),
        (429, "quota", True),
        (400, "input", False),
        (404, "api", False),      # retired model -> never retry
        (500, "busy", True),
        (503, "busy", True),
        (504, "busy", True),
        (418, "api", True),
    ]
    for code, expected_kind, expected_retry in cases:
        exc = groq_error(code, "boom")
        c3 = RecordingClient(raise_exc=exc)
        r3 = diag_mod.DiagnosticAgent(cfg, client=c3).analyze(make_png())
        check(f"HTTP {code} -> {expected_kind}", r3.ok is False and r3.error.kind == expected_kind,
              f"got {r3.error.kind if r3.error else 'ok'}")
        if r3.ok is False:
            check(f"HTTP {code} retryable={expected_retry}", r3.error.retryable is expected_retry)
            check(f"HTTP {code} never leaks key", FAKE_KEY not in (r3.error.message + str(r3.error.detail)))

    # 404 must carry actionable guidance, not a generic failure.
    r404 = diag_mod.DiagnosticAgent(cfg, client=RecordingClient(raise_exc=groq_error(404))).analyze(make_png())
    check("404 names the config field to fix",
          r404.ok is False and "vision_model" in r404.error.message,
          f"got {r404.error.message if r404.error else None!r}")

    print("\n=== C2. HTTP 429 returns the friendly Arabic message ===")
    exc429 = groq_error(429, "rate_limit_error")
    r429 = diag_mod.DiagnosticAgent(cfg, client=RecordingClient(raise_exc=exc429)).analyze(make_png())
    check("429 kind is quota", r429.ok is False and r429.error.kind == "quota")
    check("429 message is Arabic", "\u062a\u0645 \u0627\u0633\u062a\u0647\u0644\u0627\u0643 \u062d\u062f \u0627\u0644\u0637\u0644\u0628\u0627\u062a \u0627\u0644\u0645\u0624\u0642\u062a" in r429.error.message,
          f"got {r429.error.message!r}")
    check("429 message_en twin present", r429.error.to_dict().get("message_en") is not None)

    # ...and the same text must survive through the supervisor -> HTTP report.
    from agents.supervisor_agent import SupervisorAgent
    sup = SupervisorAgent(cfg, diagnostic=diag_mod.DiagnosticAgent(cfg, client=RecordingClient(raise_exc=exc429)),
                          advisor=adv_mod.AdvisorAgent(cfg, client=RecordingClient()))
    rep429 = sup.run(make_png())
    check("supervisor 429 -> ok=false", rep429.get("ok") is False)
    check("supervisor 429 report carries Arabic message",
          "\u062a\u0645 \u0627\u0633\u062a\u0647\u0644\u0627\u0643 \u062d\u062f \u0627\u0644\u0637\u0644\u0628\u0627\u062a \u0627\u0644\u0645\u0624\u0642\u062a" in str((rep429.get("error") or {}).get("message", "")),
          f"got {(rep429.get('error') or {}).get('message')!r}")

    print("\n=== D. Non-APIError (timeout/network) -> busy ===")
    c4 = RecordingClient(raise_exc=TimeoutError("timed out"))
    r4 = diag_mod.DiagnosticAgent(cfg, client=c4).analyze(make_png())
    check("timeout -> busy/retryable", r4.ok is False and r4.error.kind == "busy" and r4.error.retryable is True)

    print("\n=== E. AdvisorAgent calls SDK + merges with KB safety net ===")
    remote = ('{"title": "Remote title", "title_ar": "\u062c\u062f\u064a\u062f", "pathogen": "P", '
              '"kind": "fungal", "actions": ["r1"], '
              '"treatments": [{"type": "chemical", "detail": "X 1 mL/L"}], '
              '"irrigation_schedule": {"schedule": "S", "target": "T", "note": "N"}, '
              '"prevention": ["p1"]}')
    c5 = RecordingClient(response_text=remote)
    diag = {"disease": "Early blight", "crop": "Tomato", "severity": "moderate",
            "healthy": False, "confidence": 0.9, "symptoms": []}
    r5 = adv_mod.AdvisorAgent(cfg, client=c5).build_plan(diag)
    check("advisor remote succeeds", r5.ok is True, r5.error.message if r5.error else "")
    if r5.ok:
        d5 = r5.data
        check("source == groq", d5["meta"]["source"] == "groq")
        check("remote title wins", d5["title"] == "Remote title")
        check("remote treatment used", d5["treatments"][0]["detail"] == "X 1 mL/L")
        check("region still Tebessa", "T\u00e9bessa" in d5["region"])
        check("disclaimer always present", "T\u00e9bessa" in d5["disclaimer"])
        check("regional context always present", len(d5["regional_context"]) > 10)

    # Advisor must call the advisor model (not the vision model).
    adv_call = c5.last_call
    check("advisor uses advisor_model", adv_call.get("model") == cfg.advisor_model,
          f"got {adv_call.get('model')}")
    check("advisor model is user's chosen one", cfg.advisor_model == "llama-3.3-70b-versatile",
          f"got {cfg.advisor_model}")
    check("advisor request is text-only",
          isinstance((adv_call.get("messages") or [{}])[0].get("content"), str))
    check("advisor requests json_object", adv_call.get("response_format") == {"type": "json_object"})

    print("\n=== F. Advisor falls back to KB when remote returns junk ===")
    c6 = RecordingClient(response_text="not json at all")
    r6 = adv_mod.AdvisorAgent(cfg, client=c6).build_plan(diag)
    check("advisor still succeeds", r6.ok is True)
    if r6.ok:
        check("source == local_kb", r6.data["meta"]["source"] == "local_kb")
        check("fallback_reason recorded", bool(r6.data["meta"]["fallback_reason"]))
        check("KB title used", "Early blight" in r6.data["title"])

    print("\n=== G. Advisor honours advisor_fallback_enabled=False ===")
    cfg_no = Settings(_env_file=None, groq_api_key=FAKE_KEY, advisor_fallback_enabled=False)
    c7 = RecordingClient(raise_exc=groq_error(429, "rate_limit_error"))
    r7 = adv_mod.AdvisorAgent(cfg_no, client=c7).build_plan(diag)
    check("fallback disabled -> failure", r7.ok is False)
    if r7.ok is False:
        check("failure kind is quota", r7.error.kind == "quota", f"got {r7.error.kind}")

    print("\n=== H. Unconfigured key fails loudly (auth), never anonymously ===")
    no_key = Settings(_env_file=None, groq_api_key=None)
    r8 = diag_mod.DiagnosticAgent(no_key, client=None).analyze(make_png())
    check("unconfigured -> auth failure", r8.ok is False and r8.error.kind == "auth",
          f"got {r8.error.kind if r8.error else 'ok'}")
    check("auth message names GROQ_API_KEY",
          r8.ok is False and "GROQ_API_KEY" in r8.error.message,
          f"got {r8.error.message if r8.error else None!r}")

    print(f"\n{'='*52}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
