import os, re, sys, json, random, traceback
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import notify_telegram, get_breaker

OUTPUT_DIR = _REPO_ROOT / "data" / "daily" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_episode.txt"
ROTATION_FILE = _REPO_ROOT / "state" / "daily_rotation.json"
FAQ_JSON = _REPO_ROOT / "data" / "faq-data.json"

# ============================================================
# 1. قاعدة بيانات الأذكار الموثقة 100% (مدمجة لضمان الأمان)
# ============================================================
VERIFIED_ADHKAR = [
    {
        "title": "سيد الاستغفار",
        "text": "اللَّهُمَّ أَنْتَ رَبِّي لا إِلَهَ إِلا أَنْتَ، خَلَقْتَنِي وَأَنَا عَبْدُكَ، وَأَنَا عَلَى عَهْدِكَ وَوَعْدِكَ مَا اسْتَطَعْتُ، أَعُوذُ بِكَ مِنْ شَرِّ مَا صَنَعْتُ، أَبُوءُ لَكَ بِنِعْمَتِكَ عَلَيَّ، وَأَبُوءُ لَكَ بِذَنْبِي فَاغْفِرْ لِي، فَإِنَّهُ لا يَغْفِرُ الذُّنُوبَ إِلا أَنْتَ",
        "source": "رواه البخاري في صحيحه",
        "virtue": "من قالها موقناً بها حين يصبح فمات من يومه دخل الجنة."
    },
    {
        "title": "دعاء الكرب",
        "text": "لا إِلَهَ إِلا اللَّهُ الْعَظِيمُ الْحَلِيمُ، لا إِلَهَ إِلا اللَّهُ رَبُّ الْعَرْشِ الْعَظِيمِ، لا إِلَهَ إِلا اللَّهُ رَبُّ السَّمَوَاتِ وَرَبُّ الأَرْضِ وَرَبُّ الْعَرْشِ الْكَرِيمِ",
        "source": "متفق عليه (رواه البخاري ومسلم)",
        "virtue": "كان النبي ﷺ يدعو به عند الكرب."
    },
    {
        "title": "آية الكرسي",
        "text": "اللَّهُ لَا إِلَهَ إِلَّا هُوَ الْحَيُّ الْقَيُّومُ لَا تَأْخُذُهُ سِنَةٌ وَلَا نَوْمٌ لَهُ مَا فِي السَّمَاوَاتِ وَمَا فِي الْأَرْضِ مَنْ ذَا الَّذِي يَشْفَعُ عِنْدَهُ إِلَّا بِإِذْنِهِ يَعْلَمُ مَا بَيْنَ أَيْدِيهِمْ وَمَا خَلْفَهُمْ وَلَا يُحِيطُونَ بِشَيْءٍ مِنْ عِلْمِهِ إِلَّا بِمَا شَاءَ وَسِعَ كُرْسِيُّهُ السَّمَاوَاتِ وَالْأَرْضَ وَلَا يَئُودُهُ حِفْظُهُمَا وَهُوَ الْعَلِيُّ الْعَظِيمُ",
        "source": "سورة البقرة - الآية 255",
        "virtue": "من قرأها حين يأوي إلى فراشه لم يزل عليه من الله حافظ، ولا يقربه شيطان حتى يصبح."
    }
]

