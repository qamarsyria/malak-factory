import os, re, sys, json, random, traceback
from pathlib import Path
from datetime import datetime
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import notify_telegram, get_breaker

OUTPUT_DIR = _REPO_ROOT / "data" / "daily" / "generated"
OUT_FILE_PATH = _REPO_ROOT / "out" / "latest_episode.txt"
FAQ_JSON = _REPO_ROOT / "data" / "faq-data.json"

# ============================================================
# 1. المصادر الموثقة (المدمجة كاحتياطي في حال حظر السيرفر)
# ============================================================
VERIFIED_HADITHS_FALLBACK = [
    {
        "text": "إِنَّمَا الأَعْمَالُ بِالنِّيَّاتِ، وَإِنَّمَا لِكُلِّ امْرِئٍ مَا نَوَى، فَمَنْ كَانَتْ هِجْرَتُهُ إِلَى دُنْيَا يُصِيبُهَا أَوْ إِلَى امْرَأَةٍ يَنْكِحُهَا فَهِجْرَتُهُ إِلَى مَا هَاجَرَ إِلَيْهِ",
        "rawi": "عمر بن الخطاب",
        "attribution": "رواه البخاري ومسلم (صحيح)"
    },
    {
        "text": "الدِّينُ النَّصِيحَةُ. قُلْنَا: لِمَنْ؟ قَالَ: لِلَّهِ، وَلِكِتَابِهِ، وَلِرَسُولِهِ، وَلِأَئِمَّةِ الْمُسْلِمِينَ وَعَامَّتِهِمْ",
        "rawi": "تميم بن أوس الداري",
        "attribution": "رواه مسلم (صحيح)"
    },
    {
        "text": "مَنْ كَانَ يُؤْمِنُ بِاللَّهِ وَالْيَوْمِ الآخِرِ فَلْيَقُلْ خَيْرًا أَوْ لِيَصْمُتْ، وَمَنْ كَانَ يُؤْمِنُ بِاللَّهِ وَالْيَوْمِ الآخِرِ فَلْيُكْرِمْ جَارَهُ، وَمَنْ كَانَ يُؤْمِنُ بِاللَّهِ وَالْيَوْمِ الآخِرِ فَلْيُكْرِمْ ضَيْفَهُ",
        "rawi": "أبو هريرة",
        "attribution": "رواه البخاري ومسلم (صحيح)"
    }
]

VERIFIED_FIQH_FALLBACK = [
    {
        "question": "ما حكم بر الوالدين في الإسلام؟",
        "answer": "بر الوالدين من أعظم القربات إلى الله تعالى، وهو واجب على كل مسلم. قال الله تعالى: (وَقَضَى رَبُّكَ أَلَّا تَعْبُدُوا إِلَّا إِيَّاهُ وَبِالْوَالِدَيْنِ إِحْسَانًا). ويكون البر بالطاعة في غير معصية، والإنفاق عليهما، وخفض الجناح لهما، والدعاء لهما.",
        "source": "القرآن الكريم والسنة النبوية"
    }
]

MALAK_FALLBACKS = {
    "maryam": ["يا مريم، في زمنٍ قلّ فيه الصفاء، أراد الله أن يطمئن قلبي، فخلق طهراً يسير بين البشر. اللهم احفظها لي بحفظك."],
    "malak": ["يقولون إن الملائكة خلقٌ لا نراه، لكنني أراه كل يوم في ملك. في نقائها، وسكينتها، ونورها."],
    "malaak": ["يا ملاك، اسمكِ النادر كجوهرة في قلب المحبين. اللهم كما جعلت اسمكِ فريداً، اجعل نصيبكِ من السكينة أوفر النصيب."]
}

