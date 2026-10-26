"""توليد حلقة: خطة → سرد → تدقيق → مقاطع → JSON."""
import os, re, sys, json, random, traceback
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from story_library import load_library, load_state, save_state, get_current_story
from outro_templates import OUTRO_TEMPLATES
from utilities import notify_telegram, get_breaker

OUTPUT_DIR = _REPO_ROOT / "data/stories/generated"
PENDING_DIR = _REPO_ROOT / "state/pending"
OUT_FILE_PATH = _REPO_ROOT / "out/latest_episode.txt"

SURAH_NAMES = {
    1: "الفاتحة", 2: "البقرة", 3: "آل عمران", 4: "النساء", 5: "المائدة", 6: "الأنعام", 7: "الأعراف", 8: "الأنفال", 9: "التوبة", 10: "يونس",
    11: "هود", 12: "يوسف", 13: "الرعد", 14: "إبراهيم", 15: "الحجر", 16: "النحل", 17: "الإسراء", 18: "الكهف", 19: "مريم", 20: "طه",
    21: "الأنبياء", 22: "الحج", 23: "المؤمنون", 24: "النور", 25: "الفرقان", 26: "الشعراء", 27: "النمل", 28: "القصص", 29: "العنكبوت", 30: "الروم",
    31: "لقمان", 32: "السجدة", 33: "الأحزاب", 34: "سبأ", 35: "فاطر", 36: "يس", 37: "الصافات", 38: "ص", 39: "الزمر", 40: "غافر",
    41: "فصلت", 42: "الشورى", 43: "الزخرف", 44: "الدخان", 45: "الجاثية", 46: "الأحقاف", 47: "محمد", 48: "الفتح", 49: "الحجرات", 50: "ق",
    51: "الذاريات", 52: "الطور", 53: "النجم", 54: "القمر", 55: "الرحمن", 56: "الواقعة", 57: "الحديد", 58: "المجادلة", 59: "الحشر", 60: "الممتحنة",
    61: "الصف", 62: "الجمعة", 63: "المنافقون", 64: "التغابن", 65: "الطلاق", 66: "التحريم", 67: "الملك", 68: "القلم", 69: "الحاقة", 70: "المعارج",
    71: "نوح", 72: "الجن", 73: "المزمل", 74: "المدثر", 75: "القيامة", 76: "الإنسان", 77: "المرسلات", 78: "النبأ", 79: "النازعات", 80: "عبس",
    81: "التكوير", 82: "الانفطار", 83: "المطففين", 84: "الانشقاق", 85: "البروج", 86: "الطارق", 87: "الأعلى", 88: "الغاشية", 89: "الفجر", 90: "البلد",
    91: "الشمس", 92: "الليل", 93: "الضحى", 94: "الشرح", 95: "التين", 96: "العلق", 97: "القدر", 98: "البينة", 99: "الزلزلة", 100: "العاديات",
    101: "القارعة", 102: "التكاثر", 103: "العصر", 104: "الهمزة", 105: "الفيل", 106: "قريش", 107: "الماعون", 108: "الكوثر", 109: "الكافرون", 110: "النصر",
    111: "المسد", 112: "الإخلاص", 113: "الفلق", 114: "الناس"
}
SURAH_NAME_TO_NUM = {v: k for k, v in SURAH_NAMES.items()}

def call_llm(prompt, is_json=True, timeout=180, max_tokens=8192):
    pool = get_pool()
    breaker = get_breaker()
    for model in discover_and_cache(force=False)[:5]:
        if breaker.is_open(f"model:{model}"): continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens}}
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
                        pool._advance()
                        breaker.record_success(f"model:{model}")
                        return parsed
                    except json.JSONDecodeError: break
                elif r.status_code == 429: pool.mark_failed(3600)
                elif r.status_code in (404, 503): breaker.record_failure(f"model:{model}"); break
            except Exception: pass
    return None

def fix_arabic_tashkeel(text):
    """إصلاح إجباري للزخارف والكلمات الشائعة قبل النطق (تجويد مبسط)"""
    # مسافات إجبارية بين كلمات الصلاة
    text = text.replace("ﷺ", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    text = text.replace("صلى الله عليه وسلم", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    
    text = text.replace("رضي الله عنه", "رَضِيَ اللَّهُ عَنْهُ")
    text = text.replace("رحمه الله", "رَحِمَهُ اللَّهُ")
    text = text.replace("عليه السلام", "عَلَيْهِ السَّلَامُ")
    
    text = re.sub(r'(?<![ء-ي])الله(?![ء-ي])', 'اللَّه', text)
    text = re.sub(r'(?<![ء-ي])لله(?![ء-ي])', 'لِلَّه', text)
    text = re.sub(r'(?<![ء-ي])بالله(?![ء-ي])', 'بِاللَّه', text)
    text = re.sub(r'(?<![ء-ي])والله(?![ء-ي])', 'وَاللَّه', text)
    
    text = re.sub(r'الرحمن', 'الرَّحْمَن', text).replace(r'الرحيم', 'الرَّحِيم')
    return text

def normalize_arabic_numerals(text):
    return text.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))

