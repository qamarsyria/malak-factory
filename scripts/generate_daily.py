import gzip
import json
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUTPUT_DIR = _REPO_ROOT / "data" / "daily" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_episode.txt"
STATE_FILE = _REPO_ROOT / "state" / "daily_state.json"
LIBRARIES_DIR = _REPO_ROOT / "data" / "libraries"

SALAF_SYSTEM_PROMPT = """أنت داعية ومحرر محتوى إسلامي متمكّن على منهج أهل السنة والجماعة والطريقة السلفية الصافية (الكتاب والسنة بفهم سلف الأمة، وأقوال العلماء الراسخين كابن كثير والسعدي وابن باز وابن عثيمين والألباني والفوزان).

قواعد إلزامية لا يجوز مخالفتها أبداً:
1. الأمانة العلمية التامة: التزم التزاماً صارماً بالحكم الشرعي والدليل والمعنى الموجود في المصادر المرفقة؛ لا تغيّر حكماً ولا تخترع حديثاً أو قصة أو سبب نزول غير وارد فيها.
2. النصوص الشرعية (متن الآية، الحديث النبوي، الدعاء أو الذكر المأثور): تُنقل كما هي بالضبط دون تحريف.
3. أسلوب الشرح والسرد (مهم جداً):
   - يُمنع منعاً باتاً الأسلوب المدرسي الجاف والمقدمات المعلّبة ("نتوقف اليوم مع..."، "في هذا المقطع..."، "مقدمة روحانية..."، "مما لا شك فيه...").
   - اشرح بلغة عربية مبسطة حية بلمسة شامية بيضاء مهذّبة ودافئة قريبة للقلب ومفهومة لكل العرب (مثل: "تخيّل معي..."، "بس لحظة..."، "شو يعني هالكلمة؟"، "هون بيت القصيد..."، "كتير ناس بتفكر إنو... بس الصح..."، "خلي هالمعلومة ببالك..."، "مو مجرد...").
   - فكّك وشرح أي كلمة غريبة أو تراثية في النص الشرعي (مثل: تحفهم، السكينة، غشيتهم، كظم، ضلع الدين، عصمة أمري) ببساطة مدهشة تجعل المستمع يحسّ أنه يفهمها لأول مرة في حياته.
   - أعطِ عمقاً حقيقياً (الحكمة + التفصيل الدقيق الذي يخطئ فيه الناس + التطبيق العملي اليومي).
4. قواعد النطق والتصوير التقنية:
   - يُمنع منعاً باتاً استخدام الأقواس () أو [] أو ﴿﴾ أو الرموز مثل * # _ - أو أي حروف إنجليزية داخل أي نص.
   - اكتب الأرقام بالكلمات العربية (مثال: ثلاث مرات، مئة مرة) ولا تكتب أرقاماً هندية (١٢٣).
   - اكتب "صلى الله عليه وسلم" أو "رحمه الله" أو "رضي الله عنه" بدون أقواس تماماً.
   - اجعل العنوان (title) مختصراً جداً (لا يتجاوز 45 حرفاً بدون أقواس أو إيموجي).
5. أعد JSON فقط بالبنية المطلوبة دون أي كلام قبله أو بعده."""

HOOK_STYLES = [
    "ابدأ بسؤال واقعي صادم أو فضولي يمسّ حياة الناس اليومية فوراً (مثل: تخيّل لو... أو ليش كتير منا بيعمل كذا مع إنو...؟).",
    "ابدأ بتصحيح مفهوم غلط شائع عند أغلب الناس حول هذا الموضوع ثم اكشف الصواب بلطف.",
    "ابدأ بالتركيز على كلمة واحدة مدهشة داخل النص لا يعرف معناها نصّ الناس، واشعل الفضول لمعرفتها.",
    "ابدأ بمشهد إنساني يومي يعيشه كل واحد فينا (في البيت، الشغل، أو لحظة ضيق) ثم اربطه مباشرة بالحل الشرعي.",
]

