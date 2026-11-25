"""
filters_loader.py — محمّل فلاتر الفيديو والمحتوى (متوافق مع اليوميات والقصص ذات المستويين)
- الوضع الافتراضي (allow_ordinary_humans=False): صارم تماماً كما في اليوميات (باستثناء المنقبات).
- وضع القصص (allow_ordinary_humans=True): يسمح بالبشر العاديين تاريخياً مع استمرار الحظر المطلق
  لأي تجسيد للذات الإلهية أو الأنبياء أو الصحابة أو الملائكة أو المبشرين بالجنة أو النساء المسلمات.
"""

import json
import re
from pathlib import Path
from functools import lru_cache

_REPO_ROOT = Path(__file__).resolve().parent.parent
FILTERS_DIR = _REPO_ROOT / "data" / "filters"

# كاشف السياقات التي يُمنع فيها أي تجسيد بشري أو ملائكي منعاً باتاً
_SACRED_CONTEXT_RE = re.compile(
    r"(?:^|[^\u0621-\u064A])(?:و|ف|ب|ل|ك)?"
    r"(?:"
    r"الذات الإلهية|ذات الله|وجه الله|يد الله|عرش الله|عرش الرحمن|العرش|الكرسي|"
    r"اللوح المحفوظ|القلم الأعلى|بدء الخلق|خلق الله السماوات|تجلى الله|"
    r"جبريل|جبرائيل|ميكائيل|إسرافيل|اسرافيل|ملك الموت|ملاك|ملائكة|الملائكة|حملة العرش|الكرام الكاتبون|"
    r"نبي|نبيا|أنبياء|انبياء|الأنبياء|رسول الله|الرسول|المرسلين|ﷺ|صلى الله عليه وسلم|عليه السلام|عليهم السلام|"
    r"آدم|إدريس|نوح|هود|صالح|إبراهيم|ابراهيم|لوط|إسماعيل|اسماعيل|إسحاق|اسحاق|يعقوب|"
    r"يوسف|أيوب|ايوب|شعيب|موسى|هارون|داود|سليمان|إلياس|الياس|اليسع|ذو الكفل|يونس|"
    r"زكريا|يحيى|عيسى|محمد|"
    r"صحابي|صحابة|الصحابة|أبو بكر|ابو بكر|الصديق|عمر بن الخطاب|الفاروق|عثمان بن عفان|"
    r"علي بن أبي طالب|طلحة|الزبير|سعد بن أبي وقاص|سعيد بن زيد|عبد الرحمن بن عوف|أبو عبيدة|"
    r"المبشرون بالجنة|المبشرين بالجنة|آل البيت|أهل البيت|"
    r"أمهات المؤمنين|أم المؤمنين|مريم|آسية|خديجة|عائشة|فاطمة|حفصة|زينب|أم سلمة|هاجر|سارة|"
    r"امرأة مسلمة|النساء المسلمات|المؤمنة|المؤمنات|زوجة النبي"
    r")"
    r"(?=$|[^\u0621-\u064A])"
)


def is_sacred_context(text: str) -> bool:
    if not text:
        return False
    norm = re.sub(r"[\u0640\u064B-\u065F\u0670]", "", text)
    return bool(_SACRED_CONTEXT_RE.search(norm))


@lru_cache(maxsize=1)
def _load_all() -> dict:
    files = {
        "allowed": FILTERS_DIR / "allowed_topics.json",
        "animals": FILTERS_DIR / "forbidden_animals.json",
        "objects": FILTERS_DIR / "forbidden_objects.json",
        "humans":  FILTERS_DIR / "forbidden_humans.json",
        "themes":  FILTERS_DIR / "forbidden_themes.json",
        "islamic": FILTERS_DIR / "forbidden_islamic.json",
    }

    data = {}
    for key, path in files.items():
        if not path.exists():
            print(f"⚠️ فلتر مفقود: {path.name}")
            data[key] = {}
            continue
        try:
            data[key] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"⚠️ فشل قراءة {path.name}: {e}")
            data[key] = {}

    forbidden_flat = set()
    for key in ("animals", "objects", "themes", "islamic"):
        forbidden_flat.update(_flatten_forbidden(data[key]))

    data["forbidden_flat"] = sorted(forbidden_flat)
    data["humans_flat"] = sorted(_flatten_forbidden(data["humans"]))

    niqab_data = data["humans"].get("exception", {}).get("niqab_woman", {})
    niqab_tags = niqab_data.get("allowed_tags") or niqab_data.get("tags") or []
    data["niqab_allowed"] = {str(t).lower().strip() for t in niqab_tags if str(t).strip()}

    print(
        f"✅ فلاتر: مسموح={_count_allowed(data['allowed'])}, "
        f"محظور={len(forbidden_flat)}, human_words={len(data['humans_flat'])}, "
        f"niqab={len(data['niqab_allowed'])}"
    )
    return data


