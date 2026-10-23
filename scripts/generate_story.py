import os
import json
import requests
import sys
import time
from pathlib import Path
from gemini_tts_pool import get_pool

STATE_FILE = "state/story_state.json"
SOURCES_FILE = "data/auto-generated/sources.json"
OUTPUT_DIR = Path("data/stories/generated")
START_WITH_STORY = "مريم"

PROMPT_TEMPLATE = """
أنت كاتب سيناريو إسلامي محترف على منهج أهل السنة والجماعة.
مهمتك: تحويل هذا النص إلى سرد درامي غني ومؤثر لحلقة صوتية.

الموضوع: {title}
الوصف المختصر: {brief}
المحتوى الأصلي:
{sections}

قواعد صارمة (لا تكسرها أبداً):
1. لا تذكر مذاهب فقهية، ولا فرقاً، ولا علماء معاصرين.
2. أضف إثراءً أدبياً ووصفاً للمشاعر والأحداث.
3. استخرج الآيات القرآنية التي تدعم النص، وأعدها كأرقام سور وآيات فقط (مثال: "surah": 2, "ayah_numbers": [30]).
4. استخرج الأحاديث الصحيحة التي تدعم النص.
5. صغ سؤالاً تشويقياً في النهاية.

أعد النتيجة بصيغة JSON فقط (بدون أي نصوص إضافية أو علامات markdown):
{{
  "segments": [
    {{"type": "narration", "text": "النص السردي الأول..."}},
    {{"type": "quran", "surah": 2, "ayah_numbers": [30]}},
    {{"type": "narration", "text": "النص السردي الثاني..."}}
  ],
  "teaser_text": "النص الصوتي للسؤال التشويقي..."
}}
"""

def load_state():
    default_state = {"last_processed_index": -1, "current_story_id": "", "story_episode_num": 0, "started_maryam": False}
    if Path(STATE_FILE).exists():
        try:
            saved = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
            for k, v in default_state.items():
                if k not in saved: saved[k] = v
            return saved
        except Exception:
            return default_state
    return default_state

def save_state(state):
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def flatten_sources():
    data = json.loads(Path(SOURCES_FILE).read_text(encoding="utf-8"))
    topics = []
    for key, items in data.items():
        if key == 'meta' or key == 'women': continue
        if isinstance(items, list):
            for item in items: topics.append(item)
    for sec_key, sec_data in data.get('women', {}).get('sections', {}).items():
        for item in sec_data.get('items', []):
            topics.append(item)
    return topics

def verify_quran(surah, ayahs):
    try:
        ayah_str = ",".join(str(a) for a in ayahs)
        url = f"https://api.alquran.cloud/v1/surah/{surah}/{ayah_str}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return True, r.json()['data']['name']
    except Exception:
        pass
    return False, ""

def get_working_models(key):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            models = [m["name"].replace("models/", "") for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m["name"]]
            models.sort(key=lambda x: (0 if "flash" in x else 1, len(x)))
            return models
    except Exception as e:
        print(f"⚠️ فشل اكتشاف الموديلات: {e}")
    return []

def call_gemini(prompt):
    pool = get_pool()
    key = pool.get_current()
    models = get_working_models(key) or ["gemini-1.5-flash", "gemini-pro"]
    print(f"🔄 الموديلات المكتشفة: {models[:3]}")

    for model in models[:4]:
        for retry in range(2):
            key = pool.get_current()
            print(f"🔄 محاولة {model} (مرة {retry + 1}/2)...")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json", "maxOutputTokens": 8192}}
            try:
                r = requests.post(url, json=body, timeout=60)
                if r.status_code == 200:
                    text = r.json()['candidates'][0]['content']['parts'][0]['text']
                    start, end = text.find('{'), text.rfind('}')
                    if start != -1 and end != -1: text = text[start:end+1]
                    return json.loads(text)
                elif r.status_code == 503:
                    print(f"   ⏳ جوجل مشغولة (503)، ننتظر 15 ثانية...")
                    time.sleep(15)
                    continue
                elif r.status_code == 429:
                    print(f"   🔑 المفتاح استنفد (429)...")
                    pool.mark_failed(60)
                    continue
                elif r.status_code == 404:
                    print(f"   ❌ الموديل غير متاح (404)")
                    break
                else:
                    print(f"   ⚠️ خطأ HTTP: {r.status_code}")
            except Exception as e:
                print(f"   ⚠️ استثناء: {e}")
                continue
    return None

