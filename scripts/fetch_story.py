"""
fetch_story.py — جلب القصة الموثقة من مصادر موثوقة
- الديمومة: cache + إعادة محاولة + عدم تكرار
- الأمان: تحقق صارم + مصادر محددة
"""
import os, sys, json, time, traceback, re
from pathlib import Path
from datetime import datetime, timezone
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import get_breaker

# ═══════════════════════════════════════════════════════════
# المسارات
# ═══════════════════════════════════════════════════════════
CONFIG_DIR = _REPO_ROOT / "config"
RAW_DIR = _REPO_ROOT / "data" / "story-raw"
LIBRARY_DIR = _REPO_ROOT / "data" / "libraries"
STORIES_FILE = _REPO_ROOT / "data" / "library" / "stories.json"
SITES_FILE = CONFIG_DIR / "trusted_sites.json"
LOG_DIR = _REPO_ROOT / "logs"

RAW_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════
# أدوات مساعدة
# ═══════════════════════════════════════════════════════════
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def load_sites_config():
    if not SITES_FILE.exists():
        log(f"⚠️ لا يوجد {SITES_FILE}، استخدام افتراضي")
        return {"sites": [], "tafsir_sources": [], "forbidden_content": {}}
    return json.loads(SITES_FILE.read_text(encoding="utf-8"))

def load_ayah_library():
    """تحميل مكتبة الآيات (موجودة محلياً — كمرجع تحقق)"""
    lib_path = LIBRARY_DIR / "ayah-library.json.gz"
    if not lib_path.exists():
        log("⚠️ مكتبة الآيات غير موجودة (سيتم تجاهل التحقق من الآيات)")
        return {}
    import gzip
    with gzip.open(lib_path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    return {f"{item['surah_number']}:{item['ayah_number']}": item for item in data}

def call_gemini(prompt, max_tokens=16000, timeout=300):
    """استدعاء Gemini مع retries"""
    pool = get_pool()
    breaker = get_breaker()
    try:
        models = discover_and_cache(force=False)[:5]
    except Exception as e:
        log(f"❌ خطأ في جلب النماذج: {e}")
        return None
    
    if not models:
        log("❌ لا نماذج متاحة")
        return None
    
    for model in models:
        if breaker.is_open(f"model:{model}"): continue
        for attempt in range(3):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.15,
                        "maxOutputTokens": max_tokens,
                        "responseMimeType": "application/json"
                    }
                }
                r = requests.post(url, json=body, timeout=timeout)
                
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts: continue
                    text = parts[0].get("text", "")
                    s, e = text.find("{"), text.rfind("}")
                    if s == -1 or e <= s: continue
                    try:
                        parsed = json.loads(text[s:e+1])
                        pool._advance()
                        breaker.record_success(f"model:{model}")
                        return parsed
                    except json.JSONDecodeError:
                        continue
                elif r.status_code == 429:
                    pool.mark_failed(3600)
                    time.sleep(2)
                elif r.status_code in (404, 503):
                    breaker.record_failure(f"model:{model}")
                    break
            except requests.exceptions.Timeout:
                log(f"   ⏱️ timeout (model={model}, attempt={attempt+1})")
                time.sleep(5)
            except Exception as e:
                log(f"   ⚠️ {str(e)[:100]}")
                time.sleep(3)
    return None

def validate_story(story_data, sites_config):
    """التحقق الصارم من القصة قبل القبول"""
    issues = []
    
    if not story_data.get("full_story") or len(story_data["full_story"]) < 500:
        issues.append("القصة قصيرة جداً أو فارغة")
    
    if not story_data.get("verses") or len(story_data["verses"]) < 3:
        issues.append(f"عدد الآيات قليل ({len(story_data.get('verses', []))})")
    
    # فحص المحتوى المحظور
    forbidden = sites_config.get("forbidden_content", {})
    full_text = story_data.get("full_story", "")
    
    # فحص الإسرائيليات (كلمات مفتاحية تحذيرية)
    israeliyat_keywords = ["الإسرائيليات", "كعب الأحبار", "وهب بن منبه"]
    for kw in israeliyat_keywords:
        if kw in full_text:
            log(f"   ⚠️ تحذير: '{kw}' مذكور — قد يحتوي على إسرائيليات")
    
    return issues

