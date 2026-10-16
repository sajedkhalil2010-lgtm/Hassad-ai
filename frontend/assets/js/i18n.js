/* =====================================================================
   i18n.js — Arabic-first bilingual strings (ar / en) + direction control
   ===================================================================== */

const S = {
  ar: {
    /* common */
    "common.loading": "جارٍ التحميل…",
    "common.cancel": "إلغاء",
    "common.close": "إغلاق",
    "common.confirm": "تأكيد",
    "common.delete": "حذف",
    "common.retry": "إعادة المحاولة",
    "common.optional": "اختياري",
    "common.comingSoon": "قريبًا",
    "common.seeDemo": "شاهد العرض",
    "common.demoPreview": "عرض توضيحي حي",
    "common.new": "جديد",
    "common.exit": "خروج",
    "common.error": "حدث خطأ",
    "common.tryAgain": "حاول مرة أخرى",

    /* nav / shell */
    "nav.dashboard": "لوحة التحكم",
    "nav.studio": "استوديو التشخيص",
    "nav.history": "سجل التشخيصات",
    "nav.chat": "المساعد الزراعي",
    "nav.sectionMain": "الرئيسية",
    "nav.sectionTools": "الأدوات",
    "nav.logout": "تسجيل الخروج",
    "nav.login": "تسجيل الدخول",
    "nav.start": "ابدأ مجانًا",
    "nav.openApp": "فتح التطبيق",
    "nav.apiOn": "متصل",
    "nav.apiOff": "غير متصل",
    "nav.menu": "القائمة",

    /* landing — hero */
    "hero.badge": "ذكاء اصطناعي خاص بالمزارعين العرب",
    "hero.title1": "حاصد ذكي لمحصولك،",
    "hero.titleGrad": "تشخيص فوري بالصورة",
    "hero.title2": "",
    "hero.sub": "ارفع صورة ورقة نبات واحدة، وسيتولى حَصاد AI تحليلها وتحديد المرض وتقديم خطة علاج موثوقة — خلال ثوانٍ، وبواجهة عربية أنيقة.",
    "hero.cta1": "ابدأ التشخيص الآن",
    "hero.cta2": "كيف يعمل؟",
    "hero.meta1": "خصوصية أولًا — صورك تُعالج بأمان",
    "hero.meta2": "يعمل بدون خبرة تقنية",
    "hero.meta3": "دعم كامل للعربية RTL",
    "hero.mockT": "النتيجة: بليط متأخر",
    "hero.mockS": "الطماطم · ثقة النموذج",
    "hero.chip1": "خطة علاج جاهزة",
    "hero.chip2": "ثقة 92%",
    "hero.chip3": "تم التحقق",

    /* stats */
    "stats.1n": "94%",
    "stats.1l": "دقة النموذج المخبري",
    "stats.2n": "3 خطوات",
    "stats.2l": "من الصورة إلى الخطة",
    "stats.3n": "< 30ث",
    "stats.3l": "متوسط زمن التشخيص",
    "stats.4n": "24/7",
    "stats.4l": "مساعد زراعي دائم",

    /* how it works */
    "how.eyebrow": "كيف يعمل",
    "how.title": "ثلاث خطوات ",
    "how.titleGrad": "فقط",
    "how.sub": "صُمم حَصاد AI ليكون بسيطًا كالتقاط صورة — دون تعقيد ودون واجهات معقّدة.",
    "step1.t": "ارفع صورة الورقة",
    "step1.d": "التقط صورة واضحة للورقة المصابة واسحبها إلى منطقة الرفع.",
    "step1.li1": "يدعم JPG وPNG وWEBP حتى 8 ميجابايت",
    "step1.li2": "فحص ذكي لوضوح الصورة قبل التحليل",
    "step1.li3": "لا حاجة لأي معدات خاصة",
    "step1.svHint": "اسحب الصورة هنا",
    "step2.t": "تحليل بالذكاء الاصطناعي",
    "step2.d": "يصنّف النموذج المحصول ثم يكشف المرض بدقة عالية.",
    "step2.li1": "تحقق من جودة الصورة (الضبابية والأبعاد)",
    "step2.li2": "تصنيف المحصول ثم تشخيص المرض",
    "step2.li3": "نماذج بصرية حديثة على السحابة",
    "step2.svHint": "جارٍ التحليل…",
    "step3.t": "تشخيص وخطة علاج",
    "step3.d": "تظهر النتيجة مع نسبة الثقة وخطة علاج مفصلة خطوة بخطوة.",
    "step3.li1": "بطاقة تشخيص مرتّبة مع نسبة ثقة",
    "step3.li2": "إجراءات فورية وخيارات علاجية",
    "step3.li3": "متابعة وقائية وتوصيات للمزارع",
    "step3.svHint": "النتيجة جاهزة",

    /* modules */
    "mod.eyebrow": "الوحدات",
    "mod.title": "ثلاث وحدات ",
    "mod.titleGrad": "متكاملة",
    "mod.sub": "كل ما يحتاجه المزارع والأخصائي في مكان واحد — تجربة متكاملة وسريعة.",
    "mod.doctor.t": "طبيب المحاصيل",
    "mod.doctor.d": "تشخيص أمراض النباتات من صورة واحدة مع خطة علاج موثقة.",
    "mod.doctor.link": "افتح الاستوديو",
    "mod.chat.t": "المساعد الزراعي",
    "mod.chat.d": "اسأل عن أي محصول أو مرض واحصل على إجابة فورية مدعومة بالمعرفة.",
    "mod.chat.link": "جرّب المحادثة",
    "mod.yield.t": "التوقع الإنتاجي",
    "mod.yield.d": "نمذجة الإنتاجية المتوقعة للموسم القادم بناءً على بيانات مزجّجة.",
    "mod.yield.link": "شاهد العرض",
    "mod.doctor.prev": "معاينة حية: من صورة إلى تشخيص",
    "mod.chat.prev": "معاينة حية: محادثة مع المساعد",
    "mod.yield.prev": "معاينة حية: توقع الموسم القادم",

    /* features */
    "feat.eyebrow": "لماذا حَصاد؟",
    "feat.title": "مصمّم ",
    "feat.titleGrad": "للميدان",
    "feat.sub": "واجهة عربية أولاً، تعمل بسلاسة على الجوال والحاسوب، وتحترم خصوصيتك.",
    "feat1.t": "واجهة عربية RTL",
    "feat1.d": "تصميم عربي أصيل من اليمين لليسار مع دعم لغتين كامل.",
    "feat2.t": "أداء فوري",
    "feat2.d": "حركة سلسة وتصميم زجاجي خفيف يشبه تطبيقات الخط الزمني العالمية.",
    "feat3.t": "خصوصية وأمان",
    "feat3.d": "مصادقة JWT محفوظة تلقائيًا في متصفحك، ولا نشارك بياناتك مع أحد.",
    "feat4.t": "يعمل دون اتصال جيد",
    "feat4.d": "طابور مزامنة يحفظ التشخيصات ويرسلها عند عودة الشبكة.",
    "feat5.t": "ثقة قابلة للقياس",
    "feat5.d": "نسبة ثقة واضحة لكل تشخيص ومسار مراجعة الأخصائيين.",
    "feat6.t": "مساعد مدعوم بالمعرفة",
    "feat6.d": "إجابات مدعومة بمصادر زراعية موثوقة مع بطاقات اقتباس.",

    /* cta + footer */
    "cta.title": "جاهز لحصاد أفضل؟",
    "cta.sub": "أنشئ حسابك المجاني وابدأ تشخيص محاصيلك خلال دقيقة واحدة.",
    "cta.btn": "إنشاء حساب مجاني",
    "foot.rights": "جميع الحقوق محفوظة",
    "foot.made": "صُنع بعناية للمزارعين",

    /* auth */
    "auth.loginT": "مرحبًا بعودتك",
    "auth.loginS": "سجّل دخولك لمتابعة عملك في حَصاد",
    "auth.regT": "أنشئ حسابك",
    "auth.regS": "دقيقة واحدة وتصبح جاهزًا للتشخيص",
    "auth.tabLogin": "تسجيل الدخول",
    "auth.tabReg": "حساب جديد",
    "auth.email": "البريد الإلكتروني",
    "auth.password": "كلمة المرور",
    "auth.name": "الاسم الكامل",
    "auth.role": "نوع الحساب",
    "auth.roleFarmer": "مزارع",
    "auth.roleFarmerS": "أصوّر وأشخّص",
    "auth.roleAgronomist": "أخصائي زراعي",
    "auth.roleAgronomistS": "أراجع الحالات",
    "auth.phone": "رقم الهاتف",
    "auth.showPw": "إظهار كلمة المرور",
    "auth.hidePw": "إخفاء كلمة المرور",
    "auth.submitLogin": "دخول",
    "auth.submitReg": "إنشاء الحساب",
    "auth.noAccount": "ليس لديك حساب؟",
    "auth.hasAccount": "لديك حساب بالفعل؟",
    "auth.pwHint": "8 أحرف على الأقل، أحرف وأرقام",
    "auth.strength": ["ضعيفة", "متوسطة", "جيدة", "ممتازة"],
    "auth.err.email": "أدخل بريدًا إلكترونيًا صحيحًا",
    "auth.err.pw": "كلمة المرور: 8 أحرف على الأقل بأحرف وأرقام",
    "auth.err.name": "الاسم حرفان على الأقل",
    "auth.ok.login": "أهلًا بك من جديد!",
    "auth.ok.reg": "تم إنشاء حسابك بنجاح!",

    /* dashboard */
    "dash.hi": "أهلًا",
    "dash.welcome": "ماذا تريد أن تفعل اليوم؟",
    "dash.welcomeS": "صوّر ورقة، اسأل المساعد، أو تابع تشخيصاتك السابقة.",
    "dash.newScan": "تشخيص جديد",
    "dash.askBot": "اسأل المساعد",
    "dash.stat.total": "إجمالي التشخيصات",
    "dash.stat.confirmed": "مؤكدة",
    "dash.stat.review": "قيد المراجعة",
    "dash.stat.avg": "متوسط الثقة",
    "dash.recent": "أحدث التشخيصات",
    "dash.recentEmpty": "لا توجد تشخيصات بعد — ابدأ بأول صورة!",
    "dash.quick": "إجراءات سريعة",
    "dash.q1.t": "استوديو التشخيص",
    "dash.q1.d": "ارفع صورة ورقة جديدة",
    "dash.q2.t": "اسأل خبيرًا",
    "dash.q2.d": "محادثة زراعية فورية",
    "dash.q3.t": "السجل الكامل",
    "dash.q3.d": "تصفح كل التشخيصات",
    "dash.q4.t": "مزامنة",
    "dash.q4.d": "مزامنة بياناتك الآن",
    "dash.api.ok": "الخادم يعمل",
    "dash.api.bad": "تعذر الوصول للخادم",

    /* studio */
    "studio.title": "استوديو التشخيص",
    "studio.sub": "ارفع صورة الورقة واحصل على تشخيص وخطة علاج",
    "studio.dropT": "اسحب صورة الورقة هنا",
    "studio.dropS": "أو اضغط للاختيار من جهازك",
    "studio.rule1": "JPG · PNG · WEBP",
    "studio.rule2": "حتى 8MB",
    "studio.rule3": "وضوح ≥ 64px",
    "studio.analyze": "تحليل الصورة",
    "studio.analyze2": "تأكيد المحصول وإعادة التحليل",
    "studio.other": "صورة أخرى",
    "studio.remove": "إزالة الصورة",
    "prog.up": "رفع الصورة",
    "prog.quality": "فحص جودة الصورة",
    "prog.crop": "تصنيف المحصول",
    "prog.disease": "تشخيص المرض",
    "prog.plan": "إعداد خطة العلاج",
    "prog.slowHint": "قد يستغرق التحليل حتى 45 ثانية — إذا تأخر النموذج فهذا طبيعي مؤقتاً، انتظر قليلاً",
    "prog.title": "جارٍ التحليل",
    "cand.title": "اختر المحصول",
    "cand.sub": "لم نتمكن من تحديد المحصول بدقة كافية — اختر أحد هذه الاحتمالات لإكمال التشخيص:",
    "cand.confirm": "تأكيد المحصول ومتابعة التشخيص",
    "res.confidence": "الثقة",
    "res.status.confirmed": "تشخيص مؤكد",
    "res.status.in_review": "قيد مراجعة الأخصائي",
    "res.status.retake_required": "يلزم إعادة التصوير",
    "res.status.queued": "في الطابور",
    "res.status.awaiting_crop_selection": "بانتظار اختيار المحصول",
    "res.status.rejected": "مرفوض",
    "res.crop": "المحصول",
    "res.disease": "المرض",
    "plan.summary": "ملخص الحالة",
    "plan.immediate": "إجراءات فورية",
    "plan.control": "خيارات العلاج",
    "plan.monitoring": "المتابعة",
    "plan.prevention": "الوقاية",
    "plan.disclaimer": "إخلاء مسؤولية",
    "q.res": "الأبعاد",
    "q.size": "الحجم",
    "q.blur": "الوضوح",
    "q.ok": "ممتاز",
    "q.blurry": "ضبابية",
    "res.askBot": "اسأل المساعد عن هذا المرض",
    "res.viewHistory": "عرض في السجل",
    "res.mock": "نتيجة تجريبية (وضع عدم الاتصال): لم يتاح نموذج الرؤية، والنتيجة أدناه محاكاة للمعاينة فقط.",
    "res.mockShort": "نتيجة تجريبية",
    "res.newScan": "فحص نبات آخر",
    "retake.title": "الصورة غير واضحة",
    "retake.sub": "نحتاج صورة أوضح لنتمكن من التشخيص بدقة. جرّب هذه النصائح:",
    "retake.tip1": "التقط الصورة في إضاءة نهار طبيعية",
    "retake.tip2": "قرّب الكاميرا من الورقة المصابة",
    "retake.tip3": "تجنب الرجوع والضبابية في الصورة",
    "retake.btn": "إعادة التصوير",
    "review.title": "سيراجعها أخصائي",
    "review.sub": "ثقة التشخيص بين 50% و75% — أرسلنا الحالة لأحد أخصائيينا ليؤكدها قريبًا.",
    "toasts.needImage": "اختر صورة أولًا",
    "toasts.uploaded": "تم استلام الصورة",
    "toasts.ok": "اكتمل التحليل",
    "toasts.deleted": "تم حذف التشخيص",
    "toasts.copyId": "تم نسخ المعرّف",
    "toast.uploadFail": "فشل رفع الصورة",

    /* history */
    "hist.title": "سجل التشخيصات",
    "hist.sub": "كل تشخيصاتك في مكان واحد",
    "hist.filter.all": "الكل",
    "hist.empty": "لا توجد تشخيصات مطابقة",
    "hist.emptyS": "ابدأ تشخيص جديد من استوديو التشخيص.",
    "hist.deleteAsk": "حذف هذا التشخيص؟",
    "hist.deleteS": "لا يمكن التراجع عن هذا الإجراء.",
    "hist.open": "عرض التفاصيل",
    "hist.count": "تشخيص",
    "hist.id": "المعرّف",
    "hist.date": "التاريخ",
    "hist.notes": "ملاحظات",

    /* chat */
    "chat.title": "المساعد الزراعي",
    "chat.sub": "متصل بقاعدة المعرفة الزراعية",
    "chat.placeholder": "اسأل عن محصولك أو مرضه…",
    "chat.hint": "Enter للإرسال · Shift+Enter لسطر جديد",
    "chat.send": "إرسال",
    "chat.stop": "إيقاف",
    "chat.greeting": "مرحبًا! أنا مساعدك الزراعي 🌱 اسألني عن أمراض المحاصيل، ريّها، أو كيفية علاجها وسأجيبك فورًا.",

    /* onboarding gateway (first screen) */
    "onb.title": "أهلًا بك في حَصاد",
    "onb.sub": "أخبرنا عن مزرعتك — الاسم والموقع والمحصول — وسنبدأ تشخيص محاصيلك بالذكاء الاصطناعي فورًا.",
    "onb.name": "الاسم",
    "onb.namePh": "مثال: محمد بن علي",
    "onb.location": "موقع المزرعة",
    "onb.locationPh": "مثال: تبسة، الجزائر",
    "onb.crop": "المحصول الأساسي",
    "onb.submit": "دخول المنصة",
    "onb.have": "لديك حساب بالفعل؟",
    "onb.login": "تسجيل الدخول",
    "onb.requiredName": "الرجاء إدخال اسمك للمتابعة",

    /* diagnosis plan + dashboard extras */
    "plan.symptoms": "الأعراض المُشاهدة",
    "dash.farm": "المزرعة",
    "chat.sugg1": "كيف أعالج بليط الطماطم؟",
    "chat.sugg2": "علامات نقص النيتروجين",
    "chat.sugg3": "متى أري محصولي؟",
    "chat.sugg4": "أفضل طريقة لمكافحة الذبابة البيضاء",
    "chat.new": "محادثة جديدة",
    "chat.err": "تعذّر إرسال الرسالة",
    "chat.needLogin": "سجّل دخولك لاستخدام المساعد",
    "chat.mode.ai": "مساعد ذكي",
    "chat.mode.rag": "بحث في المعرفة",
    "chat.mode.local": "وضع محلي",
    "chat.waitHint": "قد يستغرق الرد حتى دقيقتين على الاتصال المجاني — نُجهّز لك إجابة دقيقة…",
    "chat.waitSec": " ث",
    "chat.sessionErr": "تعذّر بدء المحادثة — تحقّق من اتصالك وحاول مرة أخرى.",

    /* demo previews */
    "demo.scan": "المسح…",
    "demo.result": "النتيجة",
    "demo.yield.t": "التوقع الإنتاجي للموسم القادم",
    "demo.yield.sub": "مبني على نمذجة موسمية وبيانات الطقس",
    "demo.yield.now": "المحصول الحالي",
    "demo.yield.pred": "التوقع",
    "demo.chat.user": "عندي بقع بنية على أوراق الطماطم، ماذا أفعل؟",
    "demo.chat.ai": "الأعراض تطابق البليط المتأخر. أبدأ بمشهد الأوراق المصابة، ورشّ نحاسي وفق الجدول، وسأرسل لك خطة مفصلة…",
    "demo.open": "افتح الوحدة",

    /* errors */
    "err.network": "تعذّر الاتصال بالخادم — تأكد من تشغيل الـ backend",
    "err.timeout": "انتهى وقت الاتصال بالنموذج — أعد المحاولة بعد قليل",
    "err.session": "انتهت الجلسة — سجّل دخولك من جديد",
    "err.creds": "بيانات الدخول غير صحيحة",
    "err.server": "خطأ في الخادم",
    "err.unauth": "يجب تسجيل الدخول أولًا",
    "err.upload": "فشل الرفع — حاول مرة أخرى",
    "err.tooLarge": "الصورة أكبر من 8MB",
  },

  en: {
    /* common */
    "common.loading": "Loading…",
    "common.cancel": "Cancel",
    "common.close": "Close",
    "common.confirm": "Confirm",
    "common.delete": "Delete",
    "common.retry": "Retry",
    "common.optional": "optional",
    "common.comingSoon": "Coming soon",
    "common.seeDemo": "See demo",
    "common.demoPreview": "Live preview",
    "common.new": "New",
    "common.exit": "Exit",
    "common.error": "Something went wrong",
    "common.tryAgain": "Try again",

    /* nav / shell */
    "nav.dashboard": "Dashboard",
    "nav.studio": "Diagnostic Studio",
    "nav.history": "Diagnosis History",
    "nav.chat": "Agri Assistant",
    "nav.sectionMain": "Main",
    "nav.sectionTools": "Tools",
    "nav.logout": "Log out",
    "nav.login": "Log in",
    "nav.start": "Get started",
    "nav.openApp": "Open app",
    "nav.apiOn": "Online",
    "nav.apiOff": "Offline",
    "nav.menu": "Menu",

    /* hero */
    "hero.badge": "AI built for Arab farmers",
    "hero.title1": "A smart harvester for your crop —",
    "hero.titleGrad": "instant diagnosis from a photo",
    "hero.title2": "",
    "hero.sub": "Upload a single leaf photo and Hassad AI analyzes it, identifies the disease and delivers a trusted treatment plan — in seconds, with an elegant Arabic-first interface.",
    "hero.cta1": "Start diagnosing",
    "hero.cta2": "How it works",
    "hero.meta1": "Privacy first — your images are handled safely",
    "hero.meta2": "No technical skills needed",
    "hero.meta3": "Full Arabic RTL support",
    "hero.mockT": "Result: Late blight",
    "hero.mockS": "Tomato · model confidence",
    "hero.chip1": "Treatment plan ready",
    "hero.chip2": "92% confidence",
    "hero.chip3": "Verified",

    /* stats */
    "stats.1n": "94%",
    "stats.1l": "Lab-validated accuracy",
    "stats.2n": "3 steps",
    "stats.2l": "Photo to treatment plan",
    "stats.3n": "< 30s",
    "stats.3l": "Average diagnosis time",
    "stats.4n": "24/7",
    "stats.4l": "Always-on agronomy assistant",

    /* how it works */
    "how.eyebrow": "How it works",
    "how.title": "Just three ",
    "how.titleGrad": "steps",
    "how.sub": "Hassad AI is designed to feel as simple as taking a photo — no complexity, no cluttered screens.",
    "step1.t": "Upload the leaf photo",
    "step1.d": "Take a clear shot of the affected leaf and drop it into the upload area.",
    "step1.li1": "Supports JPG, PNG & WEBP up to 8 MB",
    "step1.li2": "Smart image-quality check before analysis",
    "step1.li3": "No special equipment required",
    "step1.svHint": "Drop your photo here",
    "step2.t": "AI analysis",
    "step2.d": "The model classifies the crop, then detects the disease with high accuracy.",
    "step2.li1": "Quality gate (blur & dimensions)",
    "step2.li2": "Crop classification, then disease diagnosis",
    "step2.li3": "Modern vision models in the cloud",
    "step2.svHint": "Analyzing…",
    "step3.t": "Diagnosis & treatment",
    "step3.d": "You get a confidence-scored result plus a step-by-step treatment plan.",
    "step3.li1": "Structured diagnosis card with confidence",
    "step3.li2": "Immediate actions & control options",
    "step3.li3": "Monitoring, prevention and farmer tips",
    "step3.svHint": "Result ready",

    /* modules */
    "mod.eyebrow": "Modules",
    "mod.title": "Three ",
    "mod.titleGrad": "integrated modules",
    "mod.sub": "Everything farmer and agronomist need in one place — fast, coherent experience.",
    "mod.doctor.t": "Crop Doctor",
    "mod.doctor.d": "Diagnose plant diseases from one photo with a documented treatment plan.",
    "mod.doctor.link": "Open studio",
    "mod.chat.t": "Agronomy Chatbot",
    "mod.chat.d": "Ask about any crop or disease and get instant, knowledge-backed answers.",
    "mod.chat.link": "Try the chat",
    "mod.yield.t": "Yield Prediction",
    "mod.yield.d": "Forecast next season's productivity from blended field data.",
    "mod.yield.link": "View demo",
    "mod.doctor.prev": "Live preview: photo → diagnosis",
    "mod.chat.prev": "Live preview: assistant chat",
    "mod.yield.prev": "Live preview: next-season forecast",

    /* features */
    "feat.eyebrow": "Why Hassad",
    "feat.title": "Designed ",
    "feat.titleGrad": "for the field",
    "feat.sub": "Arabic-first UI that flows on mobile and desktop — and respects your privacy.",
    "feat1.t": "Arabic-first RTL",
    "feat1.d": "Authentic right-to-left design with full bilingual support.",
    "feat2.t": "Instant performance",
    "feat2.d": "Smooth motion and lightweight glass visuals like world-class apps.",
    "feat3.t": "Privacy & security",
    "feat3.d": "JWT auth auto-persisted in your browser; data is never shared.",
    "feat4.t": "Works offline",
    "feat4.d": "A sync queue keeps diagnoses safe and flushes when you reconnect.",
    "feat5.t": "Measurable confidence",
    "feat5.d": "A clear confidence score for every diagnosis, plus agronomist review.",
    "feat6.t": "Knowledge-backed answers",
    "feat6.d": "Answers grounded in trusted agronomy sources with citation cards.",

    /* cta + footer */
    "cta.title": "Ready for a better harvest?",
    "cta.sub": "Create your free account and diagnose your first crop in a minute.",
    "cta.btn": "Create free account",
    "foot.rights": "All rights reserved",
    "foot.made": "Crafted for farmers",

    /* auth */
    "auth.loginT": "Welcome back",
    "auth.loginS": "Log in to continue working with Hassad",
    "auth.regT": "Create your account",
    "auth.regS": "One minute and you are ready to diagnose",
    "auth.tabLogin": "Log in",
    "auth.tabReg": "Sign up",
    "auth.email": "Email",
    "auth.password": "Password",
    "auth.name": "Full name",
    "auth.role": "Account type",
    "auth.roleFarmer": "Farmer",
    "auth.roleFarmerS": "I take photos & diagnose",
    "auth.roleAgronomist": "Agronomist",
    "auth.roleAgronomistS": "I review cases",
    "auth.phone": "Phone",
    "auth.showPw": "Show password",
    "auth.hidePw": "Hide password",
    "auth.submitLogin": "Log in",
    "auth.submitReg": "Create account",
    "auth.noAccount": "No account yet?",
    "auth.hasAccount": "Already have an account?",
    "auth.pwHint": "8+ characters, letters and numbers",
    "auth.strength": ["Weak", "Fair", "Good", "Excellent"],
    "auth.err.email": "Enter a valid email address",
    "auth.err.pw": "Password: 8+ chars with letters and numbers",
    "auth.err.name": "Name must be at least 2 characters",
    "auth.ok.login": "Welcome back!",
    "auth.ok.reg": "Your account is ready!",

    /* dashboard */
    "dash.hi": "Hi",
    "dash.welcome": "What would you like to do today?",
    "dash.welcomeS": "Photograph a leaf, ask the assistant, or review past diagnoses.",
    "dash.newScan": "New diagnosis",
    "dash.askBot": "Ask the assistant",
    "dash.stat.total": "Total diagnoses",
    "dash.stat.confirmed": "Confirmed",
    "dash.stat.review": "In review",
    "dash.stat.avg": "Avg. confidence",
    "dash.recent": "Recent diagnoses",
    "dash.recentEmpty": "No diagnoses yet — start with your first photo!",
    "dash.quick": "Quick actions",
    "dash.q1.t": "Diagnostic Studio",
    "dash.q1.d": "Upload a new leaf photo",
    "dash.q2.t": "Ask an expert",
    "dash.q2.d": "Instant agronomy chat",
    "dash.q3.t": "Full history",
    "dash.q3.d": "Browse all diagnoses",
    "dash.q4.t": "Sync",
    "dash.q4.d": "Sync your data now",
    "dash.api.ok": "Server online",
    "dash.api.bad": "Server unreachable",

    /* studio */
    "studio.title": "Diagnostic Studio",
    "studio.sub": "Upload a leaf photo and get a diagnosis with a treatment plan",
    "studio.dropT": "Drop a leaf photo here",
    "studio.dropS": "or click to choose from your device",
    "studio.rule1": "JPG · PNG · WEBP",
    "studio.rule2": "Up to 8MB",
    "studio.rule3": "≥ 64px sharp",
    "studio.analyze": "Analyze photo",
    "studio.analyze2": "Confirm crop & re-analyze",
    "studio.other": "Another photo",
    "studio.remove": "Remove photo",
    "prog.up": "Uploading photo",
    "prog.quality": "Checking image quality",
    "prog.crop": "Classifying crop",
    "prog.disease": "Diagnosing disease",
    "prog.plan": "Building treatment plan",
    "prog.slowHint": "Analysis can take up to 45 seconds — a slow model response is normal right now, please wait",
    "prog.title": "Analyzing",
    "cand.title": "Select the crop",
    "cand.sub": "We could not pin the crop confidently — pick one of these to finish the diagnosis:",
    "cand.confirm": "Confirm crop & diagnose",
    "res.confidence": "Confidence",
    "res.status.confirmed": "Confirmed diagnosis",
    "res.status.in_review": "Agronomist review",
    "res.status.retake_required": "Retake photo",
    "res.status.queued": "Queued",
    "res.status.awaiting_crop_selection": "Awaiting crop selection",
    "res.status.rejected": "Rejected",
    "res.crop": "Crop",
    "res.disease": "Disease",
    "plan.summary": "Case summary",
    "plan.immediate": "Immediate actions",
    "plan.control": "Control options",
    "plan.monitoring": "Monitoring",
    "plan.prevention": "Prevention",
    "plan.disclaimer": "Disclaimer",
    "q.res": "Dimensions",
    "q.size": "Size",
    "q.blur": "Sharpness",
    "q.ok": "Excellent",
    "q.blurry": "Blurry",
    "res.askBot": "Ask the assistant",
    "res.viewHistory": "View in history",
    "res.mock": "Offline demo result: the vision model was unavailable — the result below is a simulated preview.",
    "res.mockShort": "Demo result",
    "res.newScan": "Scan another plant",
    "retake.title": "Photo not clear enough",
    "retake.sub": "We need a sharper photo to diagnose accurately. Try these tips:",
    "retake.tip1": "Shoot in natural daylight",
    "retake.tip2": "Move closer to the affected leaf",
    "retake.tip3": "Avoid shake and blur",
    "retake.btn": "Retake photo",
    "review.title": "An agronomist will review it",
    "review.sub": "Confidence is between 50% and 75% — we sent the case to our specialists for confirmation.",
    "toasts.needImage": "Choose an image first",
    "toasts.uploaded": "Image received",
    "toasts.ok": "Analysis complete",
    "toasts.deleted": "Diagnosis deleted",
    "toasts.copyId": "ID copied",
    "toast.uploadFail": "Upload failed",

    /* history */
    "hist.title": "Diagnosis history",
    "hist.sub": "All your diagnoses in one place",
    "hist.filter.all": "All",
    "hist.empty": "No matching diagnoses",
    "hist.emptyS": "Start a new diagnosis from the studio.",
    "hist.deleteAsk": "Delete this diagnosis?",
    "hist.deleteS": "This action cannot be undone.",
    "hist.open": "Show details",
    "hist.count": "diagnoses",
    "hist.id": "ID",
    "hist.date": "Date",
    "hist.notes": "Notes",

    /* chat */
    "chat.title": "Agri Assistant",
    "chat.sub": "Connected to the agronomy knowledge base",
    "chat.placeholder": "Ask about your crop or disease…",
    "chat.hint": "Enter to send · Shift+Enter for a new line",
    "chat.send": "Send",
    "chat.stop": "Stop",
    "chat.greeting": "Hello! I'm your agronomy assistant 🌱 Ask me about crop diseases, irrigation or treatment and I'll answer right away.",

    /* onboarding gateway (first screen) */
    "onb.title": "Welcome to Hassad",
    "onb.sub": "Tell us about your farm — name, location and main crop — and we'll start AI-powered diagnosis right away.",
    "onb.name": "Your name",
    "onb.namePh": "e.g. Mohamed Ben Ali",
    "onb.location": "Farm location",
    "onb.locationPh": "e.g. Tébessa, Algeria",
    "onb.crop": "Main crop",
    "onb.submit": "Enter the platform",
    "onb.have": "Already have an account?",
    "onb.login": "Sign in",
    "onb.requiredName": "Please enter your name to continue",

    /* diagnosis plan + dashboard extras */
    "plan.symptoms": "Observed symptoms",
    "dash.farm": "Farm",
    "chat.sugg1": "How do I treat late blight?",
    "chat.sugg2": "Signs of nitrogen deficiency",
    "chat.sugg3": "When should I irrigate?",
    "chat.sugg4": "Best way to fight whitefly",
    "chat.new": "New conversation",
    "chat.err": "Could not send the message",
    "chat.needLogin": "Log in to use the assistant",
    "chat.mode.ai": "Smart assistant",
    "chat.mode.rag": "Knowledge search",
    "chat.mode.local": "Offline mode",
    "chat.waitHint": "Answers can take up to two minutes on the free connection — preparing a precise reply…",
    "chat.waitSec": "s",
    "chat.sessionErr": "Couldn't start the conversation — check your connection and try again.",

    /* demo previews */
    "demo.scan": "Scanning…",
    "demo.result": "Result",
    "demo.yield.t": "Next-season yield forecast",
    "demo.yield.sub": "Based on seasonal modeling & weather data",
    "demo.yield.now": "Current crop",
    "demo.yield.pred": "Forecast",
    "demo.chat.user": "I have brown spots on my tomato leaves, what now?",
    "demo.chat.ai": "These symptoms match late blight. Remove affected foliage, apply a copper spray on schedule, and I'll send you a detailed plan…",
    "demo.open": "Open module",

    /* errors */
    "err.network": "Cannot reach the server — make sure the backend is running",
    "err.timeout": "Timed out waiting for the model — please try again in a moment",
    "err.session": "Session expired — please log in again",
    "err.creds": "Invalid email or password",
    "err.server": "Server error",
    "err.unauth": "Please log in first",
    "err.upload": "Upload failed — try again",
    "err.tooLarge": "Image is larger than 8MB",
  },
};

