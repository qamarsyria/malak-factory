import gzip
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_discovery import discover_and_cache
from gemini_tts_pool import get_pool
from utilities import get_breaker

OUTPUT_DIR = _REPO_ROOT / "data" / "daily" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_episode.txt"
STATE_FILE = _REPO_ROOT / "state" / "daily_state.json"
LIBRARIES_DIR = _REPO_ROOT / "data" / "libraries"

SALAF_SYSTEM_PROMPT = """أنت محرر ومُقدِّم محتوى إسلامي ذكي ومعاصر على منهج أهل السنة والجماعة وفهم سلف الأمة (مستنداً لأقوال العلماء الراسخين كابن كثير والسعدي وابن باز وابن عثيمين والألباني والفوزان).

قواعد إلزامية لا يجوز مخالفتها أبداً:
1. الأمانة العلمية التامة: التزم التزاماً صارماً بالحكم الشرعي والدليل والمعنى الموجود في المصادر المرفقة؛ لا تغيّر حكماً ولا تخترع حديثاً أو قصة أو سبب نزول غير وارد فيها.
2. النصوص الشرعية (متن الآية، الحديث النبوي، الدعاء أو الذكر المأثور): تُنقل كما هي بالضبط دون تحريف.
3. أسلوب الطرح واللغة (حاسم جداً):
   - يُمنع منعاً باتاً أسلوب الثمانينات الجاف وصيغ الأسئلة المعلّبة ("سأل سائل"، "سُئل الشيخ"، "سأل فلان وقال علتان"، "نتوقف اليوم مع..."، "في هذا المقطع..."، "مما لا شك فيه...").
   - اكتب بلغة عربية معاصرة **سهلة جداً وواضحة ومباشرة** يفهمها القاصي والداني ممن ليست لغته العربية قوية: جمل قصيرة، واضحة، سلسة النطق في القراءة الآلية (TTS)، بدون مفردات معجمية ثقيلة أو عامية مغرقة تشوه النطق.
   - ادخل في الموضوع فوراً بذكاء: اربط الحكم أو الحديث بموقف يومي واقعي نعيشه في البيت أو الشغل أو المال أو العلاقات، واشرح "ليش" والحكمة وراءه بمثال حيّ قريب للعقل.
   - إذا وجدت أي كلمة صعبة أو تراثية في الحديث أو الآية أو الفتوى (مثل: ضلع الدين، تحفّهم، كظم، السكينة، غشيتهم، أشططت)، ترجم معناها فوراً لكلمات يومية بسيطة ("بمعنى أوضح...").
4. قواعد النطق والتصوير التقنية:
   - اسم القناة هو «مَلَّاكُ المَعْرِفَةِ» بفتح الميم وتشديد اللام مع الفتح.
   - يُمنع منعاً باتاً استخدام الأقواس () أو [] أو ﴿﴾ أو الشرطة - أو الرموز مثل * # _ أو أي حروف إنجليزية داخل أي نص.
   - اكتب الأرقام بالكلمات العربية (مثال: ثلاث مرات، مئة مرة) ولا تكتب أرقاماً هندية أو إنجليزية.
   - اكتب "صلى الله عليه وسلم" أو "رحمه الله" أو "رضي الله عنه" بدون أقواس تماماً.
   - اجعل العنوان (title) قصيراً وذكياً وجذاباً (أقل من 45 حرفاً بدون أقواس أو إيموجي).
5. أعد JSON فقط بالبنية المطلوبة دون أي كلام قبله أو بعده."""

HOOK_STYLES = [
    "ابدأ مباشرةً بموقف يومي ذكي يقع فيه أغلبنا اليوم في البيت أو العمل أو الهاتف، ثم اطرح المفاجأة الشرعية التي تصحح التصرف.",
    "ابدأ بتصحيح فكرة مغلوطة منتشرة بين الناس بأسلوب هادئ وذكي يشعر المستمع أنه اكتشف شيئاً مهماً لأول مرة.",
    "ابدأ بالتقاط كلمة واحدة دقيقة ومدهشة في النص الشرعي يمر الناس عليها دون أن يعرفوا معناها الحقيقي، ثم اكشف معناها ببساطة.",
    "ابدأ بسؤال مباشر يمسّ حاجة نفسية أو مادية أو أسرية حقيقية يعيشها المشاهد الآن، وادخل فوراً في الحل العملي.",
]

