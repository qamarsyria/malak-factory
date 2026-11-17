"""
fetch_story.py — جلب القصة الموثقة من مصادر موثوقة
═══════════════════════════════════════════════════════════
- المحرك: Gemini فقط (نماذج ثقيلة، لا flash-lite)
- الديمومة: cache + 3 محاولات مع نماذج مختلفة
- الأمان: تحقق صارم (2500 حرف كحد أدنى معقول)
- التوفير: cache يمنع استهلاك المفاتيح
═══════════════════════════════════════════════════════════
"""
import os
import sys
import json
import time
import traceback
import re
from pathlib import Path
from datetime import datetime, timezone
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from utilities import get_breaker

# ═══════════════════════════════════════════════════════════
# المسارات والحدود
# ═══════════════════════════════════════════════════════════
CONFIG_DIR = _REPO_ROOT / "config"
RAW_DIR = _REPO_ROOT / "data" / "story-raw"
STORIES_FILE = _REPO_ROOT / "data" / "library" / "stories.json"
SITES_FILE = CONFIG_DIR / "trusted_sites.json"
LOG_DIR = _REPO_ROOT / "logs"

RAW_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════
# الحدود
# ═══════════════════════════════════════════════════════════
MIN_STORY_CHARS = 2500        # 500 كلمة كحد أدنى للقصة الخام
MIN_VERSES_COUNT = 5
MAX_VALIDATION_RETRIES = 3
STORY_ENDING_CHARS = "؟.!،:؛\n\""

# ═══════════════════════════════════════════════════════════
# النماذج المفضلة (لا flash-lite)
# ═══════════════════════════════════════════════════════════
PREFERRED_MODELS = [
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
]

# نماذج لا نستخدمها (لا تكتب نصوصاً طويلة)
FORBIDDEN_MODELS = [
    "flash-lite",
    "flash-8b",
    "1.5-flash",
]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_sites_config():
    if not SITES_FILE.exists():
        return {"sites": [], "tafsir_sources": [], "methodology": "أهل السنة والجماعة"}
    return json.loads(SITES_FILE.read_text(encoding="utf-8"))


def get_ordered_models():
    """يرجع قائمة مرتبة من النماذج الجيدة فقط."""
    try:
        from model_discovery import discover_and_cache
        available = discover_and_cache(force=False)
    except Exception as e:
        log(f"⚠️ فشل discovery: {e}")
        available = []

    # فلترة: استبعاد النماذج الضعيفة
    filtered = [
        m for m in available
        if not any(f in m.lower() for f in FORBIDDEN_MODELS)
    ]

    # ترتيب حسب الأولوية
    ordered = []
    for preferred in PREFERRED_MODELS:
        for m in filtered:
            if preferred in m.lower() and m not in ordered:
                ordered.append(m)

    # إضافة أي نموذج متاح لم يُذكر (كاحتياطي)
    for m in filtered:
        if m not in ordered:
            ordered.append(m)

    return ordered[:5]


# ═══════════════════════════════════════════════════════════
# إصلاح صيغة الآيات
# ═══════════════════════════════════════════════════════════
def normalize_verses(verses):
    """يحول النطاقات إلى آيات فردية: "6:78-79" → ["6:78", "6:79"]"""
    normalized = []
    for v in verses:
        ref = v.get("ref", "").strip()
        ctx = v.get("used_in_context", "")

        range_match = re.match(r"^(\d{1,3}):(\d{1,3})-(\d{1,3})$", ref)
        if range_match:
            surah = int(range_match.group(1))
            start_ayah = int(range_match.group(2))
            end_ayah = int(range_match.group(3))
            for ayah in range(start_ayah, end_ayah + 1):
                normalized.append({"ref": f"{surah}:{ayah}", "used_in_context": ctx})
        elif re.match(r"^\d{1,3}:\d{1,3}$", ref):
            normalized.append({"ref": ref, "used_in_context": ctx})
        else:
            log(f"   ⚠️ تجاهل صيغة: '{ref}'")

    return normalized


