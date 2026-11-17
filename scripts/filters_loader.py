"""
filters_loader.py — محمّل فلاتر الفيديو والمحتوى
يوحد 6 ملفات JSON في واجهة برمجية واحدة.

يدعم استثناء "المنقبات":
  - المنقبة تُفحص فقط ضد الحيوانات/الأشياء/الرموز/المواضيع المحظورة.
  - لا تُرفض بسبب كلمات مثل "woman" أو "female" أو "person".

الاستخدام:
    from filters_loader import is_safe_tag, is_safe_query, get_llm_forbidden_rules

    if is_safe_tag("woman in niqab reading quran"):
        print("آمن")   # ✅

    if not is_safe_tag("young woman smiling"):
        print("مرفوض")  # ❌
"""

import json
import re
from pathlib import Path
from functools import lru_cache

_REPO_ROOT = Path(__file__).resolve().parent.parent
FILTERS_DIR = _REPO_ROOT / "data" / "filters"

# ═══════════════════════════════════════════════════════════
# تحميل الملفات (مرة واحدة)
# ═══════════════════════════════════════════════════════════
@lru_cache(maxsize=1)
def _load_all() -> dict:
    """يحمّل كل ملفات الفلاتر ويوحدها في قاموس واحد."""
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

    # فلترة: كلمات محظورة (مسطّحة) — تستثني humans (لها معالجة خاصة)
    forbidden_flat = set()
    for key in ("animals", "objects", "themes", "islamic"):
        forbidden_flat.update(_flatten_forbidden(data[key]))

    data["forbidden_flat"] = sorted(forbidden_flat)

    # كلمات humans بشكل منفصل (للمنقبات)
    data["humans_flat"] = sorted(_flatten_forbidden(data["humans"]))

    # niqab المسموحة
    niqab_data = data["humans"].get("exception", {}).get("niqab_woman", {})
    data["niqab_allowed"] = set(niqab_data.get("allowed_tags", []))

    print(
        f"✅ فلاتر: "
        f"مسموح={_count_allowed(data['allowed'])}, "
        f"محظور={len(forbidden_flat)}, "
        f"human_words={len(data['humans_flat'])}, "
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
    هل هذا الـ tag آمن؟

    المنطق:
      1. إذا الـ tag يحتوي كلمة منقبة (niqab/burqa...) → نتجاهل كلمات humans
         (woman, person, face...) لأنه منقبة = امرأة مسلمة محتشمة.
      2. إذا لم يكن منقبة → نفحص ضد كل المحظورات (بما فيها humans).
      3. نتحقق دائماً ضد الحيوانات/الأشياء/المواضيع/الإسلاميات.
    """
    if not tag:
        return False

    tag_lower = tag.lower().strip()
    data = _load_all()

    # 1. هل هو منقبة؟
    is_niqab = any(niqab_word in tag_lower for niqab_word in data["niqab_allowed"])

    # 2. إذا كان منقبة → نتجاهل كلمات humans
    if is_niqab:
        for forbidden in data["forbidden_flat"]:
            if _word_in_text(forbidden, tag_lower):
                return False
        return True

    # 3. فحص عادي — ضد كل شيء
    for forbidden in data["forbidden_flat"]:
        if _word_in_text(forbidden, tag_lower):
            return False
    for forbidden in data["humans_flat"]:
        if _word_in_text(forbidden, tag_lower):
            return False

    return True


def is_safe_query(query: str) -> bool:
    """هل هذا الاستعلام آمن للبحث؟ (نفس منطق tag)."""
    return is_safe_tag(query)


# ═══════════════════════════════════════════════════════════
# للنصوص (لـ LLM prompts)
# ═══════════════════════════════════════════════════════════
def get_allowed_categories() -> dict:
    """يرجع قاموس الفئات المسموحة."""
    return _load_all()["allowed"].get("categories", {})


def get_allowed_keywords() -> list:
    """يرجع كل الكلمات المسموحة مسطّحة."""
    out = []
    for cat in get_allowed_categories().values():
        if isinstance(cat, list):
            out.extend(cat)
    return out


@lru_cache(maxsize=1)
def get_llm_forbidden_rules() -> str:
    """نص منسّق بالمحظورات، جاهز للـ prompt."""
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
            sample = ", ".join(sorted(items)[:20])
            lines.append(f"- {label}: {sample}")

    # humans
    humans = _flatten_forbidden(data["humans"])
    if humans:
        lines.append(f"- بشر (وجوه/أجساد): {', '.join(sorted(humans)[:15])}")

    lines.append("")
    lines.append("⚠️ استثناء: المنقبات فقط (niqab/burqa) مسموحات كوسائط بصرية،")
    lines.append("    بشرط عدم ظهور الوجه أو الأيدي أو الزينة.")
    lines.append("")
    lines.append("✅ المسموح: مناظر طبيعية، فضاء، براكين، أعاصير، فيضانات،")
    lines.append("    مساجد، دمشق القديمة، طيور، زهور، حيوانات (عدا المحظورة).")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# مساعدات داخلية
# ═══════════════════════════════════════════════════════════
def _word_in_text(word: str, text: str) -> bool:
    """
    هل الكلمة موجودة في النص؟
    - ≤3 أحرف: word boundary فقط (لتجنب "man" داخل "romantic").
    - أطول: substring عادي.
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

    data = _load_all()
    print(f"\n📊 إحصائيات:")
    print(f"   كلمات محظورة: {len(data['forbidden_flat'])}")
    print(f"   كلمات بشر:    {len(data['humans_flat'])}")
    print(f"   كلمات مسموحة: {_count_allowed(data['allowed'])}")
    print(f"   استثناءات niqab: {len(data['niqab_allowed'])}")

    print(f"\n🔍 اختبار is_safe_tag:")
    tests = [
        ("beautiful forest",           True),
        ("old damascus rainy",         True),
        ("woman in niqab",             True),    # ← بعد الإصلاح
        ("niqab woman praying",        True),    # ← بعد الإصلاح
        ("woman in niqab reading quran", True),  # ← بعد الإصلاح
        ("young woman smiling",        False),
        ("cute puppy playing",         False),
        ("volcano eruption lava",      True),
        ("red car on highway",         False),
        ("mosque at sunset",           True),
        ("pig farm",                   False),
        ("starry night sky",           True),
        ("cross on church",            False),
        ("man walking",                False),
        ("niqab woman with puppy",     False),   # ← يجب يُرفض (كلب)
        ("niqab woman in mosque",      True),    # ← يجب يُقبل
    ]

    passed = 0
    for tag, expected in tests:
        result = is_safe_tag(tag)
        status = "✅" if result == expected else "❌"
        if result == expected:
            passed += 1
        print(f"   {status} '{tag}' → {result} (متوقع: {expected})")

    print(f"\n📈 نجح {passed}/{len(tests)} اختبار")

    print(f"\n📝 get_llm_forbidden_rules() — أول 400 حرف:")
    print(get_llm_forbidden_rules()[:400] + "...")
