import os, json, requests, sys, time, random, traceback
from pathlib import Path
from gemini_tts_pool import get_pool

STATE_FILE = "state/story_state.json"
OUTPUT_DIR = Path("data/stories/generated")
PENDING_DIR = Path("state/pending")
MODELS_CACHE_FILE = "state/models_cache.json"

STORIES_ORDER = ["مريم", "زكريا", "يحيى", "عيسى"]
MAX_EPISODES_PER_STORY = 40
CACHE_TTL = 86400

OUTRO_TEXTS = [
    {"text": "قال رسول الله ﷺ: «مَن دَلَّ على خيرٍ فله مثلُ أجرِ فاعِلِه». كن أنت الدال على الخير، شارك هذه الحلقة مع من تحب، فلك مثل أجره. ملاك المعرفة... كن دالاً على الخير.", "voice_style": "(in a gentle, encouraging tone)"},
    {"text": "قال ﷺ: «مَن دعا إلى هُدًى كان له من الأجرِ مثلُ أجورِ مَن تبِعَه». كل شخص يهتدي بهذه الكلمة، لك مثل أجره. انشر العلم، وشارك القناة. ملاك المعرفة.", "voice_style": "(in a warm, inviting tone)"},
    {"text": "قال ﷺ لعلي رضي الله عنه: «لأَنْ يَهدِيَ اللهُ بك رجُلًا واحدًا خيرٌ لك من حُمْرِ النَّعَم». لعل كلمة واحدة منك تكون سبباً في هداية إنسان. شارك الحلقة. ملاك المعرفة.", "voice_style": "(in a hopeful, inspiring tone)"},
    {"text": "قال ﷺ: «إذا مات ابنُ آدمَ انقطع عملُه إلا من ثلاثٍ: صدقةٍ جاريةٍ، أو علمٍ يُنتفَعُ به». اجعل لك علماً جارياً، شارك هذه المعرفة لتبقى صدقة جارية لك. ملاك المعرفة.", "voice_style": "(in a reflective, solemn tone)"},
    {"text": "قال ﷺ: «نَضَّرَ اللهُ امرأً سَمِعَ مِنَّا حديثًا فحَفِظَه حتى يُبَلِّغَه». كن ممن يحفظ العلم ويبلغه. انشر هذه الحلقة. ملاك المعرفة.", "voice_style": "(in a strong, encouraging tone)"},
    {"text": "قال ﷺ: «إنَّ اللهَ وملائكتَه وأهلَ السماواتِ والأرضِ... ليُصَلُّونَ على مُعَلِّمِ الناسِ الخيرَ». كن معلمًا للناس الخير، ولو بمشاركة. ملاك المعرفة.", "voice_style": "(in a reverent, uplifting tone)"},
    {"text": "قال الله تعالى: ﴿وَتَعَاوَنُوا عَلَى الْبِرِّ وَالتَّقْوَىٰ﴾. التعاون على الخير واجب. شارك الحلقة، وتعاون معنا على البر والتقوى. ملاك المعرفة.", "voice_style": "(in a clear, instructional tone)"},
    {"text": "قال الله تعالى: ﴿وَتَوَاصَوْا بِالْحَقِّ وَتَوَاصَوْا بِالصَّبْرِ﴾. من التواصي بالحق أن تنشر الخير. انشر الحلقة، وذكر بها غيرك. ملاك المعرفة.", "voice_style": "(in a calm, wise tone)"},
    {"text": "قال الإمام ابن الجوزي رحمه الله: «من أحب ألا ينقطع عمله بعد موته فلينشر العلم». انشر العلم، ليبقى أجرك بعد موتك. ملاك المعرفة.", "voice_style": "(in a deep, philosophical tone)"},
    {"text": "قال ﷺ: «بَلِّغُوا عَنِّي ولو آية». خير الكلام ما قل ودل. دلّ غيرك على الخير، وشاركنا الأجر. ملاك المعرفة.", "voice_style": "(in a direct, motivating tone)"}
]

def get_working_models():
    if Path(MODELS_CACHE_FILE).exists():
        try:
            cache = json.loads(Path(MODELS_CACHE_FILE).read_text(encoding="utf-8"))
            if time.time() - cache.get("ts", 0) < CACHE_TTL: return cache["models"]
        except: pass
    pool = get_pool()
    key = pool.get_current()
    try:
        r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=15)
        if r.status_code == 200:
            raw_models = [m["name"].replace("models/", "") for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
            valid_models = [m for m in raw_models if "gemini" in m and "2.5" not in m and "embedding" not in m]
            valid_models.sort(key=lambda x: (0 if "3.8" in x else 1 if "3.6" in x else 2 if "3.5" in x else 3))
            Path(MODELS_CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(MODELS_CACHE_FILE).write_text(json.dumps({"models": valid_models, "ts": time.time()}), encoding="utf-8")
            return valid_models
    except: pass
    return ["gemini-3.8-flash", "gemini-1.5-flash"]

def call_gemini(prompt, is_json=True, timeout=120, max_tokens=8192):
    pool = get_pool()
    models = get_working_models()
    for model in models[:2]:
        key = pool.get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens}}
        if is_json: body["generationConfig"]["responseMimeType"] = "application/json"
        try:
            print(f"      [API] محاولة {model}...", flush=True)
            r = requests.post(url, json=body, timeout=timeout)
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                if is_json: text = text[text.find('{'):text.rfind('}')+1]
                return json.loads(text) if is_json else text
            if r.status_code == 429: pool.mark_failed(3600)
        except Exception as e: print(f"      [API Error] {e}", flush=True)
    return None

