"""نوع الرقية الشرعية — إنتاج فيديو رقية."""
import json
import random
from pathlib import Path


RUQYAH_FILE = "data/ruqyah/ruqyah.json"


def load_all():
    """يقرأ كل الرقى من الملف."""
    p = Path(RUQYAH_FILE)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️  خطأ قراءة ruqyah: {e}")
        return []


def pick_unpublished(published_ids):
    """يختار رقية لم تُنشر بعد."""
    items = load_all()
    for item in items:
        if item["id"] not in published_ids:
            return item
    # انتهت — أعد من البداية
    return items[0] if items else None


def build_script(item):
    """يبني نص الرقية للصوت والفيديو."""
    return {
        "question": item.get("title", ""),
        "answer": item.get("text", ""),
        "attribution": item.get("source", ""),
        "voice": item.get("voice", "Charon"),
        "used_original": True,
    }
