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
  "condition_type": "Disease|Insect Damage|Environmental Stress|Nutrient Deficiency",
  "matched_category": "Fungal Pathogen|Bacterial Pathogen|Viral & Viroid|Nematode & Root Parasite|Physiological & Abiotic|Insect & Structural Damage",
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
You are an expert botanist and plant pathologist examining ONE photograph of
a plant. Any species, and any part: leaf, stem, bark, fruit, root or a whole
plant. Crop or wild, food plant or ornamental, tree, shrub, grass, houseplant
or succulent - identify what is actually in front of you.

Work in THREE steps, in this order.

STEP 1 - IDENTIFY THE PLANT (this gates everything else).
Name the plant species you actually see in "crop" (and its script twin in
"crop_ar"), and record how sure you are in "species_confidence" (float 0-1).
Judge from leaf shape, margin, venation, arrangement and surface texture -
and from bark, fruit, flower, thorns, seed pods or growth habit when the
framed plant part allows it. When the exact species is beyond reach, name the
GENUS or FAMILY instead and say which you gave: Citrus, Eucalyptus, Brassica,
Solanaceae, Poaceae, Fabaceae, Asteraceae, Lamiaceae, Liliaceae, a fern, a
cactus, a palm, an ornamental, a houseplant. A correct family is far more
useful than a confident wrong species, and family-level identification is
enough to reason about what actually attacks that family.
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

Then classify whatever you found into "condition_type" - exactly one of these:
  - "Disease" -> fungal, bacterial or viral attack: lesions with defined
    borders, spreading necrosis, rusts, mildews, rots, cankers, blights.
  - "Insect Damage" -> animals feeding on or in the plant: galls, bumps,
    blisters and swellings, mines and tunnels under the epidermis, chewed
    margins, webbing, stippling, frass. A structural deformation of the
    tissue is INSECT DAMAGE until proven otherwise, not a fungus.
  - "Environmental Stress" -> abiotic injury with no organism present: tip
    burn, leaf scorch, sunscald, windburn, frost, drought, waterlogging,
    salt or herbicide damage. It tends to stop at the same place on every
    leaf and has no advancing edge.
  - "Nutrient Deficiency" -> interveinal chlorosis, marginal scorch, uniform
    yellowing, purpling or blossom-end rot, typically SYMMETRICAL across the
    leaf and following the vein pattern rather than a lesion boundary.
"condition_type" = "None" only when "status" = "healthy".

When you can name a disease, also set "matched_category" to exactly one of:
  - "Fungal Pathogen" -> mildews, rusts, smuts, scabs, blights, anthracnose,
    rots, wilts, leaf spots, sooty mould
  - "Bacterial Pathogen" -> cankers, fire blight, soft rots, bacterial wilt,
    crown gall, angular leaf spot, vascular streaking (Xylella)
  - "Viral & Viroid" -> mosaic, mottle, leafroll, yellow leaf curl,
    ringspot, dwarfing, and viroids
  - "Nematode & Root Parasite" -> root-knot, cyst, lesion and citrus
    nematodes: root galls, stunting, patchy wilt, slow dieback
  - "Physiological & Abiotic" -> nutrient deficiency or toxicity, tip burn,
    blossom-end rot, gummosis, frost, sunscald, fruit splitting
  - "Insect & Structural Damage" -> galls, miners, borers, chewing,
    stippling, webbing
Draw on the FULL breadth of plant pathology, not only the handful of crop
diseases you see most often. This plant may be any species and any part, so a
rare or regional condition belongs in exactly the same taxonomy as a common
one - name it in the domain it belongs to rather than forcing it into a
familiar category.

