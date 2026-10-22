"""AdvisorAgent — localized agronomy for the Tébessa region (Algeria).

Turns a diagnostic report into an actionable treatment plan: immediate
actions, an irrigation schedule and prevention guidelines.

Two sources of truth, in order of preference:

1. **Groq** — one structured generation using the project's single key.
2. **Local Tébessa KB** — a built-in, offline knowledge base used whenever the
   API is unavailable, the key is missing, or the response is unusable. The
   advisor therefore *never* dead-ends a farmer on a rate-limit error.
"""

from __future__ import annotations

import json
import time
from typing import Any

from groq import APIError as GroqAPIError

from .base import (
    AgentError,
    AgentResult,
    as_dict,
    as_str,
    as_str_list,
    classify_http_error,
    parse_model_json,
    run_with_output_retry,
    run_with_quota_retry,
)
from .config import Settings, get_settings
from .language import (
    DEFAULT_LANGUAGE,
    LANG_NAME,
    has_arabic,
    normalise_language,
    reply_instruction,
)

REGION = "Tébessa, Algeria"
REGION_AR = "تبسة، الجزائر"

# ---------------------------------------------------------------------------
# Local knowledge base — semi-arid high plateau of Tébessa
# ---------------------------------------------------------------------------
# Agro-climatic context used in every generated plan:
#   * semi-arid continental, ~300-400 mm annual rainfall, erratic
#   * calcareous (Terra Rossa / rendzina) soils, pH ~7.8-8.2
#   * hot dry summers, cold winters, strong Ouarglia/Ech-Chergui winds
#   * dominant crops: durum wheat, barley, olive, almond, fig, pepper, potato

REGION_CONTEXT_AR = (
    "مناخ شبه جاف شبه قاري بأمطار تتراوح بين 300 و400 ملم غير منتظمة، ورياح قوية صيفًا، "
    "وأرض جيرية حمراء (تيرا روسا) بدرجة قلوية حوالي 8.0. "
    "الزراعة السائدة: القمح الصلب، الشعير، الزيتون، اللوز، التين، والفلفل."
)

REGION_CONTEXT_EN = (
    "Semi-arid continental high plateau: 300-400 mm of erratic annual rainfall, "
    "strong summer winds, and alkaline red Terra Rossa soils at around pH 8.0. "
    "The dominant crops are durum wheat, barley, olive, almond, fig, pepper and potato."
)

#: Every plan carries this. It is safety text, so it is never abbreviated and
#: never left in one language when the farmer asked for the other.
_DISCLAIMER_EN = (
    "This is decision-support guidance, not a certified agronomic prescription. "
    "Confirm the diagnosis with the DPA of Tébessa (Direction Provinciale de "
    "l'Agriculture) or the Chambre d'Agriculture before applying any "
    "phytosanitary product, and always respect the product label and local regulations."
)
_DISCLAIMER_AR = (
    "هذه إرشادات لدعم القرار وليست وصفة زراعية معتمدة. "
    "تحقّق من التشخيص مع المديرية الإقليمية للزراعة بتبسة أو مع غرفة الفلاحة "
    "قبل استعمال أي منتج فلاحي، واحترم دائمًا ملصق المنتج والتشريعات المحلية."
)