DUA_TOPICS = [
    {
        "id": "dua-faraj-karb",
        "topic": "أدعية تفريج الهم والكرب وقضاء الدين",
        "duas": [
            {
                "text": "لا إله إلا الله العظيم الحليم، لا إله إلا الله رب العرش العظيم، لا إله إلا الله رب السماوات ورب الأرض ورب العرش الكريم.",
                "source": "متفق عليه في الصحيحين، كان النبي صلى الله عليه وسلم يقوله عند الكرب",
                "focus_words": "الحليم، رب العرش العظيم",
            },
            {
                "text": "لا إله إلا أنت سبحانك إني كنت من الظالمين.",
                "source": "دعاء ذي النون يونس عليه السلام في سورة الأنبياء، رواه الترمذي وصححه الألباني",
                "focus_words": "سبحانك إني كنت من الظالمين",
            },
            {
                "text": "اللهم إني أعوذ بك من الهم والحزن، والعجز والكسل، والبخل والجبن، وضلع الدين وغلبة الرجال.",
                "source": "رواه البخاري، وكان النبي صلى الله عليه وسلم يكثر منه",
                "focus_words": "ضلع الدين وغلبة الرجال، الفرق بين الهم والحزن",
            },
            {
                "text": "اللهم رحمتك أرجو فلا تكلني إلى نفسي طرفة عين، وأصلح لي شأني كله، لا إله إلا أنت.",
                "source": "رواه أبو داود وأحمد وحسنه الألباني",
                "focus_words": "طرفة عين، لا تكلني إلى نفسي",
            },
        ],
    },
    {
        "id": "dua-rizq-barakah",
        "topic": "جوامع أدعية الرزق الحلال والبركة وتيسير الأمور",
        "duas": [
            {
                "text": "اللهم إني أسألك علماً نافعاً، ورزقاً طيباً، وعملاً متقبلاً.",
                "source": "رواه ابن ماجه وصححه الألباني، كان يدعو به بعد صلاة الفجر",
                "focus_words": "رزقاً طيباً وعملاً متقبلاً",
            },
            {
                "text": "اللهم اكفني بحلالك عن حرامك، وأغنني بفضلك عمن سواك.",
                "source": "رواه الترمذي وحسنه الألباني",
                "focus_words": "أغنني بفضلك عمن سواك",
            },
            {
                "text": "اللهم اغفر لي ذنبي، ووسع لي في داري، وبارك لي في رزقي.",
                "source": "رواه الترمذي والنسائي وصححه الألباني",
                "focus_words": "ووسع لي في داري وبارك لي في رزقي",
            },
            {
                "text": "يا حي يا قيوم برحمتك أستغيث، أصلح لي شأني كله ولا تكلني إلى نفسي طرفة عين.",
                "source": "رواه الحاكم والنسائي وحسنه الألباني",
                "focus_words": "القيوم، برحمتك أستغيث",
            },
        ],
    },
    {
        "id": "dua-heart-steadfastness",
        "topic": "أدعية صلاح القلب والثبات والهداية وحسن الخاتمة",
        "duas": [
            {
                "text": "يا مقلب القلوب ثبت قلبي على دينك.",
                "source": "رواه الترمذي وصححه الألباني، أكثر دعاء النبي صلى الله عليه وسلم",
                "focus_words": "مقلب القلوب وتقلب القلوب",
            },
            {
                "text": "اللهم مصرف القلوب صرف قلوبنا على طاعتك.",
                "source": "رواه مسلم في صحيحه",
                "focus_words": "صرف قلوبنا",
            },
            {
                "text": "اللهم أصلح لي ديني الذي هو عصمة أمري، وأصلح لي دنياي التي فيها معاشي، وأصلح لي آخرتي التي فيها معادي.",
                "source": "رواه مسلم في صحيحه",
                "focus_words": "عصمة أمري، فيها معادي",
            },
            {
                "text": "ربنا لا تزغ قلوبنا بعد إذ هديتنا وهب لنا من لدنك رحمة إنك أنت الوهاب.",
                "source": "القرآن الكريم، سورة آل عمران",
                "focus_words": "لا تزغ قلوبنا، من لدنك",
            },
        ],
    },
    {
        "id": "dua-shifa-afiyah",
        "topic": "أدعية الشفاء والعافية والحفظ من كل شر",
        "duas": [
            {
                "text": "أذهب البأس رب الناس، واشف وأنت الشافي، لا شفاء إلا شفاؤك، شفاء لا يغادر سقماً.",
                "source": "متفق عليه في الصحيحين",
                "focus_words": "البأس، لا يغادر سقماً",
            },
            {
                "text": "اللهم عافني في بدني، اللهم عافني في سمعي، اللهم عافني في بصري، لا إله إلا أنت.",
                "source": "رواه أبو داود وأحمد وحسنه الألباني",
                "focus_words": "العافية في البدن والسمع والبصر",
            },
            {
                "text": "اللهم إني أسألك العفو والعافية في الدنيا والآخرة، اللهم إني أسألك العفو والعافية في ديني ودنياي وأهلي ومالي.",
                "source": "رواه أبو داود وابن ماجه وصححه الألباني",
                "focus_words": "الفرق بين العفو والعافية",
            },
            {
                "text": "اللهم احفظني من بين يدي ومن خلفي، وعن يميني وعن شمالي، ومن فوقي، وأعوذ بعظمتك أن أغتال من تحتي.",
                "source": "رواه أبو داود وابن ماجه وصححه الألباني",
                "focus_words": "أغتال من تحتي",
            },
        ],
    },
    {
        "id": "dua-family-parents",
        "topic": "أدعية الوالدين والأهل والذرية والمودة في البيت",
        "duas": [
            {
                "text": "رب ارحمهما كما ربياني صغيرا.",
                "source": "القرآن الكريم، سورة الإسراء",
                "focus_words": "كما ربياني صغيرا",
            },
            {
                "text": "ربنا هب لنا من أزواجنا وذرياتنا قرة أعين واجعلنا للمتقين إماما.",
                "source": "القرآن الكريم، سورة الفرقان",
                "focus_words": "قرة أعين، للمتقين إماماً",
            },
            {
                "text": "رب أوزعني أن أشكر نعمتك التي أنعمت علي وعلى والدي وأن أعمل صالحا ترضاه وأصلح لي في ذريتي إني تبت إليك وإني من المسلمين.",
                "source": "القرآن الكريم، سورة الأحقاف",
                "focus_words": "أوزعني، أصلح لي في ذريتي",
            },
            {
                "text": "اللهم ألف بين قلوبنا، وأصلح ذات بيننا، واهدنا سبل السلام، ونجنا من الظلمات إلى النور.",
                "source": "رواه أبو داود والحاكم وصححه الألباني",
                "focus_words": "ألف بين قلوبنا، أصلح ذات بيننا",
            },
        ],
    },
    {
        "id": "dua-istighfar-tawbah",
        "topic": "سيد الاستغفار وأدعية التوبة ومحو الذنوب",
        "duas": [
            {
                "text": "اللهم أنت ربي لا إله إلا أنت، خلقتني وأنا عبدك، وأنا على عهدك ووعدك ما استطعت، أعوذ بك من شر ما صنعت، أبوء لك بنعمتك علي، وأبوء بذنبي فاغفر لي فإنه لا يغفر الذنوب إلا أنت.",
                "source": "رواه البخاري في صحيحه، سيد الاستغفار",
                "focus_words": "أبوء لك بنعمتك، على عهدك ووعدك ما استطعت",
            },
            {
                "text": "اللهم اغفر لي خطيئتي وجهلي، وإسرافي في أمري، وما أنت أعلم به مني.",
                "source": "متفق عليه في الصحيحين",
                "focus_words": "إسرافي في أمري",
            },
            {
                "text": "اللهم باعد بيني وبين خطاياي كما باعدت بين المشرق والمغرب، اللهم نقني من خطاياي كما ينقى الثوب الأبيض من الدنس.",
                "source": "متفق عليه في الصحيحين",
                "focus_words": "الدنس، المشرق والمغرب",
            },
            {
                "text": "اللهم إنك عفو تحب العفو فاعف عني.",
                "source": "رواه الترمذي وابن ماجه وصححه الألباني",
                "focus_words": "معنى العفو وأنه يمحو أثر الذنب كله",
            },
        ],
    },
    {
        "id": "dua-sakinah-anxiety",
        "topic": "أدعية السكينة والطمأنينة وذهاب القلق",
        "duas": [
            {
                "text": "اللهم إني عبدك ابن عبدك ابن أمتك، ناصيتي بيدك، ماض في حكمك، عدل في قضاؤك، أسألك بكل اسم هو لك سميت به نفسك أو أنزلته في كتابك، أو علمته أحداً من خلقك، أو استأثرت به في علم الغيب عندك، أن تجعل القرآن ربيع قلبي، ونور صدري، وجلاء حزني، وذهاب همي.",
                "source": "رواه أحمد وصححه الألباني",
                "focus_words": "ناصيتي بيدك، ربيع قلبي، جلاء حزني",
            },
            {
                "text": "حسبنا الله ونعم الوكيل.",
                "source": "القرآن الكريم ورواه البخاري، قالها إبراهيم عليه السلام وأصحاب النبي صلى الله عليه وسلم",
                "focus_words": "معنى حسبنا ونعم الوكيل",
            },
            {
                "text": "اللهم إني أعوذ بك من منكرات الأخلاق والأعمال والأهواء.",
                "source": "رواه الترمذي وصححه الألباني",
                "focus_words": "منكرات الأهواء",
            },
            {
                "text": "اللهم اهدني وسددني.",
                "source": "رواه مسلم في صحيحه",
                "focus_words": "الفرق بين اهدني وسددني مثل السهم",
            },
        ],
    },
    {
        "id": "dua-jawami-goodness",
        "topic": "جوامع الدعاء النبوية التي تجمع خير الدنيا والآخرة",
        "duas": [
            {
                "text": "ربنا آتنا في الدنيا حسنة وفي الآخرة حسنة وقنا عذاب النار.",
                "source": "متفق عليه، أكثر دعاء النبي صلى الله عليه وسلم",
                "focus_words": "حسنة الدنيا وحسنة الآخرة",
            },
            {
                "text": "اللهم إني أسألك من الخير كله عاجله وآجله، ما علمت منه وما لم أعلم، وأعوذ بك من الشر كله عاجله وآجله، ما علمت منه وما لم أعلم.",
                "source": "رواه ابن ماجه وأحمد وصححه الألباني، علمه النبي لعائشة رضي الله عنها",
                "focus_words": "عاجله وآجله، ما علمت منه وما لم أعلم",
            },
            {
                "text": "اللهم إني أسألك الهدى والتقى والعفاف والغنى.",
                "source": "رواه مسلم في صحيحه",
                "focus_words": "العفاف والغنى أي غنى النفس",
            },
            {
                "text": "اللهم أعني على ذكرك وشكرك وحسن عبادتك.",
                "source": "رواه أبو داود والنسائي وصححه الألباني",
                "focus_words": "أعني على، حسن عبادتك",
            },
        ],
    },
    {
        "id": "dua-tawfiq-guidance",
        "topic": "أدعية التوفيق والسداد وتيسير الصعاب",
        "duas": [
            {
                "text": "اللهم لا سهل إلا ما جعلته سهلا، وأنت تجعل الحزن إذا شئت سهلا.",
                "source": "رواه ابن حبان وصححه الألباني",
                "focus_words": "الحزن بفتح الحاء وسكون الزاي أي الأرض الصعبة",
            },
            {
                "text": "رب اشرح لي صدري ويسر لي أمري واحلل عقدة من لساني يفقهوا قولي.",
                "source": "القرآن الكريم، سورة طه",
                "focus_words": "اشرح لي صدري، عقدة من لساني",
            },
            {
                "text": "رب أدخلني مدخل صدق وأخرجني مخرج صدق واجعل لي من لدنك سلطانا نصيرا.",
                "source": "القرآن الكريم، سورة الإسراء",
                "focus_words": "مدخل صدق ومخرج صدق",
            },
            {
                "text": "اللهم إني أستخيرك بعلمك وأستقدرك بقدرتك وأسألك من فضلك العظيم، فإنك تقدر ولا أقدر وتعلم ولا أعلم وأنت علام الغيوب.",
                "source": "رواه البخاري في صحيحه، من دعاء الاستخارة",
                "focus_words": "أستخيرك بعلمك وأستقدرك بقدرتك",
            },
        ],
    },
    {
        "id": "dua-mercy-deceased",
        "topic": "أدعية الرحمة والمغفرة للأموات ولجميع المسلمين",
        "duas": [
            {
                "text": "اللهم اغفر له وارحمه وعافه واعف عنه وأكرم نزله ووسع مدخله واغسله بالماء والثلج والبرد.",
                "source": "رواه مسلم في صحيحه، من أجمع أدعية الجنازة",
                "focus_words": "أكرم نزله، نقه كما ينقى الثوب",
            },
            {
                "text": "اللهم اغفر لحينا وميتنا وشاهدنا وغائبنا وصغيرنا وكبيرنا وذكرنا وأنثانا.",
                "source": "رواه أبو داود والترمذي وصححه الألباني",
                "focus_words": "شمول الدعاء لكل الأمة",
            },
            {
                "text": "ربنا اغفر لنا ولإخواننا الذين سبقونا بالإيمان ولا تجعل في قلوبنا غلا للذين آمنوا.",
                "source": "القرآن الكريم، سورة الحشر",
                "focus_words": "غلاً أي الحقد والبغض",
            },
            {
                "text": "اللهم من أحييته منا فأحيه على الإسلام ومن توفيته منا فتوفه على الإيمان.",
                "source": "رواه أبو داود والترمذي وصححه الألباني",
                "focus_words": "الفرق بين الإسلام والإيمان عند الختام",
            },
        ],
    },
]

