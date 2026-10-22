"""DiagnosticAgent — visual pathology of a single crop-leaf image.

Uses the project's **one and only** Groq API key through the shared client
built in :mod:`agents.config`. Every failure mode is classified into an
:class:`~agents.base.AgentError` so the supervisor can decide what to do;
nothing here ever raises a bare exception to the caller.

Vision call shape (Groq OpenAI-compatible chat completions)::

    client.chat.completions.create(
        model=vision_model,
        messages=[{role: user, content: [text, image_url(data:image/jpeg;base64,...)]}],
        response_format={"type": "json_object"},
    )
"""

from __future__ import annotations

import base64
import time
from typing import Any

from groq import APIError as GroqAPIError

from .base import (
    AgentError,
    AgentResult,
    as_dict,
    as_severity,
    as_str,
    as_str_list,
    clamp01,
    classify_http_error,
    normalise_image_bytes,
    parse_model_json,
    run_with_output_retry,
    run_with_quota_retry,
)
from .config import Settings, get_settings
from .language import (
    DEFAULT_LANGUAGE,
    LANG_NAME,
    normalise_language,
    reply_instruction,
)

#: Structured-output contract handed to the model. Keeping it inline (rather
#: than a remote schema) makes the shape auditable and stable. The keys the
#: frontend depends on — status / disease / confidence / severity / symptoms /
#: recommendations — are all part of this contract.
#:
#: ``status`` is the addition this revision is built around: a single field
#: that answers the question a farmer actually has ("is this plant sick?")
#: before any disease name is argued about.
_DIAGNOSTIC_JSON_SHAPE = """\
{
  "is_leaf_image": true,
  "crop": "tomato",
  "crop_ar": "طماطم",
  "species_confidence": 0.0,
  "status": "healthy|diseased|uncertain",
  "disease": "Early blight",
  "disease_ar": "اللفحة المبكرة",
  "pathogen": "Alternaria solani",
  "pathology": "short paragraph on how the pathogen acts on the tissue",
  "symptoms": ["concise symptom", "..."],
  "severity": "none|low|moderate|high|critical",
  "severity_pct": 0.0,
  "confidence": 0.0,
  "confidence_basis": "why this confidence and not higher",
  "recommendations": ["immediate action the farmer should take", "..."],
  "differential": ["plausible alternative", "..."],
  "image_quality": "good|usable|poor"
}"""

