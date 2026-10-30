"""توليد حلقة: خطة → سرد → تدقيق → مقاطع → JSON."""
import os
import re
import sys
import json
import random
import traceback
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
    111: "المسد", 112: "الإخلاص", 113: "الفلق", 114: "الناس",
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


def call_huggingface(prompt, is_json=True):
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token: return None
    model_id = "mistralai/Mistral-7B-Instruct-v0.3"
    url = f"https://api-inference.huggingface.co/models/{model_id}/v1/chat/completions"
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {hf_token}", "Content-Type": "application/json"}, json={"model": model_id, "messages": [{"role": "user", "content": prompt}], "temperature": 0.4, "max_tokens": 6000}, timeout=180)
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


def fix_arabic_tashkeel_minimal(text):
    text = text.replace("ﷺ", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    text = text.replace("صلى الله عليه وسلم", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    text = text.replace("رضي الله عنه", "رَضِيَ اللَّهُ عَنْهُ")
    text = text.replace("رضي الله عنها", "رَضِيَ اللَّهُ عَنْهَا")
    text = text.replace("رحمه الله", "رَحِمَهُ اللَّهُ")
    text = text.replace("عليه السلام", "عَلَيْهِ السَّلَامُ")
    text = re.sub(r'(?<![ء-ي])الله(?![ء-ي])', 'اللَّه', text)
    text = re.sub(r'(?<![ء-ي])لله(?![ء-ي])', 'لِلَّه', text)
    text = re.sub(r'(?<![ء-ي])بالله(?![ء-ي])', 'بِاللَّه', text)
    text = re.sub(r'(?<![ء-ي])والله(?![ء-ي])', 'وَاللَّه', text)
    text = re.sub(r'الرحمن', 'الرَّحْمَن', text)
    text = re.sub(r'الرحيم', 'الرَّحِيم', text)
    return text


def normalize_arabic_numerals(text):
    return text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))


