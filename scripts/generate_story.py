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
    """فحص صحة المفاتيح قبل البدء"""
    print("=" * 60)
    print("🔑 فحص صحة المفاتيح...")
    print("=" * 60)
    pool = get_pool()
    valid, invalid, exhausted = 0, 0, 0
    for key in pool.keys[:3]:  # فحص أول 3 مفاتيح للسرعة
        try:
            r = requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
                timeout=10
            )
            if r.status_code == 200:
                valid += 1
                print(f"   ✅ مفتاح {key[:15]}... → صالح")
            elif r.status_code == 429:
                exhausted += 1
                print(f"   🚫 مفتاح {key[:15]}... → استنفد (429)")
            else:
                invalid += 1
                print(f"   ❌ مفتاح {key[:15]}... → خطأ ({r.status_code})")
        except Exception as e:
            print(f"   ⚠️ مفتاح {key[:15]}... → استثناء: {str(e)[:50]}")
    print(f"📊 الحالة: {valid} صالح، {exhausted} مستنفد، {invalid} تالف")
    print("=" * 60)
    return valid > 0

def get_working_models():
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
            models = [m["name"].replace("models/", "") for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m["name"] and "embedding" not in m["name"]]
            models.sort(key=lambda x: (0 if "flash" in x else 1, len(x)))
            Path(MODELS_CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(MODELS_CACHE_FILE).write_text(json.dumps({"models": models, "ts": time.time()}), encoding="utf-8")
            print(f"✅ الموديلات المكتشفة: {models[:3]}")
            return models
    except Exception as e:
        print(f"⚠️ فشل الاكتشاف: {e}")
    return ["gemini-2.0-flash", "gemini-1.5-flash"]

def call_gemini(prompt, is_json=True, timeout=45, max_tokens=4000):
    """مع طباعة تفصيلية لكل فشل"""
    pool = get_pool()
    models = get_working_models()

    for model in models[:2]:  # فقط أفضل موديلين (لتسريع الفشل الواضح)
        key = pool.get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        config = {"temperature": 0.3, "maxOutputTokens": max_tokens}
        if is_json: config["responseMimeType"] = "application/json"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
        
        try:
            print(f"   📤 محاولة {model} (Timeout: {timeout}s, MaxTokens: {max_tokens})...")
            r = requests.post(url, json=body, timeout=timeout)
            
            if r.status_code == 200:
                print(f"   ✅ نجح {model}")
                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                if is_json:
                    text = text[text.find('{'):text.rfind('}')+1]
                    return json.loads(text)
                return text
            
            # طباعة السبب الدقيق
            err_text = r.text[:300]
            print(f"   ❌ فشل {model}: HTTP {r.status_code}")
            print(f"      الرسالة: {err_text}")
            
            if r.status_code == 429:
                print(f"      ⚠️ المفتاح استنفد، ننتقل للتالي...")
                pool.mark_failed(3600)
                continue
            elif r.status_code == 404:
                print(f"      ⚠️ الموديل غير موجود، ننتقل للتالي...")
                break
            elif r.status_code == 503:
                print(f"      ⚠️ جوجل مشغولة، انتظار 5ث...")
                time.sleep(5)
                continue
            else:
                continue
                
        except requests.exceptions.Timeout:
            print(f"   ⏱️ {model}: انتهت المهلة ({timeout}s)")
            continue
        except requests.exceptions.ConnectionError as e:
            print(f"   🌐 {model}: خطأ اتصال - {str(e)[:100]}")
            continue
        except json.JSONDecodeError as e:
            print(f"   📋 {model}: JSON غير صالح - {str(e)[:100]}")
            continue
        except Exception as e:
            print(f"   ⚠️ {model}: استثناء - {type(e).__name__}: {str(e)[:100]}")
            continue
    return None

def call_groq(prompt, is_json=True):
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        print("   ⚠️ GROQ_API_KEY غير موجود في الـ Secrets")
        return None
    
    print("   📤 محاولة Groq...")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
    body = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 4000}
    if is_json: body["response_format"] = {"type": "json_object"}
    
    try:
        r = requests.post(url, headers=headers, json=body, timeout=45)
        if r.status_code == 200:
            print(f"   ✅ نجح Groq")
            text = r.json()['choices'][0]['message']['content']
            if is_json: return json.loads(text)
            return text
        else:
            print(f"   ❌ Groq فشل: HTTP {r.status_code} - {r.text[:200]}")
    except Exception as e:
        print(f"   ⚠️ Groq: {str(e)[:100]}")
    return None

def call_llm(prompt, is_json=True, timeout=45, max_tokens=4000):
    res = call_gemini(prompt, is_json, timeout, max_tokens)
    if res: return res
    print("⚠️ Gemini فشل، تجربة Groq...")
    res = call_groq(prompt, is_json)
    if res: return res
    print("❌ كل المحركات فشلت.")
    return None

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
    """ملخص مركز بدل النص الكامل - لتجنب الطلبات الضخمة"""
    print(f"📚 جلب ملخص مركز لقصة {story_name}...")
    prompt = f"""أعطني ملخصاً مركزاً لأهم الأحداث في قصة {story_name} كما وردت في القرآن الكريم وتفسير ابن كثير.
اعتمد على مصادر أهل السنة والجماعة فقط.
اكتب 800-1200 كلمة تغطي كل المراحل الأساسية للقصة.
لا تختلق أحداثاً غير موثقة."""
    text = call_llm(prompt, is_json=False, timeout=45, max_tokens=2000)
    return text or f"قصة {story_name} من المصادر الموثوقة."