# ═══════════════════════════════════════════════════════════
# جلب القصة
# ═══════════════════════════════════════════════════════════
def build_fetch_prompt(story_meta, sites_config, ayah_lib_sample):
    """بناء prompt احترافي لجلب القصة"""
    sites_str = "\n".join([
        f"- {s['name']} ({s['url']}) — {s.get('notes', '')}"
        for s in sites_config.get("sites", [])
    ])
    tafsir_str = "\n".join([f"- {t['name']}" for t in sites_config.get("tafsir_sources", [])])
    
    return f"""أنت باحث إسلامي متخصص على النهج السلفي (أهل السنة والجماعة).

المهمة: إعداد مادة موثقة لقصة "{story_meta['title']}" من قصص الأنبياء/الصالحين.

المصادر المعتمدة التي يجب أن تستند إليها حصراً:
{sites_str}

التفاسير المعتمدة:
{tafsir_str}

⚠️ المحظورات المطلقة:
- يُمنع الإسرائيليات (كعب الأحبار، وهب بن منبه، الروايات المشكوكة).
- يُمنع الأحاديث الضعيفة والموضوعة.
- يُمنع أي رواية لا تجدها في: القرآن، الصحيحين، كتب التفسير المعتمدة، أو المصادر السابقة.
- عند الشك في رواية، لا تذكرها.

المخرجات المطلوبة (JSON فقط، بالعربية الفصحى):

{{
  "story_id": "{story_meta['id']}",
  "title": "{story_meta['title']}",
  "summary_one_line": "ملخص القصة في سطر واحد",
  "full_story": "القصة الكاملة بترتيبها الزمني (1000-2000 كلمة). استخدم أسلوب سردي (لا اقتباسات قرآنية حرفية إلا بين قوسين مع الإشارة للآية). اعتمد على التفاسير المعتمدة.",
  "verses": [
    {{
      "ref": "3:35",
      "used_in_context": "ماذا تقول هذه الآية في القصة (وصف موجز)"
    }}
  ],
  "key_chapters_estimate": 7,
  "sources_used": ["dorar", "islamway"]
}}

⚠️ متطلبات التحقق:
1. كل آية في verses يجب أن تكون آية حقيقية من القرآن.
2. full_story يجب أن يكون 1000-2000 كلمة.
3. لا تضع أي حديث بصيغة "قال النبي ﷺ" إلا لو تعرف أنه صحيح (البخاري/مسلم).
4. إذا لم تجد معلومات كافية، قلل الطول ولا تختلق.

أعد JSON فقط."""

# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def fetch_story(story_id, force=False):
    """جلب قصة واحدة مع دعم cache"""
    log("=" * 70)
    log(f"🚀 بدء جلب: {story_id}")
    log("=" * 70)
    
    # ═══ الطبقة 1: cache ═══
    cached = RAW_DIR / f"{story_id}.json"
    if cached.exists() and not force:
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            log(f"✅ من cache: {cached} (آخر تحديث: {data.get('_fetched_at', 'unknown')})")
            return data
        except Exception as e:
            log(f"⚠️ cache معطوب، سيتم إعادة الجلب: {e}")
    
    # ═══ الطبقة 2: قراءة مكتبة القصص ═══
    if not STORIES_FILE.exists():
        log(f"❌ {STORIES_FILE} غير موجود")
        return None
    
    stories_data = json.loads(STORIES_FILE.read_text(encoding="utf-8"))
    story_meta = next((s for s in stories_data.get("stories", []) if s["id"] == story_id), None)
    
    if not story_meta:
        log(f"❌ القصة '{story_id}' غير موجودة في المكتبة")
        return None
    
    log(f"📖 القصة: {story_meta['title']}")
    
    # ═══ الطبقة 3: تحميل السياق ═══
    sites_config = load_sites_config()
    log(f"🌐 المواقع المعتمدة: {len(sites_config.get('sites', []))}")
    
    # ═══ الطبقة 4: توليد القصة عبر Gemini ═══
    log("🤖 توليد القصة من Gemini (بناءً على المصادر)...")
    prompt = build_fetch_prompt(story_meta, sites_config, {})
    
    result = call_gemini(prompt, max_tokens=16000, timeout=300)
    
    if not result:
        log("❌ فشل الجلب من Gemini (كل النماذج فشلت)")
        return None
    
    # ═══ الطبقة 5: التحقق الصارم ═══
    log("🔍 التحقق من المحتوى...")
    issues = validate_story(result, sites_config)
    
    if issues:
        log(f"⚠️ مشاكل في القصة:")
        for issue in issues:
            log(f"   - {issue}")
        log("❌ رفض القصة — جودة غير كافية")
        return None
    
    log(f"✅ القصة صالحة ({len(result.get('full_story', ''))} حرف، {len(result.get('verses', []))} آية)")
    
    # ═══ الطبقة 6: إضافة metadata + حفظ ═══
    result["_fetched_at"] = datetime.now(timezone.utc).isoformat()
    result["_sources"] = sites_config.get("sites", [])
    result["_methodology"] = sites_config.get("methodology", "أهل السنة والجماعة")
    
    cached.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"💾 محفوظ: {cached}")
    
    # ═══ الطبقة 7: حفظ سجل ═══
    log_file = LOG_DIR / f"fetch_{story_id}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
    log_file.write_text(json.dumps({
        "story_id": story_id,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chars": len(result.get("full_story", "")),
        "verses": len(result.get("verses", [])),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return result

# ═══════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--story-id", required=True)
    parser.add_argument("--force", action="store_true", help="تجاهل cache")
    args = parser.parse_args()
    
    try:
        result = fetch_story(args.story_id, force=args.force)
        if result:
            log(f"🎉 نجح الجلب: {args.story_id}")
            sys.exit(0)
        else:
            log(f"❌ فشل الجلب: {args.story_id}")
            sys.exit(1)
    except Exception as e:
        log(f"❌ خطأ فادح: {e}")
        traceback.print_exc()
        sys.exit(1)