# ============================================================
# 2. دوال الجلب
# ============================================================
def fetch_verified_hadith(topic="الصبر"):
    try:
        r = requests.get("https://dorar.net/dorar_api.json", params={"skey": topic, "page": 1}, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            for h in r.json().get("ahadith", {}).get("result", []):
                if "صحيح" in h.get("grade", "") or "حسن" in h.get("grade", ""):
                    return {
                        "text": re.sub(r'<[^>]+>', '', h.get("hadith", "")), "rawi": h.get("rawi", ""),
                        "attribution": f"رواه {h.get('mohdith', '')} في {h.get('book', '')} ({h.get('grade', '')})"
                    }
        print(f"⚠️ dorar.net أعاد HTTP {r.status_code}.")
    except Exception as e:
        print(f"⚠️ فشل الاتصال بـ dorar.net ({e}).")
    
    h = random.choice(VERIFIED_HADITHS_FALLBACK)
    print(f"✅ استخدام حديث احتياطي موثق ({h['rawi']})")
    return h

def fetch_verified_fiqh():
    if FAQ_JSON.exists():
        try:
            data = json.loads(FAQ_JSON.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                item = random.choice(data)
                return {"question": item.get("question", ""), "answer": item.get("answer", ""), "source": item.get("source", "")}
        except: pass
    
    print("⚠️ ملف الفقه غير موجود أو فارغ. سيتم استخدام الفقه الاحتياطي.")
    return random.choice(VERIFIED_FIQH_FALLBACK)

def fetch_ayah_and_tafsir():
    surah, ayah = random.choice([(2, 255), (24, 35), (18, 10), (2, 286)])
    try:
        url = f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah}/ar.muyassar"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()["data"]
            return {"text": data["text"], "tafsir": data.get("tafsir", ""), "attribution": f"سورة {data['surah']['name']} - الآية {ayah}"}
    except Exception as e:
        print(f"⚠️ فشل جلب الآية: {e}")
    return None

# ============================================================
# 3. دوال مساعدة
# ============================================================
def call_llm(prompt, is_json=True, timeout=45, max_tokens=8192):
    pool = get_pool(); breaker = get_breaker()
    models = discover_and_cache(force=False)[:3]
    if not models: print("   ❌ لا توجد نماذج متاحة!"); return None

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
                elif r.status_code == 429: pool.mark_failed(3600)
                elif r.status_code in (404, 503): breaker.record_failure(f"model:{model}"); break
            except requests.exceptions.Timeout: pass
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
            corrected = call_llm(prompt, is_json=False, timeout=30, max_tokens=1000)
            if corrected and len(corrected) > len(chunk) * 0.7:
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

# ============================================================
# 4. البرومبتات
# ============================================================
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
    if name_type == "maryam": focus = "الاسم: مريم. ركز على الطهر، السكينة، البراءة."
    elif name_type == "malak": focus = "الاسم: ملك (مفرد ملائكة). ركز على النور، الحماية، النقاء."
    else: focus = "الاسم: ملاك. ركز على الندرة، الجمال الروحي الخفي."
    return f"""أنت شاعر صوفي رومانسي محترم، تكتب همسات روحانية.
{focus}
اكتب مقطعاً غزلياً عفيفاً جداً (50 كلمة)، بطابع ديني راقٍ.
ادعُ الله أن يحفظها ويجمعك بها على طاعة.
ممنوع تماماً ذكر أي اسم آخر غير الاسم المطلوب.
ضع علامة [النص_الموثق] في بداية الهمسة. أعد النص فقط."""

# ============================================================
# 5. التشغيل الرئيسي
# ============================================================
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 🌟 نظام الدوران الجديد: يعتمد على تاريخ اليوم (لن يعيد المقطع الأول أبداً)
    day_of_epoch = (datetime.now() - datetime(2024, 1, 1)).days
    content_type = day_of_epoch % 6  
    
    print(f"📅 اليوم رقم {day_of_epoch} من الدورة. نوع المحتوى المختار: {content_type}", flush=True)

    data, prompt, content_title, segment_type = None, "", "", ""
    name_type = ""

    if content_type == 0:
        topics = ["الصبر", "بر الوالدين", "الصدق", "الرحمة", "حسن الخلق"]
        data = fetch_verified_hadith(random.choice(topics))
        if data: prompt = build_hadith_prompt(data); content_title = f"حديث: {data['rawi']}"; segment_type = "hadith"
    elif content_type == 1:
        data = fetch_verified_fiqh()
        if data: prompt = build_fiqh_prompt(data); content_title = "فائدة فقهية"; segment_type = "fiqh"
    elif content_type == 2:
        data = fetch_ayah_and_tafsir()
        if data: prompt = build_ayah_story_prompt(data); content_title = "آية وحكاية"; segment_type = "quran"
    elif content_type == 3:
        name_type = "maryam"; prompt = build_malak_prompt(name_type); content_title = "همسات لمريم"; segment_type = "malak"
    elif content_type == 4:
        name_type = "malak"; prompt = build_malak_prompt(name_type); content_title = "همسات لملك"; segment_type = "malak"
    else:
        name_type = "malaak"; prompt = build_malak_prompt(name_type); content_title = "همسات لملاك"; segment_type = "malak"

    if content_type < 3 and not data:
        print("❌ فشل جلب المحتوى الموثق الأساسي. إيقاف.")
        sys.exit(1)

    print(f"✍️ جاري كتابة سيناريو ({content_title})...", flush=True)
    script_text = call_llm(prompt, is_json=False, timeout=60, max_tokens=1000)
    
    if not script_text:
        print("⚠️ فشل توليد السيناريو عبر Gemini. استخدام السيناريو الاحتياطي.", flush=True)
        if content_type >= 3:
            script_text = "[النص_الموثق] " + random.choice(MALAK_FALLBACKS[name_type])
        elif content_type == 0:
            script_text = f"في هذا المقطع، نتوقف مع حديث عظيم يضيء لنا الطريق. [النص_الموثق]"
        elif content_type == 1:
            script_text = f"كثيراً ما يبحث المسلم عن إجابة شافية. [النص_الموثق]"
        else:
            script_text = f"في آيات القرآن الكريم، توجد أسرار وعبر. [النص_الموثق]"
    
    script_text = proofread_script(script_text)

    segments = []
    parts = script_text.split("[النص_الموثق]")
    if len(parts) == 1: parts = script_text.split("[النص_موثق]")

    if len(parts) == 2:
        for chunk in split_narration(parts[0]): segments.append({"type": "narration", "text": chunk.strip()})
        
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
