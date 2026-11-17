"""
collect_story.py — المرحلة 1: جمع المصادر
يجمع كل ما يتعلق بقصة معينة بترتيب منطقي (ب):
  1. القرآن + التفسير الميسر (الأساس)
  2. قصص ابن كثير (إن وُجد)
  3. المقالات (dorar + islamway + LLM)
ثم:
  - يكتشف الأحداث (LLM + فصول ابن كثير)
  - يفصل الآيات عن السرد في ملفات منفصلة
  - يحفظ كل شيء في data/stories/{id}/collected/

الاستخدام:
    python collect_story.py <story_id>
    python collect_story.py <story_id> --force   # يتجاهل الـ cache
"""

import os, re, sys, json, gzip, time
from pathlib import Path
from datetime import datetime, timezone
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import get_breaker, notify_telegram
from filters_loader import get_llm_forbidden_rules

# ═══════════════════════════════════════════════════════════
# المسارات
# ═══════════════════════════════════════════════════════════
STORIES_JSON       = _REPO_ROOT / "data" / "library" / "stories.json"
LIBRARIES_DIR      = _REPO_ROOT / "data" / "libraries"
AYAH_LIBRARY       = LIBRARIES_DIR / "ayah-library.json.gz"
STORIES_OUTPUT     = _REPO_ROOT / "data" / "stories"

# ═══════════════════════════════════════════════════════════
# حدود
# ═══════════════════════════════════════════════════════════
MAX_TOTAL_SIZE_MB  = 2.0     # تحذير لو تجاوزت المادة 2MB
MAX_AYAHS_PER_LINE = 1       # آية واحدة لكل سطر في ayahs.json

# ═══════════════════════════════════════════════════════════
# كلمات مفتاحية لكل قصة (للبحث في التفسير)
# ═══════════════════════════════════════════════════════════
STORY_KEYWORDS = {
    "adam":             ["آدم", "أبونا آدم"],
    "idris":            ["إدريس"],
    "nuh":              ["نوح", "سفينة", "الطوفان"],
    "hud":              ["هود", "عاد", "الأحقاف"],
    "salih":            ["صالح", "ثمود", "الناقة"],
    "ibrahim":          ["إبراهيم", "الخليل", "النار", "الأصنام", "إسماعيل", "الكعبة"],
    "lut":              ["لوط", "سدوم", "عمورية"],
    "ismail":           ["إسماعيل", "الذبيح", "زمزم"],
    "ishaq":            ["إسحاق"],
    "yaqub":            ["يعقوب", "إسرائيل"],
    "yusuf":            ["يوسف", "الجب", "زليخة", "العزيز", "بنيامين"],
    "ayyub":            ["أيوب", "الصبر", "المرض"],
    "shuaib":           ["شعيب", "مدين", "أصحاب الأيكة", "الميزان"],
    "dhul-kifl":        ["ذو الكفل"],
    "musa":             ["موسى", "فرعون", "هارون", "التوراة", "بني إسرائيل", "سينا", "اليم", "عصا", "آسية"],
    "harun":            ["هارون"],
    "dawud":            ["داود", "جالوت", "طالوت", "الزبور", "المحراب"],
    "sulayman":         ["سليمان", "الهدهد", "بلقيس", "سبأ", "الجن", "الريح"],
    "ilyas":            ["إلياس"],
    "alyasa":           ["اليسع"],
    "yunus":            ["يونس", "الحوت", "نينوى"],
    "zakariya":         ["زكريا", "المحراب"],
    "yahya":            ["يحيى"],
    "isa":              ["عيسى", "المسيح", "مريم", "الإنجيل", "الحواريون"],
    "muhammad":         ["محمد", "الرسول", "النبي", "مكة", "المدينة", "بدر", "أحد", "الخندق", "فتح"],
}

# ═══════════════════════════════════════════════════════════
# قراءة المكتبات
# ═══════════════════════════════════════════════════════════
def load_stories_library() -> dict:
    if not STORIES_JSON.exists():
        raise RuntimeError(f"ملف المكتبة غير موجود: {STORIES_JSON}")
    return json.loads(STORIES_JSON.read_text(encoding="utf-8"))

def load_ayah_library() -> list:
    if not AYAH_LIBRARY.exists():
        raise RuntimeError(f"مكتبة الآيات غير موجودة: {AYAH_LIBRARY}")
    with gzip.open(AYAH_LIBRARY, "rt", encoding="utf-8") as f:
        return json.load(f)

# ═══════════════════════════════════════════════════════════
# البحث عن الآيات ذات الصلة بالقصة
# ═══════════════════════════════════════════════════════════
def find_story_ayahs(ayah_lib: list, story_id: str, story_sources: list) -> list:
    """
    يبحث عن كل الآيات التي تخص القصة في القرآن كامل:
    - في نص الآية، أو
    - في التفسير
    """
    keywords = STORY_KEYWORDS.get(story_id, [])
    if not keywords:
        # fallback: استخدم عنوان القصة
        keywords = [story_id]

    found = []
    for ayah in ayah_lib:
        text = ayah.get("text", "")
        tafsir = ayah.get("tafsir", "")

        # فحص الكلمات المفتاحية
        for kw in keywords:
            if kw in text or kw in tafsir:
                found.append(ayah)
                break

    print(f"   📖 وُجدت {len(found)} آية متعلقة بـ '{story_id}' (من أصل {len(ayah_lib)} آية)")
    return found

# ═══════════════════════════════════════════════════════════
# جمع من مصادر خارجية
# ═══════════════════════════════════════════════════════════

def fetch_ibn_kathir_story(story_id: str) -> Optional_dummy:
    pass
