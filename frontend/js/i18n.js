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
    badge_probable: "تشخيص محتمل · ثقة منخفضة",
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
    analyze: "تحليل", analyze_busy: "جارٍ التحليل…", r_conf: "الثقة", r_sev: "تأثر الورقة",
    card_symptoms: "الأعراض المرصودة", card_guidance: "التوصيات الزراعية",
    history_hint: "محفوظ في متصفحك فقط — لا يُرسل إلى أي خادم.",
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

    /* Pricing panel — a UI demo. No payment gateway exists behind it, so
       the copy says so rather than letting a price imply a checkout. */
    group_plan: "الخدمات والاشتراكات", nav_pricing: "أقسام الخدمات والاشتراكات",
    pricing_t: "الأسعار ونموذج العمل",
    pricing_hint: "معاينة لواجهة التسعير — لا توجد بوابة دفع متصلة. جميع الخطط مجانية خلال هذه المرحلة.",
    pricing_demo: "وضع تجريبي",
    pricing_note: "الأسعار المعروضة تقديرية لأغراض العرض فقط. لا تُخصم أي عملية دفع، وتبقى جميع الخطط مفتوحة ومجاناً.",
    plan_free_badge: "الحالية / مجانية",
    plan_free_t: "المجانية", plan_free_seg: "صغار المزارعين · B2C",
    plan_free_amount: "$0", plan_free_per: "للأبد",
    plan_free_d: "كل ما يحتاجه المزارع للبدء — تشخيص فوري وبلا تكلفة.",
    plan_free_f1: "عدد محدود من فحوصات المحاصيل شهرياً",
    plan_free_f2: "تشخيص لورقة النبات بالذكاء الاصطناعي في أقل من ثانية",
    plan_free_f3: "إرشاد أساسي بالعربية والإنجليزية",
    plan_free_f4: "بدون بطاقة بنكية",
    plan_free_cta: "ابدأ مجاناً",
    plan_pro_badge: "قريباً",
    plan_pro_t: "برو", plan_pro_seg: "المزارع المستقلة · B2C",
    plan_pro_amount: "$9", plan_pro_per: "شهرياً",
    plan_pro_d: "تتبّع ميداني كامل وسجل تشخيصات غير محدود عبر الموسم.",
    plan_pro_f1: "فحوصات غير محدودة",
    plan_pro_f2: "تقارير تشخيص ميداني تاريخية",
    plan_pro_f3: "أولوية في الاستدلال السحابي للذكاء الاصطناعي",
    plan_pro_f4: "سجل تشخيصات كامل مع درجات الثقة",
    plan_pro_cta: "اشترك الآن",
    plan_ent_badge: "حسب العقد",
    plan_ent_t: "المزارع والتعاونيات", plan_ent_seg: "التعاونيات والمزارع الكبرى · B2B",
    plan_ent_amount: "$49", plan_ent_per: "شهرياً",
    plan_ent_d: "عقود موسمية مصمّمة للمزارع الكبيرة والتعاونيات الزراعية.",
    plan_ent_f1: "عقود موسمية مخصّصة",
    plan_ent_f2: "لوحة مراقبة متعددة الحقول",
    plan_ent_f3: "تكامل واجهة برمجية للبيانات الإقليمية",
    plan_ent_f4: "تدريب ميداني وأولوية في الدعم",
    plan_ent_cta: "تواصل معنا",
    /* Title + body together say the required sentence once. Carrying it in
       both would make a small dialog greet the reader with the same words
       two lines apart. */
    plan_modal_t: "شكراً لاهتمامك!",
    plan_modal_b: "حصاد AI حالياً في مرحلة المعاينة المبكرة والوصول المجاني.",
    plan_modal_plan: "الخطة",
    plan_modal_note: "سنُعلمك فور توفر الاشتراكات المدفوعة — ويمكنك المتابعة بالمجان في هذه الأثناء.",
    plan_modal_ok: "حسناً",

    /* --- sign-up handoff: plan selection, then a demo checkout ---------- */
    su_title: "اختر خطتك",
    su_pay_t: "الدفع",
    su_account: "تم إنشاء الحساب",
    su_step_plan: "1 · الخطة",
    su_step_pay: "2 · الدفع",
    su_pick: "الخطة التي تريد البدء بها",
    su_free_t: "المجانية", su_free_amt: "$0",
    su_free_f: "وصول أساسي بعدد محدود من الفحوصات شهرياً",
    su_pro_t: "برو", su_pro_amt: "$9",
    su_pro_f: "فحوصات غير محدودة وتقارير ميدانية تاريخية · شهرياً",
    su_ent_t: "المؤسسات", su_ent_amt: "$49",
    su_ent_f: "وصول جماعي للتعاونيات وتحليلات إقليمية · شهرياً",
    su_continue: "المتابعة إلى الدفع",
    su_skip: "تخطَّ الآن (المتابعة بالمجان)",
    su_back: "رجوع",
    su_due: "المطلوب",
    su_pay_demo: "وضع تجريبي: لا يُخصم أي مبلغ ولا تُرسل هذه البيانات إلى أي مكان — استخدم رقماً تجريبياً.",
    su_card_num: "رقم البطاقة",
    su_card_exp: "تاريخ الانتهاء (شهر/سنة)",
    su_card_cvv: "رمز الأمان CVV",
    su_card_holder: "اسم حامل البطاقة",
    su_pay_cta: "إتمام الاشتراك",
    su_bad_number: "أدخل رقماً كاملاً من 15 رقماً فأكثر.",
    su_bad_expiry: "تاريخ الانتهاء غير صحيح أو قد انقضى.",
    su_bad_cvv: "رمز الأمان من 3 إلى 4 أرقام.",
    su_bad_holder: "أدخل اسم حامل البطاقة.",
    su_ok: "تم تأكيد الاشتراك — أهلاً بك!",
    su_free_ok: "المتابعة بالخطة المجانية.",

    /* ---- demo recorder ---- */
    group_dev: "أدوات المطوّر",
    nav_record: "مسجّل العرض التجريبي",
    rec_t: "مسجّل العرض التجريبي",
    rec_hint: "يقيّد الواجهة الحقيقية على ساعة ثابتة مدتها ثلاث دقائق، ثم يصدّر ملف الفيديو. لا يُرسم أي إطار بالكامل: كل ما يظهر هو المنتج نفسه وهو يستجيب.",
    rec_flag: "تسجيل مباشر",
    rec_sample_l: "صورة الورقة التي سيشخّصها العرض",
    rec_step1: "اختر تبويب هذه الصفحة في نافذة مشاركة الشاشة التي يفتحها المتصفح.",
    rec_step2: "أبعد يدك عن الفأرة: الجدول زمني يتحرّك وحده طوال الثلاث دقائق.",
    rec_step3: "لإنهاء مبكر اضغط Escape أو زر الإيقاف — سيُحفظ ما سُجّل حتى اللحظة.",
    rec_start: "ابدأ تسجيل عرض ٣ دقائق",
    rec_stop: "إيقاف مبكر",
    rec_download: "تنزيل الفيديو",
    rec_note: "يحتاج التسجيل إلى ثلاث دقائق حقيقية — التسجيل يحدث بالزمن الحقيقي ولا يمكن تسريعه. يُصدَّر MP4 إذا كان المتصفح يدعمه، وإلا WebM.",
    rec_ready: "جاهز. ابدأ التسجيل ثم اختر تبويب هذه الصفحة في نافذة المشاركة.",
    rec_need_image: "اختر صورة ورقة أولاً — العرض يشخّص صورة حقيقية ولا يُنتج نتيجة مُزيَّفة.",
    rec_granting: "بانتظار إذن مشاركة الشاشة من المتصفح…",
    rec_preroll: "تمت المشاركة — تبدأ الجولة خلال ثلاث ثوانٍ. أبعد يدك عن الفأرة.",
    rec_running: "جارٍ التسجيل. لا تلمس الفأرة أو لوحة المفاتيح.",
    rec_finalising: "جارٍ تجهيز ملف الفيديو…",
    rec_done: "اكتمل التسجيل. الملف جاهز للتنزيل.",
    rec_early: "تم الإيقاف المبكر — سُجّل ما كان قد اكتمل حتى تلك اللحظة.",
    rec_failed: "لم يصل أي بيانات إلى التسجيل. جرّب مرة أخرى.",
    rec_no_cap: "هذا المتصفح لا يدعم تسجيل الشاشة (MediaRecorder أو getDisplayMedia غير متاحين).",
    rec_denied: "لم يُمنح إذن المشاركة. أعد المحاولة واختر تبويب هذه الصفحة.",
    rec_seg1: "١/٤ · المشكلة والمنتج",
    rec_seg2: "٢/٤ · تشخيص مباشر بالذكاء الاصطناعي",
    rec_seg3: "٣/٤ · الخطط والاشتراك",
    rec_seg4: "٤/٤ · دعوة لاتخاذ إجراء",
    cta_tag: "نحمي المحصول، ونمكّن المزارع، ونؤمّن السلامة الغذائية.",
    cta_source: "الشيفرة المصدرية على GitHub",

    role_farmer: "مزارع", role_guest: "ضيف", role_admin: "مدير",
  },

  en: {
    nav_how: "How it works", nav_modules: "Modules", nav_why: "Why Hassad?",
    sign_in: "Sign in", start_free: "Start free", create_account: "Create account",
    hero_badge: "AI built for Arab farmers",
    badge_probable: "Probable diagnosis · low confidence",
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
    analyze: "Analyze", analyze_busy: "Analyzing…", r_conf: "Confidence", r_sev: "Leaf affected",
    card_symptoms: "Observed symptoms", card_guidance: "Agronomic guidance",
    history_hint: "Stored in your browser only — never sent to a server.",
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

    /* Pricing panel — a UI demo. No payment gateway exists behind it, so
       the copy says so rather than letting a price imply a checkout. */
    group_plan: "SERVICES", nav_pricing: "Pricing & Business Model",
    pricing_t: "Pricing & business model",
    pricing_hint: "A preview of the pricing interface — no payment gateway is connected. Every plan is free during this phase.",
    pricing_demo: "Demo preview",
    pricing_note: "Prices shown are illustrative for this preview only. Nothing is charged, and every plan stays open and free.",
    plan_free_badge: "Current / Free",
    plan_free_t: "Free", plan_free_seg: "Smallholder farmers · B2C",
    plan_free_amount: "$0", plan_free_per: "forever",
    plan_free_d: "Everything a farmer needs to start — instant diagnosis at no cost.",
    plan_free_f1: "A limited number of crop scans each month",
    plan_free_f2: "Sub-second AI leaf diagnosis",
    plan_free_f3: "Basic Arabic and English guidance",
    plan_free_f4: "No card required",
    plan_free_cta: "Start free",
    plan_pro_badge: "Preview",
    plan_pro_t: "Pro", plan_pro_seg: "Independent growers · B2C",
    plan_pro_amount: "$9", plan_pro_per: "per month",
    plan_pro_d: "Full field tracking and an unlimited diagnosis history across the season.",
    plan_pro_f1: "Unlimited scans",
    plan_pro_f2: "Historical field diagnostic reports",
    plan_pro_f3: "Priority cloud AI inference",
    plan_pro_f4: "Full diagnosis log with confidence figures",
    plan_pro_cta: "Subscribe now",
    plan_ent_badge: "By contract",
    plan_ent_t: "Enterprise & Co-op", plan_ent_seg: "Co-ops & large farms · B2B",
    plan_ent_amount: "$49", plan_ent_per: "per month",
    plan_ent_d: "Seasonal contracts built for large farms and agricultural cooperatives.",
    plan_ent_f1: "Customized seasonal contracts",
    plan_ent_f2: "Multi-field monitoring dashboard",
    plan_ent_f3: "Regional data API integration",
    plan_ent_f4: "Field training and priority support",
    plan_ent_cta: "Contact sales",
    /* Title + body together say the required sentence once. Carrying it in
       both would make a small dialog greet the reader with the same words
       two lines apart. */
    plan_modal_t: "Thank you for your interest!",
    plan_modal_b: "Hassad AI is currently in early preview with free open access.",
    plan_modal_plan: "Plan",
    plan_modal_note: "We will let you know the moment paid plans open — and you can keep using Hassad for free in the meantime.",
    plan_modal_ok: "Got it",

    /* --- sign-up handoff: plan selection, then a demo checkout ---------- */
    su_title: "Choose your plan",
    su_pay_t: "Payment",
    su_account: "Account created",
    su_step_plan: "1 · Plan",
    su_step_pay: "2 · Payment",
    su_pick: "The plan you want to start on",
    su_free_t: "Free", su_free_amt: "$0",
    su_free_f: "Basic access with a limited number of scans each month",
    su_pro_t: "Pro", su_pro_amt: "$9",
    su_pro_f: "Unlimited scans and historical field reports · per month",
    su_ent_t: "Enterprise", su_ent_amt: "$49",
    su_ent_f: "Multi-user co-op access and regional analytics · per month",
    su_continue: "Continue to payment",
    su_skip: "Skip for now (Continue with Free)",
    su_back: "Back",
    su_due: "Amount",
    su_pay_demo: "Demo only: nothing is charged and these details are never sent anywhere — use a test number.",
    su_card_num: "Card number",
    su_card_exp: "Expiry (MM/YY)",
    su_card_cvv: "Security code (CVV)",
    su_card_holder: "Cardholder name",
    su_pay_cta: "Complete Subscription",
    su_bad_number: "Enter a full card number of 15 digits or more.",
    su_bad_expiry: "That expiry date is invalid or already past.",
    su_bad_cvv: "The security code is 3 or 4 digits.",
    su_bad_holder: "Enter the cardholder name.",
    su_ok: "Subscription confirmed — welcome aboard!",
    su_free_ok: "Continuing with the Free plan.",

    /* ---- demo recorder ---- */
    group_dev: "Developer tools",
    nav_record: "Demo video recorder",
    rec_t: "Demo video recorder",
    rec_hint: "Drives the real interface on a fixed three-minute clock, then exports the file. Nothing is drawn from scratch: every frame is the product itself responding.",
    rec_flag: "Live capture",
    rec_sample_l: "Leaf photo the demo will diagnose",
    rec_step1: "Pick this tab in the screen-sharing dialog the browser opens.",
    rec_step2: "Take your hand off the mouse: the timeline moves by itself for three minutes.",
    rec_step3: "To stop early press Escape or the stop button — everything recorded so far is kept.",
    rec_start: "Record the 3-minute demo",
    rec_stop: "Stop early",
    rec_download: "Download the video",
    rec_note: "The recording needs three real minutes — capture runs in real time and cannot be sped up. MP4 is exported where the browser supports it, WebM otherwise.",
    rec_ready: "Ready. Start recording, then choose this tab in the sharing dialog.",
    rec_need_image: "Choose a leaf photo first — the demo diagnoses a real image and does not invent a result.",
    rec_granting: "Waiting for the browser to grant screen sharing…",
    rec_preroll: "Sharing is on — the walkthrough starts in three seconds. Take your hand off the mouse.",
    rec_running: "Recording. Do not touch the mouse or keyboard.",
    rec_finalising: "Preparing the video file…",
    rec_done: "Recording complete. The file is ready to download.",
    rec_early: "Stopped early — everything completed up to that point was recorded.",
    rec_failed: "No data reached the recorder. Please try again.",
    rec_no_cap: "This browser cannot record the screen (MediaRecorder or getDisplayMedia is unavailable).",
    rec_denied: "Screen sharing was not granted. Try again and pick this tab.",
    rec_seg1: "1/4 · The problem and the product",
    rec_seg2: "2/4 · Live AI diagnosis",
    rec_seg3: "3/4 · Plans and subscription",
    rec_seg4: "4/4 · Call to action",
    cta_tag: "Protecting crops, empowering farmers, securing the food supply.",
    cta_source: "Source code on GitHub",

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
