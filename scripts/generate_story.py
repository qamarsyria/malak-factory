import os, json, requests, sys, time, re
from pathlib import Path
from gemini_tts_pool import get_pool

STATE_FILE = "state/story_state.json"
BIBLES_DIR = Path("state/bibles")
OUTPUT_DIR = Path("data/stories/generated")

# قائمة القصص بالترتيب
STORIES_ORDER = ["مريم", "زكريا", "يحيى", "عيسى"]

def load_state():
    default = {"story_index": 0, "episode_num": 1}
    if Path(STATE_FILE).exists():
        try:
            saved = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
            # دمج المفاتيح القديمة مع الجديدة لتجنب أي انهيار
            for k, v in default.items():
                if k not in saved:
                    saved[k] = v
            return saved
        except:
            return default
    return default

def save_state(state):
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def call_gemini(prompt, is_json=True):
    pool = get_pool()
    models = ["gemini-1.5-flash", "gemini-1.5-flash-latest", "gemini-pro"]
    for model in models:
        key = pool.get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        config = {"temperature": 0.3, "maxOutputTokens": 8192}
        if is_json: config["responseMimeType"] = "application/json"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
        try:
            r = requests.post(url, json=body, timeout=90)
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                if is_json:
                    text = text[text.find('{'):text.rfind('}')+1]
                    return json.loads(text)
                return text
            elif r.status_code == 429: 
                pool.mark_failed(60)
                continue
            elif r.status_code in [503, 404]: 
                time.sleep(5)
                continue
        except Exception as e:
            print(f"⚠️ خطأ في الاتصال: {e}")
            continue
    return None

def fetch_full_story_from_ibn_kathir(story_name):
    print(f"📚 جلب المصادر الكاملة لقصة {story_name}...")
    prompt = f"اكتب النص التاريخي الكامل والموثق لقصة {story_name} من كتاب قصص الأنبياء لابن كثير، بالتفصيل الممل وبأمانة تامة، دون أي إضافات من عندك."
    text = call_gemini(prompt, is_json=False)
    return text or f"قصة {story_name} من المصادر الموثوقة."

def build_bible(story_name):
    BIBLES_DIR.mkdir(parents=True, exist_ok=True)
    bible_file = BIBLES_DIR / f"{story_name}.json"
    if bible_file.exists():
        return json.loads(bible_file.read_text(encoding="utf-8"))

    print(f"🏗️ بناء هيكل 40 حلقة لقصة {story_name}...")
    full_text = fetch_full_story_from_ibn_kathir(story_name)
    
    prompt = f"""
    أنت كاتب سيناريو إسلامي. لديك النص التالي عن {story_name}:
    {full_text[:10000]}
    
    قسّم هذه القصة إلى 40 حلقة متتالية (Episodes). 
    كل حلقة يجب أن تحتوي على:
    - عنوان جذاب.
    - ملخص درامي لما سيحدث.
    
    أعد النتيجة بصيغة JSON:
    {{
      "story_name": "{story_name}",
      "episodes": [ {{"ep": 1, "title": "العنوان", "summary": "الملخص"}}, ... ]
    }}
    """
    result = call_gemini(prompt)
    if not result or "episodes" not in result:
        print("⚠️ استخدام هيكل احتياطي.")
        result = {"story_name": story_name, "episodes": [{"ep": i, "title": f"الحلقة {i}", "summary": "..."} for i in range(1, 41)]}
    
    bible_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

def main():
    state = load_state()
    story_name = STORIES_ORDER[state["story_index"]]
    ep_num = state["episode_num"]
    
    # 1. بناء الهيكل
    bible = build_bible(story_name)
    
    # 2. التحقق من انتهاء القصة
    if ep_num > len(bible["episodes"]):
        print(f"🎉 انتهت قصة {story_name}. الانتقال للقصة التالية.")
        state["story_index"] += 1
        state["episode_num"] = 1
        if state["story_index"] >= len(STORIES_ORDER):
            print("✅ تم إنجاز جميع القصص!")
            sys.exit(0)
        save_state(state)
        sys.exit(0) # يتوقف اليوم، ويبدأ القصة الجديدة غداً

    # 3. توليد الحلقة
    ep_plan = bible["episodes"][ep_num - 1]
    print(f"📖 توليد: {story_name} - الحلقة {ep_num}: {ep_plan['title']}")
    
    prompt = f"""
    أنت كاتب سيناريو إسلامي. اكتب حلقة صوتية عن {story_name}.
    عنوان الحلقة: {ep_plan['title']}
    ملخص الحلقة: {ep_plan['summary']}
    
    القواعد:
    1. اكتب سرداً درامياً غنياً (حوالي 1500 كلمة).
    2. استخرج الآيات القرآنية المرتبطة (كأرقام سور وآيات فقط).
    3. لا تختلق أحداثاً تاريخية.
    4. أضف سؤالاً تشويقياً للحلقة القادمة.
    
    أعد النتيجة بصيغة JSON:
    {{
      "segments": [ {{"type": "narration", "text": "..."}}, {{"type": "quran", "surah": 3, "ayah_numbers": [35]}} ],
      "teaser_text": "..."
    }}
    """
    result = call_gemini(prompt)
    if not result or "segments" not in result:
        print("❌ فشل توليد الحلقة. سنحاول غداً.")
        sys.exit(1)

    # 4. التحقق من الآيات
    final_segments = []
    for seg in result["segments"]:
        if seg.get("type") == "quran":
            try:
                url = f"https://api.alquran.cloud/v1/surah/{seg['surah']}/{','.join(map(str, seg['ayah_numbers']))}"
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    surah_name = r.json()['data']['name']
                    seg["surah_name"] = surah_name
                    seg["display"] = f"{surah_name}: {seg['ayah_numbers'][0]}"
                    final_segments.append(seg)
            except: pass
        else:
            final_segments.append(seg)

    # 5. إضافة Teaser
    if result.get("teaser_text"):
        next_title = bible["episodes"][ep_num]["title"] if ep_num < len(bible["episodes"]) else "الحلقة القادمة"
        final_segments.append({
            "type": "teaser", "text": result["teaser_text"], "emotion_hint": "(mysterious tone)",
            "next_episode_title": f"{story_name} - {next_title}", "next_episode_source": story_name,
            "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"
        })

    # 6. الحفظ
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_story_name = story_name.replace(" ", "_")
    out_file = OUTPUT_DIR / f"{safe_story_name}-{ep_num:02d}.json"
    
    episode_data = {
        "episode_number": ep_num,
        "title": f"{story_name} - {ep_plan['title']} - الحلقة {ep_num}",
        "segments": final_segments
    }
    out_file.write_text(json.dumps(episode_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    Path("out").mkdir(exist_ok=True)
    Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")
    
    state["episode_num"] += 1
    save_state(state)
    print("✅ تم إنشاء الحلقة بنجاح.")

if __name__ == "__main__":
    main()
