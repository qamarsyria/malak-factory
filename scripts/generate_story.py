import os, re, sys, json, random, traceback, gzip, time
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from story_library import load_library, load_state, save_state, get_current_story, record_topic
from outro_templates import OUTRO_TEMPLATES
from utilities import get_breaker

OUTPUT_DIR = _REPO_ROOT / "data" / "stories" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_episode.txt"
LIBRARIES_DIR = _REPO_ROOT / "data" / "libraries"

SURAH_NAMES = {1: "الفاتحة", 2: "البقرة", 3: "آل عمران", 4: "النساء", 5: "المائدة", 6: "الأنعام", 7: "الأعراف", 8: "الأنفال", 9: "التوبة", 10: "يونس", 11: "هود", 12: "يوسف", 13: "الرعد", 14: "إبراهيم", 15: "الحجر", 16: "النحل", 17: "الإسراء", 18: "الكهف", 19: "مريم", 20: "طه", 21: "الأنبياء", 22: "الحج", 23: "المؤمنون", 24: "النور", 25: "الفرقان", 26: "الشعراء", 27: "النمل", 28: "القصص", 29: "العنكبوت", 30: "الروم", 31: "لقمان", 32: "السجدة", 33: "الأحزاب", 34: "سبأ", 35: "فاطر", 36: "يس", 37: "الصافات", 38: "ص", 39: "الزمر", 40: "غافر", 41: "فصلت", 42: "الشورى", 43: "الزخرف", 44: "الدخان", 45: "الجاثية", 46: "الأحقاف", 47: "محمد", 48: "الفتح", 49: "الحجرات", 50: "ق", 51: "الذاريات", 52: "الطور", 53: "النجم", 54: "القمر", 55: "الرحمن", 56: "الواقعة", 57: "الحديد", 58: "المجادلة", 59: "الحشر", 60: "الممتحنة", 61: "الصف", 62: "الجمعة", 63: "المنافقون", 64: "التغابن", 65: "الطلاق", 66: "التحريم", 67: "الملك", 68: "القلم", 69: "الحاقة", 70: "المعارج", 71: "نوح", 72: "الجن", 73: "المزمل", 74: "المدثر", 75: "القيامة", 76: "الإنسان", 77: "المرسلات", 78: "النبأ", 79: "النازعات", 80: "عبس", 81: "التكوير", 82: "الانفطار", 83: "المطففين", 84: "الانشقاق", 85: "البروج", 86: "الطارق", 87: "الأعلى", 88: "الغاشية", 89: "الفجر", 90: "البلد", 91: "الشمس", 92: "الليل", 93: "الضحى", 94: "الشرح", 95: "التين", 96: "العلق", 97: "القدر", 98: "البينة", 99: "الزلزلة", 100: "العاديات", 101: "القارعة", 102: "التكاثر", 103: "العصر", 104: "الهمزة", 105: "الفيل", 106: "قريش", 107: "الماعون", 108: "الكوثر", 109: "الكافرون", 110: "النصر", 111: "المسد", 112: "الإخلاص", 113: "الفلق", 114: "الناس"}
SURAH_NAME_TO_NUM = {v: k for k, v in SURAH_NAMES.items()}

_ayah_lib = []
try:
    lib_path = LIBRARIES_DIR / "ayah-library.json.gz"
    if lib_path.exists():
        with gzip.open(lib_path, "rt", encoding="utf-8") as f:
            _ayah_lib = json.load(f)
except Exception as e:
    print(f"⚠️ فشل تحميل مكتبة الآيات: {e}")

# ═══════════════════════════════════════════════════════════
# 🌟 البحث الشامل: من كل السور المذكورة في القصة
# ═══════════════════════════════════════════════════════════
def retrieve_context(story, limit=12):
    if not _ayah_lib: return "لا توجد نصوص متاحة."
    
    sources = story.get("sources", [])
    if not sources: sources = ["القرآن"]
    
    context = []
    for surah_name in sources:
        # تنظيف اسم السورة
        clean_name = surah_name.replace("سورة ", "").replace("سُورَةُ ", "").strip()
        for item in _ayah_lib:
            if clean_name in item.get("surah_name", ""):
                context.append(f"- {item['surah_name']} (سورة رقم {item['surah_number']}، آية رقم {item['ayah_number']}): {item['text']} | التفسير: {item.get('tafsir', '')[:100]}")
                if len(context) >= limit:
                    break
        if len(context) >= limit:
            break
    
    return "\n".join(context) if context else "لم يتم العثور على آيات مطابقة."

