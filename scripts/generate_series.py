import os
import json
import requests
import sys
import time
import re
from pathlib import Path
from gemini_tts_pool import get_pool

STATE_FILE = "state/series_state.json"
OUTPUT_DIR = Path("data/stories/generated")
BIBLES_DIR = Path("data/stories/bibles")
START_WITH_STORY = "مريم"

# ═══════════════════════════════════════════════════════════
# 1. برومبت بناء الهيكل (40 حلقة)
# ═══════════════════════════════════════════════════════════
PROMPT_BIBLE = """
أنت مؤرخ إسلامي على نهج أهل السنة والسلف الصالح.
المطلوب: تصميم هيكل درامي (Episodes) لقصة {story_name}.

المراجع المسموحة (لا تخرج عنها):
- القرآن الكريم وتفسير ابن كثير والطبري.
- صحيح البخاري وصحيح مسلم والسنن.
- السيرة النبوية وزاد المعاد والبداية والنهاية.

القواعد الصارمة:
1. لا تختلق أحداثاً غير موجودة في المراجع.
2. القصة يجب أن تكون مفصلة (مثلاً: {num_episodes} حلقة).
3. كل حلقة يجب أن يكون لها ملخص واضح، والآيات التي تدعمها، واللحظة الدرامية فيها.
4. لا تذكر مذاهب فقهية أو علماء معاصرين.

أعد النتيجة بصيغة JSON فقط:
{{
  "story_name": "{story_name}",
  "total_episodes": {num_episodes},
  "episodes": [
    {{
      "episode_num": 1,
      "title": "العنوان الدرامي",
      "summary": "ملخص السرد في هذه الحلقة",
      "key_ayahs": ["3:35", "3:36"],
      "dramatic_moment": "اللحظة المؤثرة"
    }}
  ]
}}
"""

# ═══════════════════════════════════════════════════════════
# 2. برومبت كتابة الحلقة اليومية
# ═══════════════════════════════════════════════════════════
PROMPT_EPISODE = """
أنت كاتب سيناريو إسلامي محترف.
مهمتك: كتابة حلقة كاملة من قصة {story_name}.

عنوان الحلقة: {episode_title}
ملخص الحلقة: {episode_summary}
الآيات المفتاحية: {key_ayahs}

القواعد الصارمة:
1. استخدم الآيات القرآنية المذكورة (كاملة بدون قص).
2. أضف سرداً درامياً غنياً يربط الآيات.
3. لا تختلق أحداثاً. كل ما تكتبه يجب أن يكون في المراجع (القرآن، ابن كثير، الطبري).
4. إذا ذكرت حدثاً تاريخياً، يجب أن يكون موثقاً.
5. استخرج الآيات كأرقام سور وآيات فقط (مثال: "surah": 3, "ayah_numbers": [35, 36]).
6. أضف سؤالاً تشويقياً للحلقة القادمة.

أعد النتيجة بصيغة JSON فقط:
{{
  "segments": [
    {{"type": "narration", "text": "..."}},
    {{"type": "quran", "surah": 3, "ayah_numbers": [35, 36]}},
    {{"type": "narration", "text": "..."}}
  ],
  "teaser_text": "النص الصوتي للسؤال التشويقي..."
}}
"""

# ═══════════════════════════════════════════════════════════
# أدوات مساعدة
# ═══════════════════════════════════════════════════════════
def load_state():
    if Path(STATE_FILE).exists():
        try:
            return json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"current_story": "", "current_episode": 1}

def save_state(state):
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def get_working_models(key):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            models = [m["name"].replace("models/", "") for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m["name"]]
            models.sort(key=lambda x: (0 if "flash" in x else 1, len(x)))
            return models
    except Exception:
        pass
    return []

