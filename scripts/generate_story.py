"""
generate_story.py — المرحلة 3: تقسيم الأحداث وتوليد الحلقات
- يقرأ audited/events/ بالترتيب الزمني الدقيق.
- يبني كل حلقة بنمط: [فقرة سرد موثقة ← الآية المؤكدة لها مباشرة].
- يمنع إقحام آيات من خارج الحدث أو تسرب نص الآية حرفياً داخل السرد.
- يعتمد حداً أدنى مرناً للكلمات حسب حجم المادة المدققة لمنع الحشو والاختلاق.
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
    get_all_stories,
    get_story_by_id,
    mark_story_complete,
    advance_to_next_story,
)

# ═══════════════════════════════════════════════════════════
# المسارات والإعدادات
# ═══════════════════════════════════════════════════════════
STORIES_DIR    = _REPO_ROOT / "data" / "stories"
LATEST_EPISODE = _REPO_ROOT / "out" / "latest_episode.txt"

SPLIT_THRESHOLD_AYAHS = 6
SPLIT_THRESHOLD_WORDS = 950
TARGET_WORDS_CAP      = 850

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

# ═══════════════════════════════════════════════════════════
# معالجة النصوص والأرقام العربية
# ═══════════════════════════════════════════════════════════
_ARABIC_UNITS    = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_ARABIC_TEENS    = ["عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
                    "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
_ARABIC_TENS     = ["", "عشرة", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_ARABIC_HUNDREDS = ["", "مئة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]


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
    text = re.sub(r"[\u064B-\u065F\u0670\u06D6-\u06ED]", "", text or "")
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"[^\u0621-\u064A\s]", " ", text).strip()


# ═══════════════════════════════════════════════════════════
# استدعاء LLM
# ═══════════════════════════════════════════════════════════
def _advance_pool(pool):
    if hasattr(pool, "_advance"):
        pool._advance()
    elif hasattr(pool, "advance"):
        pool.advance()


def call_llm(prompt: str, is_json: bool = True, timeout: int = 180, max_tokens: int = 8192):
    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:5]
    if not models:
        return None

    for model in models:
        if breaker.is_open(f"model:{model}"):
            continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.25, "maxOutputTokens": max_tokens},
                }
                if is_json:
                    body["generationConfig"]["responseMimeType"] = "application/json"

                r = requests.post(url, json=body, timeout=timeout)

                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts:
                        break
                    text = parts[0].get("text", "")
                    if is_json:
                        s, e = text.find("{"), text.rfind("}")
                        if s == -1 or e <= s:
                            break
                        text = text[s:e + 1]
                    parsed = json.loads(text) if is_json else text
                    _advance_pool(pool)
                    breaker.record_success(f"model:{model}")
                    return parsed
                elif r.status_code == 429:
                    pool.mark_failed(75)
                    time.sleep(2)
                elif r.status_code in (400, 404):
                    breaker.record_failure(f"model:{model}")
                    break
                elif r.status_code >= 500:
                    time.sleep(3)
            except requests.exceptions.Timeout:
                time.sleep(3)
            except json.JSONDecodeError:
                break
            except Exception:
                break
    return None


# ═══════════════════════════════════════════════════════════
# تقسيم الأحداث الكبيرة مع الحفاظ على التسلسل الزمني
# ═══════════════════════════════════════════════════════════
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

    result = call_llm(prompt, is_json=True, timeout=180, max_tokens=4096)
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
        # ضمان عدم ضياع أي فقرة أو آية
        if cleaned_eps:
            missing_idx = [i for i in range(n_len) if i not in used_idx]
            if missing_idx:
                cleaned_eps[-1]["narrative_indices"].extend(missing_idx)
            missing_refs = [a["ref"] for a in ayahs if a["ref"] not in used_refs]
            if missing_refs:
                cleaned_eps[-1]["ayah_refs"].extend(missing_refs)
            return {"episodes": cleaned_eps, "reason": result.get("reason", "llm")}

    # fallback تسلسلي منضبط
    mid_n = max(1, n_len // 2)
    ep1_refs = []
    seen = set()
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


# ═══════════════════════════════════════════════════════════
# توليد سكريبت الحلقة: [فقرة سرد ← آية مؤكدة]
# ═══════════════════════════════════════════════════════════
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
- تتلوها مباشرة كتلة `"quran"`: الآية (أو الآيات المتتابعة من نفس السورة) التي **تؤكد وتثبت ما ذُكر في فقرة السرد التي سبقتها مباشرة**.

⚠️ قواعد صارمة:
1️⃣ **لا إضافة من الخارج**: التزم بالمعلومات الموثقة أعلاه فقط. لا تخترع أحداثاً ولا تستورد آيات غير موجودة في القائمة.
2️⃣ **فصل التلاوة عن السرد**: يُمنع كتابة الآية القرآنية حرفياً داخل نص `"narration"`. اشرح معناها وسياقها، ثم ضع مرجعها في كتلة `"quran"` التالية مباشرة.
3️⃣ **لا حشو**: ممنوع الجمل الإنشائية الفارغة ("في خضم الأحداث"، "مما لا شك فيه"، "ومن الجدير بالذكر").
4️⃣ **اللغة**: فصحى سليمة فقط، بلا كلمات إنجليزية ولا أرقام رقمية داخل السرد.
5️⃣ **الطول المستهدف للسرد**: {min_words} إلى {max_words} كلمة (بقدر المادة الموثقة دون مطّ).

{forbidden_rules}

أعد JSON فقط:
{{
  "title": "عنوان مختصر للحلقة (3-6 كلمات)",
  "segments": [
    {{"type": "narration", "text": "فقرة السرد الأولى..."}},
    {{"type": "quran", "surah": 112, "surah_name": "الإخلاص", "ayah_numbers": [1], "display": "الإخلاص: 1"}}
  ]
}}"""


