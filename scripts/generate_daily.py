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

# ------------------------------------------------------------
# 1. استيراد الحديث الموثق من dorar.net
# ------------------------------------------------------------
def fetch_verified_hadith(topic="الصبر"):
    """يجلب حديثاً من dorar.net ويتأكد أنه صحيح أو حسن."""
    print(f"🔎 جاري البحث عن حديث صحيح/حسن في موضوع: {topic}...")
    try:
        # نطلب 5 أحاديث لزيادة فرصة إيجاد حديث صحيح
        r = requests.get(
            "https://dorar.net/dorar_api.json",
            params={"skey": topic, "page": 1},
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            return None
        
        data = r.json()
        ahadith = data.get("ahadith", {}).get("result", [])
        
        for h in ahadith:
            grade = h.get("grade", "")
            # نتحقق من أن الحديث صحيح أو حسن
            if "صحيح" in grade or "حسن" in grade:
                text = re.sub(r'<[^>]+>', '', h.get("hadith", "")) # إزالة وسوم HTML
                rawi = h.get("rawi", "")
                mohdith = h.get("mohdith", "")
                book = h.get("book", "")
                
                print(f"✅ تم العثور على حديث ({grade}) رواه {rawi}")
                return {
                    "text": text,
                    "rawi": rawi,
                    "mohdith": mohdith,
                    "book": book,
                    "grade": grade,
                    "attribution": f"رواه {mohdith} في {book} عن {rawi} ({grade})"
                }
    except Exception as e:
        print(f"⚠️ فشل جلب الحديث: {e}")
    return None

# ------------------------------------------------------------
# 2. دوال مساعدة (نفس محرك القصص)
# ------------------------------------------------------------
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
    bad_patterns = [
        r'^\s*إليك\s+', r'^\s*هذا\s+', r'^\s*هذه\s+', r'^\s*النص\s+', r'^\s*نص\s+',
        r'^\s*الحلقة\s+القادمة\s*[:：\-]', r'^\s*في\s+الحلقة\s+القادمة\s*[,،:]',
        r'^\s*ترقبوا\s*[:：]?', r'^\s*بالتأكيد\s*[,،]', r'^\s*طبعاً\s*[,،]', r'^\s*الخلاصة\s*[:：]',
    ]
    for pat in bad_patterns: t = re.sub(pat, '', t, count=1, flags=re.IGNORECASE).strip()
    t = t.strip('«»"\'[]')
    return t

def apply_forced_tashkeel(text):
    FORCED_TASHKEEL = {
        "الله": "اللَّه", "لله": "لِلَّه", "بالله": "بِاللَّه", "والله": "وَاللَّه",
        "الرحمن": "الرَّحْمَن", "الرحيم": "الرَّحِيم", "إبراهيم": "إِبْرَاهِيم",
    }
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
            prompt = f"""أنت مدقق لغوي عربي خبير.
مهمتك: وضع التشكيل التام على النص التالي.
قواعد: ممنوع تغيير أو حذف أي كلمة، أعد النص المشكّل فقط.
النص:
{chunk}"""
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

# ------------------------------------------------------------
# 3. توليد السيناريو من الحديث
# ------------------------------------------------------------
def build_script_prompt(hadith_data):
    return f"""أنت راوي محتوى إسلامي قصير (Short).
لقد اخترنا هذا الحديث الصحيح:
"{hadith_data['text']}"
المصدر: {hadith_data['attribution']}

اكتب سيناريو قصير جداً (لا يتجاوز 100 كلمة) بهيكل:
1. جملة افتتاحية مشوقة (Hook).
2. سياق أو شرح مبسط للحديث.
3. ثم ضع علامة [حديث] في المكان الذي تريد أن يُقرأ فيه نص الحديث.
4. جملة ختامية (Takeaway).

قواعد صارمة:
- لا تخترع أحداثاً أو معلومات غير موجودة.
- اكتب بأسلوب روائي متصل، بدون عناوين.
- أعد النص فقط."""

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. جلب حديث صحيح
    topics = ["الصبر", "بر الوالدين", "الصدق", "الرحمة", "ذكر الله", "حسن الخلق"]
    hadith = fetch_verified_hadith(random.choice(topics))
    if not hadith:
        print("❌ فشل جلب حديث صحيح. إيقاف.")
        sys.exit(1)

    # 2. توليد السرد (Text)
    print("✍️ جاري كتابة السيناريو...", flush=True)
    script_text = call_llm(build_script_prompt(hadith), is_json=False, timeout=120, max_tokens=1000)
    if not script_text:
        print("❌ فشل التوليد.")
        sys.exit(1)
    
    # 3. التشكيل
    script_text = proofread_script(script_text)

    # 4. استبدال علامة [حديث] بالنص الفعلي المشكّل
    # (هنا سنفترض أن النص القادم من dorar.net مشكّل أصلاً، أو نتركه لـ Gemini)
    # سنضع الحديث كـ segment منفصل بصوت مختلف
    segments = []
    
    # تقسيم السرد إلى قطع
    parts = script_text.split("[حديث]")
    
    if len(parts) == 2:
        # ما قبل الحديث
        for chunk in split_narration(parts[0]): 
            segments.append({"type": "narration", "text": chunk.strip()})
            
        # الحديث
        segments.append({
            "type": "hadith", 
            "text": hadith['text'], 
            "display": hadith['attribution']
        })
        
        # ما بعد الحديث
        for chunk in split_narration(parts[1]): 
            segments.append({"type": "narration", "text": chunk.strip()})
    else:
        # إذا فشل النموذج في وضع العلامة، نضع الحديث في النهاية
        for chunk in split_narration(script_text): 
            segments.append({"type": "narration", "text": chunk.strip()})
        segments.append({
            "type": "hadith", 
            "text": hadith['text'], 
            "display": hadith['attribution']
        })

    # إضافة CTA
    segments.append({
        "type": "cta", 
        "text": "قال رسول الله ﷺ: «مَن دَلَّ على خيرٍ فله مثلُ أجرِ فاعِلِه». شارك المقطع مع من تحب. ملاك المعرفة.", 
        "visual": "mosque architecture"
    })

    ep_data = {
        "episode_number": 1, 
        "story_id": "daily",
        "title": f"مقطع يومي - {hadith['rawi']}", 
        "segments": segments
    }
    
    output_path = OUTPUT_DIR / "daily-latest.json"
    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE_PATH.write_text(str(output_path), encoding="utf-8")
    print(f"✅ تم إنشاء المقطع اليومي: {output_path}", flush=True)

if __name__ == "__main__":
    try: main()
    except Exception as e: traceback.print_exc(); sys.exit(1)
