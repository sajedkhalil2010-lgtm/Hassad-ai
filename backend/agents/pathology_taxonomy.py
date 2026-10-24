"""Curated plant-pathology reference index — 500+ conditions, six domains.

WHY THIS IS A PYTHON MODULE AND NOT PROMPT TEXT
------------------------------------------------
The vision prompt is capped at 12,000 characters by ``test_offline`` and
currently sits at ~11.3k, of which roughly 1.5k is the JSON shape. Inlining
500 disease names would add thousands of tokens to a request that already
carries an image, and the call would 400 on every diagnosis. So the
classification lives HERE — importable, testable, and costing the model
nothing — while the prompt keeps only the six domain *rules* the model needs
to sort what it already knows.

The index is the reference standard the model's answer is measured against,
not a vocabulary the model is confined to. The prompt explicitly tells the
model to "draw on the FULL breadth of plant pathology", so it may name
anything it sees. When it names something this index does not hold, the
diagnosis is flagged ``is_novel_discovery`` — that is the open-ended
discovery channel: the engine is never limited to these entries, but it tells
you when it stepped outside them so the taxonomy can grow.

MATCHING IS DELIBERATELY LENIENT
---------------------------------
A false "novel" is worse than a missed one: it tells an operator to review a
condition we already catalogue. Lookup therefore folds case, punctuation,
Arabic tashkeel and the alef/ya/ta-marbuta variants before comparing, and
also tries the head of a compound name ("Fusarium wilt of tomato" resolves
via "Fusarium wilt"). Nothing here ever reaches the farmer directly — the
model writes ``disease``/``disease_ar`` itself; this index only classifies
and compares.
"""

from __future__ import annotations

import re
import unicodedata

# --- the six domains, in the order the prompt presents them ---------------
# These are the ONLY values ``matched_category`` may take, and the strings
# must match the prompt, the JSON shape and ``MATCHED_CATEGORIES`` exactly —
# a one-character drift would make every domain assertion vacuous.
DOMAIN_FUNGAL = "Fungal Pathogen"
DOMAIN_BACTERIAL = "Bacterial Pathogen"
DOMAIN_VIRAL = "Viral & Viroid"
DOMAIN_NEMATODE = "Nematode & Root Parasite"
DOMAIN_PHYSIOLOGICAL = "Physiological & Abiotic"
DOMAIN_INSECT = "Insect & Structural Damage"

DOMAINS: tuple[str, ...] = (
    DOMAIN_FUNGAL,
    DOMAIN_BACTERIAL,
    DOMAIN_VIRAL,
    DOMAIN_NEMATODE,
    DOMAIN_PHYSIOLOGICAL,
    DOMAIN_INSECT,
)

# --- raw tables -----------------------------------------------------------
# One "English|Arabic" pair per line. Blank lines and `#` comments are
# ignored, so the sections can be grouped by pathology rather than alphabet.

