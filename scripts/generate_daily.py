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

# ------------------------------------------------------------
# 1. النصوص الموثقة والاحتياطية
# ------------------------------------------------------------
VERIFIED_ADHKAR = [
    {"title": "سيد الاستغفار", "text": "اللَّهُمَّ أَنْتَ رَبِّي لا إِلَهَ إِلا أَنْتَ...", "source": "رواه البخاري"}
]

MALAK_FALLBACKS = {
    "maryam": [
        "يا مريم، في زمنٍ قلّ فيه الصفاء، أراد الله أن يطمئن قلبي، فخلق طهراً يسير بين البشر. اللهم احفظها لي بحفظك.",
    ],
    "malak": [
        "يقولون إن الملائكة خلقٌ لا نراه، لكنني أراه كل يوم في ملك. في نقائها، وسكينتها، ونورها.",
    ],
    "malaak": [
        "يا ملاك، اسمكِ النادر كجوهرة في قلب المحبين. اللهم كما جعلت اسمكِ فريداً، اجعل نصيبكِ من السكينة أوفر النصيب.",
    ]
}

def fetch_verified_hadith(topic="الصبر"):
    try:
        r = requests.get("https://dorar.net/dorar_api.json", params={"skey": topic, "page": 1}, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            for h in r.json().get("ahadith", {}).get("result", []):
                if "صحيح" in h.get("grade", "") or "حسن" in h.get("grade", ""):
                    return {
                        "text": re.sub(r'<[^>]+>', '', h.get("hadith", "")), "rawi": h.get("rawi", ""),
                        "attribution": f"رواه {h.get('mohdith', '')} في {h.get('book', '')} ({h.get('grade', '')})"
                    }
    except: pass
    return None

def fetch_verified_fiqh():
    if FAQ_JSON.exists():
        try:
            data = json.loads(FAQ_JSON.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                item = random.choice(data)
                return {"question": item.get("question", ""), "answer": item.get("answer", ""), "source": item.get("source", "")}
        except: pass
    return None

def fetch_ayah_and_tafsir():
    surah, ayah = random.choice([(2, 255), (24, 35), (18, 10), (2, 286)])
    try:
        url = f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah}/ar.muyassar"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()["data"]
            return {"text": data["text"], "tafsir": data.get("tafsir", ""), "attribution": f"سورة {data['surah']['name']} - الآية {ayah}"}
    except: pass
    return None

# ------------------------------------------------------------
# 2. دوال مساعدة
# ------------------------------------------------------------
def call_llm(prompt, is_json=True, timeout=180, max_tokens=8192):
    pool = get_pool(); breaker = get_breaker()
    for model in discover_and_cache(force=False)[:5]:
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
                elif r.status_code == 429: pool.mark_failed(3600)
                elif r.status_code in (404, 503): breaker.record_failure(f"model:{model}"); break
            except Exception: pass
    return None

def clean_llm_text(text: str) -> str:
    if not text: return ""
    t = text.strip()
    for pat in [r'^\s*إليك\s+', r'^\s*هذا\s+', r'^\s*هذه\s+', r'^\s*بالتأكيد\s*[,،]']:
        t = re.sub(pat, '', t, count=1, flags=re.IGNORECASE).strip()
    return t.strip('«»"\'[]')

def apply_forced_tashkeel(text):
    for wrong, right in {"الله": "اللَّه", "الرحمن": "الرَّحْمَن"}.items():
        text = re.sub(rf'(?<![ء-ي\u064B-\u0652]){re.escape(wrong)}(?![ء-ي\u064B-\u0652])', right, text)
    return text

def proofread_script(text):
    parts = re.split(r'(\[آية: \d+:\d+\])', text)
    corrected_parts = []
    for part in parts:
        if not part.strip(): continue
        if part.startswith("[آية:"): corrected_parts.append(part); continue
        chunks = []; current = ""
        for sentence in re.split(r'(?<=[.!؟])\s+', part):
            if len(current) + len(sentence) < 300: current += " " + sentence
            else:
                if current: chunks.append(current.strip())
                current = sentence
        if current: chunks.append(current.strip())
        for chunk in chunks:
            prompt = f"أنت مدقق لغوي. ضع التشكيل التام على النص. ممنوع تغيير أي كلمة. أعد النص فقط:\n{chunk}"
            corrected = call_llm(prompt, is_json=False, timeout=60, max_tokens=1500)
            if corrected and len(corrected) > len(chunk) * 0.8:
                corrected_parts.append(apply_forced_tashkeel(clean_llm_text(corrected)))
            else: corrected_parts.append(chunk)
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

# ------------------------------------------------------------
# 3. البرومبتات
# ------------------------------------------------------------
def build_hadith_prompt(hadith):
    return f"""أنت راوي محتوى إسلامي قصير. الحديث: "{hadith['text']}" (المصدر: {hadith['attribution']}).
اكتب سيناريو قصير (100 كلمة) بهيكل:
1. مقدمة مشوقة. 2. شرح مبسط. 3. ضع علامة [النص_الموثق] ليُقرأ الحديث. 4. عبرة.
ممنوع اختراع أي شيء. أعد النص فقط."""

def build_fiqh_prompt(fiqh):
    return f"""أنت راوي محتوى إسلامي قصير. المسألة: "{fiqh['question']}"، الجواب: "{fiqh['answer']}" (المصدر: {fiqh['source']}).
اكتب سيناريو قصير (100 كلمة) بهيكل:
1. طرح السؤال. 2. ضع علامة [النص_الموثق] للجواب. 3. فائدة ختامية.
ممنوع تغيير الفتوى. أعد النص فقط."""

def build_ayah_story_prompt(ayah):
    return f"""أنت راوي قصصي. الآية: "{ayah['text']}"، التفسير الميسر: "{ayah['tafsir']}".
اكتب سيناريو قصير (100 كلمة) يحكي القصة أو السياق بناءً على التفسير فقط.
ضع علامة [النص_الموثق] ليُقرأ النص القرآني.
ممنوع الخروج عن التفسير. أعد النص فقط."""

def build_malak_prompt(name_type):
    if name_type == "maryam":
        focus = "الاسم: مريم. ركز في همستك على الطهر، السكينة، البراءة، والاقتداء بالسيدة مريم عليها السلام."
    elif name_type == "malak":
        focus = "الاسم: ملك (بمعنى المَلَك، مفرد الملائكة). ركز في همستك على النور، الحماية، النقاء، والسمو الروحي."
    else: # malaak
        focus = "الاسم: ملاك (الاسم التقليدي النادر). ركز في همستك على الندرة، الجمال الروحي الخفي، والخصوصية."

    return f"""أنت شاعر صوفي رومانسي محترم، تكتب همسات روحانية.
{focus}
اكتب مقطعاً غزلياً عفيفاً جداً (50 كلمة)، بطابع ديني راقٍ.
تحدث عن صفات هذا الاسم، وادعُ الله أن يحفظها ويجمعك بها على طاعة.
الأسلوب: دعاء، همسة، وخاطرة تلامس القلب.
قاعدة صارمة: **ممنوع تماماً ذكر أي اسم آخر غير الاسم المطلوب**. المقطع يجب أن يكون مخصصاً لهذا الاسم فقط.
ضع علامة [النص_الموثق] في بداية الهمسة. أعد النص فقط."""

# ------------------------------------------------------------
# 4. التشغيل الرئيسي
# ------------------------------------------------------------
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ROTATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    rotation = {"index": 0}
    if ROTATION_FILE.exists():
        try: rotation = json.loads(ROTATION_FILE.read_text(encoding="utf-8"))
        except: pass
    
    # دورة من 6 أيام
    content_type = rotation["index"] % 6  
    rotation["index"] = (content_type + 1) % 6
    ROTATION_FILE.write_text(json.dumps(rotation), encoding="utf-8")

    data, prompt, content_title, segment_type = None, "", "", ""
    name_type = ""

    if content_type == 0:
        topics = ["الصبر", "بر الوالدين", "الصدق", "الرحمة", "حسن الخلق"]
        data = fetch_verified_hadith(random.choice(topics))
        if data: prompt = build_hadith_prompt(data); content_title = f"حديث: {data['rawi']}"; segment_type = "quran"
    elif content_type == 1:
        data = fetch_verified_fiqh()
        if data: prompt = build_fiqh_prompt(data); content_title = "فائدة فقهية"; segment_type = "quran"
    elif content_type == 2:
        data = fetch_ayah_and_tafsir()
        if data: prompt = build_ayah_story_prompt(data); content_title = "آية وحكاية"; segment_type = "quran"
    elif content_type == 3:
        name_type = "maryam"
        prompt = build_malak_prompt(name_type)
        content_title = "همسات لمريم"
        segment_type = "malak"
    elif content_type == 4:
        name_type = "malak"
        prompt = build_malak_prompt(name_type)
        content_title = "همسات لملك"
        segment_type = "malak"
    else:
        name_type = "malaak"
        prompt = build_malak_prompt(name_type)
        content_title = "همسات لملاك"
        segment_type = "malak"

    if content_type < 3 and not data:
        print("❌ فشل جلب المحتوى الموثق. إيقاف.")
        sys.exit(1)

    print(f"✍️ جاري كتابة سيناريو ({content_title})...", flush=True)
    script_text = call_llm(prompt, is_json=False, timeout=120, max_tokens=1000)
    
    if not script_text:
        if content_type >= 3: script_text = "[النص_الموثق] " + random.choice(MALAK_FALLBACKS[name_type])
        else: sys.exit(1)
    
    script_text = proofread_script(script_text)

    segments = []
    parts = script_text.split("[النص_موثق]")
    if len(parts) == 1:
         parts = script_text.split("[النص_الموثق]") # Just in case

    if len(parts) == 2:
        for chunk in split_narration(parts[0]): segments.append({"type": "narration", "text": chunk.strip()})
        
        # إدراج النص الموثق
        if content_type >= 3:
            segments.append({"type": "malak", "text": parts[1].strip(), "display": content_title})
        else:
            segments.append({"type": segment_type, "text": data["text"], "display": data.get("attribution", data.get("source", ""))})
            
        if content_type < 3 and len(parts) > 1:
            for chunk in split_narration(parts[1]): segments.append({"type": "narration", "text": chunk.strip()})
    else:
        if content_type >= 3:
            segments.append({"type": "malak", "text": script_text.strip(), "display": content_title})
        else:
            for chunk in split_narration(script_text): segments.append({"type": "narration", "text": chunk.strip()})
            segments.append({"type": segment_type, "text": data["text"], "display": data.get("attribution", data.get("source", ""))})

    segments.append({"type": "cta", "text": "قال رسول الله ﷺ: «مَن دَلَّ على خيرٍ فله مثلُ أجرِ فاعِلِه». شارك المقطع. ملاك المعرفة.", "visual": "mosque architecture"})

    ep_data = {"episode_number": 1, "story_id": "daily", "title": content_title, "segments": segments}
    
    output_path = OUTPUT_DIR / "daily-latest.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    print(f"✅ تم إنشاء المقطع اليومي: {output_path}", flush=True)

if __name__ == "__main__":
    try: main()
    except Exception as e: traceback.print_exc(); sys.exit(1)
