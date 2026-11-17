"""
audit_story.py — المرحلة 2: التدقيق (نسخة ذكية ومقاومة للـ Timeouts)
يقرأ collected/events/*/narrative.json ويدقّق كل block عبر LLM.
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
# المسارات والإعدادات
# ═══════════════════════════════════════════════════════════
STORIES_JSON   = _REPO_ROOT / "data" / "library" / "stories.json"
STORIES_DIR    = _REPO_ROOT / "data" / "stories"

BATCH_SIZE = 2              # ✅ تقليل الحجم لتجنب الـ Timeout
MIN_BLOCK_LENGTH = 30       
MAX_BLOCK_LENGTH = 2000     # ✅ تقليل الطول الأقصى

# ═══════════════════════════════════════════════════════════
# prompt التدقيق
# ═══════════════════════════════════════════════════════════
AUDIT_PROMPT_TEMPLATE = """أنت مدقّق شرعي متخصص في قصص الأنبياء. مهمتك مراجعة النصوص التالية (سرد قصصي، ليست آيات قرآنية) وحذف أي مخالفة.

قواعد التدقيق الصارمة:
🔴 مخالفات تُحذف دائماً:
1. أي تشبيه أو تجسيد للذات الإلهية.
2. أي وصف لشكل أو هيئة أو ملامح الأنبياء / الملائكة / الصحابة.
3. الإسرائيليات (روايات من كتب أهل الكتاب لم تثبت في الإسلام).
4. أي حديث ضعيف أو موضوع يُنسب إلى النبي ﷺ.
5. أي معلومة دينية صريحة لا يوجد لها أصل واضح.
6. أي إيحاء جنسي أو تعري.

🟢 ما يبقى (وصف قصصي مشروع):
- المشاهد الطبيعية والأجواء.
- الحالة النفسية بدون وصف جسدي.
- الحوارات والأسئلة.
- انتقالات زمنية ومكانية.
- العبرة والعظة العامة.

📋 قواعد إعادة الصياغة:
- احذف الجزء المخالف فقط، وحافظ على بقية الجملة.
- لا تُغيّر الأسماء الصحيحة أو الآيات المرجعية.
- إذا كانت الجملة كلها مخالفة → احذفها بالكامل.

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
      "violations": ["تشبيه"],
      "reason": "سبب مختصر"
    }}
  ]
}}
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
    base = STORIES_DIR / story_id / "collected" / "events"
    if not base.exists():
        raise RuntimeError(f"لم يُجمع بعد: {base}")

    events = {}
    for ev_dir in sorted(base.glob("event_*")):
        event_id = ev_dir.name
        ayahs, narrative = [], []
        if (ev_dir / "ayahs.json").exists():
            ayahs = json.loads((ev_dir / "ayahs.json").read_text(encoding="utf-8"))
        if (ev_dir / "narrative.json").exists():
            narrative = json.loads((ev_dir / "narrative.json").read_text(encoding="utf-8"))
        events[event_id] = {"ayahs": ayahs, "narrative": narrative}
    print(f"📂 {len(events)} حدث محمّل من {base}")
    return events

def preprocess_blocks(blocks: list) -> list:
    out = []
    for original in blocks:
        text = (original.get("text") or "").strip()
        if len(text) < MIN_BLOCK_LENGTH:
            out.append({"original": original, "audit_needed": False, "text": text})
            continue

        if len(text) > MAX_BLOCK_LENGTH:
            sentences = re.split(r'(?<=[.!؟])\s+', text)
            buf = ""
            for sent in sentences:
                if len(buf) + len(sent) < MAX_BLOCK_LENGTH:
                    buf += " " + sent
                else:
                    if buf.strip():
                        out.append({"original": {**original, "text": buf.strip()}, "audit_needed": True, "text": buf.strip()})
                    buf = sent
            if buf.strip():
                out.append({"original": {**original, "text": buf.strip()}, "audit_needed": True, "text": buf.strip()})
        else:
            out.append({"original": original, "audit_needed": True, "text": text})
    return out

# ═══════════════════════════════════════════════════════════
# [ذكاء] استدعاء LLM مع معالجة Timeouts
# ═══════════════════════════════════════════════════════════
def audit_batch(batch: list) -> list:
    if not batch:
        return []

    blocks_json = json.dumps([{"block_id": b["block_id"], "text": b["text"]} for b in batch], ensure_ascii=False, indent=2)
    prompt = AUDIT_PROMPT_TEMPLATE.format(forbidden_rules=get_llm_forbidden_rules(), blocks_json=blocks_json)

    pool = get_pool()
    breaker = get_breaker()
    # ✅ تثبيت موديلات معروفة بالاستقرار لتجنب موديلات الـ Alias
    models = ["gemini-1.5-flash", "gemini-1.5-pro"] + discover_and_cache(force=False)[:2]
    models = list(dict.fromkeys(models)) # إزالة التكرار

    for model in models:
        if breaker.is_open(f"model:{model}"): continue
        for attempt in range(3):  # 3 محاولات
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096, "responseMimeType": "application/json"}
                }
                
                # ✅ مهلة ذكية: 20 ثانية للاتصال، 300 ثانية للقراءة
                r = requests.post(url, json=body, timeout=(20, 300))
                
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts: continue
                    text = parts[0].get("text", "")
                    s, e = text.find("{"), text.rfind("}")
                    if s == -1: continue
                    parsed = json.loads(text[s:e+1])
                    results = parsed.get("results", [])
                    if results:
                        if hasattr(pool, '_advance'): pool._advance()
                        elif hasattr(pool, 'advance'): pool.advance()
                        breaker.record_success(f"model:{model}")
                        return results
                elif r.status_code == 429:
                    print(f"      ⚠️ 429 (Rate Limit) على {model} - تدوير المفتاح")
                    pool.mark_failed(3600)
                    continue # جرب مفتاح تاني
                elif r.status_code in (404, 503):
                    breaker.record_failure(f"model:{model}")
                    break # جرب موديل تاني
                    
            except requests.exceptions.Timeout:
                print(f"      ⏱️ Timeout على {model} محاولة {attempt+1} - تدوير المفتاح")
                pool.mark_failed(600) # اعزل المفتاح ده 10 دقايق
                continue
            except Exception as e:
                print(f"      ⚠️ خطأ {model}: {str(e)[:60]}")
                continue
    return []

