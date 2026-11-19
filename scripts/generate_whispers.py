#!/usr/bin/env python3
"""
scripts/generate_whispers.py
============================
المحرّك الأدبي واللغوي لمشروع «همسات ملك» (حالات واتساب 35-45 ثانية).
- أولوية ذكية للنماذج المجانية الفعّالة (gemini-4-hydrogen, gemini-3.5-flash-lite, gemini-3.8-flash).
- استبعاد تلقائي للنماذج المدفوعة فقط (argon / carbon / neon / pro).
- أسلوب هادئ رصين + بوابة نحو وصرف وإعراب + قفل عرض «ملك» بلا تشكيل.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "out"
GEN_DIR = REPO_ROOT / "data" / "whispers" / "generated"
HISTORY_FILE = REPO_ROOT / "data" / "whispers" / "history.json"
LATEST_PTR_FILE = OUT_DIR / "latest_whisper.txt"

MALAK_LAM_MODE = os.environ.get("MALAK_LAM_MODE", "fatha").strip().lower()

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
            "اكتب دعاءً صادقاً هادئاً خاشعاً بنكهة إيمانية راقية، تستودع فيه الله قلبها وسكينتها، "
            "وتدعو أن يجمع الله بينكما في خيرٍ وطُهرٍ وبركة."
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
            "اكتب نصاً شعرياً أو نثراً عفيفاً هادئ الإيقاع، يمزج بين صور النجوم والفراشات والغابات المضيئة "
            "والحنين الصادق بوقار ورقي."
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
            "اكتب غزلاً عفيفاً هادئاً يتغزل بعفّتها ووقارها وهدوء روحها وجمال حيائها، "
            "بعبارات رقيقة صادقة بلا تكلف."
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
            "اكتب مقطعاً رقيقاً يدمج بين الرومانسية الهادئة والدعاء، وينتهي بخاتمة صادقة مؤثرة "
            "تجمع أسماءها الثلاثة (ملك، ومريم، وملاك) في جملة مترابطة عذبة."
        ),
    },
]

_HARAKAT_CLASS = r"\u064B-\u065F\u0670\u0640"
_PUNCT_CHARS = "،؛؟!.:«»\"'()[]-…"


class GeminiKeyPool:
    def __init__(self):
        raw = (
            os.environ.get("GEMINI_API_KEYS", "")
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        )
        keys = [k.strip() for k in re.split(r"[\s,;]+", raw.strip()) if len(k.strip()) > 20]
        self.keys: list[str] = list(dict.fromkeys(keys))
        if not self.keys:
            raise RuntimeError("❌ لم يتم العثور على مفاتيح GEMINI_API_KEYS صالحة.")
        self.idx = 0
        self.cooldown_until: dict[str, float] = {}
        self.dead: set[str] = set()
        print(f"🔑 [Pool] تم تحميل {len(self.keys)} مفتاح Gemini.", flush=True)

    def alive_count(self) -> int:
        return len(self.keys) - len(self.dead)

    def current(self) -> str:
        now = time.time()
        for _ in range(len(self.keys)):
            k = self.keys[self.idx % len(self.keys)]
            if k not in self.dead and self.cooldown_until.get(k, 0) <= now:
                return k
            self.idx = (self.idx + 1) % len(self.keys)

        alive = [k for k in self.keys if k not in self.dead]
        if not alive:
            raise RuntimeError("❌ جميع مفاتيح Gemini انتهت صلاحيتها.")
        soonest = min(alive, key=lambda k: self.cooldown_until.get(k, 0))
        wait_s = min(20.0, max(1.0, self.cooldown_until.get(soonest, 0) - now))
        print(f"   ⏳ انتظار سريع {wait_s:.1f} ث...", flush=True)
        time.sleep(wait_s)
        self.idx = self.keys.index(soonest)
        return soonest

    def advance(self):
        self.idx = (self.idx + 1) % len(self.keys)

    def mark_cooldown(self, seconds: float = 18.0):
        k = self.keys[self.idx % len(self.keys)]
        self.cooldown_until[k] = time.time() + seconds
        self.advance()

    def mark_dead(self, reason: str = ""):
        k = self.keys[self.idx % len(self.keys)]
        self.dead.add(k)
        print(f"   ⚠️ استبعاد مفتاح منتهي ({reason[:30]}) — السليمة: {self.alive_count()}", flush=True)
        self.advance()


_DISCOVERED_MODELS_CACHE: list[str] | None = None


def discover_text_models(pool: GeminiKeyPool) -> list[str]:
    global _DISCOVERED_MODELS_CACHE
    if _DISCOVERED_MODELS_CACHE:
        return _DISCOVERED_MODELS_CACHE

    # القائمة المجانية الموثوقة مرتبة حسب الأداء والاستقرار
    safe_defaults = [
        "gemini-4-hydrogen",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]
    # استبعاد النماذج المدفوعة فقط والنماذج غير النصية
    excluded = (
        "argon", "carbon", "neon", "ultra", "pro",
        "tts", "image", "banana", "imagen", "veo", "lyria", "embedding",
        "robotics", "live", "transcribe", "translate", "audio", "computer-use",
        "deep-research", "1.5", "2.0", "gemma",
    )
    try:
        for _ in range(min(4, max(1, pool.alive_count()))):
            key = pool.current()
            r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=15)
            if r.status_code in (401, 403):
                pool.mark_dead(f"HTTP {r.status_code}")
                continue
            if r.status_code == 200:
                available = set()
                for m in r.json().get("models", []):
                    name = m.get("name", "").replace("models/", "")
                    methods = m.get("supportedGenerationMethods", [])
                    nl = name.lower()
                    if "generateContent" in methods and "gemini" in nl and not any(bad in nl for bad in excluded):
                        available.add(name)

                ordered = [m for m in safe_defaults if m in available]
                for m in sorted(available):
                    if m not in ordered:
                        ordered.append(m)
                if ordered:
                    _DISCOVERED_MODELS_CACHE = ordered[:10]
                    print(f"🧠 [Models] المجانية المختارة: {_DISCOVERED_MODELS_CACHE[:6]}", flush=True)
                    return _DISCOVERED_MODELS_CACHE
            break
    except Exception:
        pass

    _DISCOVERED_MODELS_CACHE = safe_defaults
    return safe_defaults


def _is_gemini_3_or_newer(model: str) -> bool:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", model.lower())
    return bool(m and float(m.group(1)) >= 3.0)


def _build_gen_config(model: str, use_thinking: bool = True, max_tokens: int = 8192) -> dict:
    cfg: dict = {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"}
    ml = model.lower()
    if _is_gemini_3_or_newer(ml):
        if use_thinking:
            cfg["thinkingConfig"] = {"thinkingLevel": "low" if "lite" in ml else "medium"}
    else:
        cfg["temperature"] = 0.2
        if use_thinking and "2.5" in ml:
            cfg["thinkingConfig"] = {"thinkingBudget": 1024}
    return cfg


def call_gemini_json(pool: GeminiKeyPool, prompt: str, timeout: int = 45) -> dict:
    models = discover_text_models(pool)
    last_err = ""

    for model in models:
        use_thinking = True
        tries = 0
        max_tries = min(3, max(2, pool.alive_count()))
        while tries < max_tries and pool.alive_count() > 0:
            key = pool.current()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": _build_gen_config(model, use_thinking=use_thinking),
            }
            try:
                r = requests.post(url, json=body, timeout=(12, timeout))
                if r.status_code == 200:
                    cands = r.json().get("candidates", [])
                    parts = cands[0].get("content", {}).get("parts", []) if cands else []
                    txt_parts = [p.get("text", "") for p in parts if not p.get("thought") and p.get("text")]
                    raw_txt = "\n".join(txt_parts) if txt_parts else (parts[-1].get("text", "") if parts else "")
                    s, e = raw_txt.find("{"), raw_txt.rfind("}")
                    if s != -1 and e > s:
                        parsed = json.loads(raw_txt[s : e + 1])
                        pool.advance()
                        print(f"   ✅ [LLM] {model}", flush=True)
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
                    txt_low = r.text.lower()
                    if "limit: 0" in txt_low or '"limit":0' in txt_low:
                        last_err = f"{model}: غير متاح على المجاني (limit=0)"
                        break
                    pool.mark_cooldown(18.0)
                    tries += 1
                    continue

                if r.status_code == 503:
                    last_err = f"{model}: HTTP 503"
                    print(f"   ⚠️ {model} مشغول (503)، تجربة المفتاح/النموذج التالي...", flush=True)
                    pool.advance()
                    tries += 1
                    time.sleep(1.5)
                    continue

                if r.status_code in (400, 403, 404):
                    last_err = f"{model}: HTTP {r.status_code}"
                    break

                last_err = f"{model}: HTTP {r.status_code}"
                pool.advance()
                tries += 1
                time.sleep(1.5)
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
    out = []
    for tok in text.split():
        lead, core, trail = _split_token(tok)
        skel = _word_skeleton(core)
        if skel == "ملك":
            out.append(f"{lead}ملك{trail}")
        elif len(skel) == 4 and skel[0] in ("و", "ف", "ب", "ل", "ك") and skel[1:] == "ملك":
            m = re.match(rf"^([وفبلك][{_HARAKAT_CLASS}]*)(م[{_HARAKAT_CLASS}]*ل[{_HARAKAT_CLASS}]*ك[{_HARAKAT_CLASS}]*)$", core)
            out.append(f"{lead}{m.group(1)}ملك{trail}" if m else tok)
        else:
            out.append(tok)
    return " ".join(out)


def _lock_names_for_tts(text: str) -> str:
    lam_mark = "\u0651\u064E" if MALAK_LAM_MODE == "shadda" else "\u064E"
    tokens = text.split()
    out = []

    for idx, tok in enumerate(tokens):
        lead, core, trail = _split_token(tok)
        skel = _word_skeleton(core)
        is_pause_end = bool(trail) or (idx == len(tokens) - 1)

        prefix = ""
        name_part = core
        if len(skel) >= 4 and skel[0] in ("و", "ف", "ب", "ل", "ك") and skel[1:] in ("ملك", "مريم", "ملاك"):
            pm = re.match(rf"^([وفبلك][{_HARAKAT_CLASS}]*)(.+)$", core)
            if pm:
                prefix, name_part = pm.group(1), pm.group(2)
                skel = skel[1:]

        if skel == "ملك":
            last_h = "\u0652" if is_pause_end else ("\u064F" if "\u064F" in name_part[-2:] else "\u064E")
            out.append(f"{lead}{prefix}م\u064Eل{lam_mark}ك{last_h}{trail}")
        elif skel == "مريم":
            last_h = "\u0652" if is_pause_end else ("\u064F" if "\u064F" in name_part[-2:] else "\u064E")
            out.append(f"{lead}{prefix}م\u064Eر\u0652ي\u064Eم{last_h}{trail}")
        elif skel == "ملاك":
            last_h = "\u0652" if is_pause_end else ("\u064F" if "\u064F" in name_part[-2:] else "\u064E")
            out.append(f"{lead}{prefix}م\u064Eل\u064Eاك{last_h}{trail}")
        else:
            out.append(tok)

    return " ".join(out)


def _diacritic_ratio(text: str) -> float:
    diacs = len(re.findall(r"[\u064B-\u0652]", text))
    letters = max(1, len(re.findall(r"[\u0621-\u064A]", text)))
    return diacs / letters


def _build_pass1_prompt(theme: dict, speaker_mode: str, recent_titles: list[str], recent_openings: list[str]) -> str:
    if theme["id"] == "trinity_names":
        names_instruction = (
            "موضوع هذه الحلقة هو «ثلاثية الأسماء»: يجب أن تظهر أسماؤها الثلاثة (ملك، ومريم، وملاك) "
            "في الحلقة؛ وتُختَم الجملة الأخيرة بجمع الأسماء الثلاثة معاً برقة ووقار."
        )
    else:
        chosen_name = random.choice(["ملك", "ملك", "مريم", "ملاك"])
        names_instruction = (
            f"وظّف اسم «{chosen_name}» بشكل طبيعي في النص (منفرداً ما أمكن، مثل: قَلْبَ ملك، يَا ملك، عَيْنَيْ مَرْيَمَ، دَرْبَ مَلَاكَ)."
        )

    if speaker_mode == "duet":
        speaker_rule = (
            "الوضع: ثنائي (duet). وزّع الحلقات على 3 مقاطع قصيرة متبادلة بين \"he\" و\"she\"، على أن يبدأ \"he\" ويختم \"he\"."
        )
    elif speaker_mode == "she":
        speaker_rule = "الوضع: بصوتها (she). اجعل المقاطع بلسانها في مناجاة هادئة ودعاء رقيق (2 أو 3 مقاطع)."
    else:
        speaker_rule = "الوضع: بصوته (he). اجعل المقاطع بلسانه في دعاءٍ أو غزلٍ عفيف أو شوقٍ هادئ رصين (3 مقاطع)."

    avoid_block = ""
    if recent_titles or recent_openings:
        avoid_block = (
            "\nتجنب تكرار هذه العناوين أو الافتتاحيات:\n"
            f"- عناوين: {', '.join(recent_titles[-8:])}\n"
            f"- افتتاحيات: {' | '.join(recent_openings[-6:])}\n"
        )

    return f"""أنت أديب عربي تكتب نصوصاً رومانسية عفيفة ومحتشمة جداً بنكهة روحانية هادئة، مخصّصة لحالات واتساب (35 إلى 45 ثانية).

