import os, re, sys, json, random, traceback
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from story_library import load_library, load_state, save_state, get_current_story, record_topic
from outro_templates import OUTRO_TEMPLATES
from utilities import notify_telegram, get_breaker

OUTPUT_DIR = _REPO_ROOT / "data" / "stories" / "generated"
PENDING_DIR = _REPO_ROOT / "state" / "pending"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_episode.txt"

SURAH_NAMES = {1: "الفاتحة", 2: "البقرة", 3: "آل عمران", 4: "النساء", 5: "المائدة", 6: "الأنعام", 7: "الأعراف", 8: "الأنفال", 9: "التوبة", 10: "يونس", 11: "هود", 12: "يوسف", 13: "الرعد", 14: "إبراهيم", 15: "الحجر", 16: "النحل", 17: "الإسراء", 18: "الكهف", 19: "مريم", 20: "طه", 21: "الأنبياء", 22: "الحج", 23: "المؤمنون", 24: "النور", 25: "الفرقان", 26: "الشعراء", 27: "النمل", 28: "القصص", 29: "العنكبوت", 30: "الروم", 31: "لقمان", 32: "السجدة", 33: "الأحزاب", 34: "سبأ", 35: "فاطر", 36: "يس", 37: "الصافات", 38: "ص", 39: "الزمر", 40: "غافر", 41: "فصلت", 42: "الشورى", 43: "الزخرف", 44: "الدخان", 45: "الجاثية", 46: "الأحقاف", 47: "محمد", 48: "الفتح", 49: "الحجرات", 50: "ق", 51: "الذاريات", 52: "الطور", 53: "النجم", 54: "القمر", 55: "الرحمن", 56: "الواقعة", 57: "الحديد", 58: "المجادلة", 59: "الحشر", 60: "الممتحنة", 61: "الصف", 62: "الجمعة", 63: "المنافقون", 64: "التغابن", 65: "الطلاق", 66: "التحريم", 67: "الملك", 68: "القلم", 69: "الحاقة", 70: "المعارج", 71: "نوح", 72: "الجن", 73: "المزمل", 74: "المدثر", 75: "القيامة", 76: "الإنسان", 77: "المرسلات", 78: "النبأ", 79: "النازعات", 80: "عبس", 81: "التكوير", 82: "الانفطار", 83: "المطففين", 84: "الانشقاق", 85: "البروج", 86: "الطارق", 87: "الأعلى", 88: "الغاشية", 89: "الفجر", 90: "البلد", 91: "الشمس", 92: "الليل", 93: "الضحى", 94: "الشرح", 95: "التين", 96: "العلق", 97: "القدر", 98: "البينة", 99: "الزلزلة", 100: "العاديات", 101: "القارعة", 102: "التكاثر", 103: "العصر", 104: "الهمزة", 105: "الفيل", 106: "قريش", 107: "الماعون", 108: "الكوثر", 109: "الكافرون", 110: "النصر", 111: "المسد", 112: "الإخلاص", 113: "الفلق", 114: "الناس"}
SURAH_NAME_TO_NUM = {v: k for k, v in SURAH_NAMES.items()}

def call_llm(prompt, is_json=True, timeout=180, max_tokens=8192):
    pool = get_pool(); breaker = get_breaker()
    for model in discover_and_cache(force=False)[:5]:
        if breaker.is_open(f"model:{model}"): continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens}}
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
                elif r.status_code == 429: pool.mark_failed(3600)
                elif r.status_code in (404, 503): breaker.record_failure(f"model:{model}"); break
            except Exception: pass
    return None

def call_huggingface(prompt, is_json=True):
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token: return None
    try:
        r = requests.post("https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3/v1/chat/completions", headers={"Authorization": f"Bearer {hf_token}", "Content-Type": "application/json"}, json={"model": "mistralai/Mistral-7B-Instruct-v0.3", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 4000}, timeout=180)
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"]
            if is_json:
                s, e = text.find("{"), text.rfind("}")
                if s != -1 and e > s: text = text[s:e + 1]
            return json.loads(text) if is_json else text
    except Exception: pass
    return None

