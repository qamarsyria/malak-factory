#!/usr/bin/env python3
"""
scripts/generate_whispers.py
============================
المحرّك الأدبي واللغوي لمشروع «همسات ملك» (حالات واتساب 35-45 ثانية).
- يتناوب بين 4 عوالم: الدعاء المحتشم، فانتازيا الشوق، الغزل العفيف، وثلاثية الأسماء.
- بوابة نحو وصرف وإعراب ثلاثية المراحل:
  1) التأليف الدرامي وتوزيع الأدوار (he / she / duet).
  2) التدقيق النحوي والصرفي (إعراب الجمل وإعراب المفردات + الممنوع من الصرف للأسماء الثلاثة).
  3) الدمج الآمن بـ SequenceMatcher + قفل عرض «ملك» بلا تشكيل وقفل نطق اللام في TTS.
- متوافق مع مفاتيح Gemini المجانية (تدوير تلقائي + كشف النماذج + منع التكرار).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
import requests

# ==============================================================================
# ⚙️ الإعدادات الأساسية
# ==============================================================================
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "out"
GEN_DIR = REPO_ROOT / "data" / "whispers" / "generated"
HISTORY_FILE = REPO_ROOT / "data" / "whispers" / "history.json"
LATEST_PTR_FILE = OUT_DIR / "latest_whisper.txt"

# خيارات نطق اسم «ملك» في الصوت فقط:
# "fatha"  -> مَلَكْ / مَلَكَ / مَلَكُ (مفرد ملائكة - فتح الميم واللام)
# "shadda" -> مَلَّكْ / مَلَّكَ / مَلَّكُ (تشديد اللام مع الفتح)
MALAK_LAM_MODE = os.environ.get("MALAK_LAM_MODE", "fatha").strip().lower()

# الأسماء الثلاثة الخاصة بها
NAMES_PROFILE = {
    "primary": "ملك",
    "secondary": "مريم",
    "third": "ملاك",
}

THEMES = [
    {
        "id": "prayer_niqab",
        "label": "دعاء ومناجاة بنكهة إيمانية",
        "scene_category": "prayer_niqab",
        "music_mood": "spiritual_warm",
        "ambient_mood": "spiritual_calm",
        "default_speaker_mode": "he",
        "visual_brief": "أنيميشن محتشم دافئ: فتاة منقبة بوقار وسط ورود متفتحة، فراشات ذهبية مضيئة، وقطط هادئة تحت ضوء القمر أو الفجر.",
        "writing_guide": (
            "اكتب دعاءً صادقاً حنوناً خاشعاً بنكهة إيمانية راقية، تستودع فيه الله قلبها وابتسامتها "
            "وسكينتها، وتدعو أن يجمع الله بينكما في خيرٍ وطُهرٍ وبركة."
        ),
    },
    {
        "id": "fantasy_fairies",
        "label": "شوق وغرام وهيام في عالم الخيال",
        "scene_category": "fantasy_fairies",
        "music_mood": "dreamy_piano",
        "ambient_mood": "starlight_magic",
        "default_speaker_mode": "duet",
        "visual_brief": "عالم فانتازيا خيالي مضيء: غابات ساحرة بأزهار متوهجة، بحيرة نجوم، فراشات كريستال، وجنّيات ضوئية لطيفة.",
        "writing_guide": (
            "اكتب نصاً شعرياً أو نثراً مسجوعاً عميق الشوق والهيام بأسلوب عفيف ساحر، يمزج بين "
            "صور النجوم والفراشات والغابات المضيئة والحنين الصادق لها."
        ),
    },
    {
        "id": "modest_love",
        "label": "غزل عفيف وحب هادئ",
        "scene_category": "modest_love",
        "music_mood": "romantic_strings",
        "ambient_mood": "night_rain",
        "default_speaker_mode": "he",
        "visual_brief": "مشاهد أنيميشن رومانسية محترمة: نافذة دافئة على المطر مع قطة تراقب السماء، أو ممر زهور مضيء بفوانيس.",
        "writing_guide": (
            "اكتب غزلاً عفيفاً راقياً يتغزل بعفّتها ووقارها وهدوء روحها وجمال عينيها وحيائها، "
            "بألفاظ طاهرة تسرق القلب بدون أي ابتذال."
        ),
    },
    {
        "id": "trinity_names",
        "label": "ثلاثية الأسماء (ملك ومريم وملاك)",
        "scene_category": "fantasy_fairies",
        "music_mood": "romantic_strings",
        "ambient_mood": "starlight_magic",
        "default_speaker_mode": "duet",
        "visual_brief": "ذروة خيالية تجمع هالة القمر والفراشات الذهبية وأزهار الجنة المضيئة.",
        "writing_guide": (
            "اكتب مقطع ذروة يخطف القلب يدمج بين الرومانسية والدعاء، وينتهي بخاتمة صاعقة للقلب "
            "تجمع أسماءها الثلاثة (ملك، ومريم، وملاك) في سياق مترابط عذب."
        ),
    },
]

_HARAKAT_CLASS = r"\u064B-\u065F\u0670\u0640"
_AR_ALL_CLASS = r"\u0621-\u064A" + _HARAKAT_CLASS
_PUNCT_CHARS = "،؛؟!.:«»\"'()[]-…"

# ==============================================================================
# 🔑 إدارة مفاتيح Gemini واكتشاف النماذج (مستقلة ومضادة للأعطال)
# ==============================================================================
class GeminiKeyPool:
    def __init__(self):
        raw = (
            os.environ.get("GEMINI_API_KEYS", "")
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        )
        keys = [k.strip() for k in re.split(r"[\s,;]+", raw.strip()) if len(k.strip()) > 20]
        # إزالة التكرار مع الحفاظ على الترتيب
        self.keys: list[str] = list(dict.fromkeys(keys))
        if not self.keys:
            raise RuntimeError("❌ لم يتم العثور على مفاتيح GEMINI_API_KEYS صالحة في متغيرات البيئة.")
        self.idx = 0
        self.cooldown_until: dict[str, float] = {}
        self.dead: set[str] = set()
        print(f"🔑 [Pool] تم تحميل {len(self.keys)} مفتاح Gemini.", flush=True)

    def current(self) -> str:
        now = time.time()
        for _ in range(len(self.keys)):
            k = self.keys[self.idx % len(self.keys)]
            if k not in self.dead and self.cooldown_until.get(k, 0) <= now:
                return k
            self.idx = (self.idx + 1) % len(self.keys)

        # إذا الكل في cooldown، ننتظر أقرب مفتاح
        alive = [k for k in self.keys if k not in self.dead]
        if not alive:
            raise RuntimeError("❌ جميع مفاتيح Gemini انتهت صلاحيتها أو تم رفضها.")
        soonest = min(alive, key=lambda k: self.cooldown_until.get(k, 0))
        wait_s = max(1.0, self.cooldown_until.get(soonest, 0) - now)
        wait_s = min(wait_s, 65.0)
        print(f"   ⏳ جميع المفاتيح في هدنة، انتظار {wait_s:.1f} ث...", flush=True)
        time.sleep(wait_s)
        self.idx = self.keys.index(soonest)
        return soonest

    def advance(self):
        self.idx = (self.idx + 1) % len(self.keys)

    def mark_cooldown(self, seconds: float = 65.0):
        k = self.keys[self.idx % len(self.keys)]
        self.cooldown_until[k] = time.time() + seconds
        self.advance()

    def mark_dead(self, reason: str = ""):
        k = self.keys[self.idx % len(self.keys)]
        print(f"   ⚠️ استبعاد مفتاح منتهي/مرفوض ({reason[:40]})", flush=True)
        self.dead.add(k)
        self.advance()


_DISCOVERED_MODELS_CACHE: list[str] | None = None


def discover_text_models(pool: GeminiKeyPool) -> list[str]:
    global _DISCOVERED_MODELS_CACHE
    if _DISCOVERED_MODELS_CACHE:
        return _DISCOVERED_MODELS_CACHE

    fallback = [
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3-flash-preview",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash",
    ]
    excluded = (
        "tts", "image", "banana", "imagen", "veo", "lyria", "embedding",
        "robotics", "live", "transcribe", "translate", "audio", "computer-use",
        "deep-research", "1.5", "2.0", "pro",
    )
    try:
        key = pool.current()
        r = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=15,
        )
        if r.status_code == 200:
            found = []
            for m in r.json().get("models", []):
                name = m.get("name", "").replace("models/", "")
                methods = m.get("supportedGenerationMethods", [])
                nl = name.lower()
                if "generateContent" not in methods:
                    continue
                if any(bad in nl for bad in excluded):
                    continue
                if "gemini" in nl:
                    found.append(name)

            # ترتيب الأولوية: الأحدث أولاً (3.8 -> 3.6 -> 3.5 -> 3 -> 2.5)
            def _rank(n: str) -> tuple:
                nl = n.lower()
                is_lite = 1 if "lite" in nl else 0
                is_prev = 1 if "preview" in nl or "exp" in nl else 0
                ver = 0.0
                m = re.search(r"gemini-(\d+(?:\.\d+)?)", nl)
                if m:
                    ver = float(m.group(1))
                return (-ver, is_lite, is_prev, nl)

            if found:
                found.sort(key=_rank)
                _DISCOVERED_MODELS_CACHE = found[:8]
                print(f"🧠 [Models] المكتشفة: {_DISCOVERED_MODELS_CACHE[:4]}", flush=True)
                return _DISCOVERED_MODELS_CACHE
    except Exception as e:
        print(f"   ⚠️ تعذر كشف النماذج تلقائياً ({str(e)[:60]})، استخدام الافتراضي.", flush=True)

    _DISCOVERED_MODELS_CACHE = fallback
    return fallback


def _is_gemini_3_or_newer(model: str) -> bool:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", model.lower())
    return bool(m and float(m.group(1)) >= 3.0)


def _build_gen_config(model: str, use_thinking: bool = True, max_tokens: int = 8192) -> dict:
    cfg: dict = {
        "maxOutputTokens": max_tokens,
        "responseMimeType": "application/json",
    }
    ml = model.lower()
    # نماذج Gemini 3.x تحذف temperature/topP وتستبدل thinkingBudget بـ thinkingLevel
    if _is_gemini_3_or_newer(ml):
        if use_thinking:
            level = "low" if "lite" in ml else "medium"
            cfg["thinkingConfig"] = {"thinkingLevel": level}
    else:
        cfg["temperature"] = 0.2
        if use_thinking and "2.5" in ml:
            cfg["thinkingConfig"] = {"thinkingBudget": 1024}
    return cfg


def call_gemini_json(pool: GeminiKeyPool, prompt: str, timeout: int = 90) -> dict:
    models = discover_text_models(pool)
    last_err = ""

    for model in models:
        use_thinking = True
        tries = 0
        while tries < 3:
            key = pool.current()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": _build_gen_config(model, use_thinking=use_thinking),
            }
            try:
                r = requests.post(url, json=body, timeout=(15, timeout))
                if r.status_code == 200:
                    cands = r.json().get("candidates", [])
                    parts = cands[0].get("content", {}).get("parts", []) if cands else []
                    txt_parts = [p.get("text", "") for p in parts if not p.get("thought") and p.get("text")]
                    raw_txt = "\n".join(txt_parts) if txt_parts else (parts[-1].get("text", "") if parts else "")
                    s, e = raw_txt.find("{"), raw_txt.rfind("}")
                    if s != -1 and e > s:
                        parsed = json.loads(raw_txt[s : e + 1])
                        pool.advance()
                        return parsed
                    last_err = f"{model}: JSON غير صالح"
                    tries += 1
                    continue

                if r.status_code == 401 or (r.status_code == 403 and "api key" in r.text.lower()):
                    pool.mark_dead(f"HTTP {r.status_code}")
                    continue

                if r.status_code == 400 and use_thinking and "thinking" in r.text.lower():
                    use_thinking = False
                    continue

                if r.status_code == 429:
                    pool.mark_cooldown(65.0)
                    tries += 1
                    continue

                if r.status_code in (400, 403, 404):
                    last_err = f"{model}: HTTP {r.status_code} - {r.text[:120]}"
                    break

                last_err = f"{model}: HTTP {r.status_code}"
                pool.advance()
                tries += 1
                time.sleep(2)
            except requests.exceptions.Timeout:
                last_err = f"{model}: Timeout"
                pool.advance()
                tries += 1
            except json.JSONDecodeError as je:
                last_err = f"{model}: JSONDecodeError ({je})"
                tries += 1
            except Exception as ex:
                last_err = f"{model}: {ex}"
                break

    raise RuntimeError(f"❌ فشلت جميع محاولات Gemini JSON: {last_err}")


# ==============================================================================
# 📚 بوابة النحو والصرف والأسماء الثلاثة
# ==============================================================================
def _word_skeleton(word: str) -> str:
    w = re.sub(rf"[{_HARAKAT_CLASS}]", "", word or "")
    w = re.sub(r"[^\u0621-\u064A]", "", w)
    w = w.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
    w = w.replace("ى", "ي").replace("ة", "ه")
    return w


def _split_token(token: str) -> tuple[str, str, str]:
    m = re.match(rf"^([{_PUNCT_CHARS}]*)(.*?)([{_PUNCT_CHARS}]*)$", token)
    if not m:
        return "", token, ""
    return m.group(1), m.group(2), m.group(3)


def _merge_diacritics_safely(original: str, diacritized: str) -> str:
    """دمج كلمة بكلمة يمنع سقوط أو تبديل الكلمات من المدقق اللغوي."""
    orig_tokens = original.split()
    diac_tokens = diacritized.split()
    if not orig_tokens or not diac_tokens:
        return original

    orig_skels = [_word_skeleton(w) for w in orig_tokens]
    diac_skels = [_word_skeleton(w) for w in diac_tokens]

    matcher = SequenceMatcher(None, orig_skels, diac_skels, autojunk=False)
    merged = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for oi, di in zip(range(i1, i2), range(j1, j2)):
                lead_o, _, trail_o = _split_token(orig_tokens[oi])
                _, core_d, _ = _split_token(diac_tokens[di])
                merged.append(f"{lead_o}{core_d}{trail_o}")
        elif tag == "replace":
            if (i2 - i1) == (j2 - j1):
                for oi, di in zip(range(i1, i2), range(j1, j2)):
                    sim = SequenceMatcher(None, orig_skels[oi], diac_skels[di]).ratio()
                    if sim >= 0.72:
                        lead_o, _, trail_o = _split_token(orig_tokens[oi])
                        _, core_d, _ = _split_token(diac_tokens[di])
                        merged.append(f"{lead_o}{core_d}{trail_o}")
                    else:
                        merged.append(orig_tokens[oi])
            else:
                merged.extend(orig_tokens[i1:i2])
        elif tag == "delete":
            merged.extend(orig_tokens[i1:i2])

    return " ".join(merged) if merged else original


def _strip_malak_for_display(text: str) -> str:
    """
    قفل العرض: اسم «ملك» يظهر على الشاشة بدون أي تشكيل نهائياً،
    مع بقاء بقية الكلام مضبوطاً بالشكل الكامل.
    """
    out = []
    for tok in text.split():
        lead, core, trail = _split_token(tok)
        skel = _word_skeleton(core)
        if skel == "ملك":
            out.append(f"{lead}ملك{trail}")
        elif len(skel) == 4 and skel[0] in ("و", "ف", "ب", "ل", "ك") and skel[1:] == "ملك":
            # الحفاظ على حركة حرف العطف/الجر وتجريد «ملك» من أي تشكيل
            m = re.match(rf"^([وفبلك][{_HARAKAT_CLASS}]*)(م[{_HARAKAT_CLASS}]*ل[{_HARAKAT_CLASS}]*ك[{_HARAKAT_CLASS}]*)$", core)
            if m:
                out.append(f"{lead}{m.group(1)}ملك{trail}")
            else:
                out.append(tok)
        else:
            out.append(tok)
    return " ".join(out)


def _lock_names_for_tts(text: str) -> str:
    """
    قفل النطق الصوتي:
    1) تأكيد فتح الميم واللام (أو شدّة اللام حسب MALAK_LAM_MODE) في اسم «ملك» ومنع قراءته مَلِك/مُلْك.
    2) منع التنوين والجر بالكسرة عن الأسماء الثلاثة الممنوعة من الصرف (مَلَك / مَرْيَم / مَلَاك).
    """
    lam_mark = "\u0651\u064E" if MALAK_LAM_MODE == "shadda" else "\u064E"  # َّ أو َ
    tokens = text.split()
    out = []

    for idx, tok in enumerate(tokens):
        lead, core, trail = _split_token(tok)
        skel = _word_skeleton(core)
        is_pause_end = bool(trail) or (idx == len(tokens) - 1)

        # استخراج البادئة إن وجدت (و / ف / ب / ل / ك)
        prefix = ""
        name_part = core
        if len(skel) >= 4 and skel[0] in ("و", "ف", "ب", "ل", "ك") and skel[1:] in ("ملك", "مريم", "ملاك"):
            pm = re.match(rf"^([وفبلك][{_HARAKAT_CLASS}]*)(.+)$", core)
            if pm:
                prefix, name_part = pm.group(1), pm.group(2)
                skel = skel[1:]

        if skel == "ملك":
            # التقاط حركة الكاف الأخيرة إن كانت فتحة أو ضمة عند الوصل
            last_h = ""
            if not is_pause_end:
                km = re.search(rf"ك([{_HARAKAT_CLASS}]*)$", name_part)
                if km and "\u064E" in km.group(1):
                    last_h = "\u064E"
                elif km and "\u064F" in km.group(1):
                    last_h = "\u064F"
                else:
                    last_h = "\u064E"
            else:
                last_h = "\u0652"  # سكون للوقف الواضح: مَلَكْ
            fixed = f"م\u064Eل{lam_mark}ك{last_h}"
            out.append(f"{lead}{prefix}{fixed}{trail}")

        elif skel == "مريم":
            last_h = "\u0652" if is_pause_end else ("\u064F" if "\u064F" in name_part[-2:] else "\u064E")
            fixed = f"م\u064Eر\u0652ي\u064Eم{last_h}"
            out.append(f"{lead}{prefix}{fixed}{trail}")

        elif skel == "ملاك":
            last_h = "\u0652" if is_pause_end else ("\u064F" if "\u064F" in name_part[-2:] else "\u064E")
            fixed = f"م\u064Eل\u064Eاك{last_h}"
            out.append(f"{lead}{prefix}{fixed}{trail}")
        else:
            out.append(tok)

    return " ".join(out)


def _diacritic_ratio(text: str) -> float:
    diacs = len(re.findall(r"[\u064B-\u0652]", text))
    letters = max(1, len(re.findall(r"[\u0621-\u064A]", text)))
    return diacs / letters


# ==============================================================================
# 📝 البرومبتات الأدبية والنحوية
# ==============================================================================
def _build_pass1_prompt(theme: dict, speaker_mode: str, recent_titles: list[str], recent_openings: list[str]) -> str:
    if theme["id"] == "trinity_names":
        names_instruction = (
            "موضوع هذه الحلقة هو «ثلاثية الأسماء»: يجب أن تظهر أسماؤها الثلاثة (ملك، ومريم، وملاك) "
            "في الحلقة بذكاء شاعري؛ وتُختَم الجملة الأخيرة بجمع الأسماء الثلاثة معاً بأسلوب يخلع القلب!"
        )
    else:
        chosen_name = random.choice(["ملك", "ملك", "مريم", "ملاك"])  # ترجيح الاسم الأول والأهم «ملك»
        names_instruction = (
            f"وظّف اسم «{chosen_name}» بشكل طبيعي ساحر في النص (ويُفضّل أن يظهر في جملة الذروة أو الختام، "
            "ويمكنك التلميح لاسم «ملك» أيضاً). انتبه: عند وضع الاسم في الجملة اجعله منفرداً قدر الإمكان "
            "(مثل: قَلْبَ ملك، يَا ملك، عَيْنَيْ مَرْيَمَ، دَرْبَ مَلَاكَ)."
        )

    if speaker_mode == "duet":
        speaker_rule = (
            "الوضع: ثنائي (duet). وزّع الحلقات على 3 أو 4 مقاطع قصيرة متبادلة بين \"he\" (صوت المحب الدافئ الوقور) "
            "و\"she\" (صوتها الخجول الدافئ)، على أن يبدأ \"he\" ويختم \"he\" بجملة الذروة."
        )
    elif speaker_mode == "she":
        speaker_rule = "الوضع: بصوتها (she). اجعل جميع المقاطع بلسانها في مناجاة عفيفة حانية ودعاء دافئ (2 أو 3 مقاطع)."
    else:
        speaker_rule = "الوضع: بصوته (he). اجعل جميع المقاطع بلسانه في دعاءٍ أو غزلٍ عفيف أو شوقٍ وقور لها (3 مقاطع)."

    avoid_block = ""
    if recent_titles or recent_openings:
        avoid_block = (
            "\nتجنب تكرار هذه العناوين أو الافتتاحيات السابقة:\n"
            f"- عناوين سابقة: {', '.join(recent_titles[-8:])}\n"
            f"- افتتاحيات سابقة: {' | '.join(recent_openings[-6:])}\n"
        )

    return f"""أنت أديب وشاعر عربي معاصر تكتب نصوصاً رومانسية عفيفة ومحتشمة جداً بنكهة روحانية وإيمانية راقية، مخصّصة لحالات واتساب (مدة الإلقاء الكلية بين 35 و45 ثانية فقط).