# ═══════════════════════════════════════════════════════════
# استدعاء Gemini
# ═══════════════════════════════════════════════════════════
def call_gemini(prompt, max_tokens=16000, timeout=300):
    """استدعاء Gemini بنماذج ثقيلة فقط."""
    pool = get_pool()
    breaker = get_breaker()

    models = get_ordered_models()
    if not models:
        raise RuntimeError("❌ لا نماذج Gemini متاحة")

    log(f"🧠 نماذج متاحة: {models}")

    for model in models:
        if breaker.is_open(f"model:{model}"):
            continue

        log(f"   🤖 محاولة: {model}")

        for attempt in range(2):
            try:
                key = pool.get_current()
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/"
                    f"models/{model}:generateContent?key={key}"
                )
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.15,
                        "maxOutputTokens": max_tokens,
                        "responseMimeType": "application/json",
                    },
                }
                r = requests.post(url, json=body, timeout=timeout)

                if r.status_code == 200:
                    parts = (
                        r.json()
                        .get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [])
                    )
                    if not parts:
                        continue
                    text = parts[0].get("text", "")
                    s, e = text.find("{"), text.rfind("}")
                    if s == -1 or e <= s:
                        continue
                    try:
                        parsed = json.loads(text[s:e + 1])
                        pool._advance()
                        breaker.record_success(f"model:{model}")
                        log(f"   ✅ نجح: {model}")
                        return parsed
                    except json.JSONDecodeError:
                        continue

                elif r.status_code == 429:
                    log("   ⏸️ rate limit")
                    pool.mark_failed(3600)
                    time.sleep(5)

                elif r.status_code in (404, 503):
                    breaker.record_failure(f"model:{model}")
                    break

            except requests.exceptions.Timeout:
                log("   ⏱️ timeout")
                time.sleep(5)
            except Exception as e:
                log(f"   ⚠️ {str(e)[:100]}")
                time.sleep(3)

    raise RuntimeError(
        "❌ فشلت كل النماذج. لا بدائل أقل دقة. سنعيد في التشغيل القادم."
    )


# ═══════════════════════════════════════════════════════════
# التحقق
# ═══════════════════════════════════════════════════════════
def validate_story(story_data):
    issues = []

    full_story = story_data.get("full_story", "")
    if len(full_story) < MIN_STORY_CHARS:
        issues.append(
            f"القصة قصيرة: {len(full_story)} حرف (الحد الأدنى {MIN_STORY_CHARS})"
        )

    story_stripped = full_story.strip()
    if story_stripped and story_stripped[-1] not in STORY_ENDING_CHARS:
        issues.append(f"القصة مبتورة: '...{story_stripped[-40:]}'")

    verses = story_data.get("verses", [])
    if len(verses) < MIN_VERSES_COUNT:
        issues.append(
            f"آيات قليلة: {len(verses)} (الحد الأدنى {MIN_VERSES_COUNT})"
        )

    bad_refs = [
        v.get("ref") for v in verses
        if not re.match(r"^\d{1,3}:\d{1,3}$", v.get("ref", ""))
    ]
    if bad_refs:
        issues.append(f"صيغة آيات خاطئة: {bad_refs[:3]}")

    refs = [v.get("ref") for v in verses]
    if len(refs) != len(set(refs)):
        issues.append("آيات مكررة")

    for marker in ["الإسرائيليات", "كعب الأحبار", "وهب بن منبه"]:
        if marker in full_story:
            log(f"   ⚠️ '{marker}' مذكور")

    for field in ["story_id", "title", "full_story", "verses"]:
        if not story_data.get(field):
            issues.append(f"حقل مفقود: {field}")

    return issues


# ═══════════════════════════════════════════════════════════
# بناء الـ Prompt (طلب تفاصيل محددة)
# ═══════════════════════════════════════════════════════════
def build_fetch_prompt(story_meta, sites_config, retry_context=None):
    sites_str = "\n".join([
        f"- {s['name']} ({s['url']}) — {s.get('notes', '')}"
        for s in sites_config.get("sites", [])
    ]) or "- (اعتمد على معرفتك الموثقة)"

    tafsir_str = "\n".join([
        f"- {t['name']}" for t in sites_config.get("tafsir_sources", [])
    ]) or "- (ابن كثير، الطبري، السعدي، القرطبي)"

    retry_warning = ""
    if retry_context:
        retry_warning = f"""
⚠️ محاولة سابقة فشلت بسبب:
{retry_context.get('warning', '')}

في هذه المحاولة يجب أن تكون القصة أطول وأكثر تفصيلاً. أضف مواقف محددة وحوارات.
"""

    return f"""أنت باحث إسلامي ومؤرخ قصصي على النهج السلفي.

المهمة: كتابة قصة "{story_meta['title']}" بشكل شامل ومفصل.
{retry_warning}

المصادر المعتمدة:
{sites_str}

التفاسير:
{tafsir_str}

⚠️ المحظورات المطلقة:
- لا إسرائيليات (كعب الأحبار، وهب بن منبه).
- لا أحاديث ضعيفة/موضوعة.
- عند الشك في رواية، لا تذكرها.

⚠️ متطلبات المحتوى (لضمان الطول والعمق):
1. القصة يجب أن تغطي **كل المواقف الكبرى** في حياة الشخصية.
2. اذكر **حوارات** بين الشخصية وقومها/أعدائها (بأسلوب سردي، لا اقتباس قرآني حرفي).
3. اشرح كل موقف بجملتين أو ثلاث (المشهد، الحوار، النتيجة).
4. اربط كل موقف بمرجع آية بين قوسين: (انظر: سورة:آية).
5. **أنهِ القصة بجملة كاملة**.

⚠️ المتطلبات التقنية:
- "full_story": بين 2800 و 5000 حرف.
- **صيغة كل آية في verses**: "سورة:آية" (مثل `"3:35"`). **لا نطاقات**.
- 5 آيات على الأقل.
- أعد JSON فقط.

أعد JSON بهذه البنية:
{{
  "story_id": "{story_meta['id']}",
  "title": "{story_meta['title']}",
  "summary_one_line": "ملخص في سطر واحد",
  "full_story": "القصة الشاملة (2800-5000 حرف) بترتيبها الزمني، مع المواقف والحوارات والمراجع.",
  "verses": [
    {{"ref": "3:35", "used_in_context": "دور الآية"}}
  ],
  "key_chapters_estimate": 8
}}

ابدأ الآن وأعد JSON فقط."""


# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def fetch_story(story_id, force=False):
    log("=" * 70)
    log(f"🚀 جلب: {story_id}")
    log("=" * 70)

    cached = RAW_DIR / f"{story_id}.json"
    if cached.exists() and not force:
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            log(f"✅ من cache: {cached.name}")
            log(f"   💰 لم يتم استهلاك استدعاء")
            return data
        except Exception as e:
            log(f"⚠️ cache معطوب: {e}")

    if not STORIES_FILE.exists():
        raise RuntimeError(f"❌ {STORIES_FILE} غير موجود")

    stories_data = json.loads(STORIES_FILE.read_text(encoding="utf-8"))
    story_meta = next(
        (s for s in stories_data.get("stories", []) if s["id"] == story_id),
        None,
    )
    if not story_meta:
        raise RuntimeError(f"❌ القصة '{story_id}' غير موجودة")

    log(f"📖 {story_meta['title']}")
    sites_config = load_sites_config()

    last_issues = []
    result = None
    best_candidate = None  # أفضل محاولة حتى لو فشلت

    for validation_attempt in range(1, MAX_VALIDATION_RETRIES + 1):
        log(f"🎯 محاولة {validation_attempt}/{MAX_VALIDATION_RETRIES}")

        retry_ctx = None
        if validation_attempt > 1:
            retry_ctx = {"warning": "\n".join(f"• {i}" for i in last_issues)}

        prompt = build_fetch_prompt(story_meta, sites_config, retry_ctx)

        try:
            candidate = call_gemini(prompt, max_tokens=16000)
        except Exception as e:
            log(f"❌ فشل استدعاء: {e}")
            continue

        if not candidate:
            continue

        if "verses" in candidate:
            candidate["verses"] = normalize_verses(candidate["verses"])

        # تتبع أفضل مرشح
        candidate_len = len(candidate.get("full_story", ""))
        if not best_candidate or candidate_len > len(best_candidate.get("full_story", "")):
            best_candidate = candidate
            log(f"   📝 أفضل حتى الآن: {candidate_len} حرف")

        log("🔍 تحقق...")
        issues = validate_story(candidate)

        if not issues:
            result = candidate
            log(f"✅ صالحة: {candidate_len} حرف")
            break
        else:
            last_issues = issues
            log(f"❌ فشل:")
            for i in issues:
                log(f"   • {i}")
            if validation_attempt < MAX_VALIDATION_RETRIES:
                log("🔁 إعادة المحاولة...")
                time.sleep(5)

    if not result:
        # لا نحفظ — لكن نبلغ عن أفضل محاولة
        if best_candidate:
            log(f"⚠️ أفضل محاولة: {len(best_candidate.get('full_story', ''))} حرف (تحت الحد)")
        raise RuntimeError(
            f"❌ فشل بعد {MAX_VALIDATION_RETRIES} محاولات. "
            f"آخر المشاكل: {last_issues}"
        )

    # metadata
    result["_fetched_at"] = datetime.now(timezone.utc).isoformat()
    result["_methodology"] = sites_config.get("methodology", "أهل السنة والجماعة")
    result["_sources"] = [s["name"] for s in sites_config.get("sites", [])]
    result["_validation"] = {
        "chars": len(result.get("full_story", "")),
        "verses": len(result.get("verses", [])),
        "attempts": validation_attempt,
        "passed": True,
    }

    cached.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"💾 محفوظ: {cached.name}")

    log_file = (
        LOG_DIR
        / f"fetch_{story_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
    )
    log_file.write_text(
        json.dumps({
            "story_id": story_id,
            "status": "success",
            "timestamp": result["_fetched_at"],
            "attempts": validation_attempt,
            "chars": result["_validation"]["chars"],
            "verses": result["_validation"]["verses"],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return result


# ═══════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--story-id", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        fetch_story(args.story_id, force=args.force)
        log("🎉 نجح!")
        sys.exit(0)
    except Exception as e:
        log(f"❌ {e}")
        traceback.print_exc()
        sys.exit(1)