def call_llm(prompt, is_json=True, timeout=180, max_tokens=8192):
    pool = get_pool()
    breaker = get_breaker()
    try:
        models = discover_and_cache(force=False)[:5]
    except Exception as e:
        print(f"❌ خطأ في جلب النماذج: {e}", flush=True)
        return None

    if not models:
        print("❌ لا توجد نماذج متاحة!", flush=True)
        return None
        
    for model in models:
        if breaker.is_open(f"model:{model}"): continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens}}
                if is_json: body["generationConfig"]["responseMimeType"] = "application/json"
                
                r = requests.post(url, json=body, timeout=timeout)
                
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts: break
                    text = parts[0].get("text", "")
                    if is_json:
                        s, e = text.find("{"), text.rfind("}")
                        if s == -1 or e <= s: break
                        text = text[s:e + 1]
                    try:
                        parsed = json.loads(text) if is_json else text
                        pool._advance(); breaker.record_success(f"model:{model}")
                        return parsed
                    except json.JSONDecodeError: break
                elif r.status_code == 429: 
                    pool.mark_failed(3600)
                elif r.status_code in (404, 503): 
                    breaker.record_failure(f"model:{model}"); break
            except requests.exceptions.Timeout:
                pass
            except Exception as e: 
                pass
    return None

def clean_narration_text(t: str) -> str:
    if not t: return ""
    t = re.sub(r'[a-zA-Z]+', '', t)
    t = re.sub(r'\b\d+\b', '', t)
    t = re.sub(r'\(\s*\)|\[\s*\]', '', t)
    t = re.sub(r'[﴿﴾\(\)]', '', t)
    bad_words = ["وبخ", "موبخ", "بخع", "لعن", "غبي", "أحمق", "كاذب", "خائن", "مذلول", "حقير", "تافه"]
    for bw in bad_words: t = t.replace(bw, "")
    return re.sub(r'\s+', ' ', t).strip()

