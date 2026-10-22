import os
import json
import requests
import sys
from pathlib import Path
from gemini_tts_pool import get_pool

STATE_FILE = "state/story_state.json"
SOURCES_FILE = "data/auto-generated/sources.json"
OUTPUT_DIR = Path("data/stories/generated")

PROMPT_TEMPLATE = """
أنت كاتب سيناريو إسلامي محترف على منهج أهل السنة والجماعة.
مهمتك: تحويل هذا النص إلى سرد درامي غني ومؤثر لحلقة صوتية.

الموضوع: {title}
الوصف المختصر: {brief}
المحتوى الأصلي:
{sections}

قواعد صارمة (لا تكسرها أبداً):
1. لا تذكر مذاهب فقهية، ولا فرقاً، ولا علماء معاصرين.
2. أضف إثراءً أدبياً ووصفاً للمشاعر والأحداث (دون اختلاق أحداث تاريخية غير موجودة).
3. استخرج الآيات القرآنية التي تدعم النص، وأعدها كأرقام سور وآيات فقط (مثال: "surah": 2, "ayah_numbers": [30]). لا تكتب النص القرآني بنفسك.
4. استخرج الأحاديث الصحيحة التي تدعم النص، وأعدها كنصوص حرفية.
5. صغ سؤالاً تشويقياً في النهاية للحلقة القادمة يثير فضول المستمع.

أعد النتيجة بصيغة JSON فقط (بدون أي نصوص إضافية أو علامات markdown):
{{
  "episode_title": "عنوان الحلقة",
  "segments": [
    {{"type": "narration", "text": "النص السردي الأول..."}},
    {{"type": "quran", "surah": 2, "ayah_numbers": [30]}},
    {{"type": "narration", "text": "النص السردي الثاني..."}}
  ],
  "teaser_text": "النص الصوتي للسؤال التشويقي للحلقة القادمة..."
}}
"""

def load_state():
    if Path(STATE_FILE).exists():
        return json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
    return {"last_processed_index": -1}

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

def call_gemini(prompt):
    pool = get_pool()
    models = ["gemini-1.5-flash", "gemini-1.5-flash-latest", "gemini-2.0-flash"]

    for model in models:
        key = pool.get_current()
        print(f"🔄 محاولة باستخدام {model}...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.4, 
                "responseMimeType": "application/json",
                "maxOutputTokens": 2500
            }
        }
        try:
            r = requests.post(url, json=body, timeout=30)
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                text = text.replace("```json", "").replace("```", "").strip()
                return json.loads(text)
            else:
                print(f"⚠️ فشل {model}: HTTP {r.status_code}")
                if r.status_code == 429:
                    pool.mark_daily_exhausted()
        except Exception as e:
            print(f"⚠️ انتهى وقت الاتصال بـ {model}: {e}")
    return None

def main():
    state = load_state()
    topics = flatten_sources()
    
    next_idx = state["last_processed_index"] + 1
    if next_idx >= len(topics):
        print("🎉 تم إنجاز جميع المواضيع الـ 300!")
        return
        
    topic = topics[next_idx]
    next_topic = topics[next_idx + 1] if next_idx + 1 < len(topics) else None
    
    print(f"📖 بدء توليد الحلقة: {topic['title']} ({topic['source_id']})")
    
    sections_text = "\n".join([f"{s.get('h','')}: {s.get('p','')}" for s in topic['sections']])
    if len(sections_text) > 4000:
        sections_text = sections_text[:4000] + "..."

    prompt = PROMPT_TEMPLATE.format(
        title=topic['title'],
        brief=topic.get('brief', ''),
        sections=sections_text
    )
    
    print("🤖 جاري الإثراء عبر Gemini...")
    result = call_gemini(prompt)
    
    if not result or "segments" not in result:
        print("❌ فشل Gemini في إنتاج النص. إيقاف العملية.")
        sys.exit(1)

    final_segments = []
    for seg in result.get("segments", []):
        if seg.get("type") == "quran":
            surah = seg.get("surah")
            ayahs = seg.get("ayah_numbers", [])
            if not surah or not ayahs: continue
            
            is_valid, surah_name = verify_quran(surah, ayahs)
            if is_valid:
                display = f"{surah_name}: {ayahs[0]}" if len(ayahs) == 1 else f"{surah_name}: {ayahs[0]}-{ayahs[-1]}"
                seg["surah_name"] = surah_name
                seg["display"] = display
                final_segments.append(seg)
            else:
                print(f"⚠️ تم تجاهل آية غير صحيحة: {surah}:{ayahs}")
        else:
            final_segments.append(seg)

    if next_topic and result.get("teaser_text"):
        teaser = {
            "type": "teaser",
            "text": result["teaser_text"],
            "emotion_hint": "(in a mysterious, inviting tone, with a slight pause before the question)",
            "next_episode_title": next_topic['title'],
            "next_episode_source": next_topic['source_id'],
            "url": "https://qamarsyria.github.io/qamar-malak/",
            "platform_name": "ملاك المعرفة"
        }
        final_segments.append(teaser)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ep_num = next_idx + 1
    out_file = OUTPUT_DIR / f"episode-{ep_num:03d}.json"
    
    episode_data = {
        "episode_number": ep_num,
        "title": result.get("episode_title", topic['title']),
        "source": topic['source_id'],
        "segments": final_segments
    }
    
    out_file.write_text(json.dumps(episode_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ تم إنشاء الحلقة: {out_file}")
    
    state["last_processed_index"] = next_idx
    save_state(state)
    print("💾 تم تحديث الحالة لليوم التالي.")

if __name__ == "__main__":
    main()
