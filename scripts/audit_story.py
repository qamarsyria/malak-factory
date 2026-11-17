"""
audit_story.py — المرحلة 2: التدقيق
يقرأ collected/events/*/narrative.json ويدقّق كل block عبر LLM:
  - يكشف المخالفات (تشبيه، إسرائيليات، تناقض، إضافة دينية غير موثقة)
  - يعيد صياغة الـ block بحيث يحذف الجزء المخالف ويبقي السياق القصصي
  - يسجّل كل تعديل في audit_log.json

الآيات (ayahs.json) لا تُمسّ إطلاقاً — تُنسخ كما هي.

الاستخدام:
    python audit_story.py <story_id>
    python audit_story.py <story_id> --force
"""

import os, re, sys, json, time
from pathlib import Path
from datetime import datetime, timezone
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import get_breaker, notify_telegram
from filters_loader import get_llm_forbidden_rules

# ═══════════════════════════════════════════════════════════
# المسارات
# ═══════════════════════════════════════════════════════════
STORIES_JSON   = _REPO_ROOT / "data" / "library" / "stories.json"
STORIES_DIR    = _REPO_ROOT / "data" / "stories"

# ═══════════════════════════════════════════════════════════
# إعدادات
# ═══════════════════════════════════════════════════════════
BATCH_SIZE = 3              # عدد الـ blocks في كل استدعاء LLM
MIN_BLOCK_LENGTH = 30       # block أقل من 30 حرف → يُتجاهل (نص فارغ أو قصير جداً)
MAX_BLOCK_LENGTH = 3000     # block أطول من 3000 حرف → يُقسّم قبل التدقيق

# ═══════════════════════════════════════════════════════════
# prompt التدقيق
# ═══════════════════════════════════════════════════════════
AUDIT_PROMPT_TEMPLATE = """أنت مدقّق شرعي متخصص في قصص الأنبياء. مهمتك مراجعة النصوص التالية (سرد قصصي، ليست آيات قرآنية) وحذف أي مخالفة.

قواعد التدقيق الصارمة:

🔴 مخالفات تُحذف دائماً:
1. أي تشبيه أو تجسيد للذات الإلهية (يد، وجه، عين، ساق، نزول حسي، رؤية بصرية، جلوس، مكان...).
2. أي وصف لشكل أو هيئة أو ملامح الأنبياء / الملائكة / الصحابة (طول، عرض، وجه، شعر، ملابس، أعضاء، هيئة...).
3. الإسرائيليات (روايات من كتب أهل الكتاب لم تثبت في الإسلام).
4. أي حديث ضعيف أو موضوع يُنسب إلى النبي ﷺ.
5. أي معلومة دينية صريحة (اسم، رقم، حدث، حكم شرعي) لا يوجد لها أصل واضح في القرآن أو التفسير الميسر أو صحيح السيرة.
6. أي تناقض مع التفسير الميسر أو الآيات.
7. أي إيحاء جنسي أو تعري أو وصف شهواني.

🟢 ما يبقى (وصف قصصي مشروع):
- المشاهد الطبيعية والأجواء (ليلة، نجوم، صحراء، مطر).
- الحالة النفسية (حزن، فرح، خوف، تأمل) بدون وصف جسدي.
- الحوارات والأسئلة (بدون تشبيه أو إضافة دينية).
- انتقالات زمنية ومكانية (ثم غادر، عاد بعد سنوات).
- العبرة والعظة العامة.

📋 قواعد إعادة الصياغة:
- احذف الجزء المخالف فقط، وحافظ على بقية الجملة.
- لا تُغيّر الأسماء الصحيحة أو الآيات المرجعية [آية: x:y].
- لا تُضف معلومات جديدة.
- إذا كانت الجملة كلها مخالفة → احذفها بالكامل.
- اللغة: عربية فصحى.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{forbidden_rules}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📝 النصوص للمراجعة (JSON):
{blocks_json}

أعد JSON فقط بهذا الشكل:
{{
  "results": [
    {{
      "block_id": 1,
      "clean_text": "...",
      "changed": true,
      "violations": ["تشبيه", "إضافة غير موثقة"],
      "reason": "سبب مختصر"
    }},
    ...
  ]
}}

إذا كان النص نظيفاً:
  "changed": false, "violations": [], "clean_text" = نفس النص الأصلي.
"""

# ═══════════════════════════════════════════════════════════
# قراءة البيانات
# ═══════════════════════════════════════════════════════════
def load_story_meta(story_id: str) -> dict:
    library = json.loads(STORIES_JSON.read_text(encoding="utf-8"))
    story = next((s for s in library["stories"] if s["id"] == story_id), None)
    if not story:
        raise RuntimeError(f"القصة '{story_id}' غير موجودة في stories.json")
    return story