_FUNGAL_RAW = """
# --- blights ---
Early blight|اللفحة المبكرة
Late blight|اللفحة المتأخرة
Gummy stem blight|لفحة السيقان العفصية
Black rot|التعفن الأسود
Stalk rot|تعفن السيقان
Head blight|لفحة الأغاريم
Fusarium head blight|لفحة الأغاريم الفوزاريّة
Gibberella ear rot|تعفن الأذن جيبريلا
Diplodia ear rot|تعفن الأذن ديبلوديا
Pythium damping-off|ال뻔ثرة الصنوبرية
Rhizoctonia damping-off|ال뻔ثرة الفطرية
Damping-off|ال뻔ثرة
Seedling blight|لفحة البادرات
Buckeye rot|تعفن الكستناء
Brown rot|التعفن البني
Brown rot of stone fruit|تعفن الثمار الحجرية البني
Soft rot|التعفن الرخوي
Pink rot|التعفن الوردي
White rot|التعفن الأبيض
Blackleg|الساق الأسود
Black shank|الرقبة السوداء
Brown foot rot|تعفن القدم البني
Crown rot|تعفن التاج
Foot rot|تعفن القدم
Collar rot|تعفن القاعدة
Damping off of cabbage|뻔ثرة الملفوف
Top rot|تعفن القمة
Watery soft rot|تعفن الرطوبي المائي
Blossom rot|تعفن الأزهار
# --- mildews ---
Powdery mildew|البياض الدقيقي
Downy mildew|البياض الخيطي
Leaf mold|العفن الورقي
False downy mildew|البياض الخيطي الكاذب
# --- leaf spots ---
Septoria leaf spot|البقع الورقيةسبتورية
Alternaria leaf spot|البقع الورقية ألترناريا
Gray leaf spot|البقع الرمادية
Grey mold|العفن الرمادي
Target spot|البقعة الهدفية
Frogeye leaf spot|بقعة العين الضارية
Brown leaf spot|البقع البنية
Tan leaf spot|البقع الكهرمانية
Angular leaf spot (fungal)|البقع الزاوية الفطرية
Spot blotch|البقعة المنبثقة
Net blotch|البقعة الشبكية
Spot form of net blotch|شكل البقعة من الشبكية
Ramularia leaf spot|البقع الورقية رامولارية
Cercospora leaf spot|البقع الورقية سركوسبورا
Cercosporiosis|مرض السركوسبورا
Phyllosticta leaf spot|البقع الورقية فيلوستكتا
Mycosphaerella leaf spot|بقع ميكوسفيريلا
Stemphylium leaf spot|البقع الورقية ستيمفيليوم
Colletotrichum leaf spot|البقع الورقية كوليتوتريشوم
Anthracnose leaf spot|بقع الجمرة الورقية
Sigatoka leaf spot|بقع سيغاتوكا
Black spot of rose|البقع السوداء على الورد
Coffee leaf spot|بقع أوراق القهوة
Walnut blight|لفحة الجوز
# --- rusts and smuts ---
Leaf rust|الصدأ الورقي
Stem rust|الصدأ السقمي
Stripe rust|الصدأ الخطّي
Brown rust|الصدأ البني
Orange rust|الصدأ البرتقالي
Yellow rust|الصدأ الأصفر
Crown rust|الصدأ التاجي
Rust of legumes|صدأ البقوليات
Soybean rust|صدأ الصويا
Coffee leaf rust|صدأ أوراق القهة
Cotton leaf rust|صدأ أوراق القطن
Melon rust|صدأ الشمّوي
Bean rust|صدأ الفاصولياء
Wheat stem rust|صدأ ساق القمح
Downy rust|الصدأ الخيطي
White rust|الصدأ الأبيض
Loose smut|التفحم السائب
Covered smut|التفحم المغطى
Head smut|تفحم الأغاريم
Kernel smut|تفحم الحبوب
Bunt (stinking smut)|التفحم النتن
Corn smut|تفحم الذرة
Sugarcane smut|قصدير القصب
# --- wilts and vascular ---
Fusarium wilt|الذبول الفوزاري
Verticillium wilt|الذبول الفرتيليّ
Fusarium wilt of tomato|الذبول الفوزاري الطماطم
Fusarium wilt of cotton|الذбол الفوزاري القطن
Fusarium wilt of banana|الذبول الفوزاري الموز
Verticillium wilt of olive|الذبول الفرتيليّ الزيتون
Fusarium crown rot|تعفن التاج الفوزاري
Vascular wilt|الذبول الأوعية
Sudden oak death|الموت المفاجئ للبلّوط
Phytophthora root rot|تعفن الجذور فايتوسبتورا
Phytophthora blight|لفحة فايتوسبتورا
Phytophthora crown rot|تعفن التاج فايتوسبتورا
Little leaf disease|مرض الأوراق الصغيرة
# --- rots of fruit and tissue ---
Anthracnose|الجمرة
Bitter rot|التعفن المر
Ripe rot|التعفن عند النضج
Stem-end rot|تعفن نهاية الساق
Button rot|تعفن الزر
Alternaria rot|تعفن ألترناريا
Aspergillus rot|تعفن أسبيرغيلوس
Penicillium rot|تعفن بينيسيليوم
Green mold|العفن الأخضر
Blue mold|العفن الأزرق
Blue rot|التعفن الأزرق
Alternaria black rot|التعفن الأسود ألترناريا
Neck rot|تعفن الرقبة
# --- cankers, scabs, sooty ---
Canker (fungal)|قرح فطرية
Botryosphaeria canker|قرحة بوتريوسفيريا
Nectria canker|قرحة نكتريا
Eutypella canker|قرحة يوتيبيلا
Cytospora canker|قرحة سيتوسبورا
Peach canker|قرحة الخوخ
Citrus scab|الحكّة الحمضيات
Apple scab|الحكّة التفاحية
Pear scab|الحكّة الكمثرية
Peach scab|الحكّة الخوخية
Olive scab|الحكّة الزيتونية
Scab (general)|الحكّة
Sooty mould|السواد الدخاني
Sooty blotch|البقعة الدخانية
Sooty line disease|مرض الخط الدخاني
# --- specialty / soilborne ---
White mold|العفن الأبيض
Sclerotinia rot|تعفن سكلروتينيا
Sclerotinia drop|السقوط السكلروتيني
Southern blight|لفحة الجنوب
Southern stem blight|لفحة الجنوب الساقية
Charcoal rot|تعفن الفحم
Macrophomina rot|تعفن ماكروفومينا
Armillaria root rot|تعفن الجذور العسحلية
Honey fungus|فطر العسل
Knot (root knot)|العقد الجذرية
Clubroot|الجذور المضمرة
Take-all|الذبول الشامل
Common root rot|تعفن الجذور الشائع
Dry rot|التعفن الجاف
Wet rot|التعفن المبلل
Pink root|الجذر الوردي
Black root rot|تعفن الجذور الأسود
Root and stem rot|تعفن الجذور والسيقان
Late leaf spot|بقعة الورقة المتأخرة
Cocoa black pod|القرن الأسود للكاكاو
Ashy stem blight|لفحة الساق الرمادية
Red rot|التعفن الأحمر
Red rot of sugarcane|تعفن القصب الأحمر
Wart disease|مرض الشرخ
Potato wart|شرخ البطاطا
Powdery scab|الحكّة الدقيقة
Silver scurf|القشرة الفضية
Black scurf|القشرة السوداء
Rhizoctonia scurf|القشرة ريزوكتونيا
Cyst (fungal)|الأكياس الفطرية
Leaf curl (fungal)|التواء الورقي فطر
Peach leaf curl|التواء أوراق الخوخ
# --- rots in storage and field ---
Storage rot|تعفن التخزين
Field rot|تعفن الحقل
Transport rot|تعفن النقل
Fruit rot|تعفن الثمار
Vegetable rot|تعفن الخضروات
Tuber rot|تعفن الدرنات
Tuber decay|فساد الدرنات
Rhizopus rot|تعفن رايزوبس
Mucor rot|تعفن موكور
Geotrichum rot|تعفن جيوتريشوم
# --- mycotoxin and systemic ---
Fusarium ear rot (maize)|تعفن الأذن فوزاري الذرة
Aspergillus ear rot|تعفن الأذن أسبيرغيلوس
Vomitoxin contamination|تلوث الأوكتيكسين
Yellow leaf stripe|الخط الأصفر الورقي
# --- ornamentals, turf, trees ---
Dollar spot|بقعة الدولار
Brown patch|البقعة البنية للملاعب
Pythium blight|لفحة بيثيوم
Pythium turf blight|لفحة الملاعب بيثيوم
Zoysia patch|بقعة الزويزيا
Melting out|الذوبان
Leaf blight of turf|لفحة أوراق الملاعب
Needle cast|تساقط الإبر
Dothistroma needle cast|تساقط إبر دوذيستروما
Rhabdocline needle cast|تساقط إبر ربادوكلاين
Larch blister|فواصل اللارش
Compressed blister|الفقاعة المضغوطة
Pine blister|فقار الصنوبر
Cedar-apple rust|صدأ التفاح الأرزّي
Juniper rust|صدأ العرعر
Holly leaf spot|بقعة أوراق الصنوبر الحولي
Dogwood anthracnose|جمرة داودوود
Sycamore anthracnose|جمرة الطرفة
Maple anthracnose|جمرة القيقب
Birch leaf spot|بقعة أوراق البتولا
Cherry leaf spot|بقعة أوراق الكرز
Apricot leaf spot|بقعة أوراق المشمش
Almond leaf scorch|حرق أوراق اللوز
Grape leaf scorch|حرق أوراق العنب
# --- end of fungal ---
"""


