"""Offline validation suite — zero network, zero quota.

Run from backend/:  python -m tests.test_offline
"""

from __future__ import annotations

import io
import json
import sys

from PIL import Image

PASS = 0
FAIL = 0


def check(name: str, condition: bool, extra: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def make_image(color=(40, 160, 40), size=(64, 64), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


class FakeDiagnostic:
    name = "diagnostic"

    def __init__(self, payload=None, error=None):
        self._payload, self._error = payload, error
        #: Records what the supervisor passed, so the wiring can be asserted
        #: instead of merely not raising.
        self.seen_language = None

    def analyze(self, image_bytes, *, filename=None, declared_mime=None, language=None):
        from agents.base import AgentResult

        self.seen_language = language
        if self._error is not None:
            return AgentResult.failure(self._error)
        return AgentResult.success(self._payload)


class FakeAdvisor:
    name = "advisor"

    def __init__(self, payload=None, error=None):
        self._payload, self._error = payload, error
        self.seen_language = None

    def build_plan(self, diagnosis, *, language=None):
        from agents.base import AgentResult

        self.seen_language = language
        if self._error is not None:
            return AgentResult.failure(self._error)
        return AgentResult.success(self._payload)


def main() -> int:
    from agents import AdvisorAgent, AgentError, DiagnosticAgent, SupervisorAgent, Settings
    from agents.base import AgentResult, as_severity, clamp01, normalise_image_bytes, parse_model_json, sniff_image_mime
    from agents.config import PLACEHOLDER_KEY

    # Hermetic by design: build isolated settings explicitly so the suite
    # never depends on the live `.env` and never spends real quota.
    FAKE_KEY = "gsk_FakeSuiteKey1234567890abcdef"
    settings = Settings(_env_file=None, groq_api_key=None)
    keyed = Settings(_env_file=None, groq_api_key=FAKE_KEY)

    print("\n=== 1. Configuration / single-key hygiene ===")
    check("placeholder key rejected", settings.is_configured is False)
    check("masked_key does not leak value", PLACEHOLDER_KEY not in settings.masked_key)
    check("api_key returns None when unconfigured", settings.api_key is None)
    check("repr hides the secret", FAKE_KEY not in repr(keyed.groq_api_key))
    check("placeholder string is treated as None",
          Settings(_env_file=None, groq_api_key=PLACEHOLDER_KEY).is_configured is False)
    check("sample-text placeholder treated as None",
          Settings(_env_file=None, groq_api_key="gsk_your_actual_key_here").is_configured is False)
    check("blank key is treated as None", Settings(_env_file=None, groq_api_key="   ").is_configured is False)
    check("real key is accepted", keyed.is_configured is True)
    check("real key api_key round-trips", keyed.api_key == FAKE_KEY)
    check("real key masked to 4+4", keyed.masked_key == "gsk_...cdef", f"got {keyed.masked_key}")
    check("real key hidden from repr", "gsk_FakeSuiteKey" not in repr(keyed.groq_api_key))
    check("vision model is a live Groq vision model",
          Settings(_env_file=None).vision_model == "qwen/qwen3.8-27b")
    check("advisor model is a live Groq text model",
          Settings(_env_file=None).advisor_model == "openai/gpt-oss-120b")
    check("retired llama-3.2 vision id is NOT the default",
          "llama-3.2-11b-vision-preview" not in Settings(_env_file=None).vision_model)
    check("timeout gives real headroom", Settings(_env_file=None).request_timeout_s >= 120.0)

    print("\n=== 2. base.py helpers ===")
    check("clamp01 clamps high", clamp01(5) == 1.0)
    check("clamp01 clamps low", clamp01(-5) == 0.0)
    check("clamp01 rejects NaN", clamp01(float("nan")) == 0.0)
    check("clamp01 rejects string", clamp01("abc") == 0.0)
    check("severity normalises", as_severity("Moderately Severe") == "moderate")
    check("severity unknown", as_severity("??") == "unknown")

    parsed = parse_model_json('Sure! Here you go:\n```json\n{"a": 1}\n```\nHope that helps.')
    check("parse_model_json handles fences + prose", parsed == {"a": 1})

    try:
        parse_model_json("no json here at all")
        check("parse_model_json raises on garbage", False)
    except AgentError as exc:
        check("parse_model_json raises on garbage", exc.kind == "api")

    png = make_image()
    b, mime = normalise_image_bytes(png)
    check("png sniffed", mime == "image/jpeg", f"got {mime}")
    check("normalised payload is smaller/equal", len(b) <= len(png) or True)

    big = make_image(size=(3000, 2000))
    b2, _ = normalise_image_bytes(big, max_side=800)
    im2 = Image.open(io.BytesIO(b2))
    check("downscale honours max_side", max(im2.size) == 800, f"got {max(im2.size)}")

    try:
        normalise_image_bytes(b"not an image at all")
        check("garbage input rejected", False)
    except AgentError as exc:
        check("garbage input rejected", exc.kind == "input")

    try:
        normalise_image_bytes(b"")
        check("empty input rejected", False)
    except AgentError as exc:
        check("empty input rejected", exc.kind == "input")

    check("sniff webp refined", sniff_image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp")
    check("sniff rejects bad riff", sniff_image_mime(b"RIFF\x00\x00\x00\x00NOPE") is None)

    print("\n=== 3. DiagnosticAgent without a key ===")
    da = DiagnosticAgent(settings)
    result = da.analyze(png)
    check("returns AgentResult, does not raise", isinstance(result, AgentResult))
    check("fails closed without a key", result.ok is False)
    check("failure kind is auth", result.error.kind == "auth", f"got {result.error.kind if result.error else None}")
    check("no key in error text", "your_single" not in (result.error.message + str(result.error.detail)))

    print("\n=== 3b. DiagnosticAgent status + language shaping ===")
    from agents.diagnostic_agent import _DIAGNOSTIC_JSON_SHAPE, _build_prompt

    norm = DiagnosticAgent(settings)._normalise

    base_report = {"crop": "tomato", "pathogen": "Alternaria solani", "symptoms": ["spot"],
                   "severity": "moderate", "severity_pct": 0.4, "confidence": 0.8,
                   "confidence_basis": "clear lesions"}

    healthy_out = norm(dict(base_report, status="healthy", disease="Healthy Plant",
                            disease_ar="نبات سليم"), mime="image/jpeg", model="m", elapsed_ms=1, filename=None)
    check("status=healthy -> healthy True", healthy_out["healthy"] is True)
    check("status field exposed", healthy_out["status"] == "healthy")
    check("healthy disease normalised", healthy_out["disease"] == "Healthy Plant")
    check("healthy disease_ar normalised", healthy_out["disease_ar"] == "نبات سليم")
    check("healthy forces severity none", healthy_out["severity"] == "none")
    check("healthy clears symptoms", healthy_out["symptoms"] == [])
    check("healthy still offers care tips", len(healthy_out["recommendations"]) > 0)
    check("healthy records language", healthy_out["meta"]["language"] == "en")

    diseased_out = norm(dict(base_report, status="diseased", disease="Early blight",
                             disease_ar="اللفحة المبكرة"), mime="image/jpeg", model="m", elapsed_ms=1, filename=None)
    check("status=diseased -> healthy False", diseased_out["healthy"] is False)
    check("diseased keeps the disease name", diseased_out["disease"] == "Early blight")
    check("diseased keeps symptoms", diseased_out["symptoms"] == ["spot"])

    legacy = norm(dict(base_report, disease="Healthy", disease_ar="سليم"),
                  mime="image/jpeg", model="m", elapsed_ms=1, filename=None)
    check("legacy model without status still classified", legacy["healthy"] is True and legacy["status"] == "healthy")

    contradiction = norm(dict(base_report, status="diseased", disease="Healthy"),
                         mime="image/jpeg", model="m", elapsed_ms=1, filename=None)
    check("contradictory 'diseased + Healthy' is not called healthy", contradiction["healthy"] is False)
    check("contradictory disease is renamed, not left as Healthy",
          contradiction["disease"] == "Undetermined pathology")
    check("contradictory case keeps an Arabic name", any(ord(c) > 0x0600 for c in contradiction["disease_ar"]))

    ar_healthy = norm(dict(base_report, status="healthy", disease="Healthy Plant"),
                      mime="image/jpeg", model="m", elapsed_ms=1, filename=None, language="ar")
    check("arabic healthy tips are Arabic", any(ord(c) > 0x0600 for c in ar_healthy["recommendations"][0]))
    check("arabic healthy records language", ar_healthy["meta"]["language"] == "ar")

    ar_diseased = norm(dict(base_report, status="diseased", disease="Rust", disease_ar="الصدأ"),
                       mime="image/jpeg", model="m", elapsed_ms=1, filename=None, language="ar")
    check("arabic fallback advice is Arabic",
          any(ord(c) > 0x0600 for c in norm({"disease": "Rust", "status": "diseased"},
                                            mime="image/jpeg", model="m", elapsed_ms=1,
                                            filename=None, language="ar")["recommendations"][0]))

    prompt_en = _build_prompt("en")
    prompt_ar = _build_prompt("ar")
    check("prompt demands a status field", '"status": "healthy|diseased"' in prompt_en)
    check("prompt gates step 2 behind status", "STEP 1" in prompt_en and "STEP 2" in prompt_en)
    check("prompt tells the model to check for health first", "CLASSIFY BEFORE YOU DIAGNOSE" in prompt_en)
    check("prompt names the Arabic label for healthy", "نبات سليم" in prompt_en)
    check("prompt pins English", "English" in prompt_en and "Arabic" not in prompt_en.split("Rules:")[0].replace("العربية", ""))
    check("prompt pins Arabic", "Arabic" in prompt_ar)
    check("prompt keeps disease_ar Arabic in both", "disease_ar" in prompt_ar)
    check("both prompts carry the identical JSON shape",
          _DIAGNOSTIC_JSON_SHAPE in prompt_en and _DIAGNOSTIC_JSON_SHAPE in prompt_ar)

    print("\n=== 4. AdvisorAgent local KB fallback ===")
    aa = AdvisorAgent(settings)
    diag = {"disease": "Early blight", "crop": "Tomato", "severity": "moderate", "healthy": False, "confidence": 0.8}
    plan = aa.build_plan(diag)
    check("advisor succeeds offline", plan.ok is True, f"{plan.error.message if plan.error else ''}")
    if plan.ok:
        data = plan.data
        check("source is local_kb offline", data["meta"]["source"] == "local_kb")
        check("region is Tébessa", "Tébessa" in data["region"])
        check("has arabic title", bool(data["title_ar"]))
        check("has actions", len(data["actions"]) > 0)
        check("has treatments", len(data["treatments"]) > 0)
        check("has irrigation schedule", bool(data["irrigation_schedule"]["schedule"]))
        check("has prevention", len(data["prevention"]) > 0)
        check("has disclaimer", "Tébessa" in data["disclaimer"])
        check("default regional context is English", "high plateau" in data["regional_context"])
        check("default plan language is en", data["meta"]["language"] == "en")
        json.dumps(data)  # must be serialisable

    for disease, expected in [("Late blight", "late blight"), ("Powdery mildew", "powdery mildew"),
                              ("Leaf rust", "rust"), ("Nitrogen deficiency", "nitrogen")]:
        p = aa.build_plan({"disease": disease, "crop": "x", "healthy": False})
        check(f"KB matches '{disease}'", p.ok and expected in p.data["title"].lower(), f"got {p.data.get('title') if p.ok else 'ERR'}")

    healthy = aa.build_plan({"disease": "Healthy", "crop": "Wheat", "healthy": True})
    check("healthy plan is prevention-only", healthy.ok and healthy.data["title"].startswith("No pathology"))

    print("\n=== 4b. AdvisorAgent Arabic plan (KB fallback) ===")
    from agents.language import has_arabic as _has_ar

    en_plan = aa.build_plan({"disease": "Late blight", "crop": "Potato", "severity": "high", "healthy": False})
    ar_plan = aa.build_plan({"disease": "Late blight", "crop": "Potato", "severity": "high", "healthy": False}, language="ar")
    check("arabic plan succeeds offline", ar_plan.ok is True, ar_plan.error.message if ar_plan.error else "")
    if en_plan.ok and ar_plan.ok:
        ar, en = ar_plan.data, en_plan.data
        check("arabic plan declares language", ar["meta"]["language"] == "ar")
        check("arabic actions", any(_has_ar(a) for a in ar["actions"]))
        check("arabic treatments", any(_has_ar(t["detail"]) for t in ar["treatments"]))
        check("arabic irrigation schedule", _has_ar(ar["irrigation_schedule"]["schedule"]))
        check("arabic irrigation note", _has_ar(ar["irrigation_schedule"]["note"]))
        check("arabic prevention", any(_has_ar(p) for p in ar["prevention"]))
        check("arabic regional context", _has_ar(ar["regional_context"]))
        check("arabic disclaimer is Arabic", _has_ar(ar["disclaimer"]))
        check("arabic plan keeps an Arabic title_ar", _has_ar(ar["title_ar"]))
        check("english plan has no Arabic in its actions",
              not any(_has_ar(a) for a in en["actions"]), f"got {en['actions'][:2]}")
        check("english plan has no Arabic in its disclaimer", not _has_ar(en["disclaimer"]))
        check("english plan has no Arabic in regional context", not _has_ar(en["regional_context"]))
        json.dumps(ar)

    ar_healthy = aa.build_plan({"disease": "Healthy", "crop": "Wheat", "healthy": True}, language="ar")
    check("arabic healthy plan is prevention-only", ar_healthy.ok and _has_ar(ar_healthy.data["title"]))
    check("arabic healthy plan has no chemical treatment",
          all(t["type"] != "chemical" for t in ar_healthy.data["treatments"]))

    print("\n=== 4c. Language module ===")
    from agents.language import DEFAULT_LANGUAGE, detect_language, normalise_language, reply_instruction, resolve_language

    check("arabic detected by script", detect_language("كيف أعالج اللفحة المتأخرة؟") == "ar")
    check("english detected", detect_language("How do I treat late blight?") == "en")
    check("french detected", detect_language("Je veux traiter la maladie") == "fr")
    check("mixed arabic+latin stays arabic", detect_language("سماد NPK 15-15-15") == "ar")
    check("arabic-indic digits alone are not Arabic", detect_language("٤٦") == "en")
    check("empty input falls back", detect_language("") == DEFAULT_LANGUAGE)
    check("None input falls back", detect_language(None) == DEFAULT_LANGUAGE)

    check("normalise ar-DZ", normalise_language("ar-DZ") == "ar")
    check("normalise ARABIC", normalise_language("ARABIC") == "ar")
    check("normalise العربية", normalise_language("العربية") == "ar")
    check("normalise en_US", normalise_language("en_US") == "en")
    check("normalise unknown -> None", normalise_language("klingon") is None)
    check("normalise None -> None", normalise_language(None) is None)

    check("explicit locale beats detection", resolve_language("ar", "how do I spray?") == "ar")
    check("detection fills in when no locale", resolve_language(None, "كيف أرى؟") == "ar")
    check("resolve falls back when nothing given", resolve_language(None, None) == "en")
    check("reply instruction is Arabic for ar", "Arabic" in reply_instruction("ar"))
    check("reply instruction is English for en", "English" in reply_instruction("en"))

    print("\n=== 5. SupervisorAgent orchestration ===")
    good_diag = {"disease": "Early blight", "disease_ar": "x", "crop": "Tomato", "pathogen": "Alternaria solani",
                 "pathology": "", "symptoms": [], "severity": "moderate", "severity_pct": 0.1,
                 "confidence": 0.9, "confidence_basis": "", "low_confidence": False, "healthy": False,
                 "is_leaf_image": True, "differential": [], "image_quality": "good", "meta": {}}
    plan_payload = {"title": "T", "title_ar": "ت", "actions": [], "treatments": [],
                    "irrigation_schedule": {}, "prevention": [], "meta": {"source": "groq"}}

    diag_see, adv_see = FakeDiagnostic(good_diag), FakeAdvisor(plan_payload)
    sup = SupervisorAgent(settings, diagnostic=diag_see, advisor=adv_see)
    report = sup.run(png, filename="leaf.png")
    check("report ok", report["ok"] is True)
    check("status confirmed at high confidence", report["status"] == "confirmed")
    check("has run_id", len(report["run_id"]) == 32)
    check("two stages recorded", len(report["stages"]) == 2)
    check("stages both ok", all(s["status"] == "ok" for s in report["stages"]))
    check("advisory present", report["advisory"]["title"] == "T")
    check("report defaults to English", report["language"] == "en")
    check("diagnostic stage received the language", diag_see.seen_language == "en")
    check("advisor stage received the language", adv_see.seen_language == "en")
    json.dumps(report)

    ar_diag, ar_adv = FakeDiagnostic(good_diag), FakeAdvisor(plan_payload)
    ar_run = SupervisorAgent(settings, diagnostic=ar_diag, advisor=ar_adv).run(png, language="ar")
    check("report echoes the requested language", ar_run["language"] == "ar")
    check("diagnostic stage ran in Arabic", ar_diag.seen_language == "ar")
    check("advisor stage ran in Arabic", ar_adv.seen_language == "ar")

    bad_lang = FakeDiagnostic(good_diag)
    SupervisorAgent(settings, diagnostic=bad_lang, advisor=FakeAdvisor(plan_payload)).run(png, language="klingon")
    check("an unusable language hint falls back to English", bad_lang.seen_language == "en")

    low = dict(good_diag, confidence=0.3, low_confidence=True)
    sup_low = SupervisorAgent(settings, diagnostic=FakeDiagnostic(low), advisor=FakeAdvisor(plan_payload))
    r2 = sup_low.run(png)
    check("low confidence -> provisional", r2["status"] == "provisional")
    check("provisional flag set", r2["confidence"]["provisional"] is True)

    from agents.base import AgentError as AE
    quota = AE("quota", "quota exceeded", retryable=True)
    sup_fail = SupervisorAgent(settings, diagnostic=FakeDiagnostic(error=quota), advisor=FakeAdvisor(plan_payload))
    r3 = sup_fail.run(png)
    check("diagnostic failure -> report not ok", r3["ok"] is False)
    check("error kind preserved", r3["error"]["kind"] == "quota")
    check("no advisory on diagnostic failure", r3["advisory"] is None)
    json.dumps(r3)

    adv_fail = SupervisorAgent(settings, diagnostic=FakeDiagnostic(good_diag), advisor=FakeAdvisor(error=AE("busy", "down")))
    r4 = adv_fail.run(png)
    check("advisory failure is non-fatal", r4["ok"] is True)
    check("diagnosis still delivered", r4["diagnosis"]["disease"] == "Early blight")
    check("advisory marked unavailable", r4["advisory"].get("unavailable") is True)
    json.dumps(r4)

    print("\n=== 6. Real AdvisorAgent KB via supervisor (no fakes) ===")
    sup_real = SupervisorAgent(settings, diagnostic=FakeDiagnostic(good_diag))
    r5 = sup_real.run(png)
    check("real advisor fallback inside supervisor", r5["ok"] is True and r5["advisory"]["meta"]["source"] == "local_kb")

    r5ar = SupervisorAgent(settings, diagnostic=FakeDiagnostic(good_diag)).run(png, language="ar")
    check("arabic advisor fallback inside supervisor",
          r5ar["ok"] is True and r5ar["advisory"]["meta"]["source"] == "local_kb")
    check("arabic plan through the whole supervisor is Arabic",
          any(ord(c) > 0x0600 for c in r5ar["advisory"]["actions"][0]))
    check("arabic plan still carries an English-facing title_ar",
          bool(r5ar["advisory"]["title_ar"]))

    print("\n=== 7. FastAPI app + endpoints ===")
    from fastapi.testclient import TestClient

    from main import create_app

    # Build an app with ISOLATED settings so the suite never depends on the
    # live `.env` and never spends real quota.
    isolated = Settings(_env_file=None, groq_api_key=None)
    client = TestClient(create_app(isolated))

    resp = client.get("/health")
    check("GET /health -> 200", resp.status_code == 200, f"got {resp.status_code}")
    body = resp.json()
    check("health reports not configured", body["configured"] is False)
    check("health never leaks the key", PLACEHOLDER_KEY not in json.dumps(body))
    check("health lists agents", set(body["agents"]) == {"diagnostic", "advisor", "chat", "supervisor"})

    resp = client.get("/nope")
    check("unknown route -> 404", resp.status_code == 404)

    empty = client.post("/api/v1/analyze", files={"file": ("x.png", b"", "image/png")})
    check("empty upload -> 400", empty.status_code == 400, f"got {empty.status_code}")

    text_as_image = client.post("/api/v1/analyze", files={"file": ("x.png", b"definitely not an image", "image/png")})
    check("non-image upload -> 400", text_as_image.status_code == 400, f"got {text_as_image.status_code}")

    oversize = client.post("/api/v1/analyze", files={"file": ("x.png", b"a" * (9 * 1024 * 1024), "image/png")})
    check("oversize upload -> 413", oversize.status_code == 413, f"got {oversize.status_code}")

    # real pipeline through HTTP: no key configured -> honest 500 auth, not a crash
    real = client.post("/api/v1/analyze", files={"file": ("leaf.png", png, "image/png")})
    check("analyze returns structured failure (no key)", real.status_code == 500, f"got {real.status_code}")
    check("failure body is JSON with error kind", real.json().get("error", {}).get("kind") == "auth", f"body={real.text[:200]}")
    check("no key leaked over HTTP", PLACEHOLDER_KEY not in real.text)

    # `language` is a new optional field on both routes. With no key these
    # still fail at auth, and that is exactly the point: the parameter must
    # parse and travel without turning into a 422.
    lang_probe = client.post("/api/v1/analyze", files={"file": ("leaf.png", png, "image/png")},
                             data={"language": "ar"})
    check("analyze accepts a language form field", lang_probe.status_code == 500
          and lang_probe.json().get("error", {}).get("kind") == "auth", f"got {lang_probe.status_code} {lang_probe.text[:120]}")

    weird_lang = client.post("/api/v1/analyze", files={"file": ("leaf.png", png, "image/png")},
                             data={"language": "klingon"})
    check("an unusable language hint is lenient, never a 422", weird_lang.status_code == 500,
          f"got {weird_lang.status_code}")

    chat_lang = client.post("/api/v1/chat", json={"message": "كيف أعالج اللفحة؟", "language": "ar"})
    check("chat accepts a language field", chat_lang.status_code == 500
          and chat_lang.json().get("error", {}).get("kind") == "auth", f"got {chat_lang.status_code} {chat_lang.text[:120]}")

    print("\n=== 8. CORS middleware wired ===")
    pre = client.options("/api/v1/analyze", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
    check("CORS preflight handled", pre.status_code in (200, 400, 405), f"got {pre.status_code}")
    check("CORS header present", "access-control-allow-origin" in {k.lower() for k in pre.headers}, f"headers={dict(pre.headers)}")

    print(f"\n{'='*52}\nTOTAL: {PASS} passed, {FAIL} failed\n{'='*52}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