def _flatten_forbidden(obj) -> set:
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("version", "note", "conditions", "allowed_tags", "tags", "exception"):
                continue
            out.update(_flatten_forbidden(v))
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, str) and item.strip():
                out.add(item.lower().strip())
            else:
                out.update(_flatten_forbidden(item))
    elif isinstance(obj, str) and obj.strip():
        out.add(obj.lower().strip())
    return out


def _count_allowed(allowed_obj: dict) -> int:
    total = 0
    for cat_data in allowed_obj.get("categories", {}).values():
        if isinstance(cat_data, list):
            total += len(cat_data)
        elif isinstance(cat_data, dict):
            total += len(cat_data.get("keywords", []))
    return total


def _word_in_text(word: str, text: str) -> bool:
    word = word.lower().strip()
    if not word:
        return False
    return bool(re.search(rf"\b{re.escape(word)}(?:s|es)?\b", text))


def is_safe_tag(tag: str, allow_ordinary_humans: bool = False) -> bool:
    if not tag:
        return False
    tag_lower = tag.lower().strip()
    data = _load_all()

    is_niqab = any(_word_in_text(nw, tag_lower) for nw in data["niqab_allowed"])

    for forbidden in data["forbidden_flat"]:
        if _word_in_text(forbidden, tag_lower):
            return False

    if is_niqab or allow_ordinary_humans:
        return True

    for forbidden in data["humans_flat"]:
        if _word_in_text(forbidden, tag_lower):
            return False

    return True


def is_safe_query(query: str, allow_ordinary_humans: bool = False) -> bool:
    return is_safe_tag(query, allow_ordinary_humans=allow_ordinary_humans)


def get_allowed_categories() -> dict:
    return _load_all()["allowed"].get("categories", {})


def get_allowed_keywords() -> list:
    out = []
    for cat in get_allowed_categories().values():
        if isinstance(cat, list):
            out.extend(cat)
        elif isinstance(cat, dict):
            out.extend(cat.get("keywords", []))
    return out


@lru_cache(maxsize=2)
def get_llm_forbidden_rules(allow_ordinary_humans: bool = False) -> str:
    data = _load_all()
    lines = ["ممنوع نهائياً ذكر أو تصوير أي مما يلي:"]
    groups = {
        "animals": "حيوانات محظورة",
        "objects": "مركبات وأشياء حديثة",
        "themes":  "مفاهيم ممنوعة",
        "islamic": "محظورات إسلامية",
    }
    for key, label in groups.items():
        items = _flatten_forbidden(data[key])
        if items:
            lines.append(f"- {label}: {', '.join(sorted(items)[:20])}")

    if allow_ordinary_humans:
        lines.append("- محظورات التجسيد المطلقة: يُمنع تماماً تصوير أو وصف هيئة الذات الإلهية أو الأنبياء أو المرسلين أو الصحابة أو الملائكة أو المبشرين بالجنة أو النساء المسلمات.")
        lines.append("✅ المسموح في القصص: البشر العاديون تاريخياً بوقار وحشمة عند الحاجة القصصية (أقوام، عمال، قوافل)، إضافة إلى المناظر الطبيعية والفضاء والعمارة التاريخية.")
    else:
        humans = _flatten_forbidden(data["humans"])
        if humans:
            lines.append(f"- بشر (وجوه/أجساد): {', '.join(sorted(humans)[:15])}")
        lines.append("")
        lines.append("⚠️ استثناء: المنقبات فقط (niqab/burqa) مسموحات كوسائط بصرية، بشرط عدم ظهور الوجه أو الأيدي أو الزينة.")
        lines.append("✅ المسموح: مناظر طبيعية، فضاء، براكين، أعاصير، فيضانات، مساجد، دمشق القديمة، طيور، زهور، حيوانات (عدا المحظورة).")

    return "\n".join(lines)


def reload_filters():
    _load_all.cache_clear()
    get_llm_forbidden_rules.cache_clear()
