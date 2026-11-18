"""
audit_story.py — المرحلة 2: التدقيق الشرعي واللغوي والتسلسلي الصارم
- يقرأ strict_mode و special_rules من stories.json.
- يدقق كل حدث بالكامل مع منع التكرار مع الأحداث السابقة.
- يدمج ويزيل التكرار بين الفقرات التي تشرح نفس الآية (anchor_ayah) دون اختصار مخل.
- يصحح أي ركاكة نحوية أو لغوية أو التباس لفظي عقدي.
- يستبعد المفاتيح المعطوبة (401/403) فوراً ويعالج ضغط 503 و thinkingConfig.
"""

import sys
import json
import time
import shutil
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import get_breaker

STORIES_JSON = _REPO_ROOT / "data" / "library" / "stories.json"
STORIES_DIR  = _REPO_ROOT / "data" / "stories"


def _advance_pool(pool):
    if hasattr(pool, "_advance"):
        pool._advance()
    elif hasattr(pool, "advance"):
        pool.advance()


def _thinking_config(model: str) -> dict | None:
    m = model.lower()
    if any(x in m for x in ["gemini-3", "gemini-4"]):
        return {"thinkingLevel": "low"}
    if "gemini-2.5" in m:
        return {"thinkingBudget": 512}
    return None


def load_story_meta(story_id: str) -> dict:
    if not STORIES_JSON.exists():
        return {}
    library = json.loads(STORIES_JSON.read_text(encoding="utf-8"))
    return next((s for s in library.get("stories", []) if s["id"] == story_id), {})


def load_collected(story_id: str) -> list[dict]:
    base = STORIES_DIR / story_id / "collected" / "events"
    if not base.exists():
        raise RuntimeError(f"لم يُجمع بعد: {base}")

    events = []
    for ev_dir in sorted(base.glob("event_*")):
        if not ev_dir.is_dir():
            continue
        meta = json.loads((ev_dir / "meta.json").read_text(encoding="utf-8")) if (ev_dir / "meta.json").exists() else {}
        ayahs = json.loads((ev_dir / "ayahs.json").read_text(encoding="utf-8")) if (ev_dir / "ayahs.json").exists() else []
        narrative = json.loads((ev_dir / "narrative.json").read_text(encoding="utf-8")) if (ev_dir / "narrative.json").exists() else []
        events.append({
            "event_id": ev_dir.name,
            "meta": meta,
            "ayahs": ayahs,
            "narrative": narrative,
        })
    print(f"📂 {len(events)} حدث محمّل للتدقيق.")
    return events


def call_audit_llm(prompt: str) -> dict | None:
    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:10]
    if not models:
        return None

    for model in models:
        if breaker.is_open(f"model:{model}"):
            continue

        use_thinking = True
        valid_tries = 0
        while valid_tries < 2:
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                gen_cfg = {
                    "temperature": 0.1,
                    "maxOutputTokens": 6144,
                    "responseMimeType": "application/json",
                }
                if use_thinking:
                    tc = _thinking_config(model)
                    if tc:
                        gen_cfg["thinkingConfig"] = tc

                body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
                r = requests.post(url, json=body, timeout=(15, 90))

                if r.status_code == 200:
                    resp = r.json()
                    cand = (resp.get("candidates") or [{}])[0]
                    finish = cand.get("finishReason", "")
                    parts = cand.get("content", {}).get("parts", [])
                    if not parts:
                        print(f"   ⚠️ {model}: رد فارغ (finish={finish})", flush=True)
                        break
                    text_parts = [p.get("text", "") for p in parts if not p.get("thought") and p.get("text")]
                    txt = "\n".join(text_parts) if text_parts else parts[-1].get("text", "")
                    s, e = txt.find("{"), txt.rfind("}")
                    if s == -1 or e <= s:
                        print(f"   ⚠️ {model}: JSON ناقص (finish={finish})", flush=True)
                        break
                    parsed = json.loads(txt[s:e + 1])
                    _advance_pool(pool)
                    breaker.record_success(f"model:{model}")
                    return parsed
                elif r.status_code == 401 or (r.status_code == 403 and "api key" in r.text.lower()):
                    pool.mark_dead(f"HTTP {r.status_code}")
                    continue
                elif r.status_code == 400 and use_thinking and "thinking" in r.text.lower():
                    use_thinking = False
                    continue
                elif r.status_code == 429:
                    print(f"   ⏸️ {model}: 429", flush=True)
                    pool.mark_failed(75)
                    valid_tries += 1
                    time.sleep(2)
                    continue
                elif r.status_code in (400, 403, 404):
                    print(f"   ⚠️ {model}: HTTP {r.status_code}", flush=True)
                    breaker.record_failure(f"model:{model}")
                    break
                else:
                    print(f"   ⚠️ {model}: HTTP {r.status_code}", flush=True)
                    valid_tries += 1
                    _advance_pool(pool)
                    time.sleep(4)
                    continue
            except requests.exceptions.Timeout:
                print(f"   ⏱️ {model}: Timeout", flush=True)
                _advance_pool(pool)
                break
            except Exception as e:
                print(f"   ⚠️ {model}: {str(e)[:80]}", flush=True)
                break
    return None