def verify_ayah(surah_num, ayah_num):
    if not (1 <= surah_num <= 114) or not (1 <= ayah_num <= 286): return None, False
    try:
        r = requests.get(f"https://api.alquran.cloud/v1/ayah/{surah_num}:{ayah_num}", timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", {})
            if data and data.get("numberInSurah") == ayah_num: return SURAH_NAMES.get(surah_num), True
    except Exception: pass
    return SURAH_NAMES.get(surah_num), False


def split_narration(text, max_chars=700):
    text = text.strip()
    if not text: return []
    sentences = re.split(r'(?<=[.!؟])\s+', text)
    chunks, current = [], ""
    for sentence in sentences:
        if not sentence.strip(): continue
        if len(sentence) > max_chars:
            words = sentence.split()
            for word in words:
                if len(current) + len(word) + 1 <= max_chars:
                    current = (current + " " + word).strip() if current else word
                else:
                    if current: chunks.append(current)
                    current = word
        else:
            if len(current) + len(sentence) + 1 <= max_chars:
                current = (current + " " + sentence).strip() if current else sentence
            else:
                if current: chunks.append(current)
                current = sentence
    if current: chunks.append(current)
    return chunks


def parse_script_with_verses(script_text):
    text = normalize_arabic_numerals(script_text)
    for name, num in SURAH_NAME_TO_NUM.items():
        text = text.replace(f"آية: {name}:", f"آية: {num}:").replace(f"آية {name}:", f"آية {num}:")

    patterns = [r"\[آ[يی]ة[:\s]*(\d+)\s*[:\-،]\s*(\d+)\]", r"[\(（]آ[يی]ة\s+(\d+)\s*[:\-،]\s*(\d+)[\)）]", r"\[آ[يی]ة\s+(\d+)\s*[:\-،]\s*(\d+)\s*\]"]
    parts = None
    for pat in patterns:
        parts = re.split(pat, text)
        if len(parts) > 1: break

    if not parts or len(parts) == 1:
        return [{"type": "narration", "text": fix_arabic_tashkeel_minimal(c)} for c in split_narration(text)]

    segments, i, used_ayahs = [], 0, set()
    while i < len(parts):
        if i % 3 == 0:
            chunk = parts[i].strip()
            # إزالة أي آية مكتوبة بين الأقواس لمنع التكرار
            chunk = re.sub(r'﴿.*?﴾', '', chunk)
            chunk = re.sub(r"[\[\(]آ[يی]ة[^\]]*[\]\)]", "", chunk).strip()
            if len(chunk) > 5:
                for piece in split_narration(chunk):
                    segments.append({"type": "narration", "text": fix_arabic_tashkeel_minimal(piece)})
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
            i += 2
    return segments


def review_in_chunks(text, chunk_size=4500):
    if len(text) <= chunk_size:
        result = call_llm_with_fallback("صحح الأخطاء الإملائية فقط. حافظ على علامات [آية: X:Y] كما هي. أعد النص المصحح كاملاً:\n\n" + text, is_json=False, timeout=180, max_tokens=8192)
        return result if result and len(result) > len(text) * 0.8 else text
    return text


def build_plan_prompt(story, ep_num, covered_topics):
    avoid = f"\nتجنب هذه المواضيع: {', '.join(covered_topics)}" if covered_topics else ""
    return f'أنت مؤرخ إسلامي. اكتب خطة للحلقة {ep_num} من "{story["title"]}".\nاعتمد على: {", ".join(story.get("sources", []))}\nالمواضيع: {", ".join(story.get("key_topics", [])[:8])}{avoid}\n\nأعد JSON فقط: {{"title": "عنوان", "summary": "ملخص دقيق ووقور (300 كلمة)...", "topic": "الموضوع"}}'


def build_script_prompt(story, plan, ep_num):
    return f"""أنت راوي قصص الأنبياء ومؤرخ إسلامي موثوق. اكتب نصاً سردياً دينياً وقوراً، للحلقة {ep_num} من قصة "{story['title']}".
الخطة: {plan['title']} - {plan['summary']}

قواعد صارمة:
1. المصدر: القرآن، السنة، تفسير ابن كثير. ممنوع الإسرائيليات.
2. **ممنوع أي لغة سينمائية** (الكاميرا، المشهد، اللقطة). نحن نحكي قصة حقيقية.
3. اكتب من 1500 إلى 2000 كلمة.
4. **التشكيل**: اكتب النص بالتشكيل التام.
5. اكتب "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ" كاملة، ممنوع استخدام (ﷺ).
6. **الآيات**: اكتب فقط العلامة [آية: رقم_السورة:رقم_الآية]. **ممنوع كتابة نص الآية نهائياً**. النظام سيقوم بإدراج الآية بصوت منفصل.
7. أدرج من 6 إلى 8 آيات.
8. ابدأ السرد مباشرة، دون مقدمات.

اكتب النص السردي فقط."""


def build_summary_prompt(story, plan):
    return f"بناءً على: {plan['summary']}\nاستخرج 'خلاصة وعبرة' بليغة من 3 أسطر. أعد النص فقط."


def build_teaser_prompt(story, ep_num):
    return f"اكتب جملة تشويقية واحدة (سؤال مشوق) للحلقة {ep_num + 1} من قصة {story['title']}. الجملة فقط."


def save_episode(ep_data):
    story_id, ep_num = ep_data["story_id"], ep_data["episode_number"]
    PENDING_DIR.mkdir(parents=True, exist_ok=True); OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(ep_data, ensure_ascii=False, indent=2)
    (PENDING_DIR / f"{story_id}-{ep_num:02d}.json").write_text(payload, encoding="utf-8")
    output_path = OUTPUT_DIR / f"{story_id}-{ep_num:02d}.json"
    output_path.write_text(payload, encoding="utf-8")
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    return output_path


def main():
    library, state = load_library(), load_state()
    story = get_current_story(library, state)
    ep_num = state.get("episode_num", 1)
    save_state(state)

    print(f"🚀 {story['title']} — الحلقة {ep_num}", flush=True)
    pending_path = PENDING_DIR / f"{story['id']}-{ep_num:02d}.json"
    if pending_path.exists():
        try:
            ep_data = json.loads(pending_path.read_text(encoding="utf-8"))
            if ep_data.get("segments") and len(ep_data["segments"]) > 3:
                out = save_episode(ep_data)
                print(f"✅ استئناف: {out}", flush=True); return
        except Exception: pass

    plan = call_llm_with_fallback(build_plan_prompt(story, ep_num, state.get("covered_topics", [])))
    if not plan: plan = {"title": f"الحلقة {ep_num}", "summary": "...", "topic": ""}

    print("✍️ توليد السرد...", flush=True)
    script_text = call_llm_with_fallback(build_script_prompt(story, plan, ep_num), is_json=False, timeout=180, max_tokens=8192)
    if not script_text: sys.exit(1)
    script_text = review_in_chunks(script_text)
    segments = parse_script_with_verses(script_text)

    print("📝 توليد الخلاصة...", flush=True)
    summary_text = call_llm_with_fallback(build_summary_prompt(story, plan), is_json=False, timeout=60, max_tokens=300)
    segments.append({"type": "summary", "text": summary_text.strip() if summary_text else "وهكذا تتجلى العبرة.", "visual": "sunrise sky"})

    print("📢 توليد التشويق...", flush=True)
    teaser_text = call_llm_with_fallback(build_teaser_prompt(story, ep_num), is_json=False, timeout=60, max_tokens=200)
    segments.append({"type": "teaser", "text": teaser_text.strip() if teaser_text else "فماذا حدث بعد ذلك؟", "next_episode_title": f"الحلقة {ep_num + 1}", "url": "https://qamarsyria.github.io/qamar-malak/", "platform_name": "ملاك المعرفة"})

    outro = random.choice(OUTRO_TEMPLATES)
    segments.append({"type": "cta", "text": outro["text"], "visual": "mosque architecture"})

    ep_data = {"episode_number": ep_num, "story_id": story["id"], "story_title": story["title"], "title": f"{story['title']} — {plan['title']}", "segments": segments}
    save_episode(ep_data)
    if plan.get("topic"): record_topic(state, plan["topic"])
    save_state(state)
    print(f"✅ تم التوليد ({len(segments)} مقطع)", flush=True)

if __name__ == "__main__":
    try: main()
    except Exception as e: traceback.print_exc(); sys.exit(1)
