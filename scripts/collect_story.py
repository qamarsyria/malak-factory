"""
collect_story.py — المرحلة 1: جمع المصادر (نظام الحدث أولاً - Story-First)
يتبع منهجية السرد الزمني:
  1. يجمع السرد التاريخي القصصي من المصادر الموثوقة.
  2. يقسم السرد إلى أحداث زمنية متسلسلة (Timeline).
  3. يطابق الآيات القرآنية الداعمة لكل حدث (من المكتبة المحلية أو عبر API).
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

# ═══════════════════════════════════════════════════════════
# المسارات
# ═══════════════════════════════════════════════════════════
STORIES_JSON       = _REPO_ROOT / "data" / "library" / "stories.json"
AYAH_LIBRARY       = _REPO_ROOT / "data" / "libraries" / "ayah-library.json.gz"
STORIES_OUTPUT     = _REPO_ROOT / "data" / "stories"

# ═══════════════════════════════════════════════════════════
# استدعاء LLM
# ═══════════════════════════════════════════════════════════
def call_llm(prompt: str, is_json: bool = True, timeout: int = 180, max_tokens: int = 8192):
    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:5]
    if not models: return None

    for model in models:
        if breaker.is_open(f"model:{model}"): continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens}
                }
                if is_json: body["generationConfig"]["responseMimeType"] = "application/json"
                r = requests.post(url, json=body, timeout=timeout)
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts: break
                    text = parts[0].get("text", "")
                    if is_json:
                        s, e = text.find("{"), text.rfind("}")
                        if s == -1 or e <= s: break
                        text = text[s:e+1]
                    try:
                        parsed = json.loads(text) if is_json else text
                        if hasattr(pool, '_advance'): pool._advance()
                        elif hasattr(pool, 'advance'): pool.advance()
                        breaker.record_success(f"model:{model}")
                        return parsed
                    except json.JSONDecodeError: break
                elif r.status_code == 429: pool.mark_failed(3600)
                elif r.status_code in (404, 503): breaker.record_failure(f"model:{model}"); break
            except Exception: pass
    return None

# ═══════════════════════════════════════════════════════════
# تحميل مكتبة الآيات (أو استخدام API كبديل)
# ═══════════════════════════════════════════════════════════
def load_ayah_library() -> dict:
    lookup = {}
    if AYAH_LIBRARY.exists():
        try:
            with gzip.open(AYAH_LIBRARY, "rt", encoding="utf-8") as f:
                ayahs = json.load(f)
            for a in ayahs:
                s = a.get("surah", a.get("surah_number"))
                v = a.get("ayah", a.get("ayah_number"))
                txt = a.get("text", a.get("arabic_text", ""))
                tafsir = a.get("tafsir", "")
                if s and v and txt:
                    lookup[f"{s}:{v}"] = {"text": txt, "tafsir": tafsir}
            print(f"✅ تم تحميل {len(lookup)} آية من المكتبة المحلية.")
        except Exception as e:
            print(f"⚠️ فشل تحميل المكتبة المحلية: {e}")
    else:
        print("⚠️ المكتبة المحلية غير موجودة، سيتم استخدام API (AlQuran Cloud) لجلب الآيات.")
    return lookup

def fetch_ayah_from_api(surah: int, ayah: int) -> dict:
    """جلب الآية والتفسير من API مباشرة"""
    try:
        text_url = f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah}/quran-uthmani"
        tafsir_url = f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah}/ar.muyassar"
        
        text_data = ""
        r_text = requests.get(text_url, timeout=10)
        if r_text.status_code == 200:
            text_data = r_text.json()["data"]["text"]
            
        tafsir_data = ""
        r_tafsir = requests.get(tafsir_url, timeout=10)
        if r_tafsir.status_code == 200:
            tafsir_data = r_tafsir.json()["data"]["text"]
            
        return {"text": text_data, "tafsir": tafsir_data}
    except Exception as e:
        print(f"   ⚠️ فشل API للآية {surah}:{ayah}: {str(e)[:50]}")
        return {"text": "", "tafsir": ""}

# ═══════════════════════════════════════════════════════════
# بناء القصة (الحدث أولاً)
# ═══════════════════════════════════════════════════════════
def build_story_from_timeline(story_title: str, era_name: str) -> dict:
    print(f"🧠 جاري بناء الهيكل القصصي لـ '{story_title}' (العهد: {era_name})...")
    
    prompt = f"""أنت مؤرخ إسلامي خبير ومتخصص في القصص القرآني والسيرة النبوية.
مهمتك بناء "سرد تاريخي زمني" لقصة "{story_title}" من عهد "{era_name}".

الخطوات المطلوبة (اتبعها بدقة):
1. استخرج الأحداث الكبرى التي تتكون منها هذه القصة من المصادر الموثوقة (ابن كثير، الطبري، صحيح السيرة). 
2. رتب هذه الأحداث زمنياً (من البداية إلى النهاية).
3. لكل حدث، اكتب "سرداً قصصياً" موثقاً (بدون إسرائيليات) يشرح تفاصيل الحدث.
4. بعد كتابة السرد، ابحث عن الآيات القرآنية التي تثبت وتدعم هذا السرد (استخدم مرجع "سورة:آية"، مثلاً "2:30").