#: Known pathogens -> plan. Keys are matched loosely against the diagnosis.
LOCAL_KB: dict[str, dict[str, Any]] = {
    "early blight": {
        "title": "Early blight (Alternaria solani)",
        "title_ar": "اللفحة المبكرة (أولرياريا سولاني)",
        "pathogen": "Alternaria solani",
        "kind": "fungal",
        "actions": [
            "Remove and destroy (do not compost) the most infected lower leaves to cut inoculum.",
            "Stop overhead irrigation; water at the soil line in the morning only.",
            "Maintain plant spacing for airflow; Tébessa's dry winds favour sporulation on stressed tissue.",
        ],
        "actions_ar": [
            "أزل وأتلف أوراق الجزء السفلي الأكثر إصابة (لا تضعها في التسميد العضوي) لتقليل مسبب المرض.",
            "أوقف الري بالرش فوق الأوراق؛ والِ عند خط التربة صباحًا فقط.",
            "حافظ على مسافات الزرع لتهوية جيدة؛ فرياح تبسة الجافّة تساعد على تكوين الأبواغ في الأنسجة المجهدة.",
        ],
        "treatments": [
            {"type": "cultural", "detail": "Rotate with a non-host (cereals) for 2-3 seasons; avoid solanaceae volunteers."},
            {"type": "organic", "detail": "Bacillus subtilis or copper hydroxide (Bordeaux mixture) every 7-10 days, max 4 sprays/season."},
            {"type": "chemical", "detail": "Azoxystrobin 250 g/L (0.8 mL/L) or Mancozeb 80% WP (2 g/L), alternate modes of action."},
        ],
        "treatments_ar": [
            {"type": "cultural", "detail": "بادل مع محاصيل غير مضيفة (الحبوب) لمدة 2-3 موسم؛ وتجنّب بادرات الباذنجانيات."},
            {"type": "organic", "detail": "رشّ Bacillus subtilis أو هيدروكسيد النحاس (مزيج بوردو) كل 7-10 أيام، بحد أقصى 4 رشات في الموسم."},
            {"type": "chemical", "detail": "أوكسيستروبين 250 غ/ل (0.8 مل/ل) أو مانكوزيب 80% (2 غ/ل)، مع تبديل آليات التأثير لتجنّب المقاومة."},
        ],
        "irrigation": {
            "schedule": "Drip, early morning only, 2-3 sessions/week in summer.",
            "target": "Keep the root zone at 60-70% field capacity; never wet the foliage.",
            "note": "Deficit stress widens the infection window - hold moisture steady during fruit set.",
        },
        "irrigation_ar": {
            "schedule": "ري بالتنقيط، صباحًا فقط، 2-3 مرات أسبوعيًا في الصيف.",
            "target": "حافظ على منطقة الجذور عند 60-70% من سعة الحقل؛ ولا تبلّل الأوراق إطلاقًا.",
            "note": "الإجهاد الناتج عن نقص الماء يوسّع فترة الإصابة - حافظ على ثبات الرطوبة أثناء تثمار الثمار.",
        },
        "prevention": [
            "Mulch to stop soil splash, which carries spores onto lower leaves.",
            "Balanced potassium feeding improves tissue resistance.",
            "Scout weekly from the first lower-leaf lesion; treat before 5% canopy loss.",
        ],
        "prevention_ar": [
            "استعمل الغطاء النباتي لمنع تطاير ماء التربة الذي ينقل الأبواغ إلى الأوراق السفلية.",
            "التسميد المتوازن بالبوتاسيوم يعزّز مقاومة الأنسجة.",
            "افحص المحصول أسبوعيًا بدءًا من أول بقعة على الأوراق السفلية؛ وعالج قبل أن يتجاوز فقدان الكانوب 5%.",
        ],
    },
    "late blight": {
        "title": "Late blight (Phytophthora infestans)",
        "title_ar": "اللفحة المتأخرة (فيتوفتورا إنفيستانس)",
        "pathogen": "Phytophthora infestans",
        "kind": "oomycete",
        "actions": [
            "Destroy infected plants immediately and burn or bury them - this is a polycyclic, epidemic disease.",
            "Apply a protective copper treatment to all remaining healthy plants the same day.",
            "Halt all foliar watering; increase plant spacing and remove weeds.",
        ],
        "actions_ar": [
            "دمر النباتات المصابة فورًا عن طريق الحرق أو الدفن - فالمرض يتكاثر وينتشر وبائيًا.",
            "طبّق معالجة نحاسية واقية على كل النباتات السليمة المتبقية في اليوم نفسه.",
            "أوقف كل ري يصيب الأوراق؛ وزيّد مسافات الزرع وأزل الأعشاب.",
        ],
        "treatments": [
            {"type": "organic", "detail": "Copper hydroxide 500 g/L (2-3 mL/L) as a protective cover, every 7 days in wet spells."},
            {"type": "chemical", "detail": "Metalaxyl-M + Mancozeb (1.5-2 g/L) curatively; alternate with Cymoxanil to avoid resistance."},
        ],
        "treatments_ar": [
            {"type": "organic", "detail": "هيدروكسيد النحاس 500 غ/ل (2-3 مل/ل) كطبقة واقية، كل 7 أيام خلال الأمطار."},
            {"type": "chemical", "detail": "ميتالاكسيل-M + مانكوزيب (1.5-2 غ/ل) للعلاج؛ وبدّل مع سيموكسانيلا لتجنّب المقاومة."},
        ],
        "irrigation": {
            "schedule": "Drip only, 2 sessions/week, strictly before 8 a.m.",
            "target": "Reduce humidity in the canopy; avoid any evening wetting.",
            "note": "Cool nights plus dew drive outbreaks - increase vigilance after any humidity spike.",
        },
        "irrigation_ar": {
            "schedule": "ري بالتنقيط فقط، مرتين أسبوعيًا، ويُنفَّذ قبل الساعة الثامنة صباحًا.",
            "target": "قلّل الرطوبة داخل الكانوب؛ وتجنّب أي بلل في المساء.",
            "note": "الليالي الباردة مع الندى تسبّب التفشّي - زد اليقظة بعد أي ارتفاع في الرطوبة.",
        },
        "prevention": [
            "Plant resistant varieties where available.",
            "Remove volunteer potato/ tomato plants around the plot.",
            "Destroy haulm after harvest to remove overwintering sites.",
        ],
        "prevention_ar": [
            "استعمل الأصناف المقاومة حيثما توافرت.",
            "أزل نباتات البطاطس والطماطم البرية حول الحقل.",
            "دمر بقايا الساق بعد الحصاد لإزالة أماكن بقاء الممرضات خلال الشتاء.",
        ],
    },
    "powdery mildew": {
        "title": "Powdery mildew",
        "title_ar": "البَيَاض الدقيقي",
        "pathogen": "Blumeria / Erysiphe spp.",
        "kind": "fungal",
        "actions": [
            "Remove heavily infected leaves and dispose of them off-plot.",
            "Avoid nitrogen over-fertilisation, which produces soft, susceptible tissue.",
            "Keep vines and canopies open to sun and wind.",
        ],
        "actions_ar": [
            "أزل الأوراق المصابة بشدّة وأبعدها خارج الحقل.",
            "تجنّب الإفراط في التسميد بالنيتروجين الذي يُنتج أنسجة طرية سهلة الإصابة.",
            "حافظ على فتح أغصان الكانوب لدخول الشمس والهواء.",
        ],
        "treatments": [
            {"type": "organic", "detail": "Sulphur (5 g/L) at the first signs; also effective as a preventive."},
            {"type": "chemical", "detail": "Tebuconazole 250 g/L (1 mL/L) or Myclobutanil; rotate chemistry."},
        ],
        "treatments_ar": [
            {"type": "organic", "detail": "الكبريت (5 غ/ل) عند أول الأعراض؛ وهو فعّال كذلك كمعالجة وقائية."},
            {"type": "chemical", "detail": "تيبيكونازول 250 غ/ل (1 مل/ل) أو ميكلوبيوتانيلا؛ مع تبديل المادة الفعّالة."},
        ],
        "irrigation": {
            "schedule": "Drip at the root zone, morning only.",
            "target": "Moderate, steady moisture - drought stress increases susceptibility.",
            "note": "Dry leaves but a wet root zone is the ideal configuration against mildew.",
        },
        "irrigation_ar": {
            "schedule": "ري بالتنقيط عند منطقة الجذور، صباحًا فقط.",
            "target": "رطوبة معتدلة ومنتظمة - فجفاف التربة يزيد من العُنّة.",
            "note": "أوراق جافة مع منطقة جذور رطبة هي التهيئة المثالية ضد البياض الدقيقي.",
        },
        "prevention": [
            "Prune for airflow; this is the single best cultural control.",
            "Burn or bury pruned material rather than composting it.",
            "Treat preventively in the 2-3 weeks before the usual local onset.",
        ],
        "prevention_ar": [
            "أجري التقليم لتهوية جيدة؛ وهذه أفعلى وسائل المكافحة الثقافية.",
            "احرق أو ادفن المواد المقطوعة بدلاً من تحويلها إلى کمپوست.",
            "عالج وقائيًا خلال الأسبوعين إلى الثلاثة السابقة لظهور المرض المعتاد في المنطقة.",
        ],
    },
    "rust": {
        "title": "Leaf / stem rust",
        "title_ar": "الصدأ",
        "pathogen": "Puccinia spp.",
        "kind": "fungal",
        "actions": [
            "Eliminate volunteer host plants and alternate hosts around the field.",
            "Remove the most heavily pustuled leaves.",
            "Avoid late nitrogen applications that prolong succulent tissue.",
        ],
        "actions_ar": [
            "أزل النباتات المضيفة البرية والمضيفين البديلين حول الحقل.",
            "أزل الأوراق الأكثر تكوّنًا للأبواغ الصدئية.",
            "تجنّب إضافة النيتروجين متأخرًا في الموسم؛ لأنها تطيل بقاء الأنسجة الطريّة.",
        ],
        "treatments": [
            {"type": "chemical", "detail": "Tebuconazole 250 g/L (1 mL/L) or Propiconazole 250 g/L (1 mL/L) at first pustules."},
            {"type": "organic", "detail": "Sulphur-based treatments give partial control on soft crops."},
        ],
        "treatments_ar": [
            {"type": "chemical", "detail": "تيبيكونازول 250 غ/ل (1 مل/ل) أو بروبيكونازول 250 غ/ل (1 مل/ل) عند أول ظهور للأبواغ."},
            {"type": "organic", "detail": "العلاجات المبنية على الكبريت تعطي ضبطًا جزئيًا في المحاصيل الطريّة."},
        ],
        "irrigation": {
            "schedule": "Drip only, early morning, avoid late-day wetting.",
            "target": "Even moisture; do not allow prolonged leaf wetness or heavy drought stress.",
            "note": "Rain-dew cycles trigger rust epidemics on the high plateau.",
        },
        "irrigation_ar": {
            "schedule": "ري بالتنقيط فقط، في الصباح الباكر، وتجنّب البلل في أواخر النهار.",
            "target": "رطوبة متوازنة؛ ولا تسمح ببقاء الماء على الأوراق طويلًا ولا بجفاف شديد.",
            "note": "تتالي المطر والندى يسبّب تفشّي وبائيًا للصدأ على الهضاب العليا.",
        },
        "prevention": [
            "Use rust-resistant varieties and certified seed.",
            "Rotate with non-host crops for at least one season.",
            "Scout the plot edges first - infection starts there.",
        ],
        "prevention_ar": [
            "استعمل أصنافًا مقاومة للصدأ وبذورًا معتمدة.",
            "بادل مع محاصيل غير مضيفة لمدة موسم واحد على الأقل.",
            "ابدأ الفحص من أطراف الحقل أولًا - فالإصابة تبدأ من هناك.",
        ],
    },
    "nitrogen": {
        "title": "Nitrogen deficiency",
        "title_ar": "نقص النيتروجين",
        "pathogen": "Nutritional disorder (non-infectious)",
        "kind": "abiotic",
        "actions": [
            "Apply a quick-release nitrogen source (ammonium nitrate or urea) and water it in.",
            "Correct the underlying cause: leaching on sandy pockets or poor rooting in compacted/calcareous soil.",
            "Do not over-apply - excess N also invites aphids and mildew.",
        ],
        "actions_ar": [
            "أضف مصدرًا سريع التحلّل للنيتروجين (نترات الأمونيوم أو اليوريا) والِه جيدًا.",
            "صحّح السبب الجذري: الغسيل في الجيوب الرملية أو ضعف تجذر الجذور في التربة المضغوطة الجيرية.",
            "لا تفرط في الجرعة - فالإفراط في النيتروجين يجذب الحشرات ويعزّز البياض الدقيقي.",
        ],
        "treatments": [
            {"type": "cultural", "detail": "Split N into 2-3 doses across the season rather than one large application."},
            {"type": "organic", "detail": "Well-rotted compost or poultry manure at 3-5 t/ha, incorporated before planting."},
            {"type": "chemical", "detail": "Foliar urea 2% for a fast green-up where soil application is too slow."},
        ],
        "treatments_ar": [
            {"type": "cultural", "detail": "قسّم النيتروجين إلى 2-3 جرعات خلال الموسم بدلاً من جرعة واحدة كبيرة."},
            {"type": "organic", "detail": "سماد عضوي متحلّل جيدًا أو زردي دواجن بمقدار 3-5 طن/هكتار، يخلط بالتربة قبل الزرع."},
            {"type": "chemical", "detail": "رشّ يوريا ورقي 2% لإعادة الخضرة السريعة حين يكون إصلاح التربة بطيئًا."},
        ],
        "irrigation": {
            "schedule": "Irrigate lightly right after fertilising so the nutrient reaches the roots.",
            "target": "60-70% field capacity at the time of application.",
            "note": "Calcareous Tébessa soils lock up micronutrients - balance N with a micronutrient mix.",
        },
        "irrigation_ar": {
            "schedule": "الِ بكمية خفيفة مباشرة بعد التسميد ليصل المغذّي إلى الجذور.",
            "target": "60-70% من سعة الحقل عند وقت التطبيق.",
            "note": "تربة تبسة الجيرية تحدّ من توافر العناصر الصغرى - وازن النيتروجين بمزيج من العناصر الصغرى.",
        },
        "prevention": [
            "Base dressing on a soil analysis rather than a fixed calendar.",
            "Add organic matter every season to improve nitrogen-holding capacity.",
        ],
        "prevention_ar": [
            "اعتمد التسميد على تحليل التربة لا على تقويم ثابت.",
            "أضف المادة العضوية كل موسم لرفع قدرة التربة على حجز النيتروجين.",
        ],
    },
    "healthy": {
        "title": "No pathology detected",
        "title_ar": "لا تظهر آفات على العينة",
        "pathogen": "-",
        "kind": "none",
        "actions": [
            "No treatment required. Continue the current programme.",
            "Keep scouting weekly - early detection is what keeps a plot healthy in Tébessa's climate.",
        ],
        "actions_ar": [
            "لا حاجة إلى علاج. واصلي البرنامج الحالي.",
            "واصلي الفحص الأسبوعي - فاكتشاف الإصابة مبكرًا هو ما يحافظ على سلامة الحقل في مناخ تبسة.",
        ],
        "treatments": [
            {"type": "cultural", "detail": "Maintain balanced fertilisation and weed control."},
            {"type": "organic", "detail": "Annual compost application (3-5 t/ha) sustains soil biology."},
        ],
        "treatments_ar": [
            {"type": "cultural", "detail": "حافظي على تسميد متوازن ومكافحة الأعشاب."},
            {"type": "organic", "detail": "إضافة سماد عضوي سنوي (3-5 طن/هكتار) يحافظ على حياة التربة."},
        ],
        "irrigation": {
            "schedule": "Drip, early morning, adjusted to the crop's growth stage.",
            "target": "60-75% field capacity depending on stage; reduce before harvest.",
            "note": "Deficit irrigation before harvest concentrates sugars in fruit and tree crops.",
        },
        "irrigation_ar": {
            "schedule": "ري بالتنقيط صباحًا، وفق مرحلة نمو المحصول.",
            "target": "60-75% من سعة الحقل حسب المرحلة؛ ويُقلَّل قبل الحصاد.",
            "note": "الري المحدود قبل الحصاد يركّز السكريات في الثمار والمحاصيل المثمرة.",
        },
        "prevention": [
            "Inspect new plantings and incoming material before adding them to the plot.",
            "Keep the plot tidy - debris and weeds harbour the next season's inoculum.",
        ],
        "prevention_ar": [
            "افحص المشتريات الجديدة والمواد الداخلة قبل إضافتها إلى الحقل.",
            "حافظ على نظافة الحقل - فالبقايا والأعشاب تختزن مسببات مرض الموسم القادم.",
        ],
    },
}

