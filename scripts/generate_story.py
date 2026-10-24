import os, json, requests, sys, time, random
from pathlib import Path
from gemini_tts_pool import get_pool

STATE_FILE = "state/story_state.json"
BIBLES_DIR = Path("state/bibles")
OUTPUT_DIR = Path("data/stories/generated")
PENDING_DIR = Path("state/pending")
MODELS_CACHE_FILE = "state/models_cache.json"

STORIES_ORDER = ["مريم", "زكريا", "يحيى", "عيسى"]
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

def check_keys_health():
    pool = get_pool()
    for key in pool.keys[:3]:
        try:
            r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=10)
            print(f"   {'✅' if r.status_code == 200 else '❌'} مفتاح {key[:15]}... → {r.status_code}")
        except: pass

def get_working_models():
    if Path(MODELS_CACHE_FILE).exists():
        try:
            cache = json.loads(Path(MODELS_CACHE_FILE).read_text(encoding="utf-8"))
            if time.time() - cache.get("ts", 0) < CACHE_TTL:
                return cache["models"]
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

def call_gemini(prompt, is_json=True, timeout=90, max_tokens=8192):
    pool = get_pool()
    models = get_working_models()
    for model in models[:2]:
        key = pool.get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens}}
        if is_json: body["generationConfig"]["responseMimeType"] = "application/json"
        try:
            r = requests.post(url, json=body, timeout=timeout)
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                if is_json: text = text[text.find('{'):text.rfind('}')+1]
                return json.loads(text) if is_json else text
            if r.status_code == 429: pool.mark_failed(3600)
        except: pass
    return None

def call_llm(prompt, is_json=True, timeout=90, max_tokens=8192):
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

def build_bible(story_name):
    BIBLES_DIR.mkdir(parents=True, exist_ok=True)
    bible_file = BIBLES_DIR / f"{story_name}.json"
    
    # إذا كان الهيكل موجوداً، نتحقق من جودته (هل الملخصات طويلة؟)
    if bible_file.exists():
        try:
            data = json.loads(bible_file.read_text(encoding="utf-8"))
            if len(data.get("episodes", [])) >= 40 and len(data["episodes"][0].get("summary", "")) > 100:
                return data # الهيكل ممتاز، نستخدمه
            else:
                print("⚠️ الهيكل القديم ضعيف، سيتم إعادة بنائه...")
        except: pass

    print(f"🏗️ بناء هيكل غني (40 حلقة) لقصة {story_name}...")
    
    # جلب النص الأساسي
    summary = call_llm(f"أعطني ملخصاً مفصلاً (3000 كلمة) لأهم أحداث قصة {story_name} من القرآن وتفسير ابن كثير.", is_json=False, max_tokens=4000) or f"قصة {story_name}."
    
    all_episodes = []
    # بناء الهيكل على 4 دفعات (10 حلقات في كل مرة) لضمان الطول
    for batch in range(4):
        start_ep = batch * 10 + 1
        end_ep = start_ep + 9
        print(f"   📝 توليد الحلقات {start_ep} إلى {end_ep}...")
        
        prompt = f"""أنت كاتب سيناريو إسلامي.
لديك قصة {story_name}:
{summary[:3000]}

المطلوب: اكتب ملخصاً درامياً مفصلاً (لا يقل عن 200 كلمة لكل حلقة) للحلقات من {start_ep} إلى {end_ep}.
يجب أن يكون الملخص غنياً بالأحداث لكي نتمكن من كتابة سيناريو طويل منه.

أعد النتيجة بصيغة JSON فقط:
{{
  "episodes": [
    {{"ep": {start_ep}, "title": "العنوان", "summary": "ملخص مفصل جداً..."}},
    ...
  ]
}}"""
        res = call_llm(prompt, max_tokens=6000)
        if res and "episodes" in res:
            all_episodes.extend(res["episodes"])
        else:
            print(f"   ⚠️ فشلت الدفعة {batch+1}، استخدام احتياطي.")
            for i in range(start_ep, end_ep + 1):
                all_episodes.append({"ep": i, "title": f"الحلقة {i}", "summary": "استكمال الأحداث الدرامية للقصة."})
    
    result = {"story_name": story_name, "episodes": all_episodes}
    bible_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

