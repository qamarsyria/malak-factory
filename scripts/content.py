"""اختيار الموضوع اليومي حسب التوزيع الأسبوعي + تتبع المنشور."""
import json
from pathlib import Path
from datetime import datetime, timezone
from config import (
    FAQ_JSON, HADITH_JSON, RIYAD_JSON,
    STATE_FILE, SOURCE_ROTATION,
)


def _read_json(path):
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️  تعذر قراءة {path}: {e}")
        return []


def _normalize_faq(item):
    """المسألة: سؤال + جواب."""
    q = (item.get("question") or "").strip()
    a = (item.get("answer") or "").strip()
    if not q or not a:
        return None
    return {
        "id": item.get("id") or f"faq-{hash(q) % 100000}",
        "source_type": "faq",
        "question": q,
        "text": a,
        "attribution": item.get("source", ""),
        "category": item.get("category", ""),
    }


def _normalize_hadith(item):
    """الحديث: نص الحديث + مصدره."""
    text = (item.get("answer") or item.get("text") or "").strip()
    if len(text) < 30:
        return None
    return {
        "id": item.get("id") or f"hadith-{hash(text) % 100000}",
        "source_type": "hadith",
        "question": "",
        "text": text,
        "attribution": item.get("source", ""),
        "category": "",
    }


def _normalize_riyad(item):
    """حديث رياض الصالحين: متن + راوي + مخرج."""
    text = (item.get("text") or "").strip()
    if len(text) < 30:
        return None
    narrator = (item.get("narrator") or "").strip()
    source = (item.get("source") or "").strip()
    attribution = source
    if narrator and source:
        attribution = f"{source} — عن {narrator}"
    return {
        "id": item.get("id") or f"riyad-{item.get('number', hash(text) % 100000)}",
        "source_type": "riyad",
        "question": "",
        "text": text,
        "attribution": attribution,
        "category": item.get("chapter", ""),
    }


def load_all_items():
    """يرجع dict بمفاتيح faq/hadith/riyad وقوائم موحّدة."""
    faq = [x for x in (_normalize_faq(i) for i in _read_json(FAQ_JSON)) if x]
    hadith = [x for x in (_normalize_hadith(i) for i in _read_json(HADITH_JSON)) if x]
    riyad = [x for x in (_normalize_riyad(i) for i in _read_json(RIYAD_JSON)) if x]

    print(f"📚 محتوى محمّل: faq={len(faq)}, hadith={len(hadith)}, riyad={len(riyad)}")
    return {"faq": faq, "hadith": hadith, "riyad": riyad}


def _load_state():
    p = Path(STATE_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"published_ids": [], "last_run": None, "day_index": 0}


def _save_state(state):
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _pick_from_pool(pool, published_set):
    """أول عنصر غير منشور. إذا انتهى الكل، نبدأ دورة جديدة."""
    for item in pool:
        if item["id"] not in published_set:
            return item
    # انتهى المصدر → أعد من البداية
    return pool[0] if pool else None


def pick_next():
    """
    يرجع (item, state) حسب التوزيع اليومي.
    يحدّث day_index تلقائياً.
    """
    pools = load_all_items()
    state = _load_state()
    published = set(state.get("published_ids", []))

    # إذا كانت كل المصادر فارغة
    if not any(pools.values()):
        raise RuntimeError("لا يوجد محتوى. تأكد من وجود JSON في data/")

    day_index = state.get("day_index", 0) % len(SOURCE_ROTATION)
    source_key = SOURCE_ROTATION[day_index]

    # إذا المصدر فارغ، جرّب المصادر الأخرى بالترتيب
    tried = set()
    item = None
    while source_key not in tried:
        tried.add(source_key)
        if pools.get(source_key):
            item = _pick_from_pool(pools[source_key], published)
            if item:
                break
        day_index = (day_index + 1) % len(SOURCE_ROTATION)
        source_key = SOURCE_ROTATION[day_index]

    if not item:
        raise RuntimeError("لم يُعثر على عنصر مناسب")

    # احفظ المصدر واليوم لاستخدامهما بعد النجاح
    state["_pending"] = {
        "day_index": day_index,
        "source_key": source_key,
        "item_id": item["id"],
    }
    return item, state


def mark_published(item, state):
    """تُستدعى بعد نجاح النشر."""
    pending = state.pop("_pending", None)
    if pending:
        state["day_index"] = (pending["day_index"] + 1) % len(SOURCE_ROTATION)

    ids = set(state.get("published_ids", []))
    ids.add(item["id"])
    state["published_ids"] = list(ids)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    print(f"💾 حالة محدّثة — id={item['id']}, المجموع={len(ids)}")