_BACTERIAL_RAW = """
# --- blights and vascular ---
Bacterial blight|اللفحة البكتيرية
Bacterial brown spot|البقع البنية البكتيرية
Bacterial leaf blight|لفحة الأوراق البكتيرية
Bacterial leaf streak|الخطوط البكتيرية الورقية
Bacterial stripes|الخطوط البكتيرية
Bacterial fruit blotch|بقع الثمار البكتيرية
Bacterial spot|البقع البكتيرية
Bacterial spot of tomato|بقع الطماطم البكتيرية
Bacterial spot of pepper|بقع الفلفل البكتيرية
Bacterial ring spot|بقعة الحلقة البكتيرية
Bacterial wilt|الذبول البكتيرية
Bacterial wilt of potato|ذبول البطاطا البكتيرية
Bacterial wilt of tomato|ذبول الطماطم البكتيرية
Bacterial wilt of banana|ذبول الموز البكتيرية
Ralstonia wilt|ذبول رالتونيا
Burkholderia wilt|ذبول بوركولدريا
# --- cankers ---
Crown gall|الغرّة التاجية
Canker (bacterial)|قرحة بكتيرية
Bacterial canker|القرحة البكتيرية
Bacterial canker of tomato|قرحة الطماطم البكتيرية
Bacterial canker of citrus|قرحة الحمضيات البكتيرية
Bacterial canker of kiwifruit|قرحة الكيوي البكتيرية
Varnish spot|بقعة اللك
Blast|الانفجار
Bacterial blast|الانفجار البكتيري
Gummosis (bacterial)|العفصية البكتيرية
# --- soft rots ---
Bacterial soft rot|التعفن الرخوي البكتيري
Pectobacterium rot|تعفن بكتوباكتيريوم
Dickeya rot|تعفن ديكييا
Black rot (bacterial)|التعفن الأسود البكتيري
# --- fire blight and pome ---
Fire blight|اللفحة النارية
Blossom blight|لفحة الأزهار
Shoot blight|لفحة الأفرع
Crown blight|لفحة التاج
# --- leaf spots ---
Angular leaf spot|البقع الزاوية
Bacterial angular leaf spot|البقع الزاوية البكتيرية
Bacterial brown leaf spot|بقعة الورقة البنية البكتيرية
Bacterial speck|البقع الدقيقة البكتيرية
Greasy spot|البقعة الزيتونية
Xanthomonas leaf spot|بقع زانثوموناس
Pseudomonas leaf spot|بقع بسيودوموناس
Bacterial blotch|البقع البكتيرية المنبثقة
# --- citrus and fruit ---
Citrus canker|قرحة الحمضيات
Citrus black spot|البقعة السوداء للحمضيات
Citrus greasy spot|البقعة الزيتونية للحمضيات
Huanglongbing (citrus greening)|الطول الأخضر للحمضيات
Candidatus Liberibacter|ليبيريباكتر
Bacterial fruit drop|سقوط الثمار البكتيري
# --- grain and legume ---
Bacterial grain rot|تعفن الحبوب البكتيري
Bacterial brown stripe|الخط البنّي البكتيري
Bacterial stalk rot|تعفن السيقان البكتيري
Bacterial foot rot|تعفن القدم البكتيري
Bacterial root rot|تعفن الجذور البكتيري
Bacterial blight of beans|لفحة الفاصولياء البكتيرية
Bacterial blight of cotton|لفحة القطن البكتيرية
Bacterial blight of urdbean|لفحة الماش البكتيرية
Bacterial pustule|الدثر البكتيري
# --- soft tissue ---
Soft rot of ornamentals|تعفن الرخوي للزهور
Bacterial wilt of corn|ذبول الذرة البكتيرية
Stewart's wilt|ذبول ستيوارت
Leaf streak of banana|خط الأوراق للموز
Moko disease|مرض موكو
# --- others ---
Bacterial wetwood|الخشب الرطب البكتيري
Bacterial leaf scorch|حرق الأوراق البكتيري
Oleander leaf scorch|حرق أوراق الأليريا
Mulberry leaf scorch|حرق أوراق التوت
Xylella fastidiosa|كسيللا فاستيديوسا
Pierce's disease|مرض بيرس
Grapevine leaf scorch|حرق أوراق العنب البكتيري
Almond leaf scorch (bacterial)|حرق أوراق اللوز البكتيري
Coffee bacterial spot|بقعة القهوة البكتيرية
Bacterial brown rot|التعفن البني البكتيري
Bacterial hemorrhage|النزف البكتيري
Seed rot (bacterial)|تعفن البذور البكتيري
Seedling rot (bacterial)|تعفن البادرات البكتيري
# --- end of bacterial ---
"""