#: The full instruction. Assembled from the shape plus a language directive
#: by :func:`_build_prompt`, because the JSON braces below make this
#: unusable as a Python f-string without doubling every one of them.
_PROMPT_TEMPLATE = """\
You are a plant-pathologist AI examining ONE crop-leaf photograph.

Work in THREE steps, in this order.

STEP 1 - IDENTIFY THE PLANT (this gates everything else).
Name the plant species you actually see in "crop" (and its script twin in
"crop_ar"), and record how sure you are in "species_confidence" (float 0-1).
Judge from leaf shape, margin, venation, arrangement and surface texture.
  - Do NOT assume the plant is a crop you were told about, and NEVER assume
    it is tomato. Lavender, sage, olive, mint, basil, rose, cactus and most
    ornamentals are not tomato, and a leaf that is not a Solanaceous crop
    cannot be given a tomato disease.
  - If you cannot identify the plant, say so in "crop" and use your best
    guess; do not invent a species to fill the field.

STEP 2 - CLASSIFY BEFORE YOU DIAGNOSE (this gates step 3).
Decide whether the leaf is healthy or pathological. "Healthy" means ZERO
lesions, ZERO abnormal discolouration, ZERO fungal or bacterial growth, and
ZERO pest damage. Natural variation in colour and shape is not disease.
NATURAL SURFACE FEATURES ARE NOT SYMPTOMS. Trichomes (fine hairs/fuzz), a
silvery or grey felty sheen, a white waxy bloom, dust, pollen, natural
variegation, cultivar colour, age-related yellowing of one lower leaf,
mechanical tears and flash glare are all NORMAL tissue or NORMAL photography.
Lavender, sage, mint, olive and many ornamentals are naturally hairy or
silvery - that fuzz is not powdery mildew, and pale grey-green foliage is not
a nutrient deficiency. Only discrete lesions with defined borders, spreading
necrosis, pustules, or growth sitting ON the surface count as pathological.
  - HEALTHY -> "status" = "healthy", "disease" = "Healthy Plant",
    "disease_ar" = "نبات سليم", "pathogen" = "None", "severity" = "none",
    "severity_pct" = 0.0, "symptoms" = [], "differential" = [], and make
    "recommendations" purely care and prevention advice (scouting, balanced
    feeding, irrigation hygiene).
  - UNCERTAIN -> "status" = "uncertain" when the image is blurred, glare-hit,
    too dark, taken at a bad angle, not clearly a leaf, or when the features
    you see could plausibly be natural. Fill "disease" with your best guess
    prefixed by "Possible: " and lower "confidence".
  - PATHOLOGICAL -> "status" = "diseased" and continue to step 3.
Do NOT invent a disease in order to be helpful. A wrong disease name sends a
farmer to spray something their healthy crop did not need. Reporting "healthy"
or "uncertain" when you are not sure is the CORRECT answer, not a failure.

STEP 3 - IDENTIFY (only when "status" is "diseased").
Name the single most likely condition and ground it in what is actually
visible in this image. Distinguish between the patterns that separate the
common look-alikes:
  - rust -> raised orange-brown pustules, often ringed by chlorosis
  - blight -> expanding necrotic lesions; early blight shows concentric
    "target-board" rings, late blight shows water-soaked dark patches with
    pale haloes and can be followed by white fuzzy growth underneath
  - powdery / downy mildew -> discrete white-grey COLONIES growing on the
    surface, powdery spreading between veins, downy angular and confined by
    veins. An evenly fuzzy or silvery leaf is NOT mildew.
  - pest damage -> chewing, mines, stippling, or frass; say so rather than
    calling it a fungus
  - nutrient deficiency -> a symmetric pattern (interveinal chlorosis,
    marginal scorch, uniform yellowing) with no expanding lesion edge
State the primary symptoms you can see, list at least one plausible
alternative in "differential", and give a concrete ordered treatment
sequence in "recommendations".

CONFIDENCE RULE - apply it in step 2, not after.
"confidence" is how sure you are of your FINAL "status". A disease may only be
reported at "confidence" >= 0.70. If you are less than 0.70 sure that the leaf
is diseased, you MUST output "status" = "uncertain" instead of naming a
severe disease. Severity does not raise confidence: a dramatic-looking leaf
you cannot clearly identify is still "uncertain".

Return ONLY a JSON object with EXACTLY this shape (no prose, no markdown fences):

__SCHEMA__

__LANGUAGE__

Rules:
- "status" is EXACTLY "healthy", "diseased" or "uncertain" — no other value,
  no blanks. Default to "uncertain" rather than guessing.
- "status" = "healthy" only when you have positively confirmed a healthy
  leaf, and never as a way of being polite about a poor photo - a poor photo
  is "uncertain".
- "confidence" and "severity_pct" and "species_confidence" are floats in [0,1].
- Be honest: if the image is blurry, over-exposed or not a leaf, lower
  "confidence" and say so in "confidence_basis"; still fill every field.
- "crop" and "disease" are ALWAYS in English and "crop_ar" and "disease_ar"
  are ALWAYS natural Arabic — all four, in every response, whichever language
  you are writing the rest in.
- "pathology", "symptoms", "confidence_basis", "differential" and
  "recommendations" must be written in __LANG_NAME__, because that is the
  language the farmer is reading.
- "recommendations" must always be a non-empty array of concrete steps.
"""