def main():
    try:
        print("🚀 بدء السكربت...")
        state = load_state()
        topics = flatten_sources()
        print(f"📊 تم تحميل {len(topics)} موضوعاً.")
        
        # 🌟 إجبار البدء بقصة مريم
        if not state.get("started_maryam", False):
            found = False
            for i, t in enumerate(topics):
                if START_WITH_STORY in json.dumps(t, ensure_ascii=False):
                    state["last_processed_index"] = i - 1
                    state["current_story_id"] = "" 
                    state["story_episode_num"] = 0
                    state["started_maryam"] = True
                    print(f"🌟 العثور على قصة {START_WITH_STORY}. سنبدأ منها.")
                    found = True
                    break
            if not found:
                print(f"⚠️ لم يتم العثور على مريم، البدء من الأول.")
                state["last_processed_index"] = -1

        next_idx = state["last_processed_index"] + 1
        if next_idx >= len(topics): next_idx = 0
            
        topic = topics[next_idx]
        next_topic = topics[next_idx + 1] if next_idx + 1 < len(topics) else topics[0]
        
        story_id = topic['source_id']
        if state.get("current_story_id") != story_id:
            state["current_story_id"] = story_id
            state["story_episode_num"] = 1
        else:
            state["story_episode_num"] = state.get("story_episode_num", 0) + 1
        ep_num = state["story_episode_num"]

        final_title = f"{topic['title']} - الحلقة {ep_num}"
        print(f"📖 بدء توليد: {final_title}")
        
        sections_text = "\n".join([f"{s.get('h','')}: {s.get('p','')}" for s in topic['sections']])[:3000]
        prompt = PROMPT_TEMPLATE.format(title=topic['title'], brief=topic.get('brief', ''), sections=sections_text)
        
        print("🤖 الاتصال بـ Gemini...")
        result = call_gemini(prompt)
        
        if not result or "segments" not in result:
            print("❌ فشل Gemini في إنتاج النص. إيقاف.")
            sys.exit(1)

        final_segments = []
        for seg in result.get("segments", []):
            if seg.get("type") == "quran":
                surah, ayahs = seg.get("surah"), seg.get("ayah_numbers", [])
                if not surah or not ayahs: continue
                is_valid, surah_name = verify_quran(surah, ayahs)
                if is_valid:
                    seg["surah_name"] = surah_name
                    seg["display"] = f"{surah_name}: {ayahs[0]}" if len(ayahs) == 1 else f"{surah_name}: {ayahs[0]}-{ayahs[-1]}"
                    final_segments.append(seg)
            else:
                final_segments.append(seg)

        if next_topic and result.get("teaser_text"):
            next_name = next_topic.get('title', 'الحلقة القادمة')
            if next_topic['source_id'] != story_id: next_name = f"قصة {next_name}"
            final_segments.append({
                "type": "teaser", "text": result["teaser_text"], "emotion_hint": "(in a mysterious tone)",
                "next_episode_title": next_name, "next_episode_source": next_topic['source_id'],
                "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"
            })

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = topic.get('name', 'story').replace(' ', '_')[:20]
        out_file = OUTPUT_DIR / f"{topic['source_type']}-{safe_name}-{ep_num:02d}.json"
        
        episode_data = {"episode_number": ep_num, "title": final_title, "source": story_id, "segments": final_segments}
        out_file.write_text(json.dumps(episode_data, ensure_ascii=False, indent=2), encoding="utf-8")
        
        Path("out").mkdir(exist_ok=True)
        Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")
        
        state["last_processed_index"] = next_idx
        save_state(state)
        print("✅ تم إنشاء الحلقة وتحديث الحالة بنجاح.")
        
    except Exception as e:
        print(f"❌ خطأ فادح: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
