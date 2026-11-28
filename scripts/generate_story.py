"""
generate_story.py — المرحلة 3: تقسيم الأحداث وتوليد الحلقات بأسلوب سردي حي ومبسّط
- يقرأ audited/events/ بالترتيب الزمني الدقيق.
- يبني كل حلقة بنمط: [فقرة سرد حيّة مبسّطة تشرح المعاني والألفاظ الصعبة ← الآية المؤكدة لها مباشرة].
- يجمع بين الدقة العلمية والتعظيم الشرعي وبين اللغة العربية البسيطة الواضحة للجميع.
- يضبط اسم القناة (مَلَّاكُ المَعْرِفَةِ) بفتح الميم وتشديد اللام مع الفتح.
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

THEOLOGICAL_DIGNITY_RULES = """🛡️ ضوابط التعظيم والتنزيه العقائدي (مطلقة ولا تقبل التهاون):
- التزم بأقصى درجات الأدب والتعظيم لله عز وجل وأسمائه الحسنى وصفاته وآياته ومخلوقاته العظيمة (العرش، الكرسي، السماوات، الملائكة، اللوح المحفوظ، القلم).
- الله عز وجل هو القاهر فوق عباده، لا يعجزه شيء في الأرض ولا في السماء، ولا يؤثر مخلوق في خلقه أو ملكه أو عرشه أو كرسيه.
- يُمنع منعاً باتاً إسقاط استعارات السياسة والصراعات البشرية على عرش الرحمن أو كرسيه أو ملكه سبحانه (مثل: "يصمد العرش"، "يتهوى الكرسي"، "خيانة"، "أنقاض"، "انهيار"، "سقوط العرش"، "صراع"، "تهديد"، "تحدٍّ للخالق").
- اجعل لغة السرد والخلاصة والتشويق تأملية وقورة تُعظّم الخالق وتُبرز سعة علمه وقدرته وضآلة المخلوقين أمام عظمته سبحانه."""

OPENING_STYLES = [
    "الدخول فوراً في قلب الحدث بسؤال مثير يوقظ الفضول (مثل: تخيل لو... أو هل خطر ببالك يوماً كيف...؟)، دون أي ترحيب تقليدي.",
    "البدء بكشف معنى كلمة قرآنية عميقة أو تفصيلة مدهشة في الحدث لا ينتبه لها أغلب الناس، بلغة بسيطة جداً ومباشرة.",
    "التقاط مشهد حسّي حي في أول ثانية يجذب الأذن فوراً قبل التوسع بالتفاصيل.",
    "تصحيح فكرة خاطئة أو سطحية يتداولها بعض الناس حول هذا الموقف، ثم بيان الحقيقة الموثقة بأسلوب دافئ ومبسط.",
    "مخاطبة مشاعر الإنسان اليوم وتفكيره، والربط المباشر بين الحكمة في هذه القصة وبين هموم حياتنا اليومية.",
    "إظهار التدرّج العجيب في سنن الله وأحداث القصة بربط لطيف وسلس يفهمه الجميع من أول مرة.",
]

_CLICHE_OPENINGS = (
    "في هذه الحلقة",
    "اهلا بكم",
    "مرحبا بكم",
    "السلام عليكم",
    "نتحدث اليوم",
    "نقف اليوم",
    "في رحاب هذه الحلقة",
    "في مستهل هذه الحلقة",
    "مما لا شك فيه",
    "من الجدير بالذكر",
)

_FORBIDDEN_IN_CREATION_RE = re.compile(
    r"(?:خيان[ةه]|يتهوى|يتداعى|أنقاض|انقاض|يصمد\s+العرش|سقوط\s+العرش|انهيار\s+العرش|"
    r"تهوي\s+الكرسي|يتزلزل\s+الكرسي|صراع\s+على\s+العرش|مؤامر[ةه]|انقلاب|تحدي\s+الخالق)"
)

_SACRED_DISRESPECT_RE = re.compile(
    r"(?:(?:عرش\s+(?:الله|الرحمن)|الكرسي|كرسي\s+الله|ملك\s+الله|قدرة\s+الله|الذات\s+الالهيه)"
    r"[^.،؟!]{0,55}"
    r"(?:خيانه|يتهوى|يتداعى|انقاض|انهيار|سقوط|يصمد|عجز|تهديد|يضعف))|"
    r"(?:(?:خيانه|يتهوى|يتداعى|انقاض|انهيار|سقوط|يصمد)"
    r"[^.،؟!]{0,55}"
    r"(?:عرش\s+(?:الله|الرحمن)|الكرسي|كرسي\s+الله|ملك\s+الله))"
)


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


def _enforce_mallak(text: str) -> str:
    """يضبط اسم القناة دائماً بفتح الميم وتشديد اللام مع الفتح: مَلَّاكُ المَعْرِفَةِ."""
    return re.sub(
        r"(?<![\u0621-\u064A])م[\u064B-\u065F]*ل[\u064B-\u065F]*ا[\u064B-\u065F]*ك([\u064B-\u065F]*)"
        r"\s+ال[\u064B-\u065F]*م[\u064B-\u065F]*ع[\u064B-\u065F]*ر[\u064B-\u065F]*ف[\u064B-\u065F]*ة([\u064B-\u065F]*)",
        lambda m: f"مَلَّاك{m.group(1) or 'ُ'} المَعْرِفَة{m.group(2) or 'ِ'}",
        text,
    )


def clean_narration(text: str) -> str:
    if not text:
        return ""
    text = text.replace("ﷺ", " صلى الله عليه وسلم ")
    text = arabize_numbers(text)
    text = re.sub(r"[a-zA-Z]+", "", text)
    text = re.sub(r"[\[\]\(\)\{\}<>﴿﴾«»\"'`*#@_•/\\|~=+—–\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _enforce_mallak(text)


def _norm_ar(text: str) -> str:
    text = re.sub(r"[\u0640\u064B-\u065F\u0670\u06D6-\u06ED]", "", text or "")
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"[^\u0621-\u064A\s]", " ", text).strip()


def check_theological_and_context_safety(text: str, story: dict | None = None) -> list[str]:
    if not text:
        return []
    issues = []
    era = (story or {}).get("era", "")
    story_id = (story or {}).get("story_id") or (story or {}).get("id", "")
    is_creation = (era == "creation" or story_id.startswith("creation-"))

    norm_txt = _norm_ar(text)
    if is_creation and _FORBIDDEN_IN_CREATION_RE.search(norm_txt):
        issues.append("inappropriate_dramatic_metaphor_in_creation_era")
    if _SACRED_DISRESPECT_RE.search(norm_txt):
        issues.append("disrespectful_phrase_towards_divine_attributes")
    return issues


def get_recent_openings(story_id: str, current_ep_num: int, sstate: dict | None = None, limit: int = 5) -> list[str]:
    openings: list[str] = []
    gen_dir = STORIES_DIR / story_id / "generated"
    if gen_dir.exists():
        for p in sorted(gen_dir.glob(f"{story_id}-*.json"), reverse=True):
            m = re.search(r"-(\d+)\.json$", p.name)
            if not m or int(m.group(1)) >= current_ep_num:
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                for seg in data.get("segments", []):
                    if seg.get("type") == "narration" and seg.get("text"):
                        words = seg["text"].strip().split()[:18]
                        if words:
                            op = " ".join(words)
                            if op not in openings:
                                openings.append(op)
                        break
            except Exception:
                continue
            if len(openings) >= limit:
                break

    for op in (sstate or {}).get("recent_openings") or []:
        op_s = str(op).strip()
        if op_s and op_s not in openings:
            openings.append(op_s)
            if len(openings) >= limit:
                break

    return openings[:limit]


def _advance_pool(pool):
    if hasattr(pool, "_advance"):
        pool._advance()
    elif hasattr(pool, "advance"):
        pool.advance()


def _thinking_config(model: str, minimal_thinking: bool = False) -> dict | None:
    m = model.lower()
    if any(x in m for x in ["gemini-3", "gemini-4"]):
        if minimal_thinking and "lite" in m:
            return {"thinkingLevel": "minimal"}
        return {"thinkingLevel": "low"}
    if "gemini-2.5" in m:
        return {"thinkingBudget": 0 if minimal_thinking else 512}
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
    for fb in ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"):
        if fb not in seen:
            seen.add(fb)
            cleaned.append(fb)
    cleaned.sort(key=lambda m: _MODEL_PENALTIES.get(m, 0))
    return cleaned


def call_llm(
    prompt: str,
    is_json: bool = True,
    timeout: int = 60,
    max_tokens: int = 8192,
    minimal_thinking: bool = False,
    temperature: float = 0.25,
):
    global _LAST_LLM_ERROR
    _LAST_LLM_ERROR = ""

    pool = get_pool()
    breaker = get_breaker()
    raw_models = discover_and_cache(force=False)[:10]
    models = _prepare_models(raw_models) if raw_models else ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]

    for model in models:
        if breaker.is_open(f"model:{model}"):
            _LAST_LLM_ERROR = f"{model}: circuit breaker مفتوح"
            continue

        use_thinking = True
        allow_minimal = minimal_thinking
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
                gen_cfg: dict = {"maxOutputTokens": max_tokens}
                if use_temperature:
                    gen_cfg["temperature"] = temperature
                if is_json:
                    gen_cfg["responseMimeType"] = "application/json"
                if use_thinking:
                    tc = _thinking_config(model, minimal_thinking=allow_minimal)
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

                    if finish == "MAX_TOKENS":
                        _LAST_LLM_ERROR = f"{model}: الرد مقطوع بسبب MAX_TOKENS"
                        print(f"   ⚠️ {_LAST_LLM_ERROR}", flush=True)
                        _advance_pool(pool)
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
                    if allow_minimal:
                        allow_minimal = False
                    else:
                        use_thinking = False
                    continue

                elif r.status_code == 400 and use_temperature and ("temperature" in r.text.lower() or "sampling" in r.text.lower()):
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
            for mi in missing_idx:
                # إلحاق أي فقرة منسية بأقرب حلقة تحافظ على الترتيب الزمني التصاعدي
                target_ep = next(
                    (ep for ep in cleaned_eps if ep["narrative_indices"] and mi < ep["narrative_indices"][-1]),
                    cleaned_eps[-1],
                )
                target_ep["narrative_indices"].append(mi)
                target_ep["narrative_indices"].sort()
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


SCRIPT_PROMPT = """أنت حكواتي وراوٍ إسلامي ذكي تخاطب القلب والعقل بلغة عربية معاصرة **سهلة وبسيطة جداً**، وتلتزم التزاماً صارماً بالمادة الموثقة فقط.