المطلوب اليوم:
- المحور: {theme['label']}
- التوجيه الأدبي: {theme['writing_guide']}
- الجو البصري المصاحب: {theme['visual_brief']}
- {names_instruction}
- {speaker_rule}
{avoid_block}

قواعد إلزامية للجودة واللغة:
1. المجموع الكلي لكلمات النص في كل المقاطع معاً بين 38 و68 كلمة فقط (حتى يناسب 35-45 ثانية بإلقاء هادئ متأنٍّ).
2. التزم بالعفة والوقار والاحتشام المطلق مع عمق عاطفي يخلع القلب.
3. **قاعدة حاسمة**: يُمنع منعاً باتاً استخدام كلمات (مَلِك / مُلْك / مَلَكَ بمعنى الامتلاك أو الحكم) في النص؛ كلمة «ملك» تُستخدم حصراً كاسمها الشخصي!
4. اختر كلمات فصيحة رصينة سهلة التشكيل والنطق، وخالية من الحشو أو الكليشيهات الرخيصة.
5. لا تضع أي وسوم إنجليزية مثل <sigh> داخل `raw_text`؛ اجعل علامات الترقيم العربية فقط (، . … ؟) هي فواصل التنفّس، واكتب التوجيه الصوتي في `style` بالإنجليزية وفي `tag_before` (إما "" أو "<sigh>" أو "<short pause>").