_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_ARABIC_UNITS = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_ARABIC_TEENS = ["عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
                 "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
_ARABIC_TENS = ["", "عشرة", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_ARABIC_HUNDREDS = ["", "مئة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]


def _num_to_arabic(n: int) -> str:
    if n == 0:
        return "صفر"
    if n < 10:
        return _ARABIC_UNITS[n]
    if n < 20:
        return _ARABIC_TEENS[n - 10]
    if n < 100:
        t, u = divmod(n, 10)
        return _ARABIC_TENS[t] if u == 0 else f"{_ARABIC_UNITS[u]} و{_ARABIC_TENS[t]}"
    if n < 1000:
        h, r = divmod(n, 100)
        p = _ARABIC_HUNDREDS[h]
        return p if r == 0 else f"{p} و{_num_to_arabic(r)}"
    if n < 1_000_000:
        th, r = divmod(n, 1000)
        if th == 1:
            p = "ألف"
        elif th == 2:
            p = "ألفان"
        elif 3 <= th <= 10:
            p = f"{_ARABIC_UNITS[th]} آلاف"
        else:
            p = f"{_num_to_arabic(th)} ألف"
        return p if r == 0 else f"{p} و{_num_to_arabic(r)}"
    return str(n)


def _enforce_mallak(text: str) -> str:
    """يضبط اسم القناة دائماً بفتح الميم وتشديد اللام مع الفتح: مَلَّاكُ المَعْرِفَةِ."""
    text = re.sub(
        r"(?<![\u0621-\u064A])م[\u064B-\u065F]*ل[\u064B-\u065F]*ا[\u064B-\u065F]*ك([\u064B-\u065F]*)"
        r"\s+ال[\u064B-\u065F]*م[\u064B-\u065F]*ع[\u064B-\u065F]*ر[\u064B-\u065F]*ف[\u064B-\u065F]*ة([\u064B-\u065F]*)",
        lambda m: f"مَلَّاك{m.group(1) or 'ُ'} المَعْرِفَة{m.group(2) or 'ِ'}",
        text,
    )
    return text


def make_tts_safe(text: str) -> str:
    if not text:
        return ""
    text = text.translate(_DIGIT_MAP)
    text = re.sub(r"\b(\d{1,6})\b", lambda m: _num_to_arabic(int(m.group(0))), text)
    text = text.replace("ﷺ", " صلى الله عليه وسلم ")
    text = re.sub(r"[()\[\]﴿﴾«»\"'`]", " ", text)
    text = re.sub(r"[*#@_•/\\|~=+—–\-]", " ", text)
    text = re.sub(r"[a-zA-Z0-9]", " ", text)
    text = re.sub(r"\s*([،؛؟!.:])\s*", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _enforce_mallak(text)


def make_title_safe(title: str, fallback: str) -> str:
    t = make_tts_safe(title)
    t = re.sub(r"[.!؟]", "", t).strip()
    if not t:
        return fallback
    if len(t) > 46:
        words = t[:46].rsplit(" ", 1)[0].strip()
        return words if words else t[:46]
    return t


def format_repeat_ar(rep: int) -> str:
    mapping = {
        1: "",
        2: "مرتين",
        3: "ثلاث مرات",
        4: "أربع مرات",
        5: "خمس مرات",
        7: "سبع مرات",
        10: "عشر مرات",
        33: "ثلاثاً وثلاثين مرة",
        100: "مئة مرة",
    }
    if rep in mapping:
        return mapping[rep]
    return f"{_num_to_arabic(rep)} مرة"


def load_library(filename: str) -> list:
    path = LIBRARIES_DIR / filename
    if not path.exists():
        print(f"⚠️ المكتبة غير موجودة: {path}")
        return []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ فشل تحميل {filename}: {e}")
        return []


def pick_unused(
    items: list,
    used_ids: list[str],
    predicate=None,
    exclude_ids: set[str] | None = None,
    mark_used: bool = True,
) -> tuple[dict | None, list[str]]:
    pool = [i for i in items if (predicate(i) if predicate else True)]
    if exclude_ids:
        pool = [i for i in pool if i.get("id") not in exclude_ids]
    if not pool:
        return None, used_ids

    pool_ids = {i.get("id") for i in pool}
    unused = [i for i in pool if i.get("id") not in used_ids]
    if not unused:
        print("♻️ تم استهلاك عناصر هذا التصنيف، إعادة تدوير هذا التصنيف فقط...")
        used_ids = [uid for uid in used_ids if uid not in pool_ids]
        unused = pool

    picked = random.choice(unused)
    if mark_used and picked.get("id") not in used_ids:
        used_ids.append(picked["id"])
    return picked, used_ids


def _extract_json(raw_text: str) -> dict | None:
    if not raw_text:
        return None
    s = raw_text.find("{")
    e = raw_text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        return json.loads(raw_text[s:e + 1])
    except Exception:
        return None


def _thinking_config(model: str) -> dict | None:
    m = model.lower()
    if any(x in m for x in ["gemini-3", "gemini-4"]):
        return {"thinkingLevel": "low"}
    if "gemini-2.5" in m:
        return {"thinkingBudget": 512}
    return None


def call_llm_json(user_prompt: str, max_tokens: int = 3200) -> dict | None:
    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:8]

    for model in models:
        if breaker.is_open(f"model:{model}"):
            continue

        use_thinking = True
        use_temperature = True
        valid_tries = 0
        dead_skips = 0

        while valid_tries < 2:
            try:
                key = pool.get_current()
                if not key:
                    return None
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                gen_cfg: dict = {
                    "maxOutputTokens": max_tokens,
                    "responseMimeType": "application/json",
                }
                if use_temperature:
                    gen_cfg["temperature"] = 0.40
                if use_thinking:
                    tc = _thinking_config(model)
                    if tc:
                        gen_cfg["thinkingConfig"] = tc

                body = {
                    "systemInstruction": {"parts": [{"text": SALAF_SYSTEM_PROMPT}]},
                    "contents": [{"parts": [{"text": user_prompt}]}],
                    "generationConfig": gen_cfg,
                }
                r = requests.post(url, json=body, timeout=(15, 65))

                if r.status_code == 200:
                    resp = r.json()
                    cand = (resp.get("candidates") or [{}])[0]
                    finish = cand.get("finishReason", "")
                    parts = cand.get("content", {}).get("parts", [])
                    txt = "".join(p.get("text", "") for p in parts if not p.get("thought") and p.get("text"))
                    parsed = _extract_json(txt)
                    if parsed:
                        if hasattr(pool, "_advance"):
                            pool._advance()
                        breaker.record_success(f"model:{model}")
                        print(f"   🤖 تم التوليد عبر Gemini ({model})", flush=True)
                        return parsed
                    print(f"   ⚠️ {model}: رد JSON غير مكتمل (finish={finish})", flush=True)
                    break

                elif r.status_code == 401 or (r.status_code == 403 and "api key" in r.text.lower()):
                    pool.mark_dead(f"HTTP {r.status_code}")
                    dead_skips += 1
                    if dead_skips >= 20:
                        break
                    continue

                elif r.status_code == 400:
                    low_err = r.text.lower()
                    if use_thinking and "thinking" in low_err:
                        use_thinking = False
                        continue
                    if use_temperature and ("temperature" in low_err or "sampling" in low_err):
                        use_temperature = False
                        continue
                    print(f"   ⚠️ {model}: HTTP 400 ({r.text[:140]})", flush=True)
                    breaker.record_failure(f"model:{model}")
                    break

                elif r.status_code == 429:
                    print(f"   ⏸️ {model}: 429 تدوير المفتاح", flush=True)
                    pool.mark_failed(75)
                    valid_tries += 1
                    time.sleep(2)
                    continue

                elif r.status_code in (403, 404):
                    print(f"   ⚠️ {model}: HTTP {r.status_code}", flush=True)
                    breaker.record_failure(f"model:{model}")
                    break

                else:
                    print(f"   ⚠️ {model}: HTTP {r.status_code}", flush=True)
                    valid_tries += 1
                    if hasattr(pool, "_advance"):
                        pool._advance()
                    time.sleep(2)
                    continue

            except requests.exceptions.Timeout:
                print(f"   ⏱️ {model}: Timeout", flush=True)
                if hasattr(pool, "_advance"):
                    pool._advance()
                break
            except Exception as e:
                print(f"   ⚠️ {model}: {str(e)[:90]}", flush=True)
                break

    return None


def build_fiqh_episode(used_ids: list[str], hook_style: str) -> tuple[str, list[dict], list[str]]:
    lib = load_library("fiqh-library.json.gz")
    if not lib:
        raise RuntimeError("❌ مكتبة الفقه فارغة.")

    prefer_uthaymeen = (len(used_ids) % 3 == 2)
    subset_pred = (lambda x: x.get("id", "").startswith("salaf-")) if prefer_uthaymeen else (lambda x: x.get("id", "").startswith("binbaz-"))
    item, used_ids = pick_unused(lib, used_ids, predicate=subset_pred)
    if not item:
        item, used_ids = pick_unused(lib, used_ids)

    question = item.get("question", "")
    answer = item.get("answer", "")
    scholar = item.get("scholar", "أهل العلم")
    source = item.get("source", "المصادر الشرعية الموثوقة")

    prompt = f"""لدينا مسألة فقهية موثقة، نريد تحويلها إلى فيديو ذكي وعصري يصلح للنشر في 2026 بلغة عربية بسيطة جداً ومفهومة لكل الناس:

المسألة الخام (للاستناد العلمي فقط - لا تنسخ صيغة السؤال والجواب أبداً!):
الموضوع: {question[:700]}
الشرح الموثوق من {scholar}: {answer[:3200]}

أسلوب الافتتاحية المطلوب:
{hook_style}

قواعد صارمة لصياغة الفقه:
- ممنوع تماماً صيغة "سأل رجل / سُئل الشيخ / ما حكم كذا / الجواب: نعم يجوز / قال فلان وقال علّان".
- حوّل الفتوى إلى **فكرة ذكية ومباشرة**:
  1) في `hook_question`: ادخل فوراً في موقف واقعي يعيشه الناس اليوم أو خطأ شائع يقعون فيه (جملتان قصيرتان تشدّان الانتباه).
  2) في `ruling_summary`: أعطِ الحكم الشرعي الصافي والواضح مباشرةً بلغة عصرية سهلة جداً، مع ذكر الشرط أو الاستثناء المهم في 3 جمل قصيرة وواضحة.
  3) في `simple_explanation`: اشرح "السبب والحكمة" + قدّم **مثالاً واقعياً من حياتنا اليومية** (مثل العمل، البيت، الراتب، الأهل) + بسّط أي مصطلح فقهي صعب فوراً بعبارة واضحة في 4 إلى 6 جمل قصيرة وسهلة الفهم لمن لغته العربية ضعيفة.
  4) في `source_credit`: جملة مختصرة أنيقة للختام تذكر المرجع باختصار (مثل: المرجع فتوى الشيخ ابن باز رحمه الله).

أعد JSON فقط:
{{
  "title": "عنوان ذكي قصير أقل من 45 حرفاً بدون أقواس",
  "hook_question": "...",
  "ruling_summary": "...",
  "simple_explanation": "...",
  "source_credit": "..."
}}"""

    res = call_llm_json(prompt)
    if not res or not res.get("hook_question") or not res.get("ruling_summary"):
        raise RuntimeError(f"❌ فشل LLM في توليد صياغة الفقه الذكية ({item.get('id')}); توقف لمنع نشر نص خام.")

    title = make_title_safe(res.get("title", ""), "فائدة فقهية مبسطة")
    segments = [
        {"type": "intro",     "voice": "Puck",       "text": make_tts_safe(res["hook_question"])},
        {"type": "fiqh",      "voice": "Orus",       "text": make_tts_safe(res["ruling_summary"])},
        {"type": "narration", "voice": "Charon",     "text": make_tts_safe(res.get("simple_explanation", ""))},
        {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(res.get("source_credit", f"المرجع: {scholar} في {source}."))},
    ]
    segments = [s for s in segments if s.get("text")]
    return title, segments, used_ids


def _enrich_hadith_from_henc(item: dict) -> dict | None:
    if not item.get("henc_id") or item.get("explanation"):
        grade = (item.get("grade") or "").strip()
        if grade and not any(g in grade for g in ("صحيح", "حسن", "متفق عليه")):
            return None
        return item
    try:
        r = requests.get(
            "https://hadeethenc.com/api/v1/hadeeths/one/",
            params={"language": "ar", "id": item["henc_id"]},
            timeout=20,
        )
        if r.status_code == 200:
            d = r.json()
            grade = (d.get("grade") or "").strip()
            if grade and not any(g in grade for g in ("صحيح", "حسن", "متفق عليه")):
                return None
            item["text"] = (d.get("hadeeth") or item.get("text", "")).strip()
            item["attribution"] = (d.get("attribution") or "").strip()
            item["grade"] = grade or "صحيح"
            item["explanation"] = (d.get("explanation") or "").strip()
            item["hints"] = d.get("hints") or []
            item["words_meanings"] = d.get("words_meanings") or []
    except Exception as e:
        print(f"   ⚠️ تعذر جلب تفاصيل الحديث {item.get('henc_id')}: {e}")
    return item


def build_hadith_episode(used_ids: list[str], hook_style: str) -> tuple[str, list[dict], list[str]]:
    lib = load_library("hadith-library.json.gz")
    if not lib:
        raise RuntimeError("❌ مكتبة الحديث فارغة.")

    item = None
    tried_bad: set[str] = set()
    for _ in range(12):
        cand, used_ids = pick_unused(
            lib,
            used_ids,
            predicate=lambda i: 15 <= len(i.get("text", "")) <= 900,
            exclude_ids=tried_bad,
            mark_used=False,
        )
        if not cand:
            break
        enriched = _enrich_hadith_from_henc(cand)
        if enriched and enriched.get("text") and len(enriched.get("text", "")) <= 950:
            item = enriched
            if item.get("id") and item["id"] not in used_ids:
                used_ids.append(item["id"])
            break
        if cand.get("id"):
            tried_bad.add(cand["id"])

    if not item:
        raise RuntimeError("❌ لم يتم العثور على حديث صحيح مناسب.")

    matn = item.get("text", "")
    attribution = item.get("attribution", "")
    grade = item.get("grade", "صحيح")
    explanation = item.get("explanation", "")
    hints_raw = item.get("hints") or []
    hints_text = " | ".join(str(h) for h in hints_raw[:6] if h)

    words_raw = item.get("words_meanings") or []
    words_list = []
    for w in words_raw[:8]:
        if isinstance(w, dict):
            words_list.append(f"{w.get('word', '')}: {w.get('meaning', '')}")
        elif isinstance(w, str):
            words_list.append(w)
    words_text = " | ".join(words_list)

    prompt = f"""لدينا حديث نبوي شريف موثق نريد تقديمه بأسلوب عصري ذكي ومبسّط يفهمه كل إنسان بسهولة تامة:

المتن النبوي الشريف (يُذكر كما هو):
{matn}

التخريج والدرجة: {attribution} ، درجته: {grade}
الشرح الموثوق: {explanation[:2500] if explanation else "حديث صحيح ثابت في السنة النبوية."}
معاني الكلمات: {words_text}
أبرز الفوائد: {hints_text}

أسلوب الافتتاحية المطلوب:
{hook_style}

المطلوب:
{{
  "title": "عنوان مشوق أقل من 45 حرفاً بدون أقواس",
  "intro": "مدخل ذكي من جملتين يربط الحديث بمشكلة حقيقية في حياتنا أو يكشف كلمة مهمة فيه، بدون مقدمات تقليدية.",
  "hadith_text": "نص الحديث النبوي الشريف كاملاً كما ورد بدون أقواس.",
  "sharh_and_benefits": "شرح واضح جداً بلغة عربية بسيطة وسلسة: ترجم أي كلمة غريبة فوراً لمعنى يومي بسيط، ثم اشرح الفكرة العملية التي تغير يوم المستمع مع مثال واقعي قصير في 4 إلى 6 جمل.",
  "takhrij_credit": "جملة ختامية مختصرة: رواه {attribution} وهو حديث {grade}."
}}"""

    res = call_llm_json(prompt)
    if not res or not res.get("sharh_and_benefits"):
        raise RuntimeError(f"❌ فشل LLM في توليد شرح الحديث ({item.get('id')}); توقف لمنع نشر نص خام.")

    title = make_title_safe(res.get("title", ""), "هدي نبوي مبسط")
    segments = [
        {"type": "intro",     "voice": "Puck",       "text": make_tts_safe(res.get("intro", "كلمة واحدة في هذا الحديث تغير نظرتك لكثير من مواقف حياتك."))},
        {"type": "hadith",    "voice": "Orus",       "text": make_tts_safe(matn)},
        {"type": "narration", "voice": "Charon",     "text": make_tts_safe(res["sharh_and_benefits"])},
        {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(res.get("takhrij_credit", f"التخريج: {attribution}، {grade}."))},
    ]
    segments = [s for s in segments if s.get("text")]
    return title, segments, used_ids


def _fetch_extra_tafsir(edition: str, surah: int, ayah: int) -> str:
    url = f"https://cdn.jsdelivr.net/gh/spa5k/tafsir_api@main/tafsir/{edition}/{surah}/{ayah}.json"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            txt = (r.json().get("text") or "").strip()
            return re.sub(r"<[^>]+>", " ", txt)
    except Exception:
        pass
    return ""


def build_quran_episode(used_ids: list[str], hook_style: str) -> tuple[str, list[dict], list[str]]:
    lib = load_library("ayah-library.json.gz")
    if not lib:
        raise RuntimeError("❌ مكتبة الآيات فارغة.")

    def _good_ayah(i: dict) -> bool:
        t = (i.get("text") or "").strip()
        if not (45 <= len(t) <= 320 and len(i.get("tafsir", "")) >= 80):
            return False
        plain = re.sub(r"[\u064B-\u065F\u0670]", "", t)
        if re.match(r"^(?:قالوا|فلما|ثم قال|وإذ قال|أولئك|فإن لم|فإذا)", plain):
            return False
        return True

    item, used_ids = pick_unused(lib, used_ids, predicate=_good_ayah)
    if not item:
        item, used_ids = pick_unused(
            lib,
            used_ids,
            predicate=lambda i: 45 <= len(i.get("text", "")) <= 340 and len(i.get("tafsir", "")) >= 80,
        )
    if not item:
        raise RuntimeError("❌ لم يتم العثور على آية صالحة.")

    s_num = int(item["surah_number"])
    a_num = int(item["ayah_number"])
    s_name = item["surah_name"].replace("سورة ", "").replace("سُورَةُ ", "").strip()
    muyassar = item["tafsir"]

    saadi = _fetch_extra_tafsir("ar-tafseer-al-saddi", s_num, a_num)
    ibn_kathir = _fetch_extra_tafsir("ar-tafsir-ibn-kathir", s_num, a_num)

    prompt = f"""لدينا آية كريمة لفقرة «آية وحكاية» مع تفاسيرها المعتمدة:

السورة: سورة {s_name} ، الآية رقم {a_num}
نص الآية الكريمة:
{item["text"]}

التفسير الميسر:
{muyassar[:1000]}

تفسير العلامة السعدي رحمه الله:
{saadi[:1400] if saadi else "غير متوفر"}

مقتطف من تفسير ابن كثير رحمه الله:
{ibn_kathir[:1400] if ibn_kathir else "غير متوفر"}

أسلوب الافتتاحية المطلوب:
{hook_style}

المطلوب: صغ الفقرة بلغة عربية سهلة وواضحة ومؤثرة (يفهمها الجميع بسهولة تامة) مع الالتزام الصارم بالتفاسير الموثقة أعلاه دون اختراع أي سبب نزول:
{{
  "title": "عنوان مشوق أقل من 45 حرفاً بدون أقواس",
  "intro": "مدخل قصصي أو حياتي ذكي من جملتين يمهّد لموقف الآية ويذكر اسم سورة {s_name}.",
  "tafsir_story": "شرح مبسط وحكواتي لمعنى الآية وقصتها كما في التفاسير؛ بسّط كل كلمة قرآنية قد تبدو غامضة بلغة سهلة واضحة، ثم اربط الحكمة بواقع الإنسان اليوم في 5 إلى 6 جمل قصيرة.",
  "takeaway": "خلاصة عملية ليومك من جملة أو جملتين مع ذكر مصدر التفسير باختصار."
}}"""

    res = call_llm_json(prompt)
    if not res or not res.get("tafsir_story"):
        raise RuntimeError(f"❌ فشل LLM في توليد تفسير الآية {s_num}:{a_num}.")

    title = make_title_safe(res.get("title", ""), f"آية وحكاية: سورة {s_name}")
    segments = [
        {"type": "intro", "voice": "Puck", "text": make_tts_safe(res.get("intro", f"آية عظيمة من سورة {s_name} تحمل لك رسالة عملية واضحة."))},
        {
            "type": "quran",
            "surah": s_num,
            "surah_name": s_name,
            "ayah_numbers": [a_num],
            "display": f"{s_name}: {a_num}",
        },
        {"type": "narration", "voice": "Charon",     "text": make_tts_safe(res["tafsir_story"])},
        {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(res.get("takeaway", "مستفاد من التفسير الميسر وتفسير السعدي رحمه الله."))},
    ]
    return title, segments, used_ids


def build_adhkar_episode(used_ids: list[str], state: dict, hook_style: str) -> tuple[str, list[dict], list[str]]:
    lib = load_library("adhkar-library.json.gz")
    if not lib:
        raise RuntimeError("❌ مكتبة الأذكار فارغة.")

    slots = ["sabah", "masaa", "sleep"]
    slot_names = {
        "sabah": "أذكار الصباح",
        "masaa": "أذكار المساء",
        "sleep": "أذكار قبل النوم",
    }
    slot_idx = int(state.get("adhkar_slot_idx", 0)) % 3
    current_slot = slots[slot_idx]
    state["adhkar_slot_idx"] = slot_idx + 1
    slot_label = slot_names[current_slot]

    picked_list = []
    picked_ids: set[str] = set()
    for _ in range(3):
        item, used_ids = pick_unused(
            lib,
            used_ids,
            predicate=lambda i: i.get("slot") == current_slot and 20 <= len(i.get("text", "")) <= 450,
            exclude_ids=picked_ids,
        )
        if item and item.get("id") not in picked_ids:
            picked_list.append(item)
            picked_ids.add(item["id"])

    if not picked_list:
        raise RuntimeError(f"❌ لم يتم العثور على أذكار لقسم {slot_label}.")

    adhkar_bundle = "\n".join(
        f"- الذكر {idx+1}: {x['text']} | التكرار: {x.get('repeat', 1)} | الفضل: {x.get('bless', '')} | المصدر: {x.get('reference', 'حصن المسلم')}"
        for idx, x in enumerate(picked_list)
    )

    prompt = f"""لدينا باقة مختارة من {slot_label} الصحيحة من حصن المسلم (عددها {len(picked_list)}):

{adhkar_bundle}

أسلوب الافتتاحية المطلوب:
{hook_style}

المطلوب: إعداد حلقة روحانية واضحة ومبسّطة لقسم {slot_label} بلغة سهلة جداً تفهّم المستمع سر كل ذكر ومعنى كلماته الصعبة (ليستحضر قلبه عند الذكر لا لسانه فقط)، على أن يكون عدد عناصر `items` مساوياً تماماً لعدد الأذكار ({len(picked_list)}):
{{
  "title": "عنوان مختصر أقل من 45 حرفاً يبدأ بـ {slot_label}",
  "intro": "مدخل قريب للقلب من جملتين يلامس الحاجة النفسية لهذه الأذكار بدون مقدمات جامدة.",
  "items": [
    {{
      "lead_in": "تمهيد قصير من جملة إلى جملتين يذكر وقت الذكر وعدد تكراره بالكلمات وفضله.",
      "dhikr_exact": "نص الذكر الأصلي كما هو بدون أقواس.",
      "meaning_note": "تبسيط معنى الكلمات الصعبة في الذكر وأثره النفسي بلغة سهلة جداً في جملتين."
    }}
  ],
  "closing": "خاتمة عملية تذكّر بمكان هذه الأذكار في حصن المسلم."
}}"""

    res = call_llm_json(prompt)
    if not res or not isinstance(res.get("items"), list) or not res["items"]:
        raise RuntimeError(f"❌ فشل LLM في توليد شرح الأذكار ({slot_label}).")

    title = make_title_safe(res.get("title", ""), f"{slot_label} المباركة")
    segments = [
        {"type": "intro", "voice": "Puck", "text": make_tts_safe(res.get("intro", f"كثيرون يرددون {slot_label} بسرعة دون التوقف عند معانيها المدهشة."))}
    ]
    for idx, src_item in enumerate(picked_list):
        gen_item = res["items"][idx] if idx < len(res["items"]) else {}
        rep = int(src_item.get("repeat", 1) or 1)
        rep_txt = format_repeat_ar(rep)
        fallback_lead = f"يُقال هذا الذكر {rep_txt}." if rep_txt else ""
        lead = make_tts_safe(gen_item.get("lead_in") or fallback_lead)
        if lead:
            segments.append({"type": "narration", "voice": "Charon", "text": lead})
        segments.append({"type": "adhkar", "voice": "Algieba", "text": make_tts_safe(src_item["text"])})
        note = make_tts_safe(gen_item.get("meaning_note", ""))
        if note:
            segments.append({"type": "narration", "voice": "Charon", "text": note})
    segments.append({"type": "summary", "voice": "Rasalgethi", "text": make_tts_safe(res.get("closing", f"داوم على {slot_label} يومياً، من كتاب حصن المسلم."))})

    segments = [s for s in segments if s.get("text")]
    return title, segments, used_ids


def build_dua_episode(used_ids: list[str], hook_style: str) -> tuple[str, list[dict], list[str]]:
    topic_obj, used_ids = pick_unused(DUA_TOPICS, used_ids)
    if not topic_obj:
        topic_obj = DUA_TOPICS[0]

    topic_name = topic_obj["topic"]
    duas_list = topic_obj["duas"]

    duas_bundle = "\n".join(
        f"- الدعاء {i+1}: {d['text']} | المصدر: {d['source']} | كلمات مهمة للشرح: {d['focus_words']}"
        for i, d in enumerate(duas_list)
    )

    prompt = f"""نريد إعداد حلقة دعاء عميقة ومبسّطة حول محور: «{topic_name}».

الأدعية المعتمدة ({len(duas_list)} أدعية صحيحة تُستخدم كلها بالترتيب بدون تحريف):
{duas_bundle}

أسلوب الافتتاحية المطلوب:
{hook_style}

المطلوب: صياغة رحلة دعاء تلمس القلب وتفهم المستمع معنى الكلمات الدقيقة في كل دعاء بلغة عربية بسيطة جداً ومفهومة للجميع:
{{
  "title": "عنوان مختصر دافئ أقل من 45 حرفاً يعبر عن {topic_name}",
  "intro": "مدخل إنساني واقعي من جملتين يلامس حاجة الناس لهذا الدعاء بدون مقدمات تقليدية.",
  "items": [
    {{
      "lead_in": "تمهيد قريب للقلب من جملة أو جملتين يوضح متى يُستحب هذا الدعاء.",
      "dua_exact": "نص الدعاء كما ورد بالضبط بدون أقواس.",
      "deep_meaning": "شرح بسيط وواضح جداً لمعاني الكلمات الدقيقة في الدعاء بلغة سهلة في جملتين إلى ثلاث جمل."
    }}
  ],
  "etiquette_closing": "خاتمة من جملتين إلى ثلاث تذكر بآداب استجابة الدعاء وحضور القلب."
}}"""

    res = call_llm_json(prompt, max_tokens=3600)
    if not res or not isinstance(res.get("items"), list) or not res["items"]:
        raise RuntimeError(f"❌ فشل LLM في توليد فقرة الدعاء ({topic_obj['id']}).")

    title = make_title_safe(res.get("title", ""), topic_name)
    segments = [
        {
            "type": "intro",
            "voice": "Puck",
            "text": make_tts_safe(res.get("intro", f"عندما تضيق بك السبل، تذكر أن باب الدعاء لا يغلق أبداً، وهذه جوامع {topic_name}.")),
        }
    ]
    for idx, src_dua in enumerate(duas_list):
        gen_item = res["items"][idx] if idx < len(res["items"]) else {}
        lead = make_tts_safe(gen_item.get("lead_in", ""))
        if lead:
            segments.append({"type": "narration", "voice": "Charon", "text": lead})
        segments.append({"type": "dua", "voice": "Algieba", "text": make_tts_safe(src_dua["text"])})
        deep = make_tts_safe(gen_item.get("deep_meaning", ""))
        if deep:
            segments.append({"type": "narration", "voice": "Charon", "text": deep})

    closing = make_tts_safe(res.get("etiquette_closing", "ابدأ دعاءك بالثناء على الله والصلاة على النبي صلى الله عليه وسلم، وادع وأنت موقن بالإجابة."))
    segments.append({"type": "summary", "voice": "Rasalgethi", "text": closing})

    segments = [s for s in segments if s.get("text")]
    return title, segments, used_ids


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    all_types = ("fiqh", "hadith", "quran", "adhkar", "dua")
    state = {
        "run_count": 0,
        "adhkar_slot_idx": 0,
        "used_ids": {k: [] for k in all_types},
    }
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            state.update(saved)
            for k in all_types:
                state.setdefault("used_ids", {}).setdefault(k, [])
        except Exception:
            pass

    type_names = list(all_types)
    forced = os.getenv("FORCE_CONTENT_TYPE", "auto").strip().lower()
    if forced in type_names:
        current_type = forced
    else:
        current_type = type_names[state["run_count"] % len(type_names)]

    used_list = state["used_ids"].get(current_type, [])
    hook_style = HOOK_STYLES[state["run_count"] % len(HOOK_STYLES)]
    state["run_count"] += 1
    print(f"🔄 التشغيل #{state['run_count']} | النوع: {current_type} | المستخدم سابقاً: {len(used_list)}", flush=True)

    if current_type == "fiqh":
        title, segments, used_list = build_fiqh_episode(used_list, hook_style)
    elif current_type == "hadith":
        title, segments, used_list = build_hadith_episode(used_list, hook_style)
    elif current_type == "quran":
        title, segments, used_list = build_quran_episode(used_list, hook_style)
    elif current_type == "adhkar":
        title, segments, used_list = build_adhkar_episode(used_list, state, hook_style)
    else:
        title, segments, used_list = build_dua_episode(used_list, hook_style)

    segments.append({
        "type": "cta",
        "voice": "Puck",
        "text": "قال رسول الله صلى الله عليه وسلم: من دل على خير فله مثل أجر فاعله. شارك المقطع لتعم الفائدة. مَلَّاكُ المَعْرِفَةِ.",
    })

    ep_data = {
        "episode_number": state["run_count"],
        "story_id": f"daily_{current_type}",
        "title": title,
        "segments": segments,
    }

    state["used_ids"][current_type] = used_list
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"daily-{current_type}-{timestamp}.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")

    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    print(f"✅ تم توليد الحلقة بنجاح: {output_path} | العنوان: {title}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ فشل generate_daily.py: {e}", flush=True)
        sys.exit(1)