def call_llm_with_fallback(prompt, is_json=True, timeout=180, max_tokens=8192):
    return call_llm(prompt, is_json, timeout, max_tokens) or call_huggingface(prompt, is_json)

def clean_narration_text(t: str) -> str:
    """تنظيف صارم جداً للسرد من أي حروف إنجليزية أو أرقام غريبة"""
    if not t: return ""
    # إزالة أي حروف إنجليزية
    t = re.sub(r'[a-zA-Z]+', '', t)
    # إزالة أي أرقام منفردة (مثل 2:30) التي قد تتسرب
    t = re.sub(r'\b\d+\b', '', t)
    # إزالة الأقواس الفارغة
    t = re.sub(r'\(\s*\)|\[\s*\]', '', t)
    # إزالة أي كلمات مهينة
    bad_words = ["وبخ", "موبخ", "بخع", "لعن", "غبي", "أحمق", "كاذب", "خائن", "مذلول", "حقير", "تافه"]
    for bw in bad_words: t = t.replace(bw, "")
    # تنظيف المسافات
    t = re.sub(r'\s+', ' ', t).strip()
    return t

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

def parse_script_with_verses(script_text):
    text = script_text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    for name, num in SURAH_NAME_TO_NUM.items(): 
        text = text.replace(f"آية: {name}:", f"آية: {num}:").replace(f"آية {name}:", f"آية {num}:")
    
    patterns = [r"\[آ[يی]ة[:\s]*(\d+)\s*[:\-،]\s*(\d+)\]", r"\[آ[يی]ة\s+(\d+)\s*[:\-،]\s*(\d+)\s*\]", r"[\(（]آ[يی]ة\s+(\d+)\s*[:\-،]\s*(\d+)[\)）]"]
    parts = None
    for pat in patterns:
        parts = re.split(pat, text)
        if len(parts) > 1: break
    if not parts or len(parts) == 1: 
        return [{"type": "narration", "text": clean_narration_text(c)} for c in split_narration(text) if c.strip()]

    segments, i, used_ayahs = [], 0, set()
    while i < len(parts):
        if i % 3 == 0:
            chunk = re.sub(r"[\[\(]آ[يی]ة[^\]]*[\]\)]", "", parts[i]).strip()
            chunk = clean_narration_text(chunk)
            if len(chunk) > 5:
                for piece in split_narration(chunk): 
                    segments.append({"type": "narration", "text": piece.strip()})
            i += 1
        else:
            try: surah, ayah = int(parts[i]), int(parts[i + 1])
            except: i += 2; continue
            
            if surah not in SURAH_NAMES or ayah < 1 or ayah > 286:
                i += 2
                continue
                
            ayah_key = f"{surah}:{ayah}"
            if ayah_key not in used_ayahs:
                segments.append({"type": "quran", "surah": surah, "surah_name": SURAH_NAMES.get(surah), "ayah_numbers": [ayah], "display": f"{SURAH_NAMES.get(surah)}: {ayah}"})
                used_ayahs.add(ayah_key)
            i += 2
    return segments

def build_plan_prompt(story, ep_num, state):
    covered = state.get("covered_topics", [])
    covered_str = "\n".join([f"- {t}" for t in covered]) if covered else "لا يوجد."
    
    return f'''أنت مؤرخ إسلامي. نحن نصنع سلسلة حلقات تغطي قصة "{story["title"]}" بتفاصيل دقيقة.
المواضيع التي تمت تغطيتها في الحلقات السابقة:
{covered_str}

اكتب خطة للحلقة {ep_num} تركز على جزء محدد وجديد من حياته (لم يتم تغطيته سابقاً).
أعد JSON فقط: {{"title": "عنوان", "summary": "ملخص دقيق ووقور...", "topic": "الموضوع الجديد"}}'''

