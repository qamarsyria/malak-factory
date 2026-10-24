import os, json, requests, sys, time, re, random
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
    print("=" * 60)
    print("🔑 فحص صحة المفاتيح...")
    print("=" * 60)
    pool = get_pool()
    valid = 0
    for key in pool.keys[:3]:
        try:
            r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=10)
            if r.status_code == 200:
                valid += 1
                print(f"   ✅ مفتاح {key[:15]}... → صالح")
            else:
                print(f"   ❌ مفتاح {key[:15]}... → {r.status_code}")
        except Exception as e:
            print(f"   ⚠️ {key[:15]}... → {str(e)[:50]}")
    print(f"📊 الحالة: {valid} صالح من {len(pool.keys[:3])}")
    print("=" * 60)

def get_working_models():
    """اكتشاف ذكي: يسأل جوجل، يستبعد 2.5، ويرتب 3.8 أولاً"""
    # محاولة القراءة من الكاش
    if Path(MODELS_CACHE_FILE).exists():
        try:
            cache = json.loads(Path(MODELS_CACHE_FILE).read_text(encoding="utf-8"))
            if time.time() - cache.get("ts", 0) < CACHE_TTL:
                return cache["models"]
        except: pass

    print("🔍 سؤال جوجل عن الموديلات المتاحة...")
    pool = get_pool()
    key = pool.get_current()
    
    try:
        r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=15)
        if r.status_code == 200:
            raw_models = [m["name"].replace("models/", "") for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
            
            # 🌟 الفلترة الذكية
            valid_models = []
            for m in raw_models:
                if "gemini" not in m or "embedding" in m or "aqa" in m or "vision" in m: continue
                if "2.5" in m: continue # 🌟 جوجل ألغت 2.5
                valid_models.append(m)
            
            # 🌟 الترتيب الذكي: 3.8 أولاً، ثم 3.6، ثم 3.5، ثم 1.5
            def score(m):
                if "3.8" in m: return 0
                if "3.6" in m: return 1
                if "3.5" in m: return 2
                if "3.1" in m: return 3
                if "1.5" in m: return 4
                return 5
                
            valid_models.sort(key=score)
            
            # حفظ في الكاش
            Path(MODELS_CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(MODELS_CACHE_FILE).write_text(json.dumps({"models": valid_models, "ts": time.time()}), encoding="utf-8")
            print(f"✅ الموديلات المتاحة (بعد الترتيب): {valid_models[:3]}")
            return valid_models
    except Exception as e:
        print(f"⚠️ فشل الاكتشاف: {e}")
        
    return ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-1.5-flash"] # احتياطي ذكي

def call_gemini(prompt, is_json=True, timeout=45, max_tokens=4000):
    pool = get_pool()
    models = get_working_models()
    
    for model in models[:3]: # نجرب أفضل 3 موديلات فقط
        key = pool.get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        config = {"temperature": 0.3, "maxOutputTokens": max_tokens}
        if is_json: config["responseMimeType"] = "application/json"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
        
        try:
            print(f"   📤 محاولة {model}...")
            r = requests.post(url, json=body, timeout=timeout)
            
            if r.status_code == 200:
                print(f"   ✅ نجح {model}")
                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                if is_json:
                    text = text[text.find('{'):text.rfind('}')+1]
                    return json.loads(text)
                return text
            
            print(f"   ❌ فشل {model}: HTTP {r.status_code}")
            if r.status_code == 429:
                pool.mark_failed(3600)
                continue
            elif r.status_code in [404, 503]:
                continue
        except Exception as e:
            print(f"   ⚠️ {model}: {str(e)[:100]}")
            continue
    return None

def call_groq(prompt, is_json=True):
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        print("   ⚠️ GROQ_API_KEY غير موجود (يمكن إضافته كاحتياطي)")
        return None
    
    print("   📤 محاولة Groq (احتياطي)...")
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {groq_key}"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 4000, "response_format": {"type": "json_object"}},
            timeout=45)
        if r.status_code == 200:
            print(f"   ✅ نجح Groq")
            return json.loads(r.json()['choices'][0]['message']['content']) if is_json else r.json()['choices'][0]['message']['content']
    except: pass
    return None

def call_llm(prompt, is_json=True, timeout=45, max_tokens=4000):
    res = call_gemini(prompt, is_json, timeout, max_tokens)
    if res: return res
    print("⚠️ Gemini فشل، تجربة Groq...")
    return call_groq(prompt, is_json)

def load_state():
    default = {"story_index": 0, "episode_num": 1}
    if Path(STATE_FILE).exists():
        try:
            saved = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
            for k, v in default.items():
                if k not in saved: saved[k] = v
            return saved
        except: pass
    return default

def save_state(state):
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch_story_text(story_name):
    print(f"📚 جلب ملخص مركز لقصة {story_name}...")
    prompt = f"أعطني ملخصاً مركزاً لأهم الأحداث في قصة {story_name} كما وردت في القرآن وتفسير ابن كثير، بـ 1000 كلمة."
    return call_llm(prompt, is_json=False, timeout=45, max_tokens=2000) or f"قصة {story_name}."

def build_bible(story_name):
    BIBLES_DIR.mkdir(parents=True, exist_ok=True)
    bible_file = BIBLES_DIR / f"{story_name}.json"
    if bible_file.exists():
        return json.loads(bible_file.read_text(encoding="utf-8"))

    print(f"🏗️ بناء هيكل 40 حلقة لـ {story_name}...")
    summary = fetch_story_text(story_name)
    
    prompt = f"""قسّم قصة {story_name} إلى 40 حلقة. كل حلقة: عنوان + ملخص.
القصة: {summary[:3000]}
أعد JSON: {{"story_name": "{story_name}", "episodes": [{{"ep": 1, "title": "...", "summary": "..."}}]}}"""
    
    result = call_llm(prompt, timeout=60, max_tokens=6000)
    if not result or "episodes" not in result:
        result = {"story_name": story_name, "episodes": [{"ep": i, "title": f"الحلقة {i}", "summary": "استكمال القصة"} for i in range(1, 41)]}
    
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
    
    prompt = f"""اكتب حلقة سردية عن {story_name}.
العنوان: {ep_plan['title']}
الملخص: {ep_plan['summary']}
القواعد: سرد درامي، آيات (أرقام فقط)، لا اختلاق.
أعد JSON: {{"segments": [{{"type": "narration", "text": "..."}}]}}"""
    
    result = call_llm(prompt, timeout=60, max_tokens=4000)
    if not result or "segments" not in result:
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        (PENDING_DIR / f"pending-{story_name}-{ep_num}.json").write_text(json.dumps({"title": ep_plan["title"]}), encoding="utf-8")
        sys.exit(0)

    verified_segments = []
    for seg in result["segments"]:
        if seg.get("type") == "quran":
            try:
                r = requests.get(f"https://api.alquran.cloud/v1/surah/{seg['surah']}/{','.join(map(str, seg['ayah_numbers']))}", timeout=10)
                if r.status_code == 200:
                    seg["surah_name"] = r.json()['data']['name']
                    seg["display"] = f"{seg['surah_name']}: {seg['ayah_numbers'][0]}"
                    verified_segments.append(seg)
            except: pass
        else:
            verified_segments.append(seg)

    outro = random.choice(OUTRO_TEXTS)
    verified_segments.append({"type": "outro", "text": outro["text"], "emotion_hint": outro["voice_style"], "visual": "mosque", "sfx": ""})

    if ep_num < len(bible["episodes"]):
        next_title = bible["episodes"][ep_num]["title"]
        verified_segments.append({"type": "teaser", "text": f"وهل تعلم ما حدث بعد ذلك؟ الحلقة القادمة: {next_title}.", "emotion_hint": "(mysterious)", "next_episode_title": f"{story_name} - {next_title}", "next_episode_source": story_name, "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{story_name}-{ep_num:02d}.json"
    out_file.write_text(json.dumps({"episode_number": ep_num, "title": f"{story_name} - {ep_plan['title']}", "segments": verified_segments}, ensure_ascii=False, indent=2), encoding="utf-8")
    
    Path("out").mkdir(exist_ok=True)
    Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")
    
    state["episode_num"] += 1
    save_state(state)
    print("✅ تم إنشاء الحلقة.")

if __name__ == "__main__":
    main()
