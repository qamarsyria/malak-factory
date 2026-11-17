"""
filters_loader.py — محمّل فلاتر الفيديو والمحتوى
يوحد 6 ملفات JSON في واجهة برمجية واحدة.

الاستخدام:
    from filters_loader import is_safe_tag, is_safe_query, get_llm_forbidden_rules
    
    if is_safe_tag("beautiful forest"):
        print("آمن")
    
    rules = get_llm_forbidden_rules()
    # ← نص عربي/إنجليزي جاهز للـ prompt
"""

import json
import re
from pathlib import Path
from functools import lru_cache
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
FILTERS_DIR = _REPO_ROOT / "data" / "filters"

# ═══════════════════════════════════════════════════════════
# تحميل الملفات الستة (مرة واحدة، مع cache)
# ═══════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _load_all() -> dict:
    """يحمّل كل ملفات الفلاتر ويوحدها في قاموس واحد."""
    files = {
        "allowed":      FILTERS_DIR / "allowed_topics.json",
        "animals":      FILTERS_DIR / "forbidden_animals.json",
        "objects":      FILTERS_DIR / "forbidden_objects.json",
        "humans":       FILTERS_DIR / "forbidden_humans.json",
        "themes":       FILTERS_DIR / "forbidden_themes.json",
        "islamic":      FILTERS_DIR / "forbidden_islamic.json",
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
    
    # فلترة: قائمة الكلمات المحظورة (مسطّحة)
    forbidden_flat = set()
    for key in ("animals", "objects", "humans", "themes", "islamic"):
        forbidden_flat.update(_flatten_forbidden(data[key]))
    
    data["forbidden_flat"] = sorted(forbidden_flat)
    
    # فلترة: قائمة Niqab المسموحة (استثناء)
    niqab = data["humans"].get("exception", {}).get("niqab_woman", {})
    data["niqab_allowed"] = set(niqab.get("allowed_tags", []))
    
    print(
        f"✅ فلاتر محمّلة: "
        f"مسموح={_count_allowed(data['allowed'])}, "
        f"محظور={len(forbidden_flat)}, "
        f"niqab={len(data['niqab_allowed'])}"
    )
    return data


def _flatten_forbidden(obj, parent_key="") -> set:
    """يفلطح أي هيكل JSON إلى مجموعة كلمات."""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("version", "note", "conditions", "allowed_tags", "exception"):
                continue
            out.update(_flatten_forbidden(v, k))
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, str):
                out.add(item.lower().strip())
            else:
                out.update(_flatten_forbidden(item, parent_key))
    elif isinstance(obj, str):
        out.add(obj.lower().strip())
    return out


def _count_allowed(allowed_obj: dict) -> int:
    """يحسب عدد الكلمات المسموحة."""
    total = 0
    for cat_data in allowed_obj.get("categories", {}).values():
        if isinstance(cat_data, list):
            total += len(cat_data)
    return total


# ═══════════════════════════════════════════════════════════
# دوال الفحص
# ═══════════════════════════════════════════════════════════

def is_safe_tag(tag: str) -> bool:
    """
    هل هذا الـ tag من Pixabay آمن؟
    - يرفض أي tag فيه كلمة محظورة (كلب، وجه، سيارة...).
    - يسمح بالمنقبات استثناءً.
    """
    if not tag:
        return False
    
    tag_lower = tag.lower().strip()
    data = _load_all()
    
    # 1. هل هو منقبة؟ (استثناء مسموح)
    if any(allowed in tag_lower for allowed in data["niqab_allowed"]):
        # لكن نتأكد ألا يحتوي على كلمات محظورة أخرى
        for forbidden in data["forbidden_flat"]:
            if forbidden in tag_lower and forbidden not in data["niqab_allowed"]:
                return False
        return True
    
    # 2. فحص عادي ضد كل المحظورات
    for forbidden in data["forbidden_flat"]:
        if _word_in_text(forbidden, tag_lower):
            return False
    
    return True