STEP 3 - DESCRIBE AND EXPLAIN (whenever "status" is "diseased" or "uncertain").
Ground the finding in what is actually visible in this image. The exact name
may be rare, regional, or simply unknown to you - that is NOT a reason to
return nothing. An empty answer helps nobody, so never leave a field blank and
never emit an "I don't know" state. When you cannot name the condition with
confidence, describe the symptom type, the severity, and the most likely
biological or environmental cause instead, hold "status" at "uncertain", and
rank your hypotheses in "differential" from most to least likely.
Distinguish between the patterns that separate the common look-alikes:
  - rust -> raised orange-brown pustules, often ringed by chlorosis
  - blight -> expanding necrotic lesions; early blight shows concentric
    "target-board" rings, late blight shows water-soaked dark patches with
    pale haloes and can be followed by white fuzzy growth underneath
  - powdery / downy mildew -> discrete white-grey COLONIES growing on the
    surface, powdery spreading between veins, downy angular and confined by
    veins. An evenly fuzzy or silvery leaf is NOT mildew.
  - galls and blisters -> raised bumps where the tissue has been reprogrammed
    by a mite, wasp, midge or bacterium; count them and note any exit hole on
    the underside, and never report them as a leaf spot
  - leaf miners -> pale wandering trails, blotches or serpentine lines INSIDE
    the leaf, best seen against the light, with the tissue around them still
    intact - an insect, not a deficiency
  - pest damage -> chewing, mines, stippling, or frass; say so rather than
    calling it a fungus
  - nutrient deficiency -> a symmetric pattern (interveinal chlorosis,
    marginal scorch, uniform yellowing) with no expanding lesion edge
  - tip burn and scorch -> browning that begins at the tip or margin and
    stops abruptly; abiotic, so it does not advance from day to day
State the primary symptoms you can see, list at least one plausible
alternative in "differential", and give a concrete ordered treatment
sequence in "recommendations" - irrigation, nutrition and field hygiene count
as treatment here, not only a spray. If the condition really is unknown, the
steps must be cultural and low-risk - hygiene, water, nutrition, physical
removal - rather than a specific product.

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
- "status" = "healthy" only when you have positively confirmed healthy
  tissue, and never as a way of being polite about a poor photo - a poor photo
  is "uncertain".
- "condition_type" is EXACTLY "Disease", "Insect Damage", "Environmental
  Stress", "Nutrient Deficiency", or "None" when healthy. Fill it in every
  response including uncertain ones, where it names the CLASS you suspect.
- "matched_category" is EXACTLY one of "Fungal Pathogen", "Bacterial
  Pathogen", "Viral & Viroid", "Nematode & Root Parasite", "Physiological &
  Abiotic", "Insect & Structural Damage", or "None" when healthy. It refines
  "condition_type": a "Disease" class must pick the pathogen domain, and a
  healthy plant is "None" in both fields.
- No field is ever blank and no response is ever an "I don't know". An
  unusual or rare image is answered with a symptom description, a severity and
  ranked hypotheses — see STEP 3 — never with silence.
- "confidence" and "severity_pct" and "species_confidence" are floats in [0,1].
- Be honest: if the image is blurry, over-exposed or not a plant part, lower
  "confidence" and say so in "confidence_basis"; still fill every field.
- "crop" and "disease" are ALWAYS in English and "crop_ar" and "disease_ar"
  are ALWAYS natural Arabic — all four, in every response, whichever language
  you are writing the rest in.
- "pathology" is the full explanation the farmer reads: the symptom type, the
  severity, and the most likely biological or environmental cause — including
  when the condition itself is uncertain or left unnamed.
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

#: The labels a confirmed-healthy plant is renamed to. Named once here because
#: two places need them and they must agree: the healthy branch writes them,
#: and :func:`_provisional_name` has to recognise them — otherwise the
#: sanitizer would hand "نبات سليم" straight back as a probable disease, which
#: is worse than the blank label it exists to improve on.
_HEALTHY_LABEL_EN = "Healthy Plant"
_HEALTHY_LABEL_AR = "نبات سليم"


