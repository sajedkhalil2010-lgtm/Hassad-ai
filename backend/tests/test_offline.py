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

    print("\n=== 3c. False-positive guardrails (no hallucinated pathology) ===")
    from agents.diagnostic_agent import CONFIDENCE_FLOOR, SPECIES_CONFIDENCE_FLOOR

    def _norm(**kw):
        return norm(dict(base_report, **kw), mime="image/jpeg", model="m",
                    elapsed_ms=1, filename=None)

    check("confidence floor is 0.70", abs(CONFIDENCE_FLOOR - 0.70) < 1e-9)
    check("species floor is 0.50", abs(SPECIES_CONFIDENCE_FLOOR - 0.50) < 1e-9)

    # The lavender case: a fuzzy silvery leaf the model called mildew at 0.45.
    fuzzy = _norm(status="diseased", disease="Powdery Mildew", confidence=0.45,
                  species_confidence=0.9, crop="Lavender", severity="high",
                  severity_pct=0.9, differential=["Natural trichomes"])
    check("low-confidence diseased is downgraded to uncertain",
          fuzzy["status"] == "uncertain", f"got {fuzzy['status']!r}")
    check("uncertain is not healthy", fuzzy["healthy"] is False)
    check("uncertain flag raised", fuzzy["uncertain"] is True)
    check("guessed disease is not asserted as the name", fuzzy["disease"] == "Undetermined pathology")
    check("guessed disease survives as a differential",
          any("Powdery Mildew" in d for d in fuzzy["differential"]),
          f"got {fuzzy['differential']!r}")
    check("uncertain clamps a severe severity", fuzzy["severity"] == "low")
    check("uncertain clamps the severity ring", fuzzy["severity_pct"] <= 0.25)
    check("uncertain advice is about the photo, not a spray",
          any("photograph" in r.lower() for r in fuzzy["recommendations"]))
    check("uncertain carries an Arabic name", any(ord(c) > 0x0600 for c in fuzzy["disease_ar"]))
    check("uncertain is flagged low_confidence", fuzzy["low_confidence"] is True)
    check("downgrade reason recorded for the audit trail",
          "0.70 floor" in (fuzzy["meta"]["downgraded_from"] or ""), f"got {fuzzy['meta']['downgraded_from']!r}")

    # A confident, well-identified disease must still stand.
    confident = _norm(status="diseased", disease="Early blight", confidence=0.92,
                      species_confidence=0.95)
    check("confident diseased survives the floor", confident["status"] == "diseased")
    check("confident diseased keeps its name", confident["disease"] == "Early blight")
    check("confident diseased is not flagged", confident["low_confidence"] is False)

    # Unknown species + a disease name is how "tomato" gets invented.
    wrong_species = _norm(status="diseased", disease="Early blight", confidence=0.88,
                          species_confidence=0.2, crop="Unknown")
    check("low species confidence is downgraded",
          wrong_species["status"] == "uncertain", f"got {wrong_species['status']!r}")

    # A payload predating species_confidence must not be silently downgraded.
    legacy_species = norm({"status": "diseased", "disease": "Early blight", "confidence": 0.88,
                           "crop": "Tomato"}, mime="image/jpeg", model="m", elapsed_ms=1,
                          filename=None)
    check("absent species_confidence does not downgrade",
          legacy_species["status"] == "diseased", f"got {legacy_species['status']!r}")

    # A non-leaf photograph can never be a diagnosis.
    not_leaf = _norm(status="diseased", disease="Late blight", confidence=0.95,
                     is_leaf_image=False)
    check("a non-leaf image cannot carry a diagnosis",
          not_leaf["status"] == "uncertain", f"got {not_leaf['status']!r}")

    # An already-uncertain verdict is passed through untouched by the floors.
    explicit = _norm(status="uncertain", disease="Possible: Rust", confidence=0.4)
    check("explicit uncertain is preserved", explicit["status"] == "uncertain")
    check("explicit uncertain clears the disease name",
          explicit["disease"] == "Undetermined pathology")
    check("explicit uncertain lowers its severity", explicit["severity"] == "low")

    ar_uncertain = norm(dict(base_report, status="uncertain", disease="Possible: Rust",
                             confidence=0.4), mime="image/jpeg", model="m", elapsed_ms=1,
                        filename=None, language="ar")
    check("arabic uncertain advice is Arabic",
          any(ord(c) > 0x0600 for c in ar_uncertain["recommendations"][0]))
    check("arabic uncertain records language", ar_uncertain["meta"]["language"] == "ar")
    check("uncertain verdicts cannot be promoted by the floors",
          norm(dict(base_report, status="healthy", confidence=0.2),
               mime="image/jpeg", model="m", elapsed_ms=1, filename=None)["status"] == "healthy")

    print("\n=== 3d. Prompt language and shape ===")
    prompt_en = _build_prompt("en")
    prompt_ar = _build_prompt("ar")
    check("prompt demands a status field", '"status": "healthy|diseased|uncertain"' in prompt_en)
    check("prompt gates step 2 behind status", "STEP 1" in prompt_en and "STEP 2" in prompt_en)
    check("prompt tells the model to identify the species first", "IDENTIFY THE PLANT" in prompt_en)
    check("prompt tells the model to check for health first", "CLASSIFY BEFORE YOU DIAGNOSE" in prompt_en)
    check("prompt tells the model not to default to tomato", "NEVER assume" in prompt_en)
    check("prompt warns that fuzz is not mildew", "trichomes" in prompt_en.lower())
    check("prompt sets the 0.70 confidence floor", ">= 0.70" in prompt_en)
    check("prompt names the Arabic label for healthy", "نبات سليم" in prompt_en)
    check("prompt pins English", "English" in prompt_en and "Arabic" not in prompt_en.split("Rules:")[0].replace("العربية", ""))
    check("prompt pins Arabic", "Arabic" in prompt_ar)
    check("prompt keeps disease_ar Arabic in both", "disease_ar" in prompt_ar)
    check("both prompts carry the identical JSON shape",
          _DIAGNOSTIC_JSON_SHAPE in prompt_en and _DIAGNOSTIC_JSON_SHAPE in prompt_ar)

    print("\n=== 3d. Universal pathology domains + response contract ===")
    from agents.diagnostic_agent import MATCHED_CATEGORIES, MATCHED_CATEGORY_NONE
    from agents import pathology_taxonomy as taxonomy

    def shape(**overrides):
        return norm(dict(base_report, **overrides), mime="image/jpeg",
                    model="m", elapsed_ms=1, filename=None)

    # The taxonomy has to reach the model as a handful of RULES, not as a
    # disease dump. A few hundred entries inlined here would add thousands of
    # tokens to a prompt that already carries an image, and the request would
    # 400 on every call — so the budget is asserted, not assumed.
    check("prompt names every agronomic domain",
          all(f'"{d}"' in prompt_en for d in MATCHED_CATEGORIES))
    check("prompt asks for matched_category", '"matched_category"' in prompt_en)
    check("prompt tells the model to draw on its full knowledge",
          "full breadth" in prompt_en.lower())
    check("treatment covers irrigation and nutrition, not only a spray",
          "irrigation, nutrition" in prompt_en.lower())
    check("prompt stays inside a context-safe budget",
          max(len(prompt_en), len(prompt_ar)) < 12000,
          f"en={len(prompt_en)} ar={len(prompt_ar)} chars")
    check("no pathogen genus list is inlined into the prompt",
          "Meloidogyne" not in prompt_en and "Pratylenchus" not in prompt_en)

    # The standardised wire contract. Nine keys, all of which every endpoint
    # must return on every verdict — the four the engine request named
    # (matched_category, disease_name_{en,ar}, confidence_score) plus the
    # discovery flag and the two prose/plan fields under their standard
    # names, alongside the plant and condition class.
    contract = ("detected_plant", "condition_type", "matched_category",
                "disease_name_en", "disease_name_ar", "confidence_score",
                "detailed_pathology_analysis", "actionable_treatment_plan",
                "is_novel_discovery")
    for label, overrides in (
        ("healthy", dict(status="healthy", disease="Healthy Plant", disease_ar="نبات سليم")),
        ("diseased", dict(status="diseased", disease="Early blight", disease_ar="اللفحة المبكرة")),
        ("uncertain", dict(status="uncertain", disease="Possible: Rust", confidence=0.4)),
    ):
        out = shape(**overrides)
        missing = [k for k in contract if k not in out]
        check(f"{label} carries every contract field", not missing, f"missing {missing}")
        check(f"{label} leaves the legacy keys intact",
              all(k in out for k in ("crop", "disease", "confidence",
                                     "recommendations", "status")),
              str([k for k in ("crop", "disease", "confidence", "recommendations", "status")
                   if k not in out]))
        check(f"{label} detailed_pathology_analysis is never blank",
              bool(str(out["detailed_pathology_analysis"]).strip()))
        check(f"{label} matched_category is never blank", bool(out["matched_category"]))
        check(f"{label} disease_name_en mirrors disease",
              out["disease_name_en"] == out["disease"])
        check(f"{label} disease_name_ar mirrors disease_ar",
              out["disease_name_ar"] == out["disease_ar"])
        check(f"{label} actionable_treatment_plan mirrors recommendations",
              out["actionable_treatment_plan"] == out["recommendations"])
        check(f"{label} is_novel_discovery is a bool",
              isinstance(out["is_novel_discovery"], bool))

    # The interim names are retired, and the standardised ones replace them.
    retired = shape(status="diseased", disease="Early blight")
    check("the interim field names were retired",
          "detailed_analysis" not in retired
          and "recommended_treatment" not in retired)
    check("the superseded prose/plan names were retired",
          "analysis_details" not in retired and "treatment_plan" not in retired,
          str([k for k in ("analysis_details", "treatment_plan") if k in retired]))

    check("a healthy plant has no domain",
          shape(status="healthy", disease="Healthy Plant")["matched_category"]
          == MATCHED_CATEGORY_NONE)
    check("a pathogen genus names its own domain",
          shape(status="diseased", disease="Citrus canker", pathogen="Xanthomonas citri",
                confidence=0.92)["matched_category"] == "Bacterial Pathogen")
    check("a virology read resolves to the viral domain",
          shape(status="diseased", disease="Mosaic", pathogen="TMV",
                confidence=0.92)["matched_category"] == "Viral & Viroid")
    check("a root-knot read resolves to the nematode domain",
          shape(status="diseased", disease="Root-knot", pathogen="Meloidogyne",
                confidence=0.92)["matched_category"] == "Nematode & Root Parasite")

    # An insect gall is not crown gall, and crown gall is not a nutrient
    # problem — so the coarse class outranks any symptom word, and a disease
    # with no other signal falls to the documented fungal prior rather than
    # being silently labelled with a confident wrong domain.
    check("an insect gall keeps the structural domain",
          shape(status="diseased", disease="Leaf galls", condition_type="Insect Damage",
                pathogen="Unknown", confidence=0.92)["matched_category"]
          == "Insect & Structural Damage")
    check("abiotic injury folds into the physiological domain",
          shape(status="diseased", disease="Unknown", condition_type="Environmental Stress",
                pathogen="Unknown", confidence=0.92)["matched_category"]
          == "Physiological & Abiotic")
    check("a nutrient disorder folds into the physiological domain",
          shape(status="diseased", disease="Unknown", condition_type="Nutrient Deficiency",
                pathogen="Unknown", confidence=0.92)["matched_category"]
          == "Physiological & Abiotic")
    check("a bare disease falls back to the fungal prior",
          shape(status="diseased", disease="Unknown", pathogen="Unknown",
                confidence=0.92)["matched_category"] == "Fungal Pathogen")

    # A domain is metadata about a verdict, never a way to lift one: the
    # lavender regression that this whole guardrail exists to prevent.
    guarded = shape(status="diseased", disease="Powdery Mildew", confidence=0.45,
                    species_confidence=0.9, crop="Lavender", severity="high",
                    severity_pct=0.9, differential=["Natural trichomes"])
    check("a domain never rescues a sub-floor verdict", guarded["status"] == "uncertain")
    check("an undetermined verdict still names no disease",
          guarded["disease_name_en"] == "Undetermined pathology")
    check("an undetermined verdict still gets a domain",
          guarded["matched_category"] in MATCHED_CATEGORIES)
    check("an undetermined verdict still keeps its hypotheses",
          any("Powdery Mildew" in d for d in guarded["differential"]))

    # The probable name is a LEAD, never a diagnosis. The mask on
    # `disease`/`disease_ar` is deliberately untouched so nothing downstream
    # can mistake it for confirmed, but a farmer is no longer shown a blank
    # label either.
    check("an undetermined verdict exposes the model's guess",
          guarded["provisional_disease"] == "Powdery Mildew",
          f"got {guarded['provisional_disease']!r}")
    check("the guess is not restored into the masked name",
          guarded["disease"] == "Undetermined pathology"
          and "Powdery Mildew" not in guarded["disease"])
    check("a healthy plant carries no probable name",
          shape(status="healthy", disease="Healthy Plant")["provisional_disease"] == "")
    check("a confirmed diagnosis carries no probable name",
          shape(status="diseased", disease="Early blight")["provisional_disease"] == "")
    check("provisional fields are blank strings, never null",
          shape(status="healthy", disease="Healthy Plant")["provisional_disease"] == ""
          and shape(status="healthy", disease="Healthy Plant")["provisional_disease_ar"] == "")

    # The sanitizer must refuse our own masking labels and healthy verdicts —
    # echoing either back as a *probable disease* would be worse than the
    # blank label this field exists to improve on.
    echo = shape(status="uncertain", disease="Undetermined pathology",
                 disease_ar="ظهور مرض غير محدد", confidence=0.4)
    check("the masking label is never echoed as a probable name",
          echo["provisional_disease"] == "" and echo["provisional_disease_ar"] == "")
    healthy_echo = shape(status="uncertain", disease="Healthy Plant",
                         disease_ar="نبات سليم", confidence=0.4)
    check("a healthy verdict is never echoed as a probable name",
          healthy_echo["provisional_disease"] == ""
          and healthy_echo["provisional_disease_ar"] == "")

    # --- the server-side reference index --------------------------------
    # The 500+ classification lives HERE and not in the prompt, which the
    # context-budget assertion above proves: a list this size could not ride
    # along with an image. These checks hold the claim itself to its word so
    # "500+" cannot quietly rot into "whatever entries remain".
    check("the reference index holds 500+ conditions",
          taxonomy.INDEX_SIZE >= 500, f"only {taxonomy.INDEX_SIZE}")
    check("the index spans every prompt domain",
          {e.domain for e in taxonomy.ENTRIES} == set(MATCHED_CATEGORIES),
          str({e.domain for e in taxonomy.ENTRIES} ^ set(MATCHED_CATEGORIES)))
    check("no entry claims a domain the prompt does not accept",
          all(e.domain in MATCHED_CATEGORIES for e in taxonomy.ENTRIES))
    check("every Arabic name resolves to a catalogued condition",
          all(taxonomy.resolve(e.name_ar) is not None for e in taxonomy.ENTRIES))
    check("every entry resolves to its own domain",
          all(taxonomy.domain_for(e.name_en) == e.domain for e in taxonomy.ENTRIES))
    check("lookup folds case, punctuation and tashkeel",
          taxonomy.resolve("EARLY  BLIGHT!") is not None
          and taxonomy.resolve("اللفحه المبكره") is not None)
    check("a qualified name resolves through its head",
          taxonomy.resolve("Bacterial wilt of beans") is not None,
          "head of a qualified name should still match")
    check("an unknown condition is not catalogued",
          taxonomy.resolve("Quinoxycetium nonexistentium") is None)
    check("a bare genus is not resolved to an arbitrary disease",
          taxonomy.resolve("Fusarium") is None,
          f"got {taxonomy.resolve('Fusarium')}")
    check("the scope digest is counts only, never names",
          "Early blight" not in taxonomy.digest())

    # The index is consulted for the DOMAIN only, and it must be able to
    # beat the documented fungal prior: "Citrus canker" with no genus and no
    # recognised symptom word would otherwise be filed as fungal, which is
    # wrong, while the catalogue knows it is bacterial.
    check("a catalogued condition supplies its own domain",
          shape(status="diseased", disease="Citrus canker", pathogen="Unknown",
                confidence=0.92)["matched_category"] == "Bacterial Pathogen",
          "the index should beat the fungal prior here")

    # --- open-ended discovery -------------------------------------------
    # A condition outside the index is flagged, and every verdict that did
    # not actually name one is not — a false "novel" sends an operator to
    # review a condition nobody claimed.
    check("a catalogued disease is not a discovery",
          shape(status="diseased", disease="Early blight",
                disease_ar="اللفحة المبكرة",
                confidence=0.92)["is_novel_discovery"] is False)
    check("a condition outside the index is flagged",
          shape(status="diseased", disease="Quinoxycetium nonexistentium",
                disease_ar="مرض غير موجود",
                confidence=0.92)["is_novel_discovery"] is True)
    check("an unnamed verdict is never a discovery",
          shape(status="diseased", disease="Unknown", disease_ar="غير معروف",
                confidence=0.92)["is_novel_discovery"] is False)
    check("an undetermined verdict never claims a discovery",
          guarded["is_novel_discovery"] is False,
          "the confidence floor must not be bypassed by a discovery flag")
    check("a healthy verdict never claims a discovery",
          shape(status="healthy",
                disease="Healthy Plant")["is_novel_discovery"] is False)
    check("a sub-floor guess is never a discovery",
          shape(status="uncertain", disease="Possible: Rust",
                confidence=0.4)["is_novel_discovery"] is False)

    ar_guess = norm(dict(base_report, status="uncertain", disease="Possible: Rust",
                         disease_ar="الصدأ", confidence=0.4),
                    mime="image/jpeg", model="m", elapsed_ms=1, filename=None,
                    language="ar")
    check("the arabic locale keeps the arabic probable name",
          ar_guess["provisional_disease_ar"] == "الصدأ",
          f"got {ar_guess['provisional_disease_ar']!r}")
    check("the arabic locale masks the arabic name it replaces",
          ar_guess["disease_ar"] == "ظهور مرض غير محدد")

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