def is_safe_query(query: str) -> bool:
    """هل هذا الاستعلام آمن للبحث؟ (نفس منطق tag)."""
    return is_safe_tag(query)


def get_allowed_categories() -> dict:
    """يرجع قاموس الفئات المسموحة."""
    return _load_all()["allowed"].get("categories", {})


def get_allowed_keywords() -> list:
    """يرجع كل الكلمات المسموحة مسطّحة."""
    categories = get_allowed_categories()
    out = []
    for cat in categories.values():
        if isinstance(cat, list):
            out.extend(cat)
    return out


# ═══════════════════════════════════════════════════════════
# للنصوص (لـ LLM prompts)
# ═══════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def get_llm_forbidden_rules() -> str:
    """
    يرجع نصاً منسّقاً بالمحظورات، جاهزاً للإدراج في prompt LLM.
    """
    data = _load_all()
    lines = ["ممنوع نهائياً ذكر أو تصوير أي مما يلي:"]
    
    groups = {
        "animals":      "حيوانات محظورة",
        "objects":      "مركبات وأشياء حديثة",
        "humans":       "بشر (وجوه/أجساد/أيدي)",
        "themes":       "مفاهيم ممنوعة",
        "islamic":      "محظورات إسلامية",
    }
    
    for key, label in groups.items():
        items = _flatten_forbidden(data[key])
        if items:
            sample = ", ".join(sorted(items)[:20])
            lines.append(f"- {label}: {sample}")
    
    lines.append("\nالمسموح: مناظر طبيعية، فضاء، براكين، أعاصير، فيضانات،")
    lines.append("مساجد، دمشق القديمة، طيور، زهور، حيوانات (عدا المحظورة).")
    lines.append("المنقبات مسموحات فقط (بدون وجه مكشوف).")
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# مساعدات داخلية
# ═══════════════════════════════════════════════════════════

def _word_in_text(word: str, text: str) -> bool:
    """
    هل الكلمة موجودة في النص؟
    - كلمات قصيرة (≤3): يجب أن تكون كلمة مستقلة (boundary).
    - كلمات طويلة: substring آمن.
    """
    word = word.lower().strip()
    if not word:
        return False
    if len(word) <= 3:
        return bool(re.search(rf"\b{re.escape(word)}\b", text))
    return word in text


def reload_filters():
    """يفرغ الـ cache (للاستخدام بعد تعديل ملفات الفلاتر)."""
    _load_all.cache_clear()
    get_llm_forbidden_rules.cache_clear()
    print("🔄 تم إعادة تحميل الفلاتر")


# ═══════════════════════════════════════════════════════════
# للاختبار المباشر
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("🧪 اختبار filters_loader.py")
    print("=" * 60)
    
    # 1. إحصائيات
    data = _load_all()
    print(f"\n📊 إحصائيات:")
    print(f"   كلمات محظورة: {len(data['forbidden_flat'])}")
    print(f"   كلمات مسموحة: {_count_allowed(data['allowed'])}")
    print(f"   استثناءات niqab: {len(data['niqab_allowed'])}")
    
    # 2. اختبار tags
    print(f"\n🔍 اختبار is_safe_tag:")
    tests = [
        ("beautiful forest",     True),
        ("old damascus rainy",   True),
        ("woman in niqab",       True),
        ("young woman smiling",  False),
        ("cute puppy playing",   False),
        ("volcano eruption lava", True),
        ("red car on highway",   False),
        ("mosque at sunset",     True),
        ("pig farm",             False),
        ("starry night sky",     True),
        ("cross on church",      False),
    ]
    
    passed = 0
    for tag, expected in tests:
        result = is_safe_tag(tag)
        status = "✅" if result == expected else "❌"
        if result == expected:
            passed += 1
        print(f"   {status} '{tag}' → {result} (متوقع: {expected})")
    
    print(f"\n📈 نجح {passed}/{len(tests)} اختبار")
    
    # 3. عرض قواعد LLM
    print(f"\n📝 get_llm_forbidden_rules() — أول 300 حرف:")
    print(get_llm_forbidden_rules()[:300] + "...")