def _provisional_name(value: Any) -> str:
    """Keep a hypothesis only when it actually names something.

    The undetermined branch masks ``disease``/``disease_ar`` — that guardrail
    is the point of the field — but a lead the model stood behind is still
    worth more to a farmer than a blank label. So it is carried separately,
    here sanitised against the three shapes that would be worse than silence:
    nothing at all, a healthy verdict echoed back, and our own masking label.

    Returns ``""`` for every rejected case rather than ``None``, so a client
    can test presence with a plain truthiness check.
    """
    text = as_str(value, "").strip()
    if not text:
        return ""
    if text.lower() in _HEALTHY_DISEASE_NAMES:
        return ""
    if text in {_HEALTHY_LABEL_EN, _HEALTHY_LABEL_AR}:
        return ""
    if text.lower() in {_UNCERTAIN_LABEL_EN.lower(), _UNCERTAIN_LABEL_AR.lower()}:
        return ""
    if text.lower() in {"unknown", "none", "-", "غير معروف", "غير محدد"}:
        return ""
    return text


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


# --------------------------------------------------------------- taxonomy ---

#: The pathological classes every finding is sorted into. The prompt asks the
#: model for ``condition_type`` directly, but a field *requested* of a model is
#: not a guarantee either, so :func:`_condition_type` re-derives it whenever the
#: model omits or mangles the value. Four classes plus ``None`` for a confirmed
#: healthy plant: a healthy plant belongs to none of them, and defaulting it to
#: ``Disease`` would reintroduce exactly the false positive the floors below
#: exist to stop.
CONDITION_TYPES: tuple[str, ...] = (
    "Disease",
    "Insect Damage",
    "Environmental Stress",
    "Nutrient Deficiency",
)

#: What a healthy plant reports as. Deliberately a member of the vocabulary
#: rather than an empty string, so the field is never blank on any response.
CONDITION_TYPE_NONE = "None"

_CONDITION_TYPES_ALL: tuple[str, ...] = (*CONDITION_TYPES, CONDITION_TYPE_NONE)

#: Script twin of each class, so ``analysis_details`` reads as one sentence in
#: either language instead of dropping an English taxonomy label mid-line.
_CONDITION_TYPE_AR: dict[str, str] = {
    "Disease": "مرض",
    "Insect Damage": "أذى حشري",
    "Environmental Stress": "إجهاد بيئي",
    "Nutrient Deficiency": "نقص غذائي",
    "None": "لا يوجد",
}

_SEVERITY_AR: dict[str, str] = {
    "none": "منعدمة",
    "low": "منخفضة",
    "moderate": "متوسطة",
    "high": "عالية",
    "critical": "حرجة",
}

#: Evidence that identifies a class when the model did not supply a usable one.
#: Matching is by whole token first and only then by stem, because a plain
#: substring test on the shortest word here — "mine" — also matches "mineral",
#: which would file a nutrient deficiency under insect damage.
#:
#: Order is load-bearing: insect evidence is by far the most distinctive, and a
#: "deficiency" reading must beat an environmental one because the two overlap
#: in practice — waterlogging also yellows a leaf, but only the deficiency
#: reading tells the farmer to check fertiliser and pH.
_INSECT_TOKENS = frozenset({
    "gall", "galls", "galled", "galling", "mine", "mines", "mined", "mining",
    "miner", "miners", "mite", "mites", "aphid", "aphids", "thrip", "thrips",
    "insect", "insects", "pest", "pests", "borer", "borers", "larva",
    "larvae", "grub", "grubs", "whitefly", "whiteflies", "caterpillar",
    "caterpillars", "scale", "scales", "webbing", "stippling", "frass",
    "chew", "chewed", "chewing", "snail", "snails", "blister", "blisters",
})
_INSECT_STEMS = ("gall", "tunnel", "frass", "stippl", "webbing")

_NUTRIENT_TOKENS = frozenset({
    "deficiency", "deficient", "nutrient", "nutrients", "interveinal",
    "chlorosis", "chlorotic", "nitrogen", "magnesium", "potassium",
    "phosphorus", "phosphor", "iron", "zinc", "calcium", "manganese",
    "fertiliser", "fertilizer", "fertilising", "fertilizing",
})
_NUTRIENT_STEMS = ("deficien", "interveinal", "chlorot", "fertilis", "fertiliz")