def load_collected(story_id: str) -> dict:
    """
    يقرأ:
      collected/events/event_NN/ayahs.json
      collected/events/event_NN/narrative.json
    ويعيد:
      {event_id: {"ayahs": [...], "narrative": [...]}}
    """
    base = STORIES_DIR / story_id / "collected" / "events"
    if not base.exists():
        raise RuntimeError(f"لم يُجمع بعد: {base}")

    events = {}
    for ev_dir in sorted(base.glob("event_*")):
        event_id = ev_dir.name
        ayahs = []
        narrative = []

        ayahs_file = ev_dir / "ayahs.json"
        if ayahs_file.exists():
            ayahs = json.loads(ayahs_file.read_text(encoding="utf-8"))

        narrative_file = ev_dir / "narrative.json"
        if narrative_file.exists():
            narrative = json.loads(narrative_file.read_text(encoding="utf-8"))

        events[event_id] = {"ayahs": ayahs, "narrative": narrative}

    print(f"📂 {len(events)} حدث محمّل من {base}")
    return events


# ═══════════════════════════════════════════════════════════
# تقسيم الـ blocks الطويلة
# ═══════════════════════════════════════════════════════════
def preprocess_blocks(blocks: list) -> list:
    """
    - يتجاهل blocks القصيرة جداً
    - يُقسّم blocks الأطول من MAX_BLOCK_LENGTH لجمل
    - يعيد قائمة جديدة مع index مرجعي
    """
    out = []
    for original in blocks:
        text = (original.get("text") or "").strip()
        if len(text) < MIN_BLOCK_LENGTH:
            # نبقيه بدون تدقيق (يُنسخ كما هو)
            out.append({
                "original": original,
                "audit_needed": False,
                "text": text,
            })
            continue

        if len(text) > MAX_BLOCK_LENGTH:
            # تقسيم لجمل
            sentences = re.split(r'(?<=[.!؟])\s+', text)
            buf = ""
            for sent in sentences:
                if len(buf) + len(sent) < MAX_BLOCK_LENGTH:
                    buf += " " + sent
                else:
                    if buf.strip():
                        out.append({
                            "original": {**original, "text": buf.strip()},
                            "audit_needed": True,
                            "text": buf.strip(),
                        })
                    buf = sent
            if buf.strip():
                out.append({
                    "original": {**original, "text": buf.strip()},
                    "audit_needed": True,
                    "text": buf.strip(),
                })
        else:
            out.append({
                "original": original,
                "audit_needed": True,
                "text": text,
            })
    return out


# ═══════════════════════════════════════════════════════════
# استدعاء LLM للتدقيق (batch)
# ═══════════════════════════════════════════════════════════
def audit_batch(batch: list) -> list:
    """
    batch: list of dicts (each with block_id + text)
    returns: list of dicts {block_id, clean_text, changed, violations, reason}
    """
    if not batch:
        return []

    blocks_json = json.dumps(
        [{"block_id": b["block_id"], "text": b["text"]} for b in batch],
        ensure_ascii=False,
        indent=2
    )

    prompt = AUDIT_PROMPT_TEMPLATE.format(
        forbidden_rules=get_llm_forbidden_rules(),
        blocks_json=blocks_json
    )

    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:3]

    for model in models:
        if breaker.is_open(f"model:{model}"):
            continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "maxOutputTokens": 8192,
                        "responseMimeType": "application/json",
                    }
                }
                r = requests.post(url, json=body, timeout=180)
                if r.status_code == 429:
                    pool.mark_failed(3600)
                    continue
                if r.status_code != 200:
                    continue

                parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                if not parts:
                    continue

                text = parts[0].get("text", "")
                s, e = text.find("{"), text.rfind("}")
                if s == -1:
                    continue
                parsed = json.loads(text[s:e+1])
                results = parsed.get("results", [])
                if results:
                    pool._advance()
                    breaker.record_success(f"model:{model}")
                    return results
            except Exception as e:
                print(f"      ⚠️ {model} محاولة {attempt+1}: {str(e)[:80]}")
                continue
    return []