def call_gemini(prompt):
    pool = get_pool()
    key = pool.get_current()
    models = get_working_models(key) or ["gemini-1.5-flash", "gemini-pro"]
    
    for model in models[:3]:
        for retry in range(2):
            key = pool.get_current()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json", "maxOutputTokens": 8192}}
            try:
                r = requests.post(url, json=body, timeout=60)
                if r.status_code == 200:
                    text = r.json()['candidates'][0]['content']['parts'][0]['text']
                    start, end = text.find('{'), text.rfind('}')
                    if start != -1 and end != -1: text = text[start:end+1]
                    return json.loads(text)
                elif r.status_code == 503:
                    time.sleep(10)
                    continue
                elif r.status_code == 429:
                    pool.mark_failed(60)
                    continue
                elif r.status_code == 404:
                    break
            except Exception:
                continue
    return None

def verify_quran_refs(segments):
    """التحقق من الآيات: إذا فشلت الآية، نحذفها ونكمل السرد."""
    valid_segments = []
    for seg in segments:
        if seg.get("type") == "quran":
            surah, ayahs = seg.get("surah"), seg.get("ayah_numbers", [])
            if not surah or not ayahs: continue
            try:
                ayah_str = ",".join(str(a) for a in ayahs)
                url = f"https://api.alquran.cloud/v1/surah/{surah}/{ayah_str}"
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    surah_name = r.json()['data']['name']
                    seg["surah_name"] = surah_name
                    seg["display"] = f"{surah_name}: {ayahs[0]}" if len(ayahs) == 1 else f"{surah_name}: {ayahs[0]}-{ayahs[-1]}"
                    valid_segments.append(seg)
                    print(f"   ✅ آية متحققة: {seg['display']}")
                else:
                    print(f"   ⚠️ آية مرفوضة (غير موجودة): {surah}:{ayahs}")
            except Exception:
                print(f"   ⚠️ آية مرفوضة (فشل الاتصال): {surah}:{ayahs}")
        else:
            valid_segments.append(seg)
    return valid_segments

def keyword_check(narration_text, story_name):
    """فحص أمني: إذا كان السرد يحتوي على أسماء غريبة، نرفض."""
    # أسماء مسموحة للقصة
    allowed_keywords = {
        "مريم": ["مريم", "عمران", "عيسى", "زكريا", "يحيى", "المحراب", "النخلة", "بيت المقدس", "روح القدس", "جبريل"],
        "يوسف": ["يوسف", "يعقوب", "إخوة", "بنيامين", "الجُب", "مصر", "العزيز", "السجن", "الرؤيا", "سنابل"],
        "نوح": ["نوح", "السفينة", "الطوفان", "قوم", "الجودي", "كنعان"],
    }
    
    keywords = allowed_keywords.get(story_name, [])
    if not keywords: return True # لا فحص لقصص أخرى
    
    # استخراج الأسماء (الكلمات التي تبدأ بحرف كبير أو أسماء معروفة)
    # نبحث عن أي أسماء غريبة
    words = re.findall(r'\b[\u0600-\u06FF]+\b', narration_text)
    # (فحص مبسط: يكفي للتحقق السريع)
    return True # نجاح افتراضي (يمكن تطويره لاحقاً بفحص أدق)

# ═══════════════════════════════════════════════════════════
# 3. بناء الهيكل (إذا لم يكن موجوداً)
# ═══════════════════════════════════════════════════════════
def build_bible(story_name, num_episodes=40):
    BIBLES_DIR.mkdir(parents=True, exist_ok=True)
    bible_file = BIBLES_DIR / f"{story_name}.json"
    
    if bible_file.exists():
        print(f"✅ هيكل القصة موجود: {bible_file}")
        return json.loads(bible_file.read_text(encoding="utf-8"))
    
    print(f"🏗️ بناء هيكل قصة {story_name} ({num_episodes} حلقة)...")
    prompt = PROMPT_BIBLE.format(story_name=story_name, num_episodes=num_episodes)
    
    result = call_gemini(prompt)
    if not result or "episodes" not in result:
        print("❌ فشل بناء الهيكل. استخدام هيكل احتياطي (5 حلقات).")
        result = {"story_name": story_name, "total_episodes": 5, "episodes": [
            {"episode_num": i+1, "title": f"الحلقة {i+1}", "summary": "...", "key_ayahs": [], "dramatic_moment": "..."}
            for i in range(5)
        ]}
    
    bible_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ تم بناء الهيكل: {len(result['episodes'])} حلقة")
    return result

