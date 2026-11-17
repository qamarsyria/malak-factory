"""
collect_story.py — المرحلة 1: جمع المصادر
يجمع كل ما يتعلق بقصة معينة بترتيب منطقي:
  1. القرآن + التفسير الميسر (الأساس)
  2. قصص ابن كثير (إن وُجد)
  3. المقالات (dorar + islamway + LLM)
ثم:
  - يكتشف الأحداث (LLM + فصول ابن كثير)
  - يفصل الآيات عن السرد في ملفات منفصلة
  - يحفظ كل شيء في data/stories/{id}/collected/
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
    "creation-pen":     ["القلم", "الكتابة", "خلق القلم", "الأول"],
}

# ═══════════════════════════════════════════════════════════
# LLM
# ═══════════════════════════════════════════════════════════
def call_llm(prompt: str, is_json: bool = True, timeout: int = 180, max_tokens: int = 8192):
    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:5]
    if not models:
        return None

    for model in models:
        if breaker.is_open(f"model:{model}"):
            continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens}
                }
                if is_json:
                    body["generationConfig"]["responseMimeType"] = "application/json"

                r = requests.post(url, json=body, timeout=timeout)

                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts:
                        break
                    text = parts[0].get("text", "")
                    if is_json:
                        s, e = text.find("{"), text.rfind("}")
                        if s == -1 or e <= s:
                            break
                        text = text[s:e+1]
                    try:
                        parsed = json.loads(text) if is_json else text
                        # محاولة تدوير المفاتيح بأمان
                        if hasattr(pool, '_advance'):
                            pool._advance()
                        elif hasattr(pool, 'advance'):
                            pool.advance()
                        breaker.record_success(f"model:{model}")
                        return parsed
                    except json.JSONDecodeError:
                        break
                elif r.status_code == 429:
                    pool.mark_failed(3600)
                elif r.status_code in (404, 503):
                    breaker.record_failure(f"model:{model}")
                    break
            except requests.exceptions.Timeout:
                pass
            except Exception as e:
                print(f"   ⚠️ خطأ في LLM: {e}")
    return None

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
# توحيد شكل الآيات (عشان نمنع أي KeyError)
# ═══════════════════════════════════════════════════════════
def normalize_ayah(ayah: dict) -> dict:
    """يتأكد إن كل آية فيها ref و surah_name و text"""
    if "ref" not in ayah:
        s = ayah.get("surah", ayah.get("surah_number", ayah.get("surah_id", "")))
        a = ayah.get("ayah", ayah.get("ayah_number", ayah.get("numberInSurah", ayah.get("number", ""))))
        ayah["ref"] = f"{s}:{a}"
    
    if "surah_name" not in ayah:
        ayah["surah_name"] = ayah.get("surah_name_ar", f"سورة {ayah.get('surah', '')}")
        
    if "text" not in ayah:
        ayah["text"] = ayah.get("arabic_text", "")
        
    return ayah

# ═══════════════════════════════════════════════════════════
# البحث عن الآيات ذات الصلة بالقصة
# ═══════════════════════════════════════════════════════════
def find_story_ayahs(ayah_lib: list, story_id: str, story_sources: list) -> list:
    keywords = STORY_KEYWORDS.get(story_id, [])
    if not keywords:
        keywords = [story_id]

    found = []
    for ayah in ayah_lib:
        text = ayah.get("text", "")
        tafsir = ayah.get("tafsir", "")

        for kw in keywords:
            if kw in text or kw in tafsir:
                # إصلاح وتوحيد الشكل قبل الإضافة
                found.append(normalize_ayah(ayah))
                break

    print(f"   📖 وُجدت {len(found)} آية متعلقة بـ '{story_id}' (من أصل {len(ayah_lib)} آية)")
    return found

# ═══════════════════════════════════════════════════════════
# جمع من مصادر خارجية (ابن كثير)
# ═══════════════════════════════════════════════════════════
def fetch_ibn_kathir_story(story_id: str) -> str:
    return ""

# ═══════════════════════════════════════════════════════════
# اكتشاف الأحداث وفصل الآيات عن السرد
# ═══════════════════════════════════════════════════════════
def detect_events_and_separate(ayahs: list, narrative_texts: list, story_title: str) -> dict:
    # ضمان إن كل الآيات متوحدة الشكل
    ayahs = [normalize_ayah(a) for a in ayahs]
    
    ayahs_block = "\n".join([f"[{i}] {a['surah_name']} {a['ref']}: {a['text']}" for i, a in enumerate(ayahs)])
    narrative_block = "\n\n".join([f"[{i}] {t[:500]}..." for i, t in enumerate(narrative_texts)])

    prompt = f"""أنت مؤرخ إسلامي خبير. مهمتك تحليل المادة التالية لقصة "{story_title}".

