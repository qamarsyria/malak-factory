"""
filters_loader.py — محمّل فلاتر الفيديو والمحتوى
يوحد 6 ملفات JSON في واجهة برمجية واحدة، مع استثناء المنقبات ومطابقة الكلمات الكاملة.
"""

import json
import re
from pathlib import Path
from functools import lru_cache

_REPO_ROOT = Path(__file__).resolve().parent.parent
FILTERS_DIR = _REPO_ROOT / "data" / "filters"


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
    """مطابقة الكلمة الكاملة (مع الجمع البسيط) لمنع تداخل مثل plane مع planet."""
    word = word.lower().strip()
    if not word:
        return False
    return bool(re.search(rf"\b{re.escape(word)}(?:s|es)?\b", text))


def is_safe_tag(tag: str) -> bool:
    if not tag:
        return False
    tag_lower = tag.lower().strip()
    data = _load_all()

    is_niqab = any(_word_in_text(nw, tag_lower) for nw in data["niqab_allowed"])

    for forbidden in data["forbidden_flat"]:
        if _word_in_text(forbidden, tag_lower):
            return False

    if is_niqab:
        return True

    for forbidden in data["humans_flat"]:
        if _word_in_text(forbidden, tag_lower):
            return False

    return True


def is_safe_query(query: str) -> bool:
    return is_safe_tag(query)


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


@lru_cache(maxsize=1)
def get_llm_forbidden_rules() -> str:
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
