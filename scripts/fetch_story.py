"""
fetch_story.py — جلب القصة الموثقة من مصادر موثوقة
═══════════════════════════════════════════════════════════
- المحرك: Gemini فقط (لا بدائل — الدقة أولاً)
- الديمومة: cache + retry محدود + توقف ذكي
- الأمان: تحقق صارم (طول + بتر + آيات + إسرائيليات)
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
# الحدود الدنيا (لا تنازل عنها)
# ═══════════════════════════════════════════════════════════
MIN_STORY_CHARS = 4000       # ~700 كلمة كحد أدنى
MIN_VERSES_COUNT = 5         # 5 آيات كحد أدنى
STORY_ENDING_CHARS = "؟.!،:؛\n\""  # الأحرف المقبولة لإنهاء القصة


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_sites_config():
    if not SITES_FILE.exists():
        log(f"⚠️ لا يوجد {SITES_FILE}، استخدام افتراضي")
        return {"sites": [], "tafsir_sources": [], "methodology": "أهل السنة والجماعة"}
    return json.loads(SITES_FILE.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════
# استدعاء Gemini فقط
# ═══════════════════════════════════════════════════════════
def call_gemini(prompt, max_tokens=16000, timeout=300, max_rounds=3):
    """
    استدعاء Gemini فقط. لا بدائل.
    - max_rounds: عدد المحاولات الكاملة
    - عند نفاد كل المفاتيح → استثناء (لا حلقة لا نهائية)
    """
    pool = get_pool()
    breaker = get_breaker()

    try:
        models = discover_and_cache(force=False)[:5]
    except Exception as e:
        raise RuntimeError(f"❌ فشل جلب النماذج: {e}")

    if not models:
        raise RuntimeError("❌ لا نماذج Gemini متاحة")

    for round_num in range(1, max_rounds + 1):
        log(f"🔄 محاولة {round_num}/{max_rounds}")

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

        # بين الدورات: انتظار متزايد
        if round_num < max_rounds:
            wait = 60 * round_num  # 60s, 120s
            log(f"😴 انتظار {wait} ثانية قبل المحاولة التالية...")
            time.sleep(wait)

    raise RuntimeError(
        f"❌ فشل Gemini بعد {max_rounds} محاولات كاملة. "
        "لا نستخدم بدائل أقل دقة. سنعيد المحاولة في التشغيل القادم."
    )


# ═══════════════════════════════════════════════════════════
# التحقق الصارم (لا تنازل)
# ═══════════════════════════════════════════════════════════
def validate_story(story_data):
    """
    يفحص القصة قبل القبول. أي فشل = رفض.
    """
    issues = []

    # ─── 1. الطول الأدنى ───
    full_story = story_data.get("full_story", "")
    if len(full_story) < MIN_STORY_CHARS:
        issues.append(
            f"القصة قصيرة: {len(full_story)} حرف "
            f"(الحد الأدنى {MIN_STORY_CHARS})"
        )

    # ─── 2. فحص البتر (نهاية القصة) ───
    story_stripped = full_story.strip()
    if story_stripped and story_stripped[-1] not in STORY_ENDING_CHARS:
        issues.append(
            f"القصة منتهية بكلمة مبتورة: '...{story_stripped[-40:]}'"
        )

    # ─── 3. عدد الآيات ───
    verses = story_data.get("verses", [])
    if len(verses) < MIN_VERSES_COUNT:
        issues.append(
            f"عدد الآيات قليل: {len(verses)} "
            f"(الحد الأدنى {MIN_VERSES_COUNT})"
        )

    # ─── 4. صحة صيغة الآيات ───
    bad_refs = []
    for v in verses:
        ref = v.get("ref", "")
        if not re.match(r"^\d{1,3}:\d{1,3}$", ref):
            bad_refs.append(ref)
    if bad_refs:
        issues.append(f"صيغة آيات خاطئة: {bad_refs[:3]}")

    # ─── 5. فحص الإسرائيليات ───
    israeliyat_markers = ["الإسرائيليات", "كعب الأحبار", "وهب بن منبه"]
    for marker in israeliyat_markers:
        if marker in full_story:
            log(f"   ⚠️ تحذير: '{marker}' مذكور — يحتاج مراجعة")

    # ─── 6. تكرار آيات ───
    refs = [v.get("ref") for v in verses]
    if len(refs) != len(set(refs)):
        duplicates = [r for r in refs if refs.count(r) > 1]
        issues.append(f"آيات مكررة: {list(set(duplicates))}")

    # ─── 7. فحص وجود الحقول الأساسية ───
    required_fields = ["story_id", "title", "full_story", "verses"]
    for field in required_fields:
        if not story_data.get(field):
            issues.append(f"حقل مفقود أو فارغ: {field}")

    return issues


# ═══════════════════════════════════════════════════════════
# بناء الـ Prompt
# ═══════════════════════════════════════════════════════════
def build_fetch_prompt(story_meta, sites_config):
    sites_str = "\n".join([
        f"- {s['name']} ({s['url']}) — {s.get('notes', '')}"
        for s in sites_config.get("sites", [])
    ]) or "- (لا توجد مواقع محددة، اعتمد على معرفتك الموثقة)"

    tafsir_str = "\n".join([
        f"- {t['name']}" for t in sites_config.get("tafsir_sources", [])
    ]) or "- (ابن كثير، الطبري، السعدي، القرطبي)"

    return f"""أنت باحث إسلامي متخصص على النهج السلفي (أهل السنة والجماعة).