# مكتبة أدعية نبوية وقرآنية صحيحة موسّعة (10 محاور شاملة تشتغل تلقائياً دون حاجة لرفع ملفات)
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
                "focus_words": "ضَلَع الدَّيْن وغَلَبَة الرجال، الفرق بين الهم والحزن",
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
                "focus_words": "صَرِّف قلوبنا",
            },
            {
                "text": "اللهم أصلح لي ديني الذي هو عصمة أمري، وأصلح لي دنياي التي فيها معاشي، وأصلح لي آخرتي التي فيها معادي.",
                "source": "رواه مسلم في صحيحه",
                "focus_words": "عِصمة أمري، فيها معادي",
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
                "focus_words": "أُغتال من تحتي",
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
                "focus_words": "قُرّة أعين، للمتقين إماماً",
            },
            {
                "text": "رب أوزعني أن أشكر نعمتك التي أنعمت علي وعلى والدي وأن أعمل صالحا ترضاه وأصلح لي في ذريتي.",
                "source": "القرآن الكريم، سورة الأحقاف",
                "focus_words": "أوزعني، أصلح لي في ذريتي",
            },
            {
                "text": "اللهم ألف بين قلوبنا، وأصلح ذات بيننا، واهدنا سبل السلام، ونجنا من الظلمات إلى النور.",
                "source": "رواه أبو داود والحاكم وصححه الألباني",
                "focus_words": "ألّف بين قلوبنا، أصلح ذات بيننا",
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
                "focus_words": "الدَّنَس، المشرق والمغرب",
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
                "text": "اللهم إني عبدك ابن عبدك ابن أمتك، ناصيتي بيدك، ماض في حكمك، عدل في قضاؤك، أسألك بكل اسم هو لك سميت به نفسك أو أنزلته في كتابك، أن تجعل القرآن ربيع قلبي، ونور صدري، وجلاء حزني، وذهاب همي.",
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
                "focus_words": "الفرق بين اهدني وسدّدني مثل السهم",
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
                "focus_words": "الحَزْن بفتح الحاء وسكون الزاي أي الأرض الصعبة",
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
                "text": "اللهم خر لي واختر لي ولا تكلني إلى اختياري.",
                "source": "معنى جامع في طلب التوفيق والخيرة",
                "focus_words": "خِرْ لي واختر لي",
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
                "focus_words": "أكرم نُزُله، نقه كما ينقى الثوب",
            },
            {
                "text": "اللهم اغفر لحينا وميتنا وشاهدنا وغائبنا وصغيرنا وكبيرنا وذكرنا وأنثانا.",
                "source": "رواه أبو داود والترمذي وصححه الألباني",
                "focus_words": "شمول الدعاء لكل الأمة",
            },
            {
                "text": "ربنا اغفر لنا ولإخواننا الذين سبقونا بالإيمان ولا تجعل في قلوبنا غلا للذين آمنوا.",
                "source": "القرآن الكريم، سورة الحشر",
                "focus_words": "غِلاً أي الحقد والبغض",
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


def make_tts_safe(text: str) -> str:
    if not text:
        return ""
    text = text.translate(_DIGIT_MAP)
    text = text.replace("ﷺ", " صلى الله عليه وسلم ")
    text = re.sub(r"[()\[\]﴿﴾«»\"'`]", " ", text)
    text = re.sub(r"[*#@_•/\\|~=+]", " ", text)
    text = re.sub(r"[a-zA-Z]", " ", text)
    text = re.sub(r"\s*([،؛؟!.:])\s*", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
    return f"{rep} مرة"


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


def pick_unused(items: list, used_ids: list[str], predicate=None) -> tuple[dict | None, list[str]]:
    pool = [i for i in items if (predicate(i) if predicate else True)]
    unused = [i for i in pool if i.get("id") not in used_ids]
    if not unused:
        print("♻️ تم استهلاك جميع العناصر المتاحة في هذا القسم، جاري إعادة التدوير...")
        unused = pool
        used_ids = []
    if not unused:
        return None, used_ids
    picked = random.choice(unused)
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
        return json.loads(raw_text[s : e + 1])
    except Exception:
        return None


def call_llm_json(user_prompt: str, max_tokens: int = 2800) -> dict | None:
    gh_token = os.getenv("GITHUB_TOKEN", "").strip()
    if gh_token:
        for model in ("openai/gpt-4.1", "openai/gpt-4o", "openai/gpt-4o-mini"):
            try:
                r = requests.post(
                    "https://models.github.ai/inference/chat/completions",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {gh_token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "temperature": 0.42,
                        "max_tokens": max_tokens,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": SALAF_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                    timeout=60,
                )
                if r.status_code == 200:
                    content = r.json()["choices"][0]["message"]["content"]
                    parsed = _extract_json(content)
                    if parsed:
                        print(f"   🤖 تم التوليد عبر GitHub Models ({model})", flush=True)
                        return parsed
                else:
                    print(f"   ⚠️ GitHub Models ({model}): HTTP {r.status_code}", flush=True)
            except Exception as e:
                print(f"   ⚠️ خطأ GitHub Models ({model}): {e}", flush=True)

    raw_keys = os.getenv("GEMINI_API_KEYS", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
    keys = [k.strip() for k in re.split(r"[\s,;]+", raw_keys) if len(k.strip()) > 20]
    for key in keys[:5]:
        for g_model in ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{g_model}:generateContent?key={key}",
                    json={
                        "systemInstruction": {"parts": [{"text": SALAF_SYSTEM_PROMPT}]},
                        "contents": [{"parts": [{"text": user_prompt}]}],
                        "generationConfig": {
                            "temperature": 0.42,
                            "maxOutputTokens": max_tokens,
                            "responseMimeType": "application/json",
                        },
                    },
                    timeout=60,
                )
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    txt = "".join(p.get("text", "") for p in parts if not p.get("thought") and p.get("text"))
                    parsed = _extract_json(txt)
                    if parsed:
                        print(f"   🤖 تم التوليد عبر Gemini ({g_model})", flush=True)
                        return parsed
            except Exception:
                continue
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

    prompt = f"""لدينا مسألة فقهية موثقة من {scholar} من مصدر {source}:

السؤال أو الموضوع:
{question[:700]}

النص العلمي والفتوى الكاملة:
{answer[:3200]}

أسلوب البداية الإلزامي لهذه الحلقة:
{hook_style}

المطلوب: حوّل هذه المسألة إلى طرح فقهي عميق ومشوّق بلغة مبسّطة دافئة بلمسة شامية بيضاء (بعيداً تماماً عن الأسلوب المدرسي الممل) مع الالتزام التام بالحكم والدليل الموجودين في الفتوى فقط:
{{
  "title": "عنوان مشوق مختصر أقل من 45 حرفاً يبدأ بـ فقه:",
  "hook_question": "خطاف جذاب من جملتين يلامس خطأً شائعاً أو سؤالاً حقيقياً في حياة الناس، بدون أي مقدمات تقليدية.",
  "ruling_summary": "الحكم الشرعي الدقيق مع توضيح التفصيل المهم أو الحالة الاستثنائية إن وردت في كلام العالم، بلغة واضحة وحاسمة في 3 إلى 4 جمل.",
  "simple_explanation": "العمق الفقهي: ليش الحكم هيك؟ اشرح الدليل والحكمة وفكّك أي مصطلح فقهي صعب بلغة شامية بيضاء قريبة للقلب، ثم أعطِ تطبيقاً عملياً واضحاً للمستمع في 5 إلى 7 جمل.",
  "source_credit": "جملة واحدة دافئة تذكر اسم العالم والمصدر مثل: هذه الفتوى مستفادة من علم الشيخ ابن باز رحمه الله."
}}"""

    res = call_llm_json(prompt)
    if res and res.get("hook_question") and res.get("ruling_summary"):
        title = make_title_safe(res.get("title", ""), "فقه وفتوى على منهج السلف")
        segments = [
            {"type": "intro",     "voice": "Puck",       "text": make_tts_safe(res["hook_question"])},
            {"type": "fiqh",      "voice": "Orus",       "text": make_tts_safe(res["ruling_summary"])},
            {"type": "narration", "voice": "Charon",     "text": make_tts_safe(res.get("simple_explanation", ""))},
            {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(res.get("source_credit", f"مستفاد من {scholar}"))},
        ]
    else:
        title = make_title_safe(f"فقه: {item.get('title', '')}", "فقه وفتوى على منهج السلف")
        segments = [
            {"type": "intro",     "voice": "Puck",       "text": make_tts_safe(f"كتير ناس بتحتار بهالسؤال اليومي: {question[:220]}")},
            {"type": "fiqh",      "voice": "Orus",       "text": make_tts_safe(answer[:950])},
            {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(f"هذه الفائدة مستفادة من {scholar} في {source}.")},
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
    for _ in range(8):
        cand, used_ids = pick_unused(lib, used_ids)
        if not cand:
            break
        enriched = _enrich_hadith_from_henc(cand)
        if enriched and enriched.get("text") and len(enriched.get("text", "")) <= 1200:
            item = enriched
            break

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

    prompt = f"""لدينا حديث نبوي شريف موثق مع شرحه ومعاني مفرداته من موسوعة الأحاديث النبوية:

المتن النبوي الشريف:
{matn}

التخريج والدرجة: {attribution} ، درجته: {grade}
الشرح الموثوق:
{explanation[:2500] if explanation else "حديث صحيح ثابت في السنة النبوية."}

معاني الكلمات: {words_text}
أبرز الفوائد: {hints_text}

أسلوب البداية الإلزامي لهذه الحلقة:
{hook_style}

المطلوب: أعد شرح هذا الحديث بأسلوب حيّ يدهش المستمع ويبسّط المعاني الصعبة بلغة شامية بيضاء دافئة ومهذّبة، مع الحفاظ على لفظ الحديث النبوي حرفياً كما ورد:
{{
  "title": "عنوان مشوق أقل من 45 حرفاً مثل: سر كلمة في حديث نبوي:",
  "intro": "خطاف جذاب من جملتين يشد السامع فوراً (مثل ذكر كلمة غريبة في الحديث يخطئ الناس بفهمها أو سؤال واقعي من حياتنا)، بدون أي ترحيب أو تمهيد تقليدي.",
  "hadith_text": "نص الحديث النبوي الشريف كاملاً ومضبوطاً كما ورد، بدون أي أقواس.",
  "sharh_and_benefits": "هنا جوهر التبسيط والعمق: أولاً فكّك الكلمات الغريبة في الحديث (مثل: بس لحظة... شو يعني كلمة كذا؟ كتير ناس بتفكر معناها كذا، بس الحقيقة معناها...) مستنداً إلى معاني الكلمات والشرح المرفق، ثم اشرح الحكمة الدقيقة وكيف يغيّر الحديث يومنا وحياتنا في 5 إلى 7 جمل دافئة.",
  "takhrij_credit": "جملة واحدة تذكر التخريج والدرجة والمصدر مثل: رواه البخاري ومسلم وهو حديث صحيح، من موسوعة الأحاديث النبوية."
}}"""

    res = call_llm_json(prompt)
    if res and res.get("sharh_and_benefits"):
        title = make_title_safe(res.get("title", ""), "حديث شريف وشرحه")
        segments = [
            {"type": "intro",     "voice": "Puck",       "text": make_tts_safe(res.get("intro", "في كلمة صغيرة بهالحديث، لو فهمت معناها رح يتغير إحساسك كله."))},
            {"type": "hadith",    "voice": "Orus",       "text": make_tts_safe(matn)},
            {"type": "narration", "voice": "Charon",     "text": make_tts_safe(res["sharh_and_benefits"])},
            {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(res.get("takhrij_credit", f"التخريج: {attribution}، {grade}، عن موسوعة الأحاديث النبوية."))},
        ]
    else:
        title = make_title_safe(f"حديث: {item.get('title', '')}", "حديث شريف وشرحه")
        segments = [
            {"type": "intro",     "voice": "Puck",       "text": "اسمع هالحديث العظيم وركّز بمعاني كلماته الدقيقة."},
            {"type": "hadith",    "voice": "Orus",       "text": make_tts_safe(matn)},
            {"type": "narration", "voice": "Charon",     "text": make_tts_safe(explanation[:1000] if explanation else f"حديث عظيم من هدي نبينا صلى الله عليه وسلم، {hints_text}")},
            {"type": "summary",   "voice": "Rasalgethi", "text": make_tts_safe(f"{attribution}، ودرجته {grade}، من موسوعة الأحاديث النبوية.")},
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

تفسير العلامة عبد الرحمن السعدي رحمه الله:
{saadi[:1400] if saadi else "غير متوفر لهذه الآية"}

مقتطف من تفسير الحافظ ابن كثير رحمه الله:
{ibn_kathir[:1400] if ibn_kathir else "غير متوفر لهذه الآية"}

أسلوب البداية الإلزامي لهذه الحلقة:
{hook_style}

المطلوب: صُغ فقرة «آية وحكاية» بأسلوب قصصي وتدبّري حيّ يشدّ السامع (بلغة عربية مبسطة بلمسة شامية بيضاء دافئة ومهذّبة)، مع الالتزام الصارم بالتفاسير الموثقة أعلاه دون اختراع أي سبب نزول غير وارد:
{{
  "title": "عنوان مشوق أقل من 45 حرفاً مثل: سر آية في سورة {s_name}",
  "intro": "خطاف تمهيدي درامي أو سؤال من واقع حياتنا من جملتين يمهّد لقصة الآية أو حكمتها ويشوق لسماعها، ثم يذكر اسم سورة {s_name}.",
  "tafsir_story": "شرح معمّق وحيّ لمعنى الآية وحكايتها أو سبب نزولها كما ورد في التفاسير؛ توقف عند أي كلمة قرآنية غريبة معناها غامض عند الناس واشرحها ببساطة مدهشة (يعني شو هالكلمة؟)، ثم اربط لطائف السعدي وابن كثير بشعور الإنسان اليوم في 5 إلى 7 جمل.",
  "takeaway": "رسالة عملية مباشرة لقلبك من بكرة، مع ذكر مصدر التفسير في جملة واحدة."
}}"""

    res = call_llm_json(prompt)
    if res and res.get("tafsir_story"):
        title = make_title_safe(res.get("title", ""), f"آية وحكاية: سورة {s_name}")
        intro_txt = make_tts_safe(res.get("intro", f"لما تضيق الدنيا، بتلاقي الجواب بهالآية من سورة {s_name}."))
        tafsir_txt = make_tts_safe(res["tafsir_story"])
        takeaway_txt = make_tts_safe(res.get("takeaway", "هذه اللطائف مستفادة من التفسير الميسر وتفسير السعدي رحمه الله."))
    else:
        title = make_title_safe(f"آية وحكاية: سورة {s_name}", f"آية وحكاية: سورة {s_name}")
        intro_txt = f"في سورة {s_name}، اسمع هالآية العظيمة وشوف معناها العميق:"
        tafsir_txt = make_tts_safe(f"يقول أهل التفسير في معنى هذه الآية الكريمة: {muyassar[:850]}")
        takeaway_txt = "فلنتدبر كتاب الله ونعمل بهداياته في حياتنا اليومية."

    segments = [
        {"type": "intro", "voice": "Puck", "text": intro_txt},
        {
            "type": "quran",
            "surah": s_num,
            "surah_name": s_name,
            "ayah_numbers": [a_num],
            "display": f"{s_name}: {a_num}",
        },
        {"type": "narration", "voice": "Charon",     "text": tafsir_txt},
        {"type": "summary",   "voice": "Rasalgethi", "text": takeaway_txt},
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
    for _ in range(3):
        item, used_ids = pick_unused(
            lib,
            used_ids,
            predicate=lambda i: i.get("slot") == current_slot and 20 <= len(i.get("text", "")) <= 500,
        )
        if item and item not in picked_list:
            picked_list.append(item)

    if not picked_list:
        raise RuntimeError(f"❌ لم يتم العثور على أذكار لقسم {slot_label}.")

    adhkar_bundle = "\n".join(
        f"- الذكر {idx+1}: {x['text']} | التكرار: {x.get('repeat', 1)} | الفضل: {x.get('bless', '')} | المصدر: {x.get('reference', 'حصن المسلم')}"
        for idx, x in enumerate(picked_list)
    )

    prompt = f"""لدينا باقة مختارة من {slot_label} الصحيحة من حصن المسلم والسنة النبوية:

{adhkar_bundle}

أسلوب البداية الإلزامي لهذه الحلقة:
{hook_style}

المطلوب: إعداد حلقة روحانية دافئة ومبسّطة لقسم {slot_label} بلغة شامية بيضاء مهذّبة تُفهم المستمع سر كل ذكر ومعنى كلماته الصعبة (حتى لا يردده باللسان فقط بدون قلب!)، مع بقاء نص الذكر النبوي حرفياً كما ورد:
{{
  "title": "عنوان مختصر أقل من 45 حرفاً يبدأ بـ {slot_label}",
  "intro": "خطاف روحاني جذاب من جملتين يلامس الحاجة النفسية لهذا الذكر (القلق، الحماية، الرزق، السكينة) بدون مقدمات جامدة.",
  "items": [
    {{
      "lead_in": "تمهيد شائق من جملة إلى جملتين يوضح متى تقوله وعدد تكراره بالكلمات وفضله العجيب.",
      "dhikr_exact": "نص الذكر الأصلي كما هو بدون أي أقواس أو تغيير.",
      "meaning_note": "تفكيك وتبسيط معنى الكلمات الصعبة في الذكر وأثره النفسي والإيماني بلمسة شامية دافئة في جملتين إلى ثلاث جمل."
    }}
  ],
  "closing": "خاتمة عملية تذكّر بمكان هذه الأذكار في حصن المسلم وتثبّتها في يوم المستمع."
}}"""

    res = call_llm_json(prompt)
    segments = []
    if res and isinstance(res.get("items"), list) and len(res["items"]) > 0:
        title = make_title_safe(res.get("title", ""), f"{slot_label} من السنة الصحيحة")
        segments.append({"type": "intro", "voice": "Puck", "text": make_tts_safe(res.get("intro", f"أغلبنا بيقرأ {slot_label} بسرعة بس ما بينتبه لمعانيها العجيبة."))})
        for idx, gen_item in enumerate(res["items"][:3]):
            src_item = picked_list[idx] if idx < len(picked_list) else picked_list[-1]
            lead = make_tts_safe(gen_item.get("lead_in", ""))
            if lead:
                segments.append({"type": "narration", "voice": "Charon", "text": lead})
            segments.append({"type": "adhkar", "voice": "Algieba", "text": make_tts_safe(src_item["text"])})
            note = make_tts_safe(gen_item.get("meaning_note", ""))
            if note:
                segments.append({"type": "narration", "voice": "Charon", "text": note})
        segments.append({"type": "summary", "voice": "Rasalgethi", "text": make_tts_safe(res.get("closing", f"خلي {slot_label} جزء ثابت من يومك، من كتاب حصن المسلم."))})
    else:
        title = make_title_safe(f"{slot_label} المباركة", slot_label)
        segments.append({"type": "intro", "voice": "Puck", "text": f"باقة مباركة من {slot_label} الثابتة عن النبي صلى الله عليه وسلم ومعانيها المبسطة."})
        for x in picked_list:
            rep = int(x.get("repeat", 1) or 1)
            rep_txt = format_repeat_ar(rep)
            bless = x.get("bless", "")
            lead = f"يُقال هذا الذكر {rep_txt}. {bless}".strip() if rep_txt else bless
            if lead.strip():
                segments.append({"type": "narration", "voice": "Charon", "text": make_tts_safe(lead)})
            segments.append({"type": "adhkar", "voice": "Algieba", "text": make_tts_safe(x["text"])})
        segments.append({"type": "summary", "voice": "Rasalgethi", "text": f"حافظ على {slot_label} يومياً، من كتاب حصن المسلم والسنة النبوية الصحيحة."})

    segments = [s for s in segments if s.get("text")]
    return title, segments, used_ids


def build_dua_episode(used_ids: list[str], hook_style: str) -> tuple[str, list[dict], list[str]]:
    """
    فقرة أدعية نبوية وقرآنية موسّعة (4 أدعية مترابطة في محور واحد):
    - تشرح متى نلجأ لكل دعاء وتفكّك كلماته الصعبة بلغة شامية بيضاء دافئة.
    - تعرض متن كل دعاء على الدفتر الصوفي الإسلامي 8K.
    """
    topic_obj, used_ids = pick_unused(DUA_TOPICS, used_ids)
    if not topic_obj:
        topic_obj = DUA_TOPICS[0]

    topic_name = topic_obj["topic"]
    duas_list = topic_obj["duas"]

    duas_bundle = "\n".join(
        f"- الدعاء {i+1}: {d['text']} | المصدر: {d['source']} | كلمات مهمة للشرح: {d['focus_words']}"
        for i, d in enumerate(duas_list)
    )

    prompt = f"""نريد إعداد حلقة موسّعة ومؤثرة جداً لفقرة الأدعية حول محور: «{topic_name}».

الأدعية المعتمدة (4 أدعية صحيحة تُستخدم كلها بدون تحريف نص الدعاء):
{duas_bundle}

أسلوب البداية الإلزامي لهذه الحلقة:
{hook_style}

المطلوب: صياغة رحلة دعاء موسّعة تلمس القلب، تفهم المستمع أسرار كل دعاء وكلماته الصعبة بلغة شامية بيضاء دافئة ومبسّطة (حتى يدعو وهو مستحضر قلبه، لا يردد بلسانه فقط):
{{
  "title": "عنوان مختصر دافئ أقل من 45 حرفاً يعبر عن {topic_name}",
  "intro": "خطاف عاطفي واقعي من 2 إلى 3 جمل يلامس ألم الناس وحاجتهم لهذا المحور من الدعاء، ويمهّد له بدون مقدمات تقليدية.",
  "items": [
    {{
      "lead_in": "تمهيد حميمي من جملتين يهيّئ القلب ويبين متى قال النبي هذا الدعاء أو متى يُستحب.",
      "dua_exact": "نص الدعاء كما ورد بالضبط بدون أقواس.",
      "deep_meaning": "شرح معمّق ومبسط لمعاني الكلمات الدقيقة في الدعاء (ركّز على: {duas_list[0]['focus_words']} ومثيلاتها في كل دعاء) بلغة شامية بيضاء دافئة ومفهومة في 3 إلى 4 جمل."
    }}
  ],
  "etiquette_closing": "خاتمة موسّعة من 3 إلى 4 جمل تذكّر بآداب استجابة الدعاء (الثناء، الصلاة على النبي صلى الله عليه وسلم، وحضور القلب واليقين بالإجابة) مع دعوة للناس بترديدها دائماً."
}}"""

    res = call_llm_json(prompt, max_tokens=3400)
    segments = []

    if res and isinstance(res.get("items"), list) and len(res["items"]) > 0:
        title = make_title_safe(res.get("title", ""), topic_name)
        segments.append({
            "type": "intro",
            "voice": "Puck",
            "text": make_tts_safe(res.get("intro", f"لمّا تتسكّر الأبواب بوجهك، ما في باب بيفتح أسرع من الدعاء الصادق، تعال نعيش اليوم مع {topic_name}.")),
        })
        for idx, src_dua in enumerate(duas_list):
            gen_item = res["items"][idx] if idx < len(res["items"]) else {}
            lead = make_tts_safe(gen_item.get("lead_in", ""))
            if lead:
                segments.append({"type": "narration", "voice": "Charon", "text": lead})
            segments.append({"type": "adhkar", "voice": "Algieba", "text": make_tts_safe(src_dua["text"])})
            deep = make_tts_safe(gen_item.get("deep_meaning", ""))
            if deep:
                segments.append({"type": "narration", "voice": "Charon", "text": deep})

        closing = make_tts_safe(res.get("etiquette_closing", "ولحتى يستجيب ربنا دعاءك، ابدأ بالثناء على الله والصلاة على النبي صلى الله عليه وسلم، وادعُ وأنت واثق أن الله يسمعك وقريب منك."))
        segments.append({"type": "summary", "voice": "Rasalgethi", "text": closing})
    else:
        title = make_title_safe(topic_name, "أدعية نبوية وقرآنية")
        segments.append({"type": "intro", "voice": "Puck", "text": f"باقة مباركة وموسّعة من {topic_name} من القرآن الكريم والسنة الصحيحة."})
        for d in duas_list:
            segments.append({"type": "narration", "voice": "Charon", "text": make_tts_safe(f"من الأدعية العظيمة الثابتة في {d['source']}:")})
            segments.append({"type": "adhkar", "voice": "Algieba", "text": make_tts_safe(d["text"])})
        segments.append({"type": "summary", "voice": "Rasalgethi", "text": "حافظ على هذه الأدعية بقلب حاضر ويقين بالإجابة، ولا تنسَ الصلاة على النبي صلى الله عليه وسلم."})

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
        "text": "قال رسول الله صلى الله عليه وسلم: من دل على خير فله مثل أجر فاعله. شارك المقطع لتعم الفائدة. ملاك المعرفة.",
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