let LANG = "ar";

export function getLang() {
  return LANG;
}

export function setLang(l) {
  LANG = l === "en" ? "en" : "ar";
  try {
    localStorage.setItem("hassad.lang", LANG);
  } catch (e) { /* private mode */ }
  document.documentElement.lang = LANG;
  document.documentElement.dir = LANG === "ar" ? "rtl" : "ltr";
  return LANG;
}

export function loadLang() {
  let saved = null;
  try {
    saved = localStorage.getItem("hassad.lang");
  } catch (e) { /* ignore */ }
  return setLang(saved || "ar");
}

/** Translate key → string for the active language (falls back to ar). */
export function t(key) {
  const table = S[LANG] || S.ar;
  let v = table[key];
  if (v === undefined) v = S.ar[key];
  if (v === undefined) return key;
  if (Array.isArray(v)) return v;
  return v;
}

/** Pick item `i` from an i18n array (e.g. password strength labels). */
export function tAt(key, i) {
  const arr = t(key);
  return Array.isArray(arr) ? arr[Math.min(i, arr.length - 1)] : arr;
}

/** Format a localized date/time from an ISO string. */
export function fmtDate(iso, withTime) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const opts = withTime
      ? { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
      : { year: "numeric", month: "short", day: "numeric" };
    return new Intl.DateTimeFormat(LANG === "ar" ? "ar-EG" : "en-GB", opts).format(d);
  } catch (e) {
    return iso;
  }
}

export function fmtTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return new Intl.DateTimeFormat(LANG === "ar" ? "ar-EG" : "en-GB", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(d);
  } catch (e) {
    return "";
  }
}

/** Human-readable byte size. */
export function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
  return (n / (1024 * 1024)).toFixed(1) + " MB";
}
