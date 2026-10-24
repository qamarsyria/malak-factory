import os, json, requests, sys, time, re, random
from pathlib import Path
from gemini_tts_pool import get_pool

# ═══════════════════════════════════════════════════════════
# الإعدادات والمسارات
# ═══════════════════════════════════════════════════════════
STATE_FILE = "state/story_state.json"
BIBLES_DIR = Path("state/bibles")
OUTPUT_DIR = Path("data/stories/generated")
PENDING_DIR = Path("state/pending")
MODELS_CACHE_FILE = "state/models_cache.json"

STORIES_ORDER = ["مريم", "زكريا", "يحيى", "عيسى"]
CACHE_TTL = 86400  # 24 ساعة

# ═══════════════════════════════════════════════════════════
# [طلبك] نصوص نهاية الحلقة (Outro)
# ═══════════════════════════════════════════════════════════
OUTRO_TEXTS = [
    {
        "text": "قال رسول الله ﷺ: «مَن دَلَّ على خيرٍ فله مثلُ أجرِ فاعِلِه». كن أنت الدال على الخير، شارك هذه الحلقة مع من تحب، فلك مثل أجره. ملاك المعرفة... كن دالاً على الخير.",
        "voice_style": "(in a gentle, encouraging tone)"
    },
    {
        "text": "قال ﷺ: «مَن دعا إلى هُدًى كان له من الأجرِ مثلُ أجورِ مَن تبِعَه». كل شخص يهتدي بهذه الكلمة، لك مثل أجره. انشر العلم، وشارك القناة. ملاك المعرفة.",
        "voice_style": "(in a warm, inviting tone)"
    },
    {
        "text": "قال ﷺ لعلي رضي الله عنه: «لأَنْ يَهدِيَ اللهُ بك رجُلًا واحدًا خيرٌ لك من حُمْرِ النَّعَم». لعل كلمة واحدة منك تكون سبباً في هداية إنسان. شارك الحلقة. ملاك المعرفة.",
        "voice_style": "(in a hopeful, inspiring tone)"
    },
    {
        "text": "قال ﷺ: «إذا مات ابنُ آدمَ انقطع عملُه إلا من ثلاثٍ: صدقةٍ جاريةٍ، أو علمٍ يُنتفَعُ به». اجعل لك علماً جارياً، شارك هذه المعرفة لتبقى صدقة جارية لك. ملاك المعرفة.",
        "voice_style": "(in a reflective, solemn tone)"
    },
    {
        "text": "قال ﷺ: «نَضَّرَ اللهُ امرأً سَمِعَ مِنَّا حديثًا فحَفِظَه حتى يُبَلِّغَه». كن ممن يحفظ العلم ويبلغه. انشر هذه الحلقة. ملاك المعرفة.",
        "voice_style": "(in a strong, encouraging tone)"
    },
    {
        "text": "قال ﷺ: «إنَّ اللهَ وملائكتَه وأهلَ السماواتِ والأرضِ... ليُصَلُّونَ على مُعَلِّمِ الناسِ الخيرَ». كن معلمًا للناس الخير، ولو بمشاركة. ملاك المعرفة.",
        "voice_style": "(in a reverent, uplifting tone)"
    },
    {
        "text": "قال الله تعالى: ﴿وَتَعَاوَنُوا عَلَى الْبِرِّ وَالتَّقْوَىٰ﴾. التعاون على الخير واجب. شارك الحلقة، وتعاون معنا على البر والتقوى. ملاك المعرفة.",
        "voice_style": "(in a clear, instructional tone)"
    },
    {
        "text": "قال الله تعالى: ﴿وَتَوَاصَوْا بِالْحَقِّ وَتَوَاصَوْا بِالصَّبْرِ﴾. من التواصي بالحق أن تنشر الخير. انشر الحلقة، وذكر بها غيرك. ملاك المعرفة.",
        "voice_style": "(in a calm, wise tone)"
    },
    {
        "text": "قال الإمام ابن الجوزي رحمه الله: «من أحب ألا ينقطع عمله بعد موته فلينشر العلم». انشر العلم، ليبقى أجرك بعد موتك. ملاك المعرفة.",
        "voice_style": "(in a deep, philosophical tone)"
    },
    {
        "text": "قال ﷺ: «بَلِّغُوا عَنِّي ولو آية». خير الكلام ما قل ودل. دلّ غيرك على الخير، وشاركنا الأجر. ملاك المعرفة.",
        "voice_style": "(in a direct, motivating tone)"
    }
]