أعد JSON فقط بهذه البنية الدقيقة:
{{
  "title": "عنوان شاعري مختصر (2-4 كلمات)",
  "poetic_hook": "وصف قصير لجو الفيديو بالانجليزية لمحرك التلوين",
  "segments": [
    {{
      "speaker": "he أو she",
      "style": "وصف دقيق لنبرة الإلقاء بالإنجليزية مثلاً: warm, deeply affectionate, reverent, slow intimate pace",
      "tag_before": "إما فارغ أو <short pause> أو <sigh>",
      "pause_after_ms": 450,
      "raw_text": "النص العربي النقي لهذا المقطع",
      "is_climax": false
    }}
  ]
}}"""


def _build_pass2_grammar_prompt(draft: dict) -> str:
    segs_slim = [
        {"index": i, "speaker": s.get("speaker", "he"), "raw_text": s.get("raw_text", "")}
        for i, s in enumerate(draft.get("segments", []))
    ]
    return f"""أنت عالم لغوي متخصص في النحو والصرف العربي والإعراب التفصيلي وضبط النطق الجَهري (TTS).

راجع المقاطع التالية واضبطها بالتشكيل الكامل (100% — حركة على كل حرف تقبلها البنية، مع الشدّة `ّ` وجوباً):

المقاطع:
{json.dumps(segs_slim, ensure_ascii=False, indent=2)}