def verify_ayah(surah_num, ayah_num):
    if not (1 <= surah_num <= 114) or not (1 <= ayah_num <= 286): return None, False
    try:
        r = requests.get(f"https://api.alquran.cloud/v1/ayah/{surah_num}:{ayah_num}", timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", {})
            if data and data.get("numberInSurah") == ayah_num: return SURAH_NAMES.get(surah_num), True
    except Exception: pass
    return SURAH_NAMES.get(surah_num), True

def split_narration(text, max_chars=700):
    text = text.strip()
    if not text: return []
    if len(text) <= max_chars: return [text]
    paragraphs = re.split(r'\n\s*\n', text)
    segments = []
    for para in paragraphs:
        para = para.strip()
        if not para: continue
        sentences = re.split(r'(?<=[.!؟])\s+', para)
        current = ""
        for sent in sentences:
            sent = sent.strip()
            if not sent: continue
            if len(sent) > max_chars:
                if current: segments.append(current); current = ""
                parts = re.split(r'(?<=[،؛])\s+', sent)
                for p in parts:
                    if len(current) + len(p) + 1 <= max_chars: current = (current + " " + p).strip() if current else p
                    else:
                        if current: segments.append(current)
                        current = p
            elif len(current) + len(sent) + 1 <= max_chars: current = (current + " " + sent).strip() if current else sent
            else:
                if current: segments.append(current)
                current = sent
        if current: segments.append(current)
    return [s for s in segments if len(s) > 5]

def parse_script_with_verses(script_text):
    text = normalize_arabic_numerals(script_text)
    # تحويل اسم السورة إلى رقم (مثال: البقرة -> 2)
    for name, num in SURAH_NAME_TO_NUM.items():
        text = text.replace(f"آية: {name}:", f"آية: {num}:")
        text = text.replace(f"آية {name}:", f"آية {num}:")
        
    patterns = [r'\[آ[يی]ة[:\s]*(\d+)\s*[:\-،]\s*(\d+)\]', r'[\(（]آ[يی]ة\s+(\d+)\s*[:\-،]\s*(\d+)[\)）]', r'\[آ[يی]ة\s+(\d+)\s*[:\-،]\s*(\d+)\s*\]']
    parts = None
    for pat in patterns:
        parts = re.split(pat, text)
        if len(parts) > 1: break
    if not parts or len(parts) == 1:
        return [{"type": "narration", "text": fix_arabic_tashkeel(c)} for c in split_narration(text)]
    segments, i, quran_count, used_ayahs = [], 0, 0, set()
    while i < len(parts):
        if i % 3 == 0:
            chunk = parts[i].strip()
            chunk = re.sub(r'[\[\(]آ[يی]ة[^\]]*[\]\)]', '', chunk).strip()
            if len(chunk) > 5:
                for piece in split_narration(chunk): segments.append({"type": "narration", "text": fix_arabic_tashkeel(piece)})
            i += 1
        else:
            try: surah, ayah = int(parts[i]), int(parts[i + 1])
            except (ValueError, IndexError): i += 2; continue
            ayah_key = f"{surah}:{ayah}"
            if ayah_key not in used_ayahs:
                sname, ok = verify_ayah(surah, ayah)
                if ok:
                    segments.append({"type": "quran", "surah": surah, "surah_name": sname, "ayah_numbers": [ayah], "display": f"{sname}: {ayah}"})
                    used_ayahs.add(ayah_key)
                    quran_count += 1
            i += 2
    return segments

def build_plan_prompt(story, ep_num):
    return f"""أنت مؤرخ إسلامي. اكتب خطة للحلقة {ep_num} من "{story['title']}".
اعتمد على: {', '.join(story.get('sources', []))}
غطِّ هذه المواضيع: {', '.join(story.get('key_topics', [])[:5])}
أعد JSON فقط: {{"title": "عنوان", "summary": "ملخص"}}"""

def build_script_prompt(story, plan, ep_num):
    return f"""أنت كاتب سيناريو إسلامي محترف. اكتب سرداً درامياً متصلاً (رواية صوتية).
القصة: {story['title']} | الحلقة: {ep_num} | الخطة: {plan['title']} - {plan['summary']}

قواعد صارمة جداً:
1. ممنوع كتابة عناوين فرعية (المشهد الأول، الفصل الأول...).
2. اكتب 2000 كلمة.
3. **التشكيل الإلزامي**: يجب كتابة النص بالكامل مع التشكيل التام.
4. اكتب "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ" كاملة، ولا تستخدم الرمز (ﷺ).
5. **شروط الآيات (مهم جداً):**
   - **ممنوع كتابة نص الآية في السرد**. اكتب فقط جملة السرد، ثم ضع الرمز `[آية: 3:35]` (رقم السورة ثم رقم الآية). النظام سيقوم بإدراج الآية تلقائياً.
   - مثال صحيح: "ثم قال الله تعالى [آية: 2:30] فسجد الملائكة."
   - مثال خاطئ (لا تفعله): "ثم قال الله تعالى [آية: 2:30] وإذ قال ربك للملائكة إني جاعل..."

اكتب النص فقط."""

def build_summary_prompt(story, plan):
    return f"""بناءً على أحداث هذه الحلقة من قصة {story['title']}:
{plan['summary']}
استخرج "خلاصة وعبرة" بليغة من 3 أسطر موجهة للمستمع. أعد النص فقط."""

def build_teaser_prompt(story, ep_num):
    return f"""أنت مسوق محترف. اكتب جملة تشويقية واحدة (سؤال مشوق) لجذب المستمع لانتظار الحلقة {ep_num + 1} من قصة {story['title']}.
مثال: "فكيف ستواجه مريم قومها؟ وما هو السر الذي كتمته؟ اكتشف ذلك في الحلقة القادمة."
اكتب الجملة فقط بدون مقدمات."""

def save_episode(ep_data):
    story_id = ep_data["story_id"]
    ep_num = ep_data["episode_number"]
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(ep_data, ensure_ascii=False, indent=2)
    (PENDING_DIR / f"{story_id}-{ep_num:02d}.json").write_text(payload, encoding="utf-8")
    output_path = OUTPUT_DIR / f"{story_id}-{ep_num:02d}.json"
    output_path.write_text(payload, encoding="utf-8")
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    return output_path

def main():
    library = load_library()
    state = load_state()
    story = get_current_story(library, state)
    ep_num = state.get("episode_num", 1)
    save_state(state)

    print(f"🚀 {story['title']} — الحلقة {ep_num}/{story.get('target_episodes', 40)}", flush=True)

    pending_path = PENDING_DIR / f"{story['id']}-{ep_num:02d}.json"
    if pending_path.exists():
        try:
            ep_data = json.loads(pending_path.read_text(encoding="utf-8"))
            if ep_data.get("segments") and len(ep_data["segments"]) > 3:
                out = save_episode(ep_data)
                print(f"✅ استئناف من pending: {out}", flush=True)
                return
        except Exception: pass

    plan = call_llm(build_plan_prompt(story, ep_num))
    if not plan: plan = {"title": f"الحلقة {ep_num}", "summary": "..."}

    print("✍️ توليد السرد...", flush=True)
    script_text = call_llm(build_script_prompt(story, plan, ep_num), is_json=False, timeout=180, max_tokens=8192)
    if not script_text: sys.exit(1)

    segments = parse_script_with_verses(script_text)

    print("📝 توليد الخلاصة...", flush=True)
    summary_text = call_llm(build_summary_prompt(story, plan), is_json=False, timeout=60, max_tokens=300)
    if not summary_text: summary_text = "وهكذا نرى في هذه القصة عظمة تدبير الله، وأن الصبر والتوكل هما زاد المؤمن."
    segments.append({"type": "summary", "text": fix_arabic_tashkeel(summary_text.strip()), "emotion_hint": "(in a calm, reflective tone)", "visual": "mosque"})

    print("📢 توليد التشويق...", flush=True)
    teaser_text = call_llm(build_teaser_prompt(story, ep_num), is_json=False, timeout=60, max_tokens=200)
    if not teaser_text: teaser_text = f"فماذا حدث بعد ذلك؟ تابعوا الحلقة القادمة."
    segments.append({"type": "teaser", "text": fix_arabic_tashkeel(teaser_text.strip()), "emotion_hint": "(in a mysterious tone)", "next_episode_title": f"الحلقة {ep_num + 1}", "next_episode_source": story["title"], "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"})

    outro = random.choice(OUTRO_TEMPLATES)
    segments.append({"type": "cta", "text": outro["text"], "emotion_hint": outro["voice_style"], "visual": "mosque"})

    ep_data = {"episode_number": ep_num, "story_id": story["id"], "story_title": story["title"], "title": f"{story['title']} — {plan['title']}", "segments": segments}
    save_episode(ep_data)

if __name__ == "__main__":
    try: main()
    except Exception as e:
        traceback.print_exc(); sys.exit(1)
