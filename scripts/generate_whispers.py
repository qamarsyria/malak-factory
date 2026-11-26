#!/usr/bin/env python3
"""
scripts/generate_whispers.py
============================
المحرّك الأدبي واللغوي لمشروع «همسات ملك»:
- ينتج خواطر وجدانية عذبة وأدعية خاشعة ورسائل حنين صادقة بلغة إنسانية قريبة من القلب.
- يحافظ على إيجاز رشيق (32-46 كلمة) يناسب الدفتر القطني الرومانسي بوضوح وجمال.
- يفرض صيغة المؤنث حصراً عند مخاطبة (ملك / مريم / ملاك) برمجياً ولغوياً مع صون ضمائر الدعاء لله.
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
        "id": "modest_love",
        "label": "خواطر الحب الصادق والمواقف الدافئة",
        "scene_category": "modest_love",
        "music_mood": "romantic_strings",
        "ambient_mood": "night_rain",
        "default_speaker_mode": "he",
        "writing_guide": (
            "اكتب خاطرة رومانسية صادقة وإنسانية جداً عن معنى الحب الحقيقي: الأمان، والوفاء في التعب "
            "قبل الفرح، والاهتمام بالتفاصيل الصغيرة، بلغة بسيطة عذبة تدخل القلب فوراً دون تعقيد."
        ),
    },
    {
        "id": "prayer_niqab",
        "label": "أدعية مؤثرة ومناجاة خاشعة",
        "scene_category": "prayer_niqab",
        "music_mood": "spiritual_warm",
        "ambient_mood": "spiritual_calm",
        "default_speaker_mode": "he",
        "writing_guide": (
            "اكتب دعاءً صادقاً يمسّ شغاف القلب: تناجي فيه الله أن يحفظ قلبها من كل هم، "
            "ويزرع السكينة في أيامها، ويجمعكما على الطهر والخير والبركة في الدنيا والآخرة."
        ),
    },
    {
        "id": "fantasy_fairies",
        "label": "رسائل شوق وحنين ليلية دافئة",
        "scene_category": "fantasy_fairies",
        "music_mood": "dreamy_piano",
        "ambient_mood": "starlight_magic",
        "default_speaker_mode": "duet",
        "writing_guide": (
            "اكتب رسالة حنين ليلية رقيقة مليئة بالدفء والشوق العفيف تحت أضواء السكون والمطر، "
            "تعبر عن الاشتياق والسكينة حين تخطر على البال."
        ),
    },
    {
        "id": "trinity_names",
        "label": "ثلاثية الأسماء (ملك ومريم وملاك)",
        "scene_category": "fantasy_fairies",
        "music_mood": "romantic_strings",
        "ambient_mood": "starlight_magic",
        "default_speaker_mode": "duet",
        "writing_guide": (
            "اكتب خاطرة وجدانية ناعمة تلتقي فيها أسماؤها الثلاثة (ملك، ومريم، وملاك) بعفوية رقيقة: "
            "طهر الدعاء ودفء الحب ونقاء الروح، بدون ألقاب غريبة."
        ),
    },
]

LITERARY_FORMS = [
    "خاطرة نثرية إنسانية عميقة تبدأ بتعريف مؤثر للحب أو الوفاء ثم تُهدى لها بصدق ورقة.",
    "مناجاة ودعاء خاشع يبدأ بـ (اللهم) ويفيض بالمحبة الصادقة والرجاء بأن يحفظها الله ويجمعكما بخير.",
    "رسالة شوق مسائية رقيقة تخاطب قلبها مباشرةً بعبارات بسيطة دافئة خالية من التعقيد.",
    "حوار وجداني قصير وعذب (هو وهي) يتبادلان فيه عهد البقاء والوفاء في الشدة قبل الرخاء.",
]

_HARAKAT_CLASS = r"\u064B-\u065F\u0670\u0640"
_PUNCT_CHARS = "،؛؟!.:«»\"'()[]-…"

_DIVINE_CLAUSE_RE = re.compile(
    r"(?:اللهم|اللَّهُمَّ|يا\s*رب|يَا\s*رَبِّ|إلهي|إِلَهِي|سبحانك|سُبْحَانَكَ|أسألك|أَسْأَلُكَ|بحفظك|بِحِفْظِكَ|رحمتك|رَحْمَتَكَ|بفضلك|بِفَضْلِكَ|لطفك|لُطْفَكَ|الله|اللَّه)"
)


def _enforce_feminine_address(text: str, speaker: str = "he") -> str:
    """يضمن بقاء خطاب الأنثى (ملك/مريم/ملاك) مكسوراً للمؤنث دون المساس بضمائر الدعاء لله."""
    if not text or speaker == "she":
        return text

    parts = re.split(r"([،؛؟!.\n]+)", text)
    fixed_parts: list[str] = []
    fem_verbs = (
        "بَقِيت", "كُنْت", "صِرْت", "أَصْبَحْت", "سَكَنْت", "زَرَعْت",
        "غَرَسْت", "مَلَأْت", "أَشْرَقْت", "جِئْت", "أَقْبَلْت", "حَفِظْت",
    )
    fem_verb_re = re.compile(rf"\b({'|'.join(fem_verbs)})َ\b")
    prep_re = re.compile(r"\b(لَك|بِك|فِيك|عَنْك|مِنْك|إِلَيْك|عَلَيْك|مَعَك)َ\b")
    noun_kaf_re = re.compile(
        r"\b((?:عَهْد|قَلْب|رُوح|عَيْن|اسْم|بِاسْم|طَيْف|قُرْب|حُضُور|غِيَاب|حُبّ?|وُجُود|صَوْت|وَقْت|دَرْب|يَوْم|حَيَات)[َُِ]ك)َ\b"
    )

    for seg in parts:
        if not seg or re.fullmatch(r"[،؛؟!.\s\n]+", seg):
            fixed_parts.append(seg)
            continue
        if _DIVINE_CLAUSE_RE.search(seg):
            fixed_parts.append(seg)
            continue

        s = re.sub(r"\bأَنْتَ\b", "أَنْتِ", seg)
        s = fem_verb_re.sub(r"\1ِ", s)
        s = prep_re.sub(r"\1ِ", s)
        s = noun_kaf_re.sub(r"\1ِ", s)
        # معالجة ذكية لـ (اسمك/قلبك/عهدك + فتحة نهائية) حتى مع تفاوت الحركات الداخلية
        s = re.sub(
            r"([\u0621-\u064A\u064B-\u0652]{2,}(?:ك|كِ))َ(?=$|\s|[،؛؟!.])",
            lambda m: m.group(0)[:-1] + "ِ",
            s,
        )
        fixed_parts.append(s)

    return "".join(fixed_parts)


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

    safe_defaults = [
        "gemini-2.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-flash-latest",
        "gemini-2.5-flash-lite",
    ]
    excluded = (
        "pro", "ultra", "tts", "image", "banana", "imagen", "veo", "lyria",
        "embedding", "robotics", "live", "transcribe", "translate", "audio",
        "computer-use", "deep-research", "1.5", "2.0", "gemma",
    )
    try:
        for _ in range(min(8, max(1, pool.alive_count()))):
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
                    _DISCOVERED_MODELS_CACHE = ordered[:8]
                    print(f"🧠 [Models] المختارة: {_DISCOVERED_MODELS_CACHE[:5]}", flush=True)
                    return _DISCOVERED_MODELS_CACHE
            break
    except Exception:
        pass

    _DISCOVERED_MODELS_CACHE = safe_defaults
    return safe_defaults


def _is_gemini_3_or_newer(model: str) -> bool:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", model.lower())
    return bool(m and float(m.group(1)) >= 3.0)


def _build_gen_config(model: str, use_thinking: bool = True, max_tokens: int = 8192, temp: float = 0.72) -> dict:
    cfg: dict = {"maxOutputTokens": max_tokens, "responseMimeType": "application/json", "temperature": temp}
    ml = model.lower()
    if _is_gemini_3_or_newer(ml):
        if use_thinking:
            cfg["thinkingConfig"] = {"thinkingLevel": "low" if "lite" in ml else "medium"}
    else:
        if use_thinking and "2.5" in ml:
            cfg["thinkingConfig"] = {"thinkingBudget": 512}
    return cfg


def call_gemini_json(pool: GeminiKeyPool, prompt: str, timeout: int = 45, temp: float = 0.72) -> dict:
    models = discover_text_models(pool)
    last_err = ""

    for model in models:
        use_thinking = True
        use_temp = True
        tries = 0
        max_tries = min(4, max(2, pool.alive_count()))
        while tries < max_tries and pool.alive_count() > 0:
            key = pool.current()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            gen_cfg = _build_gen_config(model, use_thinking=use_thinking, temp=temp)
            if not use_temp:
                gen_cfg.pop("temperature", None)
            body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
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

                if r.status_code == 400 and use_temp and "temperature" in r.text.lower():
                    use_temp = False
                    continue

                if r.status_code == 429:
                    txt_low = r.text.lower()
                    if "limit: 0" in txt_low or '"limit":0' in txt_low:
                        last_err = f"{model}: limit=0"
                        break
                    pool.mark_cooldown(18.0)
                    tries += 1
                    continue

                if r.status_code == 503:
                    last_err = f"{model}: HTTP 503"
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


def _build_pass1_prompt(
    theme: dict,
    literary_form: str,
    speaker_mode: str,
    recent_titles: list[str],
    recent_openings: list[str],
) -> str:
    if theme["id"] == "trinity_names":
        names_instruction = (
            "اجمع في النص أسماءها الثلاثة (ملك، ومريم، وملاك) بعفوية رقيقة وبسيطة — وممنوع تماماً وضع ألقاب غريبة مثل 'مريم الكبرى'."
        )
    else:
        chosen_name = random.choice(["ملك", "ملك", "مريم", "ملاك"])
        names_instruction = (
            f"وجّه الهمسة إلى الأنثى الغالية «{chosen_name}» برقة وعفوية (مثل: يا {chosen_name}، في قلبِ {chosen_name}، دعائي لكِ يا {chosen_name})."
        )

    if speaker_mode == "duet":
        speaker_rule = (
            "الوضع: ثنائي دافئ (duet) من 3 مقاطع: يبدأ \"he\" بمخاطبة «ملك/مريم/ملاك» (أنثى بصيغة كِ/أنتِ)، "
            "وترد \"she\" بضمير المتكلم الجمع (نحن/قلوبنا/معاً) أو بالدعاء لله، ويختم \"he\" بمخاطبتها بصيغة المؤنث."
        )
    elif speaker_mode == "she":
        speaker_rule = (
            "الوضع: بصوتها (she) في 3 جمل قصيرة تفيض بالمشاعر الصادقة والدعاء لله أو الحديث عن الأمان والوفاء "
            "(تجنبي أي ضمير مذكر منفرد على الشاشة، واستخدمي ضمير الجمع نا/معاً أو خطاب الخالق في الدعاء)."
        )
    else:
        speaker_rule = (
            "الوضع: بصوته (he) في 3 جمل قصيرة دافئة صادقة تخاطب الأنثى الحبيبة (ملك/مريم/ملاك) "
            "بصيغة المؤنث دائماً (أنتِ، عهدكِ، قلبكِ، بقيتِ)."
        )

    avoid_block = ""
    if recent_titles or recent_openings:
        avoid_block = (
            "\n🚫 ممنوع تكرار أي عنوان أو افتتاحية مما سبق:\n"
            f"- عناوين سابقة: {', '.join(recent_titles[-12:])}\n"
            f"- افتتاحيات سابقة: {' | '.join(recent_openings[-10:])}\n"
        )

    scene_rule = (
        "المشاهد الإنجليزية يجب أن تكون **دينية وقورة ومحتشمة فقط** (فانوس دافئ، مسجد مضاء وقت الفجر، ورد أبيض، سماء نجوم، أو منتقبة تدعو بخشوع)."
        if theme["id"] == "prayer_niqab"
        else "المشاهد الإنجليزية يجب أن تكون **رومانسية دافئة ومحتشمة** (شخصان محبان يسيران بوقار بين أزهار الخريف، نافذة مطر على ضوء شمعة دافئ وورد أحمر، حديقة متألقة وقت الغروب)."
    )

    return f"""أنت كاتب خواطر عربية معاصر تكتب بأسلوب دافئ وصادق وقريب جداً من القلب (مثل خواطر الحب الراقية البسيطة التي يفهمها الجميع ويتأثر بها فوراً).

