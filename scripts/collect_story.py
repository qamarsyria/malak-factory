"""
collect_story.py — المرحلة 1: جمع المصادر (نظام ذكي ومقاوم للأخطاء)
يتميز بـ:
  1. محرك تطبيع عربي للبحث الدقيق (يتجاهل التشكيل والهمزات).
  2. نظام تقييم الكلمات المفتاحية (قوية/ضعيفة) لمنع سحب آيات عشوائية.
  3. تقسيم أحداث ذكي يعتمد على المراجع (سورة:آية) بدلاً من الأرقام.
  4. استخراج سرد من 3 مصادر مع التعامل مع كل أشكال الـ API.
"""

import os, re, sys, json, gzip, time
from pathlib import Path
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import get_breaker, notify_telegram
from filters_loader import get_llm_forbidden_rules

# ═══════════════════════════════════════════════════════════
# المسارات والإعدادات
# ═══════════════════════════════════════════════════════════
STORIES_JSON       = _REPO_ROOT / "data" / "library" / "stories.json"
LIBRARIES_DIR      = _REPO_ROOT / "data" / "libraries"
AYAH_LIBRARY       = LIBRARIES_DIR / "ayah-library.json.gz"
STORIES_OUTPUT     = _REPO_ROOT / "data" / "stories"

TAFSIR_IBN_KATHIR_API = "https://quranapi.misryoum.com/api/tafsir/ibnkathir/surah/{surah}/ayah/{ayah}"
ISLAMWEB_STORIES_URL = "https://www.islamweb.net/ar/library/index.php?page=bookcontents&idfrom=1&idto=272&bk_no=86&ID=2"

# ═══════════════════════════════════════════════════════════
# قاعدة الكلمات المفتاحية الذكية (قوية = تكفي وحدها | ضعيفة = تحتاج دعم)
# ═══════════════════════════════════════════════════════════
STORY_KEYWORDS = {
    "creation-pen": {
        "strong": ["القلم", "نون", "خلق القلم", "الكتابة", "اللوح"],
        "weak": ["الأول", "خلق", "علم", "كتب", "سطر"]
    },
    "nuh": {
        "strong": ["نوح", "الطوفان", "السفينة", "أغرقنا"],
        "weak": ["قوم", "آية", "الأرض", "الجبل", "الماء"]
    },
    "musa": {
        "strong": ["موسى", "فرعون", "هارون", "التوراة", "آسية", "السامري", "آياتنا"],
        "weak": ["اليم", "العصا", "بني إسرائيل", "السحرة", "الطور", "البحر"]
    },
    "yusuf": {
        "strong": ["يوسف", "زليخة", "بنيامين", "الصديق", "العزيز"],
        "weak": ["الجب", "القميص", "الرؤيا", "السجن", "الملك", "السنين"]
    },
    "ibrahim": {
        "strong": ["إبراهيم", "الخليل", "النمرود"],
        "weak": ["النار", "الأصنام", "الكعبة", "إسماعيل", "الذبح", "الفداء"]
    },
    "muhammad": {
        "strong": ["محمد", "بدر", "أحد", "الخندق", "الهجرة", "فتح مكة", "الرسول"],
        "weak": ["المدينة", "مكة", "الكفار", "المشركين", "المسجد", "غزوة"]
    }
}

# ═══════════════════════════════════════════════════════════
# [1] محرك التطبيع العربي (لضمان دقة البحث)
# ═══════════════════════════════════════════════════════════
def normalize_arabic(text: str) -> str:
    """إزالة التشكيل وتوحيد الحروف لضمان مطابقة دقيقة"""
    if not text: return ""
    text = re.sub(r'[\u064B-\u065F\u0670]', '', text)  # إزالة التشكيل
    text = re.sub(r'[أإآ]', 'ا', text)                 # توحيد الألف
    text = re.sub(r'ة', 'ه', text)                     # توحيد التاء المربوطة
    text = re.sub(r'ى', 'ي', text)                     # توحيد الياء
    text = re.sub(r'ـ', '', text)                      # إزالة التطويل
    return text

