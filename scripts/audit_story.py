"""
audit_story.py — المرحلة 2: التدقيق (نسخة البلوك الواحد لضمان الرد)
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

BATCH_SIZE = 1              # 🔴 تعديل مهم: بلوك واحد في كل مرة لتجنب الـ Timeout
MIN_BLOCK_LENGTH = 30       
MAX_BLOCK_LENGTH = 3000     

# ═══════════════════════════════════════════════════════════
# prompt التدقيق
# ═══════════════════════════════════════════════════════════
AUDIT_PROMPT_TEMPLATE = """أنت مدقّق شرعي متخصص في قصص الأنبياء. مهمتك مراجعة النص التالي (سرد قصصي) وحذف أي مخالفة.

قواعد التدقيق:
🔴 يُحذف: تشبيه الذات الإلهية، وصف شكل الأنبياء/الملائكة، الإسرائيليات، الإيحاء الجنسي.
🟢 يبقى: الأجواء، الحالة النفسية، الحوارات، الانتقالات الزمنية.

النص للمراجعة:
"{block_text}"

أعد JSON فقط:
{{
  "clean_text": "النص بعد الحذف (أو نفس النص لو سليم)",
  "changed": true_or_false,
  "violations": ["نوع المخالفة إن وجدت"],
  "reason": "سبب التعديل"
}}
"""

def load_collected(story_id: str) -> dict:
    base = STORIES_DIR / story_id / "collected" / "events"
    if not base.exists():
        raise RuntimeError(f"لم يُجمع بعد: {base}")
    events = {}
    for ev_dir in sorted(base.glob("event_*")):
        ayahs, narrative = [], []
        if (ev_dir / "ayahs.json").exists():
            ayahs = json.loads((ev_dir / "ayahs.json").read_text(encoding="utf-8"))
        if (ev_dir / "narrative.json").exists():
            narrative = json.loads((ev_dir / "narrative.json").read_text(encoding="utf-8"))
        events[ev_dir.name] = {"ayahs": ayahs, "narrative": narrative}
    print(f"📂 {len(events)} حدث محمّل.")
    return events

# ═══════════════════════════════════════════════════════════
# استدعاء LLM
# ═══════════════════════════════════════════════════════════
def audit_single_block(text: str) -> dict:
    prompt = AUDIT_PROMPT_TEMPLATE.format(block_text=text)
    pool = get_pool()
    breaker = get_breaker()
    models = ["gemini-1.5-flash", "gemini-1.5-pro"] + discover_and_cache(force=False)[:2]
    models = list(dict.fromkeys(models))

    for model in models:
        if breaker.is_open(f"model:{model}"): continue
        for attempt in range(3):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096, "responseMimeType": "application/json"}
                }
                r = requests.post(url, json=body, timeout=(20, 120)) # مهلة أقصر عشان منعلقش
                
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts: continue
                    res_text = parts[0].get("text", "")
                    s, e = res_text.find("{"), res_text.rfind("}")
                    if s == -1: continue
                    parsed = json.loads(res_text[s:e+1])
                    pool._advance() if hasattr(pool, '_advance') else pool.advance()
                    breaker.record_success(f"model:{model}")
                    return parsed
                elif r.status_code == 429:
                    print(f"      ⚠️ 429 Rate Limit - تدوير المفتاح")
                    pool.mark_failed(3600)
                    continue
                else:
                    print(f"      ⚠️ HTTP {r.status_code}")
                    
            except requests.exceptions.Timeout:
                print(f"      ⏱️ Timeout (موديل: {model})")
                pool.mark_failed(600)
                continue
            except json.JSONDecodeError:
                print(f"      🧨 الرد ليس JSON صالح")
                continue
            except Exception as e:
                print(f"      ⚠️ خطأ: {str(e)[:60]}")
                continue
    return {}

# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def audit_story(story_id: str, force: bool = False):
    print("=" * 60)
    print(f"🔍 تدقيق: {story_id}")
    print("=" * 60)
    
    collected = load_collected(story_id)
    audited_dir = STORIES_DIR / story_id / "audited"
    audited_dir.mkdir(parents=True, exist_ok=True)

    total_changes = 0
    for event_id, event_data in collected.items():
        print(f"\n📦 {event_id}")
        ev_ayahs = event_data["ayahs"]
        ev_narrative = event_data["narrative"]
        
        audited_narrative = []
        for idx, block in enumerate(ev_narrative):
            text = block.get("text", "").strip()
            if len(text) < MIN_BLOCK_LENGTH:
                audited_narrative.append(block)
                continue

            print(f"   🔍 تدقيق block {idx+1}/{len(ev_narrative)}...")
            result = audit_single_block(text)
            
            if result and "clean_text" in result:
                if result.get("changed"):
                    total_changes += 1
                    clean_txt = result["clean_text"].strip()
                    if len(clean_txt) > 10:
                        audited_narrative.append({**block, "text": clean_txt, "_audited": True})
                    else:
                        print(f"      🗑️ تم حذف block {idx+1}")
                else:
                    audited_narrative.append(block)
            else:
                print(f"      ⚠️ فشل، يُبقى كما هو")
                audited_narrative.append(block)
            time.sleep(1)

        # حفظ الحدث
        ev_dir = audited_dir / "events" / event_id
        ev_dir.mkdir(parents=True, exist_ok=True)
        (ev_dir / "ayahs.json").write_text(json.dumps(ev_ayahs, ensure_ascii=False, indent=2), encoding="utf-8")
        (ev_dir / "narrative.json").write_text(json.dumps(audited_narrative, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ✅ {event_id}: تم الحفظ")

    print(f"\n🎉 اكتمل التدقيق! إجمالي التعديلات: {total_changes}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    audit_story(sys.argv[1], force="--force" in sys.argv)