def call_llm(prompt, is_json=True, timeout=120, max_tokens=8192):
    res = call_gemini(prompt, is_json, timeout, max_tokens)
    if res: return res
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", 
                headers={"Authorization": f"Bearer {groq_key}"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "response_format": {"type": "json_object"} if is_json else None},
                timeout=60)
            if r.status_code == 200:
                text = r.json()['choices'][0]['message']['content']
                if is_json: text = text[text.find('{'):text.rfind('}')+1]
                return json.loads(text) if is_json else text
        except: pass
    return None

def load_state():
    default = {"story_index": 0, "episode_num": 1}
    if Path(STATE_FILE).exists():
        try:
            saved = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
            for k, v in default.items(): saved.setdefault(k, v)
            return saved
        except: pass
    return default

def save_state(state):
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def main():
    state = load_state()
    story_name = STORIES_ORDER[state["story_index"]]
    ep_num = state["episode_num"]
    
    print(f"🚀 [1/5] بدء معالجة: {story_name} - الحلقة {ep_num}", flush=True)

    pending_file = PENDING_DIR / f"pending-{story_name}-{ep_num}.json"
    plan = None
    
    # هل هناك خطة محفوظة مسبقاً في pending؟
    if pending_file.exists():
        print(f"📂 [2/5] وجدت خطة محفوظة في pending. سنستخدمها.", flush=True)
        try:
            plan = json.loads(pending_file.read_text(encoding="utf-8"))
        except: pass

    # إذا لم توجد خطة، نولدها
    if not plan:
        print("📝 [2/5] توليد خطة الحلقة الجديدة...", flush=True)
        plan_prompt = f"""أنت كاتب سيناريو إسلامي.
المطلوب: ابتكر فكرة للحلقة رقم {ep_num} من قصة {story_name}.
اعطني عنواناً درامياً جذاباً، وملخصاً موسعاً (لا يقل عن 300 كلمة).
أعد النتيجة بصيغة JSON:
{{"title": "عنوان الحلقة", "summary": "ملخص مفصل جداً..."}}"""
        
        plan = call_llm(plan_prompt)
        if not plan or "title" not in plan:
            print("❌ فشل توليد خطة الحلقة.", flush=True)
            sys.exit(1)
        
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        pending_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 تم حفظ الخطة في pending: {plan['title']}", flush=True)

    print(f"📜 العنوان: {plan['title']}", flush=True)

    # توليد السرد
    print("✍️ [3/5] توليد السرد الطويل...", flush=True)
    script_prompt = f"""أنت كاتب سيناريو إسلامي.
اكتب نصاً سردياً درامياً مفصلاً (1500 كلمة) لهذه الحلقة.
القصة: {story_name}
العنوان: {plan['title']}
الملخص: {plan['summary']}

اكتب النص كاملاً. لا تختصر. لا تضع أي رموز أو أقواس.
أعد النص فقط."""
    
    script_text = call_llm(script_prompt, is_json=False, timeout=120, max_tokens=8192)
    if not script_text:
        print("❌ فشل توليد السرد (النص فارغ).", flush=True)
        sys.exit(1)

    print(f"📊 عدد كلمات السرد: {len(script_text.split())}", flush=True)

    # بناء المقاطع والآيات
    print("🛠️ [4/5] بناء المقاطع والتحقق من الآيات...", flush=True)
    ayah_prompt = f"""استخرج 5 آيات قرآنية تدعم هذا النص:
{script_text[:2000]}
أعد JSON: {{"ayahs": [{{"surah": 3, "ayah": 35}}]}}"""
    
    ayah_res = call_llm(ayah_prompt)
    ayahs = ayah_res.get("ayahs", []) if ayah_res else []

    verified = []
    paragraphs = [p.strip() for p in script_text.split('\n') if p.strip()]

    for i, p in enumerate(paragraphs):
        verified.append({"type": "narration", "text": p})
        if i < len(ayahs):
            verified.append({"type": "quran", "surah": ayahs[i]["surah"], "ayah_numbers": [ayahs[i]["ayah"]]})

    # إضافة Outro و Teaser
    outro = random.choice(OUTRO_TEXTS)
    verified.append({"type": "outro", "text": outro["text"], "emotion_hint": outro["voice_style"], "visual": "mosque", "sfx": ""})
    
    next_title = f"الحلقة {ep_num + 1}" if ep_num < MAX_EPISODES_PER_STORY else "الحلقة القادمة"
    verified.append({"type": "teaser", "text": f"وهل تعلم ما حدث بعد ذلك؟ الحلقة القادمة: {next_title}.", "emotion_hint": "(mysterious)", "next_episode_title": f"{story_name} - {next_title}", "next_episode_source": story_name, "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"})

    # الحفظ النهائي
    print("💾 [5/5] حفظ الملف النهائي...", flush=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{story_name}-{ep_num:02d}.json"
    out_file.write_text(json.dumps({"episode_number": ep_num, "title": f"{story_name} - {plan['title']} - الحلقة {ep_num}", "segments": verified}, ensure_ascii=False, indent=2), encoding="utf-8")
    
    Path("out").mkdir(exist_ok=True)
    Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")
    
    state["episode_num"] += 1
    save_state(state)

    if pending_file.exists():
        pending_file.unlink()

    print(f"✅ نجح: {out_file}", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ خطأ فادح: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