# ═══════════════════════════════════════════════════════════
# [2] نظام البحث والتقييم الذكي
# ═══════════════════════════════════════════════════════════
def find_story_ayahs(ayah_lib: list, story_id: str) -> list:
    rules = STORY_KEYWORDS.get(story_id, {"strong": [story_id], "weak": []})
    strong_kws = [normalize_arabic(k) for k in rules["strong"]]
    weak_kws = [normalize_arabic(k) for k in rules["weak"]]

    found = []
    for ayah in ayah_lib:
        combined = normalize_arabic(ayah.get("text", "") + " " + ayah.get("tafsir", ""))
        
        strong_score = sum(1 for kw in strong_kws if kw in combined)
        weak_score = sum(1 for kw in weak_kws if kw in combined)

        if strong_score >= 1 or weak_score >= 3:
            ayah = normalize_ayah(ayah)
            found.append(ayah)

    print(f"   🧠 وُجدت {len(found)} آية متعلقة بـ '{story_id}' (بعد التصفية الذكية)")
    return found

def normalize_ayah(ayah: dict) -> dict:
    if "ref" not in ayah:
        s = ayah.get("surah", ayah.get("surah_number", ayah.get("surah_id", "")))
        a = ayah.get("ayah", ayah.get("ayah_number", ayah.get("numberInSurah", ayah.get("number", ""))))
        ayah["ref"] = f"{s}:{a}"
    if "surah_name" not in ayah:
        ayah["surah_name"] = ayah.get("surah_name_ar", f"سورة {ayah.get('surah', '')}")
    if "text" not in ayah:
        ayah["text"] = ayah.get("arabic_text", "")
    return ayah

# ═══════════════════════════════════════════════════════════
# [3] دوال LLM والقراءة
# ═══════════════════════════════════════════════════════════
def call_llm(prompt: str, is_json: bool = True, timeout: int = 180, max_tokens: int = 8192):
    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:5]
    if not models: return None
    for model in models:
        if breaker.is_open(f"model:{model}"): continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens}
                }
                if is_json: body["generationConfig"]["responseMimeType"] = "application/json"
                r = requests.post(url, json=body, timeout=timeout)
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts: break
                    text = parts[0].get("text", "")
                    if is_json:
                        s, e = text.find("{"), text.rfind("}")
                        if s == -1 or e <= s: break
                        text = text[s:e+1]
                    try:
                        parsed = json.loads(text) if is_json else text
                        if hasattr(pool, '_advance'): pool._advance()
                        elif hasattr(pool, 'advance'): pool.advance()
                        breaker.record_success(f"model:{model}")
                        return parsed
                    except json.JSONDecodeError: break
                elif r.status_code == 429: pool.mark_failed(3600)
                elif r.status_code in (404, 503): breaker.record_failure(f"model:{model}"); break
            except Exception: pass
    return None

def load_stories_library() -> dict:
    return json.loads(STORIES_JSON.read_text(encoding="utf-8"))

def load_ayah_library() -> list:
    with gzip.open(AYAH_LIBRARY, "rt", encoding="utf-8") as f:
        return json.load(f)

# ═══════════════════════════════════════════════════════════
# [4] جلب السرد من 3 مصادر (مقاوم للأخطاء)
# ═══════════════════════════════════════════════════════════
def fetch_tafsir_ibn_kathir(ayah_ref: str) -> str:
    try:
        surah, ayah = ayah_ref.split(":")
        url = TAFSIR_IBN_KATHIR_API.format(surah=surah, ayah=ayah)
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            # [إصلاح ذكي] التعامل مع الاستجابة أياً كان شكلها
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            if isinstance(data, dict):
                text = data.get("text") or data.get("tafsir") or (data.get("data", {}) or {}).get("text", "")
                if text and len(str(text).strip()) > 50:
                    return str(text).strip()
    except Exception:
        pass
    return ""