def build_script_prompt(story, plan, ep_num):
    return f"""أنت راوي قصص الأنبياء ومؤرخ إسلامي متخصص في التفسير (تفسير ابن كثير، التفسير الميسر، وفتاوى ابن تيمية).
الخطة: {plan['title']} - {plan['summary']}
القصة المطلوبة: {story['title']}

قواعد صارمة جداً (أرجو الالتزام التام):
1. **الطول**: اكتب سرداً مفصلاً ومطولاً (من 1200 إلى 1500 كلمة).
2. **التوثيق 100%**: لا تكتب أي معلومة غير واردة في القرآن الكريم، أو تفسير ابن كثير، أو التفسير الميسر، أو آراء ابن تيمية. ممنوع منعاً باتاً ذكر الإسرائيليات أو الروايات الضعيفة.
3. **الآيات**: يجب إدراج 6 إلى 8 آيات قرآنية في النص، وتكتبها بهذه الصيغة بالضبط: [آية: رقم_السورة:رقم_الآية]. 
   **ممنوع منعاً باتاً كتابة أي كلمة أو رقم أو مرجع أو حرف إنجليزي بعد القوس مباشرة.** اترك مسافة وضع الجملة التالية فوراً.
4. **احترام مطلق**: لا تصف الملائكة أو الأنبياء بأي وصف مهين. استخدم ألفاظ التوقير.
5. **الأسلوب**: روائي متدفق، بدون عناوين فرعية.
اكتب النص السردي فقط."""

def build_teaser_prompt(story, ep_num):
    return f"""أنت راوي قصص محترف. اكتب **جملة واحدة فقط** تشوّق المستمع للحلقة {ep_num + 1} من قصة {story['title']}.
قواعد صارمة:
1. ابدأ **مباشرة** بسؤال درامي (مثل: "فكيف...؟" / "وماذا...؟" / "هل...؟").
2. **ممنوع تمامًا** أن تبدأ بـ: "إليك"، "هذا"، "النص"، "الحلقة القادمة"، "في الحلقة القادمة"، "ترقبوا"، "بالتأكيد".
3. أعد الجملة فقط، بدون علامات اقتباس.
مثال صحيح: "فكيف ستواجه مريم قومها؟ وما السر الذي كتمته؟"
أعد الجملة فقط."""

def main():
    library, state = load_library(), load_state()
    story = get_current_story(library, state)
    ep_num = state.get("episode_num", 1)
    save_state(state)
    print(f"🚀 {story['title']} — الحلقة {ep_num}", flush=True)

    plan = call_llm_with_fallback(build_plan_prompt(story, ep_num, state)) or {"title": f"الحلقة {ep_num}", "summary": "...", "topic": ""}
    
    raw_title = plan.get("title", "")
    cleaned_title = re.sub(r'الحلقة\s*\d+[:\s\-]*', '', raw_title).strip()
    if not cleaned_title: cleaned_title = f"الحلقة {ep_num}"
    plan["title"] = cleaned_title

    script_text = call_llm_with_fallback(build_script_prompt(story, plan, ep_num), is_json=False, timeout=180, max_tokens=8192)
    if not script_text: sys.exit(1)
    
    script_text = re.sub(r'\(.*?\)|\[مشهد.*?\]', '', script_text)
    segments = parse_script_with_verses(script_text)
    
    summary_raw = call_llm_with_fallback(f"بناءً على: {plan['summary']}\nاستخرج 'خلاصة وعبرة' بليغة من 3 أسطر. ابدأ مباشرة دون مقدمات.", is_json=False, timeout=60, max_tokens=300)
    summary_clean = clean_narration_text(summary_raw)
    if not summary_clean or len(summary_clean) < 20:
        summary_clean = "وهكذا تتجلى عظمة تدبير الله، وأن الصبر والتوكل هما زاد المؤمن."
    segments.append({"type": "summary", "text": summary_clean, "visual": "sunrise sky"})

    teaser_raw = call_llm_with_fallback(build_teaser_prompt(story, ep_num), is_json=False, timeout=60, max_tokens=200)
    teaser_clean = clean_narration_text(teaser_raw)
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
    try: main()
    except Exception as e: traceback.print_exc(); sys.exit(1)