_ENV_TOKENS = frozenset({
    "scorch", "scorched", "scorching", "sunscald", "sunburn", "drought",
    "frost", "waterlogged", "waterlogging", "herbicide", "windburn",
    "oedema", "edema", "salinity", "overwatered",
})
_ENV_STEMS = (
    "scorch", "waterlog", "sunscald", "windburn", "tip burn", "tipburn",
    "overwater", "herbicid", "transplant shock",
)


# --------------------------------------------------- agronomic domains ---

#: The agronomic domains a named disease must be sorted into.
#:
#: The brief named five. A sixth — ``Insect & Structural Damage`` — is added
#: deliberately: galls and leaf miners were already classified as
#: ``Insect Damage`` at the coarse level, and folding them into "Nematode &
#: Root Parasite" (because *root* galls are nematode galls) or into the
#: physiological bucket would tell a farmer that an insect is a nutrient
#: problem. This field *refines* :data:`CONDITION_TYPES`; it does not replace
#: it.
MATCHED_CATEGORIES: tuple[str, ...] = (
    "Fungal Pathogen",
    "Bacterial Pathogen",
    "Viral & Viroid",
    "Nematode & Root Parasite",
    "Physiological & Abiotic",
    "Insect & Structural Damage",
)

#: Reported for a confirmed-healthy plant, mirroring
#: :data:`CONDITION_TYPE_NONE` so the field is never blank on any response.
MATCHED_CATEGORY_NONE = "None"

#: Domain inference, most specific first. A pathogen genus identifies the
#: domain whatever the symptom happens to look like — Alternaria is a fungus
#: whether it produced blight or a leaf spot — so genus names outrank symptom
#: words. Symptom words that occur in more than one domain (wilt, rot, canker,
#: gall, blight) are deliberately absent: a confident wrong answer is worse
#: than falling through to the documented fungal prior below.
_DOMAIN_RULES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({
        "alternaria", "fusarium", "botrytis", "phytophthora", "colletotrichum",
        "erysiphe", "puccinia", "ustilago", "venturia", "septoria",
        "cercospora", "podosphaera", "sclerotinia", "rhizoctonia",
        "monilinia", "plasmopara", "peronospora", "oomycete",
    }), "Fungal Pathogen"),
    (frozenset({
        "xanthomonas", "agrobacterium", "pseudomonas", "erwinia", "xylella",
        "phytoplasma", "clavibacter", "ralstonia", "pantoea", "acidovorax",
        "bacteria", "bacterial",
    }), "Bacterial Pathogen"),
    (frozenset({
        "meloidogyne", "globodera", "heterodera", "pratylenchus",
        "tylenchulus", "radopholus", "ditylenchus",
    }), "Nematode & Root Parasite"),
    (frozenset({
        "virus", "viral", "viroid", "tmv", "cmv", "tylcv", "plrv", "tswv",
        "mosaic", "mottle", "leafroll", "ringspot",
    }), "Viral & Viroid"),
    (frozenset({
        "mildew", "powdery", "downy", "rust", "rusts", "smut", "smuts",
        "scab", "scabs", "anthracnose", "mould", "mold", "mycelium",
        "spores", "spore",
    }), "Fungal Pathogen"),
    (frozenset({
        "nematode", "nematodes", "knot", "cyst",
    }), "Nematode & Root Parasite"),
)

_JOIN_WORDS = frozenset({"and", "of", "the", "with"})


def _category_words(text: str) -> frozenset[str]:
    """Lower-cased word set for a category value, minus the joining words.

    Lets ``"viral/viroid"`` and ``"Viral & Viroid"`` compare equal — the
    punctuation is discarded and ``&`` carries no meaning — so a model that
    renders the separator differently is still matched instead of silently
    falling through to the prior.
    """
    words = "".join(c if c.isalpha() else " " for c in text.lower()).split()
    return frozenset(w for w in words if w not in _JOIN_WORDS)


