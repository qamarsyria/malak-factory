"""
fetch_story.py — جلب القصة الموثقة من مصادر موثوقة
═══════════════════════════════════════════════════════════
- المحرك: Gemini فقط (لا بدائل — الدقة أولاً)
- الديمومة: cache + retry ذكي (3 محاولات مع تحذير متزايد)
- الأمان: تحقق صارم + رفض الناقص + إعادة المحاولة
- التوفير: cache يمنع استهلاك المفاتيح في إعادة الجلب
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
from model_discovery import discover_and_cache
from utilities import get_breaker

# ═══════════════════════════════════════════════════════════
# المسارات
# ═══════════════════════════════════════════════════════════
CONFIG_DIR = _REPO_ROOT / "config"
RAW_DIR = _REPO_ROOT / "data" / "story-raw"
STORIES_FILE = _REPO_ROOT / "data" / "library" / "stories.json"
SITES_FILE = CONFIG_DIR / "trusted_sites.json"
LOG_DIR = _REPO_ROOT / "logs"

RAW_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════
# الحدود الدنيا
# ═══════════════════════════════════════════════════════════
MIN_STORY_CHARS = 3500       # حد أدنى معقول
TARGET_STORY_CHARS = 5000    # الهدف (في الـ prompt)
MIN_VERSES_COUNT = 5
STORY_ENDING_CHARS = "؟.!،:؛\n\""

MAX_VALIDATION_RETRIES = 3   # إعادة المحاولة عند فشل التحقق


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_sites_config():
    if not SITES_FILE.exists():
        log(f"⚠️ لا يوجد {SITES_FILE}، استخدام افتراضي")
        return {"sites": [], "tafsir_sources": [], "methodology": "أهل السنة والجماعة"}
    return json.loads(SITES_FILE.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════
# إصلاح صيغة الآيات (يقبل النطاقات)
# ═══════════════════════════════════════════════════════════
def normalize_verses(verses):
    """
    يحول النطاقات إلى آيات فردية:
    "6:78-79" → ["6:78", "6:79"]
    """
    normalized = []
    for v in verses:
        ref = v.get("ref", "").strip()
        ctx = v.get("used_in_context", "")

        # نطاق (س:ص-ع)
        range_match = re.match(r"^(\d{1,3}):(\d{1,3})-(\d{1,3})$", ref)
        if range_match:
            surah = int(range_match.group(1))
            start_ayah = int(range_match.group(2))
            end_ayah = int(range_match.group(3))
            for ayah in range(start_ayah, end_ayah + 1):
                normalized.append({
                    "ref": f"{surah}:{ayah}",
                    "used_in_context": ctx,
                })
        # آية مفردة (س:ص)
        elif re.match(r"^\d{1,3}:\d{1,3}$", ref):
            normalized.append({"ref": ref, "used_in_context": ctx})
        # صيغة غير معروفة → تجاهل
        else:
            log(f"   ⚠️ تجاهل صيغة غير معروفة: '{ref}'")

    return normalized


# ═══════════════════════════════════════════════════════════
# استدعاء Gemini
# ═══════════════════════════════════════════════════════════
def call_gemini(prompt, max_tokens=16000, timeout=300, max_rounds=2):
    """استدعاء Gemini فقط. لا بدائل."""
    pool = get_pool()
    breaker = get_breaker()

    try:
        models = discover_and_cache(force=False)[:5]
    except Exception as e:
        raise RuntimeError(f"❌ فشل جلب النماذج: {e}")

    if not models:
        raise RuntimeError("❌ لا نماذج Gemini متاحة")

    for round_num in range(1, max_rounds + 1):
        log(f"🔄 محاولة API {round_num}/{max_rounds}")

        for model in models:
            if breaker.is_open(f"model:{model}"):
                continue

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
                            log(f"✅ نجح Gemini ({model})")
                            return parsed
                        except json.JSONDecodeError:
                            continue

                    elif r.status_code == 429:
                        log("   ⏸️ rate limit — انتظار")
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

        if round_num < max_rounds:
            wait = 30 * round_num
            log(f"😴 انتظار {wait} ثانية...")
            time.sleep(wait)

    raise RuntimeError(
        f"❌ فشل Gemini بعد {max_rounds} محاولات. "
        "لا بدائل أقل دقة. سنعيد المحاولة في التشغيل القادم."
    )


# ═══════════════════════════════════════════════════════════
# التحقق الصارم
# ═══════════════════════════════════════════════════════════
def validate_story(story_data):
    """يفحص القصة. أي فشل = رفض."""
    issues = []

    # 1. الطول الأدنى
    full_story = story_data.get("full_story", "")
    if len(full_story) < MIN_STORY_CHARS:
        issues.append(
            f"القصة قصيرة: {len(full_story)} حرف "
            f"(الحد الأدنى {MIN_STORY_CHARS})"
        )

    # 2. فحص البتر
    story_stripped = full_story.strip()
    if story_stripped and story_stripped[-1] not in STORY_ENDING_CHARS:
        issues.append(
            f"القصة منتهية بكلمة مبتورة: '...{story_stripped[-40:]}'"
        )

    # 3. عدد الآيات
    verses = story_data.get("verses", [])
    if len(verses) < MIN_VERSES_COUNT:
        issues.append(
            f"عدد الآيات قليل: {len(verses)} "
            f"(الحد الأدنى {MIN_VERSES_COUNT})"
        )

    # 4. صحة صيغة الآيات (بعد التطبيع)
    bad_refs = []
    for v in verses:
        ref = v.get("ref", "")
        if not re.match(r"^\d{1,3}:\d{1,3}$", ref):
            bad_refs.append(ref)
    if bad_refs:
        issues.append(f"صيغة آيات خاطئة: {bad_refs[:3]}")

    # 5. تكرار آيات
    refs = [v.get("ref") for v in verses]
    if len(refs) != len(set(refs)):
        duplicates = [r for r in refs if refs.count(r) > 1]
        issues.append(f"آيات مكررة: {list(set(duplicates))[:3]}")

    # 6. فحص الإسرائيليات (تحذير فقط)
    israeliyat_markers = ["الإسرائيليات", "كعب الأحبار", "وهب بن منبه"]
    for marker in israeliyat_markers:
        if marker in full_story:
            log(f"   ⚠️ تحذير: '{marker}' مذكور")

    # 7. الحقول الإلزامية
    required_fields = ["story_id", "title", "full_story", "verses"]
    for field in required_fields:
        if not story_data.get(field):
            issues.append(f"حقل مفقود: {field}")

    return issues


# ═══════════════════════════════════════════════════════════
# بناء الـ Prompt
# ═══════════════════════════════════════════════════════════
def build_fetch_prompt(story_meta, sites_config, retry_context=None):
    """
    retry_context: dict مع معلومات عن المحاولة السابقة (إذا كان retry)
    """
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
⚠️⚠️⚠️ تحذير من محاولة سابقة فاشلة:
{retry_context.get('warning', '')}

يجب عليك:
1. تطويل القصة إلى 4000-6000 حرف على الأقل.
2. إنهاء القصة بجملة كاملة تنتهي بـ (. أو ! أو ؟).
3. استخدام صيغة آية واحدة فقط: "سورة:آية" (مثل "3:35") — بدون نطاقات.
"""

    return f"""أنت باحث إسلامي متخصص على النهج السلفي (أهل السنة والجماعة).

المهمة: إعداد مادة موثقة شاملة لقصة "{story_meta['title']}".
{retry_warning}

المصادر المعتمدة (اعتمد عليها حصراً):
{sites_str}

التفاسير المعتمدة:
{tafsir_str}

⚠️ المحظورات المطلقة:
- يُمنع الإسرائيليات (كعب الأحبار، وهب بن منبه، الروايات المشكوكة).
- يُمنع الأحاديث الضعيفة والموضوعة.
- عند الشك في رواية، لا تذكرها.
- لا تختلق أحداثاً غير موجودة في المصادر.

⚠️ متطلبات صارمة (لا تتنازل عنها):
1. "full_story" يجب أن يكون **بين 4000 و 6000 حرف** (800-1200 كلمة).
2. اكتب قصة **كاملة** من البداية للنهاية، بأسلوب سردي متدفق.
3. **أنهِ القصة بجملة كاملة** تنتهي بنقطة أو علامة تعجب.
4. اذكر الآيات بين قوسين داخل السرد لربط الأحداث بالوحي.
5. **صيغة كل آية**: "سورة:آية" بالضبط (مثال: `"3:35"`). **لا نطاقات** (لا `"6:78-79"`).
6. رتب الأحداث زمنياً.
7. أعد JSON فقط.

أعد JSON بهذه البنية:
{{
  "story_id": "{story_meta['id']}",
  "title": "{story_meta['title']}",
  "summary_one_line": "ملخص شامل في سطر واحد",
  "full_story": "القصة الكاملة (4000-6000 حرف). ابدأ بمقدمة جذابة وانهِ بخلاصة وعبرة.",
  "verses": [
    {{"ref": "3:35", "used_in_context": "دور الآية في القصة"}}
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

    # ═══ 1. Cache ═══
    cached = RAW_DIR / f"{story_id}.json"
    if cached.exists() and not force:
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            log(f"✅ من cache: {cached.name}")
            log(f"   💰 لم يتم استهلاك أي استدعاء")
            return data
        except Exception as e:
            log(f"⚠️ cache معطوب: {e}")

    # ═══ 2. قراءة القصة ═══
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

    # ═══ 3. محاولات متعددة (Validation Retry Loop) ═══
    last_issues = []
    result = None

    for validation_attempt in range(1, MAX_VALIDATION_RETRIES + 1):
        log(f"🎯 محاولة إنشاء قصة: {validation_attempt}/{MAX_VALIDATION_RETRIES}")

        # بناء prompt (مع تحذير إذا كانت محاولة سابقة)
        retry_ctx = None
        if validation_attempt > 1:
            retry_ctx = {
                "warning": "\n".join(f"• {issue}" for issue in last_issues)
            }

        prompt = build_fetch_prompt(story_meta, sites_config, retry_ctx)
        candidate = call_gemini(prompt, max_tokens=16000)

        if not candidate:
            log("❌ Gemini أعاد None")
            continue

        # ═══ 4. تطبيع الآيات (قبل التحقق) ═══
        if "verses" in candidate:
            candidate["verses"] = normalize_verses(candidate["verses"])

        # ═══ 5. التحقق ═══
        log("🔍 تحقق...")
        issues = validate_story(candidate)

        if not issues:
            result = candidate
            log(f"✅ صالحة: {len(candidate.get('full_story', ''))} حرف، "
                f"{len(candidate.get('verses', []))} آية")
            break
        else:
            last_issues = issues
            log(f"❌ فشل التحقق (محاولة {validation_attempt}):")
            for issue in issues:
                log(f"   • {issue}")
            if validation_attempt < MAX_VALIDATION_RETRIES:
                log("🔁 إعادة المحاولة مع prompt محسّن...")
                time.sleep(5)

    if not result:
        raise RuntimeError(
            f"❌ فشل بعد {MAX_VALIDATION_RETRIES} محاولات إنشاء. "
            f"آخر المشاكل: {last_issues}"
        )

    # ═══ 6. metadata ═══
    result["_fetched_at"] = datetime.now(timezone.utc).isoformat()
    result["_methodology"] = sites_config.get(
        "methodology", "أهل السنة والجماعة"
    )
    result["_sources"] = [s["name"] for s in sites_config.get("sites", [])]
    result["_validation"] = {
        "chars": len(result.get("full_story", "")),
        "verses": len(result.get("verses", [])),
        "attempts": validation_attempt,
        "passed": True,
    }

    # ═══ 7. حفظ ═══
    cached.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"💾 محفوظ: {cached.name}")

    # ═══ 8. سجل ═══
    log_file = (
        LOG_DIR
        / f"fetch_{story_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
    )
    log_file.write_text(
        json.dumps(
            {
                "story_id": story_id,
                "status": "success",
                "timestamp": result["_fetched_at"],
                "attempts": validation_attempt,
                "chars": result["_validation"]["chars"],
                "verses": result["_validation"]["verses"],
            },
            ensure_ascii=False,
            indent=2,
        ),
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="تجاهل cache (يستهلك استدعاءات إضافية)",
    )
    args = parser.parse_args()

    try:
        result = fetch_story(args.story_id, force=args.force)
        log("🎉 نجح!")
        sys.exit(0)
    except Exception as e:
        log(f"❌ {e}")
        traceback.print_exc()
        sys.exit(1)