# ═══════════════════════════════════════════════════════════
# تدقيق حدث واحد
# ═══════════════════════════════════════════════════════════
def audit_event(event_id: str, event_data: dict) -> tuple[dict, dict]:
    """
    يدقّق narrative blocks لحدث واحد.
    يعيد: (audited_narrative, audit_details)
    """
    blocks = event_data["narrative"]
    if not blocks:
        print(f"   {event_id}: لا سرد للتدقيق")
        return [], {"blocks_total": 0, "blocks_changed": 0, "changes": []}

    print(f"   {event_id}: {len(blocks)} block")

    # 1. preprocessing
    preprocessed = preprocess_blocks(blocks)

    # 2. تجميع blocks للتدقيق في دفعات
    to_audit = []
    for idx, item in enumerate(preprocessed):
        if item["audit_needed"]:
            to_audit.append({
                "block_id": idx,
                "text": item["text"],
            })

    # 3. استدعاء LLM بدفعات
    audit_results = {}
    total_batches = (len(to_audit) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(to_audit), BATCH_SIZE):
        batch = to_audit[i:i+BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"      🔍 دفعة {batch_num}/{total_batches} ({len(batch)} blocks)")
        results = audit_batch(batch)
        for res in results:
            bid = res.get("block_id")
            if bid is not None:
                audit_results[bid] = res
        time.sleep(1)

    # 4. دمج النتائج
    audited_narrative = []
    changes_log = []

    for idx, item in enumerate(preprocessed):
        original = item["original"]

        if not item["audit_needed"]:
            # block قصير → يُنسخ كما هو
            audited_narrative.append(original)
            continue

        result = audit_results.get(idx)
        if not result:
            # LLM فشل → نُبقي النص الأصلي (بأمان)
            print(f"      ⚠️ block {idx} بدون نتيجة → يُبقى كما هو")
            audited_narrative.append(original)
            continue

        clean_text = (result.get("clean_text") or "").strip()
        changed = result.get("changed", False)

        if changed:
            violations = result.get("violations", [])
            reason = result.get("reason", "")

            # إذا النص النظيف فارغ → نحذف الـ block
            if not clean_text or len(clean_text) < 10:
                changes_log.append({
                    "block_index": idx,
                    "action": "removed",
                    "original_text": item["text"][:500],
                    "violations": violations,
                    "reason": reason,
                })
                continue

            new_block = {
                "text": clean_text,
                "source": original.get("source", ""),
                "related_ayah": original.get("related_ayah"),
                "_audited": True,
            }
            audited_narrative.append(new_block)
            changes_log.append({
                "block_index": idx,
                "action": "rephrased",
                "original_text": item["text"][:500],
                "clean_text": clean_text[:500],
                "violations": violations,
                "reason": reason,
            })
        else:
            # لم يتغير
            audited_narrative.append(original)

    audit_details = {
        "blocks_total": len(blocks),
        "blocks_changed": len(changes_log),
        "changes": changes_log,
    }
    print(f"   ✅ {event_id}: {len(changes_log)} تعديل من {len(blocks)} block")
    return audited_narrative, audit_details


# ═══════════════════════════════════════════════════════════
# الحفظ
# ═══════════════════════════════════════════════════════════
def save_audited(story_id: str, events: dict, audit_log: dict, force: bool = False):
    """
    يحفظ:
    data/stories/{id}/audited/
      ├── events/event_NN/ayahs.json       (نسخة حرفية)
      ├── events/event_NN/narrative.json   (نظيف)
      ├── audit_log.json
      └── audit_summary.json
    """
    base = STORIES_DIR / story_id / "audited"
    if base.exists() and force:
        import shutil
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    for event_id, data in events.items():
        ev_dir = base / "events" / event_id
        ev_dir.mkdir(parents=True, exist_ok=True)

        (ev_dir / "ayahs.json").write_text(
            json.dumps(data["ayahs"], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        (ev_dir / "narrative.json").write_text(
            json.dumps(data["audited_narrative"], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    (base / "audit_log.json").write_text(
        json.dumps(audit_log, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    summary = {
        "story_id": story_id,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "events": {
            eid: {
                "blocks_total": d["details"]["blocks_total"],
                "blocks_changed": d["details"]["blocks_changed"],
            }
            for eid, d in events.items()
        },
        "total_blocks_changed": sum(
            d["details"]["blocks_changed"] for d in events.values()
        ),
    }
    (base / "audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    size_mb = sum(
        f.stat().st_size for f in base.rglob("*.json")
    ) / (1024 * 1024)
    print(f"💾 حُفظ: {base} ({size_mb:.2f} MB)")
    return size_mb


# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def audit_story(story_id: str, force: bool = False):
    print("=" * 60)
    print(f"🔍 تدقيق: {story_id}")
    print("=" * 60)

    story = load_story_meta(story_id)
    print(f"📖 {story['title']}")

    base = STORIES_DIR / story_id / "audited"
    if base.exists() and not force:
        print(f"ℹ️ موجود سابقاً: {base} (استخدم --force لإعادة التدقيق)")
        return

    collected = load_collected(story_id)
    if not collected:
        print("❌ لا توجد بيانات مجمّعة")
        sys.exit(1)

    audited_events = {}
    audit_log = {"story_id": story_id, "audited_at": datetime.now(timezone.utc).isoformat(), "events": {}}

    total_changes = 0
    for event_id, event_data in collected.items():
        print(f"\n📦 {event_id}")
        audited_narrative, details = audit_event(event_id, event_data)
        audited_events[event_id] = {
            "ayahs": event_data["ayahs"],  # لا تُمسّ
            "audited_narrative": audited_narrative,
            "details": details,
        }
        audit_log["events"][event_id] = details
        total_changes += details["blocks_changed"]

    size_mb = save_audited(story_id, audited_events, audit_log, force=force)

    notify_telegram(
        f"✅ اكتمل التدقيق: {story['title']}\n"
        f"📊 {total_changes} تعديل | {size_mb:.1f} MB",
        level="success"
    )

    print(f"\n🎉 اكتمل التدقيق! إجمالي التعديلات: {total_changes}")


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("الاستخدام: python audit_story.py <story_id> [--force]")
        sys.exit(1)

    story_id = sys.argv[1]
    force = "--force" in sys.argv

    try:
        audit_story(story_id, force=force)
    except Exception as e:
        print(f"❌ خطأ فادح: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
