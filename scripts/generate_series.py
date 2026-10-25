import os, json, requests, sys, time, random, traceback, re
from pathlib import Path
from gemini_tts_pool import get_pool

STATE_FILE = "state/story_state.json"
OUTPUT_DIR = Path("data/stories/generated")
PENDING_DIR = Path("state/pending")
MODELS_CACHE_FILE = Path("state/models_cache.json")
STORIES_ORDER = ["مريم", "زكريا", "يحيى", "عيسى"]
MAX_EPISODES_PER_STORY = 40

OUTRO_TEXTS = [
    {"text": "قال رسول الله ﷺ: «مَن دَلَّ على خيرٍ فله مثلُ أجرِ فاعِلِه». كن أنت الدال على الخير، شارك هذه الحلقة مع من تحب، فلك مثل أجره. مَلَاك المَعْرِفَة.", "voice_style": "(in a calm, dignified tone)"},
    {"text": "قال ﷺ: «مَن دعا إلى هُدًى كان له من الأجرِ مثلُ أجورِ مَن تبِعَه». كل شخص يهتدي بهذه الكلمة، لك مثل أجره. انشر العلم. مَلَاك المَعْرِفَة.", "voice_style": "(in a warm, narrative tone)"},
    {"text": "قال ﷺ لعلي رضي الله عنه: «لأَنْ يَهدِيَ اللهُ بك رجُلًا واحدًا خيرٌ لك من حُمْرِ النَّعَم». لعل كلمة واحدة منك تكون سبباً في هداية إنسان. مَلَاك المَعْرِفَة.", "voice_style": "(in a calm, dignified tone)"},
    {"text": "قال ﷺ: «إذا مات ابنُ آدمَ انقطع عملُه إلا من ثلاثٍ: صدقةٍ جاريةٍ، أو علمٍ يُنتفَعُ به». اجعل لك علماً جارياً. مَلَاك المَعْرِفَة.", "voice_style": "(in a solemn, respectful tone)"},
    {"text": "قال ﷺ: «نَضَّرَ اللهُ امرأً سَمِعَ مِنَّا حديثًا فحَفِظَه حتى يُبَلِّغَه». كن ممن يحفظ العلم ويبلغه. مَلَاك المَعْرِفَة.", "voice_style": "(in a warm, narrative tone)"},
    {"text": "قال ﷺ: «إنَّ اللهَ وملائكتَه... ليُصَلُّونَ على مُعَلِّمِ الناسِ الخيرَ». كن معلمًا للناس الخير. مَلَاك المَعْرِفَة.", "voice_style": "(in a solemn, respectful tone)"},
    {"text": "قال الله تعالى: ﴿وَتَعَاوَنُوا عَلَى الْبِرِّ وَالتَّقْوَىٰ﴾. التعاون على الخير واجب. مَلَاك المَعْرِفَة.", "voice_style": "(in a calm, dignified tone)"},
    {"text": "قال الله تعالى: ﴿وَتَوَاصَوْا بِالْحَقِّ وَتَوَاصَوْا بِالصَّبْرِ﴾. من التواصي بالحق أن تنشر الخير. مَلَاك المَعْرِفَة.", "voice_style": "(in a gentle, reflective tone)"},
    {"text": "قال الإمام ابن الجوزي رحمه الله: «من أحب ألا ينقطع عمله بعد موته فلينشر العلم». انشر العلم. مَلَاك المَعْرِفَة.", "voice_style": "(in a gentle, reflective tone)"},
    {"text": "قال ﷺ: «بَلِّغُوا عَنِّي ولو آية». خير الكلام ما قل ودل. مَلَاك المَعْرِفَة.", "voice_style": "(in a warm, narrative tone)"}
]

def get_working_models():
    if Path(MODELS_CACHE_FILE).exists():
        try:
            cache = json.loads(Path(MODELS_CACHE_FILE).read_text(encoding="utf-8"))
            models = [m for m in cache.get("models", []) if "2.5" not in m]
            if models: return models
        except: pass
    return ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-1.5-flash"]

def call_gemini(prompt, is_json=True, timeout=120, max_tokens=8192):
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
                    try:
                        parsed = json.loads(text) if is_json else text
                        pool._advance()
                        return parsed
                    except json.JSONDecodeError:
                        break
                elif r.status_code == 429:
                    pool.mark_failed(3600)
                elif r.status_code in [404, 503]:
                    break
            except: pass
    return None

def call_huggingface(prompt, is_json=True):
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token: return None
    model_id = "mistralai/Mistral-7B-Instruct-v0.3"
    url = f"https://api-inference.huggingface.co/models/{model_id}/v1/chat/completions"
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {hf_token}", "Content-Type": "application/json"},
            json={"model": model_id, "messages": [{"role": "user", "content": prompt}], "temperature": 0.4, "max_tokens": 4000}, timeout=120)
        if r.status_code == 200:
            text = r.json()['choices'][0]['message']['content']
            if is_json: text = text[text.find('{'):text.rfind('}')+1]
            return json.loads(text) if is_json else text
    except Exception: pass
    return None

def call_llm(prompt, is_json=True, timeout=120, max_tokens=8192):
    res = call_gemini(prompt, is_json, timeout, max_tokens)
    if res: return res
    print("⚠️ Gemini فشل، تجربة Hugging Face...", flush=True)
    res = call_huggingface(prompt, is_json)
    if res: return res
    return None