def fetch_tafsir_muyassar(ayahs: list) -> list:
    blocks = []
    for ayah in ayahs:
        tafsir = (ayah.get("tafsir") or "").strip()
        if tafsir and len(tafsir) > 50:
            blocks.append({
                "source": f"التفسير الميسر - {ayah.get('ref', '')}",
                "text": tafsir,
                "related_ayah": ayah.get("ref", "")
            })
    print(f"   📚 التفسير الميسر: {len(blocks)} block")
    return blocks

def fetch_islamweb_stories() -> list:
    blocks = []
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(ISLAMWEB_STORIES_URL, headers=headers, timeout=30)
        if r.status_code == 200:
            soup = BeautifulSoup(r.content, "html.parser")
            body = soup.select_one("#pagebody") or soup.find("div", {"class": "content"}) or soup
            current_text = []
            for p in body.find_all(["p", "div"]):
                txt = p.get_text(strip=True)
                if len(txt) > 100 and not re.search(r'[<>{}]', txt):
                    current_text.append(txt)
                    if len(" ".join(current_text)) > 3000:
                        blocks.append({"source": "إسلام ويب", "text": " ".join(current_text)})
                        current_text = []
            if current_text: blocks.append({"source": "إسلام ويب", "text": " ".join(current_text)})
        print(f"   📚 إسلام ويب: {len(blocks)} block")
    except Exception as e:
        print(f"   ⚠️ إسلام ويب فشل: {e}")
    return blocks

# ═══════════════════════════════════════════════════════════
# [5] التقسيم الذكي (بناءً على المراجع وليس الأرقام)
# ═══════════════════════════════════════════════════════════
def detect_events_and_separate(ayahs: list, narrative_texts: list, story_title: str) -> dict:
    ayahs = [normalize_ayah(a) for a in ayahs]
    
    ayahs_block = "\n".join([f"[{a['ref']}] {a['text']}" for a in ayahs])
    # [إصلاح ذكي] بناخد النص من القاموس بأمان
    narrative_block = "\n\n".join([f"--- Block {i} ---\n{t.get('text', '')[:800]}" for i, t in enumerate(narrative_texts)])

    prompt = f"""أنت مؤرخ إسلامي خبير. لديك قصة "{story_title}".
الآيات المتاحة (استخدم المرجع [سورة:آية] لتحديدها):
{ayahs_block}

النصوص السردية المتاحة (استخدم رقم الـ Block لتحديدها):
{narrative_block}

المطلوب:
1. قسم القصة إلى أحداث كبرى متسلسلة زمنياً.
2. لكل حدث، حدد الآيات التابعة له باستخدام مراجعها الدقيقة (مثال: ["2:30", "2:31"]).
3. لكل حدث، حدد أرقام الـ Blocks السردية التابعة له (مثال: [0, 1]).

أعد JSON فقط بهذا الشكل (بدون أي كلام إضافي):
{{
  "events": [
    {{
      "event_id": "event_01",
      "title": "عنوان الحدث",
      "ayah_refs": ["سورة:آية", "سورة:آية"],
      "narrative_ids": [0, 1]
    }}
  ]
}}"""

    result = call_llm(prompt, is_json=True, timeout=180)
    
    if result and isinstance(result, dict) and "events" in result:
        for ev in result["events"]:
            refs = ev.get("ayah_refs", [])
            ev_ayahs = [a for a in ayahs if a["ref"] in refs]
            ev["resolved_ayahs"] = ev_ayahs
            
            ids = ev.get("narrative_ids", [])
            ev_narr = [narrative_texts[i] for i in ids if 0 <= i < len(narrative_texts)]
            ev["resolved_narrative"] = ev_narr
        return result

    print("   ⚠️ فشل التقسيم الذكي، سيتم وضع كل شيء في حدث واحد.")
    return {
        "events": [{
            "event_id": "event_01",
            "title": story_title,
            "resolved_ayahs": ayahs,
            "resolved_narrative": narrative_texts
        }]
    }

# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def main():
    if len(sys.argv) < 2:
        print("الاستخدام: python collect_story.py <story_id>")
        sys.exit(1)

    story_id = sys.argv[1]
    print("=" * 60)
    print(f"📚 جمع المصادر الذكي: {story_id}")
    print("=" * 60)

    library = load_stories_library()
    story = next((s for s in library["stories"] if s["id"] == story_id), None)
    if not story:
        print(f"❌ القصة '{story_id}' غير موجودة.")
        sys.exit(1)

    story_title = story.get("title", story_id)
    collected_dir = STORIES_OUTPUT / story_id / "collected"
    collected_dir.mkdir(parents=True, exist_ok=True)

    print("\n🔍 البحث الذكي في القرآن والتفسير...")
    ayah_lib = load_ayah_library()
    ayahs = find_story_ayahs(ayah_lib, story_id)
    
    if not ayahs:
        print("   ❌ لم يتم العثور على أي آية. تأكد من كلمات القصة في STORY_KEYWORDS")
        sys.exit(1)

    print("\n📝 جمع السرد من المصادر الثلاثة...")
    narrative_texts = []

    muyassar_blocks = fetch_tafsir_muyassar(ayahs)
    narrative_texts.extend(muyassar_blocks)

    print("   📚 جلب تفسير ابن كثير...")
    for ayah in ayahs[:5]:
        ref = ayah.get("ref", "")
        kathir_text = fetch_tafsir_ibn_kathir(ref)
        if kathir_text:
            narrative_texts.append({"source": f"ابن كثير - {ref}", "text": kathir_text})
        time.sleep(0.3)
    print(f"   📚 ابن كثير: {len([b for b in narrative_texts if 'ابن كثير' in b.get('source', '')])} block")

    narrative_texts.extend(fetch_islamweb_stories())

    total_words = sum(len(b["text"].split()) for b in narrative_texts)
    if total_words < 100:
        print("   ⚠️ السرد قليل جداً، جاري التوليد عبر LLM...")
        ayahs_summary = "\n".join([f"{a['ref']}: {a['text']}" for a in ayahs[:20]])
        prompt = f"اكتب سرداً قصصياً موثقاً لقصة '{story_title}' بناءً على الآيات:\n{ayahs_summary}\nأعد JSON: {{\"narrative\": [{{\"text\": \"...\"}}]}}"
        fallback = call_llm(prompt, is_json=True)
        if fallback:
            for b in fallback.get("narrative", []):
                narrative_texts.append({"source": "توليد ذكي", "text": b.get("text", "")})
        print(f"   🤖 LLM أضاف {len(narrative_texts)} block")

    print("\n🧠 التقسيم الذكي للأحداث...")
    events_data = detect_events_and_separate(ayahs, narrative_texts, story_title)
    
    print("\n💾 حفظ الملفات...")
    (collected_dir / "ayahs_all.json").write_text(json.dumps(ayahs, ensure_ascii=False, indent=2), encoding="utf-8")
    (collected_dir / "narrative_all.json").write_text(json.dumps(narrative_texts, ensure_ascii=False, indent=2), encoding="utf-8")
    
    events_dir = collected_dir / "events"
    events_dir.mkdir(exist_ok=True)

    for ev in events_data.get("events", []):
        ev_id = ev.get("event_id", "event_01")
        ev_dir = events_dir / ev_id
        ev_dir.mkdir(exist_ok=True)
        
        ev_ayahs = ev.get("resolved_ayahs", [])
        ev_narr = ev.get("resolved_narrative", [])
        
        (ev_dir / "ayahs.json").write_text(json.dumps(ev_ayahs, ensure_ascii=False, indent=2), encoding="utf-8")
        (ev_dir / "narrative.json").write_text(json.dumps(ev_narr, ensure_ascii=False, indent=2), encoding="utf-8")
        (ev_dir / "meta.json").write_text(json.dumps({"event_id": ev_id, "title": ev.get("title", "")}, ensure_ascii=False, indent=2), encoding="utf-8")
        
        print(f"   ✅ {ev_id}: {len(ev_ayahs)} آية | {len(ev_narr)} سرد")

    print("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ خطأ فادح: {e}")
        sys.exit(1)