def _build_prompt(language: str) -> str:
    """Assemble the vision instruction for a given response language.

    Splitting this out is what lets one schema serve both locales without
    maintaining two copies of the rules that must not drift apart.
    """
    return _PROMPT_TEMPLATE.replace("__SCHEMA__", _DIAGNOSTIC_JSON_SHAPE).replace(
        "__LANGUAGE__", reply_instruction(language)
    ).replace("__LANG_NAME__", LANG_NAME.get(language, LANG_NAME[DEFAULT_LANGUAGE]))


#: Disease labels that really mean "nothing is wrong". Used only as the
#: fallback when a model omits ``status``, and again to catch the contradictory
#: case where ``status`` says diseased but this is the only label available.
_HEALTHY_DISEASE_NAMES = {
    "healthy",
    "healthy plant",
    "healthy leaf",
    "no disease",
    "no pathology",
    "normal",
    "not diseased",
    "free of disease",
}

#: Care and prevention advice for a confirmed-healthy leaf. The model writes
#: the real answer; this is the last-resort pair so a healthy plant is never
#: handed a treatment schedule, in either language.
_HEALTHY_TIPS: dict[str, list[str]] = {
    "en": [
        "No treatment required - continue the current programme.",
        "Scout weekly: catching an outbreak early is far cheaper than curing one.",
        "Keep irrigation on the soil rather than the foliage; wet leaves are where infection starts.",
        "Remove and bin fallen or pruned material, which is where many pathogens overwinter.",
    ],
    "ar": [
        "لا حاجة إلى علاج - واصلي البرنامج الحالي كما هو.",
        "افحص المحاصيل أسبوعيًا: اكتشاف الإصابة مبكرًا أرخص بكثير من علاجها.",
        "وجّه الري إلى التربة لا إلى الأوراق؛ الأوراق المبللة هي حيث تبدأ العدوى.",
        "أزل المواد الساقطة والمقطوعة واحرقها، فهي المكان الذي تبيّت فيه كثير من الممرضات.",
    ],
}

#: Last-resort treatment line when the model returned no recommendations at
#: all. Pointing at the regional extension service is the honest answer when
#: we cannot name a disease ourselves.
_TREATMENT_FALLBACK_EN = [
    "Consult the DPA of Tébessa before applying any phytosanitary product.",
]
_TREATMENT_FALLBACK_AR = [
    "استشر المديرية الإقليمية للزراعية بتبسة قبل استعمال أي مبيد أو منتج فلاحي.",
]

#: Confidence below this is flagged for the supervisor to treat cautiously.
LOW_CONFIDENCE_THRESHOLD = 0.55


#: Confidence below which a "diseased" verdict is not allowed to stand. The
#: prompt asks the model to self-apply it, but a prompt is a request, not a
#: guarantee, so the floor is re-applied here: a fuzzy silvery leaf the model
#: called "powdery mildew" at 0.45 confidence must not reach a farmer as a
#: severe diagnosis. This is deliberately stricter than
#: :data:`LOW_CONFIDENCE_THRESHOLD`, which only flags a verdict for caution
#: and leaves it standing.
CONFIDENCE_FLOOR = 0.70

#: How confidently the species itself must be named before a pathological
#: verdict built on that species can stand. Below this the read is uncertain,
#: because treating a lavender leaf as a tomato is how mildew gets invented.
SPECIES_CONFIDENCE_FLOOR = 0.50

#: Labels for "we could not confirm a disease", in both scripts. The UI shows
#: them side by side, and an uncertain verdict must read as uncertain in
#: Arabic too - not as an English word stranded next to Arabic advice.
_UNCERTAIN_LABEL_EN = "Undetermined pathology"
_UNCERTAIN_LABEL_AR = "ظهور مرض غير محدد"


