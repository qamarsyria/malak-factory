"""
collect_story.py — المرحلة 1: جمع المصادر (نظام السرد الزمني العميق)
يقرأ دليل القصة (Story Guide) من stories.json، ويبني السرد القصصي بناءً عليه.
يطبق قواعد "بداية الخلق" الصارمة، ويجلب الآيات من API.
"""

import os, re, sys, json, time
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
STORIES_OUTPUT     = _REPO_ROOT / "data" / "stories"

# ═══════════════════════════════════════════════════════════
# استدعاء LLM
# ═══════════════════════════════════════════════════════════
def call_llm(prompt: str, is_json: bool = True, timeout: int = 300, max_tokens: int = 8192):
    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:5]
    if not models:
        print("❌ لا توجد موديلات متاحة!")
        return None

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
                if is_json:
                    body["generationConfig"]["responseMimeType"] = "application/json"
                
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
                elif r.status_code == 429:
                    pool.mark_failed(3600)
                elif r.status_code in (404, 503):
                    breaker.record_failure(f"model:{model}")
                    break
            except Exception as e:
                pass
    return None

# ═══════════════════════════════════════════════════════════
# جلب الآيات من API
# ═══════════════════════════════════════════════════════════
def fetch_ayah_from_api(surah: int, ayah: int) -> dict:
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
    except Exception:
        return {"text": "", "tafsir": ""}

