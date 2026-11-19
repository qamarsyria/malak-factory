"""
generate_story.py — المرحلة 3: تقسيم الأحداث وتوليد الحلقات
- يقرأ audited/events/ بالترتيب الزمني الدقيق.
- يبني كل حلقة بنمط: [فقرة سرد موثقة ← الآية المؤكدة لها مباشرة].
- يمنع إقحام آيات من خارج الحدث أو تسرب نص الآية حرفياً داخل السرد (دون حظر الكلمات المفردة).
- يمنع تتابع آيتين مباشرة إلا إذا كانتا من نفس السورة ومتتاليتين بالأرقام لتجنب الفهم الخاطئ.
- يعتمد حداً أدنى مرناً للكلمات حسب حجم المادة المدققة لمنع الحشو والاختلاق.
- يستبعد المفاتيح المعطوبة (401/403) ويعالج ضغط 503 و Timeout و thinkingConfig.
- يستخدم story_library لتحديث العصر والقصة التالية بشكل صحيح.
"""

import os
import re
import sys
import json
import time
import random
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import get_breaker, notify_telegram
from filters_loader import get_llm_forbidden_rules
from outro_templates import OUTRO_TEMPLATES
from story_library import (
    load_global,
    load_story_state,
    save_story_state,
    get_story_by_id,
    mark_story_complete,
    advance_to_next_story,
)

STORIES_DIR    = _REPO_ROOT / "data" / "stories"
LATEST_EPISODE = _REPO_ROOT / "out" / "latest_episode.txt"

SPLIT_THRESHOLD_AYAHS = 6
SPLIT_THRESHOLD_WORDS = 950
TARGET_WORDS_CAP      = 850

_LAST_LLM_ERROR = ""

# أسماء مهملة تحولها Google تلقائياً إلى النماذج الأحدث وتسبب تكرار المحاولات بلا فائدة
_DEPRECATED_MODEL_ALIASES = {
    "gemini-3.7-flash": "gemini-3.8-flash",
    "gemini-3.5-flash": "gemini-3.6-flash",
}
_MODEL_PENALTIES: dict[str, int] = {}

_SURAH_NAMES = {
    1: "الفاتحة", 2: "البقرة", 3: "آل عمران", 4: "النساء", 5: "المائدة",
    6: "الأنعام", 7: "الأعراف", 8: "الأنفال", 9: "التوبة", 10: "يونس",
    11: "هود", 12: "يوسف", 13: "الرعد", 14: "إبراهيم", 15: "الحجر",
    16: "النحل", 17: "الإسراء", 18: "الكهف", 19: "مريم", 20: "طه",
    21: "الأنبياء", 22: "الحج", 23: "المؤمنون", 24: "النور", 25: "الفرقان",
    26: "الشعراء", 27: "النمل", 28: "القصص", 29: "العنكبوت", 30: "الروم",
    31: "لقمان", 32: "السجدة", 33: "الأحزاب", 34: "سبأ", 35: "فاطر",
    36: "يس", 37: "الصافات", 38: "ص", 39: "الزمر", 40: "غافر",
    41: "فصلت", 42: "الشورى", 43: "الزخرف", 44: "الدخان", 45: "الجاثية",
    46: "الأحقاف", 47: "محمد", 48: "الفتح", 49: "الحجرات", 50: "ق",
    51: "الذاريات", 52: "الطور", 53: "النجم", 54: "القمر", 55: "الرحمن",
    56: "الواقعة", 57: "الحديد", 58: "المجادلة", 59: "الحشر", 60: "الممتحنة",
    61: "الصف", 62: "الجمعة", 63: "المنافقون", 64: "التغابن", 65: "الطلاق",
    66: "التحريم", 67: "الملك", 68: "القلم", 69: "الحاقة", 70: "المعارج",
    71: "نوح", 72: "الجن", 73: "المزمل", 74: "المدثر", 75: "القيامة",
    76: "الإنسان", 77: "المرسلات", 78: "النبأ", 79: "النازعات", 80: "عبس",
    81: "التكوير", 82: "الانفطار", 83: "المطففين", 84: "الانشقاق", 85: "البروج",
    86: "الطارق", 87: "الأعلى", 88: "الغاشية", 89: "الفجر", 90: "البلد",
    91: "الشمس", 92: "الليل", 93: "الضحى", 94: "الشرح", 95: "التين",
    96: "العلق", 97: "القدر", 98: "البينة", 99: "الزلزلة", 100: "العاديات",
    101: "القارعة", 102: "التكاثر", 103: "العصر", 104: "الهمزة", 105: "الفيل",
    106: "قريش", 107: "الماعون", 108: "الكوثر", 109: "الكافرون", 110: "النصر",
    111: "المسد", 112: "الإخلاص", 113: "الفلق", 114: "الناس",
}

