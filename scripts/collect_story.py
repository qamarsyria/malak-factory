"""
collect_story.py — المرحلة 1: جمع المصادر
يجمع كل ما يتعلق بقصة معينة بترتيب منطقي:
  1. القرآن + التفسير الميسر (الأساس)
  2. تفسير ابن كثير (عبر API)
  3. قصص الأنبياء من إسلام ويب (عبر scraping)
  4. LLM كخيار أخير (لتوليد سرد من الآيات إذا فشلت كل المصادر)
ثم:
  - يكتشف الأحداث (LLM + فصول ابن كثير)
  - يفصل الآيات عن السرد في ملفات منفصلة
  - يحفظ كل شيء في data/stories/{id}/collected/
"""

import os, re, sys, json, gzip, time
from pathlib import Path
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

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
# روابط المصادر الخارجية
# ═══════════════════════════════════════════════════════════
TAFSIR_IBN_KATHIR_API = "https://quranapi.misryoum.com/api/tafsir/ibnkathir/surah/{surah}/ayah/{ayah}"
ISLAMWEB_STORIES_URL = "https://www.islamweb.net/ar/library/index.php?page=bookcontents&idfrom=1&idto=272&bk_no=86&ID=2"

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
# دوال جلب السرد من مصادر خارجية
# ═══════════════════════════════════════════════════════════
def fetch_tafsir_ibn_kathir(ayah_ref: str) -> str:
    """
    يجلب تفسير ابن كثير لآية محددة من API خارجي.
    ayah_ref: بصيغة "surah:ayah" مثل "12:4"
    """
    try:
        parts = ayah_ref.split(":")
        if len(parts) != 2:
            return ""
        surah, ayah = parts
        url = TAFSIR_IBN_KATHIR_API.format(surah=surah, ayah=ayah)
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            # استخراج النص من الاستجابة (الهيكل قد يختلف)
            text = data.get("text") or data.get("tafsir") or (data.get("data", {}) or {}).get("text")
            if text and len(str(text).strip()) > 50:
                return str(text).strip()
    except Exception as e:
        print(f"   ⚠️ فشل جلب تفسير ابن كثير لـ {ayah_ref}: {e}")
    return ""


def fetch_islamweb_stories() -> list:
    """
    يجلب نصوصاً من كتاب قصص الأنبياء على إسلام ويب.
    يعيد قائمة من dicts: {"source": "إسلام ويب", "text": "..."}
    """
    blocks = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(ISLAMWEB_STORIES_URL, headers=headers, timeout=60)
        if r.status_code != 200:
            print(f"   ⚠️ إسلام ويب أرجع HTTP {r.status_code}")
            return blocks

        soup = BeautifulSoup(r.content, "html.parser")
        # محاولة تحديد الحاوية الرئيسية للنص
        body = soup.select_one("#pagebody") or soup.select_one(".book_content") or soup.find("div", {"class": "content"})
        
        if not body:
            # fallback: أخذ كل الفقرات الطويلة
            body = soup

        # استخراج الفقرات النصية
        paragraphs = body.find_all(["p", "div"], recursive=True)
        current_text = []
        for p in paragraphs:
            txt = p.get_text(strip=True)
            if len(txt) > 100 and not re.search(r'[<>{}]', txt):
                current_text.append(txt)
                if len(" ".join(current_text)) > 3000:  # حد أقصى للـ block
                    blocks.append({"source": "إسلام ويب - قصص الأنبياء", "text": " ".join(current_text)})
                    current_text = []
        
        if current_text:
            blocks.append({"source": "إسلام ويب - قصص الأنبياء", "text": " ".join(current_text)})

        print(f"   📚 إسلام ويب: {len(blocks)} block")
    except Exception as e:
        print(f"   ⚠️ فشل جلب إسلام ويب: {e}")
    return blocks


def fetch_tafsir_muyassar(ayahs: list) -> list:
    """
    يجلب التفسير الميسر من مكتبة الآيات المحلية (الحقل tafsir).
    """
    blocks = []
    for ayah in ayahs:
        tafsir = (ayah.get("tafsir") or "").strip()
        if tafsir and len(tafsir) > 50:
            blocks.append({
                "source": f"التفسير الميسر - {ayah.get('ref', '')}",
                "text": tafsir,
                "related_ayah": ayah.get("ref", "")
            })
    print(f"   📚 التفسير الميسر: {len(blocks)} block")
    return blocks


def generate_fallback_narrative(story_title: str, ayahs: list) -> list:
    """
    خيار أخير: توليد سرد من الآيات عبر LLM إذا فشلت كل المصادر.
    """
    if not ayahs:
        return []
    ayahs_summary = "\n".join([
        f"- {a.get('surah_name', '')} {a.get('ref', '')}: {a.get('text', '')}"
        for a in ayahs[:20]
    ])
    prompt = f"""أنت راوي قصص إسلامي. اكتب سرداً قصصياً موثقاً (300-500 كلمة) 
لقصة "{story_title}" بناءً على الآيات التالية فقط، دون إسرائيليات:

{ayahs_summary}

أعد JSON: {{"narrative": [{{"text": "...", "source": "توليد من الآيات"}}]}}"""

    result = call_llm(prompt, is_json=True, timeout=120)
    blocks = []
    if result and isinstance(result, dict):
        for block in result.get("narrative", []):
            if block.get("text") and len(block["text"]) > 50:
                blocks.append({
                    "source": block.get("source", "توليد من الآيات"),
                    "text": block["text"]
                })
    print(f"   🤖 LLM fallback: {len(blocks)} block")
    return blocks

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
                found.append(normalize_ayah(ayah))
                break

    print(f"   📖 وُجدت {len(found)} آية متعلقة بـ '{story_id}' (من أصل {len(ayah_lib)} آية)")
    return found