📖 القصة: {story_title}
📖 الحلقة: {ep_title}
📖 الحدث: {event_title}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
الآيات المعتمدة لهذه الحلقة فقط (تُستخدم كلها، ولا يُزاد عليها أي آية من خارجها):
{ayahs_block}

المادة السردية المدققة بالترتيب الزمني (تُصاغ منها الحلقة فقط دون اختراع):
{narrative_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 أسلوب الطرح والتبسيط (سر تميّز الحلقة):
1. **لغة عربية بسيطة جداً وواضحة للجميع**: ابتعد تماماً عن الأسلوب المدرسي الجاف، وعن أسلوب "سأل فلان وقال علّان"، وعن الكلمات التراثية المعقدة. اكتب بجمل قصيرة، واضحة، سلسة ومضبوطة النطق لـ TTS يفهمها أي مشاهد حتى لو لم تكن لغته العربية قوية.
2. **ترجمة أي كلمة صعبة فوراً**: إذا وردت في الآية أو المادة الموثقة كلمة قد يصعب فهمها على الناس (مثل: دحاها، أغطش، رتق، فتق، أقواتها، تحفّهم، كظم)، اشرح معناها في فقرة `"narration"` التي تسبقها بلغة يومية واضحة ("بمعنى أوضح...") دون نقل الآية حرفياً!
3. **العمق قبل العاطفة**: اشرح *الحكمة والدلالة العملية* وراء المشهد واربطها بإحساس الإنسان وحياته اليوم.

🎯 الهيكل الإلزامي للسكريبت:
- كتلة `"narration"`: تشرح المشهد والمعنى والكلمات الصعبة بأسلوب حكواتي مشوّق ودافئ مستند للمادة الموثقة فقط.
- تتلوها مباشرة كتلة `"quran"`: الآية (أو الآيات المتتابعة أرقاماً من نفس السورة) التي **تؤكد وتثبت ما ذُكر في فقرة السرد التي سبقتها مباشرة**.

🎙️ أسلوب الافتتاحية الإلزامي لهذه الحلقة (للتنويع ومنع التكرار):
- افتتح أول كتلة `"narration"` بهذا الأسلوب تحديداً: **{opening_style}**
- يُمنع البدء بعبارات مكررة أو مستهلكة ("في هذه الحلقة"، "أهلاً بكم"، "مرحباً بكم"، "نقف اليوم"، "في رحاب"، "نتحدث اليوم"، "مما لا شك فيه").
{recent_openings_block}

⚠️ قواعد صارمة:
1️⃣ **لا إضافة من الخارج**: التزم بالمعلومات الموثقة أعلاه فقط. لا تخترع أحداثاً ولا تستورد آيات غير موجودة في القائمة.
2️⃣ **فصل التلاوة عن السرد**: يُمنع كتابة الآية القرآنية حرفياً أو اقتباس جملة قرآنية كاملة داخل نص `"narration"`. اشرح معناها وسياقها بكلمات وصفية بسيطة، ثم ضع مرجعها في كتلة `"quran"` التالية مباشرة.
3️⃣ **منع تتابع آيتين غير متسلسلتين**: يُمنع منعاً باتاً وضع كتلتَي `"quran"` متتاليتين مباشرةً دون فقرة `"narration"` فاصلة بينهما، **إلا** إذا كانتا من **نفس السورة ومتتاليتين تماماً بالأرقام** (مثل آية 22 ثم 23). وكذلك لا تجمع داخل كتلة `"quran"` واحدة أرقاماً غير متتالية؛ إذا تباعدت الآيات أو اختلفت السور فاجعل قبل كل آية فقرة `"narration"` تمهد لها.
4️⃣ **لا حشو**: ممنوع الجمل الإنشائية الفارغة ("في خضم الأحداث"، "مما لا شك فيه"، "ومن الجدير بالذكر").
5️⃣ **اللغة**: عربية بسيطة وواضحة فقط، بلا كلمات إنجليزية ولا أرقام رقمية داخل السرد، واسم القناة هو «مَلَّاكُ المَعْرِفَةِ» بفتح الميم وتشديد اللام.
6️⃣ **الطول المستهدف للسرد**: {min_words} إلى {max_words} كلمة (بقدر المادة الموثقة دون مطّ).

{dignity_rules}
{story_rules_block}
{forbidden_rules}
{retry_feedback}

أعد JSON فقط:
{{
  "title": "عنوان مختصر مشوّق للحلقة (3-6 كلمات)",
  "segments": [
    {{"type": "narration", "text": "فقرة السرد الأولى..."}},
    {{"type": "quran", "surah": 112, "surah_name": "الإخلاص", "ayah_numbers": [1], "display": "الإخلاص: 1"}}
  ]
}}"""


def generate_episode_script(
    story,
    ep_title,
    event_title,
    ayahs,
    narrative,
    min_words,
    max_words,
    opening_style,
    recent_openings,
    retry_feedback="",
):
    ayahs_block = "\n".join(
        f"[{a['ref']}] ({a.get('surah_name', '')}): {a['text']}"
        for a in ayahs
    ) if ayahs else "(لا توجد آيات)"

    narrative_block = "\n\n".join(
        f"[{i}] (المصدر: {b.get('source', '')} | الآية المؤكدة: {b.get('anchor_ayah') or 'عام'})\n{b.get('text', '')}"
        for i, b in enumerate(narrative)
    )

    guide = story.get("story_guide", {})
    special_rules = guide.get("special_rules") or []
    story_rules_block = ""
    if special_rules:
        story_rules_block = "📌 ضوابط خاصة بهذه القصة:\n" + "\n".join(f"- {r}" for r in special_rules)

    recent_openings_block = ""
    if recent_openings:
        lines = "\n".join(f"  * {op}..." for op in recent_openings)
        recent_openings_block = (
            "- يُمنع منعاً باتاً تشابه الجملة الأولى في هذه الحلقة مع بدايات الحلقات السابقة التالية:\n"
            + lines
        )

    prompt = SCRIPT_PROMPT.format(
        story_title=story["title"],
        ep_title=ep_title,
        event_title=event_title,
        ayahs_block=ayahs_block,
        narrative_block=narrative_block,
        opening_style=opening_style,
        recent_openings_block=recent_openings_block,
        min_words=min_words,
        max_words=max_words,
        dignity_rules=THEOLOGICAL_DIGNITY_RULES,
        story_rules_block=story_rules_block,
        forbidden_rules=get_llm_forbidden_rules(),
        retry_feedback=retry_feedback,
    )
    return call_llm(prompt, is_json=True, timeout=60, max_tokens=8192, temperature=0.40)


def enrich_and_validate_script(
    script: dict,
    expected_ayahs: list,
    min_words: int,
    max_words: int,
    story: dict | None = None,
    recent_openings: list[str] | None = None,
) -> tuple[bool, list[str]]:
    issues = []
    segments = script.get("segments", [])
    if not segments:
        return False, ["no_segments"]

    expected_refs = {a["ref"] for a in expected_ayahs}
    actual_refs = set()
    actual_refs_list = []
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
                ref_k = f"{surah}:{n}"
                if ref_k in actual_refs:
                    issues.append(f"duplicate_ayah ({ref_k})")
                actual_refs.add(ref_k)
                actual_refs_list.append(ref_k)
            meaningful_segments.append(s)

    missing = sorted(expected_refs - actual_refs)
    if missing:
        issues.append(f"missing_ayahs: {missing}")

    extra = sorted(actual_refs - expected_refs)
    if extra:
        issues.append(f"unexpected_ayahs: {extra}")

    if not meaningful_segments or meaningful_segments[0].get("type") == "quran":
        issues.append("starts_with_quran_without_narration")

    first_narr = next((s.get("text", "") for s in meaningful_segments if s.get("type") == "narration"), "")
    if first_narr:
        norm_first = _norm_ar(first_narr)
        for cliche in _CLICHE_OPENINGS:
            if norm_first.startswith(_norm_ar(cliche)):
                issues.append(f"cliche_opening ({cliche})")
                break
        if recent_openings:
            cur_words = norm_first.split()[:10]
            cur_head4 = cur_words[:4]
            for prev_op in recent_openings:
                prev_words = _norm_ar(prev_op).split()[:10]
                if cur_head4 and cur_head4 == prev_words[:4]:
                    issues.append("repetitive_opening_exact_start")
                    break
                if len(cur_words) >= 6 and len(set(cur_words[:8]) & set(prev_words[:8])) >= 6:
                    issues.append("repetitive_opening_similar_to_previous")
                    break

    for s in meaningful_segments:
        if s.get("type") == "narration":
            for t_issue in check_theological_and_context_safety(s.get("text", ""), story):
                if t_issue not in issues:
                    issues.append(t_issue)

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

        for qb in re.findall(r"﴿([^﴾]+)﴾", raw_text):
            if len(_norm_ar(qb).split()) >= 4:
                if "quran_quoted_with_brackets_in_narration" not in issues:
                    issues.append("quran_quoted_with_brackets_in_narration")
                break

        for a_words, ref in norm_expected:
            a_len = len(a_words)
            if a_len == 5:
                min_match = 5
            elif a_len <= 8:
                min_match = a_len - 1
            else:
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
        elif iss.startswith("duplicate_ayah"):
            notes.append(f"- {iss}: ذُكرت الآية أكثر من مرة؛ ضع كل آية مرة واحدة فقط.")
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
        elif iss in ("inappropriate_dramatic_metaphor_in_creation_era", "disrespectful_phrase_towards_divine_attributes"):
            notes.append(
                "- خطأ عقائدي/أدبي خطير: استُخدمت ألفاظ صراع أو سقوط أو خيانة لا تليق بالله عز وجل ولا بعرشه وكرسيه. "
                "أعد الصياغة بأسلوب تعظيم وتنزيه خاشع يبتعد كلياً عن أي تشبيه بملوك البشر أو سقوط العروش."
            )
        elif iss.startswith("cliche_opening") or iss.startswith("repetitive_opening"):
            notes.append(
                "- الافتتاحية مكررة أو تقليدية؛ غيّر الجملة الأولى كلياً والتزم بأسلوب الافتتاحية المحدد أعلاه."
            )
        elif iss == "empty_narration_segment":
            notes.append("- توجد فقرة narration فارغة؛ اكتب فيها شرحاً مناسباً أو احذفها.")
        else:
            notes.append(f"- {iss}")
    return (
        "\n⚠️ تنبيه تصحيح إلزامي (المحاولة السابقة رُفضت بسبب الأخطاء التالية — عالجها تماماً):\n"
        + "\n".join(notes)
    )


def generate_summary(story, ep_title, segments):
    story_title = story["title"]
    narration_text = " ".join(s.get("text", "") for s in segments if s.get("type") == "narration")[:6000]
    prompt = f"""القصة: {story_title} — {ep_title}
سرد الحلقة:
{narration_text}

{THEOLOGICAL_DIGNITY_RULES}

اكتب "خلاصة وعبرة" عملية ومباشرة من سطرين إلى ثلاثة أسطر:
- بلغة عربية معاصرة سهلة جداً وواضحة للجميع، تعطي المستمع فكرة عملية يطبقها في يومه.
- ممنوع المقدمات المستهلكة ("وفي الختام"، "نستنتج مما سبق") وممنوع أي ألفاظ غير لائقة بالذات الإلهية أو العرش.
أعد النص فقط."""
    for _ in range(2):
        res = call_llm(prompt, is_json=False, timeout=45, max_tokens=2048, minimal_thinking=True, temperature=0.35)
        summary = clean_narration(res) if isinstance(res, str) else ""
        if summary and not check_theological_and_context_safety(summary, story):
            return summary
    return ""


def generate_teaser(story, ep_title, summary_text, next_ep_title):
    story_title = story["title"]
    next_hint = f"موضوع الحلقة القادمة: {next_ep_title}" if next_ep_title else "الانتقال إلى المحطة القادمة في السلسلة"
    summary_hint = f"خلاصة الحلقة الحالية: {summary_text[:400]}" if summary_text else ""

    for attempt in range(2):
        extra_warn = (
            "\n⚠️ تحذير: المحاولة السابقة احتوت ألفاظاً غير لائقة بعظمة الله أو عرشه وكرسيه؛ التزم بالتنزيه والتأدب التام!"
            if attempt > 0 else ""
        )
        prompt = f"""السلسلة: {story_title} — الحلقة الحالية: {ep_title}.
{summary_hint}
{next_hint}

{THEOLOGICAL_DIGNITY_RULES}
{get_llm_forbidden_rules()}
{extra_warn}

المطلوب: اكتب جملة واحدة فقط تشوّق المستمع للحلقة القادمة:
- اربط السؤال بموضوع الحلقة القادمة الحقيقي بلغة عربية سهلة وواضحة ومثيرة للفضول.
- يُمنع منعاً باتاً استخدام كلمات الصراع البشري أو الخيانة أو السقوط أو انهيار العروش/الكراسي.
- جملة واحدة فقط.
أعد الجملة فقط."""
        res = call_llm(prompt, is_json=False, timeout=45, max_tokens=2048, minimal_thinking=True, temperature=0.38)
        teaser = clean_narration(res) if isinstance(res, str) else ""
        if len(teaser) >= 15 and not check_theological_and_context_safety(teaser, story):
            return teaser

    if next_ep_title:
        clean_next = clean_narration(next_ep_title)
        if clean_next and not check_theological_and_context_safety(clean_next, story):
            return f"فما الذي تكشفه لنا النصوص الموثقة في الحلقة القادمة حول {clean_next}؟"
    return "فماذا تكشف لنا الآيات الكريمة والأحاديث الصحيحة في الحلقة القادمة من هذه السلسلة المباركة؟"


def main(clear_latest: bool = True, _visited: set | None = None):
    if clear_latest:
        LATEST_EPISODE.parent.mkdir(parents=True, exist_ok=True)
        if LATEST_EPISODE.exists():
            LATEST_EPISODE.unlink()

    if _visited is None:
        _visited = set()

    print("=" * 65)
    print("🎬 توليد حلقة قصة جديدة (بالسرد الحي المبسّط المؤكد بالآيات)")
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

    recent_openings = get_recent_openings(story_id, ep_num, sstate=sstate, limit=5)
    opening_style = OPENING_STYLES[(ep_num - 1) % len(OPENING_STYLES)]

    script = None
    retry_feedback = ""
    last_issues = []
    for attempt in range(3):
        print(f"   ✍️ محاولة توليد {attempt + 1}/3 (أسلوب الافتتاح: #{((ep_num - 1) % len(OPENING_STYLES)) + 1})...", flush=True)
        candidate = generate_episode_script(
            story=story,
            ep_title=ev_title,
            event_title=meta.get("title") or ev_dir.name,
            ayahs=ayahs_ep,
            narrative=narrative_ep,
            min_words=min_words,
            max_words=max_words,
            opening_style=opening_style,
            recent_openings=recent_openings,
            retry_feedback=retry_feedback,
        )
        if candidate:
            ok, issues = enrich_and_validate_script(
                candidate,
                ayahs_ep,
                min_words,
                max_words,
                story=story,
                recent_openings=recent_openings,
            )
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
    summary = generate_summary(story, ev_title, script["segments"])

    next_sub = sub_idx + 1
    if next_sub < len(episodes_in_event):
        next_ep_title = episodes_in_event[next_sub].get("sub_title", "")
    elif ev_idx + 1 < len(ev_dirs):
        next_meta_p = ev_dirs[ev_idx + 1] / "meta.json"
        next_ep_title = json.loads(next_meta_p.read_text(encoding="utf-8")).get("title", "") if next_meta_p.exists() else ""
    else:
        next_ep_title = None

    print("   📢 تشويق...")
    teaser = generate_teaser(story, ev_title, summary, next_ep_title)

    segments = script["segments"]
    if summary:
        segments.append({"type": "summary", "text": summary, "visual": "sunrise sky"})
    if teaser:
        segments.append({
            "type": "teaser",
            "text": teaser,
            "next_episode_title": f"الحلقة {ep_num + 1}",
            "url": "https://qamarsyria.github.io/qamar-malak/",
            "platform_name": "مَلَّاكُ المَعْرِفَةِ",
        })
    outro = random.choice(OUTRO_TEMPLATES)
    segments.append({"type": "cta", "text": outro["text"], "visual": "mosque architecture"})

    for s in segments:
        if s.get("type") in ("narration", "summary", "teaser", "cta"):
            s["text"] = clean_narration(s["text"])

    first_opening = next(
        (" ".join(s["text"].split()[:18]) for s in segments if s.get("type") == "narration" and s.get("text")),
        "",
    )
    if first_opening:
        updated_openings = [first_opening] + [op for op in recent_openings if op != first_opening]
        sstate["recent_openings"] = updated_openings[:5]
        save_story_state(story_id, sstate)

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