def generate_episode_script(story_title, ep_title, event_title, ayahs, narrative, min_words, max_words):
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
    )
    return call_llm(prompt, is_json=True, timeout=240, max_tokens=8192)


def enrich_and_validate_script(script: dict, expected_ayahs: list, min_words: int, max_words: int) -> tuple[bool, list[str]]:
    issues = []
    segments = script.get("segments", [])
    if not segments:
        return False, ["no_segments"]

    expected_map = {a["ref"]: a for a in expected_ayahs}
    expected_refs = set(expected_map.keys())
    actual_refs = set()

    # توحيد بيانات السور وتنظيف أرقام الآيات
    for s in segments:
        if s.get("type") == "quran":
            surah = int(s.get("surah") or 0)
            nums = [int(n) for n in (s.get("ayah_numbers") or []) if str(n).isdigit()]
            s["surah"] = surah
            s["ayah_numbers"] = nums
            sname = _SURAH_NAMES.get(surah, str(s.get("surah_name", "")).replace("سورة ", "").strip())
            s["surah_name"] = sname
            if nums:
                s["display"] = f"{sname}: {nums[0]}" if len(nums) == 1 else f"{sname}: {nums[0]}-{nums[-1]}"
            for n in nums:
                actual_refs.add(f"{surah}:{n}")

    # 1. التحقق من الآيات المفقودة والدخيلة
    missing = sorted(expected_refs - actual_refs)
    if missing:
        issues.append(f"missing_ayahs: {missing}")

    extra = sorted(actual_refs - expected_refs)
    if extra:
        issues.append(f"unexpected_ayahs: {extra}")

    # 2. التأكد من وجود سرد ممهد قبل كل آية (أو عدم بدء الحلقة بآية يتيمة)
    if segments[0].get("type") == "quran":
        issues.append("starts_with_quran_without_narration")

    # 3. التحقق من عدم تسرب نص الآية حرفياً داخل السرد
    norm_expected = [(_norm_ar(a["text"]), a["ref"]) for a in expected_ayahs if len(_norm_ar(a["text"]).split()) >= 5]
    for s in segments:
        if s.get("type") == "narration":
            n_words = _norm_ar(s.get("text", "")).split()
            for ayah_norm, ref in norm_expected:
                a_words = ayah_norm.split()
                for i in range(len(a_words) - 4):
                    phrase = " ".join(a_words[i:i + 5])
                    if phrase in " ".join(n_words):
                        issues.append(f"quran_leaked_in_narration ({ref})")
                        break

    # 4. طول السرد والإنجليزية
    narration_words = sum(len(s.get("text", "").split()) for s in segments if s.get("type") == "narration")
    if narration_words < min_words:
        issues.append(f"too_short: {narration_words} < {min_words}")
    elif narration_words > max_words + 150:
        issues.append(f"too_long: {narration_words} > {max_words}")

    for s in segments:
        if s.get("type") == "narration" and re.search(r"[a-zA-Z]", s.get("text", "")):
            issues.append("english_in_narration")
            break

    return (len(issues) == 0), issues


# ═══════════════════════════════════════════════════════════
# الخلاصة والتشويق
# ═══════════════════════════════════════════════════════════
def generate_summary(story_title, ep_title, segments):
    narration_text = " ".join(s.get("text", "") for s in segments if s.get("type") == "narration")[:3000]
    prompt = f"""القصة: {story_title} — {ep_title}
سرد الحلقة:
{narration_text}

اكتب "خلاصة وعبرة" بليغة من سطرين إلى ثلاثة أسطر:
- عبرة مركزة دون تكرار للسرد.
- فصحى سليمة بدون مقدمات ("وفي الختام" ممنوعة).
أعد النص فقط."""
    res = call_llm(prompt, is_json=False, timeout=60, max_tokens=250)
    return clean_narration(res) if isinstance(res, str) else ""