- المحور: {theme['label']}
- التوجيه: {theme['writing_guide']}
- القالب المطلوب: **{literary_form}**
- {names_instruction}
- {speaker_rule}
{avoid_block}

قواعد صارمة جداً لجمال النص ووضوحه:
1. **تذكير قاطع بجنس الشخصية**: ملك ومريم وملاك **أنثى** دائماً! يُمنع منعاً باتاً مخاطبتها بصيغة المذكر (ممنوع: عهدكَ، بقيتَ، أنتَ، لكَ، اسمكَ). يجب استخدام صيغة المؤنث المكسورة (`عهدكِ، بقيتِ، أنتِ، لكِ، اسمكِ`).
2. **الطول الإجمالي قصير ورشيق: 32 إلى 46 كلمة فقط** موزعة على 3 مقاطع متناسقة (كل مقطع 10-16 كلمة).
3. **ممنوع منعاً باتاً** كتابة قوافٍ شعرية متكلّفة أو استعارات قاموسية غريبة غير مفهومة (مثل: سنابك الشوق، الميالا، قد هلالا، فيء ظلالها).
4. يُمنع استخدام كلمة (مَلِك / مُلْك) بمعنى الحكم؛ كلمة «ملك» هي اسمها الشخصي فقط.
5. أرفق لكل مقطع `scene_prompt_en` بالإنجليزية يصف مشهداً يطابق معنى المقطع حرفياً. ({scene_rule})