# ═══════════════════════════════════════════════════════════
# بناء السرد القصصي من دليل القصة
# ═══════════════════════════════════════════════════════════
def build_narrative(story_data: dict) -> dict:
    guide = story_data.get("story_guide", {})
    title = story_data.get("title", "")
    era = story_data.get("era", "")
    
    core_idea = guide.get("core_idea", "")
    timeline = guide.get("timeline", [])
    style = guide.get("narrative_style", "")
    strict_mode = guide.get("strict_mode", False)
    special_rules = guide.get("special_rules", [])

    timeline_text = "\n".join([f"- {t}" for t in timeline])
    rules_text = "\n".join([f"- {r}" for r in special_rules])

    strict_instruction = ""
    if strict_mode:
        strict_instruction = (
            "\n⚠️ وضع الصرامة القصوى (Strict Mode):\n"
            "هذه القصة من عهد \"بداية الخلق\". يُمنع منعاً باتاً استخدام أي إسرائيليات أو صيغ التمريض (مثل: يُروى، قيل).\n"
            "الاعتماد فقط على آيات القرآن الكريم والأحاديث النبوية الصحيحة.\n"
            "اللغة يجب أن تكون عقائدية، دقيقة، ومهيبة، تليق بمقام الألوهية.\n"
        )
    else:
        strict_instruction = (
            "\n⚠️ تنبيه:\n"
            "يمكنك استخدام التفاصيل من كتب التفسير (ابن كثير، الطبري)، ولكن أي معلومة غير قطعية يجب أن تُسبق أو تُلحق بصيغة التمريض (يُروى، قيل) وعبارة (والله أعلم).\n"
        )

    prompt_template = """أنت مؤرخ إسلامي خبير ومتخصص في السرد القصصي التاريخي.
مهمتك كتابة سرد تاريخي زمني فائق التفصيل لقصة: "{title}".

الفكرة الأساسية للقصة:
{core_idea}

الأسلوب المطلوب:
{style}

⚠️ قاعدة الترتيب الإلزامي الصارمة جداً (خط أحمر):
يجب أن يكون عدد الأحداث في مخرجات الـ events مساوياً تماماً لعدد البنود في "التسلسل الزمني (timeline)" أدناه، وبحيث يُمثّل كل بند من هذه البنود حدثاً مستقلًا (event) وبنفس الترتيب الحرفي تماماً (البند الأول يُقابل event_01، البند الثاني يُقابل event_02، وهكذا دون أي دمج، أو تقديم، أو تأخير، أو حذف):

بنود التسلسل الزمني الإلزامية:
{timeline_text}

القواعد الصارمة:
{rules_text}
{strict_instruction}

المطلوب تقسيم القصة إلى مشاهد (Events) بالترتيب الزمني الدقيق للبند أعلاه. كل حدث يجب أن يحتوي على:
- event_id: (مثل event_01, event_02, ...)
- title: (عنوان معبر مستوحى من بند التسلسل الزمني المقابل)
- narrative: (السرد القصصي المفصل الخاص بهذا البند فقط بحجم 300-500 كلمة)
- ayah_refs: (الآيات القرآنية الداعمة لهذا البند بصيغة "سورة:آية")

أعد النتيجة بصيغة JSON فقط:
{{
  "events": [
    {{
      "event_id": "event_01",
      "title": "عنوان الحدث الأول",
      "narrative": "السرد القصصي المفصل...",
      "ayah_refs": ["2:30"]
    }}
  ]
}}"""

    prompt = prompt_template.format(
        title=title,
        core_idea=core_idea,
        style=style,
        timeline_text=timeline_text,
        rules_text=rules_text,
        strict_instruction=strict_instruction
    )

    result = call_llm(prompt, is_json=True, timeout=300)
    if result and "events" in result:
        return result
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
    print(f"📚 جمع المصادر: {story_id}")
    print("=" * 60)

    # قراءة المكتبة
    if not STORIES_JSON.exists():
        print(f"❌ ملف المكتبة غير موجود: {STORIES_JSON}")
        sys.exit(1)
        
    library = json.loads(STORIES_JSON.read_text(encoding="utf-8"))
    story = next((s for s in library["stories"] if s["id"] == story_id), None)
    
    if not story:
        print(f"❌ القصة '{story_id}' غير موجودة في stories.json")
        sys.exit(1)

    print(f"📖 {story.get('title')} | العهد: {story.get('era')}")

    collected_dir = STORIES_OUTPUT / story_id / "collected"
    collected_dir.mkdir(parents=True, exist_ok=True)
    events_dir = collected_dir / "events"
    events_dir.mkdir(exist_ok=True)

    # 1. بناء السرد
    print("\n🧠 جاري بناء السرد القصصي (Deep Storytelling)...")
    story_data = build_narrative(story)
    events = story_data.get("events", [])

    if not events:
        print("❌ فشل الذكاء الاصطناعي في بناء السرد.")
        sys.exit(1)

    print(f"✅ تم بناء السرد: {len(events)} حدث.")

    # 2. معالجة كل حدث وجلب الآيات
    total_ayahs = 0
    for ev in events:
        ev_id = ev.get("event_id", "event_01")
        ev_title = ev.get("title", "")
        ev_narrative_text = ev.get("narrative", "")
        refs = ev.get("ayah_refs", [])
        
        print(f"\n📦 {ev_id}: {ev_title}")
        print(f"   📝 سرد: {len(ev_narrative_text.split())} كلمة | 📖 مراجع مقترحة: {len(refs)}")
        
        ev_ayahs = []
        ev_narrative = [{"source": "سرد تاريخي موثق", "text": ev_narrative_text}]
        
        for ref in refs:
            try:
                s, a = ref.split(":")
                ayah_data = fetch_ayah_from_api(int(s), int(a))
                
                if ayah_data and ayah_data["text"]:
                    ev_ayahs.append({
                        "ref": ref,
                        "surah": int(s),
                        "ayah": int(a),
                        "surah_name": f"سورة {s}", 
                        "text": ayah_data["text"]
                    })
                    if ayah_data.get("tafsir"):
                        ev_narrative.append({
                            "source": f"التفسير الميسر - {ref}",
                            "text": ayah_data["tafsir"],
                            "related_ayah": ref
                        })
                time.sleep(0.2)
            except Exception as e:
                print(f"   ⚠️ تخطي المرجع {ref}: {e}")

        # حفظ
        if ev_narrative:
            ev_path = events_dir / ev_id
            ev_path.mkdir(exist_ok=True)
            (ev_path / "ayahs.json").write_text(json.dumps(ev_ayahs, ensure_ascii=False, indent=2), encoding="utf-8")
            (ev_path / "narrative.json").write_text(json.dumps(ev_narrative, ensure_ascii=False, indent=2), encoding="utf-8")
            (ev_path / "meta.json").write_text(json.dumps({"event_id": ev_id, "title": ev_title}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"   ✅ تم الحفظ: {len(ev_ayahs)} آية، {len(ev_narrative)} سرد.")
            total_ayahs += len(ev_ayahs)

    print("\n" + "=" * 60)
    print(f"🎉 اكتمل التجميع: {len(events)} حدث، {total_ayahs} آية.")
    print("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ خطأ فادح: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