def audit_event(event: dict, story_meta: dict, prior_summaries: list[str]) -> dict | None:
    guide = story_meta.get("story_guide", {})
    strict_mode = bool(guide.get("strict_mode", False))
    special_rules = "\n".join(f"- {r}" for r in guide.get("special_rules", [])) or "- الالتزام بالمنهج الصحيح"

    strict_rules = (
        "🔴 وضع الصرامة القصوى (عهد بداية الخلق والميثاق):\n"
        "1. يُحذف فوراً أي تشبيه للذات الإلهية أو تكييف أو تأويل للصفات، وأي لفظ يوحي بالتباس عقدي.\n"
        "2. يُحذف فوراً أي وصف حسي لشكل الأنبياء أو الملائكة.\n"
        "3. تُحذف أي إسرائيليات أو صيغ تمريض (يُروى، قيل، ذكر المفسرون) — فقط الموثق بالقرآن والسنة الصحيحة.\n"
        "4. يُمنع ذكر اسم 'عزرائيل' لملك الموت."
    ) if strict_mode else (
        "🟡 قواعد التدقيق الشرعي:\n"
        "1. يُحذف تشبيه الذات الإلهية ووصف شكل الأنبياء/الملائكة والإيحاءات.\n"
        "2. تُحذف الإسرائيليات والأحاديث الضعيفة والموضوعة.\n"
        "3. أي تفصيل تفسيري غير قطعي يُحافظ فيه على صيغة التمريض مع (والله أعلم)."
    )

    ayahs_text = "\n".join(
        f"- [{a['ref']}] ({a.get('surah_name', '')}): {a['text']}"
        for a in event["ayahs"]
    ) or "(لا توجد آيات)"

    blocks_json = json.dumps(event["narrative"], ensure_ascii=False, indent=2)
    prior_str = "\n".join(f"- {s}" for s in prior_summaries) or "(لا توجد أحداث سابقة)"
    ev_title = event["meta"].get("title") or event["event_id"]
    timeline_step = event["meta"].get("timeline_step") or ev_title

    prompt = f"""أنت مدقق شرعي ولغوي ومنهجي صارم على منهج أهل السنة والجماعة.

القصة: "{story_meta.get('title', '')}"
الحدث الحالي ({event['event_id']}): "{ev_title}"
الخطوة الزمنية المستهدفة: "{timeline_step}"

{strict_rules}

قواعد القصة الخاصة:
{special_rules}

الأحداث السابقة التي تم تدقيقها (احذف أي تكرار لها هنا):
{prior_str}

الآيات المرفقة مع الحدث:
{ayahs_text}

فقرات السرد المطلوب تدقيقها:
{blocks_json}

المطلوب بدقة:
1. دقّق كل فقرة سرد وأزل منها: أي مخالفة شرعية، أي حشو، وأي خروج عن الخطوة الزمنية الحالية ("{timeline_step}") أو تكرار للأحداث السابقة.
2. **دمج التكرار داخل الحدث**: إذا كانت هناك فقرتان تشرحان نفس الآية (`anchor_ayah`) وتكرران نفس المعنى، فادمج الفوائد الموثقة في صياغة واحدة غنية ومتكاملة دون تكرار، ولا تختصر التفاصيل المهمة اختصاراً مخلاً.
3. **التدقيق اللغوي والنحوي الصارم**: صحّح فوراً أي خطأ نحوي أو ركاكة تعبيرية أو التباس لفظي (مثل خطأ التذكير والتأنيث أو تعبير غير فصيح مثل "استوت خلقه" ← "خلق الله").
4. تأكد أن فقرات السرد لا تحتوي نصاً قرآنياً حرفياً بين ﴿ ﴾ (يجب أن تشرح المعنى والحدث، بينما الآية تبقى في `keep_ayah_refs` للتلاوة المستقلة).
5. تأكد أن كل آية في `keep_ayah_refs` تخدم وتؤكد فقرات هذا الحدث فعلاً — واحذف أي آية مقحمة أو غير مرتبطة.
6. اربط كل فقرة نظيفة في `clean_blocks` بحقل `anchor_ayah` من `keep_ayah_refs` (إن وُجدت).

أعد JSON فقط:
{{
  "keep_ayah_refs": ["112:1"],
  "clean_blocks": [
    {{
      "source": "المصدر",
      "text": "النص المدقق النظيف الفصيح...",
      "anchor_ayah": "112:1"
    }}
  ],
  "changes_summary": "ملخص التعديلات أو 'سليم'",
  "event_summary": "ملخص من سطر واحد للحدث بعد التدقيق"
}}"""

    res = call_audit_llm(prompt)
    if not res or not res.get("clean_blocks"):
        return None

    keep_refs = set(res.get("keep_ayah_refs", []))
    audited_ayahs = [a for a in event["ayahs"] if a["ref"] in keep_refs]
    valid_refs = {a["ref"] for a in audited_ayahs}

    audited_narrative = []
    for b in res.get("clean_blocks", []):
        txt = (b.get("text") or "").strip()
        if len(txt) < 20:
            continue
        anchor = (b.get("anchor_ayah") or "").strip()
        if anchor not in valid_refs:
            anchor = audited_ayahs[0]["ref"] if audited_ayahs else ""
        audited_narrative.append({
            "source": (b.get("source") or "مصدر موثق").strip(),
            "text": txt,
            "anchor_ayah": anchor,
            "_audited": True,
        })

    if not audited_narrative:
        return None

    return {
        "meta": event["meta"],
        "ayahs": audited_ayahs,
        "narrative": audited_narrative,
        "changes": res.get("changes_summary", ""),
        "summary": res.get("event_summary", ev_title),
    }