def split_narration(text, max_chars=150):
    sentences = re.split(r'(?<=[.!؟])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_chars: current += " " + s
        else:
            if current: chunks.append(current.strip())
            current = s
    if current: chunks.append(current.strip())
    return chunks

# ═══════════════════════════════════════════════════════════
# 🌟 المحلل الذكي (يقبل أي صيغة يكتبها جيميني)
# ═══════════════════════════════════════════════════════════
def parse_script_with_verses(script_text):
    text = script_text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    
    # تحويل أسماء السور لأرقام
    for name, num in SURAH_NAME_TO_NUM.items(): 
        text = text.replace(f"آية: {name}:", f"آية: {num}:").replace(f"آية {name}:", f"آية {num}:")
        text = text.replace(f"[{name}:", f"[{num}:").replace(f"({name}:", f"({num}:")
    
    # أنماط متعددة (مع أو بدون كلمة "آية"، مع أو بدون أقواس)
    patterns = [
        r"\[آ[يی]ة[:\s]*(\d+)\s*[:\-،]\s*(\d+)\]",  # [آية: 2:30]
        r"\[آ[يی]ة\s+(\d+)\s*[:\-،]\s*(\d+)\s*\]",  # [آية 2:30]
        r"[\(（]آ[يی]ة\s+(\d+)\s*[:\-،]\s*(\d+)[\)）]",  # (آية 2:30)
        r"\[(\d+)\s*[:\-]\s*(\d+)\]",               # [2:30] أو [2-30]
        r"\[سورة\s+(\d+)[:\s]+آية\s+(\d+)\]",        # [سورة 2: آية 30]
    ]
    
    parts = None
    for pat in patterns:
        parts = re.split(pat, text)
        if len(parts) > 1: break
        
    if not parts or len(parts) == 1: 
        return [{"type": "narration", "text": clean_narration_text(c)} for c in split_narration(text) if c.strip()]

    segments, i, used_ayahs = [], 0, set()
    while i < len(parts):
        if i % 3 == 0:
            chunk = parts[i].strip()
            chunk = re.sub(r'[\(\[﴿].*?[\)\]﴾]', '', chunk) 
            chunk = re.sub(r"[\[\(]آ[يی]ة[^\]]*[\]\)]", "", chunk).strip()
            chunk = clean_narration_text(chunk)
            if len(chunk) > 5:
                for piece in split_narration(chunk): 
                    segments.append({"type": "narration", "text": piece.strip()})
            i += 1
        else:
            try: surah, ayah = int(parts[i]), int(parts[i + 1])
            except: i += 2; continue
            if surah not in SURAH_NAMES or ayah < 1 or ayah > 286: i += 2; continue
            ayah_key = f"{surah}:{ayah}"
            if ayah_key not in used_ayahs:
                segments.append({"type": "quran", "surah": surah, "surah_name": SURAH_NAMES.get(surah), "ayah_numbers": [ayah], "display": f"{SURAH_NAMES.get(surah)}: {ayah}"})
                used_ayahs.add(ayah_key)
            i += 2
    return segments

def build_plan_prompt(story, ep_num, state):
    total_eps = story.get("target_episodes", 15)
    covered = state.get("covered_topics", [])
    covered_str = "\n".join([f"- {t}" for t in covered]) if covered else "لا يوجد."
    return f'''أنت مؤرخ إسلامي. نحن نصنع سلسلة من {total_eps} حلقة تغطي قصة "{story["title"]}" بتفاصيل دقيقة.
المواضيع التي تمت تغطيتها في الحلقات السابقة:
{covered_str}

اكتب خطة للحلقة {ep_num} (من أصل {total_eps}). يجب أن تركز على جزء محدد جداً وجديد من حياته (لم يتم تغطيته سابقاً).
أعد JSON فقط: {{"title": "عنوان", "summary": "ملخص دقيق ووقور...", "topic": "الموضوع الجديد"}}'''

def build_script_prompt(story, plan, ep_num, context):
    return f"""أنت راوي قصص الأنبياء ومؤرخ إسلامي.
الخطة: {plan['title']} - {plan['summary']}
القصة المطلوبة: {story['title']}

لقد قمنا باستخراج النصوص التالية من مصادرنا الموثوقة. **أنت مجبر على استخدام هذه النصوص فقط:**

المراجع:
{context}

قواعد صارمة جداً:
1. اكتب سرداً قصصياً مشوقاً (حوالي 600 كلمة، 5 دقائق فيديو). ابدأ بسؤال وثائقي مشوق.
2. **التوثيق 100%**: لا تضف أي معلومة غير موجودة في المراجع. ممنوع الإسرائيليات.
3. **الآيات (إجباري)**: يجب إدراج **3 آيات على الأقل** من الآيات المذكورة أعلاه. الصيغة الإجبارية: [آية: رقم_السورة:رقم_الآية]. مثال: [آية: 2:30]. **ممنوع كتابة نص الآية، اكتب مرجعها فقط**.
4. **اللغة**: اللغة العربية الفصحى فقط. يُمنع منعاً باتاً كتابة أي حرف إنجليزي.
5. اكتب النص السردي فقط، بدون عناوين فرعية."""

def build_teaser_prompt(story, ep_num):
    return f"""أنت راوي قصص محترف. اكتب **جملة واحدة فقط** تشوّق المستمع للحلقة {ep_num + 1} من قصة {story['title']}.
قواعد صارمة:
1. ابدأ **مباشرة** بسؤال درامي.
2. ممنوع أن تبدأ بـ: "إليك"، "هذا"، "النص"، "ترقبوا".
3. أعد الجملة فقط، بدون علامات اقتباس."""

def main():
    library, state = load_library(), load_state()
    story = get_current_story(library, state)
    ep_num = state.get("episode_num", 1)
    save_state(state)
    print(f"🚀 {story['title']} — الحلقة {ep_num}", flush=True)

    # 🌟 البحث الشامل في كل سور القصة
    context = retrieve_context(story, limit=15)
    print(f"📚 تم استرجاع المراجع من {len(story.get('sources', []))} سورة.", flush=True)

    print("📝 جاري توليد الخطة...", flush=True)
    plan_raw = call_llm(build_plan_prompt(story, ep_num, state))
    
    if isinstance(plan_raw, str):
        try:
            s, e = plan_raw.find("{"), plan_raw.rfind("}")
            plan = json.loads(plan_raw[s:e+1])
        except: plan = {"title": f"الحلقة {ep_num}", "summary": "...", "topic": ""}
    else:
        plan = plan_raw or {"title": f"الحلقة {ep_num}", "summary": "...", "topic": ""}
    
    raw_title = plan.get("title", "")
    cleaned_title = re.sub(r'الحلقة\s*\d+[:\s\-]*', '', raw_title).strip()
    plan["title"] = cleaned_title or f"الحلقة {ep_num}"

    print("✍️ جاري توليد السرد (بناءً على المراجع)...", flush=True)
    
    # 🌟 5 محاولات (بدل 3) عشان نضمن الآيات
    max_retries = 5
    script_text = None
    for attempt in range(max_retries):
        script_text = call_llm(build_script_prompt(story, plan, ep_num, context), is_json=False)
        if script_text:
            temp_segments = parse_script_with_verses(script_text)
            ayah_count = sum(1 for s in temp_segments if s["type"] == "quran")
            if ayah_count >= 2:
                print(f"✅ وجدنا {ayah_count} آيات في المحاولة {attempt+1}.", flush=True)
                break
            else:
                print(f"⚠️ المحاولة {attempt+1}: وجدنا {ayah_count} آية فقط. نطلب المزيد...", flush=True)
        else:
            print(f"⚠️ المحاولة {attempt+1}: فشل الاتصال.", flush=True)
        time.sleep(3)
    
    if not script_text:
        print("❌ فشل توليد النص بعد 5 محاولات.", flush=True)
        sys.exit(1)

    script_text = re.sub(r'\(.*?\)|\[مشهد.*?\]', '', script_text)
    segments = parse_script_with_verses(script_text)
    
    print("📝 جاري توليد الخلاصة...", flush=True)
    summary_raw = call_llm(f"بناءً على: {plan['summary']}\nاستخرج 'خلاصة وعبرة' بليغة من 3 أسطر. ابدأ مباشرة دون مقدمات.", is_json=False)
    summary_clean = clean_narration_text(summary_raw if isinstance(summary_raw, str) else "")
    if not summary_clean or len(summary_clean) < 20:
        summary_clean = "وهكذا تتجلى عظمة تدبير الله، وأن الصبر والتوكل هما زاد المؤمن."
    segments.append({"type": "summary", "text": summary_clean, "visual": "sunrise sky"})

    print("📢 جاري توليد التشويق...", flush=True)
    teaser_raw = call_llm(build_teaser_prompt(story, ep_num), is_json=False)
    teaser_clean = clean_narration_text(teaser_raw if isinstance(teaser_raw, str) else "")
    if not teaser_clean or len(teaser_clean) < 20:
        teaser_clean = "فماذا حدث بعد ذلك؟ تابعوا الحلقة القادمة."
    segments.append({"type": "teaser", "text": teaser_clean, "next_episode_title": f"الحلقة {ep_num + 1}", "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"})

    outro = random.choice(OUTRO_TEMPLATES)
    segments.append({"type": "cta", "text": outro["text"], "visual": "mosque architecture"})

    ep_data = {
        "episode_number": ep_num, "story_id": story["id"], "story_title": story["title"],
        "title": f"{story['title']} — الحلقة {ep_num} — {plan['title']}", 
        "segments": segments
    }
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{story['id']}-{ep_num:02d}.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")

    if plan.get("topic"): record_topic(state, plan["topic"])
    save_state(state)
    print(f"✅ تم التوليد ({len(segments)} مقطع)", flush=True)

if __name__ == "__main__":
    try: 
        main()
    except Exception as e:
        print(f"❌ خطأ فادح: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