قواعد النحو والصرف والإعراب الإلزامية:
1. **إعراب الجمل والمفردات أولاً**: حلّل بنية كل جملة (مبتدأ وخبر، فعل وفاعل ومفعول، إنّ وأخواتها، كان وأخواتها، المنادى، الحال، التمييز، المضاف والمضاف إليه، التوابع) واضبط أواخر الكلمات إعرابياً بدقة 100%.
2. **أحكام الصرف والاشتقاق**: فرّق بدقة بين المبني للمعلوم والمبني للمجهول (خَلَقَ / خُلِقَ، حَفِظَ / حُفِظَ)، والأوزان الصرفية (مُفْعَل / مَفْعُول / فَعِيل).
3. **قاعدة الممنوع من الصرف للأسماء الثلاثة الخاصة بها**:
   - **«مَلَك»**: علم مؤنث ثلاثي متحرك الوسط (كمفرد ملائكة: بفتح الميم وفتح اللام)؛ **ممنوع من الصرف وجوباً**! يُرفع بالضمة (مَلَكُ) ويُنصب ويُجر بالفتحة بلا تنوين (قَلْبَ مَلَكَ / عَنْ مَلَكَ)، وعند الوقف في آخر الفاصلة يُسكَّن آخره (مَلَكْ). لا يجوز أبداً كسر اللام أو تنوينه!
   - **«مَرْيَم»**: علم مؤنث ممنوع من الصرف؛ يُرفع بالضمة (مَرْيَمُ) ويُنصب ويُجر بالفتحة بلا تنوين (لِمَرْيَمَ / يَا مَرْيَمُ)، ويُسكّن عند الوقف (مَرْيَمْ).
   - **«مَلَاك»**: يُرفع بالضمة (مَلَاكُ) ويُنصب ويُجر بالفتحة بلا تنوين كعلم مؤنث (مَلَاكَ)، ويُسكّن عند الوقف (مَلَاكْ).