# ============================================================
# 2. جلب الحديث الموثق من dorar.net
# ============================================================
def fetch_verified_hadith(topic="الصبر"):
    try:
        r = requests.get(
            "https://dorar.net/dorar_api.json",
            params={"skey": topic, "page": 1},
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200: return None
        ahadith = r.json().get("ahadith", {}).get("result", [])
        for h in ahadith:
            grade = h.get("grade", "")
            if "صحيح" in grade or "حسن" in grade:
                text = re.sub(r'<[^>]+>', '', h.get("hadith", ""))
                return {
                    "text": text, "rawi": h.get("rawi", ""), "mohdith": h.get("mohdith", ""),
                    "book": h.get("book", ""), "grade": grade,
                    "attribution": f"رواه {h.get('mohdith', '')} في {h.get('book', '')} عن {h.get('rawi', '')} ({grade})"
                }
    except Exception as e:
        print(f"⚠️ فشل جلب الحديث: {e}")
    return None

# ============================================================
# 3. جلب الفقه الموثق من الملف المحلي
# ============================================================
def fetch_verified_fiqh():
    if not FAQ_JSON.exists(): return None
    try:
        data = json.loads(FAQ_JSON.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            item = random.choice(data)
            return {
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "source": item.get("source", "من الفقه الإسلامي")
            }
    except Exception as e:
        print(f"⚠️ فشل قراءة ملف الفقه: {e}")
    return None

# ============================================================
# 4. دوال مساعدة (نفس محرك القصص)
# ============================================================
def call_llm(prompt, is_json=True, timeout=180, max_tokens=8192):
    pool = get_pool(); breaker = get_breaker()
    for model in discover_and_cache(force=False)[:5]:
        if breaker.is_open(f"model:{model}"): continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens}}
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

def clean_llm_text(text: str) -> str:
    if not text: return ""
    t = text.strip()
    bad_patterns = [r'^\s*إليك\s+', r'^\s*هذا\s+', r'^\s*هذه\s+', r'^\s*النص\s+', r'^\s*نص\s+', r'^\s*بالتأكيد\s*[,،]', r'^\s*طبعاً\s*[,،]']
    for pat in bad_patterns: t = re.sub(pat, '', t, count=1, flags=re.IGNORECASE).strip()
    return t.strip('«»"\'[]')

def apply_forced_tashkeel(text):
    FORCED_TASHKEEL = {"الله": "اللَّه", "لله": "لِلَّه", "بالله": "بِاللَّه", "والله": "وَاللَّه", "الرحمن": "الرَّحْمَن", "الرحيم": "الرَّحِيم"}
    for wrong, right in FORCED_TASHKEEL.items():
        text = re.sub(rf'(?<![ء-ي\u064B-\u0652]){re.escape(wrong)}(?![ء-ي\u064B-\u0652])', right, text)
    return text

def proofread_script(text):
    print("🔍 جاري وضع التشكيل...", flush=True)
    parts = re.split(r'(\[آية: \d+:\d+\])', text)
    corrected_parts = []
    for part in parts:
        if not part.strip(): continue
        if part.startswith("[آية:"):
            corrected_parts.append(part); continue
            
        chunks = []
        current = ""
        for sentence in re.split(r'(?<=[.!؟])\s+', part):
            if len(current) + len(sentence) < 300: current += " " + sentence
            else:
                if current: chunks.append(current.strip())
                current = sentence
        if current: chunks.append(current.strip())
        
        for chunk in chunks:
            prompt = f"أنت مدقق لغوي. ضع التشكيل التام على النص التالي. ممنوع تغيير أي كلمة. أعد النص فقط:\n{chunk}"
            corrected = call_llm(prompt, is_json=False, timeout=60, max_tokens=1500)
            if corrected and len(corrected) > len(chunk) * 0.8:
                corrected_parts.append(apply_forced_tashkeel(clean_llm_text(corrected)))
            else:
                corrected_parts.append(chunk)
    return " ".join(corrected_parts)

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

# ============================================================
# 5. بناء البرومبتات
# ============================================================
def build_hadith_prompt(hadith):
    return f"""أنت راوي محتوى إسلامي قصير.
الحديث الصحيح الذي اخترناه: "{hadith['text']}"
المصدر: {hadith['attribution']}

اكتب سيناريو قصير جداً (لا يتجاوز 100 كلمة) بهيكل:
1. جملة افتتاحية مشوقة.
2. سياق أو شرح مبسط.
3. ضع علامة [النص_الموثق] في المكان الذي تريد أن يُقرأ فيه نص الحديث.
4. جملة ختامية (عبرة).
قواعد: ممنوع اختراع أي شيء. أعد النص فقط."""