_ALL_CATEGORY_KEYS: tuple[frozenset[str], ...] = tuple(
    _category_words(c) for c in MATCHED_CATEGORIES
)


def _matched_category(
    raw: Any,
    *,
    status: str,
    condition_type: str,
    texts: list[str],
) -> str:
    """Resolve the agronomic domain, without ever leaving the field blank.

    Precedence, most reliable first:

    1. A confirmed-healthy plant belongs to no domain.
    2. The model's own value, when it is one of :data:`MATCHED_CATEGORIES`.
    3. The coarse ``condition_type``, for the two non-pathogen domains. A
       raised gall is bacterial (crown gall) or an insect's, and only the
       coarse class has already made that call, so no symptom word may
       overrule it.
    4. Pathogen genus, then unambiguous symptom words, from the text.
    5. ``Fungal Pathogen`` as an explicit prior for a disease with no other
       signal — documented here rather than hidden, because ``status`` and
       ``disease`` carry the actual claim and this is only metadata about
       which domain it most likely belongs to.
    """
    if status == "healthy":
        return MATCHED_CATEGORY_NONE

    value = as_str(raw, "").strip()
    if value:
        want = _category_words(value)
        for canonical in MATCHED_CATEGORIES:
            if _category_words(canonical) == want:
                return canonical
        # Tolerate a gloss such as "Viral & Viroid Pathogen", where the
        # canonical is a subset of what the model wrote. Longest first, so a
        # longer domain never loses its own value to a shorter one.
        for canonical in sorted(MATCHED_CATEGORIES, key=len, reverse=True):
            if _ALL_CATEGORY_KEYS[MATCHED_CATEGORIES.index(canonical)] <= want:
                return canonical

    if condition_type == "Insect Damage":
        return "Insect & Structural Damage"
    if condition_type in {"Environmental Stress", "Nutrient Deficiency"}:
        return "Physiological & Abiotic"

    blob = " ".join(t for t in texts if t).lower()
    tokens = {w for w in "".join(c if c.isalpha() else " " for c in blob).split()}
    for token_set, canonical in _DOMAIN_RULES:
        if tokens & token_set:
            return canonical

    return "Fungal Pathogen"


def _condition_type(raw: Any, *, status: str, texts: list[str]) -> str:
    """Coerce whatever the model sent into one of :data:`CONDITION_TYPES`.

    A healthy plant short-circuits to ``None`` before any other reading: no
    amount of suggestive wording in the payload may classify confirmed-healthy
    tissue as a disease class. Otherwise the value is matched exactly first
    (so a stray gloss cannot win), then as a substring ordered longest-first
    (so ``"Insect Damage (galls)"`` resolves to insect damage and never to
    ``Disease`` just because the word "damage" was parsed loosely), and only
    then by the keyword stems above.
    """
    if status == "healthy":
        return CONDITION_TYPE_NONE

    value = as_str(raw, "").strip().lower().replace("_", " ").replace("-", " ")
    if value:
        for canonical in CONDITION_TYPES:
            if value == canonical.lower():
                return canonical
        for canonical in sorted(CONDITION_TYPES, key=len, reverse=True):
            if canonical.lower() in value:
                return canonical

    blob = " ".join(t for t in texts if t).lower()
    # Tokens are whole words stripped of punctuation, so "mine" matches the
    # leaf-mining symptom but cannot swallow "mineral".
    tokens = {w for w in "".join(c if c.isalpha() else " " for c in blob).split()}
    for token_set, stems, canonical in (
        (_INSECT_TOKENS, _INSECT_STEMS, "Insect Damage"),
        (_NUTRIENT_TOKENS, _NUTRIENT_STEMS, "Nutrient Deficiency"),
        (_ENV_TOKENS, _ENV_STEMS, "Environmental Stress"),
    ):
        if tokens & token_set or any(stem in blob for stem in stems):
            return canonical
    return "Disease"