أعد JSON فقط:
{{
  "title": "عنوان رقيق بسيط (2-4 كلمات)",
  "segments": [
    {{
      "speaker": "he أو she",
      "pause_after_ms": 420,
      "raw_text": "نص عربي عذب وبليغ ومفهوم...",
      "scene_prompt_en": "visual scene matching this sentence in English (15-24 words)",
      "is_climax": false
    }}
  ]
}}"""


def _build_pass2_grammar_prompt(draft: dict) -> str:
    segs_slim = [
        {"index": i, "speaker": s.get("speaker", "he"), "raw_text": s.get("raw_text", "")}
        for i, s in enumerate(draft.get("segments", []))
    ]
    return f"""أنت مدقق أدبي ولغوي خبير تراجع نصاً وجدانياً قبل عرضه على الشاشة وقراءته صوتياً.

النص المقترح:
{json.dumps(segs_slim, ensure_ascii=False, indent=2)}

المهمة على مرحلتين:
1. **مراجعة البلاغة والسلاسة**: إذا وجدت أي عبارة ركيكة أو غريبة أو متكلّفة (مثل سنابك، ميالا، هلالا، الكبرى)، فأصلح الصياغة في `clean_text` لتصبح عذبة واضحة صادقة دون إطالة.
2. **التأنيث الإلزامي والتشكيل الكامل (100% مع الشدّة `ّ`)** لـ `clean_text` داخل `diacritized_text`:
   - 🔴 **قاعدة التأنيث المطلقة**: المخاطَبة هنا أنثى (ملك / مريم / ملاك)؛ لذلك فإن كل ضمير يعود عليها يجب أن يكون **مؤنثاً مكسوراً (`كِ` و`تِ` و`أَنْتِ`)** مثل: `عَهْدَكِ، بَقِيتِ، اسْمِكِ، قَلْبِكِ، لَكِ، فِيكِ، أَنْتِ`. يُمنع نهائياً فتحها للمذكر (`عَهْدَكَ / بَقِيتَ / أَنْتَ`).
   - 🟢 **استثناء الدعاء لله**: عند مخاطبة الله عز وجل في الدعاء (`اللهم أسألكَ، بحفظكَ، برحمتكَ`) تبقى كاف خطاب الخالق مفتوحةً (`كَ`) احتراماً وصحةً.
   - الممنوع من الصرف للأسماء الثلاثة:
     * «مَلَك»: بفتح الميم واللام بلا تنوين (قَلْبَ مَلَكَ)، وعند الوقف (مَلَكْ).
     * «مَرْيَم»: بلا تنوين (لِـمَرْيَمَ)، وعند الوقف (مَرْيَمْ).
     * «مَلَاك»: بلا تنوين (مَلَاكَ)، وعند الوقف (مَلَاكْ).

