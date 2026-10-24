import os, json, requests, sys, time, random, traceback
from pathlib import Path
from gemini_tts_pool import get_pool

STATE_FILE = "state/story_state.json"
OUTPUT_DIR = Path("data/stories/generated")
PENDING_DIR = Path("state/pending")
MODELS_CACHE_FILE = Path("state/models_cache.json")
STORIES_ORDER = ["مريم", "زكريا", "يحيى", "عيسى"]
MAX_EPISODES_PER_STORY = 40

OUTRO_TEXTS = [
    {"text": "قال رسول الله ﷺ: «مَن دَلَّ على خيرٍ فله مثلُ أجرِ فاعِلِه». كن أنت الدال على الخير، شارك هذه الحلقة مع من تحب، فلك مثل أجره. ملاك المعرفة.", "voice_style": "(in a gentle tone)"},
    {"text": "قال ﷺ: «مَن دعا إلى هُدًى كان له من الأجرِ مثلُ أجورِ مَن تبِعَه». كل شخص يهتدي بهذه الكلمة، لك مثل أجره. ملاك المعرفة.", "voice_style": "(in a warm tone)"},
]

def get_working_models():
    if Path(MODELS_CACHE_FILE).exists():
        try:
            cache = json.loads(Path(MODELS_CACHE_FILE).read_text(encoding="utf-8"))
            models = [m for m in cache.get("models", []) if "2.5" not in m]
            if models: return models
        except: pass
    return ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-1.5-flash"]

def call_llm(prompt, is_json=True, timeout=120, max_tokens=8192):
    pool = get_pool()
    for model in get_working_models()[:3]:
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens}}
                if is_json: body["generationConfig"]["responseMimeType"] = "application/json"
                
                r = requests.post(url, json=body, timeout=timeout)
                if r.status_code == 200:
                    parts = r.json().get('candidates', [{}])[0].get('content', {}).get('parts', [])
                    if not parts: break
                    text = parts[0].get('text', '')
                    if not text: break
                    if is_json: text = text[text.find('{'):text.rfind('}')+1]
                    pool._advance() # تدوير المفتاح
                    return json.loads(text) if is_json else text
                elif r.status_code == 429:
                    pool.mark_failed(3600)
                elif r.status_code in [404, 503]:
                    break
            except: pass
    return None

def get_surah_name(surah_num):
    try:
        r = requests.get(f"https://api.alquran.cloud/v1/surah/{surah_num}", timeout=10)
        if r.status_code == 200:
            return r.json()['data']['name'].replace("سُورَةُ ", "")
    except: pass
    return f"سورة {surah_num}"

def load_state():
    default = {"story_index": 0, "episode_num": 1}
    if Path(STATE_FILE).exists():
        try:
            saved = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
            for k, v in default.items(): saved.setdefault(k, v)
            return saved
        except: pass
    return default

def main():
    state = load_state()
    story_name = STORIES_ORDER[state["story_index"]]
    ep_num = state["episode_num"]
    
    print(f"🚀 بدء: {story_name} - الحلقة {ep_num}", flush=True)
    pending_file = PENDING_DIR / f"pending-{story_name}-{ep_num}.json"
    plan = None
    
    if pending_file.exists():
        try:
            loaded = json.loads(pending_file.read_text(encoding="utf-8"))
            if "title" in loaded and "summary" in loaded and len(loaded.get("summary", "")) > 50:
                plan = loaded
        except: pass

    if not plan:
        print("📝 توليد خطة الحلقة...", flush=True)
        plan_prompt = f"أنت كاتب سيناريو. ابتكر فكرة للحلقة {ep_num} من {story_name}. أعد JSON: {{\"title\": \"...\", \"summary\": \"ملخص مفصل (300 كلمة)...\"}}"
        plan = call_llm(plan_prompt)
        if not plan: sys.exit(1)
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        pending_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    print(f"📜 العنوان: {plan['title']}", flush=True)

    print("✍️ توليد السرد الطويل...", flush=True)
    script_prompt = f"أنت كاتب سيناريو. اكتب نصاً سردياً (1500 كلمة) لهذه الحلقة:\n{plan['summary']}\nاكتب النص فقط بدون رموز."
    script_text = call_llm(script_prompt, is_json=False)
    if not script_text: sys.exit(1)

    print("🛠️ بناء المقاطع والآيات...", flush=True)
    ayah_prompt = f"استخرج 5 آيات تدعم النص:\n{script_text[:2000]}\nأعد JSON: {{\"ayahs\": [{{\"surah\": 3, \"ayah\": 35}}]}}"
    ayah_res = call_llm(ayah_prompt)
    ayahs = ayah_res.get("ayahs", []) if ayah_res else []

    verified = []
    paragraphs = [p.strip() for p in script_text.split('\n') if p.strip()]

    for i, p in enumerate(paragraphs):
        verified.append({"type": "narration", "text": p})
        if i < len(ayahs):
            s_num = ayahs[i]["surah"]
            a_num = ayahs[i]["ayah"]
            s_name = get_surah_name(s_num)
            verified.append({
                "type": "quran", "surah": s_num, "surah_name": s_name, 
                "ayah_numbers": [a_num], "display": f"{s_name}: {a_num}"
            })

    outro = random.choice(OUTRO_TEXTS)
    verified.append({"type": "outro", "text": outro["text"], "emotion_hint": outro["voice_style"], "visual": "mosque", "sfx": ""})
    
    next_title = f"الحلقة {ep_num + 1}"
    verified.append({"type": "teaser", "text": f"الحلقة القادمة: {next_title}.", "emotion_hint": "(mysterious)", "next_episode_title": next_title, "next_episode_source": story_name, "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{story_name}-{ep_num:02d}.json"
    out_file.write_text(json.dumps({"episode_number": ep_num, "title": f"{story_name} - {plan['title']}", "segments": verified}, ensure_ascii=False, indent=2), encoding="utf-8")
    
    Path("out").mkdir(exist_ok=True)
    Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")
    state["episode_num"] += 1
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    if pending_file.exists(): pending_file.unlink()
    print(f"✅ نجح: {out_file}", flush=True)

if __name__ == "__main__":
    try: main()
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)
