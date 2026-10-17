"""AdvisorAgent — Tébessa-localized treatment planning + local chat.

v4 — 100% local autonomy (no external APIs):

* :meth:`AdvisorAgent.build_plan` — maps the DiagnosticAgent's measured
  verdict (quantitative metrics + condition id) onto the public
  ``TreatmentPlan`` using the **Tébessa agronomy knowledge base** below:
  treatments, irrigation strategy and prevention protocols tuned for
  the semi-arid steppe climate of Tébessa, Algeria (≈350 mm annual
  rainfall, 40 °C dry summers, calcareous soils). Rows are *properly
  bilingual* (real Arabic + English), never mirrored placeholders. The
  disease name and confidence always come from the diagnosis — the KB
  never invents a verdict.
* :meth:`AdvisorAgent.advisory_extras` — the richer regional context
  block (climate, soil, irrigation schedule, seasonal calendar) served
  by ``POST /api/v1/analyze``.
* :meth:`AdvisorAgent.stream_advice` / :meth:`answer_once` — the chat
  assistant: pure retrieval over the local knowledge base
  (``mode="local_kb"``). No model call, no network, no rate limit; the
  chatbot always answers from what the DB actually knows.

English-first formatting standard: keys/statuses/messages are English;
bilingual schema rows carry real Arabic alongside English.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.agents import knowledge
from app.core.config import get_settings
from app.models.chatbot import KnowledgeBase
from app.schemas.diagnosis import TreatmentPlan

logger = logging.getLogger("hassad.agents.advisor")

#: Chat answer mode reported in SSE/JSON (local KB retrieval is now the
#: *primary* mode, not a fallback — there is no remote model at all).
CHAT_MODE = "local_kb"


# ======================================================================
# Tébessa agronomy knowledge base
# ======================================================================
_TEBESSA: dict[str, str] = {
    "region": "Tébessa, Algeria (semi-arid steppe)",
    "climate_en": (
        "Semi-arid continental steppe: ~350 mm annual rainfall, very hot dry "
        "summers (40 °C+), cold winters with occasional frost, dominant dry "
        "winds. Rain-fed cereals dominate; vegetables and orchards rely on "
        "borehole/dam irrigation."
    ),
    "climate_ar": (
        "سهوب شبه جافة قارية: هطول سنوي ≈ 350 مم، صيف حار جاف (40°+ م)، "
        "شتاء بارد مع صقيع أحياناً، ورياح جافة سائدة. الحبوب تعتمد على المطر "
        "والخضروات والبساتين على ري الآبار والسدود."
    ),
    "soil_en": (
        "Calcareous steppe soils, often shallow and low in organic matter; "
        "salinity risk on borehole-irrigated perimeters. Build soil with "
        "compost + legume rotations before inputs."
    ),
    "soil_ar": (
        "تربة سهوب جيرية ضحلة عادةً وقليلة المادة العضوية؛ وخطر ملوحة على "
        "مساحات ري الآبار. حسّن التربة بالسماد العضوي والدورة البقلية قبل "
        "المواد الكيميائية."
    ),
}

_DISCLAIMER = {
    "en": (
        "Automated guidance from the Hassad local engine — translate it with "
        "your local DPA (Direction de la Protection des Végétaux, Tébessa) or "
        "Chambre d'Agriculture before applying any product. Follow the label."
    ),
    "ar": (
        "إرشاد آلي من محرك حَصاد المحلي — ترجمه مع مصالح حماية النبات بتبسة "
        "أو غرفة الفلاحة قبل استعمال أي منتج. اتبع البطاقة التقنية."
    ),
}

# Per-condition plan rows (bilingual). Keyed by the diagnostic
# condition_id; index 0 = English, index 1 = Arabic.
_PLAN_KB: dict[str, dict[str, list | dict]] = {
    "early_blight": {
        "summary": (
            "Early blight pressure detected on {crop} — protect the remaining "
            "green leaf area, break the infection cycle and keep foliage dry.",
            "تم رصد ضغط اللفحة المبكرة على {crop} — احمِ ما تبقّى من اللون "
            "الأخضر، واقطع دورة العدوى وأبقِ الأوراق جافة.",
        ),
        "immediate": [
            ("Remove and bag the most affected lower leaves (do not compost in place).",
             "أزل الأوراق الأكثر إصابة من الأسفل واحرقها أو دفنها (لا تتكسّرها في الحقل)."),
            ("Stop overhead irrigation; water at the root in the morning only.",
             "أوقف الري العلوي؛ اسقِ الجذر صباحاً فقط."),
            ("Apply a copper-based contact spray (Bordeaux mixture) as a first shield, "
             "ideally after a dry day.",
             "رشّ معدني نحاسي (خليط بوردو) كحاجز أولي، ويفضل بعد يوم جاف."),
        ],
        "cultural": [
            ("Mulch the soil surface to stop rain-splash spores reaching lower leaves.",
             "غطِّ سطح التربة لمنع قفز البذور الفطرية بالمطر إلى الأوراق السفلية."),
            ("Widen plant spacing and prune suckers for airflow (less leaf wetness).",
             "وسّع الفراغات بين النباتات واحصد السيقان الجانبية لتهوية أفضل ( أقل تبلل للأوراق)."),
            ("Rotate with a non-host (cereals/onion) for 2–3 seasons; Tébessa fields "
             "often carry Alternaria in residues.",
             "بدّل الدورة مع محاصيل غير مضيفة (حبوب/بصل) لمدة 2–3 مواسم؛ أحزمة تبسة تحمل "
             "فطر الAlternaria في بقايا المحصول."),
        ],
        "organic": [
            ("Garlic/neem extract sprays (weekly, evening) as a soft-site protectant.",
             "رذاذ الثوم/النيم (أسبوعياً مساءً) لحماية المواقع الرقيقة."),
            ("Compost tea foliar feeding to outcompete weak pathogens on the leaf surface.",
             "رش عصير السماد العضوي لإزاحة الممرضات الضعيفة على سطح الورقة."),
        ],
        "chemical": [
            ("Mancozeb 80% WP (e.g. Dithane) — 2.5 g/L, 7-day interval, max 3 sprays/season.",
             "مانكوزيب 80% و.ب (دايثين) — 2.5 غ/ل، فترة 7 أيام، 3 رشات كحد أقصى للموسم."),
            ("Chlorothalonil 75% WP — 2 g/L if mancozeb resistance is suspected.",
             "كلوروتالونيل 75% و.ب — 2 غ/ل عند الاشتباه في مقاومة المانكوزيب."),
            ("Azoxystrobin 25% SC — 1.5 mL/L, rotate modes of action (anti-resistance).",
             "أوكسيستروبين 25% م — 1.5 مل/ل، بدّل آليات التأثير (مانعة للمقاومة)."),
        ],
        "monitoring": [
            ("Scout lower canopy weekly; count lesions on 20 tagged leaves.",
             "افحص الطبقة السفلية أسبوعياً؛ اعُد بقع على 20 ورقة معلّمة."),
            ("Risk rises with leaf wetness >6 h after rain — spray the window, not the calendar.",
             "الخطر يرتفع مع تبلل الأوراق >6 ساعات بعد المطر — رشّ عند النافذة لا حسب التقويم."),
        ],
        "prevention": [
            ("Desiccated debris burial after harvest (Alternaria survives in residues).",
             "ادفن بقايا المحصول بعد الجفاف (فطر Alternaria ينجو في البقايا)."),
            ("Balanced nitrogen — excess soft growth attracts the disease.",
             "توازن النيتروجين — الإفراط في النمو الرقيق يجذب المرض."),
            ("Copper pre-emptive spray at first humid cool spell (Tébessa spring).",
             "رش نحاسي وقائي عند أول فترة رطوبة باردة (ربيع تبسة)."),
        ],
    },
    "late_blight": {
        "summary": (
            "Late-blight-type collapse on {crop} — act within 24 h: remove "
            "infected tissue, protect the rest, and avoid wetting the canopy.",
            "انسجام نسيجي من نوع اللفحة المتأخرة على {crop} — تصرّف خلال 24 ساعة: "
            "أزل الأنسجة المصابة، واحمِ الباقي، وتجنّب ترطيب المظلة الورقية.",
        ),
        "immediate": [
            ("Remove and destroy infected foliage immediately (bag it out of the field).",
             "أزل الأوراق المصابة فوراً وأتلفها (أخرجها من الحقل مغلّفة)."),
            ("Stop all overhead irrigation; water at the root in the morning.",
             "أوقف كل الري العلوي؛ اسقِ الجذر صباحاً."),
            ("Apply a systemic anti-oomycete (metalaxyl/mefenoxam class) + contact "
             "partner the same day.",
             "طبّق نظامياً مضاداً للأومايسيت (الميتالاكسيل/ميفينوكسام) مع ملامس في نفس اليوم."),
        ],
        "cultural": [
            ("Destroy volunteer solanaceae and nightshade volunteers around field borders.",
             "أتلف بقايا النباتات المنذرة والبنجميات حول حدود الحقل."),
            ("High ridge planting + drip lines to keep fruit/leaves dry.",
             "زراعة على تلال مرتفعة وخطوط تنقيط لتبقى الأوراق والثمار جافة."),
            ("Harvest/haul in dry hours; wet tubers spread the oomycete in storage.",
             "اجني في الساعات الجافة؛ البلّح المبلّل ينشر المرض في المخزن."),
        ],
        "organic": [
            ("Copper hydroxide + rye flour elicitor programs (EU-organic style) ahead of "
             "risk periods.",
             "برنامج هيدروكسيد النحاس مع حافز الطحين الجاودار قبل فترات الخطر."),
        ],
        "chemical": [
            ("Metalaxyl-M + Mancozeb (e.g. Ridomil Gold MZ) — 2 g/L, max 3 sprays/season.",
             "ميتالاكسيل-م + مانكوزيب (ريدوميل جولد) — 2 غ/ل، 3 رشات كحد أقصى."),
            ("Famoxadone + Cymoxanil (e.g. Tanos) — 1.5 g/L, rotate after 2 uses.",
             "فاموكсадون + سيموكسانيل (تانوس) — 1.5 غ/ل، بدّل بعد استعمالين."),
        ],
        "monitoring": [
            ("Weather-trigger scouting: any rain + night humidity >80% → inspect within 24 h.",
             "تفقّد محفّز بالطقس: أي مطر + رطوبة ليلية >80% → افحص خلال 24 ساعة."),
        ],
        "prevention": [
            ("Certified seed/tubers — the oomycete travels in planting material.",
             "بذور/بطاطس معتمدة — الممرض ينتقل مع المادة البذرية."),
            ("Copper pre-emptive program at the first cool humid forecast.",
             "برنامج نحاسي وقائي عند أول تنبؤ بجو بارد رطب."),
        ],
    },
    "septoria_leaf_spot": {
        "summary": (
            "Septoria-type leaf spotting on {crop} — protect upper canopy and "
            "slow splash-spread.",
            "بقع دائرية من نوع سبتوريا على {crop} — احمِ الطبقة العلوية وبلّغ "
            "انتشار القفز بالمطر.",
        ),
        "immediate": [
            ("Remove the most spotted leaves and mulch between rows.",
             "أزل الأوراق الأكثر بقعاً وغطِّ المسافات بين الصفوف."),
            ("Apply a protectant fungicide (mancozeb/copper) before the next rain event.",
             "طبّق مانع فطري (مانكوزيب/نحاس) قبل موجة المطر القادمة."),
        ],
        "cultural": [
            ("Residue burial + rotation breaks the pycnidia cycle.",
             "دفن البقايا + الدورة يقطعان دورة الأجسام الفطرية."),
            ("Avoid working wet foliage (tools spread pycnidia).",
             "تجنّب العمل على أوراق مبلّلة (الأدوات تنشر الأجسام الفطرية)."),
        ],
        "chemical": [
            ("Thiophanate-methyl 70% WP — 1 g/L (systemic).",
             "ثيوفانات-ميثيل 70% و.ب — 1 غ/ل (نظامي)."),
            ("Chlorothalonil 75% WP — 2 g/L (contact, anti-resistance rotation).",
             "كلوروتالونيل 75% و.ب — 2 غ/ل (ملامس، للتدوير المقاوم)."),
        ],
        "monitoring": [
            ("Count pycnidia-spotted leaves weekly on 20 tagged leaves.",
             "عُد الأوراق المصابة أسبوعياً على 20 ورقة معلّمة."),
        ],
        "prevention": [
            ("Clean seed lots and buried residues reduce the carry-over inoculum.",
             "تنظيف البذور ودفن البقايا يقلّل مخزون العدوى."),
        ],
    },
    "leaf_rust": {
        "summary": (
            "Rust pustules on {crop} — curb the epidemic before flag-leaf "
            "damage cuts yield.",
            "بثّات صدأ على {crop} — اكبح الوباء قبل أن تضرّ الورقة العلمية بالإنتاج.",
        ),
        "immediate": [
            ("Apply a triazole (propiconazole/tebuconazole) at first pustule check.",
             "طبّق ثلاثي الزجاج (بروبيكونازول/تيبوكونازول) عند أول ظهور للبثّات."),
        ],
        "cultural": [
            ("Resistant varieties adapted to the steppe belt are the cheapest control.",
             "الأصناف المقاومة الملائمة للحزام السهبي أرخص حل."),
            ("Early sowing escapes the peak rust window in Tébessa.",
             "البذر المبكر يتجاوز ذروة الصدأ في تبسة."),
        ],
        "chemical": [
            ("Propiconazole 25% EC — 0.5 mL/L.",
             "بروبيكونازول 25% ك — 0.5 مل/ل."),
            ("Tebuconazole 250 g/L OD — 0.75 mL/L (rotate with propiconazole).",
             "تيبوكونازول 250 غ/ل — 0.75 مل/ل (بدّل مع البروبيكونازول)."),
        ],
        "monitoring": [
            ("Walk the field twice weekly at stem extension; pustules double fast.",
             "اجتَز الحقل مرتين أسبوعياً في مرحلة استطال الساق؛ البثّات تتضاعف بسرعة."),
        ],
        "prevention": [
            ("Volunteer wheat/barley control near canals (green bridge for rust).",
             "أتلف الحشائش المذرة من القمح/الشعير قرب القنوات (الجسر الأخضر للصدأ)."),
        ],
    },
    "powdery_mildew": {
        "summary": (
            "Powdery mildew colonization on {crop} — treat early: the fungus "
            "spreads in warm DRY air (typical Tébessa summer pressure).",
            "استعمار مِسْبِق دقيقي على {crop} — عالج مبكراً: الفطر ينتشر في الهواء "
            "الحار الجاف (ضغط صيف تبسة المعتاد).",
        ),
        "immediate": [
            ("Sulfur-based spray (wettable sulfur) at the first white patches.",
             "رشّ كبريت (كبريت مبلل) عند أول بقع بيضاء."),
            ("Prune dense growth to open the canopy.",
             "قشّر النمو الكثيف لتهوية المظلة."),
        ],
        "cultural": [
            ("Balanced irrigation — drought stress worsens mildew susceptibility.",
             "ري متوازن — إجهاد الجفاف يزيد استعداد للإصابة."),
            ("Sanitize pruning tools between vines/trees.",
             "تعقيم أدوات التقليم بين الجذوع/الكروم."),
        ],
        "chemical": [
            ("Sulfur 80% WG — 3 g/L (evening application; heat-sensitive crops: check label).",
             "كبريت 80% و.غ — 3 غ/ل (رش مساءً؛ على المحاصيل الحساسة للحرارة اتبع البطاقة)."),
            ("Myclobutanil 25% EC — 0.5 mL/L for grapes/stone fruit.",
             "ميكلوبيوتانيل 25% ك — 0.5 مل/ل للعنب وحبوب اللوز."),
        ],
        "monitoring": [
            ("Inspect shoot tips and young leaves twice weekly — mildew starts there.",
             "افحص نهايات السيقان والأوراق الشابة مرتين أسبوعياً — الدقيقي يبدأ هناك."),
        ],
        "prevention": [
            ("Remove mummified clusters/fruits (carry-over inoculum).",
             "أزل الثمار الذابلة (مخزون العدوى)."),
            ("Sulfur pre-emptive program at berry set for grapes.",
             "برنامج كبريت وقائي عند تشكل الحبّات للعنب."),
        ],
    },
    "nitrogen_deficiency": {
        "summary": (
            "Nitrogen hunger on {crop} — generalized yellowing without "
            "pathogen spotting; Tébessa calcareous soils lose N fast.",
            "نقص النيتروجين على {crop} — اصفرار عام بلا بقع ممرضات؛ التربة الجيرية "
            "في تبسة تفقد النيتروجين بسرعة.",
        ),
        "immediate": [
            ("Side-dress with ammonium sulfate or urea (split doses; irrigate after).",
             "أضف سماداً منكبرياً أو يوريا (بجرعات مقسّمة؛ اسقِ بعدها)."),
            ("Foliar urea 1–2% as a quick green-up if roots are stressed.",
             "ورقي باليوريا 1–2% كتحمية سريعة إذا كانت الجذور مجهدة."),
        ],
        "cultural": [
            ("Legume rotation (faba bean, vetch, medick) fixes free N for the next crop.",
             "دورة بقلية (فول، جثجاث، برسيم) تثبّت نيتروجين مجاني للمحصول التالي."),
            ("Compost incorporation before sowing raises baseline N + water retention.",
             "إدخال السماد العضوي قبل البذر يرفع قاعدة النيتروجين واحتفاظ التربة بالماء."),
        ],
        "chemical": [
            ("Ammonium sulfate 21% — 25–40 kg/da split at tillering + booting.",
             "كبريت الأمونيوم 21% — 25–40 كغ/هـ مقسّمة عند التفرع وامتلاء السنابل."),
        ],
        "monitoring": [
            ("Track leaf greenness (chlorophyll meter or visual chart) every 2 weeks.",
             "تابع اخضرار الأوراق (مقياس الكلوروفيل أو مخطط بصري) كل أسبوعين."),
        ],
        "prevention": [
            ("Soil test every 2 seasons — Tébessa soils vary strongly by locality.",
             "تحليل تربة كل موسمين — تربة تبسة تتباين كثيراً حسب الجهة."),
        ],
    },
    "potassium_deficiency": {
        "summary": (
            "Marginal burn on {crop} — potassium deficit (calcareous soils "
            "lock K); expect weaker filling and shelf life.",
            "احتراق محيطي على {crop} — نقص البوتاسيوم (الجير يحبس البوتاسيوم)؛ "
            "متوقع ضعف الامتلاء والحفظ.",
        ),
        "immediate": [
            ("Potassium sulfate (SOP) side-dress — 15–25 kg/da, avoid NaCl-type K.",
             "أضف كبريت البوتاسيوم — 15–25 كغ/هـ، وتجنّب مصادر البوتاسيوم الملحية."),
        ],
        "cultural": [
            ("Return all crop residues (most of the K sits in straw/leaves).",
             "أعد كل بقايا المحصول (معظم البوتاسيوم في القش/الأوراق)."),
            ("Mulch + organic matter improve K availability in calcareous soils.",
             "التغطية والمادة العضوية تحسنان إتاحة البوتاسيوم في الترب الجيرية."),
        ],
        "chemical": [
            ("Potassium sulfate 50% — foliar 0.5% as rescue during fruiting.",
             "كبريت البوتاسيوم 50% — ورقي 0.5% كإنقاذ أثناء الإثمار."),
        ],
        "monitoring": [
            ("Watch margins of the oldest leaves first (K remobilizes to young tissue).",
             "راقب حواف الأوراق الأقدم أولاً (البوتاسيوم ينتقل للأنسجة الشابة)."),
        ],
        "prevention": [
            ("Maintain organic matter — the K bank of steppe soils.",
             "حافظ على المادة العضوية — بنك البوتاسيوم في ترب السهوب."),
        ],
    },
    "healthy_leaf": {
        "summary": (
            "{crop} foliage measures healthy — keep the preventive program; "
            "no treatment needed today.",
            "أوراق {crop} تُقاس سليمة — واصِل البرنامج الوقائي؛ لا حاجة لعلاج اليوم.",
        ),
        "immediate": [
            ("No treatment needed today; keep scouting on schedule.",
             "لا حاجة لعلاج اليوم؛ واصِل الفحص حسب الجدول."),
        ],
        "cultural": [
            ("Maintain field hygiene and balanced nutrition.",
             "حافظ على نظافة الحقل والتغذية المتوازنة."),
        ],
        "chemical": [],
        "monitoring": [
            ("Re-scout weekly; report any new spotting early.",
             "افحص أسبوعياً؛ أبلغ عن أي بقع جديدة مبكراً."),
        ],
        "prevention": [
            ("Preventive copper at the first humid cool spell (spring risk window).",
             "نحاس وقائي عند أول فترة رطوبة باردة (نافذة خطر الربيع)."),
        ],
    },
    "uncertain_condition": {
        "summary": (
            "The pattern is not conclusive — the case was queued for "
            "agronomist review; re-shoot if symptoms are progressing.",
            "النمط غير قاطع — أُحيلت الحالة للمراجعة الفلاحية؛ أعد التصوير إذا كانت "
            "الأعراض تتقدم.",
        ),
        "immediate": [
            ("Retake: single leaf, flat, bright indirect daylight, plain background.",
             "أعد التصوير: ورقة واحدة، مسطّحة، ضوء غير مباشر ساطع، خلفية محايدة."),
        ],
        "cultural": [],
        "chemical": [],
        "monitoring": [
            ("Compare old vs new photos to see if the pattern is progressing.",
             "قارن الصور القديمة والجديدة لمتابعة تقدّم النمط."),
        ],
        "prevention": [
            ("Keep general field hygiene while the case awaits review.",
             "حافظ على نظافة الحقل أثناء انتظار مراجعة الحالة."),
        ],
    },
}

_FALLBACK_PLAN = _PLAN_KB["uncertain_condition"]

#: Severity add-ons (English, Arabic) — appended to immediate actions.
_SEVERITY_BOOST: dict[str, list[tuple[str, str]]] = {
    "critical": [
        ("CRITICAL: isolate the plot tools/shoes between rows and remove heavily "
         "infected plants entirely to slow spread.",
         "حرج: عزل الأدوات والأحذية بين الصفوف وإزالة النباتات المصابة بشدة كاملاً "
         "لتبطئة الانتشار."),
    ],
    "moderate": [
        ("Moderate damage: prioritize protecting the healthy upper canopy this week.",
         "ضرر متوسط: ركّز على حماية الطبقة العلوية السليمة هذا الأسبوع."),
    ],
    "early": [],
    "none": [],
}

# Irrigation guidance by crop group (Tébessa semi-arid water budget).
_IRRIGATION = {
    "cereals": {
        "en": ("Rain-fed in Tébessa; one supplemental irrigation at tillering/booting "
               "if spring rains fail (≈30–40 mm each). Avoid late irrigation that "
               "prolongs canopy humidity and rust pressure."),
        "ar": ("في تبسة تعتمد الحبوب على المطر؛ ريّ مساعد واحد عند التفرع وامتلاء "
               "السنابل إذا خانق الربيع (≈30–40 مم لكل واحد). تجنّب الري المتأخر "
               "الذي يطيل رطوبة المظلة ويزيّد ضغط الصدأ."),
    },
    "vegetables": {
        "en": ("Drip irrigation, early morning only; keep ~350–500 mm seasonal budget "
               "by crop stage. Mulch heavily to halve evaporation. Never irrigate "
               "in the evening (leaf wetness drives blight)."),
        "ar": ("ري تنقيط صباحاً فقط؛ تحكّم في 350–500 مم موسمياً حسب مرحلة المحصول. "
               "غطِّ التربة لتقليل التبخّر النصف. لا تُسقِ مساءً (تبلل الأوراق يحرّض اللفحة)."),
    },
    "orchards": {
        "en": ("Deficit irrigation after fruit set (Regulated Deficit Irrigation style) "
               "saves water and concentrates sugars; one deep pre-harvest irrigation, "
               "then dry-down. Check borehole salinity yearly."),
        "ar": ("ريّ ناقص مقصود بعد تثمير التمرير (RDI) يوفّر الماء ويركّز السكريات؛ ريّ "
               "عميق واحد قبل الحصاد ثم الجفاف. افحص ملوحة البئر سنوياً."),
    },
}
_CROP_GROUP = {
    "wheat": "cereals", "barley": "cereals", "rice": "cereals", "maize": "cereals",
    "tomato": "vegetables", "potato": "vegetables", "pepper": "vegetables",
    "eggplant": "vegetables", "soybean": "vegetables", "cotton": "vegetables",
    "grape": "orchards", "olive": "orchards", "date-palm": "orchards",
    "almond": "orchards",
}


def _one_line(value: Any, limit: int = 300) -> str:
    """Collapse a payload string to one clean trimmed line."""
    text = " ".join(str(value or "").split())
    return text[:limit]

#: Tébessa seasonal calendar (bilingual rows).
_CALENDAR: list[dict[str, str]] = [
    {"period": "Sep–Nov", "en": "Cereal sowing window; treat seed against loose smut.",
     "ar": "نافذة بذر الحبوب؛ عالج البذور من التخرّخ."},
    {"period": "Dec–Feb", "en": "Cold dry rest; scouts watch for powdery mildew starts.",
     "ar": "راحة باردة جافة؛ الفحص يراقب بدايات البياض الدقيقي."},
    {"period": "Mar–May", "en": "Peak disease window: blight/rust/septoria after rains; "
     "copper pre-emptive program.",
     "ar": "ذروة الأمراض: لفحة/صدأ/سبتوريا بعد الأمطار؛ برنامج نحاسي وقائي."},
    {"period": "Jun–Aug", "en": "Hot dry pressure: mildew + spider mite; morning drip only.",
     "ar": "ضغط حار جاف: دقيقي + عنكبوت؛ ري تنقيط صباحي فقط."},
]


class AdvisorAgent:
    """Advisory stage: Tébessa-localized plans + local KB chat answers."""

    name = "advisor"

    # ==================================================================
    # Treatment plans (Crop Doctor pipeline)
    # ==================================================================
    def build_plan(self, analysis: dict[str, Any], crop_label: str | None) -> TreatmentPlan:
        """Map a local diagnostic verdict onto the public ``TreatmentPlan``.

        Sections come from the Tébessa KB keyed by ``condition_id``; the
        measured symptoms and the confidence always come from the
        diagnosis — the KB never invents a verdict.
        """
        cond_id = str(analysis.get("condition_id") or "")
        crop = (crop_label or "plant").strip() or "plant"
        kb = _PLAN_KB.get(cond_id, _FALLBACK_PLAN)
        severity = str(analysis.get("severity") or "none")

        summary = (kb["summary"][0].format(crop=crop), kb["summary"][1].format(crop=crop))
        immediate = list(kb["immediate"]) + _SEVERITY_BOOST.get(severity, [])
        cultural = list(kb["cultural"])
        organic = list(kb.get("organic", []))
        chemical = list(kb.get("chemical", []))
        monitoring = list(kb["monitoring"])
        prevention = list(kb["prevention"])

        irrigation = self._irrigation_row(crop_label)
        if irrigation is not None and severity != "none":
            monitoring.append(irrigation)

        options: list[dict[str, Any]] = []
        for text in cultural:
            options.append({"type": "cultural", "action": {"en": text[0], "ar": text[1]}})
        for text in organic:
            options.append({"type": "organic", "action": {"en": text[0], "ar": text[1]}})
        for text in chemical:
            product, _, usage = text[0].partition(" — ")
            options.append({
                "type": "chemical",
                "action": {"en": text[0], "ar": text[1]},
                "notes": {"en": product, "ar": usage or product},
            })

        symptoms = [
            {"en": _one_line(row.get("en")), "ar": _one_line(row.get("ar") or row.get("en"))}
            for row in (analysis.get("symptoms") or [])
            if isinstance(row, dict) and (row.get("en") or row.get("ar"))
        ]
        if not symptoms:
            symptoms = [{"en": summary[0], "ar": summary[1]}]

        return TreatmentPlan(
            summary={"en": summary[0], "ar": summary[1]},
            immediate_actions=[{"en": t[0], "ar": t[1]} for t in immediate],
            control_options=options,
            monitoring=[{"en": t[0], "ar": t[1]} for t in monitoring],
            prevention=[{"en": t[0], "ar": t[1]} for t in prevention],
            symptoms=symptoms,
            disclaimer=_DISCLAIMER,
            source="local_kb:tebessa_v1",
        )

    def advisory_extras(self, analysis: dict[str, Any], crop_label: str | None) -> dict[str, Any]:
        """Richer regional context served by ``POST /api/v1/analyze``."""
        crop = (crop_label or "").strip()
        group = _CROP_GROUP.get(crop.lower(), "vegetables")
        severity = str(analysis.get("severity") or "none")
        irrigation = _IRRIGATION[group]
        return {
            "region": _TEBESSA["region"],
            "climate": {"en": _TEBESSA["climate_en"], "ar": _TEBESSA["climate_ar"]},
            "soil": {"en": _TEBESSA["soil_en"], "ar": _TEBESSA["soil_ar"]},
            "irrigation": {"group": group, "en": irrigation["en"], "ar": irrigation["ar"]},
            "severity": severity,
            "seasonal_calendar": _CALENDAR,
            "disclaimer": _DISCLAIMER,
        }

    @staticmethod
    def _irrigation_row(crop_label: str | None) -> tuple[str, str] | None:
        group = _CROP_GROUP.get((crop_label or "").strip().lower())
        if group is None:
            return None
        row = _IRRIGATION[group]
        return (f"Irrigation: {row['en']}", f"الري: {row['ar']}")

    # ==================================================================
    # Conversational advice (chatbot) — local retrieval, always answers
    # ==================================================================
    async def stream_advice(
        self,
        *,
        question: str,
        docs: Sequence[KnowledgeBase],
        crop_context: dict[str, Any] | None,
        history: Sequence[dict[str, str]],
        lang: str,
        temperature: float | None = None,
        mode_out: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Yield the chat answer from the local knowledge base (offline).

        v4: there is no remote model — the answer is the best available
        local-KB guidance, streamed chunk by chunk so the SSE contract
        is unchanged. ``mode_out["mode"]`` is always ``"local_kb"``.
        """
        if mode_out is not None:
            mode_out["mode"] = CHAT_MODE
        _ = (crop_context, history, temperature)  # kept for wire compat

        intros = {
            "ar": "إليك أقرب الإرشادات من قاعدة معرفة حَصاد المحلية:\n\n",
            "en": "Here is the closest guidance from the Hassad local knowledge base:\n\n",
        }
        yield intros.get(lang, intros["en"])
        cards = knowledge.citation_cards(docs)
        if cards:
            for card in cards:
                yield f"[{card['index']}] {card['title']}: {card['snippet']}\n"
        else:
            empty = {
                "ar": ("لم أجد إجابة مباشرة في القاعدة المحلية لسؤالك. جرّب ذكر المحصول "
                       "والمرض (مثال: صدأ القمح، لفحة الطماطم، ريّ الطابق الهضابي)."),
                "en": ("No direct hit in the local KB for that question. Try naming the "
                       "crop and the problem (example: wheat rust, tomato blight, "
                       "steppe irrigation)."),
            }
            yield empty.get(lang, empty["en"])

    async def answer_once(
        self,
        *,
        question: str,
        docs: Sequence[KnowledgeBase],
        crop_context: dict[str, Any] | None,
        history: Sequence[dict[str, str]],
        lang: str,
        temperature: float | None = None,
        mode_out: dict[str, str] | None = None,
    ) -> str:
        """Non-streaming helper (used by ``/chatbot/ask`` and by tests)."""
        parts: list[str] = []
        async for piece in self.stream_advice(
            question=question,
            docs=docs,
            crop_context=crop_context,
            history=history,
            lang=lang,
            temperature=temperature,
            mode_out=mode_out,
        ):
            parts.append(piece)
        return "".join(parts)
