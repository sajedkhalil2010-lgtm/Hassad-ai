/* =========================================================================
   Bilingual layer — Arabic (RTL) is the default, English the alternate.

   Two properties make this worth doing properly rather than by swapping a
   handful of strings:
     · dir flips on <html>, so the whole layout mirrors through CSS logical
       properties instead of a second set of rules;
     · the choice persists, so a farmer who prefers English is not made to
       switch back on every visit.

   Only STATIC copy is translated here. Anything the backend writes — a
   disease name, an error message, a chat reply — is model output and is
   rendered verbatim by whichever module produced it.
   ========================================================================= */

import { $all } from "./dom.js";

const KEY = "hassad.lang";

const STRINGS = {
  ar: {
    nav_how: "كيف يعمل", nav_modules: "الوحدات", nav_why: "لماذا حصاد؟",
    sign_in: "تسجيل الدخول", start_free: "ابدأ مجاناً", create_account: "إنشاء حساب",
    hero_badge: "ذكاء اصطناعي خاص بالمزارعين العرب",
    hero_t1: "حاصد ذكي لمحصولك،", hero_t2: "تشخيص فوري بالصورة",
    hero_sub: "ارفع صورة ورقة نبات واحدة، وسيتولى حصاد AI تحليلها وتحديد المرض وتقديم خطة علاج موثوقة — خلال ثوانٍ، وبواجهة عربية أنيقة.",
    cta_start: "ابدأ التشخيص الآن", cta_how: "كيف يعمل؟",
    fb_privacy: "خصوصية أولاً — صورك تُعالج بأمان",
    fb_easy: "يعمل بدون خبرة تقنية", fb_rtl: "دعم كامل للعربية RTL",
    mock_label: "النتيجة", mock_disease: "اللفحة المتأخرة", mock_crop: "الطماطم",
    mock_plan: "خطة علاج جاهزة", mock_verified: "تم التحقق",
    how_t: "ثلاث خطوات، بلا تعقيد",
    how_1t: "التقط الصورة", how_1b: "صورة واحدة لورقة واحدة، بضوء النهار وبساطحة. صورة الورقة الواحدة تعطي أوضح قراءة.",
    how_2t: "تحليل فوري", how_2b: "يقرأ النموذج الأعراض ويعيد المرض المحتمل ونسبة تأثر الورقة ودرجة الثقة.",
    how_3t: "خطة علاج", how_3b: "توصيات عملية مبنية على فلاحة تبسة، مع سؤال المساعد عن أي تفصيل.",
    mod_t: "الوحدات",
    mod_1t: "استوديو التشخيص", mod_1b: "ارفع الصورة واقرأ النتيجة بصريًا: مؤشرات دائرية، شارات، وتفصيل للأعراض.",
    mod_2t: "المساعد الزراعي", mod_2b: "اسأل عن الجرعات والتوقيت وما تراه في الحقل — بعربية أو إنجليزية.",
    mod_3t: "سجل التشخيصات", mod_3b: "تتبّع تشخيصاتك السابقة ومتوسط درجة الثقة عبر الزمن.",
    why_t: "لماذا حصاد؟",
    why_1t: "يعمل دون اتصال ثابت", why_1b: "واجهة كاملة بلا اعتماديات خارجية للتصميم — تُحمَّل فورًا على أي جهاز.",
    why_2t: "محلي وخاص", why_2b: "الحسابات مخزّنة في قاعدة بيانات على هذا الجهاز فقط، بلا طرف ثالث.",
    why_3t: "مفهوم للمزارع", why_3b: "نتيجة واضحة بالعربية واللاتينية، ودرجة ثقة صريحة بدل اليقين المزيّف.",
    foot_note: "حصاد AI — دعم القرار لمنطقة تبسة. تأكّد من أي منتج مبيد لدى المصالحة الزراعية.",

    group_main: "الرئيسية", group_tools: "الأدوات",
    nav_dashboard: "لوحة التحكم", nav_studio: "استوديو التشخيص",
    nav_history: "سجل التشخيصات", nav_assistant: "المساعد الزراعي",
    ask_assistant: "اسأل المساعد", new_diagnosis: "تشخيص جديد",
    hi: "مرحبًا", welcome_sub: "هذه نظرة سريعة على حالة محاصيلك اليوم.",
    m_total: "إجمالي التشخيصات", m_total_d: "محفوظة على هذا الجهاز",
    m_review: "قيد المراجعة", m_review_d: "ثقة منخفضة — راجعها بعينك",
    m_conf: "متوسط الثقة", m_conf_d: "عبر كل التشخيصات",
    quick_actions: "إجراءات سريعة",
    q_studio: "ارفع ورقة واحصل على نتيجة خلال ثوانٍ.",
    q_ask_t: "اسأل خبيرًا", q_ask_b: "اسأل المساعد عن الجرعات والتوقيت.",
    recent: "أحدث التشخيصات", see_all: "عرض الكل",

    studio_t: "تشخيص الورقة",
    studio_hint: "صوّر ورقة واحدة، مسطّحة وفي ضوء النهار. ورقة واحدة لكل صورة تعطي أوضح قراءة.",
    drop_t: "أسقط صورة الورقة هنا", drop_or: "أو", drop_browse: "تصفّح ملفاتك",
    analyze: "تحليل", r_conf: "الثقة", r_sev: "تأثر الورقة",
    card_symptoms: "الأعراض المرصودة", card_guidance: "التوصيات الزراعية",
    raw: "الردود الخام", history_hint: "محفوظ في متصفحك فقط — لا يُرسل إلى أي خادم.",
    clear: "مسح", history_empty: "لا توجد تشخيصات بعد. ابدأ من استوديو التشخيص.",

    chat_t: "المساعد الزراعي", chat_status: "متصل بقاعدة المعرفة الزراعية",
    chat_intro: "اسأل سؤالًا للبدء. الإجابات دعم قرار لمنطقة تبسة — تأكّد من أي منتج مبيد لدى المصالحة الزراعية.",
    chat_hint: "Enter للإرسال · Shift+Enter لسطر جديد",
    sug1: "كيف أعالج اللفحة المتأخرة؟", sug2: "علامات نقص النيتروجين",
    sug3: "متى أروّي الأرض؟", sug4: "أفضل طريقة لمقاومة الذبابة البيضاء",

    account_t: "حسابك", email: "البريد الإلكتروني", password: "كلمة المرور",
    name: "الاسم", guest: "المتابعة كضيف", users: "المستخدمون المسجّلون",
    local_account: "حساب محلي",
    local_note: "الحسابات مخزّنة على هذا الجهاز فقط، في قاعدة بيانات محلية. لا يُرسل شيء إلى طرف ثالث.",

    /* Status copy the diagnostic module writes at runtime. */
    st_pick: "اختر صورة للبدء.", st_ready: "جاهز للتحليل.",
    st_stale: "جاهز للتحليل. النتيجة أدناه تخص الصورة السابقة.",
    st_analyzing: "جارٍ تحليل الورقة…",
    st_done: "اكتمل التحليل.", st_error: "تعذّر التحليل.",
    st_notimg: "هذا ليس ملف صورة. استعمل JPEG أو PNG أو WEBP أو BMP أو TIFF.",
    st_big: "حجم الصورة {size} — الحد الأقصى {max}.",

    role_farmer: "مزارع", role_guest: "ضيف", role_admin: "مدير",
  },

  en: {
    nav_how: "How it works", nav_modules: "Modules", nav_why: "Why Hassad?",
    sign_in: "Sign in", start_free: "Start free", create_account: "Create account",
    hero_badge: "AI built for Arab farmers",
    hero_t1: "A smart harvester for your crop,", hero_t2: "instant diagnosis from a photo",
    hero_sub: "Upload a single leaf photo and Hassad AI analyses it, identifies the disease and returns a reliable treatment plan — in seconds, behind a polished Arabic interface.",
    cta_start: "Start diagnosing now", cta_how: "How it works",
    fb_privacy: "Privacy first — your photos are handled safely",
    fb_easy: "No technical skill required", fb_rtl: "Full Arabic RTL support",
    mock_label: "Result", mock_disease: "Late blight", mock_crop: "Tomato",
    mock_plan: "Treatment plan ready", mock_verified: "Verified",
    how_t: "Three steps, no complexity",
    how_1t: "Take the photo", how_1b: "One photo of one leaf, flat and in daylight. A single leaf per image gives the clearest read.",
    how_2t: "Instant analysis", how_2b: "The model reads the symptoms and returns the likely disease, the share of leaf affected and a confidence figure.",
    how_3t: "Treatment plan", how_3b: "Practical recommendations grounded in Tébessa agriculture, with the assistant on hand for any detail.",
    mod_t: "Modules",
    mod_1t: "Diagnostic Studio", mod_1b: "Upload the photo and read the result visually: gauges, badges and a symptom breakdown.",
    mod_2t: "Agri Assistant", mod_2b: "Ask about dosage, timing or what you are seeing in the field — in Arabic or English.",
    mod_3t: "Diagnosis History", mod_3b: "Track your previous diagnoses and average confidence over time.",
    why_t: "Why Hassad?",
    why_1t: "Works without constant signal", why_1b: "A complete interface with no external design dependencies — it loads instantly on any device.",
    why_2t: "Local and private", why_2b: "Accounts live in a database on this machine only, with no third party involved.",
    why_3t: "Built for the farmer", why_3b: "A clear result in Arabic and Latin script, with an honest confidence figure instead of fake certainty.",
    foot_note: "Hassad AI — decision support for the Tébessa region. Confirm any pesticide product with the agricultural services.",

    group_main: "MAIN", group_tools: "TOOLS",
    nav_dashboard: "Dashboard", nav_studio: "Diagnostic Studio",
    nav_history: "Diagnosis History", nav_assistant: "Agri Assistant",
    ask_assistant: "Ask the assistant", new_diagnosis: "New diagnosis",
    hi: "Hi", welcome_sub: "Here is a quick look at your crop's condition today.",
    m_total: "Total diagnoses", m_total_d: "Stored on this device",
    m_review: "In review", m_review_d: "Low confidence — check it yourself",
    m_conf: "Avg. confidence", m_conf_d: "Across all diagnoses",
    quick_actions: "Quick actions",
    q_studio: "Upload a leaf and get a result in seconds.",
    q_ask_t: "Ask an expert", q_ask_b: "Ask the assistant about dosage and timing.",
    recent: "Recent diagnoses", see_all: "See all",

    studio_t: "Leaf diagnosis",
    studio_hint: "Photograph one leaf, flat and in daylight. A single leaf per image gives the clearest read.",
    drop_t: "Drop a leaf photo here", drop_or: "or", drop_browse: "browse your files",
    analyze: "Analyze", r_conf: "Confidence", r_sev: "Leaf affected",
    card_symptoms: "Observed symptoms", card_guidance: "Agronomic guidance",
    raw: "Raw responses", history_hint: "Stored in your browser only — never sent to a server.",
    clear: "Clear", history_empty: "No diagnoses yet. Start from the Diagnostic Studio.",

    chat_t: "Agri Assistant", chat_status: "Connected to the agronomy knowledge base",
    chat_intro: "Ask a question to begin. Answers are decision-support for the Tébessa region — confirm anything involving a phytosanitary product with the DPA or the Chambre d'Agriculture.",
    chat_hint: "Enter to send · Shift+Enter for a new line",
    sug1: "How do I treat late blight?", sug2: "Signs of nitrogen deficiency",
    sug3: "When should I irrigate?", sug4: "Best way to fight whitefly",

    account_t: "Your account", email: "Email", password: "Password",
    name: "Name", guest: "Continue as guest", users: "Registered users",
    local_account: "Local account",
    local_note: "Accounts are stored only on this machine, in a local database. Nothing is sent to a third party.",

    st_pick: "Choose an image to begin.", st_ready: "Ready to analyze.",
    st_stale: "Ready to analyze. The verdict below is from the previous image.",
    st_analyzing: "Analyzing the leaf…",
    st_done: "Analysis complete.", st_error: "The analysis failed.",
    st_notimg: "That is not an image file. Use JPEG, PNG, WEBP, BMP or TIFF.",
    st_big: "That image is {size}. The maximum is {max}.",

    role_farmer: "Farmer", role_guest: "Guest", role_admin: "Administrator",
  },
};