def build_bible(story_name):
    BIBLES_DIR.mkdir(parents=True, exist_ok=True)
    bible_file = BIBLES_DIR / f"{story_name}.json"
    if bible_file.exists():
        print(f"✅ هيكل {story_name} موجود مسبقاً")
        return json.loads(bible_file.read_text(encoding="utf-8"))

    print(f"🏗️ بناء هيكل 40 حلقة لقصة {story_name}...")
    summary = fetch_story_text(story_name)
    
    prompt = f"""قسّم قصة {story_name} (المعطاة أدناه) إلى 40 حلقة متتالية.
كل حلقة تحتوي على: عنوان جذاب + ملخص درامي في سطر أو سطرين.

القصة: {summary[:3000]}

أعد النتيجة بصيغة JSON فقط:
{{"story_name": "{story_name}", "episodes": [{{"ep": 1, "title": "...", "summary": "..."}}]}}"""
    
    result = call_llm(prompt, timeout=60, max_tokens=6000)
    if not result or "episodes" not in result:
        print("⚠️ استخدام هيكل احتياطي (40 حلقة عامة)")
        result = {"story_name": story_name, "episodes": [{"ep": i, "title": f"الحلقة {i}", "summary": "استكمال أحداث القصة"} for i in range(1, 41)]}
    
    bible_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ تم بناء الهيكل: {len(result['episodes'])} حلقة")
    return result

def main():
    print("\n" + "=" * 60)
    print("🚀 محرك القصص — تشخيص شامل")
    print("=" * 60)
    
    check_keys_health()
    
    state = load_state()
    story_name = STORIES_ORDER[state["story_index"]]
    ep_num = state["episode_num"]
    
    bible = build_bible(story_name)
    
    if ep_num > len(bible["episodes"]):
        print(f"🎉 انتهت {story_name}")
        state["story_index"] += 1
        state["episode_num"] = 1
        save_state(state)
        sys.exit(0)

    ep_plan = bible["episodes"][ep_num - 1]
    print(f"\n📖 توليد: {story_name} - الحلقة {ep_num}: {ep_plan['title']}")
    
    memory = ""
    if ep_num > 1:
        memory = "الحلقات السابقة: " + " | ".join([e["summary"] for e in bible["episodes"][:ep_num-1]])

    prompt = f"""أنت كاتب سيناريو إسلامي. اكتب حلقة سردية قصيرة (1000-1500 كلمة).
قصة: {story_name}
العنوان: {ep_plan['title']}
الملخص: {ep_plan['summary']}
{memory}

القواعد:
1. سرد درامي غني.
2. الآيات القرآنية (كأرقام سور وآيات فقط).
3. لا اختلاق أحداث.

أعد JSON: {{"segments": [{{"type": "narration", "text": "..."}}, {{"type": "quran", "surah": 3, "ayah_numbers": [35]}}]}}"""
    
    result = call_llm(prompt, timeout=60, max_tokens=4000)
    
    if not result or "segments" not in result:
        print("\n❌ فشل التوليد — الحفظ في pending")
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        (PENDING_DIR / f"pending-{story_name}-{ep_num}.json").write_text(
            json.dumps({"title": ep_plan["title"], "summary": ep_plan["summary"]}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        sys.exit(0)

    # التحقق من الآيات
    verified_segments = []
    for seg in result["segments"]:
        if seg.get("type") == "quran":
            try:
                url = f"https://api.alquran.cloud/v1/surah/{seg['surah']}/{','.join(map(str, seg['ayah_numbers']))}"
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    seg["surah_name"] = r.json()['data']['name']
                    seg["display"] = f"{seg['surah_name']}: {seg['ayah_numbers'][0]}"
                    verified_segments.append(seg)
            except: pass
        else:
            verified_segments.append(seg)

    # Outro
    outro = random.choice(OUTRO_TEXTS)
    verified_segments.append({"type": "outro", "text": outro["text"], "emotion_hint": outro["voice_style"], "visual": "mosque", "sfx": ""})

    # Teaser
    if ep_num < len(bible["episodes"]):
        next_title = bible["episodes"][ep_num]["title"]
        verified_segments.append({
            "type": "teaser",
            "text": f"وهل تعلم ما حدث بعد ذلك؟ هذا ما سنعرفه في الحلقة القادمة: {next_title}.",
            "emotion_hint": "(in a mysterious, inviting tone)",
            "next_episode_title": f"{story_name} - {next_title}",
            "next_episode_source": story_name,
            "url": "https://qamarsyria.github.io/qamar-malak/",
            "platform_name": "ملاك المعرفة"
        })

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{story_name}-{ep_num:02d}.json"
    episode_data = {"episode_number": ep_num, "title": f"{story_name} - {ep_plan['title']} - الحلقة {ep_num}", "segments": verified_segments}
    out_file.write_text(json.dumps(episode_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    Path("out").mkdir(exist_ok=True)
    Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")
    
    state["episode_num"] += 1
    save_state(state)
    print(f"\n✅ نجح — الحلقة: {out_file}")

if __name__ == "__main__":
    main()