أعد النتيجة بصيغة JSON فقط، بدون أي نص إضافي، بهذا الشكل:
{{
  "events": [
    {{
      "event_id": "event_01",
      "title": "عنوان الحدث",
      "narrative": "السرد القصصي الكامل للحدث هنا (300-500 كلمة)...",
      "ayah_refs": ["2:30", "2:31"]
    }}
  ]
}}
"""
    result = call_llm(prompt, is_json=True, timeout=240)
    if result and "events" in result:
        return result
    
    print("⚠️ فشل بناء القصة.")
    return {"events": []}

# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def main():
    if len(sys.argv) < 2:
        print("الاستخدام: python collect_story.py <story_id>")
        sys.exit(1)

    story_id = sys.argv[1]
    print("=" * 60)
    print(f"📚 جمع المصادر (نظام الحدث أولاً): {story_id}")
    print("=" * 60)

    library = json.loads(STORIES_JSON.read_text(encoding="utf-8"))
    story = next((s for s in library["stories"] if s["id"] == story_id), None)
    if not story:
        print(f"❌ القصة '{story_id}' غير موجودة.")
        sys.exit(1)

    story_title = story.get("title", story_id)
    story_era = story.get("era", "")
    print(f"📖 {story_title} | العهد: {story_era}")

    collected_dir = STORIES_OUTPUT / story_id / "collected"
    collected_dir.mkdir(parents=True, exist_ok=True)
    events_dir = collected_dir / "events"
    events_dir.mkdir(exist_ok=True)

    print("\n🔍 تحميل مكتبة الآيات...")
    ayah_lookup = load_ayah_library()

    # 1. بناء السرد والأحداث من الـ AI
    story_data = build_story_from_timeline(story_title, story_era)
    events = story_data.get("events", [])

    if not events:
        print("❌ لم يتمكن النظام من بناء القصة.")
        sys.exit(1)

    print(f"\n✅ تم بناء القصة: {len(events)} حدث.")

    # 2. معالجة كل حدث
    total_ayahs = 0
    for ev in events:
        ev_id = ev.get("event_id", "event_01")
        ev_title = ev.get("title", "")
        ev_narrative_text = ev.get("narrative", "")
        refs = ev.get("ayah_refs", [])
        
        print(f"\n📦 {ev_id}: {ev_title}")
        print(f"   📝 سرد: {len(ev_narrative_text.split())} كلمة | 📖 مراجع: {len(refs)}")
        
        ev_ayahs = []
        ev_narrative = []
        
        if ev_narrative_text:
            ev_narrative.append({
                "source": "السرد التاريخي الموثق",
                "text": ev_narrative_text
            })
        
        # جلب الآيات
        for ref in refs:
            ayah_data = None
            
            # 1. البحث في المكتبة المحلية
            if ref in ayah_lookup:
                ayah_data = ayah_lookup[ref]
            # 2. إذا لم توجد، جلبها من الـ API
            else:
                try:
                    s, a = ref.split(":")
                    ayah_data = fetch_ayah_from_api(int(s), int(a))
                    time.sleep(0.3) # تجنب الحظر
                except:
                    pass
            
            if ayah_data and ayah_data["text"]:
                s, a = ref.split(":")
                ev_ayahs.append({
                    "ref": ref,
                    "surah": int(s),
                    "ayah": int(a),
                    "surah_name": f"سورة {s}", 
                    "text": ayah_data["text"]
                })
                
                if ayah_data.get("tafsir") and len(ayah_data["tafsir"]) > 30:
                    ev_narrative.append({
                        "source": f"التفسير الميسر - {ref}",
                        "text": ayah_data["tafsir"],
                        "related_ayah": ref
                    })
            else:
                print(f"   ⚠️ تعذر جلب الآية: {ref}")

        # حفظ الملفات
        if ev_narrative:
            ev_path = events_dir / ev_id
            ev_path.mkdir(exist_ok=True)
            
            (ev_path / "ayahs.json").write_text(json.dumps(ev_ayahs, ensure_ascii=False, indent=2), encoding="utf-8")
            (ev_path / "narrative.json").write_text(json.dumps(ev_narrative, ensure_ascii=False, indent=2), encoding="utf-8")
            (ev_path / "meta.json").write_text(json.dumps({"event_id": ev_id, "title": ev_title}, ensure_ascii=False, indent=2), encoding="utf-8")
            
            print(f"   ✅ تم الحفظ: {len(ev_ayahs)} آية، {len(ev_narrative)} سرد.")
            total_ayahs += len(ev_ayahs)

    print("\n" + "=" * 60)
    print(f"🎉 اكتمل التجميع: {len(events)} حدث، {total_ayahs} آية إجمالاً.")
    print("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ خطأ فادح: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