_VIRAL_RAW = """
# --- mosaic and mottle ---
Tobacco mosaic virus|فيروس تبغ الفسيفساء
Cucumber mosaic virus|فيروس خيار الفسيفساء
Tomato mosaic virus|فيروس طماطم الفسيفساء
Potato virus Y|فيروس البطاطا واي
Potato virus X|فيروس البطاطا إكس
Potato leafroll virus|فيروس التواء أوراق البطاطا
Zucchini yellow mosaic|فسيفساء الكوسة الصفراء
Watermelon mosaic virus|فيروس بطيخ الفسيفساء
Papaya mosaic virus|فيروس بابايا الفسيفساء
Peanut mottle|الفسيفساء المبقّعة
Bean common mosaic|فسيفساء الفاصولياء الشائعة
Soybean mosaic virus|فيروس صويا الفسيفساء
Cassava mosaic virus|فيروس المانيهوت الفسيفساء
Cucurbit mosaic|فسيفساء اليقطين
Pepper mottle virus|فيروس فلفل الفسيفساء
Legume mosaic|فسيفساء البقوليات
Cereal mosaic|فسيفساء الحبوب
Wheat streak mosaic|فسيفساء خطوط القمح
Barley yellow dwarf|القزم الأصفر للشعير
Maize dwarf mosaic|القزم الفسيفسائي للذرة
Sugarcane mosaic|فسيفساء القصب
Strawberry mottle|الفسيفساء المبقّعة للفراولة
# --- yellowing and curl ---
Tomato yellow leaf curl virus|فيروس التواء الأصفر لطماطم
Pepper yellow leaf curl|التواء الأصفر للفلفل
Squash leaf curl|تواء اليقطين
Cucurbit yellow stunt|التقزم الأصفر لليقطين
Bean golden mosaic|الفسيفساء الذهبية للفاصولياء
Cassava brown streak|الخطوط البنية للمانيهوت
Citrus tristeza|الحزن للحمضيات
Citrus yellow vein|الوريد الأصفر للحمضيات
Chlorosis (viral)|الاصفرار الفيروسي
# --- ringspots and rings ---
Tobacco ringspot virus|فيروس بقع تبغ الدائرية
Tomato spotted wilt virus|فيروس البقع المطفي لطماطم
Impatiens necrotic spot|البقعة النخرية لإمبيشنز
Peanut stripe|خط الفول السوداني
Ringspot virus|فيروس البقع الدائرية
Necrotic ringspot|البقعة الدائرية النخرية
Prunus necrotic ringspot|البقع الدائرية النخرية للحجاج
Rose ring spot|بقع الورد الدائرية
# --- transmitted by vectors ---
Yellow leaf curl|التواء الأوراق الأصفر
Leaf curl virus|فيروس التواء الأوراق
Leaf roll|التواء الأوراق
Leaf roll virus|فيروس تجعيد الأوراق
Curly top|القمة الملتوية
Beet curly top|القمة الملتوية للشمندر
Big vein|الوريد الكبير
Cucumber green mottle mosaic|الفسيفساء الخضراء للخيار
# --- severe systemic ---
Plum pox virus|فيروس حجر المشمش
Sharka|شرقة
Apple mosaic virus|فيروس فسيفساء التفاح
Apple chlorotic leaf spot|بقعة الورقة الكالوروتية للتفاح
Cherry necrotic rusty mottle|الفسيفساء الصدئية النخرية للكرز
Grapevine fanleaf|مروحة العنب
Grapevine vein necrosis|نخر أوراق العنب
Grapevine red blotch|البقعة الحمراء للعنب
Grapevine leafroll|تواء العنب
Blueberry mosaic|فسيفساء التوت الأزرق
Raspberry leaf mottle|الفسيفساء المبقّعة للتوت الشوكي
Strawberry latent ringspot|البقع الدائرية الكامنة للفراولة
Peach rosette|وردة الخوخ
Peach stunt|تقزم الخوخ
Prune dwarf|تقزم البرقوق
# --- viroids ---
Citrus exocortis viroid|فيرويد قشرة الحمضيات
Peach latent mosaic viroid|فيرويد الفسيفساء الكامنة للخوخ
Hop stunt viroid|فيرويد تقزم القنب
Potato spindle tuber viroid|فيرويد الدرنة المغزلية للبطاطا
Chrysanthemum stunt viroid|فيرويد تقزم الدايزي
Cadang-cadang|كادانغ كادانغ
Coconut cadang cadang|الموت اللين لجوز الهند
# --- cucurbit and specialty ---
Watermelon necrotic spot|بقعة النخر للبطيخ
Cucurbit aphid-borne yellows|الاصفرار الناقل لليقطين
Squash mosaic|فسيفساء الكوسة
Melon chlorosis|اصفرار الشمّوي
Cucumber mosaic|فسيفساء الخيار
# --- ornamentals ---
Tulip breaking virus|فيروس كسر التوليب
Tulip mosaic virus|فيروس توليب الفسيفساء
Lily symptomless virus|فيروس الليلك بلا أعراض
Orchid mosaic virus|فسيفساء الأوركيد
Chrysanthemum virus B|فيروس الدايزي بي
Carnation mottle virus|فيروس قرنفل الفسيفساء
Carnation ring spot|بقعة قرنفل الدائرية
Geranium flower break|كسر زهور الجيرانيوم
Raspberry bushy dwarf|التقزم الكثيف للتوت الشوكي
# --- cereal and grass ---
Rice stripe virus|فيروس خطوط الأرز
Rice tungro virus|فيروس تونغرو الأرز
Rice grassy stunt|التقزم الحشري للأرز
Rice ragged stunt|التقزم الخشن للأرز
Maize chlorotic mottle|الفسيفساء الخضراء للذرة
Maize lethal necrosis|النخر المميت للذرة
Sorghum mosaic|فسيفساء الدرع
Oat sterile dwarf|التقزم العقيم للشوفان
# --- end of viral ---
"""