def audit_story(story_id: str, force: bool = False):
    print("=" * 65)
    print(f"🔍 التدقيق الشرعي واللغوي والتسلسلي: {story_id}")
    print("=" * 65)

    story_meta = load_story_meta(story_id)
    if not story_meta:
        raise RuntimeError(f"❌ القصة '{story_id}' غير موجودة في stories.json")

    collected = load_collected(story_id)
    if not collected:
        raise RuntimeError("❌ لا توجد أحداث في collected/")

    audited_root = STORIES_DIR / story_id / "audited"
    tmp_events = audited_root / "_events_tmp"
    if tmp_events.exists():
        shutil.rmtree(tmp_events)
    tmp_events.mkdir(parents=True, exist_ok=True)

    prior_summaries: list[str] = []

    for idx, ev in enumerate(collected, 1):
        ev_id = ev["event_id"]
        print(f"\n📦 [{idx}/{len(collected)}] تدقيق {ev_id}...")

        audited_ev = None
        for attempt in range(2):
            audited_ev = audit_event(ev, story_meta, prior_summaries)
            if audited_ev:
                break
            print(f"   🔁 إعادة محاولة التدقيق لـ {ev_id}...")
            time.sleep(2)

        if not audited_ev:
            shutil.rmtree(tmp_events, ignore_errors=True)
            raise RuntimeError(f"❌ فشل تدقيق {ev_id} — تم الإيقاف لمنع تمرير نص غير مدقق!")

        ev_dir = tmp_events / ev_id
        ev_dir.mkdir(parents=True, exist_ok=True)
        (ev_dir / "meta.json").write_text(
            json.dumps(audited_ev["meta"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (ev_dir / "ayahs.json").write_text(
            json.dumps(audited_ev["ayahs"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (ev_dir / "narrative.json").write_text(
            json.dumps(audited_ev["narrative"], ensure_ascii=False, indent=2), encoding="utf-8"
        )

        prior_summaries.append(f"{ev_id}: {audited_ev['summary']}")
        print(f"   ✅ {ev_id}: {len(audited_ev['narrative'])} فقرة مدققة | {len(audited_ev['ayahs'])} آية | {audited_ev['changes']}")
        time.sleep(1)

    final_events = audited_root / "events"
    if final_events.exists():
        shutil.rmtree(final_events)
    tmp_events.rename(final_events)

    print(f"\n🎉 اكتمل التدقيق الصارم لـ {len(collected)} حدث بنجاح!")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("الاستخدام: python audit_story.py <story_id>")
        sys.exit(1)
    try:
        audit_story(sys.argv[1], force="--force" in sys.argv)
    except Exception as e:
        print(f"\n❌ {e}")
        sys.exit(1)