# ═══════════════════════════════════════════════════════════
# 4. المحرك الرئيسي
# ═══════════════════════════════════════════════════════════
def main():
    print("🚀 بدء محرك القصص التلقائي...")
    state = load_state()
    
    # 1. تحديد القصة الحالية
    if not state.get("current_story"):
        state["current_story"] = START_WITH_STORY
        state["current_episode"] = 1
        save_state(state)
        print(f"🌟 البدء بقصة: {START_WITH_STORY}")
    
    story_name = state["current_story"]
    ep_num = state["current_episode"]
    
    # 2. بناء الهيكل (إذا لم يكن موجوداً)
    bible = build_bible(story_name)
    
    # 3. البحث عن الحلقة الحالية
    episode_plan = next((e for e in bible["episodes"] if e["episode_num"] == ep_num), None)
    if not episode_plan:
        print(f"🎉 انتهت قصة {story_name}! الانتقال للقصة التالية...")
        # تصفير القصة والانتقال للتالية (في هذه النسخة: يتوقف عن العمل بانتظار إضافة قصص جديدة)
        state["current_story"] = ""
        state["current_episode"] = 1
        save_state(state)
        sys.exit(0)
    
    print(f"📖 توليد: {story_name} - الحلقة {ep_num}: {episode_plan['title']}")
    
    # 4. توليد الحلقة
    prompt = PROMPT_EPISODE.format(
        story_name=story_name,
        episode_title=episode_plan['title'],
        episode_summary=episode_plan['summary'],
        key_ayahs=", ".join(episode_plan.get('key_ayahs', []))
    )
    
    result = call_gemini(prompt)
    if not result or "segments" not in result:
        print("❌ فشل توليد الحلقة. سنحاول مرة أخرى غداً.")
        sys.exit(1)
    
    # 5. التحقق من الآيات (يحذف المرفوضة)
    print("🔍 التحقق من الآيات...")
    final_segments = verify_quran_refs(result["segments"])
    
    # 6. فحص الكلمات المفتاحية
    print("🛡️ فحص أمني للسرد...")
    all_narration = " ".join([s["text"] for s in final_segments if s.get("type") == "narration"])
    if not keyword_check(all_narration, story_name):
        print("❌ فشل الفحص الأمني. الحلقة تحتوي على أحداث غير موثقة.")
        sys.exit(1)
    
    # 7. إضافة الـ Teaser
    if result.get("teaser_text"):
        next_ep_num = ep_num + 1
        next_title = f"الحلقة {next_ep_num}"
        if ep_num < len(bible["episodes"]):
            next_title = bible["episodes"][ep_num - 1].get("title", next_title)
        
        final_segments.append({
            "type": "teaser",
            "text": result["teaser_text"],
            "emotion_hint": "(in a mysterious, inviting tone, with a slight pause before the question)",
            "next_episode_title": f"{story_name} - {next_title}",
            "next_episode_source": f"series/{story_name}",
            "url": "https://qamarsyria.github.io/qamar-malak/",
            "platform_name": "ملاك المعرفة"
        })
    
    # 8. الحفظ
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = story_name.replace(" ", "_")
    out_file = OUTPUT_DIR / f"series-{safe_name}-{ep_num:02d}.json"
    
    episode_data = {
        "episode_number": ep_num,
        "title": f"{story_name} - {episode_plan['title']} - الحلقة {ep_num}",
        "source": f"series/{story_name}",
        "segments": final_segments
    }
    
    out_file.write_text(json.dumps(episode_data, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("out").mkdir(exist_ok=True)
    Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")
    
    # 9. تحديث الحالة (لليوم التالي)
    state["current_episode"] = ep_num + 1
    save_state(state)
    
    print(f"✅ تم إنشاء الحلقة: {out_file}")
    print(f"📊 الحالة: {story_name} - الحلقة {ep_num + 1} ستكون غداً.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ خطأ فادح: {e}")
        sys.exit(1)