#: Fallback when nothing in the KB matches: a generic, still-useful plan.
_GENERIC_FALLBACK_KEY = "healthy"


def _chat_text(response: Any) -> str | None:
    """Pull the assistant text out of a Groq chat-completion object.

    Duck-typed so tests can hand in a plain stub. Returns ``None`` for an
    empty/unknown shape, which ``parse_model_json`` turns into a clean,
    retryable AgentError rather than an exception.
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
    if isinstance(content, list):
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                return text
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return part["text"]
    return None


class AdvisorAgent:
    """Builds localized treatment plans from a diagnostic report."""

    name = "advisor"

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    # -- infrastructure ----------------------------------------------------

    def _get_client(self) -> Any:
        """Build (once) the single-key Groq client.

        ``max_retries=0`` keeps Groq's internal retries off, so the only
        backoff in play is our own :func:`run_with_quota_retry`.
        """
        if self._client is not None:
            return self._client
        key = self.settings.api_key
        if not key:
            raise AgentError("auth", "GROQ_API_KEY is not configured.", retryable=False)
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

        ``media`` stays False: this agent sends text only, so a 400 can never
        be the farmer's fault. Before this was unified, this file simply had
        no 400 branch and fell through to a retryable error — correct by
        accident, and divergent from its two siblings.
        """
        return classify_http_error(
            exc,
            model_not_found=(
                f"Advisor model not found on Groq ({type(exc).__name__}). "
                "Update `advisor_model` in agents/config.py."
            ),
        )

    # -- public API --------------------------------------------------------

    def build_plan(self, diagnosis: dict[str, Any], *, language: str | None = None) -> AgentResult:
        """Turn a diagnostic report into a localized plan. Never raises.

        ``language`` selects the language of the narrative plan fields. The
        bilingual ``title`` / ``title_ar`` pair is filled in both scripts
        whatever is requested, because the UI shows them side by side.
        """
        diagnosis = as_dict(diagnosis)
        lang = normalise_language(language) or DEFAULT_LANGUAGE
        started = time.perf_counter()

        remote: dict[str, Any] | None = None
        remote_error: AgentError | None = None

        try:
            client = self._get_client()
        except AgentError as exc:
            remote_error = exc
            client = None

        if client is not None:
            def _generate() -> Any:
                # Plain text->JSON call; no image involved here.
                return client.chat.completions.create(
                    model=self.settings.advisor_model,
                    messages=[{"role": "user", "content": self._build_prompt(diagnosis, lang)}],
                    temperature=self.settings.temperature,
                    max_completion_tokens=self.settings.max_output_tokens,
                    response_format={"type": "json_object"},
                )

            try:
                raw = run_with_output_retry(
                    lambda: run_with_quota_retry(
                        _generate,
                        attempts=self.settings.quota_retry_attempts,
                        base_delay_s=self.settings.quota_retry_base_delay_s,
                    ),
                    attempts=self.settings.output_retry_attempts,
                    base_delay_s=self.settings.output_retry_base_delay_s,
                )
                remote = parse_model_json(_chat_text(raw))
            except GroqAPIError as exc:
                remote_error = self._classify_sdk_error(exc)
            except AgentError as exc:
                remote_error = exc
            except Exception as exc:
                remote_error = AgentError("busy", "Advisory generation timed out.", detail=str(exc)[:160], retryable=True)

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # --- choose the best available source -----------------------------
        kb_entry = self._match_kb(diagnosis)

        if remote is not None:
            merged = self._merge_remote(remote, kb_entry, diagnosis, lang)
            source = "groq"
        elif self.settings.advisor_fallback_enabled:
            merged = self._kb_plan(kb_entry, diagnosis, lang)
            source = "local_kb"
        else:
            # No plan available. Surface the *actual* classified failure
            # (e.g. the Arabic quota message) rather than a generic string —
            # the farmer needs to know why, and `retryable` must stay accurate.
            if remote_error is not None:
                return AgentResult.failure(remote_error)
            return AgentResult.failure(AgentError("api", "No advisory source available.", retryable=True))

        merged["meta"] = {
            "agent": self.name,
            "region": REGION,
            "region_ar": REGION_AR,
            "source": source,
            "latency_ms": elapsed_ms,
            "language": lang,
            "fallback_reason": None if source == "groq" else (remote_error.message if remote_error else "disabled"),
        }
        return AgentResult.success(merged)

    # -- KB helpers --------------------------------------------------------

    @staticmethod
    def _match_kb(diagnosis: dict[str, Any]) -> dict[str, Any]:
        """Pick the best local KB entry for a diagnosis."""
        disease = as_str(diagnosis.get("disease"), "").lower()
        pathology = as_str(diagnosis.get("pathology"), "").lower()
        blob = f"{disease} {pathology}"

        if not disease or diagnosis.get("healthy") is True:
            return LOCAL_KB["healthy"]

        for key, entry in LOCAL_KB.items():
            if key == _GENERIC_FALLBACK_KEY:
                continue
            if key in blob:
                return entry

        # keyword-level match as a second pass
        keyword_map = {
            "nitrogen": ("nitrogen", "chlorosis", "yellow", "ndeficiency", "n-deficiency"),
            "rust": ("rust", "puccinia", "pustule"),
            "powdery mildew": ("powdery", "mildew", "white"),
            "late blight": ("late blight", "infestans", "water-soaked"),
            "early blight": ("early blight", "alternaria", "target", "bull's-eye"),
        }
        for key, words in keyword_map.items():
            if any(word in blob for word in words):
                return LOCAL_KB[key]

        return LOCAL_KB[_GENERIC_FALLBACK_KEY]

    @staticmethod
    def _plan_field(entry: dict[str, Any], field: str, language: str) -> Any:
        """Read ``field`` from a KB entry, preferring its ``_ar`` twin for Arabic.

        Falling back to the English value when the Arabic twin is missing is
        deliberate: a partially translated entry must still produce a plan,
        and the model-written remote path is the one that fills real gaps.
        """
        if language == "ar":
            arabic = entry.get(f"{field}_ar")
            if isinstance(arabic, list) and arabic:
                return arabic
            if isinstance(arabic, dict) and arabic:
                return arabic
            if isinstance(arabic, str) and arabic.strip():
                return arabic
        return entry.get(field)

    def _kb_plan(self, entry: dict[str, Any], diagnosis: dict[str, Any], language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
        """Assemble a full plan purely from the local KB, in one language."""
        severity = as_str(diagnosis.get("severity"), "unknown")
        irrigation = as_dict(self._plan_field(entry, "irrigation", language)) or as_dict(entry.get("irrigation"))
        title_ar = as_str(entry.get("title_ar"), "توصية")

        return {
            "region": REGION,
            "region_ar": REGION_AR,
            # The pair stays bilingual on purpose: `title` follows the
            # requested language, `title_ar` is always Arabic, and the UI
            # renders both so a farmer can flip script without re-running.
            "title": as_str(self._plan_field(entry, "title", language), "Advisory"),
            "title_ar": title_ar,
            "crop": as_str(diagnosis.get("crop"), "Unknown"),
            "pathogen": as_str(entry.get("pathogen"), "-"),
            "kind": as_str(entry.get("kind"), "unknown"),
            "severity": severity,
            "actions": as_str_list(self._plan_field(entry, "actions", language)) or as_str_list(entry.get("actions")),
            "treatments": [
                {"type": as_str(t.get("type")), "detail": as_str(t.get("detail"))}
                for t in (self._plan_field(entry, "treatments", language) or [])
                if isinstance(t, dict)
            ],
            "irrigation_schedule": {
                "schedule": as_str(irrigation.get("schedule"), "Drip, early morning."),
                "target": as_str(irrigation.get("target"), ""),
                "note": as_str(irrigation.get("note"), ""),
            },
            "prevention": as_str_list(self._plan_field(entry, "prevention", language)) or as_str_list(entry.get("prevention")),
            "regional_context": REGION_CONTEXT_AR if language == "ar" else REGION_CONTEXT_EN,
            "disclaimer": _DISCLAIMER_AR if language == "ar" else _DISCLAIMER_EN,
        }

    def _merge_remote(
        self,
        remote: dict[str, Any],
        kb_entry: dict[str, Any],
        diagnosis: dict[str, Any],
        language: str = DEFAULT_LANGUAGE,
    ) -> dict[str, Any]:
        """Prefer the model's content, but never lose the KB's safety net."""
        kb_plan = self._kb_plan(kb_entry, diagnosis, language)

        def pick(key: str, fallback_key: str) -> Any:
            value = remote.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value:
                cleaned = as_str_list(value)
                if cleaned:
                    return cleaned
            return kb_plan[fallback_key]

        treatments = remote.get("treatments")
        if isinstance(treatments, list) and treatments:
            cleaned = [
                {"type": as_str(t.get("type"), "cultural"), "detail": as_str(t.get("detail"))}
                for t in treatments
                if isinstance(t, dict) and as_str(t.get("detail"))
            ]
            treatments = cleaned if cleaned else kb_plan["treatments"]
        else:
            treatments = kb_plan["treatments"]

        irrigation = remote.get("irrigation_schedule")
        irrigation = irrigation if isinstance(irrigation, dict) else {}
        kb_irrig = as_dict(kb_plan.get("irrigation_schedule"))

        # `title_ar` must stay Arabic even when the model was told to write
        # its `title` in another script; reject a foreign-language value.
        title_ar = as_str(remote.get("title_ar"), "").strip()
        if not title_ar or not has_arabic(title_ar):
            title_ar = kb_plan["title_ar"]

        return {
            "region": REGION,
            "region_ar": REGION_AR,
            "title": pick("title", "title"),
            "title_ar": title_ar,
            "crop": as_str(diagnosis.get("crop"), "Unknown"),
            "pathogen": pick("pathogen", "pathogen"),
            "kind": pick("kind", "kind"),
            "severity": as_str(diagnosis.get("severity"), "unknown"),
            "actions": pick("actions", "actions"),
            "treatments": treatments,
            "irrigation_schedule": {
                "schedule": as_str(irrigation.get("schedule"), kb_irrig.get("schedule", "")),
                "target": as_str(irrigation.get("target"), kb_irrig.get("target", "")),
                "note": as_str(irrigation.get("note"), kb_irrig.get("note", "")),
            },
            "prevention": pick("prevention", "prevention"),
            "regional_context": REGION_CONTEXT_AR if language == "ar" else REGION_CONTEXT_EN,
            "disclaimer": _DISCLAIMER_AR if language == "ar" else _DISCLAIMER_EN,
        }

    # -- prompt ------------------------------------------------------------

    @staticmethod
    def _build_prompt(diagnosis: dict[str, Any], language: str = DEFAULT_LANGUAGE) -> str:
        compact = json.dumps(
            {
                "status": diagnosis.get("status"),
                "healthy": diagnosis.get("healthy"),
                "crop": diagnosis.get("crop"),
                "disease": diagnosis.get("disease"),
                "disease_ar": diagnosis.get("disease_ar"),
                "pathogen": diagnosis.get("pathogen"),
                "symptoms": diagnosis.get("symptoms"),
                "severity": diagnosis.get("severity"),
                "severity_pct": diagnosis.get("severity_pct"),
                "confidence": diagnosis.get("confidence"),
            },
            ensure_ascii=False,
        )
        return f"""\
You are an agronomist specialised in the {REGION} region ({REGION_AR}) - a
semi-arid high plateau (~300-400 mm rainfall, calcareous soils, ~pH 8) whose
main crops are durum wheat, barley, olive, almond, fig, pepper and potato.

Given this crop-leaf diagnosis as JSON:
{compact}

Return ONLY a JSON object (no markdown fences) with EXACTLY this shape:
{{
  "title": "...", "title_ar": "...", "pathogen": "...", "kind": "...",
  "actions": ["..."],
  "treatments": [{{"type": "cultural|organic|chemical", "detail": "..."}}, ...],
  "irrigation_schedule": {{"schedule": "...", "target": "...", "note": "..."}},
  "prevention": ["..."]
}}

{reply_instruction(language)}

Rules:
- Write "title", "actions", "treatments", "irrigation_schedule" and
  "prevention" in __LANG_NAME__, because that is the language the farmer is
  reading. Always write "title_ar" in natural Arabic whatever the above says -
  the two titles are shown together in the app.
- Localize everything to Tébessa: water-saving drip irrigation, morning-only
  watering, deficit strategies, calcareous-soil constraints, local wind.
- Give real product names with real doses where a chemical is warranted, but
  always prefer cultural and organic controls first. Keep product names and
  active ingredients in Latin script even in Arabic output, since that is how
  they appear on the label.
- If "status" is "healthy" or "healthy" is true, return a PREVENTION-ONLY plan:
  no chemical treatment, no curative dose. It is a healthy plant, so the plan
  is scouting, balanced nutrition and irrigation hygiene.
- Never invent a dose. If you are not confident of a rate, say so in the note
  rather than guessing.
""".replace("__LANG_NAME__", LANG_NAME.get(language, LANG_NAME[DEFAULT_LANGUAGE]))


__all__ = ["AdvisorAgent", "LOCAL_KB", "REGION", "REGION_AR"]