def verify_ayah(surah_num, ayah_num):
    try:
        r = requests.get(f"https://api.alquran.cloud/v1/ayah/{surah_num}:{ayah_num}", timeout=10)
        if r.status_code == 200:
            name = r.json()['data']['surah']['name'].replace("سُورَةُ ", "").strip()
            return name, True
    except: pass
    return f"سورة {surah_num}", False

def parse_script_with_verses(script_text):
    pattern = r'\[آية[:\s]*(\d+)[:\s]+(\d+)\]'
    parts = re.split(pattern, script_text)
    segments = []
    i = 0
    while i < len(parts):
        if i % 3 == 0:
            text = parts[i].strip()
            text = re.sub(r'\[آية[^\]]*\]', '', text).strip()
            if len(text) > 5:
                segments.append({"type": "narration", "text": text})
            i += 1
        elif i % 3 == 1:
            try:
                surah, ayah = int(parts[i]), int(parts[i+1])
            except (ValueError, IndexError):
                i += 2; continue
            surah_name, valid = verify_ayah(surah, ayah)
            if valid:
                segments.append({
                    "type": "quran", "surah": surah, "surah_name": surah_name,
                    "ayah_numbers": [ayah], "display": f"{surah_name}: {ayah}"
                })
            i += 2
    return segments

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
    
    print(f"🚀 [1/5] بدء: {story_name} - الحلقة {ep_num}", flush=True)
    pending_file = PENDING_DIR / f"pending-{story_name}-{ep_num}.json"
    plan = None
    
    if pending_file.exists():
        try:
            loaded = json.loads(pending_file.read_text(encoding="utf-8"))
            if "title" in loaded and "summary" in loaded and len(str(loaded.get("summary", ""))) > 50:
                plan = loaded
        except: pass

    if not plan:
        print("📝 [2/5] توليد خطة الحلقة...", flush=True)
        plan_prompt = f"""أنت مؤرخ إسلامي. اكتب خطة للحلقة {ep_num} من قصة {story_name}.
اعتمد على القرآن الكريم (سورة آل عمران، سورة مريم) وتفسير ابن كثير.
ابتعد عن الإسرائيليات.
أعد النتيجة بصيغة JSON فقط: {{"title": "عنوان", "summary": "ملخص مفصل (300 كلمة)..."}}"""
        plan = call_llm(plan_prompt)
        
        if not plan or "title" not in plan or "summary" not in plan or len(str(plan.get("summary", ""))) < 50:
            plan = {"title": f"قصة {story_name} - الحلقة {ep_num}", "summary": f"نتعرف في هذه الحلقة على تفاصيل جديدة من قصة {story_name}."}
            
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        pending_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    print(f"📜 العنوان: {plan['title']}", flush=True)

    print("✍️ [3/5] توليد السرد...", flush=True)
    script_prompt = f"""أنت كاتب سيناريو إسلامي محترف.
اكتب نصاً سردياً درامياً مفصلاً جداً (2500 كلمة).
القصة: {story_name}
العنوان: {plan['title']}
الملخص: {plan['summary']}

قواعد صارمة:
1. الطول: يجب أن يكون النص طويلاً جداً. وسّع في وصف الصحراء، ومشاعر الخوف، والترقب، والحوار الداخلي.
2. عندما تريد إدراج آية، ضع علامة بهذا الشكل بالضبط: [آية: 3:35]
3. ضع العلامة بعد الجملة التي تدعمها الآية مباشرة.
4. عدد الآيات: من 5 إلى 8 آيات فقط.
5. لا تختصر الأحداث أبداً.
6. **مهم جداً:** قسّم النص إلى فقرات قصيرة. كل فقرة يجب ألا تتجاوز **60 كلمة**. افصل بين كل فقرة وفقرة بسطر جديد.

اكتب النص فقط."""
    script_text = call_llm(script_prompt, is_json=False, timeout=120, max_tokens=8192)
    
    if not script_text:
        print("❌ فشل توليد السرد.", flush=True); sys.exit(1)

    print("🔍 [4/6] تدقيق لغوي وإملائي...", flush=True)
    review_prompt = f"""أنت مدقق لغوي خبير. راجع النص التالي وصحح أي أخطاء إملائية أو سياقية. حافظ على علامات الآيات [آية: X:Y] كما هي. أعد النص المصحح فقط.

النص:
{script_text[:5000]}"""
    corrected_text = call_llm(review_prompt, is_json=False, timeout=120, max_tokens=8192)
    
    if corrected_text and len(corrected_text.split()) > 500:
        print("   ✅ تم تصحيح النص بنجاح.", flush=True)
        script_text = corrected_text
    else:
        print("   ⚠️ فشل التدقيق، سنستخدم النص الأصلي.", flush=True)

    print("🛠️ [5/6] استخراج المقاطع والتحقق من الآيات...", flush=True)
    verified = parse_script_with_verses(script_text)

    outro = random.choice(OUTRO_TEXTS)
    verified.append({"type": "outro", "text": outro["text"], "emotion_hint": outro["voice_style"], "visual": "mosque", "sfx": ""})
    
    next_title = f"الحلقة {ep_num + 1}"
    verified.append({"type": "teaser", "text": f"الحلقة القادمة: {next_title}.", "emotion_hint": "(in a calm, inviting tone)", "next_episode_title": next_title, "next_episode_source": story_name, "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"})

    print("💾 [6/6] حفظ...", flush=True)
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
