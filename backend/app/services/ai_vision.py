"""AI Vision — image validation, quality gates and the treatment KB.

Responsibilities
----------------
1. **Image validation (Stage 0/1 gate)** — byte size, decodability, dimensions
   and a blur check based on the variance of the Laplacian (NumPy).
2. **Template treatment plans** — deterministic bilingual (AR/EN) agronomy
   plans keyed on the disease label, used by the agronomist template
   generator. The live plan built from the agent verdict lives in
   ``app.agents.advisor_agent`` (Tébessa KB).

(No API calls live in this module — it is the local knowledge/quality
layer. All AI reasoning lives in the v4 multi-agent framework in
``app.agents``: DiagnosticAgent (OpenCV/NumPy vision), AdvisorAgent
(plans/chat), SupervisorAgent (orchestration).)
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np
from fastapi import HTTPException, status
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings

logger = logging.getLogger("hassad.vision")

# Reject decompression bombs *before* Pillow warns about them.
Image.MAX_IMAGE_PIXELS = 60_000_000


# ======================================================================
# Value objects
# ======================================================================
@dataclass(slots=True)
class ValidatedImage:
    """A decoded, EXIF-corrected, RGB image plus its quality report."""

    image: Image.Image
    original_bytes: int
    width: int
    height: int
    format: str
    blur_variance: float
    is_blurry: bool
    checks: dict[str, bool] = field(default_factory=dict)

    def as_report(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "bytes_size": self.original_bytes,
            "blur_variance": round(self.blur_variance, 3),
            "is_blurry": self.is_blurry,
            "checks": self.checks,
        }


# ======================================================================
# Helpers
# ======================================================================
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_length: int = 120) -> str:
    """Turn a model label into a stable, URL-safe identifier.

    Arabic-only labels have no ASCII representation — those fall back to a
    deterministic content hash so the id stays stable across calls.
    """
    normalised = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", normalised.lower()).strip("-")
    if not slug:
        slug = "x" + hashlib.sha1((value or "").encode("utf-8")).hexdigest()[:12]
    return slug[:max_length]


def _laplacian_variance(gray: np.ndarray) -> float:
    """Variance of the 4-neighbour Laplacian — low values == blurry image."""
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    g = gray.astype(np.float64)
    lap = (
        -4.0 * g[1:-1, 1:-1]
        + g[:-2, 1:-1]
        + g[2:, 1:-1]
        + g[1:-1, :-2]
        + g[1:-1, 2:]
    )
    return float(lap.var())


def _blur_score(image: Image.Image) -> float:
    """Compute the blur score on a 512px-normalised greyscale copy."""
    probe = image.copy()
    probe.thumbnail((512, 512))
    gray = np.asarray(probe.convert("L"), dtype=np.uint8)
    return _laplacian_variance(gray)


def _encode_jpeg(image: Image.Image, quality: int = 90) -> bytes:
    buffer = io.BytesIO()
    rgb = image.convert("RGB") if image.mode not in ("RGB", "L") else image.convert("RGB")
    rgb.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


# ======================================================================
# Stage-1 validation
# ======================================================================
def validate_crop_image(raw: bytes) -> ValidatedImage:
    """Validate an uploaded leaf/field photo (sync — run via asyncio.to_thread).

    Raises:
        HTTPException 413/422 for unusable uploads (corrupt, oversized, tiny…).
    """
    settings = get_settings()

    size = len(raw)
    if size < settings.IMAGE_MIN_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "image_too_small",
                "message": f"Image is too small ({size} bytes). Please upload a real photo.",
            },
        )
    if size > settings.IMAGE_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "image_too_large",
                "message": (
                    f"Image exceeds the {settings.IMAGE_MAX_BYTES // (1024 * 1024)} MB limit. "
                    "Please compress or retake the photo."
                ),
            },
        )

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()  # cheap integrity check
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "image_unreadable",
                "message": "The file could not be decoded as an image (JPG/PNG/WEBP expected).",
            },
        ) from exc

    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            image_format = (opened.format or "UNKNOWN").upper()
    except Exception as exc:  # noqa: BLE001 - any decode failure => retake
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "image_unreadable",
                "message": "The image appears to be corrupted. Please retake the photo.",
            },
        ) from exc

    try:
        image = image.convert("RGB")
        width, height = image.size
    except Exception as exc:  # noqa: BLE001 - exotic modes / corrupt data → retake
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "image_unreadable",
                "message": "The image could not be processed. Please retake the photo.",
            },
        ) from exc

    checks: dict[str, bool] = {"dimensions": True}
    if width < settings.IMAGE_MIN_DIM or height < settings.IMAGE_MIN_DIM:
        checks["dimensions"] = False
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "image_too_small",
                "message": (
                    f"Image is {width}x{height}px; at least "
                    f"{settings.IMAGE_MIN_DIM}x{settings.IMAGE_MIN_DIM}px is required."
                ),
            },
        )
    if width > settings.IMAGE_MAX_DIM or height > settings.IMAGE_MAX_DIM:
        image.thumbnail((settings.IMAGE_MAX_DIM, settings.IMAGE_MAX_DIM))
        width, height = image.size

    try:
        blur_variance = _blur_score(image)
        is_blurry = blur_variance < settings.IMAGE_BLUR_MIN_VARIANCE
    except Exception as exc:  # noqa: BLE001 - quality checks must never 500
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "image_unreadable",
                "message": "The image could not be analysed for sharpness. Please retake it.",
            },
        ) from exc
    checks["sharpness"] = not is_blurry

    return ValidatedImage(
        image=image,
        original_bytes=size,
        width=width,
        height=height,
        format=image_format,
        blur_variance=blur_variance,
        is_blurry=is_blurry,
        checks=checks,
    )


# ======================================================================
# Bilingual treatment plans (offline-safe fallback knowledge)
# ======================================================================
_DISCLAIMER = {
    "en": (
        "Always read and follow the product label, local regulations and the "
        "pre-harvest interval. When in doubt, consult a licensed agronomist."
    ),
    "ar": (
        "اقرأ واتبع دائماً إرشادات الملصق والأنظمة المحلية وفترة الأمان قبل الحصاد، "
        "واستشر مهندس الزراعة المعتمد عند الشك."
    ),
}

_TREATMENT_KNOWLEDGE: dict[str, dict[str, object]] = {
    "late blight": {
        "summary": {
            "en": "Late blight (Phytophthora infestans) — a fast-spreading oomycete that can destroy a tomato/potato canopy within days in cool, wet weather.",
            "ar": "اللفحة المتأخرة (Phytophthora infestans) — مرض سريع الانتشار يقضي على أوراق الطماطم والبطاطس خلال أيام في الطقس البارد والرطب.",
        },
        "immediate_actions": [
            {"en": "Remove and bag infected leaves/plants immediately; do not compost them.", "ar": "اقطف الأوراق والنباتات المصابة وأبعدها عن الحقل مباشرة ولا تضعها في السماد."},
            {"en": "Stop overhead irrigation; water the soil in the morning only.", "ar": "أوقف الري بالرشّ واسقِ التربة صباحاً فقط."},
            {"en": "Protect healthy plants with a preventive fungicide before the next rain.", "ar": "احمِ النباتات السليمة بمبيد وقائي قبل المطر التالي."},
        ],
        "control_options": [
            {"type": "cultural", "action": {"en": "Increase plant spacing and remove weeds to improve airflow.", "ar": "زوّد المسافة بين النباتات ونظف الأعشاب لتهوية أفضل."}, "notes": None},
            {"type": "chemical", "action": {"en": "Apply a locally registered fungicide (e.g. chlorothalonil, metalaxyl or copper-based) on a 7-day schedule.", "ar": "استخدم مبيداً مسجلاً محلياً (كلوروثالونيل، ميتالاكسيل أو نحاسي) كل 7 أيام."}, "notes": {"en": "Rotate modes of action to avoid resistance.", "ar": "بدّل مواد الفعالية لمنع مقاومة المرض."}},
        ],
        "monitoring": [
            {"en": "Scout the field twice a week for dark, water-soaked leaf lesions.", "ar": "افحص الحقل مرتين أسبوعياً بحثاً عن بقع داكنة مبللة على الأوراق."},
            {"en": "Watch night humidity > 90% — that is when infection explodes.", "ar": "راقب رطوبة الليل فوق 90% فهي وقت انتشار العدوى."},
        ],
        "prevention": [
            {"en": "Use certified disease-free seed/tubers and resistant varieties.", "ar": "استخدم بذوراً ومعترات معتمدة خالية من المرض وأصنافاً مقاومة."},
            {"en": "Rotate with cereals for at least two seasons.", "ar": "بدّل المحصول مع الحبوب لمدة موسمين على الأقل."},
        ],
    },
    "early blight": {
        "summary": {
            "en": "Early blight (Alternaria) — concentric brown rings on older leaves, driven by warm days and heavy dew; weakens plants but is manageable.",
            "ar": "اللفحة المبكرة (ألتيرناريا) — بقع بنية دائرية على الأوراق القديمة، تنشط مع الحرارة والندى الثقيل ويمكن إدارتها.",
        },
        "immediate_actions": [
            {"en": "Remove the lowest infected leaves and keep fruit off the soil.", "ar": "أزل الأوراق السفلية المصابة وأبعد الثمار عن التربة."},
            {"en": "Mulch around plants to block soil splash onto leaves.", "ar": "ضع غطاء عضوي حول النباتات لمنع قذف التربة على الأوراق."},
            {"en": "Apply a protectant fungicide (copper or mancozeb) as per label.", "ar": "رَشّ مبيداً وقائياً (نحاسي أو مانكوزيب) حسب التعليمات."},
        ],
        "control_options": [
            {"type": "organic", "action": {"en": "Bacillus-based bio-fungicides show good contact control.", "ar": "المبيدات الحيوية على أساس باسيلوس تعطي تحكماً جيداً."}, "notes": None},
            {"type": "chemical", "action": {"en": "Rotate azoxystrobin with chlorothalonil every 10–14 days.", "ar": "بدّل الأزوكسيستروبين مع الكلوروثالونيل كل 10–14 يوماً."}, "notes": {"en": "Never use the same mode twice in a row.", "ar": "لا تستخدم نفس المادة مرتين متتاليتين."}},
        ],
        "monitoring": [
            {"en": "Check lower canopy weekly; treat before 25% of leaves are affected.", "ar": "افحص الأوراق السفلية أسبوعياً وعالج قبل إصابة 25% من الأوراق."},
        ],
        "prevention": [
            {"en": "Remove crop residues after harvest and rotate annually.", "ar": "أخرج بقايا المحصول بعد الحصاد وبادر بالتناوب السنوي."},
            {"en": "Balance nitrogen fertilisation to avoid lush, vulnerable growth.", "ar": "وازن التسميد الآزوتي لتجنب النمو المفرط الهش."},
        ],
    },
    "powdery mildew": {
        "summary": {
            "en": "Powdery mildew — white talc-like powder on leaves; thrives in warm, dry days with humid nights.",
            "ar": "البيوض الحرة (الدبق) — مسحوق أبيض على الأوراق، ينشط في الأيام الدافئة الجافة والليالي الرطبة.",
        },
        "immediate_actions": [
            {"en": "Spray a milk solution (1 part milk : 9 parts water) weekly as a first response.", "ar": "رَشّ محلول حليب (حليب:ماء 1:9) أسبوعياً كإجراء أول."},
            {"en": "Prune infected branches to open the canopy.", "ar": "قلّم الأفرع المصابة لتهوية التاج النباتي."},
            {"en": "Avoid excess nitrogen — soft growth is attacked first.", "ar": "تجنّب الإفراط في الآزوت لأن النمو الرخو هو الأول تضرراً."},
        ],
        "control_options": [
            {"type": "organic", "action": {"en": "Sulphur or potassium bicarbonate sprays, applied at first symptoms.", "ar": "رَشّ الكبريت أو بيكربونات البوتاسيوم عند أول الأعراض."}, "notes": {"en": "Do not spray above 30°C.", "ar": "لا ترشح فوق 30 درجة مئوية."}},
            {"type": "chemical", "action": {"en": "Triazole or strobilurin fungicides per local registration.", "ar": "استخدم مبيدات تريازول أو ستروبيلورين حسب التسجيل المحلي."}, "notes": None},
        ],
        "monitoring": [
            {"en": "Inspect both sides of leaves weekly — powder starts underneath.", "ar": "افحص وجهي الأوراق أسبوعياً فالمسحوق يبدأ من الأسفل."},
        ],
        "prevention": [
            {"en": "Choose resistant varieties and ensure good spacing.", "ar": "اختر أصنافاً مقاومة وحافظ على المسافات الجيدة."},
        ],
    },
    "rust": {
        "summary": {
            "en": "Rust — orange-brown pustules on leaf undersides; spreads by wind and weakens grain crops quickly.",
            "ar": "الصدأ — بثور برتقالية بنية أسفل الأوراق، تنتشر بالرياح وتضعف المحاصيل الحبوب بسرعة.",
        },
        "immediate_actions": [
            {"en": "Remove and destroy badly infected plants.", "ar": "أتلف النباتات المصابة بشدة بعيداً عن الحقل."},
            {"en": "Apply a triazole fungicide at first pustules.", "ar": "استخدم مبيد تريازول عند ظهور أول البثور."},
            {"en": "Improve drainage and reduce leaf wetness duration.", "ar": "حسّن التصريف وقلّل مدة بلل الأوراق."},
        ],
        "control_options": [
            {"type": "chemical", "action": {"en": "Tebuconazole or propiconazole per label rate on cereals.", "ar": "تيبيكونازول أو بروبيكونازول بالجرعة المذكورة على الحبوب."}, "notes": {"en": "Observe the pre-harvest interval strictly.", "ar": "التزم بفترة الأمان قبل الحصاد."}},
        ],
        "monitoring": [
            {"en": "Check leaf undersides weekly during tillering and heading.", "ar": "افحص أسفل الأوراق أسبوعياً في مرحلتي العقل والهدرة."},
        ],
        "prevention": [
            {"en": "Use rust-resistant seed lots and clean the harvester between fields.", "ar": "استخدم بذوراً مقاومة للصدأ ونظّف الحاصدة بين الحقول."},
        ],
    },
    "root rot": {
        "summary": {
            "en": "Root rot — soil-borne fungi (Pythium, Fusarium, Phytophthora) decay the roots, so water uptake fails and plants wilt, yellow and stall.",
            "ar": "التعفن الجذري — فطريات تعيش في التربة (بيثيوم، فوزاريوم، فيتوبتورا) تحلل الجذور فيتعطل امتصاص الماء فتحتل النباتات وتصفر وتقلّ نموها.",
        },
        "immediate_actions": [
            {"en": "Stop irrigation and improve drainage immediately.", "ar": "أوقف الري وحسّن التصريف فوراً."},
            {"en": "Uproot and destroy badly rotted plants together with the surrounding soil ball.", "ar": "استخرج النباتات المتحلّلة تماماً مع طبقة التربة المحيطة بها وأتلفها بعيداً عن الحقل."},
            {"en": "Let the soil dry between waterings and avoid wetting the foliage.", "ar": "دع التربة تجف بين الريات وتجنّب بلل الأوراق."},
        ],
        "control_options": [
            {"type": "chemical", "action": {"en": "Soil-drench with metalaxyl/mefenoxam or a phosphonate fungicide per label rate.", "ar": "اسقِ التربة بمذيكسيل/ميفينوكسام أو مبيد فوسفونات حسب الجرعة المذكورة على التسمية."}, "notes": {"en": "Root rots are hard to cure — act at the first wilted plants.", "ar": "التعفن الجذري صعب العلاج — تصرّف عند أول نبات ذابل."}},
            {"type": "biological", "action": {"en": "Drench with a Trichoderma-based bio-fungicide to suppress the pathogens.", "ar": "اسقِ مستحضراً حيوياً قائماً على تريكوديرما لخفض حدة الممرضات."}, "notes": None},
        ],
        "monitoring": [
            {"en": "Pull one suspect plant weekly and check root colour — healthy roots stay white and firm.", "ar": "اسحب نباتاً مشبوهاً أسبوعياً وافحص لون الجذور — الجذور السليمة تبقى بيضاء صلبة."},
        ],
        "prevention": [
            {"en": "Raise beds, rotate away from solanaceous and cucurbit hosts for three or more years.", "ar": "ارفع المشاتل وطبّق التناوب بعيداً عن المضيفات (بطاطس وقرعيات) ثلاث سنوات أو أكثر."},
            {"en": "Use certified clean seedlings and solarise the soil before planting.", "ar": "استخدم شتلات معتمدة نظيفة واعرض التربة لأشعة الشمس (التعريض الشمسي) قبل الزراعة."},
        ],
    },
    "brown spot": {
        "summary": {
            "en": "Brown spot — circular brown lesions with yellow halos (Cochliobolus/Bipolaris); flourishes on rice and maize in humid fields with weak nutrition.",
            "ar": "البُقع البنية — بقع دائرية بنية بحواف صفراء (كوليوبولوس/بيبولاريس)؛ تزدهر على الأرز والذرة في الحقول الرطبة ذات التغذية الضعيفة.",
        },
        "immediate_actions": [
            {"en": "Remove badly spotted lower leaves and burn them away from the field.", "ar": "أزل الأوراق السفلية شديدة البقع وأحرقها بعيداً عن الحقل."},
            {"en": "Apply a mancozeb or triazole fungicide at first spotting.", "ar": "استخدم مبيد مانكوزيب أو تريازول عند أول ظهور للبقع."},
            {"en": "Correct nutrition — potassium and silicon sharply reduce severity.", "ar": "صحّح التغذية — البوتاسيوم والسيليكون يقللان الحدة بوضوح."},
        ],
        "control_options": [
            {"type": "chemical", "action": {"en": "Mancozeb + tricyclazole spray per label on rice; repeat after 10-14 days.", "ar": "رش مانكوزيب + ترايسايكلازول حسب التسمية على الأرز؛ كرر بعد 10-14 يوماً."}, "notes": {"en": "Watch the pre-harvest interval on grain crops.", "ar": "انتبه لفترة الأمان قبل الحصاد على محاصيل الحبوب."}},
        ],
        "monitoring": [
            {"en": "Scout the lower canopy weekly — spots start on older leaves.", "ar": "افحص الطبقة السفلية أسبوعياً — البقع تبدأ على الأوراق الأقدم."},
        ],
        "prevention": [
            {"en": "Use treated certified seed and avoid excess standing water.", "ar": "استخدم بذوراً معالجة معتمدة وتجنّب ركود الماء الزائد."},
        ],
    },
    "leaf spot": {
        "summary": {
            "en": "Leaf spot — scattered necrotic spots of fungal or bacterial origin; heavy spotting defoliates plants and cuts yields.",
            "ar": "البقع الورقية — نقاط نخرية متناثرة مصدرها فطريات أو بكتيريا؛ كثرة البقع تسبب تساقط الأوراق ويخفض الإنتاج.",
        },
        "immediate_actions": [
            {"en": "Remove infected leaves and improve airflow between plants.", "ar": "أزل الأوراق المصابة وحسّن التهوية بين النباتات."},
            {"en": "Switch to drip irrigation and avoid wetting the canopy.", "ar": "انتقل إلى الري بالتنقيط وتجنّب بلل ظلة الأوراق."},
            {"en": "Apply a broad-spectrum fungicide (chlorothalonil) if spots spread.", "ar": "رش مبيد فطري مجزأ (كلوروثالونيل) إذا انتشرت البقع."},
        ],
        "control_options": [
            {"type": "chemical", "action": {"en": "Copper-based or chlorothalonil spray per label, rotating modes of action.", "ar": "رش نحاسي أو كلوروثالونيل حسب التسمية مع تدوير آليات التأثير."}, "notes": None},
        ],
        "monitoring": [
            {"en": "Photograph the same leaves weekly to track spot expansion.", "ar": "التقط صوراً لنفس الأوراق أسبوعياً لتتبع اتساع البقع."},
        ],
        "prevention": [
            {"en": "Rotate crops, clean debris after harvest and space plants well.", "ar": "طبّق التناوب ونظّف بقايا ما بعد الحصاد وحافظ على المسافات."},
        ],
    },
    "blast": {
        "summary": {
            "en": "Rice blast (Magnaporthe oryzae) — spindle-shaped gray-green lesions that turn brown; destroys leaves and panicles in cool, humid weather.",
            "ar": "فطر الانفجار (ماجنابورث في الأرز) — آفات مغزلية رمادية خضراء تتحول بنية؛ تدمر الأوراق والسنابل في الطقس البارد الرطب.",
        },
        "immediate_actions": [
            {"en": "Drain paddies and stop overhead irrigation.", "ar": "أفرغ مياه الأرز (الزرَّاق) وأوقف الري العلوي."},
            {"en": "Spray tricyclazole or azoxystrobin at the first spindle lesions.", "ar": "رش ترايسايكلازول أو أزوكسيستروبين عند أول آفات مغزلية."},
            {"en": "Remove infected hill bases and reduce plant density.", "ar": "أزل القواعد المصابة وقلّل كثافة الزراعة."},
        ],
        "control_options": [
            {"type": "chemical", "action": {"en": "Tricyclazole 75% WP per label, timed at panicle initiation for neck blast.", "ar": "ترايسايكلازول 75% و.ب حسب التسمية، يُوَزَّع عند بداية السنابل لحماية العنق."}, "notes": {"en": "Rotate with a different fungicide group to avoid resistance.", "ar": "بدّل المجموعة الفطرية لتجنّب المقاومة."}},
        ],
        "monitoring": [
            {"en": "Walk the field at dew-heavy mornings looking for spindle lesions.", "ar": "جُل الحقل صباحات الندى الباحثاً عن الآفات المغزلية."},
        ],
        "prevention": [
            {"en": "Use blast-resistant varieties and balanced nitrogen (avoid excess).", "ar": "استخدم أصنافاً مقاومة للانفجار ونيتروجيناً متوازناً (تجنّب الإفراط)."},
        ],
    },
    "healthy": {
        "summary": {
            "en": "No visible disease symptoms were detected. Keep the plant in good health with balanced nutrition and monitoring.",
            "ar": "لم تُكتشف أعراض مرضية ظاهرة. حافظ على صحة النبتة بالتغذية المتوازنة والمراقبة الدورية.",
        },
        "immediate_actions": [
            {"en": "Keep a weekly scouting routine to catch problems early.", "ar": "التزم بجولة فحص أسبوعية للكشف المبكر عن أي مشكلة."},
            {"en": "Verify soil moisture before each irrigation.", "ar": "تأكد من رطوبة التربة قبل كل ري."},
        ],
        "control_options": [
            {"type": "cultural", "action": {"en": "Apply balanced fertilisation based on a soil test.", "ar": "استخدم تسميداً متوازناً بناءً على تحليل التربة."}, "notes": None},
        ],
        "monitoring": [
            {"en": "Photograph the same spot weekly to track changes over time.", "ar": "التقط صورة لنفس البقعة أسبوعياً لتتبع التغيرات."},
        ],
        "prevention": [
            {"en": "Remove weeds and crop residues that host pests.", "ar": "نظف الأعشاب وبقايا المحصول التي تؤوي الآفات."},
        ],
    },
    "generic ipm": {
        "summary": {
            "en": "Signs of pest/disease stress were detected but could not be identified with high confidence. Follow integrated pest management (IPM) steps and confirm with an agronomist.",
            "ar": "ظهرت علامات إجهاد بسبب آفة أو مرض دون تحديد دقيق للنوع. اتبع خط إدارة الآفات المتكاملة وراجع مهندس الزراعة للتأكيد.",
        },
        "immediate_actions": [
            {"en": "Isolate the affected area and remove the worst parts of the plant.", "ar": "اعزل المنطقة المصابة وأزل الأجزاء الأكثر تضرراً من النبات."},
            {"en": "Sample insects or leaf sections and send them to the local extension office.", "ar": "اجمع عينات من الحشرات أو الأوراق وأرسلها لمكتب الإرشاد الزراعي."},
            {"en": "Avoid broad-spectrum sprays until the cause is confirmed.", "ar": "تجنّب الرش المجزأ حتى يتأكد السبب."},
        ],
        "control_options": [
            {"type": "biological", "action": {"en": "Release or conserve natural enemies (ladybirds, parasitoid wasps, Trichogramma).", "ar": "حرّر أو احمِ الأعداء الطبيعيين (الفصائل، الدبابير المفترسة، تريشوجراما)."}, "notes": None},
            {"type": "cultural", "action": {"en": "Practise crop rotation and remove host weeds.", "ar": "طبّق التناوب الزراعي وأزل الأعشاب المضيفة للآفات."}, "notes": None},
        ],
        "monitoring": [
            {"en": "Use yellow/blue sticky traps to monitor flying insects weekly.", "ar": "استخدم مصائد لاصقة صفراء/زرقاء لرصد الحشرات الطائرة أسبوعياً."},
        ],
        "prevention": [
            {"en": "Maintain field hygiene and diversify plantings.", "ar": "حافظ على نظافة الحقل ونوّع الرُّوى."},
        ],
    },
}

_MATCH_ORDER: tuple[tuple[str, str], ...] = (
    ("late blight", "late blight"),
    ("early blight", "early blight"),
    ("powdery mildew", "powdery mildew"),
    ("powdery", "powdery mildew"),
    ("mildew", "powdery mildew"),
    ("rust", "rust"),
    ("root rot", "root rot"),
    ("rot", "root rot"),
    ("brown spot", "brown spot"),
    ("leaf spot", "leaf spot"),
    ("blast", "blast"),
    ("healthy", "healthy"),
    ("no disease", "healthy"),
    ("normal", "healthy"),
)


def build_treatment_plan(disease_label: str | None, crop_label: str | None = None) -> dict[str, object]:
    """Return a bilingual treatment plan dict for a disease label.

    Deterministic and offline-safe (no extra API call); used for confirmed
    diagnoses so the farmer always receives actionable guidance.
    """
    haystack = (disease_label or "").lower().replace("_", " ").replace("-", " ")
    key = "generic ipm"
    for needle, mapped in _MATCH_ORDER:
        if needle in haystack:
            key = mapped
            break

    entry = _TREATMENT_KNOWLEDGE[key]
    plan: dict[str, object] = {
        "summary": dict(entry["summary"]),  # type: ignore[arg-type]
        "immediate_actions": [dict(a) for a in entry["immediate_actions"]],  # type: ignore[index]
        "control_options": [dict(o) for o in entry["control_options"]],  # type: ignore[index]
        "monitoring": [dict(m) for m in entry["monitoring"]],  # type: ignore[index]
        "prevention": [dict(p) for p in entry["prevention"]],  # type: ignore[index]
        "disclaimer": dict(_DISCLAIMER),
        "source": f"hassad_knowledge_base:{key}",
    }
    if crop_label:
        plan["crop_context"] = crop_label
    return plan


def encode_image_for_storage(image: Image.Image) -> str:
    """Base64 data-URL used when clients want to persist the analysed photo."""
    return "data:image/jpeg;base64," + base64.b64encode(_encode_jpeg(image, quality=85)).decode("ascii")