let lang = "ar";

/** Translate a key. Falls back to the key itself so a missing string shows
 *  as a visible gap in review rather than rendering "undefined". */
export function t(key) {
  const pack = STRINGS[lang] || STRINGS.ar;
  return pack[key] ?? STRINGS.ar[key] ?? key;
}

export function currentLang() {
  return lang;
}

function stored() {
  try {
    const v = localStorage.getItem(KEY);
    return v === "en" || v === "ar" ? v : "ar";
  } catch {
    return "ar";
  }
}

/** Flip <html> between the two directions, then repaint every labelled node.
 *  RTL comes for free across the stylesheet because spacing and borders use
 *  logical properties (margin-inline, border-inline-end, inset-inline). */
function setLang(next) {
  lang = next === "en" ? "en" : "ar";
  const rtl = lang === "ar";

  document.documentElement.lang = lang;
  document.documentElement.dir = rtl ? "rtl" : "ltr";

  document.title = rtl ? "حصاد AI · Hassad AI" : "Hassad AI · Crop diagnosis";

  for (const node of $all("[data-i18n]")) {
    const value = t(node.dataset.i18n);
    // textContent only: these strings are ours, but the habit of never
    // assigning markup is what keeps the XSS surface closed everywhere.
    node.textContent = value;
  }

  // The switcher advertises the language you would switch TO.
  for (const node of $all("[data-lang-label]")) node.textContent = rtl ? "EN" : "AR";

  try {
    localStorage.setItem(KEY, lang);
  } catch {
    /* private mode — the choice simply will not persist */
  }

  // Let whoever cares repaint (the diagnostic status line is translated).
  document.dispatchEvent(new CustomEvent("hassad:lang", { detail: { lang } }));
}

export function initI18n() {
  setLang(stored());

  for (const node of $all("[data-lang-toggle]")) {
    node.addEventListener("click", () => setLang(lang === "ar" ? "en" : "ar"));
  }

  return { t, setLang, currentLang };
}