# ═══════════════════════════════════════════════════════════
# تدقيق حدث واحد
# ═══════════════════════════════════════════════════════════
def audit_event(event_id: str, event_data: dict) -> tuple[dict, dict]:
    blocks = event_data["narrative"]
    if not blocks:
        print(f"   {event_id}: لا سرد للتدقيق")
        return [], {"blocks_total": 0, "blocks_changed": 0, "changes": []}

    print(f"   {event_id}: {len(blocks)} block")
    preprocessed = preprocess_blocks(blocks)

    to_audit = [{"block_id": idx, "text": item["text"]} for idx, item in enumerate(preprocessed) if item["audit_needed"]]

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

    audited_narrative = []
    changes_log = []

    for idx, item in enumerate(preprocessed):
        original = item["original"]
        if not item["audit_needed"]:
            audited_narrative.append(original)
            continue

        result = audit_results.get(idx)
        if not result:
            # ✅ حماية: الاحتفاظ بالنص الأصلي لو فشل التدقيق
            print(f"      ⚠️ block {idx} بدون نتيجة → يُبقى كما هو")
            audited_narrative.append(original)
            continue

        clean_text = (result.get("clean_text") or "").strip()
        changed = result.get("changed", False)

        if changed:
            violations, reason = result.get("violations", []), result.get("reason", "")
            if not clean_text or len(clean_text) < 10:
                changes_log.append({"block_index": idx, "action": "removed", "original_text": item["text"][:500], "violations": violations, "reason": reason})
                continue
            new_block = {"text": clean_text, "source": original.get("source", ""), "related_ayah": original.get("related_ayah"), "_audited": True}
            audited_narrative.append(new_block)
            changes_log.append({"block_index": idx, "action": "rephrased", "original_text": item["text"][:500], "clean_text": clean_text[:500], "violations": violations, "reason": reason})
        else:
            audited_narrative.append(original)

    return audited_narrative, {"blocks_total": len(blocks), "blocks_changed": len(changes_log), "changes": changes_log}

# ═══════════════════════════════════════════════════════════
# الحفظ والدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def save_audited(story_id: str, events: dict, audit_log: dict, force: bool = False):
    base = STORIES_DIR / story_id / "audited"
    if base.exists() and force:
        import shutil
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    for event_id, data in events.items():
        ev_dir = base / "events" / event_id
        ev_dir.mkdir(parents=True, exist_ok=True)
        (ev_dir / "ayahs.json").write_text(json.dumps(data["ayahs"], ensure_ascii=False, indent=2), encoding="utf-8")
        (ev_dir / "narrative.json").write_text(json.dumps(data["audited_narrative"], ensure_ascii=False, indent=2), encoding="utf-8")

    (base / "audit_log.json").write_text(json.dumps(audit_log, ensure_ascii=False, indent=2), encoding="utf-8")
    
    summary = {
        "story_id": story_id, "audited_at": datetime.now(timezone.utc).isoformat(),
        "events": {eid: {"blocks_total": d["details"]["blocks_total"], "blocks_changed": d["details"]["blocks_changed"]} for eid, d in events.items()},
        "total_blocks_changed": sum(d["details"]["blocks_changed"] for d in events.values()),
    }
    (base / "audit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 حُفظ: {base}")

def audit_story(story_id: str, force: bool = False):
    print("=" * 60)
    print(f"🔍 تدقيق: {story_id}")
    print("=" * 60)
    story = load_story_meta(story_id)
    print(f"📖 {story['title']}")

    collected = load_collected(story_id)
    if not collected:
        print("❌ لا توجد بيانات مجمّعة")
        sys.exit(1)

    audited_events, audit_log = {}, {"story_id": story_id, "audited_at": datetime.now(timezone.utc).isoformat(), "events": {}}
    total_changes = 0
    
    for event_id, event_data in collected.items():
        print(f"\n📦 {event_id}")
        audited_narrative, details = audit_event(event_id, event_data)
        audited_events[event_id] = {"ayahs": event_data["ayahs"], "audited_narrative": audited_narrative, "details": details}
        audit_log["events"][event_id] = details
        total_changes += details["blocks_changed"]

    save_audited(story_id, audited_events, audit_log, force=force)
    print(f"\n🎉 اكتمل التدقيق! إجمالي التعديلات: {total_changes}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("الاستخدام: python audit_story.py <story_id> [--force]")
        sys.exit(1)
    audit_story(sys.argv[1], force="--force" in sys.argv)