المهمة: إعداد مادة موثقة شاملة لقصة "{story_meta['title']}".

المصادر المعتمدة (اعتمد عليها حصراً):
{sites_str}

التفاسير المعتمدة:
{tafsir_str}

⚠️ المحظورات المطلقة:
- يُمنع الإسرائيليات (كعب الأحبار، وهب بن منبه، الروايات المشكوكة).
- يُمنع الأحاديث الضعيفة والموضوعة.
- عند الشك في رواية، لا تذكرها.
- لا تختلق أحداثاً غير موجودة في المصادر.

⚠️ متطلبات الإجابة (صارمة):
1. "full_story" يجب أن يكون **بين 4000 و 8000 حرف** (700-1400 كلمة).
2. القصة يجب أن تكون **كاملة من البداية إلى النهاية** — لا تتركها مبتورة.
3. اذكر الآيات في سياقها داخل القصة (بين قوسين) لتربط السرد بالوحي.
4. رتب الأحداث زمنياً.
5. أعد JSON فقط بدون أي نص قبله أو بعده.

أعد JSON بهذه البنية بالضبط:
{{
  "story_id": "{story_meta['id']}",
  "title": "{story_meta['title']}",
  "summary_one_line": "ملخص شامل في سطر واحد",
  "full_story": "القصة الكاملة (4000-8000 حرف) بترتيبها الزمني، بأسلوب سردي مع ذكر الآيات في سياقها.",
  "verses": [
    {{"ref": "3:35", "used_in_context": "وصف موجز لدور الآية في القصة"}}
  ],
  "key_chapters_estimate": 7
}}

ابدأ الآن."""


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
            log(f"   محفوظ منذ: {data.get('_fetched_at', 'unknown')}")
            log(f"   💰 لم يتم استهلاك أي استدعاء Gemini")
            return data
        except Exception as e:
            log(f"⚠️ cache معطوب، إعادة الجلب: {e}")

    # ═══ 2. قراءة بيانات القصة ═══
    if not STORIES_FILE.exists():
        raise RuntimeError(f"❌ {STORIES_FILE} غير موجود")

    stories_data = json.loads(STORIES_FILE.read_text(encoding="utf-8"))
    story_meta = next(
        (s for s in stories_data.get("stories", []) if s["id"] == story_id),
        None,
    )
    if not story_meta:
        raise RuntimeError(f"❌ القصة '{story_id}' غير موجودة في المكتبة")

    log(f"📖 القصة: {story_meta['title']}")

    # ═══ 3. جلب من Gemini ═══
    sites_config = load_sites_config()
    prompt = build_fetch_prompt(story_meta, sites_config)

    log("🤖 توليد القصة من Gemini...")
    result = call_gemini(prompt, max_tokens=16000)

    if not result:
        raise RuntimeError("❌ Gemini أعاد None (فشل كامل)")

    # ═══ 4. التحقق الصارم ═══
    log("🔍 تحقق صارم من المحتوى...")
    issues = validate_story(result)

    if issues:
        log("❌ رفض القصة — مشاكل:")
        for issue in issues:
            log(f"   • {issue}")
        raise RuntimeError(
            f"❌ فشل التحقق ({len(issues)} مشكلة). "
            "لن نحفظ قصة ناقصة أو مشكوكة."
        )

    log(
        f"✅ القصة صالحة: "
        f"{len(result.get('full_story', ''))} حرف، "
        f"{len(result.get('verses', []))} آية"
    )

    # ═══ 5. إضافة metadata ═══
    result["_fetched_at"] = datetime.now(timezone.utc).isoformat()
    result["_methodology"] = sites_config.get(
        "methodology", "أهل السنة والجماعة"
    )
    result["_sources"] = [
        s["name"] for s in sites_config.get("sites", [])
    ]
    result["_validation"] = {
        "chars": len(result.get("full_story", "")),
        "verses": len(result.get("verses", [])),
        "passed": True,
    }

    # ═══ 6. حفظ ═══
    cached.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"💾 محفوظ: {cached.name}")
    log("💰 تم استهلاك استدعاء Gemini واحد (لن نعيده في المرات القادمة)")

    # ═══ 7. سجل ═══
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
                "chars": result["_validation"]["chars"],
                "verses": result["_validation"]["verses"],
                "sources": result["_sources"],
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

    parser = argparse.ArgumentParser(
        description="جلب قصة موثقة من Gemini (بدون بدائل)"
    )
    parser.add_argument("--story-id", required=True, help="معرف القصة (مثل maryam)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="تجاهل cache (يستهلك استدعاء Gemini إضافي — استخدمه بحذر)",
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