def main():
    check_keys_health()
    
    state = load_state()
    story_name = STORIES_ORDER[state["story_index"]]
    ep_num = state["episode_num"]
    
    bible = build_bible(story_name)
    
    if ep_num > len(bible["episodes"]):
        state["story_index"] += 1
        state["episode_num"] = 1
        save_state(state)
        sys.exit(0)

    ep_plan = bible["episodes"][ep_num - 1]
    print(f"📖 توليد: {story_name} - الحلقة {ep_num}: {ep_plan['title']}")
    print(f"   📜 طول الملخص: {len(ep_plan['summary'])} حرف")

    prompt = f"""أنت كاتب سيناريو إسلامي محترف.
المطلوب: كتابة حلقة صوتية درامية مفصلة جداً (1500 كلمة على الأقل).
القصة: {story_name}
عنوان الحلقة: {ep_plan['title']}
ملخص الحلقة (اعتمد عليه كلياً): {ep_plan['summary']}

القواعد الصارمة:
1. الطول: النص السردي يجب أن يكون طويلاً جداً. لا تختصر.
2. الآيات: لا تكتب الآيات في السرد. بل ضعها كمقطع منفصل: {{"type": "quran", "surah": 3, "ayah_numbers": [35]}}.
3. لا تذكر أرقام الآيات أو السور في السرد.
4. ضع 5-10 مقاطع قرآنية على الأقل.

أعد النتيجة بصيغة JSON:
{{
  "segments": [
    {{"type": "narration", "text": "..."}},
    {{"type": "quran", "surah": 3, "ayah_numbers": [35]}}
  ]
}}"""
    
    result = call_llm(prompt, timeout=120, max_tokens=8192)
    
    # فحص الطول الآلي
    if result and "segments" in result:
        total_words = sum(len(seg.get("text", "").split()) for seg in result["segments"] if seg.get("type") == "narration")
        print(f"   📊 عدد كلمات السرد التي كتبها Gemini: {total_words}")
        
        if total_words < 800:
            print("   ⚠️ النص قصير جداً! سنرفضه ونعيد المحاولة...")
            result = None # نفرض الفشل لإعادة المحاولة أو الحفظ في pending

    if not result or "segments" not in result:
        print("❌ فشل التوليد أو النص قصير. الحفظ في pending.")
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        (PENDING_DIR / f"pending-{story_name}-{ep_num}.json").write_text(json.dumps({"title": ep_plan["title"]}), encoding="utf-8")
        sys.exit(0)

    verified = []
    for seg in result["segments"]:
        if seg.get("type") == "quran":
            try:
                r = requests.get(f"https://api.alquran.cloud/v1/surah/{seg['surah']}/{','.join(map(str, seg['ayah_numbers']))}", timeout=10)
                if r.status_code == 200:
                    seg["surah_name"] = r.json()['data']['name']
                    seg["display"] = f"{seg['surah_name']}: {seg['ayah_numbers'][0]}"
                    verified.append(seg)
            except: pass
        else: verified.append(seg)

    outro = random.choice(OUTRO_TEXTS)
    verified.append({"type": "outro", "text": outro["text"], "emotion_hint": outro["voice_style"], "visual": "mosque", "sfx": ""})

    if ep_num < len(bible["episodes"]):
        next_title = bible["episodes"][ep_num]["title"]
        verified.append({"type": "teaser", "text": f"وهل تعلم ما حدث بعد ذلك؟ الحلقة القادمة: {next_title}.", "emotion_hint": "(mysterious)", "next_episode_title": f"{story_name} - {next_title}", "next_episode_source": story_name, "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{story_name}-{ep_num:02d}.json"
    out_file.write_text(json.dumps({"episode_number": ep_num, "title": f"{story_name} - {ep_plan['title']}", "segments": verified}, ensure_ascii=False, indent=2), encoding="utf-8")
    
    Path("out").mkdir(exist_ok=True)
    Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")
    state["episode_num"] += 1
    save_state(state)
    print(f"✅ نجح: {out_file}")

if __name__ == "__main__":
    main()