# ═══════════════════════════════════════════════════════════
# اكتشاف الأحداث وفصل الآيات عن السرد
# ═══════════════════════════════════════════════════════════
def detect_events_and_separate(ayahs: list, narrative_texts: list, story_title: str) -> dict:
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

    # ═══════════════════════════════════════════════════════════
    # 4. جمع السرد من مصادر متعددة (الأولوية بالترتيب)
    # ═══════════════════════════════════════════════════════════
    print("\n📝 جمع السرد من مصادر متعددة...")
    narrative_texts = []

    # المصدر 1: التفسير الميسر (من مكتبة الآيات المحلية)
    muyassar_blocks = fetch_tafsir_muyassar(ayahs)
    narrative_texts.extend(muyassar_blocks)

    # المصدر 2: تفسير ابن كثير (عبر API) - لكل آية
    print("   📚 جلب تفسير ابن كثير (API)...")
    for ayah in ayahs[:10]:  # حد أقصى 10 آيات لتوفير الوقت
        ref = ayah.get("ref", "")
        if ref:
            kathir_text = fetch_tafsir_ibn_kathir(ref)
            if kathir_text:
                narrative_texts.append({
                    "source": f"ابن كثير - {ref}",
                    "text": kathir_text,
                    "related_ayah": ref
                })
            time.sleep(0.5)  # تجنب الحظر
    print(f"   📚 ابن كثير: {len([b for b in narrative_texts if 'ابن كثير' in b.get('source', '')])} block")

    # المصدر 3: إسلام ويب (كتاب قصص الأنبياء)
    islamweb_blocks = fetch_islamweb_stories()
    narrative_texts.extend(islamweb_blocks)

    # الخيار الأخير: LLM (فقط إذا لم يُجمع أي سرد)
    if not narrative_texts:
        print("   ⚠️ لم يُجمع أي سرد من المصادر المتاحة، جاري التوليد عبر LLM...")
        llm_blocks = generate_fallback_narrative(story_title, ayahs)
        narrative_texts.extend(llm_blocks)

    # ═══════════════════════════════════════════════════════════
    # 5. التحقق النهائي: لا تحفظ ملفاً فارغاً!
    # ═══════════════════════════════════════════════════════════
    if not narrative_texts:
        print("\n❌ فشل جمع السرد نهائياً — جميع المصادر فارغة أو فاشلة.")
        print("   🔧 تحقق من: اتصال API، صلاحية روابط إسلام ويب، وجود تفسير في مكتبة الآيات.")
        notify_telegram(
            f"❌ فشل جمع السرد لقصة: {story_title}\n"
            f"🔗 المصادر: التفسير الميسر، ابن كثير API، إسلام ويب\n"
            f"💡 تحقق من الاتصال بالإنترنت وصلاحية الروابط.",
            level="error"
        )
        sys.exit(1)

    print(f"\n✅ إجمالي السرد: {len(narrative_texts)} block")
    for i, b in enumerate(narrative_texts[:5]):
        print(f"      [{i}] {b.get('source', '')}: {b.get('text', '')[:80]}...")

    # 6. اكتشاف الأحداث
    print("\n🔍 اكتشاف الأحداث...")
    events_data = detect_events_and_separate(ayahs, narrative_texts, story_title)
    
    # 7. الحفظ
    print("\n💾 حفظ الملفات...")
    
    (collected_dir / "ayahs_all.json").write_text(
        json.dumps(ayahs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (collected_dir / "narrative_all.json").write_text(
        json.dumps(narrative_texts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    
    events_dir = collected_dir / "events"
    events_dir.mkdir(exist_ok=True)

    for i, ev in enumerate(events_data.get("events", [])):
        ev_id = ev.get("event_id", f"event_{i+1:02d}")
        ev_dir = events_dir / ev_id
        ev_dir.mkdir(exist_ok=True)
        
        ayah_indices = ev.get("ayah_indices", [])
        ev_ayahs = [ayahs[idx] for idx in ayah_indices if 0 <= idx < len(ayahs)]
        
        narr_indices = ev.get("narrative_indices", [])
        ev_narr = [narrative_texts[idx] for idx in narr_indices if 0 <= idx < len(narrative_texts)]
        
        (ev_dir / "ayahs.json").write_text(
            json.dumps(ev_ayahs, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (ev_dir / "narrative.json").write_text(
            json.dumps(ev_narr, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (ev_dir / "meta.json").write_text(
            json.dumps({"event_id": ev_id, "title": ev.get("title", ev_id)}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"✅ تم جمع {len(ayahs)} آية و {len(events_data['events'])} حدث.")
    print(f"   📁 تم إنشاء مجلدات الأحداث في: {events_dir}")
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