def build_fiqh_prompt(fiqh):
    return f"""أنت راوي محتوى إسلامي قصير.
المسألة الفقهية الموثقة: "{fiqh['question']}"
الجواب الموثق: "{fiqh['answer']}"
المصدر: {fiqh['source']}

اكتب سيناريو قصير جداً (لا يتجاوز 100 كلمة) بهيكل:
1. جملة افتتاحية تطرح المشكلة أو السؤال.
2. ضع علامة [النص_الموثق] في المكان الذي تريد أن يُقرأ فيه الجواب الفقهي.
3. جملة ختامية تثبت الفائدة.
قواعد صارمة: **ممنوع تماماً تغيير الفتوى أو إضافة أحكام من عندك**. انقل الجواب كما هو. أعد النص فقط."""

def build_adhkar_prompt(dhikr):
    return f"""أنت راوي محتوى إسلامي قصير.
الذكر الموثق: "{dhikr['title']}" - نصه: "{dhikr['text']}"
الفضل: {dhikr['virtue']}

اكتب سيناريو قصير جداً (لا يتجاوز 80 كلمة) بهيكل:
1. جملة افتتاحية عن فضل هذا الذكر.
2. ضع علامة [النص_الموثق] ليُقرأ الذكر بصوت هادئ.
3. جملة ختامية تحث على المداومة.
أعد النص فقط."""

# ============================================================
# 6. التشغيل الرئيسي
# ============================================================
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ROTATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # تحديد نوع المحتوى اليومي (دوران)
    rotation = {"index": 0}
    if ROTATION_FILE.exists():
        try: rotation = json.loads(ROTATION_FILE.read_text(encoding="utf-8"))
        except: pass
    
    content_type = rotation["index"] % 3  # 0: حديث, 1: فقه, 2: أذكار
    rotation["index"] = (content_type + 1) % 3
    ROTATION_FILE.write_text(json.dumps(rotation), encoding="utf-8")

    data = None
    prompt = ""
    content_title = ""

    if content_type == 0:
        topics = ["الصبر", "بر الوالدين", "الصدق", "الرحمة", "حسن الخلق"]
        data = fetch_verified_hadith(random.choice(topics))
        if data: prompt = build_hadith_prompt(data); content_title = f"حديث شريف عن {data['rawi']}"
    elif content_type == 1:
        data = fetch_verified_fiqh()
        if data: prompt = build_fiqh_prompt(data); content_title = f"فائدة فقهية: {data['question'][:30]}..."
    else:
        data = random.choice(VERIFIED_ADHKAR)
        if data: prompt = build_adhkar_prompt(data); content_title = f"ذكر: {data['title']}"

    if not data:
        print("❌ فشل جلب المحتوى الموثق. إيقاف.")
        sys.exit(1)

    # توليد النص
    print(f"✍️ جاري كتابة سيناريو ({content_title})...", flush=True)
    script_text = call_llm(prompt, is_json=False, timeout=120, max_tokens=1000)
    if not script_text: sys.exit(1)
    
    script_text = proofread_script(script_text)

    # بناء المقاطع واستبدال العلامة
    segments = []
    parts = script_text.split("[النص_الموثق]")
    
    if len(parts) == 2:
        for chunk in split_narration(parts[0]): segments.append({"type": "narration", "text": chunk.strip()})
        # إدراج النص الموثق
        segments.append({"type": "quran", "text": data["text"], "display": data.get("attribution", data.get("source", ""))})
        for chunk in split_narration(parts[1]): segments.append({"type": "narration", "text": chunk.strip()})
    else:
        for chunk in split_narration(script_text): segments.append({"type": "narration", "text": chunk.strip()})
        segments.append({"type": "quran", "text": data["text"], "display": data.get("attribution", data.get("source", ""))})

    # إضافة CTA
    segments.append({"type": "cta", "text": "قال رسول الله ﷺ: «مَن دَلَّ على خيرٍ فله مثلُ أجرِ فاعِلِه». شارك المقطع. ملاك المعرفة.", "visual": "mosque architecture"})

    ep_data = {
        "episode_number": 1, "story_id": "daily",
        "title": content_title, "segments": segments
    }
    
    output_path = OUTPUT_DIR / "daily-latest.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    print(f"✅ تم إنشاء المقطع اليومي: {output_path}", flush=True)

if __name__ == "__main__":
    try: main()
    except Exception as e: traceback.print_exc(); sys.exit(1)
