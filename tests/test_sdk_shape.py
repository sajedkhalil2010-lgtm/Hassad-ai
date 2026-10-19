"""SDK-integration shape tests — no network, no key.

Proves that the agents call google-genai 2.29 with the right shapes, and that
real ``google.genai.errors.APIError`` instances map onto our failure taxonomy.
"""

from __future__ import annotations

import io
import sys

from PIL import Image

import agents.diagnostic_agent as diag_mod
import agents.advisor_agent as adv_mod
from google.genai import errors as genai_errors

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def make_png(color=(40, 160, 40), size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class RecordingClient:
    """Stands in for genai.Client and records the exact call shape."""

    def __init__(self, response_text=None, raise_exc=None):
        self.calls = []
        self._text = response_text
        self._exc = raise_exc
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc

        class _Resp:
            text = self._text

        return _Resp()


def main():
    settings_cls = None
    from agents.config import Settings

    cfg = Settings(_env_file=None, gemini_api_key="AIzaFakeKeyForShapeTest1234567890")

    print("\n=== A. DiagnosticAgent calls the SDK correctly ===")
    good = ('{"is_leaf_image": true, "crop": "Tomato", "disease": "Early blight", '
            '"disease_ar": "\u0627\u0644\u0644\u0641\u062d\u0629 \u0627\u0644\u0645\u0628\u0643\u0631\u0629", '
            '"pathogen": "Alternaria solani", "pathology": "tissue", '
            '"symptoms": ["a","b"], "severity": "moderate", "severity_pct": 0.12, '
            '"confidence": 0.87, "confidence_basis": "clear", '
            '"differential": ["Late blight"], "image_quality": "good"}')

    client = RecordingClient(response_text=good)
    agent = diag_mod.DiagnosticAgent(cfg, client=client)
    res = agent.analyze(make_png(), filename="leaf.png")

    check("diagnostic succeeds", res.ok is True, res.error.message if res.error else "")
    check("exactly one SDK call", len(client.calls) == 1)
    call = client.calls[0]
    check("model passed", call.get("model") == cfg.gemini_model)
    contents = call.get("contents")
    check("contents is a list of 2", isinstance(contents, list) and len(contents) == 2)
    check("first content is a Part with inline bytes", hasattr(contents[0], "inline_data") or "Part" in type(contents[0]).__name__)
    check("second content is the prompt str", isinstance(contents[1], str))

    conf = call.get("config")
    check("config is GenerateContentConfig", type(conf).__name__ == "GenerateContentConfig")
    check("response_mime_type json", conf.response_mime_type == "application/json")

    if res.ok:
        d = res.data
        check("disease normalised", d["disease"] == "Early blight")
        check("arabic preserved", d["disease_ar"].startswith("\u0627"))
        check("confidence float 0.87", abs(d["confidence"] - 0.87) < 1e-9)
        check("severity enum", d["severity"] == "moderate")
        check("symptoms list", d["symptoms"] == ["a", "b"])
        check("low_confidence False", d["low_confidence"] is False)
        check("meta.agent == diagnostic", d["meta"]["agent"] == "diagnostic")
        check("meta.mime captured", d["meta"]["mime"] == "image/jpeg")
        check("meta.filename captured", d["meta"]["filename"] == "leaf.png")

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

    print("\n=== C. Real APIError instances map to the taxonomy ===")
    cases = [
        (401, "auth", False),
        (403, "auth", False),
        (429, "quota", True),
        (400, "input", False),
        (500, "busy", True),
        (503, "busy", True),
        (504, "busy", True),
        (418, "api", True),
    ]
    for code, expected_kind, expected_retry in cases:
        exc = genai_errors.APIError(code, {"error": {"message": "boom", "status": "ERR"}})
        c3 = RecordingClient(raise_exc=exc)
        r3 = diag_mod.DiagnosticAgent(cfg, client=c3).analyze(make_png())
        check(f"HTTP {code} -> {expected_kind}", r3.ok is False and r3.error.kind == expected_kind,
              f"got {r3.error.kind if r3.error else 'ok'}")
        if r3.ok is False:
            check(f"HTTP {code} retryable={expected_retry}", r3.error.retryable is expected_retry)
            check(f"HTTP {code} never leaks key", "AIzaFake" not in (r3.error.message + str(r3.error.detail)))

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
        check("source == gemini", d5["meta"]["source"] == "gemini")
        check("remote title wins", d5["title"] == "Remote title")
        check("remote treatment used", d5["treatments"][0]["detail"] == "X 1 mL/L")
        check("region still Tébessa", "Tébessa" in d5["region"])
        check("disclaimer always present", "Tébessa" in d5["disclaimer"])
        check("regional context always present", len(d5["regional_context"]) > 10)

    print("\n=== F. Advisor falls back to KB when remote returns junk ===")
    c6 = RecordingClient(response_text="not json at all")
    r6 = adv_mod.AdvisorAgent(cfg, client=c6).build_plan(diag)
    check("advisor still succeeds", r6.ok is True)
    if r6.ok:
        check("source == local_kb", r6.data["meta"]["source"] == "local_kb")
        check("fallback_reason recorded", bool(r6.data["meta"]["fallback_reason"]))
        check("KB title used", "Early blight" in r6.data["title"])

    print("\n=== G. Advisor honours advisor_fallback_enabled=False ===")
    cfg_no = Settings(_env_file=None, gemini_api_key="AIzaFakeKeyForShapeTest1234567890",
                      advisor_fallback_enabled=False)
    c7 = RecordingClient(raise_exc=genai_errors.APIError(429, {"error": {"message": "quota"}}))
    r7 = adv_mod.AdvisorAgent(cfg_no, client=c7).build_plan(diag)
    check("fallback disabled -> failure", r7.ok is False)
    if r7.ok is False:
        check("failure kind is quota", r7.error.kind == "quota", f"got {r7.error.kind}")

    print(f"\n{'='*52}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