#: Low-risk, non-chemical actions offered *alongside* an undetermined verdict.
#:
#: The guardrail exists so a farmer never sprays a healthy crop — not so an
#: uncertain verdict leaves them standing in the field with nothing to do.
#: Hygiene, physical removal and cultural correction are safe under every
#: hypothesis in the differential, which is what makes them safe to say when we
#: do not know which hypothesis is right. The chemical answer stays withheld:
#: "do not spray yet" remains the first thing an uncertain verdict tells them.
_CULTURAL_REMEDY: dict[str, dict[str, list[str]]] = {
    "en": {
        "Disease": [
            "Meanwhile: bag and bin heavily affected tissue, disinfect tools between plants, and keep foliage dry to slow any spread.",
        ],
        "Insect Damage": [
            "Meanwhile: prune out galled or mined shoots and destroy them, and inspect leaf undersides for eggs and young.",
        ],
        "Environmental Stress": [
            "Meanwhile: correct the cause first - water deeply but less often, shield from midday sun and wind, and pause fertiliser until growth recovers.",
        ],
        "Nutrient Deficiency": [
            "Meanwhile: apply a balanced fertiliser, and check drainage and soil pH before adding anything more.",
        ],
        "None": [
            "Meanwhile: keep scouting weekly and maintain balanced feeding and irrigation hygiene.",
        ],
    },
    "ar": {
        "Disease": [
            "في هذه الأثناء: أتلف الأوراق والأجزاء المصابة بشدة، ونظّف أدوات القص بين النباتات، وأبقِ الأوراق جافة لمنع الانتشار.",
        ],
        "Insect Damage": [
            "في هذه الأثناء: اقطع الأجزاء التي تظهر عليها نقوش أو تورّمات وأتلفها، وافحص ظهر الأوراق بحثًا عن البيض والحشرات الصغيرة.",
        ],
        "Environmental Stress": [
            "في هذه الأثناء: عالج السبب أولًا — ارِ بعمق وأقل تكرارًا، واحمِ النبات من شمس الظهيرة والرياح، وأوقف التسميد حتى يستعيد نموه.",
        ],
        "Nutrient Deficiency": [
            "في هذه الأثناء: أضف سمادًا متوازنًا، وافحص تصريف التربة ودرجة حموضتها قبل إضافة أي شيء آخر.",
        ],
        "None": [
            "في هذه الأثناء: واصِف الفحص أسبوعيًا وحافظ على تسميد متوازن ونظافة الري.",
        ],
    },
}

#: Fixed prose for a confirmed-healthy plant, used when the model supplied no
#: explanation of its own. Bilingual by construction, exactly like the care
#: tips above, so the offline path stays usable in both locales.
_ANALYSIS_HEALTHY: dict[str, str] = {
    "en": (
        "No pathological symptoms detected. The colour, texture and surface "
        "features observed are within normal variation for this plant, "
        "including any natural hairs or waxy bloom."
    ),
    "ar": (
        "لم تُرصد أي أعراض مرضية؛ فاللون والملمس وخصائص السطح الملاحظة تدخل "
        "في التباين الطبيعي لهذا النبات، بما فيها الأهداب الطبيعية أو طبقة الشمع."
    ),
}


def _uncertain_analysis(
    *,
    symptoms: list[str],
    hypotheses: list[str],
    condition_type: str,
    confidence: float,
    language: str,
) -> str:
    """Build the honest description for an undetermined verdict.

    Written here rather than taken from the model on purpose: the model's
    ``pathology`` prose is written as a diagnosis, and an uncertain verdict must
    not read as one when it lands on the farmer's screen. This keeps the
    symptom type, the severity, the classification and the ranked hypotheses —
    everything the request asks for — while stating plainly that no condition
    was confirmed.
    """
    seen = ", ".join(symptoms) if symptoms else "visible abnormality"
    ranked = " | ".join(hypotheses) if hypotheses else "not yet determined"
    if language == "ar":
        return (
            f"الأعراض المرصودة: {seen}. لا تؤكد الصورة حالة واحدة، لذا لم يُسمَّ "
            f"مرض هنا. التصنيف: {_CONDITION_TYPE_AR.get(condition_type, condition_type)}. "
            f"أكثر الفرضيات ترجيحًا: {ranked}. مستوى الثقة: {round(confidence * 100)}٪."
        )
    return (
        f"Observed symptoms: {seen}. The photograph does not confirm a single "
        f"condition, so no disease is named here. Classification: {condition_type}. "
        f"Most likely explanations: {ranked}. Confidence: {round(confidence * 100)}%."
    )