4. **الشدّة (`ّ`) ضرورية جداً**: ضع الشدّة على كل حرف مشدد وعلى الحرف الشمسي بعد (الـ) الشمسية مثل (اللَّهُ، الرَّحْمَنِ، السَّمَاءِ، الشَّوْقِ، النُّورِ، كُلِّ، حَقِّ، تَحُفُّ، أَحَبَّ).
5. **أحكام الوقف والتقاء الساكنين**:
   - حرّك الساكن المكسور لالتقاء الساكنين عند الوصل (مثل: عَنِ اللَّهِ، مِنَ النُّورِ، اجْعَلِ السَّكِينَةَ).
   - اضبط همزات الوصل والقطع بدقة (أَسْتَوْدِعُكَ، اَللَّهُمَّ، السَّكِينَةَ).
6. **حافظ على نفس الكلمات تماماً** دون زيادة أو حذف، ودون إضافة أي رموز غريبة.

أعد JSON فقط بهذه البنية:
{{
  "segments_audit": [
    {{
      "index": 0,
      "sentence_irab": "إعراب الجملة إجمالاً (نوع الجملة ومحلها وترابط عناصرها)",
      "word_irab": [
        "إعراب الكلمات الحساسة نحوياً وصرفياً باختصار دقيق"
      ],
      "diacritized_text": "النص مشكولاً تشكيلاً كاملاً ودقيقاً 100% مع الحفاظ على علامات الترقيم"
    }}
  ]
}}"""


# ==============================================================================
# 🚀 خط التشغيل الرئيسي
# ==============================================================================
def _load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"episode_counter": 0, "recent": []}


def _save_history(history: dict):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def _select_theme(history: dict, forced_theme: str | None) -> dict:
    if forced_theme:
        for t in THEMES:
            if t["id"] == forced_theme:
                return t
    ep_num = history.get("episode_counter", 0)
    # تناوب ذكي بين العوالم الأربعة
    return THEMES[ep_num % len(THEMES)]


def generate_episode(theme_override: str | None = None, speaker_override: str | None = None) -> Path:
    pool = GeminiKeyPool()
    history = _load_history()

    theme = _select_theme(history, theme_override)
    speaker_mode = speaker_override or theme["default_speaker_mode"]
    # تنويع لطيف لوضع المتحدث في حالة عدم التقييد
    if not speaker_override and theme["id"] in ("prayer_niqab", "modest_love"):
        ep_num = history.get("episode_counter", 0)
        if ep_num % 5 == 4:
            speaker_mode = "she"
        elif ep_num % 3 == 2:
            speaker_mode = "duet"

    recent = history.get("recent", [])
    recent_titles = [r.get("title", "") for r in recent]
    recent_openings = [r.get("opening", "") for r in recent]

    print(f"🌙 [1/3] تأليف حلقة جديدة | المحور: {theme['label']} | الصوت: {speaker_mode}", flush=True)
    draft = call_gemini_json(pool, _build_pass1_prompt(theme, speaker_mode, recent_titles, recent_openings))
    raw_segs = draft.get("segments") or []
    if not raw_segs:
        raise RuntimeError("❌ فشل التوليد: لم يُرجع النموذج أي مقاطع.")

    # التأكد من أن مجموع الكلمات ضمن سقف حالة الواتساب (35-45 ثانية)
    total_words = sum(len((s.get("raw_text") or "").split()) for s in raw_segs)
    print(f"   ✍️ العنوان: {draft.get('title', 'همسة')} | عدد الكلمات: {total_words}", flush=True)

    print("📚 [2/3] التدقيق النحوي والصرفي (إعراب المفردات والجمل + الممنوع من الصرف)...", flush=True)
    audit = call_gemini_json(pool, _build_pass2_grammar_prompt(draft))
    aud_map = {int(a.get("index", i)): a for i, a in enumerate(audit.get("segments_audit") or [])}

    print("🔒 [3/3] الدمج الآمن + قفل اسم «ملك» (بدون تشكيل للعرض / لام مؤكدة للصوت)...", flush=True)
    final_segments = []
    all_names_found = set()

    for idx, seg in enumerate(raw_segs):
        raw_text = re.sub(r"\s+", " ", (seg.get("raw_text") or "").strip())
        if not raw_text:
            continue

        aud = aud_map.get(idx, {})
        diac_cand = re.sub(r"\s+", " ", (aud.get("diacritized_text") or raw_text).strip())

        # الدمج الآمن الذي يمنع حذف أو تبديل أي كلمة
        merged_diac = _merge_diacritics_safely(raw_text, diac_cand)

        # فحص كثافة التشكيل
        ratio = _diacritic_ratio(merged_diac)
        if ratio < 0.40:
            print(f"   ⚠️ كثافة التشكيل منخفضة بالمقطع {idx} ({ratio:.2f})، إعادة تدقيق سريع...", flush=True)
            retry_aud = call_gemini_json(pool, _build_pass2_grammar_prompt({"segments": [{"raw_text": raw_text}]}))
            r_list = retry_aud.get("segments_audit") or []
            if r_list:
                cand2 = r_list[0].get("diacritized_text", "").strip()
                if cand2:
                    merged_diac = _merge_diacritics_safely(raw_text, cand2)
                    ratio = _diacritic_ratio(merged_diac)

        # قفل النصوص المنفصلة للعرض والصوت
        display_text = _strip_malak_for_display(merged_diac)
        tts_core = _lock_names_for_tts(merged_diac)

        tag_before = seg.get("tag_before", "")
        if tag_before not in ("", "<short pause>", "<sigh>"):
            tag_before = ""
        tts_text = f"{tag_before} {tts_core}".strip() if tag_before else tts_core

        # رصد الأسماء المذكورة لتنسيق التوهج الذهبي في الفيديو لاحقاً
        seg_names = []
        for tok in display_text.split():
            sk = _word_skeleton(tok)
            if sk in ("ملك", "مريم", "ملاك") or (len(sk) >= 4 and sk[0] in "وفبلك" and sk[1:] in ("ملك", "مريم", "ملاك")):
                base = sk[1:] if len(sk) >= 4 and sk[0] in "وفبلك" and sk[1:] in ("ملك", "مريم", "ملاك") else sk
                if base not in seg_names:
                    seg_names.append(base)
                all_names_found.add(base)

        spk = str(seg.get("speaker", "he")).strip().lower()
        if spk not in ("he", "she"):
            spk = "he"

        final_segments.append({
            "index": idx,
            "speaker": spk,
            "style": seg.get("style", "warm, tender, deeply affectionate, gentle poetic pace"),
            "tag_before": tag_before,
            "pause_after_ms": int(seg.get("pause_after_ms") or 450),
            "raw_text": raw_text,
            "display_text": display_text,
            "tts_text": tts_text,
            "diacritic_ratio": round(ratio, 3),
            "featured_names": seg_names,
            "is_climax": bool(seg.get("is_climax", idx == len(raw_segs) - 1)),
            "grammar_audit": {
                "sentence_irab": aud.get("sentence_irab", ""),
                "word_irab": aud.get("word_irab", []),
            },
        })
        print(f"   ✓ مقطع {idx} ({spk}) | تشكيل: {ratio:.2f} | العرض: {display_text}", flush=True)

    if not final_segments:
        raise RuntimeError("❌ لا توجد مقاطع صالحة بعد التدقيق.")

    ep_num = history.get("episode_counter", 0) + 1
    today = datetime.datetime.utcnow().strftime("%Y%m%d")
    ep_id = f"whisper_{today}_ep{ep_num:04d}"

    payload = {
        "episode_id": ep_id,
        "episode_number": ep_num,
        "created_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "title": draft.get("title", "همسة لروحها"),
        "theme_id": theme["id"],
        "theme_label": theme["label"],
        "scene_category": theme["scene_category"],
        "music_mood": theme["music_mood"],
        "ambient_mood": theme["ambient_mood"],
        "speaker_mode": speaker_mode,
        "malak_lam_mode": MALAK_LAM_MODE,
        "names_featured": sorted(all_names_found),
        "poetic_hook": draft.get("poetic_hook", ""),
        "segments": final_segments,
    }

    GEN_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ep_path = GEN_DIR / f"{ep_id}.json"
    ep_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # كتابة المسار النسبي كما يتوقع الـ GitHub Workflow
    rel_path = ep_path.relative_to(REPO_ROOT).as_posix()
    LATEST_PTR_FILE.write_text(rel_path, encoding="utf-8")

    # تحديث سجل منع التكرار
    history["episode_counter"] = ep_num
    recent.append({
        "id": ep_id,
        "title": payload["title"],
        "theme": theme["id"],
        "opening": final_segments[0]["raw_text"][:60],
    })
    history["recent"] = recent[-25:]
    _save_history(history)

    print(f"✅ تم حفظ التحفة اللغوية: {rel_path}", flush=True)
    return ep_path


def main():
    parser = argparse.ArgumentParser(description="مولّد همسات ملك الدرامي والنحوي")
    parser.add_argument("--theme", choices=[t["id"] for t in THEMES], default=None)
    parser.add_argument("--speaker", choices=["he", "she", "duet"], default=None)
    args = parser.parse_args()
    generate_episode(theme_override=args.theme, speaker_override=args.speaker)


if __name__ == "__main__":
    main()