_NEMATODE_RAW = """
# --- root-knot ---
Root-knot|العقد الجذرية
Root-knot nematode|نيماتود العقد الجذرية
Meloidogyne incognita|ميلويدوجن إنكوغنيتا
Meloidogyne javanica|ميلويدوجن جافانيكا
Meloidogyne arenaria|ميلويدوجن أريناريا
Meloidogyne hapla|ميلويدوجن هابلا
Meloidogyne chitwoodi|ميلويدوجن تشيتودي
Meloidogyne enterolobii|ميلويدوجن إنترولوبي
Northern root-knot|العقد الجذرية الشمالية
Southern root-knot|العقد الجذرية الجنوبية
# --- cyst nematodes ---
Cyst nematode|نيماتود الأكياس
Potato cyst nematode|أكياس بطاطا
Globodera rostochiensis|غلوبوديرا رستوشينسيس
Globodera pallida|غلوبوديرا باليدا
Heterodera glycines|هيتيروديرا غلايسينز
Soybean cyst nematode|أكياس صويا
Sugar beet cyst nematode|أكياس الشمندر
Cereal cyst nematode|أكياس الحبوب
Heterodera avenae|هيتيروديرا أفيني
# --- lesion and migratory ---
Lesion nematode|نيماتود القرح
Pratylenchus|براتيلينش
Pratylenchus penetrans|براتيلينش بينيرانس
Pratylenchus brachyurus|براتيلينش براكيوروس
Pratylenchus coffeae|براتيلينش كوفييه
Root lesion|آفات الجذور
# --- burrowing, sting, spiral ---
Burrowing nematode|نيماتود الحفر
Radopholus|رادوفولوس
Radopholus similis|رادوفولوس سيميليس
Burrowing root rot|تعفن الحفر الجذري
Sting nematode|نيماتود اللسعة
Belonolaimus|بيلونولايموس
Spiral nematode|نيماتود الحلزون
Rotylenchulus|روتيلينكولوس
Rotylenchulus reniformis|روتيلينكولوس كلووي الشكل
Ring nematode|نيماتود الحلقة
Criconemoides|كريكونيمويدس
# --- lance, needle, dagger ---
Lance nematode|نيماتود الرمح
Hoplolaimus|هوبولايموس
Hoplolaimus columbus|هوبولايموس كولومبوس
Needle nematode|نيماتود الإبرة
Dagger nematode|نيماتود الخنجر
Xiphinema|زيفينيمة
Trichodorus|تريخودوروس
Stubby-root nematode|نيماتود الجذور القصيرة
# --- sting and root-knot relatives ---
Citrus nematode|نيماتود الحمضيات
Tylenchulus|تيلنكيولوس
Tylenchulus semipenetrans|تيلنكيولوس نصف النافذ
Slow decline|التراجع البطيء
# --- seed and bulb ---
Seed-gall nematode|نيماتود غلال البذور
Ditylenchus|ديتيلنخوس
Ditylenchus dipsaci|ديتيلنخوس ديبساسي
Stem and bulb nematode|نيماتود السيقان والبصيلات
Bulb rot nematode|تعفن البصيلات
Onion rot (nematode)|تعفن البصل بالنيماتود
# --- associated symptoms ---
Nematode root galling|تقرح الجذور النماتي
Nematode stunting|التقزم النماتي
Patchy wilt|الذبول المبقّع
Slow dieback|الذبول البطيء
Degenerate root system|تدهور نظام الجذور
Reproductive stunting|التقزم الإنجابي
# --- end of nematode ---
"""