المطلوب اليوم:
- المحور: {theme['label']}
- التوجيه الأدبي: {theme['writing_guide']}
- {names_instruction}
- {speaker_rule}
{avoid_block}

قواعد إلزامية:
1. مجموع الكلمات الكلي بين 38 و65 كلمة فقط.
2. التزم بالهدوء والرزانة والصدق العاطفي؛ **ابتعد تماماً عن المبالغة الدرامية والتمثيل المتكلف**.
3. يُمنع استخدام كلمات (مَلِك / مُلْك / مَلَكَ بمعنى الحكم أو الامتلاك)؛ كلمة «ملك» تُستخدم حصراً كاسمها الشخصي.
4. ضع علامات الترقيم العربية الطبيعية فقط (، . … ؟) بدون أي وسوم إنجليزية داخل `raw_text`.

أعد JSON فقط:
{{
  "title": "عنوان هادئ مختصر (2-4 كلمات)",
  "poetic_hook": "وصف بصري بالإنجليزية",
  "segments": [
    {{
      "speaker": "he أو she",
      "pause_after_ms": 450,
      "raw_text": "النص العربي النقي",
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

راجع المقاطع التالية واضبطها بالتشكيل الكامل (100% مع الشدّة `ّ` وجوباً):

{json.dumps(segs_slim, ensure_ascii=False, indent=2)}

قواعد النحو والصرف والإعراب:
1. حلّل إعراب كل جملة وكلمة أولاً (الفاعل، المفعول، المضاف إليه، النواسخ، التوابع، المبني للمعلوم والمجهول).
2. الممنوع من الصرف للأسماء الثلاثة:
   - «مَلَك»: بفتح الميم واللام بلا تنوين ولا جر بالكسرة (قَلْبَ مَلَكَ)، وعند الوقف (مَلَكْ).
   - «مَرْيَم»: بلا تنوين ولا جر بالكسرة (لِمَرْيَمَ)، وعند الوقف (مَرْيَمْ).
   - «مَلَاك»: بلا تنوين (مَلَاكَ)، وعند الوقف (مَلَاكْ).
3. ضع الشدّة على الحروف المشددة واللام الشمسية (اللَّهُ، السَّمَاءِ، النُّورِ، كُلِّ، تَحُفُّ).
4. حرّك التقاء الساكنين واضبط همزات الوصل والقطع.
5. حافظ على نفس الكلمات تماماً دون زيادة أو نقصان.

أعد JSON فقط:
{{
  "segments_audit": [
    {{
      "index": 0,
      "sentence_irab": "إعراب الجملة إجمالاً",
      "word_irab": ["إعراب الكلمات الحساسة"],
      "diacritized_text": "النص مشكولاً 100%"
    }}
  ]
}}"""


def _fallback_from_bank(theme: dict, speaker_mode: str) -> dict:
    """احتياط طوارئ من البنك المنقح إذا تعطلت خوادم Gemini تماماً."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from whispers_bank import WHISPERS_BANK
        all_items = [item for lst in WHISPERS_BANK.values() for item in lst]
        chosen = random.choice(all_items)
    except Exception:
        chosen = {
            "display_text": "اَللَّهُمَّ إِنِّي أَسْتَوْدِعُكَ قَلْبَ ملك، وَأَسْأَلُكَ سَكِينَةً تَحُفُّ مَرْيَمَ، وَفَرَحًا لَا يُفَارِقُ مَلَاكَ.",
            "tts_text": "اَللَّهُمَّ إِنِّي أَسْتَوْدِعُكَ قَلْبَ مَلَكْ، وَأَسْأَلُكَ سَكِينَةً تَحُفُّ مَرْيَمْ، وَفَرَحًا لَا يُفَارِقُ مَلَاكْ.",
        }
    spk = "she" if speaker_mode == "she" else "he"
    return {
        "title": "همسة لروحها",
        "poetic_hook": "calm spiritual night",
        "segments": [{
            "speaker": spk,
            "pause_after_ms": 450,
            "raw_text": chosen.get("display_text", ""),
            "diacritized_override": chosen.get("tts_text") or chosen.get("display_text", ""),
            "is_climax": True,
        }],
    }


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
    return THEMES[history.get("episode_counter", 0) % len(THEMES)]


def generate_episode(theme_override: str | None = None, speaker_override: str | None = None) -> Path:
    history = _load_history()
    theme = _select_theme(history, theme_override)
    speaker_mode = speaker_override or theme["default_speaker_mode"]
    if not speaker_override and theme["id"] in ("prayer_niqab", "modest_love"):
        ep_num = history.get("episode_counter", 0)
        if ep_num % 5 == 4:
            speaker_mode = "she"
        elif ep_num % 3 == 2:
            speaker_mode = "duet"

    recent = history.get("recent", [])
    recent_titles = [r.get("title", "") for r in recent]
    recent_openings = [r.get("opening", "") for r in recent]

    draft = None
    aud_map = {}

    try:
        pool = GeminiKeyPool()
        print(f"🌙 [1/3] تأليف حلقة هادئة | المحور: {theme['label']} | الصوت: {speaker_mode}", flush=True)
        draft = call_gemini_json(pool, _build_pass1_prompt(theme, speaker_mode, recent_titles, recent_openings))
        raw_segs = draft.get("segments") or []
        if not raw_segs:
            raise RuntimeError("فارغ")

        print("📚 [2/3] التدقيق النحوي والصرفي والإعرابي...", flush=True)
        try:
            audit = call_gemini_json(pool, _build_pass2_grammar_prompt(draft))
            aud_map = {int(a.get("index", i)): a for i, a in enumerate(audit.get("segments_audit") or [])}
        except Exception as e:
            print(f"   ⚠️ تعذر التدقيق الثاني ({str(e)[:60]})، متابعة بالمتاح.", flush=True)
    except Exception as e:
        print(f"⚠️ تعذر التوليد عبر Gemini ({str(e)[:90]}) — تفعيل البنك المنقح كاحتياط!", flush=True)
        draft = _fallback_from_bank(theme, speaker_mode)

    raw_segs = draft.get("segments") or []
    print("🔒 [3/3] الدمج الآمن + قفل اسم «ملك»...", flush=True)
    final_segments = []
    all_names_found = set()

    for idx, seg in enumerate(raw_segs):
        raw_text = re.sub(r"\s+", " ", (seg.get("raw_text") or "").strip())
        if not raw_text:
            continue

        aud = aud_map.get(idx, {})
        diac_cand = re.sub(r"\s+", " ", (aud.get("diacritized_text") or seg.get("diacritized_override") or raw_text).strip())
        merged_diac = _merge_diacritics_safely(raw_text, diac_cand)
        ratio = _diacritic_ratio(merged_diac)

        display_text = _strip_malak_for_display(merged_diac)
        tts_text = _lock_names_for_tts(merged_diac)

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
            "style": "calm, natural, warm, poised conversational reading, understated sincerity, no theatrics",
            "tag_before": "",
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

    rel_path = ep_path.relative_to(REPO_ROOT).as_posix()
    LATEST_PTR_FILE.write_text(rel_path, encoding="utf-8")

    history["episode_counter"] = ep_num
    recent.append({"id": ep_id, "title": payload["title"], "theme": theme["id"], "opening": final_segments[0]["raw_text"][:60]})
    history["recent"] = recent[-25:]
    _save_history(history)

    print(f"✅ تم حفظ الحلقة: {rel_path}", flush=True)
    return ep_path


def main():
    parser = argparse.ArgumentParser(description="مولّد همسات ملك")
    parser.add_argument("--theme", choices=[t["id"] for t in THEMES], default=None)
    parser.add_argument("--speaker", choices=["he", "she", "duet"], default=None)
    args = parser.parse_args()
    generate_episode(theme_override=args.theme, speaker_override=args.speaker)


if __name__ == "__main__":
    main()