_ARABIC_UNITS    = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_ARABIC_TEENS    = ["عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
                    "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
_ARABIC_TENS     = ["", "عشرة", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_ARABIC_HUNDREDS = ["", "مئة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]


def _gh_output(**kwargs):
    out_file = os.environ.get("GITHUB_OUTPUT")
    if not out_file:
        return
    with open(out_file, "a", encoding="utf-8") as f:
        for k, v in kwargs.items():
            val = str(v).replace("\n", " ").replace("\r", " ")
            f.write(f"{k}={val}\n")


def _gh_summary(markdown_text: str):
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    with open(summary_file, "a", encoding="utf-8") as f:
        f.write(markdown_text.rstrip() + "\n\n")


def num_to_arabic(n: int) -> str:
    if n == 0:
        return "صفر"
    if n < 10:
        return _ARABIC_UNITS[n]
    if n < 20:
        return _ARABIC_TEENS[n - 10]
    if n < 100:
        tens, units = divmod(n, 10)
        return _ARABIC_TENS[tens] if units == 0 else f"{_ARABIC_UNITS[units]} و{_ARABIC_TENS[tens]}"
    if n < 1000:
        h, rem = divmod(n, 100)
        prefix = _ARABIC_HUNDREDS[h]
        return prefix if rem == 0 else f"{prefix} و{num_to_arabic(rem)}"
    if n < 1_000_000:
        th, rem = divmod(n, 1000)
        if th == 1:
            prefix = "ألف"
        elif th == 2:
            prefix = "ألفان"
        elif 3 <= th <= 10:
            prefix = f"{_ARABIC_UNITS[th]} آلاف"
        else:
            prefix = f"{num_to_arabic(th)} ألف"
        return prefix if rem == 0 else f"{prefix} و{num_to_arabic(rem)}"
    return str(n)


def arabize_numbers(text: str) -> str:
    return re.sub(r"\b(\d{1,6})\b", lambda m: num_to_arabic(int(m.group(0))), text)


def clean_narration(text: str) -> str:
    if not text:
        return ""
    text = arabize_numbers(text)
    text = re.sub(r"[a-zA-Z]+", "", text)
    text = re.sub(r"[\[\]\(\)\{\}<>﴿﴾]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm_ar(text: str) -> str:
    text = re.sub(r"[\u0640\u064B-\u065F\u0670\u06D6-\u06ED]", "", text or "")
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"[^\u0621-\u064A\s]", " ", text).strip()


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


def _prepare_models(raw_models: list[str]) -> list[str]:
    cleaned = []
    seen = set()
    for m in raw_models:
        base = m.removeprefix("models/").strip()
        canonical = _DEPRECATED_MODEL_ALIASES.get(base, base)
        if canonical and canonical not in seen:
            seen.add(canonical)
            cleaned.append(canonical)
    if "gemini-3.5-flash-lite" not in seen:
        cleaned.append("gemini-3.5-flash-lite")
    # تأخير النماذج التي تعرّضت لـ Timeout أو ضغط متكرر في المحاولات السابقة
    cleaned.sort(key=lambda m: _MODEL_PENALTIES.get(m, 0))
    return cleaned


def call_llm(prompt: str, is_json: bool = True, timeout: int = 60, max_tokens: int = 8192):
    global _LAST_LLM_ERROR
    _LAST_LLM_ERROR = ""

    pool = get_pool()
    breaker = get_breaker()
    raw_models = discover_and_cache(force=False)[:10]
    models = _prepare_models(raw_models) if raw_models else []
    if not models:
        _LAST_LLM_ERROR = "لا توجد موديلات Gemini متاحة من discover_and_cache"
        print(f"   ❌ {_LAST_LLM_ERROR}", flush=True)
        return None

    for model in models:
        if breaker.is_open(f"model:{model}"):
            _LAST_LLM_ERROR = f"{model}: circuit breaker مفتوح"
            continue

        use_thinking = True
        use_temperature = True
        valid_tries = 0
        dead_skips = 0
        while valid_tries < 2:
            try:
                key = pool.get_current()
                if not key:
                    _LAST_LLM_ERROR = "لا يوجد مفتاح Gemini سليم متاح في pool"
                    print(f"   ❌ {_LAST_LLM_ERROR}", flush=True)
                    return None
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                gen_cfg = {"maxOutputTokens": max_tokens}
                if use_temperature:
                    gen_cfg["temperature"] = 0.25
                if is_json:
                    gen_cfg["responseMimeType"] = "application/json"
                if use_thinking:
                    tc = _thinking_config(model)
                    if tc:
                        gen_cfg["thinkingConfig"] = tc

                body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
                r = requests.post(url, json=body, timeout=(15, timeout))

                if r.status_code == 200:
                    resp = r.json()
                    cand = (resp.get("candidates") or [{}])[0]
                    finish = cand.get("finishReason", "")
                    parts = cand.get("content", {}).get("parts", [])
                    if not parts:
                        _LAST_LLM_ERROR = f"{model}: رد فارغ (finish={finish})"
                        print(f"   ⚠️ {_LAST_LLM_ERROR}", flush=True)
                        break

                    text_parts = [p.get("text", "") for p in parts if not p.get("thought") and p.get("text")]
                    text = "\n".join(text_parts) if text_parts else parts[-1].get("text", "")

                    if is_json:
                        s, e = text.find("{"), text.rfind("}")
                        if s == -1 or e <= s:
                            _LAST_LLM_ERROR = f"{model}: JSON ناقص (finish={finish})"
                            print(f"   ⚠️ {_LAST_LLM_ERROR}", flush=True)
                            break
                        parsed = json.loads(text[s:e + 1])
                    else:
                        parsed = text.strip()

                    _advance_pool(pool)
                    breaker.record_success(f"model:{model}")
                    _MODEL_PENALTIES[model] = 0
                    _LAST_LLM_ERROR = ""
                    return parsed

                elif r.status_code == 401 or (r.status_code == 403 and "api key" in r.text.lower()):
                    _LAST_LLM_ERROR = f"{model}: مفتاح مرفوض (HTTP {r.status_code})"
                    pool.mark_dead(f"HTTP {r.status_code}")
                    dead_skips += 1
                    if dead_skips >= 25:
                        _LAST_LLM_ERROR = f"{model}: تعذر العثور على مفتاح سليم بعد {dead_skips} محاولة"
                        print(f"   ❌ {_LAST_LLM_ERROR}", flush=True)
                        break
                    continue

                elif r.status_code == 400 and use_thinking and "thinking" in r.text.lower():
                    use_thinking = False
                    continue

                elif r.status_code == 400 and use_temperature and "temperature" in r.text.lower():
                    use_temperature = False
                    continue

                elif r.status_code == 429:
                    _LAST_LLM_ERROR = f"{model}: حد الطلبات 429 (Rate Limit)"
                    print(f"   ⏸️ {model}: 429", flush=True)
                    pool.mark_failed(75)
                    valid_tries += 1
                    time.sleep(2)
                    continue

                elif r.status_code in (400, 403, 404):
                    _LAST_LLM_ERROR = f"{model}: HTTP {r.status_code}"
                    print(f"   ⚠️ {model}: HTTP {r.status_code}", flush=True)
                    breaker.record_failure(f"model:{model}")
                    break

                else:
                    _LAST_LLM_ERROR = f"{model}: HTTP {r.status_code}"
                    print(f"   ⚠️ {model}: HTTP {r.status_code}", flush=True)
                    valid_tries += 1
                    _advance_pool(pool)
                    time.sleep(3)
                    continue

            except requests.exceptions.Timeout:
                _LAST_LLM_ERROR = f"{model}: Timeout"
                print(f"   ⏱️ {model}: Timeout", flush=True)
                _MODEL_PENALTIES[model] = _MODEL_PENALTIES.get(model, 0) + 2
                _advance_pool(pool)
                break
            except json.JSONDecodeError as je:
                _LAST_LLM_ERROR = f"{model}: JSON تالف ({je})"
                print(f"   ⚠️ {_LAST_LLM_ERROR}", flush=True)
                break
            except Exception as e:
                _LAST_LLM_ERROR = f"{model}: {str(e)[:80]}"
                print(f"   ⚠️ {_LAST_LLM_ERROR}", flush=True)
                break

        if valid_tries >= 2:
            _MODEL_PENALTIES[model] = _MODEL_PENALTIES.get(model, 0) + 1

    if not _LAST_LLM_ERROR:
        _LAST_LLM_ERROR = "فشل كل الموديلات المتاحة"
    return None


def llm_split_event(event_title: str, ayahs: list, narrative: list) -> dict:
    ayah_index = "\n".join(
        f"[{a['ref']}] ({a.get('surah_name', '')}): {a['text'][:140]}"
        for a in ayahs
    )
    narrative_index = "\n".join(
        f"[{i}] (anchor={b.get('anchor_ayah', '')}) {b.get('text', '')[:220]}..."
        for i, b in enumerate(narrative)
    )

    prompt = f"""أنت محرر قصصي إسلامي. المطلوب تقسيم الحدث الكبير "{event_title}" إلى حلقات قصيرة متسلسلة زمنياً (كل حلقة حتى 4 آيات و~500-750 كلمة).

الآيات المتاحة:
{ayah_index}

الفقرات الموثقة بالترتيب الزمني:
{narrative_index}

شروط إلزامية:
1. حافظ على الترتيب التصاعدي الصارم لأرقام الفقرات (`narrative_indices`) بدون قفز أو خلط.
2. ضع في كل حلقة الآيات المرتبطة بفقراتها (`anchor`).
3. كل فقرة وكل آية يجب أن تظهر مرة واحدة فقط عبر جميع الحلقات.

أعد JSON فقط:
{{
  "episodes": [
    {{
      "sub_title": "عنوان فرعي قصير",
      "ayah_refs": ["12:4"],
      "narrative_indices": [0, 1]
    }}
  ],
  "reason": "سبب التقسيم"
}}"""

    result = call_llm(prompt, is_json=True, timeout=75, max_tokens=4096)
    valid_refs = {a["ref"] for a in ayahs}
    n_len = len(narrative)

    if result and isinstance(result.get("episodes"), list):
        used_idx, used_refs, cleaned_eps = set(), set(), []
        for ep in result["episodes"]:
            idxs = sorted(
                i for i in ep.get("narrative_indices", [])
                if isinstance(i, int) and 0 <= i < n_len and i not in used_idx
            )
            refs = [
                r for r in ep.get("ayah_refs", [])
                if r in valid_refs and r not in used_refs
            ]
            if idxs or refs:
                used_idx.update(idxs)
                used_refs.update(refs)
                cleaned_eps.append({
                    "sub_title": (ep.get("sub_title") or "").strip(),
                    "ayah_refs": refs,
                    "narrative_indices": idxs,
                })
        if cleaned_eps:
            missing_idx = [i for i in range(n_len) if i not in used_idx]
            if missing_idx:
                cleaned_eps[-1]["narrative_indices"].extend(missing_idx)
            missing_refs = [a["ref"] for a in ayahs if a["ref"] not in used_refs]
            if missing_refs:
                cleaned_eps[-1]["ayah_refs"].extend(missing_refs)
            return {"episodes": cleaned_eps, "reason": result.get("reason", "llm")}

    mid_n = max(1, n_len // 2)
    ep1_refs, seen = [], set()
    for i in range(mid_n):
        anc = narrative[i].get("anchor_ayah", "")
        if anc in valid_refs and anc not in seen:
            seen.add(anc)
            ep1_refs.append(anc)
    ep2_refs = [a["ref"] for a in ayahs if a["ref"] not in seen]

    return {
        "episodes": [
            {"sub_title": "الجزء الأول", "ayah_refs": ep1_refs, "narrative_indices": list(range(mid_n))},
            {"sub_title": "الجزء الثاني", "ayah_refs": ep2_refs, "narrative_indices": list(range(mid_n, n_len))},
        ],
        "reason": "fallback_ordered",
    }


def ensure_splits(story_id: str) -> list[Path]:
    events_dir = STORIES_DIR / story_id / "audited" / "events"
    if not events_dir.exists():
        raise RuntimeError(f"لا توجد أحداث مدققة: {events_dir}")

    ev_dirs = sorted(d for d in events_dir.iterdir() if d.is_dir() and d.name.startswith("event_"))

    for ev_dir in ev_dirs:
        split_file = ev_dir / "split.json"
        if split_file.exists():
            continue

        meta = json.loads((ev_dir / "meta.json").read_text(encoding="utf-8")) if (ev_dir / "meta.json").exists() else {}
        ayahs = json.loads((ev_dir / "ayahs.json").read_text(encoding="utf-8"))
        narrative = json.loads((ev_dir / "narrative.json").read_text(encoding="utf-8"))

        ev_title = meta.get("title") or ev_dir.name
        total_words = sum(len(b.get("text", "").split()) for b in narrative)
        needs_split = len(ayahs) > SPLIT_THRESHOLD_AYAHS or total_words > SPLIT_THRESHOLD_WORDS

        if not needs_split:
            split_data = {
                "episodes": [{"sub_title": ev_title, "ayah_refs": None, "narrative_indices": None}],
                "reason": f"single_episode (ayahs={len(ayahs)}, words={total_words})",
            }
        else:
            print(f"   🔪 تقسيم {ev_dir.name} ({len(ayahs)} آية, {total_words} كلمة)")
            split_data = llm_split_event(ev_title, ayahs, narrative)

        split_file.write_text(json.dumps(split_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ✅ {ev_dir.name}: {len(split_data['episodes'])} حلقة")

    return ev_dirs


SCRIPT_PROMPT = """أنت راوٍ إسلامي محترف تلتزم التزاماً صارماً بالمادة الموثقة فقط.

📖 القصة: {story_title}
📖 الحلقة: {ep_title}
📖 الحدث: {event_title}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
الآيات المعتمدة لهذه الحلقة فقط (تُستخدم كلها، ولا يُزاد عليها أي آية من خارجها):
{ayahs_block}

المادة السردية المدققة بالترتيب الزمني (تُصاغ منها الحلقة فقط دون اختراع):
{narrative_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 الهيكل الإلزامي للسكريبت:
يجب أن تتكوّن الحلقة من كتل متسلسلة بالترتيب الزمني نفسه، على النمط التالي:
- كتلة `"narration"`: تشرح الفكرة أو الحدث استناداً فقط للمادة المدققة أعلاه (بلغة سردية جذابة خالية من الحشو ومن أي نص قرآني حرفي).
- تتلوها مباشرة كتلة `"quran"`: الآية (أو الآيات المتتابعة المتتالية أرقاماً من نفس السورة) التي **تؤكد وتثبت ما ذُكر في فقرة السرد التي سبقتها مباشرة**.

⚠️ قواعد صارمة:
1️⃣ **لا إضافة من الخارج**: التزم بالمعلومات الموثقة أعلاه فقط. لا تخترع أحداثاً ولا تستورد آيات غير موجودة في القائمة.
2️⃣ **فصل التلاوة عن السرد**: يُمنع كتابة الآية القرآنية حرفياً أو اقتباس جملة قرآنية كاملة داخل نص `"narration"`. اشرح معناها وسياقها بكلمات وصفية، ثم ضع مرجعها في كتلة `"quran"` التالية مباشرة.
3️⃣ **منع تتابع آيتين غير متسلسلتين**: يُمنع منعاً باتاً وضع كتلتَي `"quran"` متتاليتين مباشرةً دون فقرة `"narration"` فاصلة بينهما، **إلا** إذا كانتا من **نفس السورة ومتتاليتين تماماً بالأرقام** (مثل آية 22 ثم 23). وكذلك لا تجمع داخل كتلة `"quran"` واحدة أرقاماً غير متتالية؛ إذا تباعدت الآيات أو اختلفت السور فاجعل قبل كل آية فقرة `"narration"` تمهد لها.
4️⃣ **لا حشو**: ممنوع الجمل الإنشائية الفارغة ("في خضم الأحداث"، "مما لا شك فيه"، "ومن الجدير بالذكر").
5️⃣ **اللغة**: فصحى سليمة فقط، بلا كلمات إنجليزية ولا أرقام رقمية داخل السرد.
6️⃣ **الطول المستهدف للسرد**: {min_words} إلى {max_words} كلمة (بقدر المادة الموثقة دون مطّ).

{forbidden_rules}
{retry_feedback}

أعد JSON فقط:
{{
  "title": "عنوان مختصر للحلقة (3-6 كلمات)",
  "segments": [
    {{"type": "narration", "text": "فقرة السرد الأولى..."}},
    {{"type": "quran", "surah": 112, "surah_name": "الإخلاص", "ayah_numbers": [1], "display": "الإخلاص: 1"}}
  ]
}}"""


def generate_episode_script(story_title, ep_title, event_title, ayahs, narrative, min_words, max_words, retry_feedback=""):
    ayahs_block = "\n".join(
        f"[{a['ref']}] ({a.get('surah_name', '')}): {a['text']}"
        for a in ayahs
    ) if ayahs else "(لا توجد آيات)"

    narrative_block = "\n\n".join(
        f"[{i}] (المصدر: {b.get('source', '')} | الآية المؤكدة: {b.get('anchor_ayah') or 'عام'})\n{b.get('text', '')}"
        for i, b in enumerate(narrative)
    )

    prompt = SCRIPT_PROMPT.format(
        story_title=story_title,
        ep_title=ep_title,
        event_title=event_title,
        ayahs_block=ayahs_block,
        narrative_block=narrative_block,
        min_words=min_words,
        max_words=max_words,
        forbidden_rules=get_llm_forbidden_rules(),
        retry_feedback=retry_feedback,
    )
    return call_llm(prompt, is_json=True, timeout=60, max_tokens=8192)


def enrich_and_validate_script(script: dict, expected_ayahs: list, min_words: int, max_words: int) -> tuple[bool, list[str]]:
    issues = []
    segments = script.get("segments", [])
    if not segments:
        return False, ["no_segments"]

    expected_refs = {a["ref"] for a in expected_ayahs}
    actual_refs = set()
    meaningful_segments = []

    for s in segments:
        stype = s.get("type")
        if stype == "narration":
            if not (s.get("text") or "").strip():
                if "empty_narration_segment" not in issues:
                    issues.append("empty_narration_segment")
                continue
            meaningful_segments.append(s)
        elif stype == "quran":
            raw_surah = str(s.get("surah") or "").strip()
            surah = int(raw_surah) if raw_surah.isdigit() else 0
            nums = [int(n) for n in (s.get("ayah_numbers") or []) if str(n).isdigit()]
            s["surah"] = surah
            s["ayah_numbers"] = nums
            sname = _SURAH_NAMES.get(surah, str(s.get("surah_name", "")).replace("سورة ", "").strip())
            s["surah_name"] = sname
            if nums:
                s["display"] = f"{sname}: {nums[0]}" if len(nums) == 1 else f"{sname}: {nums[0]}-{nums[-1]}"
            else:
                s["display"] = sname

            if surah <= 0 or not nums:
                issues.append(f"invalid_quran_segment ({s.get('display', 'unknown')})")
            elif any(nums[k + 1] != nums[k] + 1 for k in range(len(nums) - 1)):
                issues.append(f"non_consecutive_ayahs_in_block ({s['display']}: {nums})")

            for n in nums:
                actual_refs.add(f"{surah}:{n}")
            meaningful_segments.append(s)

    missing = sorted(expected_refs - actual_refs)
    if missing:
        issues.append(f"missing_ayahs: {missing}")

    extra = sorted(actual_refs - expected_refs)
    if extra:
        issues.append(f"unexpected_ayahs: {extra}")

    if not meaningful_segments or meaningful_segments[0].get("type") == "quran":
        issues.append("starts_with_quran_without_narration")

    # حماية مهمة: منع وجود آيتين وراء بعض مباشرة إلا إذا كانتا من نفس السورة ومتتاليتين بالأرقام
    for idx in range(len(meaningful_segments) - 1):
        prev_s = meaningful_segments[idx]
        curr_s = meaningful_segments[idx + 1]
        if prev_s.get("type") == "quran" and curr_s.get("type") == "quran":
            p_surah = prev_s.get("surah", 0)
            c_surah = curr_s.get("surah", 0)
            p_nums = prev_s.get("ayah_numbers") or []
            c_nums = curr_s.get("ayah_numbers") or []
            is_consecutive_same_surah = (
                p_surah > 0
                and p_surah == c_surah
                and bool(p_nums)
                and bool(c_nums)
                and c_nums[0] == p_nums[-1] + 1
            )
            if not is_consecutive_same_surah:
                p_disp = prev_s.get("display") or f"{p_surah}:{p_nums}"
                c_disp = curr_s.get("display") or f"{c_surah}:{c_nums}"
                issues.append(f"consecutive_unrelated_quran_segments ({p_disp} -> {c_disp})")

    # فحص تسرّب الآيات الكاملة أو المقاطع القرآنية الطويلة داخل السرد (دون منع الكلمات المفردة أو أسماء الله الحسنى)
    norm_expected = [
        (_norm_ar(a["text"]).split(), a["ref"])
        for a in expected_ayahs
        if len(_norm_ar(a["text"]).split()) >= 5
    ]
    for s in meaningful_segments:
        if s.get("type") != "narration":
            continue
        raw_text = s.get("text", "")
        n_words = _norm_ar(raw_text).split()
        if not n_words:
            continue

        # منع وضع نص قرآني بين أقواس التلاوة ﴿...﴾ داخل السرد
        for qb in re.findall(r"﴿([^﴾]+)﴾", raw_text):
            if len(_norm_ar(qb).split()) >= 4:
                if "quran_quoted_with_brackets_in_narration" not in issues:
                    issues.append("quran_quoted_with_brackets_in_narration")
                break

        for a_words, ref in norm_expected:
            a_len = len(a_words)
            if a_len == 5:
                # آية قصيرة من 5 كلمات: لا تُرفض إلا إذا نُقلت كاملة حرفياً
                min_match = 5
            elif a_len <= 8:
                min_match = a_len - 1
            else:
                # آية طويلة (9+ كلمات): تطابق 8 إلى 11 كلمة متتالية على الأقل
                min_match = max(8, min(11, int(a_len * 0.55)))

            if len(n_words) < min_match:
                continue

            ayah_ngrams = {tuple(a_words[i:i + min_match]) for i in range(a_len - min_match + 1)}
            for j in range(len(n_words) - min_match + 1):
                if tuple(n_words[j:j + min_match]) in ayah_ngrams:
                    tag = f"quran_leaked_in_narration ({ref})"
                    if tag not in issues:
                        issues.append(tag)
                    break

    narration_words = sum(len(s.get("text", "").split()) for s in meaningful_segments if s.get("type") == "narration")
    if narration_words < min_words:
        issues.append(f"too_short: {narration_words} < {min_words}")
    elif narration_words > max_words + 150:
        issues.append(f"too_long: {narration_words} > {max_words}")

    for s in meaningful_segments:
        if s.get("type") == "narration" and re.search(r"[a-zA-Z]", s.get("text", "")):
            issues.append("english_in_narration")
            break

    return (len(issues) == 0), issues


def _build_retry_feedback(issues: list[str], ayahs_ep: list) -> str:
    ayah_map = {a["ref"]: a["text"] for a in ayahs_ep}
    notes = []
    for iss in issues:
        if iss.startswith("quran_leaked_in_narration"):
            m = re.search(r"\(([^)]+)\)", iss)
            ref = m.group(1) if m else ""
            txt = ayah_map.get(ref, "")
            if txt:
                notes.append(
                    f"- تسرّب نص الآية [{ref}] ({txt}) داخل فقرة narration. "
                    f"اشرح معناها بعبارات وصفية مختلفة تماماً دون كتابة عبارات الآية حرفياً داخل السرد."
                )
            else:
                notes.append(f"- {iss}: أعد الصياغة دون نقل عبارات الآية داخل السرد.")
        elif iss == "quran_quoted_with_brackets_in_narration":
            notes.append("- يُمنع وضع أقواس ﴿...﴾ أو نص الآية داخل narration؛ ضع الآية في كتلة quran فقط.")
        elif iss.startswith("consecutive_unrelated_quran_segments"):
            notes.append(
                f"- خطأ تتابع آيات ({iss}): يُمنع وضع آيتين وراء بعضهما مباشرة دون فقرة narration بينهما "
                f"إلا إذا كانتا من نفس السورة ومتتاليتين تماماً في الأرقام. أضف فقرة شرح (narration) قبل الآية الثانية."
            )
        elif iss.startswith("non_consecutive_ayahs_in_block"):
            notes.append(
                f"- خطأ أرقام غير متتالية ({iss}): لا تضع أرقام آيات متباعدة في كتلة quran واحدة؛ "
                f"اجعل لكل آية (أو آيات متتالية فقط) كتلتها المسبوقة بفقرة narration."
            )
        elif iss == "empty_narration_segment":
            notes.append("- توجد فقرة narration فارغة؛ اكتب فيها شرحاً مناسباً أو احذفها.")
        else:
            notes.append(f"- {iss}")
    return (
        "\n⚠️ تنبيه تصحيح إلزامي (المحاولة السابقة رُفضت بسبب الأخطاء التالية — عالجها تماماً):\n"
        + "\n".join(notes)
    )


def generate_summary(story_title, ep_title, segments):
    narration_text = " ".join(s.get("text", "") for s in segments if s.get("type") == "narration")[:3000]
    prompt = f"""القصة: {story_title} — {ep_title}
سرد الحلقة:
{narration_text}

اكتب "خلاصة وعبرة" بليغة من سطرين إلى ثلاثة أسطر:
- عبرة مركزة دون تكرار للسرد.
- فصحى سليمة بدون مقدمات ("وفي الختام" ممنوعة).
أعد النص فقط."""
    res = call_llm(prompt, is_json=False, timeout=45, max_tokens=400)
    return clean_narration(res) if isinstance(res, str) else ""


def generate_teaser(story_title, next_ep_title):
    next_hint = f"الحدث القادم: {next_ep_title}" if next_ep_title else "الحدث القادم"
    prompt = f"""القصة: {story_title}. {next_hint}.
اكتب جملة واحدة تشوق المستمع للحلقة القادمة:
- ابدأ بسؤال درامي وقور.
- جملة واحدة فقط.
أعد الجملة فقط."""
    res = call_llm(prompt, is_json=False, timeout=45, max_tokens=200)
    teaser = clean_narration(res) if isinstance(res, str) else ""
    if len(teaser) < 15:
        teaser = "فماذا جاء بعد ذلك في هذه السلسلة المباركة؟"
    return teaser


def main(clear_latest: bool = True, _visited: set | None = None):
    if clear_latest:
        LATEST_EPISODE.parent.mkdir(parents=True, exist_ok=True)
        if LATEST_EPISODE.exists():
            LATEST_EPISODE.unlink()

    if _visited is None:
        _visited = set()

    print("=" * 65)
    print("🎬 توليد حلقة قصة جديدة (بالسرد الموثق المؤكد بالآيات)")
    print("=" * 65)

    gstate = load_global()
    story_id = gstate["current_story_id"]
    if story_id in _visited:
        msg = "تم فحص كل القصص المتاحة في المكتبة ولا توجد أحداث متبقية."
        print(f"::error::{msg}", flush=True)
        _gh_output(fail_reason=msg)
        _gh_summary(f"### ❌ توقف التوليد\n- **السبب:** {msg}\n- **القصص المفحوصة:** `{sorted(_visited)}`")
        sys.exit(1)
    _visited.add(story_id)

    story = get_story_by_id(story_id)
    if not story:
        msg = f"القصة '{story_id}' غير موجودة في stories.json"
        print(f"::error::{msg}", flush=True)
        _gh_output(story_id=story_id, fail_reason=msg)
        _gh_summary(f"### ❌ فشل التوليد\n- **السبب:** {msg}")
        sys.exit(1)

    print(f"📖 {story['title']} (العهد: {story['era']})")

    print("\n🔪 فحص التقسيمات...")
    ev_dirs = ensure_splits(story_id)
    print(f"📦 {len(ev_dirs)} حدث مدقق")

    sstate = load_story_state(story_id)
    ev_idx = sstate["current_event_index"]
    sub_idx = sstate["current_sub_episode"]

    # التقدم عبر الأحداث داخل نفس القصة في حلقة واحدة بدون إعادة الدخول في _visited
    while ev_idx < len(ev_dirs):
        ev_dir = ev_dirs[ev_idx]
        split = json.loads((ev_dir / "split.json").read_text(encoding="utf-8"))
        episodes_in_event = split["episodes"]

        if sub_idx < len(episodes_in_event):
            break

        print(f"⏭️ الحدث {ev_dir.name} مكتمل ({sub_idx}/{len(episodes_in_event)}) — انتقال للحدث التالي...")
        ev_idx += 1
        sub_idx = 0
        sstate["current_event_index"] = ev_idx
        sstate["current_sub_episode"] = 0
        save_story_state(story_id, sstate)

    if ev_idx >= len(ev_dirs):
        print(f"\n🎉 القصة '{story['title']}' مكتملة! ({len(ev_dirs)}/{len(ev_dirs)} حدث)")
        mark_story_complete(story_id)
        next_story_id = advance_to_next_story()
        next_story = get_story_by_id(next_story_id)
        next_title = next_story["title"] if next_story else next_story_id

        notify_telegram(f"✅ اكتملت: {story['title']}\n🎯 التالي: {next_title}", level="success")

        next_audited = STORIES_DIR / next_story_id / "audited" / "events"
        if next_audited.exists() and list(next_audited.glob("event_*")):
            print(f"⏭️ القصة التالية '{next_story_id}' جاهزة — بدء حلقتها الأولى...")
            return main(clear_latest=False, _visited=_visited)

        msg = f"اكتملت '{story_id}'؛ القصة التالية '{next_story_id}' ({next_title}) بانتظار التجهيز من Workflow التجهيز."
        print(f"::notice::{msg}", flush=True)
        _gh_output(story_id=next_story_id, fail_reason=msg)
        _gh_summary(f"### ℹ️ انتظار التجهيز\n- **السبب:** {msg}")
        notify_telegram(f"ℹ️ {msg}", level="info")
        return

    ev_dir = ev_dirs[ev_idx]
    split = json.loads((ev_dir / "split.json").read_text(encoding="utf-8"))
    episodes_in_event = split["episodes"]

    ep_spec = episodes_in_event[sub_idx]
    meta = json.loads((ev_dir / "meta.json").read_text(encoding="utf-8")) if (ev_dir / "meta.json").exists() else {}
    ayahs_all = json.loads((ev_dir / "ayahs.json").read_text(encoding="utf-8"))
    narrative_all = json.loads((ev_dir / "narrative.json").read_text(encoding="utf-8"))

    ayahs_ep = ayahs_all if ep_spec.get("ayah_refs") is None else [
        a for a in ayahs_all if a["ref"] in ep_spec["ayah_refs"]
    ]
    narrative_ep = narrative_all if ep_spec.get("narrative_indices") is None else [
        narrative_all[i] for i in ep_spec["narrative_indices"] if i < len(narrative_all)
    ]

    ep_num = sstate.get("total_episodes_generated", 0) + 1
    global_ep_num = gstate.get("total_episodes_published", 0) + 1
    ep_code = f"{story_id}-{ep_num:02d}"
    ev_title = ep_spec.get("sub_title") or meta.get("title") or ev_dir.name

    source_words = sum(len(b.get("text", "").split()) for b in narrative_ep)
    min_words = max(50, min(350, int(source_words * 0.65)))
    max_words = min(TARGET_WORDS_CAP, max(350, int(source_words * 1.40)))

    print(f"\n🎬 حلقة القصة #{ep_num} (الحلقة العامة #{global_ep_num}) | الكود: {ep_code}")
    print(f"   📌 الحدث: {ev_dir.name} ({ev_idx + 1}/{len(ev_dirs)}) | الجزء: {sub_idx + 1}/{len(episodes_in_event)} | {ev_title}")
    print(f"   📖 {len(ayahs_ep)} آية | 📝 {len(narrative_ep)} فقرة ({source_words} كلمة) | الهدف: {min_words}-{max_words}")

    _gh_output(
        story_id=story_id,
        ep_num=ep_num,
        global_ep_num=global_ep_num,
        ep_name=ep_code,
        event_name=f"{ev_dir.name} ({ev_idx + 1}/{len(ev_dirs)})",
    )

    generated_dir = STORIES_DIR / story_id / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    output_path = generated_dir / f"{ep_code}.json"

    if output_path.exists():
        print(f"\n♻️ الحلقة {ep_num} موجودة سابقاً — إعادة استخدام: {output_path}")
        LATEST_EPISODE.write_text(str(output_path), encoding="utf-8")
        _gh_summary(
            f"### ♻️ إعادة استخدام الحلقة #{ep_num} (العامة #{global_ep_num})\n"
            f"- **القصة:** `{story_id}` ({story['title']})\n"
            f"- **الحدث:** `{ev_dir.name}` (`{ev_idx + 1}/{len(ev_dirs)}`)\n"
            f"- **الملف:** `{output_path.name}`"
        )
        return

    script = None
    retry_feedback = ""
    last_issues = []
    for attempt in range(3):
        print(f"   ✍️ محاولة توليد {attempt + 1}/3...", flush=True)
        candidate = generate_episode_script(
            story_title=story["title"],
            ep_title=ev_title,
            event_title=meta.get("title") or ev_dir.name,
            ayahs=ayahs_ep,
            narrative=narrative_ep,
            min_words=min_words,
            max_words=max_words,
            retry_feedback=retry_feedback,
        )
        if candidate:
            ok, issues = enrich_and_validate_script(candidate, ayahs_ep, min_words, max_words)
            if ok:
                script = candidate
                print("   ✅ سكريبت صالح ومتطابق 100%")
                break
            last_issues = issues
            print(f"   ⚠️ رفض المسودة: {issues}")
            retry_feedback = _build_retry_feedback(issues, ayahs_ep)
        else:
            err = _LAST_LLM_ERROR or "LLM لم يُرجع استجابة"
            last_issues = [f"llm_error: {err}"]
            print(f"   ⚠️ فشل LLM في المحاولة {attempt + 1}: {err}", flush=True)
        time.sleep(2)

    if not script:
        fail_msg = f"فشل توليد السكريبت للحلقة #{ep_num} ({ep_code}) بعد 3 محاولات — السبب: {last_issues}"
        print(f"::error::{fail_msg}", flush=True)
        _gh_output(fail_reason=fail_msg)
        _gh_summary(
            f"### ❌ فشل توليد الحلقة #{ep_num} (العامة #{global_ep_num})\n"
            f"- **القصة:** `{story_id}` ({story['title']})\n"
            f"- **الحدث:** `{ev_dir.name}` (`{ev_idx + 1}/{len(ev_dirs)}`) | الجزء `{sub_idx + 1}/{len(episodes_in_event)}`\n"
            f"- **آخر سبب فشل:** `{last_issues}`"
        )
        sys.exit(1)

    print("   📝 خلاصة...")
    summary = generate_summary(story["title"], ev_title, script["segments"])

    next_sub = sub_idx + 1
    if next_sub < len(episodes_in_event):
        next_ep_title = episodes_in_event[next_sub].get("sub_title", "")
    elif ev_idx + 1 < len(ev_dirs):
        next_meta_p = ev_dirs[ev_idx + 1] / "meta.json"
        next_ep_title = json.loads(next_meta_p.read_text(encoding="utf-8")).get("title", "") if next_meta_p.exists() else ""
    else:
        next_ep_title = None

    print("   📢 تشويق...")
    teaser = generate_teaser(story["title"], next_ep_title)

    segments = script["segments"]
    if summary:
        segments.append({"type": "summary", "text": summary, "visual": "sunrise sky"})
    if teaser:
        segments.append({
            "type": "teaser",
            "text": teaser,
            "next_episode_title": f"الحلقة {ep_num + 1}",
            "url": "https://qamarsyria.github.io/qamar-malak/",
            "platform_name": "ملاك المعرفة",
        })
    outro = random.choice(OUTRO_TEMPLATES)
    segments.append({"type": "cta", "text": outro["text"], "visual": "mosque architecture"})

    for s in segments:
        if s.get("type") in ("narration", "summary", "teaser", "cta"):
            s["text"] = clean_narration(s["text"])

    ep_data = {
        "episode_number": ep_num,
        "global_episode_number": global_ep_num,
        "story_id": story_id,
        "story_title": story["title"],
        "title": f"{story['title']} — الحلقة {ep_num} — {script.get('title', ev_title)}",
        "segments": segments,
    }

    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_EPISODE.write_text(str(output_path), encoding="utf-8")

    _gh_summary(
        f"### 🎬 الحلقة #{ep_num} (العامة #{global_ep_num}): `{ep_code}`\n"
        f"- **القصة:** {story['title']} (`{story_id}`)\n"
        f"- **الحدث:** `{ev_dir.name}` (`{ev_idx + 1}/{len(ev_dirs)}`) | الجزء `{sub_idx + 1}/{len(episodes_in_event)}`\n"
        f"- **العنوان:** {script.get('title', ev_title)}"
    )

    notify_telegram(
        f"✍️ حلقة جاهزة للبناء: #{ep_num} (العامة #{global_ep_num})\n"
        f"📖 {story['title']} — {ep_code}\n"
        f"📌 {ev_dir.name}: {script.get('title', ev_title)}",
        level="info",
    )
    print(f"\n🎉 اكتملت الحلقة {ep_num} (العامة #{global_ep_num}): {output_path.name}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        err_msg = f"خطأ غير متوقع في generate_story.py: {type(e).__name__}: {e}"
        print(f"::error::{err_msg}", flush=True)
        _gh_output(fail_reason=err_msg)
        _gh_summary(f"### ❌ خطأ فادح أثناء التوليد\n```\n{err_msg}\n```")
        import traceback
        traceback.print_exc()
        sys.exit(1)