_PHYSIO_RAW = """
# --- mobile nutrient deficiency ---
Nitrogen deficiency|نقص النيتروجين
Phosphorus deficiency|نقص الفوسفور
Potassium deficiency|نقص البوتاسيوم
Potassium deficiency of potato|نقص البوتاسيوم في البطاطا
Mobile nutrient deficiency|نقص العناصر المتحركة
Uniform chlorosis|الاصفرار الموحد
# --- immobile nutrient deficiency ---
Calcium deficiency|نقص الكالسيوم
Magnesium deficiency|نقص المغنيسيوم
Iron deficiency|نقص الحديد
Manganese deficiency|نقص المنغنيه
Zinc deficiency|نقص الزنك
Boron deficiency|نقص البورون
Copper deficiency|نقص النحاس
Molybdenum deficiency|نقص الموليبدنوم
Sulfur deficiency|نقص الكبريت
Nickel deficiency|نقص النيكل
Chlorine deficiency|نقص الكلور
# --- visual deficiency signatures ---
Interveinal chlorosis|الاصفرار ماوريدية
Marginal scorch|الحرق الهامشي
Tip burn|حرق القمة
Blossom-end rot|تعفن نهاية الأزهار
Rosetting|الورديّة
Little leaf|الأوراق الصغيرة
Hooking|الخطّاف
Dieback of tips|ذبول القمم
Green veins|الأوريد الخضراء
Purple leaves|الأوراق البنفسجية
Reddening|الاحمرار
# --- toxicity and excess ---
Nutrient toxicity|سمية العناصر
Nitrogen excess|فائض النيتروجين
Salt toxicity|سمية الملح
Salt burn|حرق الملح
Boron toxicity|سمية البورون
Copper toxicity|سمية النحاس
Herbicide injury|أذى المبيدات
Herbicide drift|انجراف المبيدات
Phytotoxicity|السُّمية النباتية
Chemical burn|الحرق الكيميائي
Fertilizer burn|حرق الأسمدة
# --- water and weather ---
Drought stress|إجهاد الجفاف
Waterlogging|تغمر المياه
Flooding injury|أذى الغمر
Root asphyxia|اختناق الجذور
Frost damage|أذى الصقيع
Freeze injury|أذى التجمد
Windburn|حرق الرياح
Wind damage|أذى الرياح
Sunscald|الحرق الشمسي
Heat stress|إجهاد الحرارة
Heat scorch|حرق الحرارة
Lightning injury|أذى البرق
Hail damage|أذى البَرَد
Physical leaf scorch|حرق الأوراق الميكانيكي
# --- surface and tissue disorders ---
Leaf scorch|حرق الأوراق
Scorch (abiotic)|الحرق غير الحيوي
Oedema|الوذمة
Edema|الوذمة
Intumescence|التورم
Corky spot|البقع الفلينية
Bitter pit|الحفرة المرّة
Internal browning|التحمّر الداخلي
Brown heart|القلب البني
Blackheart|القلب الأسود
Hollow heart|القلب الجوفاء
Soft heart|القلب الرخو
Tip burn of lettuce|حرق قمة الخس
Black tip|القمة السوداء
Dry tip|القمة الجافة
Russeting|التورّق الفليني
Netting|التورّق الشبكي
# --- fruit and vegetable disorders ---
Fruit splitting|تشقق الثمار
Fruit cracking|التشقق
Cat-facing|وجه القطة
Malformation|التشوه
Nose rot|تعفن الأنف
Buttoning|الزرّ
Blotchy ripening|النضج المبقّع
Gold fleck|البقع الذهبية
Vein clearing|صفاء الأوريد
White tissue|النسيج الأبيض
Lenticel spotting|بقع الثغور
Pepper blossom drop|سقوط أزهار الفلفل
Tomato blossom drop|سقوط أزهار الطماطم
Fruit abortion|إجهاض الثمار
Poor fruit set|سوء تثبيت الثمار
Cloudy spot|البقعة الغائمة
# --- soil and root environment ---
Poor drainage|سوء التصريف
Compaction|التكّدس
Acidity damage|أذى الحموضة
Alkalinity damage|أذى القلوية
Lime-induced chlorosis|الاصفرار الجيري
Girdling root|الجذر الخانق
Air pruning|تقليم الهواء
Transplant shock|صدمة الزراعة
# --- mechanical and handling ---
Mechanical damage|الضرر الميكانيكي
Tractor damage|ضرر المحراث
Harvest damage|ضرر الحصاد
Storage injury|أذى التخزين
Chilling injury|أذى التبريد
Chilling damage|أذى البرودة
Decompression injury|أذى التفريغ
Bruising|الكدمة
# --- end of physiological ---
"""