def _fallback_analysis(
    *,
    disease: str,
    disease_ar: str,
    pathogen: str,
    condition_type: str,
    severity: str,
    symptoms: list[str],
    language: str,
) -> str:
    """Compose ``analysis_details`` when the model left ``pathology`` empty.

    Guarantees the field is never blank — the request forbids returning nothing
    — without inventing a cause: the cause line names the pathogen only when the
    model actually supplied one, and otherwise falls back to the class.
    """
    seen = ", ".join(symptoms) if symptoms else "not itemised"
    cause = pathogen if pathogen and pathogen.lower() not in {"unknown", "none"} else condition_type
    if language == "ar":
        return (
            f"{disease_ar}. التصنيف: {_CONDITION_TYPE_AR.get(condition_type, condition_type)}. "
            f"الشدة: {_SEVERITY_AR.get(severity, severity)}. الأعراض المرصودة: {seen}. "
            f"السبب الأكثر ترجيحًا: {cause}."
        )
    return (
        f"{disease}. Classification: {condition_type}. Severity: {severity}. "
        f"Observed symptoms: {seen}. Most likely cause: {cause}."
    )


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

        The response additionally carries the universal-engine contract —
        ``detected_plant``, ``condition_type``, ``matched_category``,
        ``disease_name_en``, ``disease_name_ar``, ``confidence_score``,
        ``analysis_details`` and ``treatment_plan``. Each is derived from the
        same local that built its legacy counterpart rather than read from the
        payload a second time, so the two views of one diagnosis cannot drift
        apart. ``analysis_details`` is written locally for the undetermined
        branch so that a guess never reaches the farmer as prose in the voice
        of a confirmed diagnosis.

        An undetermined verdict additionally carries ``provisional_disease``
        and ``provisional_disease_ar`` — the name the model proposed, kept out
        of ``disease``/``disease_ar`` so the masking guardrail is untouched.
        Both are ``""`` on every other verdict, and a client that ignores them
        sees exactly the response it saw before they existed.
        """
        report = as_dict(report)
        symptoms = as_str_list(report.get("symptoms"))
        differential = as_str_list(report.get("differential"))
        recommendations = as_str_list(report.get("recommendations"))
        pathology = as_str(report.get("pathology"), "")

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

        # --- the pathological class ----------------------------------------
        # Computed here, while `disease`, `pathology` and `symptoms` still hold
        # the model's own words and `status` already holds its final value. The
        # branches below overwrite all three, so inferring the class after them
        # would be inferring from the labels we ourselves substituted.
        condition_type = _condition_type(
            report.get("condition_type"),
            status=status,
            texts=[disease, pathology, as_str(report.get("pathogen"), ""), *symptoms],
        )
        # The domain is resolved from the same snapshot of the model's own
        # words, before the branches below overwrite them, and is seeded with
        # `condition_type` so the coarse call it already made is honoured
        # rather than second-guessed by a symptom word.
        matched_category = _matched_category(
            report.get("matched_category"),
            status=status,
            condition_type=condition_type,
            texts=[disease, pathology, as_str(report.get("pathogen"), ""), *symptoms],
        )

        healthy = status == "healthy"
        uncertain = status == "uncertain"

        # Populated ONLY for an undetermined verdict, and blank everywhere
        # else, so a client can key off presence alone without also having to
        # re-derive `status`.
        provisional = ""
        provisional_ar = ""

        if healthy:
            # A confirmed-healthy leaf must read as one in both scripts, not
            # as "Healthy"/"الصحة" or whatever phrasing the model chose.
            disease = _HEALTHY_LABEL_EN
            disease_ar = _HEALTHY_LABEL_AR
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
            # The model's own hypothesis is preserved in a SEPARATE field
            # rather than restored to `disease`, so the mask above can never
            # be bypassed by accident. It carries no severity, no confident
            # tone and no specific treatment — it is a lead, and presenting
            # it as anything more than that is exactly what the floor exists
            # to prevent. `report` is never mutated, so `differential` still
            # holds the same guess behind its "Possible: " prefix.
            provisional = _provisional_name(report.get("disease"))
            provisional_ar = _provisional_name(report.get("disease_ar"))
            severity = "low"
            severity_pct = min(severity_pct, 0.25)
            # The re-photograph advice must stay FIRST: both the test suite and
            # the guardrail itself depend on an undetermined verdict opening
            # with "get a better photo" rather than with a treatment. The
            # cultural actions are appended, not prepended, because a hypothesis
            # the farmer can safely act on is not the same thing as a diagnosis.
            by_class = _CULTURAL_REMEDY.get(language, _CULTURAL_REMEDY["en"])
            recommendations = _UNCERTAIN_TIPS[language] + (
                by_class.get(condition_type) or by_class[CONDITION_TYPE_NONE]
            )
        else:
            disease_ar = as_str(report.get("disease_ar"), "غير معروف")
            pathogen = as_str(report.get("pathogen"), "Unknown")
            if not recommendations:
                recommendations = (
                    _TREATMENT_FALLBACK_AR
                    if language == "ar"
                    else _TREATMENT_FALLBACK_EN
                )

        # --- analysis_details: never blank, never a mislaid diagnosis -------
        # Every response must carry one, so each branch falls back to composed
        # text when the model supplied none. For an undetermined verdict the
        # prose is written here rather than taken from the model, because the
        # model writes `pathology` as a diagnosis and a guess must not reach
        # the farmer dressed as one.
        model_pathology = pathology.strip()
        if uncertain:
            analysis_details = _uncertain_analysis(
                symptoms=symptoms,
                hypotheses=differential,
                condition_type=condition_type,
                confidence=confidence,
                language=language,
            )
        elif healthy:
            analysis_details = model_pathology or _ANALYSIS_HEALTHY[language]
        else:
            analysis_details = model_pathology or _fallback_analysis(
                disease=disease,
                disease_ar=disease_ar,
                pathogen=pathogen,
                condition_type=condition_type,
                severity=severity,
                symptoms=symptoms,
                language=language,
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
            "pathology": pathology,
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
            # A probable name for an undetermined verdict. Deliberately kept
            # OUT of `disease`/`disease_ar`, which stay masked above: this is
            # only the lead the model named, and a client that ignores these
            # two keys sees exactly what it saw before they existed.
            "provisional_disease": provisional,
            "provisional_disease_ar": provisional_ar,
            "image_quality": quality,
            # --- universal-engine contract ---------------------------------
            # Every key here is read straight from the same local that built
            # the legacy keys above, so the two views of one diagnosis cannot
            # drift: there is no second place any of them could be written.
            # `crop`, `disease`, `confidence` and `recommendations` stay for
            # the advisor, the chat context and the dashboard, which all read
            # them today; the names below are the public contract.
            "detected_plant": crop,
            "condition_type": condition_type,
            "matched_category": matched_category,
            "disease_name_en": disease,
            "disease_name_ar": disease_ar,
            "confidence_score": confidence,
            "analysis_details": analysis_details,
            "treatment_plan": recommendations,
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
    "CONDITION_TYPES",
    "CONDITION_TYPE_NONE",
    "MATCHED_CATEGORIES",
    "MATCHED_CATEGORY_NONE",
]
