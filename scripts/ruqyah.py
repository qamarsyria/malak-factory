"""نوع الرقية الشرعية — إنتاج فيديو رقية."""
import json
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