_INSECT_RAW = """
# --- galls and deformations ---
Leaf gall|العقد الورقية
Gall midge|بعوض العقد
Bud gall|برعم العقد
Cecidomyiid gall|عقدة السيسيدوميليد
Wasp gall|عقدة الدبر
Mite gall|عقدة العنكبوت
Bacterial gall|العقد البكتيرية
Crown gall (abiotic)|غرّة التاج
# --- miners ---
Leaf miner|حافر الأوراق
Serpentine leaf miner|حافر الأوراق المتعرّج
Blotch miner|حافر البقعة
Tentiform miner|حافر خيمة الأوراق
Needle miner|حافر الإبر
Seed miner|حافر البذور
Fruit miner|حافر الثمار
Citrus leaf miner|حافر أوراق الحمضيات
# --- borers ---
Stem borer|حافر السيقان
Root borer|حافر الجذور
Cob borer|حافر الكوز
Fruit borer|ثاقب الثمار
Corn borer|حافر الذرة
Sugarcane borer|حافر القصب
Pink borer|الحافر الوردي
Shoot borer|حافر الأفرع
# --- sucking insects ---
Aphid|المنية
Aphids (colony)|المنّيات
Blackfly|الذبابة السوداء
Whitefly|الذبابة البيضاء
Silverleaf whitefly|الذبابة البيضاء الفضية
Leafhopper|قافز الأوراق
Planthopper|قافز النبات
Psyllid|البسويل
Scale insect|الحشرة القشرية
Armored scale|القشرة المدرعة
Soft scale|القشرة الرخوة
Mealybug|الدودة القطنية
Plant bug|حشرة النبات
Tarnished plant bug|حشرة النبات الباهتة
Lygus bug|حشرة ليغوس
Stink bug|حشرة الفاصولياء الرائحة
Thrips|الدودة المجهرية
# --- mites ---
Spider mite|عنكبوت العنكبيات
Two-spotted spider mite|عنكبوت العنكبيات المنقط
Mite stippling|نقط عنكبوت العنكبيات
Broad mite|العنكبوت العريض
Rust mite|عنكبوت الصدأ
Gall mite|عنكبوت العقد
Eriophyid mite|عنكبوت الإريوفيد
Red mite|العنكبوت الأحمر
# --- chewing insects ---
Caterpillar|اليسرة
Armyworm|الدودة الجندية
Cutworm|الدودة القاطعة
Bollworm|دودة الحشيشة
Corn earworm|حافر أذن الذرة
Tomato hornworm|دودة قرن الطماطم
Potato beetle|خنفساء البطاطа
Flea beetle|خنفساء البراغيث
Leaf beetle|خنفساء الأوراق
Weevil|السوسة
Root weevil|سوسة الجذور
Boll weevil|سوسة الحشيشة
Grain weevil|سوسة الحبوب
Leaf-feeding beetle|خنفساء الأكل الورقي
Japanese beetle|خنفساء اليابان
Colorado potato beetle|خолосة كولورادو
# --- webbing and nests ---
Webworm|الدودة المنسوجة
Fall webworm|الدودة المنسوجة الخريفية
Tent caterpillar|الدودة المنسوجة للخيمة
Spider webbing|نسيج العنكبوت
Bagworm|دودة الكيس
# --- surface feeding signs ---
Stippling|النقط
Chewing damage|ضرر المضغ
Chewed margins|الهوامش الممضوغة
Skeletonized leaves|الأوراق الهيكلية
Windowpane damage|ضرر الزجاج
Frass|فضلات الحشرات
Frass in tunnels|فضلات في الأنفاق
Tunnels under epidermis|أنفاق تحت البشرة
Exit holes|ثقوب الخروج
Suckling marks|علامات المص
Honeydew|الندوة
Sooty mould from insects|السواد الدخاني من الحشرات
Leaf curling (insect)|التواء الأوراق حشري
# --- vectors and minor pests ---
Virus vector|ناقل الفيروسات
Leaf roller|ملوّي الأوراق
Fruit fly|ذبابة الثمار
Nasal fly|ذبابة الأنف
Gall wasp|دبر العقد
Sawfly|منقحة
Jumping plant louse|وزغة النبات
Termite|نمل أبيض
# --- end of insect ---
"""

# --- normalisation --------------------------------------------------------
#: Tashkeel and tatweel: a farmer's Arabic carries them, a disease name in a
#: textbook may not, and the two must still compare equal.
_TASHKEEL = re.compile(r"[ً-ٰٟـ]")

#: Arabic letter forms that differ only by spelling convention. Folding them
#: is what lets "الألف" written with hamza match the same word without.
_AR_FOLDS = (
    ("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ٱ", "ا"),
    ("ى", "ي"), ("ئ", "ي"), ("ؤ", "و"), ("ة", "ه"),
)