def generate_teaser(story_title, next_ep_title):
    next_hint = f"الحدث القادم: {next_ep_title}" if next_ep_title else "الحدث القادم"
    prompt = f"""القصة: {story_title}. {next_hint}.
اكتب جملة واحدة تشوق المستمع للحلقة القادمة:
- ابدأ بسؤال درامي وقور.
- جملة واحدة فقط.
أعد الجملة فقط."""
    res = call_llm(prompt, is_json=False, timeout=60, max_tokens=150)
    teaser = clean_narration(res) if isinstance(res, str) else ""
    if len(teaser) < 15:
        teaser = "فماذا جاء بعد ذلك في هذه السلسلة المباركة؟"
    return teaser


# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
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
        print("⚠️ تم فحص كل القصص المتاحة دون أحداث متبقية.")
        return
    _visited.add(story_id)

    story = get_story_by_id(story_id)
    if not story:
        print(f"❌ القصة '{story_id}' غير موجودة")
        sys.exit(1)

    print(f"📖 {story['title']} (العهد: {story['era']})")

    print("\n🔪 فحص التقسيمات...")
    ev_dirs = ensure_splits(story_id)
    print(f"📦 {len(ev_dirs)} حدث مدقق")

    sstate = load_story_state(story_id)
    ev_idx = sstate["current_event_index"]
    sub_idx = sstate["current_sub_episode"]

    # اكتمال القصة الحالية -> الانتقال المنضبط عبر story_library
    if ev_idx >= len(ev_dirs):
        print(f"\n🎉 القصة '{story['title']}' مكتملة!")
        mark_story_complete(story_id)
        next_story_id = advance_to_next_story()
        next_story = get_story_by_id(next_story_id)
        next_title = next_story["title"] if next_story else next_story_id

        notify_telegram(f"✅ اكتملت: {story['title']}\n🎯 التالي: {next_title}", level="success")

        next_audited = STORIES_DIR / next_story_id / "audited" / "events"
        if next_audited.exists() and list(next_audited.glob("event_*")):
            print(f"⏭️ القصة التالية '{next_story_id}' جاهزة — بدء حلقتها الأولى...")
            return main(clear_latest=False, _visited=_visited)
        print(f"ℹ️ القصة التالية '{next_story_id}' بانتظار التجهيز من Workflow التجهيز.")
        return

    ev_dir = ev_dirs[ev_idx]
    split = json.loads((ev_dir / "split.json").read_text(encoding="utf-8"))
    episodes_in_event = split["episodes"]

    if sub_idx >= len(episodes_in_event):
        sstate["current_event_index"] += 1
        sstate["current_sub_episode"] = 0
        save_story_state(story_id, sstate)
        print("⏭️ انتقال للحدث التالي...")
        return main(clear_latest=False, _visited=_visited)

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

    ep_num = sstate["total_episodes_generated"] + 1
    ev_title = ep_spec.get("sub_title") or meta.get("title") or ev_dir.name

    # حدود الكلمات الموثقة (تمنع الحشو وتسمح بالطول المناسب للمادة)
    source_words = sum(len(b.get("text", "").split()) for b in narrative_ep)
    min_words = max(80, min(400, int(source_words * 0.70)))
    max_words = min(TARGET_WORDS_CAP, max(350, int(source_words * 1.35)))

    print(f"\n🎬 حلقة {ep_num}: {ev_title}")
    print(f"   📖 {len(ayahs_ep)} آية | 📝 {len(narrative_ep)} فقرة ({source_words} كلمة) | الهدف: {min_words}-{max_words}")

    generated_dir = STORIES_DIR / story_id / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    output_path = generated_dir / f"{story_id}-{ep_num:02d}.json"

    if output_path.exists():
        print(f"\n♻️ الحلقة {ep_num} موجودة سابقاً — إعادة استخدام: {output_path}")
        LATEST_EPISODE.write_text(str(output_path), encoding="utf-8")
        return

    script = None
    for attempt in range(3):
        print(f"   ✍️ محاولة توليد {attempt + 1}/3...")
        candidate = generate_episode_script(
            story_title=story["title"],
            ep_title=ev_title,
            event_title=meta.get("title") or ev_dir.name,
            ayahs=ayahs_ep,
            narrative=narrative_ep,
            min_words=min_words,
            max_words=max_words,
        )
        if candidate:
            ok, issues = enrich_and_validate_script(candidate, ayahs_ep, min_words, max_words)
            if ok:
                script = candidate
                print("   ✅ سكريبت صالح ومتطابق 100%")
                break
            print(f"   ⚠️ رفض المسودة: {issues}")
        time.sleep(2)

    if not script:
        print("❌ فشل توليد سكريبت مطابق للقواعد بعد 3 محاولات")
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
        "story_id": story_id,
        "story_title": story["title"],
        "title": f"{story['title']} — الحلقة {ep_num} — {script.get('title', ev_title)}",
        "segments": segments,
    }

    output_path.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_EPISODE.write_text(str(output_path), encoding="utf-8")

    notify_telegram(
        f"✍️ حلقة جاهزة للبناء: {story['title']} — {ep_num}\n📖 {script.get('title', ev_title)}",
        level="info",
    )
    print(f"\n🎉 اكتملت الحلقة {ep_num}: {output_path.name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ خطأ فادح: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