# ═══════════════════════════════════════════════════════════
# [A1] ذكاء اكتشاف الموديلات
# ═══════════════════════════════════════════════════════════
def get_working_models():
    # 1. قراءة الكاش
    if Path(MODELS_CACHE_FILE).exists():
        try:
            cache = json.loads(Path(MODELS_CACHE_FILE).read_text(encoding="utf-8"))
            if time.time() - cache.get("ts", 0) < CACHE_TTL:
                return cache["models"]
        except: pass

    # 2. السؤال عن المتاح
    print("🔍 سؤال جوجل عن الموديلات المتاحة...")
    pool = get_pool()
    key = pool.get_current()
    try:
        r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=15)
        if r.status_code == 200:
            models = []
            for m in r.json().get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    name = m["name"].replace("models/", "")
                    if "gemini" in name and "embedding" not in name:
                        models.append(name)
            # ترتيب: flash أولاً، ثم pro
            models.sort(key=lambda x: (0 if "flash" in x else 1, len(x)))
            
            # حفظ في الكاش
            Path(MODELS_CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(MODELS_CACHE_FILE).write_text(json.dumps({"models": models, "ts": time.time()}), encoding="utf-8")
            print(f"✅ الموديلات المكتشفة: {models[:3]}")
            return models
    except Exception as e:
        print(f"⚠️ فشل الاكتشاف: {e}")
    
    return ["gemini-1.5-flash", "gemini-1.5-flash-latest", "gemini-pro"] # احتياطي

# ═══════════════════════════════════════════════════════════
# [A2] محرك الاتصال (Gemini + Groq)
# ═══════════════════════════════════════════════════════════
def call_gemini(prompt, is_json=True):
    pool = get_pool()
    models = get_working_models()

    for model in models[:4]:
        for retry in range(2):
            key = pool.get_current()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            config = {"temperature": 0.3, "maxOutputTokens": 8192}
            if is_json: config["responseMimeType"] = "application/json"
            body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
            
            try:
                r = requests.post(url, json=body, timeout=60)
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
            except Exception:
                continue
    return None

def call_groq(prompt, is_json=True):
    """احتياطي: Groq API (إذا توفر المفتاح)"""
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key: return None
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
    body = {
        "model": "llama3-70b-8192",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 4000
    }
    if is_json: body["response_format"] = {"type": "json_object"}
    
    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
        if r.status_code == 200:
            text = r.json()['choices'][0]['message']['content']
            if is_json: return json.loads(text)
            return text
    except: pass
    return None

def call_llm(prompt, is_json=True):
    """الواجهة الموحدة: جرب Gemini أولاً، ثم Groq"""
    res = call_gemini(prompt, is_json)
    if res: return res
    print("⚠️ Gemini فشل، تجربة Groq...")
    res = call_groq(prompt, is_json)
    if res: return res
    print("❌ كل المحركات فشلت.")
    return None

# ═══════════════════════════════════════════════════════════
# إدارة الحالة
# ═══════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════
# [B1] جلب المصادر المتعددة
# ═══════════════════════════════════════════════════════════
def fetch_story_text(story_name):
    print(f"📚 جلب المصادر لقصة {story_name}...")
    # (ملاحظة: يمكن إضافة محاولات جلب مباشرة من ketabonline هنا لاحقاً)
    # حالياً نستخدم Gemini لتوليد النص الموثق من المصادر
    prompt = f"اكتب النص التاريخي الكامل والموثق لقصة {story_name} من كتاب قصص الأنبياء لابن كثير، بالتفصيل، دون إضافات."
    text = call_llm(prompt, is_json=False)
    return text or f"قصة {story_name} من المصادر الموثوقة."

# ═══════════════════════════════════════════════════════════
# [A4, B2] بناء الهيكل وبنك الذاكرة
# ═══════════════════════════════════════════════════════════
def build_bible(story_name):
    BIBLES_DIR.mkdir(parents=True, exist_ok=True)
    bible_file = BIBLES_DIR / f"{story_name}.json"
    if bible_file.exists():
        return json.loads(bible_file.read_text(encoding="utf-8"))

    print(f"🏗️ بناء هيكل 40 حلقة لقصة {story_name}...")
    full_text = fetch_story_text(story_name)
    
    prompt = f"""
    أنت كاتب سيناريو. لديك النص التالي عن {story_name}:
    {full_text[:12000]}
    
    قسّم القصة إلى 40 حلقة متتالية. كل حلقة تحتوي على:
    - عنوان.
    - ملخص درامي.
    
    أعد النتيجة بصيغة JSON:
    {{
      "story_name": "{story_name}",
      "episodes": [ {{"ep": 1, "title": "العنوان", "summary": "الملخص"}}, ... ]
    }}
    """
    result = call_llm(prompt)
    if not result or "episodes" not in result:
        print("⚠️ استخدام هيكل احتياطي.")
        result = {"story_name": story_name, "episodes": [{"ep": i, "title": f"الحلقة {i}", "summary": "..."} for i in range(1, 41)]}
    
    bible_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

# ═══════════════════════════════════════════════════════════
# [A3] التقييم الذاتي
# ═══════════════════════════════════════════════════════════
def self_critique(segments, story_name):
    print("🧠 تقييم ذاتي للنص...")
    text = " ".join([s['text'] for s in segments if s['type'] == 'narration'])
    prompt = f"""
    راجع النص التالي لقصة {story_name}.
    هل يحتوي على أي أخطاء شرعية؟ هل الآيات في محلها؟
    إذا كان النص ممتازاً، أعد نفس النص. إذا كان يحتاج تعديلاً، عدّله.
    أعد النتيجة بصيغة JSON:
    {{"segments": [ {{"type": "narration", "text": "..."}}, ... ]}}
    
    النص: {text[:3000]}
    """
    res = call_llm(prompt)
    if res and "segments" in res:
        return res["segments"]
    return segments

# ═══════════════════════════════════════════════════════════
# [B4] المعالجة الآمنة (Degradation)
# ═══════════════════════════════════════════════════════════
def save_pending(ep_data, story_name, ep_num):
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    out = PENDING_DIR / f"pending-{story_name}-{ep_num}.json"
    out.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 تم حفظ الحلقة في {out} للمعالجة لاحقاً.")

# ═══════════════════════════════════════════════════════════
# المحرك الرئيسي
# ═══════════════════════════════════════════════════════════
def main():
    state = load_state()
    story_name = STORIES_ORDER[state["story_index"]]
    ep_num = state["episode_num"]
    
    bible = build_bible(story_name)
    
    if ep_num > len(bible["episodes"]):
        print(f"🎉 انتهت قصة {story_name}. الانتقال للقصة التالية.")
        state["story_index"] += 1
        state["episode_num"] = 1
        if state["story_index"] >= len(STORIES_ORDER):
            print("✅ تم إنجاز جميع القصص!")
            sys.exit(0)
        save_state(state)
        sys.exit(0)

    ep_plan = bible["episodes"][ep_num - 1]
    print(f"📖 توليد: {story_name} - الحلقة {ep_num}: {ep_plan['title']}")
    
    # [A4] بنك الذاكرة (ملخص الحلقات السابقة)
    memory = ""
    if ep_num > 1:
        prev_summaries = [e["summary"] for e in bible["episodes"][:ep_num-1]]
        memory = "ملخص الحلقات السابقة: " + " | ".join(prev_summaries)

    prompt = f"""
    أنت كاتب سيناريو إسلامي. اكتب حلقة عن {story_name}.
    عنوان الحلقة: {ep_plan['title']}
    ملخص الحلقة: {ep_plan['summary']}
    {memory}
    
    القواعد:
    1. اكتب سرداً درامياً غنياً (حوالي 1500 كلمة).
    2. استخرج الآيات القرآنية المرتبطة (كأرقام سور وآيات فقط).
    3. لا تختلق أحداثاً.
    
    أعد النتيجة بصيغة JSON:
    {{
      "segments": [ {{"type": "narration", "text": "..."}}, {{"type": "quran", "surah": 3, "ayah_numbers": [35]}} ]
    }}
    """
    result = call_llm(prompt)
    if not result or "segments" not in result:
        print("❌ فشل التوليد. سيتم الحفظ في pending.")
        # [B4] حفظ ما تيسر
        save_pending({"title": ep_plan["title"], "summary": ep_plan["summary"]}, story_name, ep_num)
        sys.exit(0)

    # [A3] التقييم الذاتي
    final_segments = self_critique(result["segments"], story_name)

    # التحقق من الآيات
    verified_segments = []
    for seg in final_segments:
        if seg.get("type") == "quran":
            try:
                url = f"https://api.alquran.cloud/v1/surah/{seg['surah']}/{','.join(map(str, seg['ayah_numbers']))}"
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    surah_name = r.json()['data']['name']
                    seg["surah_name"] = surah_name
                    seg["display"] = f"{surah_name}: {seg['ayah_numbers'][0]}"
                    verified_segments.append(seg)
            except: pass
        else:
            verified_segments.append(seg)

    # [Outro] إضافة النص الدعوي
    outro = random.choice(OUTRO_TEXTS)
    verified_segments.append({
        "type": "outro",
        "text": outro["text"],
        "emotion_hint": outro["voice_style"],
        "visual": "islamic mosque architecture",
        "sfx": ""
    })

    # إضافة Teaser (للحلقة القادمة)
    if ep_num < len(bible["episodes"]):
        next_title = bible["episodes"][ep_num]["title"]
        verified_segments.append({
            "type": "teaser",
            "text": f"وهل تعلم ما الذي حدث بعد ذلك؟ هذا ما سنعرفه في الحلقة القادمة: {next_title}. لا تفوتها.",
            "emotion_hint": "(in a mysterious, inviting tone)",
            "next_episode_title": f"{story_name} - {next_title}",
            "next_episode_source": story_name,
            "url": "https://qamarsyria.github.io/qamar-malak/",
            "platform_name": "ملاك المعرفة"
        })

    # الحفظ النهائي
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{story_name}-{ep_num:02d}.json"
    
    episode_data = {
        "episode_number": ep_num,
        "title": f"{story_name} - {ep_plan['title']} - الحلقة {ep_num}",
        "segments": verified_segments
    }
    out_file.write_text(json.dumps(episode_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    Path("out").mkdir(exist_ok=True)
    Path("out/latest_episode.txt").write_text(str(out_file), encoding="utf-8")
    
    state["episode_num"] += 1
    save_state(state)
    print("✅ تم إنشاء الحلقة بنجاح.")

if __name__ == "__main__":
    main()