def normalize(value: object) -> str:
    """Fold a name to its comparison key.

    Case, punctuation, Arabic tashkeel and the alef/ya/ta-marbuta variants all
    collapse here, so the lookup key for an English name and its Arabic twin
    come out the same shape. Returns ``""`` for anything empty.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = _TASHKEEL.sub("", text)
    for src, dst in _AR_FOLDS:
        text = text.replace(src, dst)
    text = text.lower()
    # Keep letters and digits from both scripts; drop everything else.
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text)
    return " ".join(text.split())


class PathologyEntry(object):
    """One catalogued condition: its two names and the domain it belongs to."""

    __slots__ = ("name_en", "name_ar", "domain")

    def __init__(self, name_en: str, name_ar: str, domain: str) -> None:
        self.name_en = name_en
        self.name_ar = name_ar
        self.domain = domain

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PathologyEntry {self.name_en!r} {self.domain!r}>"


def _parse() -> tuple[list[PathologyEntry], dict[str, PathologyEntry]]:
    """Turn the raw tables into entries plus a dual-script lookup index."""
    raw_blocks = (
        (_FUNGAL_RAW, DOMAIN_FUNGAL),
        (_BACTERIAL_RAW, DOMAIN_BACTERIAL),
        (_VIRAL_RAW, DOMAIN_VIRAL),
        (_NEMATODE_RAW, DOMAIN_NEMATODE),
        (_PHYSIO_RAW, DOMAIN_PHYSIOLOGICAL),
        (_INSECT_RAW, DOMAIN_INSECT),
    )
    entries: list[PathologyEntry] = []
    index: dict[str, PathologyEntry] = {}
    for body, domain in raw_blocks:
        for line in body.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.count("|") != 1:
                continue
            name_en, name_ar = (part.strip() for part in stripped.split("|"))
            if not name_en or not name_ar:
                continue
            entry = PathologyEntry(name_en, name_ar, domain)
            entries.append(entry)
            # Both scripts index the same entry, and the FIRST spelling wins
            # so a duplicate (two English synonyms) never clobbers a domain.
            for key in {normalize(name_en), normalize(name_ar)}:
                if key and key not in index:
                    index[key] = entry
    return entries, index


ENTRIES, _INDEX = _parse()

#: Conditions the reference index actually holds. Asserted by the test suite
#: so the "500+" claim can never quietly rot into "whatever is left".
INDEX_SIZE = len(ENTRIES)

#: Entries whose English name, used as a comparison key, collides with another
#: (true synonyms such as Oedema/Edema). Tracked rather than de-duplicated
#: because both spellings are worth resolving.

#: Heads of qualified names — "Fusarium wilt of tomato" is filed under
#: "Fusarium wilt". Only the text before a connector counts as a head, so a
#: bare genus can never resolve to an arbitrary one of its diseases: that
#: would silently report a novel condition as catalogued.
_CONNECTORS = (" of ", " in ", " on ", " from ")


def _build_heads(entries: list[PathologyEntry]) -> dict[str, PathologyEntry]:
    heads: dict[str, PathologyEntry] = {}
    for entry in entries:
        key = normalize(entry.name_en)
        for sep in _CONNECTORS:
            if sep in key:
                head = key.split(sep, 1)[0].strip()
                if len(head) >= 8:
                    heads.setdefault(head, entry)
                break
    return heads


_HEADS = _build_heads(ENTRIES)


def resolve(value: object) -> PathologyEntry | None:
    """Look a name up in either script, or return ``None``.

    Tries the exact key first, then the head of a qualified name, then the
    longest matching prefix — in that order, because a prefix match is only
    ever a guess and an exact one never is.
    """
    key = normalize(value)
    if not key:
        return None
    hit = _INDEX.get(key)
    if hit is not None:
        return hit
    hit = _HEADS.get(key)
    if hit is not None:
        return hit
    # A qualifier may trail the head ("early blight of potato"), so try each
    # prefix from longest to shortest and stop at the first real match.
    words = key.split()
    for n in range(len(words) - 1, 0, -1):
        prefix = " ".join(words[:n])
        if len(prefix) < 8:
            break
        hit = _INDEX.get(prefix) or _HEADS.get(prefix)
        if hit is not None:
            return hit
    return None


def is_known(value: object) -> bool:
    """True when this index already catalogues the condition."""
    return resolve(value) is not None


def domain_for(value: object) -> str | None:
    """The domain a catalogued condition belongs to, else ``None``."""
    hit = resolve(value)
    return hit.domain if hit is not None else None


def counts() -> dict[str, int]:
    """How many conditions each domain holds, in prompt order."""
    tally = {domain: 0 for domain in DOMAINS}
    for entry in ENTRIES:
        tally[entry.domain] = tally.get(entry.domain, 0) + 1
    return tally


def digest() -> str:
    """A one-line scope summary — counts only, never names.

    Counts are safe to say anywhere; the names are not, which is the whole
    reason this module exists instead of a prompt appendix.
    """
    tally = counts()
    parts = ", ".join(f"{domain} {tally[domain]}" for domain in DOMAINS)
    return f"{INDEX_SIZE} conditions across {len(DOMAINS)} domains: {parts}"


__all__ = [
    "DOMAINS",
    "DOMAIN_FUNGAL",
    "DOMAIN_BACTERIAL",
    "DOMAIN_VIRAL",
    "DOMAIN_NEMATODE",
    "DOMAIN_PHYSIOLOGICAL",
    "DOMAIN_INSECT",
    "PathologyEntry",
    "ENTRIES",
    "INDEX_SIZE",
    "normalize",
    "resolve",
    "is_known",
    "domain_for",
    "counts",
    "digest",
]