أعد JSON فقط:
{{
  "segments_audit": [
    {{
      "index": 0,
      "rewritten": false,
      "clean_text": "النص النظيف الفصيح السلس...",
      "diacritized_text": "النص النظيف نفسه مضبوطاً بالتشكيل الكامل والتأنيث الدقيق 100%..."
    }}
  ]
}}"""


def _fallback_from_bank(theme: dict, speaker_mode: str) -> dict:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from whispers_bank import WHISPERS_BANK
        key = theme["id"] if theme["id"] in WHISPERS_BANK else random.choice(list(WHISPERS_BANK.keys()))
        pool = WHISPERS_BANK.get(key) or [item for lst in WHISPERS_BANK.values() for item in lst]
        chosen = random.choice(pool)
    except Exception:
        chosen = {
            "display_text": "اَللَّهُمَّ إِنِّي أَسْتَوْدِعُكَ قَلْبَ ملك، وَأَسْأَلُكَ سَكِينَةً لِرُوحِ مَرْيَمَ، وَفَرَحًا لَا يُفَارِقُ أَيَّامَ مَلَاكَ.",
            "tts_text": "اَللَّهُمَّ إِنِّي أَسْتَوْدِعُكَ قَلْبَ مَلَكْ، وَأَسْأَلُكَ سَكِينَةً لِرُوحِ مَرْيَمْ، وَفَرَحًا لَا يُفَارِقُ أَيَّامَ مَلَاكْ.",
        }
    spk = "she" if speaker_mode == "she" else "he"
    return {
        "title": "أَمَانُ الرُّوحِ",
        "segments": [{
            "speaker": spk,
            "pause_after_ms": 420,
            "raw_text": chosen.get("display_text", ""),
            "diacritized_override": chosen.get("tts_text") or chosen.get("display_text", ""),
            "scene_prompt_en": chosen.get("scene_prompt_en", "warm twilight rose garden with soft candlelight"),
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
    ep_counter = history.get("episode_counter", 0)
    theme = _select_theme(history, theme_override)
    literary_form = LITERARY_FORMS[ep_counter % len(LITERARY_FORMS)]

    speaker_mode = speaker_override or theme["default_speaker_mode"]
    if not speaker_override and theme["id"] in ("prayer_niqab", "modest_love"):
        if ep_counter % 4 == 3:
            speaker_mode = "she"
        elif ep_counter % 3 == 2:
            speaker_mode = "duet"

    recent = history.get("recent", [])
    recent_titles = [r.get("title", "") for r in recent]
    recent_openings = [r.get("opening", "") for r in recent]

    draft = None
    aud_map = {}

    try:
        pool = GeminiKeyPool()
        print(f"🌙 [1/3] تأليف همسة #{ep_counter + 1} | المحور: {theme['label']} | الصوت: {speaker_mode}", flush=True)
        draft = call_gemini_json(
            pool,
            _build_pass1_prompt(theme, literary_form, speaker_mode, recent_titles, recent_openings),
            temp=0.78,
        )
        raw_segs = draft.get("segments") or []
        if not raw_segs:
            raise RuntimeError("مسودة فارغة")

        print("📚 [2/3] مراجعة السلاسة البلاغية والتأنيث والتشكيل الكامل...", flush=True)
        try:
            audit = call_gemini_json(pool, _build_pass2_grammar_prompt(draft), temp=0.15)
            aud_map = {int(a.get("index", i)): a for i, a in enumerate(audit.get("segments_audit") or [])}
        except Exception as e:
            print(f"   ⚠️ تعذر التدقيق الثاني ({str(e)[:60]})، متابعة التصحيح الفردي...", flush=True)
    except Exception as e:
        print(f"⚠️ تعذر التوليد عبر Gemini ({str(e)[:90]}) — استخدام البنك المنقح.", flush=True)
        draft = _fallback_from_bank(theme, speaker_mode)

    raw_segs = draft.get("segments") or []
    print("🔒 [3/3] التحقق من التأنيث وكثافة التشكيل + قفل نطق الأسماء...", flush=True)
    final_segments = []
    all_names_found = set()

    for idx, seg in enumerate(raw_segs):
        raw_text = re.sub(r"\s+", " ", (seg.get("raw_text") or "").strip())
        if not raw_text:
            continue

        spk = str(seg.get("speaker", "he")).strip().lower()
        if spk not in ("he", "she"):
            spk = "he"

        aud = aud_map.get(idx, {})
        base_clean = re.sub(r"\s+", " ", (aud.get("clean_text") or raw_text).strip()) or raw_text
        diac_cand = re.sub(r"\s+", " ", (aud.get("diacritized_text") or seg.get("diacritized_override") or base_clean).strip())
        merged_diac = _merge_diacritics_safely(base_clean, diac_cand)
        ratio = _diacritic_ratio(merged_diac)

        if ratio < 0.42:
            print(f"   ⚠️ كثافة التشكيل منخفضة بالمقطع {idx} ({ratio:.2f})، إعادة التشكيل...", flush=True)
            try:
                pool_retry = GeminiKeyPool()
                r2 = call_gemini_json(pool_retry, _build_pass2_grammar_prompt({"segments": [{"speaker": spk, "raw_text": base_clean}]}), temp=0.10)
                a2 = (r2.get("segments_audit") or [{}])[0]
                c2_base = (a2.get("clean_text") or base_clean).strip()
                c2_diac = (a2.get("diacritized_text") or "").strip()
                if c2_diac:
                    merged_diac = _merge_diacritics_safely(c2_base, c2_diac)
                    ratio = _diacritic_ratio(merged_diac)
            except Exception:
                pass

        if ratio < 0.30:
            print(f"   🚨 تعذر تشكيل المقطع {idx}، استبداله بمقطع مشكول من البنك.", flush=True)
            fb = _fallback_from_bank(theme, spk)["segments"][0]
            merged_diac = fb["diacritized_override"]
            ratio = _diacritic_ratio(merged_diac)

        merged_diac = _enforce_feminine_address(merged_diac, speaker=spk)
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

        scene_en = re.sub(r"[^\x20-\x7E]", " ", str(seg.get("scene_prompt_en", ""))).strip()
        if not scene_en:
            scene_en = "warm golden candlelight by rainy window with red roses"

        final_segments.append({
            "index": idx,
            "speaker": spk,
            "style": "warm and unhurried, gentle natural reading",
            "tag_before": "",
            "pause_after_ms": int(seg.get("pause_after_ms") or 420),
            "raw_text": base_clean,
            "display_text": display_text,
            "tts_text": tts_text,
            "scene_prompt_en": scene_en,
            "diacritic_ratio": round(ratio, 3),
            "featured_names": seg_names,
            "is_climax": bool(seg.get("is_climax", idx == len(raw_segs) - 1)),
            "grammar_audit": {
                "rewritten": bool(aud.get("rewritten", False)),
            },
        })
        print(f"   ✓ مقطع {idx} ({spk}) | تشكيل: {ratio:.2f} | العرض: {display_text}", flush=True)

    if not final_segments:
        raise RuntimeError("❌ لا توجد مقاطع صالحة بعد التدقيق.")

    ep_num = ep_counter + 1
    today = datetime.datetime.utcnow().strftime("%Y%m%d")
    ep_id = f"whisper_{today}_ep{ep_num:04d}"

    payload = {
        "episode_id": ep_id,
        "episode_number": ep_num,
        "created_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "title": draft.get("title", "أمان الروح"),
        "theme_id": theme["id"],
        "theme_label": theme["label"],
        "literary_form": literary_form,
        "scene_category": theme["scene_category"],
        "music_mood": theme["music_mood"],
        "ambient_mood": theme["ambient_mood"],
        "speaker_mode": speaker_mode,
        "malak_lam_mode": MALAK_LAM_MODE,
        "names_featured": sorted(all_names_found),
        "segments": final_segments,
    }

    GEN_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ep_path = GEN_DIR / f"{ep_id}.json"
    ep_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rel_path = ep_path.relative_to(REPO_ROOT).as_posix()
    LATEST_PTR_FILE.write_text(rel_path, encoding="utf-8")

    history["episode_counter"] = ep_num
    recent.append({"id": ep_id, "title": payload["title"], "theme": theme["id"], "opening": final_segments[0]["raw_text"][:65]})
    history["recent"] = recent[-30:]
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