#: What an uncertain verdict actually needs: a better photo, not a spray
#: schedule. Both languages are supplied locally so the KB fallback stays
#: usable offline, exactly like the healthy-care tips above.
_UNCERTAIN_TIPS: dict[str, list[str]] = {
    "en": [
        "Re-photograph the leaf: flat, in focus, in daylight, filling the frame, with no glare.",
        "Include a second photo of the leaf underside - many pests and fungi only show there.",
        "Do not spray anything yet. Treat only once a disease has actually been confirmed.",
        "Check the rest of the plant now and re-run the analysis if symptoms spread.",
    ],
    "ar": [
        "أعد التقاط صورة للورقة: مسطّحة، حادّة التركيز، بضوء النهار، ملء الإطار، وبدون انعكاس.",
        "أضف صورة ثانية لظهر الورقة؛ فكثير من الآفات والأمراض الفطرية لا تظهر إلا من هناك.",
        "لا ترشّ شيئًا بعد. العالِج فقط بعد التأكد الفعلي من وجود مرض.",
        "افحص باقي النبات الآن وأعد التحليل إذا انتشرت الأعراض.",
    ],
}


class DiagnosticAgent:
    """Runs a single, schema-enforced vision pass over an uploaded leaf image."""

    name = "diagnostic"

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    # -- infrastructure ----------------------------------------------------

    def _get_client(self) -> Any:
        """Build (once) the single-key Groq client.

        Raises ``AgentError(kind="auth")`` when no real key is configured, so
        an unconfigured deployment fails loudly and *immediately* rather than
        sending an anonymous request.

        ``max_retries=0`` is deliberate: the Groq SDK retries internally
        (default 2), and stacking that under our own :func:`run_with_quota_retry`
        would multiply into 3x3 calls per analysis.
        """
        if self._client is not None:
            return self._client

        key = self.settings.api_key
        if not key:
            raise AgentError(
                "auth",
                "GROQ_API_KEY is not configured. Set it in the project-root .env file.",
                retryable=False,
            )

        try:
            from groq import Groq

            self._client = Groq(
                api_key=key,
                timeout=max(5.0, self.settings.request_timeout_s),
                max_retries=0,
            )
        except AgentError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise AgentError("auth", "Failed to initialise the Groq client.", detail=str(exc)[:160], retryable=False) from exc
        return self._client

    @staticmethod
    def _classify_sdk_error(exc: BaseException) -> AgentError:
        """Delegate to the shared classifier in ``base``.

        ``media=True`` is the only thing that permits a 400 to be blamed on
        the uploaded bytes — and only when the upstream message actually
        names the image. Every other 400 stays retryable, because Groq
        emits 400 for transient upstream faults as well.
        """
        return classify_http_error(
            exc,
            media=True,
            model_not_found=(
                f"Vision model not found on Groq ({type(exc).__name__}). "
                "Update `vision_model` in agents/config.py."
            ),
        )

    # -- public API --------------------------------------------------------

    def analyze(
        self,
        image_bytes: bytes,
        *,
        filename: str | None = None,
        declared_mime: str | None = None,
        language: str | None = None,
    ) -> AgentResult:
        """Diagnose one leaf image. Never raises — always returns an AgentResult.

        ``language`` pins the language of the narrative fields (``pathology``,
        ``symptoms``, ``confidence_basis``, ``differential``, ``recommendations``).
        It defaults to English, which is what the API has always returned for a
        bare call; the app sends the current locale so an Arabic farmer gets
        Arabic prose. ``disease`` / ``disease_ar`` are always both filled in
        regardless, so the caller can flip the display language afterwards.
        """
        lang = normalise_language(language) or DEFAULT_LANGUAGE
        started = time.perf_counter()

        try:
            payload, mime = normalise_image_bytes(image_bytes, self.settings.max_image_side, declared_mime)
        except AgentError as exc:
            return AgentResult.failure(exc)

        try:
            client = self._get_client()
        except AgentError as exc:
            return AgentResult.failure(exc)

        # Groq takes images as a data URI inside an OpenAI-style message.
        # normalise_image_bytes already re-encodes to JPEG, so the mime here
        # is always image/jpeg.
        image_b64 = base64.b64encode(payload).decode("ascii")
        data_uri = f"data:{mime};base64,{image_b64}"

        def _generate() -> Any:
            return client.chat.completions.create(
                model=self.settings.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _build_prompt(lang)},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
                temperature=self.settings.temperature,
                max_completion_tokens=self.settings.max_output_tokens,
                response_format={"type": "json_object"},
            )

        try:
            response = run_with_output_retry(
                lambda: run_with_quota_retry(
                    _generate,
                    attempts=self.settings.quota_retry_attempts,
                    base_delay_s=self.settings.quota_retry_base_delay_s,
                ),
                attempts=self.settings.output_retry_attempts,
                base_delay_s=self.settings.output_retry_base_delay_s,
            )
        except GroqAPIError as exc:
            return AgentResult.failure(self._classify_sdk_error(exc))
        except AgentError as exc:
            return AgentResult.failure(exc)
        except Exception as exc:  # network / timeout / SDK-internal
            return AgentResult.failure(
                AgentError("busy", "The vision request timed out or could not reach Groq.", detail=str(exc)[:160], retryable=True)
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        try:
            report = parse_model_json(self._extract_text(response))
        except AgentError as exc:
            return AgentResult.failure(exc)

        return AgentResult.success(self._normalise(report, mime=mime, model=self.settings.vision_model, elapsed_ms=elapsed_ms, filename=filename, language=lang))

    @staticmethod
    def _extract_text(response: Any) -> str | None:
        """Pull the assistant text out of a chat-completion object.

        Duck-typed on purpose so tests can hand in a plain stub.
        """
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        if message is None:
            return None
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        # Some responses deliver content as a list of typed parts.
        if isinstance(content, list):
            for part in content:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    return text
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    return part["text"]
        return None

    # -- output shaping ----------------------------------------------------

    def _normalise(
        self,
        report: dict[str, Any],
        *,
        mime: str,
        model: str,
        elapsed_ms: int,
        filename: str | None,
        language: str = DEFAULT_LANGUAGE,
    ) -> dict[str, Any]:
        """Coerce untrusted model output into a stable, typed shape.

        ``status`` is the authority for the healthy/pathological split, and it
        now has three values: ``healthy``, ``diseased`` and ``uncertain``. The
        model is instructed to emit it directly, so the old heuristic — a
        string match on ``disease`` — now only runs as a fallback for a model
        that predates the field. That order matters: a classifier asked "is
        this healthy?" should not have its answer overridden by the name it
        happened to give afterwards.

        A ``diseased`` verdict can be *downgraded* to ``uncertain`` here, but
        never the reverse. The reasons are all in the block below; they exist
        because the cost of a false "diseased" is a farmer spraying a healthy
        crop, which is strictly worse than asking them for a better photo.
        """
        report = as_dict(report)
        symptoms = as_str_list(report.get("symptoms"))
        differential = as_str_list(report.get("differential"))
        recommendations = as_str_list(report.get("recommendations"))

        confidence = clamp01(report.get("confidence"), 0.0)
        severity_pct = clamp01(report.get("severity_pct"), 0.0)
        severity = as_severity(report.get("severity"))

        disease = as_str(report.get("disease"), "Unknown")
        crop = as_str(report.get("crop"), "Unknown")
        crop_ar = as_str(report.get("crop_ar"), "")
        species_confidence = clamp01(report.get("species_confidence"), 0.0)

        status = as_str(report.get("status"), "").strip().lower()
        if status not in {"healthy", "diseased", "uncertain"}:
            # Fallback for a model that answered in the pre-`status` shape.
            status = "healthy" if disease.lower() in _HEALTHY_DISEASE_NAMES else "diseased"

        # --- hard guardrails against a hallucinated pathology -------------
        # The prompt asks the model to self-apply every one of these; none of
        # them can be trusted to a request, because the failure they prevent is
        # a farmer spraying a healthy plant. So each is re-checked here, and
        # each can only ever downgrade "diseased" -> "uncertain", never the
        # other way: we never promote a verdict the model did not stand behind.
        downgrade = None
        if status == "diseased":
            if not bool(report.get("is_leaf_image", True)):
                downgrade = "not a verifiable leaf"
            elif disease.lower() in _HEALTHY_DISEASE_NAMES:
                # `status` says sick but the only label we have says healthy.
                downgrade = "contradictory healthy label"
            elif confidence < CONFIDENCE_FLOOR:
                downgrade = f"confidence {confidence:.2f} below the {CONFIDENCE_FLOOR:.2f} floor"
            elif "species_confidence" in report and species_confidence < SPECIES_CONFIDENCE_FLOOR:
                # Only charged when the model actually answered. A payload
                # from before this field existed is silent, not uncertain, and
                # silence must not silently downgrade a real diagnosis.
                downgrade = f"species confidence {species_confidence:.2f} below the floor"
        if downgrade is not None:
            status = "uncertain"
            # The model's own guess is kept as a differential rather than
            # deleted: it is a real hypothesis, just not one we will assert.
            guess = as_str(report.get("disease"), "").strip()
            if guess and guess.lower() not in _HEALTHY_DISEASE_NAMES:
                differential = [f"Possible: {guess}"] + [
                    d for d in differential if d.lower() != guess.lower()
                ]

        healthy = status == "healthy"
        uncertain = status == "uncertain"

        if healthy:
            # A confirmed-healthy leaf must read as one in both scripts, not
            # as "Healthy"/"الصحة" or whatever phrasing the model chose.
            disease = "Healthy Plant"
            disease_ar = "نبات سليم"
            pathogen = "None"
            severity = "none"
            severity_pct = 0.0
            symptoms = []
            differential = []
            recommendations = recommendations or _HEALTHY_TIPS[language][:2]
        elif uncertain:
            # An undetermined verdict must not look like a diagnosis: no
            # disease name, no severity ring worth alarming over, and advice
            # about the photograph rather than about a chemical.
            disease = _UNCERTAIN_LABEL_EN
            disease_ar = _UNCERTAIN_LABEL_AR
            pathogen = as_str(report.get("pathogen"), "Unknown")
            severity = "low"
            severity_pct = min(severity_pct, 0.25)
            recommendations = _UNCERTAIN_TIPS[language]
        else:
            disease_ar = as_str(report.get("disease_ar"), "غير معروف")
            pathogen = as_str(report.get("pathogen"), "Unknown")
            if not recommendations:
                recommendations = (
                    _TREATMENT_FALLBACK_AR
                    if language == "ar"
                    else _TREATMENT_FALLBACK_EN
                )

        quality = as_str(report.get("image_quality"), "usable").lower()
        if quality not in {"good", "usable", "poor"}:
            quality = "usable"

        return {
            "is_leaf_image": bool(report.get("is_leaf_image", True)),
            "status": status,
            "healthy": healthy,
            "uncertain": uncertain,
            "crop": crop,
            "crop_ar": crop_ar,
            "species_confidence": species_confidence,
            "disease": disease,
            "disease_ar": disease_ar,
            "pathogen": pathogen,
            "pathology": as_str(report.get("pathology"), ""),
            "symptoms": symptoms,
            "severity": severity,
            "severity_pct": severity_pct,
            "confidence": confidence,
            "confidence_basis": as_str(report.get("confidence_basis"), ""),
            # An undetermined verdict is by definition not a confident one, so
            # it flags itself for the supervisor's "provisional" status even
            # when the model reported a high number for the guess it made.
            "low_confidence": uncertain or confidence < LOW_CONFIDENCE_THRESHOLD,
            "recommendations": recommendations,
            "differential": differential,
            "image_quality": quality,
            "meta": {
                "agent": self.name,
                "model": model,
                "mime": mime,
                "filename": filename,
                "latency_ms": elapsed_ms,
                "language": language,
                "downgraded_from": downgrade,
            },
        }


__all__ = [
    "DiagnosticAgent",
    "LOW_CONFIDENCE_THRESHOLD",
    "CONFIDENCE_FLOOR",
    "SPECIES_CONFIDENCE_FLOOR",
]