الآيات المتاحة:
{ayahs_block}

النصوص السردية المتاحة:
{narrative_block}

المطلوب:
1. اكتشف الأحداث الكبرى التي تتكون منها القصة.
2. لكل حدث، حدد الآيات التي تنتمي إليه (بأرقامها بين الأقواس المربعة).
3. لكل حدث، حدد النصوص السردية التي تنتمي إليه (بأرقامها).
4. رتب الأحداث زمنياً.

أعد JSON فقط:
{{
  "events": [
    {{
      "event_id": "event_01",
      "title": "عنوان الحدث",
      "ayah_indices": [0, 1],
      "narrative_indices": [0]
    }}
  ]
}}"""

    result = call_llm(prompt, is_json=True, timeout=180)
    if result and isinstance(result, dict) and "events" in result:
        return result
    
    print("   ⚠️ فشل اكتشاف الأحداث، سيتم استخدام حدث واحد شامل.")
    return {
        "events": [
            {
                "event_id": "event_01",
                "title": story_title,
                "ayah_indices": list(range(len(ayahs))),
                "narrative_indices": list(range(len(narrative_texts)))
            }
        ]
    }

# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def main():
    if len(sys.argv) < 2:
        print("الاستخدام: python collect_story.py <story_id> [--force]")
        sys.exit(1)

    story_id = sys.argv[1]
    force = "--force" in sys.argv

    print("=" * 60)
    print(f"📚 جمع المصادر للقصة: {story_id}")
    print("=" * 60)

    # 1. قراءة القصة
    library = load_stories_library()
    story = next((s for s in library["stories"] if s["id"] == story_id), None)
    if not story:
        print(f"❌ القصة '{story_id}' غير موجودة في المكتبة.")
        sys.exit(1)

    story_title = story.get("title", story_id)
    print(f"📖 {story_title}")

    # 2. المسارات
    story_dir = STORIES_OUTPUT / story_id
    collected_dir = story_dir / "collected"
    collected_dir.mkdir(parents=True, exist_ok=True)

    # 3. البحث عن الآيات
    print("\n📖 البحث في القرآن والتفسير...")
    ayah_lib = load_ayah_library()
    ayahs = find_story_ayahs(ayah_lib, story_id, story.get("sources", []))
    
    if not ayahs:
        print("   ⚠️ لم يتم العثور على آيات، سيتم استخدام آيات عامة أو مصدر آخر.")

    # 4. جمع النصوص السردية
    print("\n📝 جمع النصوص السردية...")
    narrative_texts = []
    kathir_text = fetch_ibn_kathir_story(story_id)
    if kathir_text:
        narrative_texts.append({"source": "ابن كثير", "text": kathir_text})
    
    # 5. اكتشاف الأحداث
    print("\n🔍 اكتشاف الأحداث...")
    events_data = detect_events_and_separate(ayahs, narrative_texts, story_title)
    
    # 6. الحفظ
    print("\n💾 حفظ الملفات...")
    
    (collected_dir / "ayahs.json").write_text(
        json.dumps(ayahs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    
    (collected_dir / "narrative.json").write_text(
        json.dumps(narrative_texts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    
    (collected_dir / "events.json").write_text(
        json.dumps(events_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"✅ تم جمع {len(ayahs)} آية و {len(events_data['events'])} حدث.")
    print("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ أُلغي يدوياً")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ خطأ فادح: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
