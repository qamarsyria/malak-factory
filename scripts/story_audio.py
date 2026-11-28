"""
story_audio.py — المرحلة 4: بناء الصوت + الترجمة النصية المتزامنة (مشترك لليوميات والقصص)
- السرد يُولَّد مرة واحدة ويُشارك بين كل النسخ لتوفير حصة Gemini.
- يضبط التشكيل الصوتي ويفرض نطق اسم القناة (مَلَّاكُ المَعْرِفَةِ).
- يولّد ترجمة إنكليزية نصية دقيقة لكل شريحة غير قرآنية (text_en) لتظهر على الشاشة فقط دون نطق صوتي.
- يستثني نصوص القرآن الكريم (type == "quran") من الترجمة تماماً.
"""

import os
import sys
import json
import gzip
import subprocess
import tempfile
import shutil
import re
import time
import hashlib
import asyncio
import random
import argparse
from difflib import SequenceMatcher
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from utilities import cleanup_dir, get_breaker
from model_discovery import discover_and_cache
from gemini_tts_pool import get_pool, gemini_tts_with_pool

TTS_ENGINE = os.environ.get("TTS_ENGINE", "1").strip()
ENABLE_EN_SUBTITLES = os.environ.get("ENABLE_EN_SUBTITLES", "1") == "1"

VOICE_BY_TYPE = {
    "intro":     "Puck",        # نبرة حيوية واضحة لطرح المدخل والمقدمة
    "teaser":    "Puck",
    "fiqh":      "Orus",        # نبرة وقورة حازمة للحكم الشرعي
    "hadith":    "Orus",        # نبرة وقورة لنص الحديث النبوي
    "narration": "Charon",      # نبرة الشارح/الراوي الرصينة العميقة
    "adhkar":    "Algieba",     # نبرة ناعمة وهادئة للأذكار
    "dua":       "Algieba",     # نبرة خاشعة دافئة للأدعية
    "summary":   "Rasalgethi",  # نبرة متزنة للمصدر والخلاصة
    "outro":     "Rasalgethi",
    "cta":       "Puck",
}
YT_QURAN_VOICE = "Algieba"      # نبرة هادئة رخيمة لآية اليوتيوب مع صدى المسجد

EDGE_VOICE_BY_GEMINI = {
    "Puck":       "ar-EG-ShakirNeural",
    "Fenrir":     "ar-EG-ShakirNeural",
    "Orus":       "ar-SA-HamedNeural",
    "Charon":     "ar-SA-HamedNeural",
    "Algieba":    "ar-SA-HamedNeural",
    "Rasalgethi": "ar-SA-HamedNeural",
}


def _edge_phonetic_fix(text: str) -> str:
    replacements = {
        "الرحمن": "الرَّحْمَان",
        "الرحمٰن": "الرَّحْمَان",
        "هذا": "هَاذَا",
        "هذه": "هَاذِهِ",
        "هؤلاء": "هَاؤُلَاءِ",
        "ذلك": "ذَالِكَ",
        "أولئك": "أُولَائِكَ",
        "لكن": "لَاكِنْ",
        "إله": "إِلَاه",
        "داود": "دَاوُود",
        "مائة": "مِئَة",
        "مئة": "مِئَة",
    }
    for src, dst in replacements.items():
        text = re.sub(rf"\b{src}\b", dst, text)
    return _enforce_mallak(text)


if TTS_ENGINE == "1":
    print("🔊 [TTS] المحرك: Gemini (متعدد النبرات)", flush=True)

    def generate_audio(text: str, out_path: str, voice: str = "Charon") -> bool:
        try:
            gemini_tts_with_pool(text, out_path, voice=voice)
            return True
        except Exception as e:
            print(f"   ⚠️ Gemini TTS فشل ({voice}): {str(e)[:120]}", flush=True)
            return False
else:
    print("🔊 [TTS] المحرك: Edge", flush=True)
    import edge_tts

    def generate_audio(text: str, out_path: str, voice: str = "Charon") -> bool:
        edge_v = EDGE_VOICE_BY_GEMINI.get(voice, "ar-SA-HamedNeural")
        fixed_text = _edge_phonetic_fix(text)
        try:
            async def _run():
                comm = edge_tts.Communicate(fixed_text, edge_v, rate="-7%")
                await comm.save(out_path)
            asyncio.run(_run())
            return Path(out_path).exists() and Path(out_path).stat().st_size > 1000
        except Exception as e:
            print(f"   ⚠️ Edge فشل: {str(e)[:120]}", flush=True)
            return False


RECITER_YT = {
    "id": "Husary_128kbps",
    "name": "الشيخ محمود خليل الحصري",
}

RECITER_LIST_SOCIAL = [
    {"id": "Alafasy_128kbps",                 "name": "مشاري راشد العفاسي"},
    {"id": "Abdurrahmaan_As-Sudais_192kbps",  "name": "عبد الرحمن السديس"},
    {"id": "Ghamadi_40kbps",                  "name": "سعد الغامدي"},
    {"id": "Yasser_Ad-Dussary_128kbps",       "name": "ياسر الدوسري"},
    {"id": "Nasser_Alqatami_128kbps",         "name": "ناصر القطامي"},
]

QURAN_BASE_URL = "https://everyayah.com/data"
AYAH_LIBRARY = _REPO_ROOT / "data" / "libraries" / "ayah-library.json.gz"
TARGET_SR = "24000"
SILENCE_DUR = 0.20
RECITER_VOLUME_MULT = 1.30
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"

QURAN_SIMPLE_CACHE: dict[str, str] = {}
QURAN_LOCAL_CACHE: dict[str, str] = {}

_ARABIC_UNITS    = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_ARABIC_TEENS    = ["عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
                    "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
_ARABIC_TENS     = ["", "عشرة", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_ARABIC_HUNDREDS = ["", "مئة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]


def _num_to_arabic(n: int) -> str:
    if n == 0:
        return "صفر"
    if n < 10:
        return _ARABIC_UNITS[n]
    if n < 20:
        return _ARABIC_TEENS[n - 10]
    if n < 100:
        t, u = divmod(n, 10)
        return _ARABIC_TENS[t] if u == 0 else f"{_ARABIC_UNITS[u]} و{_ARABIC_TENS[t]}"
    if n < 1000:
        h, r = divmod(n, 100)
        p = _ARABIC_HUNDREDS[h]
        return p if r == 0 else f"{p} و{_num_to_arabic(r)}"
    if n < 1_000_000:
        th, r = divmod(n, 1000)
        if th == 1:
            p = "ألف"
        elif th == 2:
            p = "ألفان"
        elif 3 <= th <= 10:
            p = f"{_ARABIC_UNITS[th]} آلاف"
        else:
            p = f"{_num_to_arabic(th)} ألف"
        return p if r == 0 else f"{p} و{_num_to_arabic(r)}"
    return str(n)


def arabize_numbers(text: str) -> str:
    return re.sub(r"\b(\d{1,6})\b", lambda m: _num_to_arabic(int(m.group(0))), text)


def _enforce_mallak(text: str) -> str:
    """يضبط اسم القناة دائماً بفتح الميم وتشديد اللام مع الفتح: مَلَّاكُ المَعْرِفَةِ."""
    if not text:
        return ""
    return re.sub(
        r"(?<![\u0621-\u064A])م[\u064B-\u065F]*ل[\u064B-\u065F]*ا[\u064B-\u065F]*ك([\u064B-\u065F]*)"
        r"\s+ال[\u064B-\u065F]*م[\u064B-\u065F]*ع[\u064B-\u065F]*ر[\u064B-\u065F]*ف[\u064B-\u065F]*ة([\u064B-\u065F]*)",
        lambda m: f"مَلَّاك{m.group(1) or 'ُ'} المَعْرِفَة{m.group(2) or 'ِ'}",
        text,
    )


def _plain_len(text: str) -> int:
    return len(re.sub(r"[\u064B-\u065F\u0670]", "", text or ""))


def _word_skeleton(word: str) -> str:
    w = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", word or "")
    w = re.sub(r"[^\u0621-\u064A]", "", w)
    w = w.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
    w = w.replace("ى", "ي").replace("ة", "ه")
    return w


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
                m_trail = re.search(r"([،؛؟!.:]+)$", orig_tokens[oi])
                d_clean = re.sub(r"[،؛؟!.:]+$", "", diac_tokens[di])
                merged.append(d_clean + (m_trail.group(1) if m_trail else ""))
        elif tag == "replace":
            if (i2 - i1) == (j2 - j1):
                for oi, di in zip(range(i1, i2), range(j1, j2)):
                    sim = SequenceMatcher(None, orig_skels[oi], diac_skels[di]).ratio()
                    if sim >= 0.72:
                        m_trail = re.search(r"([،؛؟!.:]+)$", orig_tokens[oi])
                        d_clean = re.sub(r"[،؛؟!.:]+$", "", diac_tokens[di])
                        merged.append(d_clean + (m_trail.group(1) if m_trail else ""))
                    else:
                        merged.append(orig_tokens[oi])
            else:
                merged.extend(orig_tokens[i1:i2])
        elif tag == "delete":
            merged.extend(orig_tokens[i1:i2])

    return " ".join(merged) if merged else original


def _diacritize_models() -> list[str]:
    discovered = discover_and_cache(force=False)
    heavy = [m for m in discovered if "lite" not in m.lower()]
    lite = [m for m in discovered if "lite" in m.lower()]
    return (heavy + lite)[:10]


def _thinking_config(model: str, light: bool = False) -> dict | None:
    m = model.lower()
    if any(x in m for x in ["gemini-3", "gemini-4"]):
        return {"thinkingLevel": "low" if light else "medium"}
    if "gemini-2.5" in m:
        return {"thinkingBudget": 256 if light else 1024}
    return None


def _call_gemini_json(prompt: str, timeout: int = 75, light_thinking: bool = False) -> dict | None:
    try:
        pool = get_pool()
        breaker = get_breaker()
        for model in _diacritize_models():
            if breaker.is_open(f"model:{model}"):
                continue

            use_thinking = True
            use_temperature = True
            valid_tries = 0
            dead_skips = 0
            while valid_tries < 2:
                try:
                    key = pool.get_current()
                    if not key:
                        return None
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                    gen_cfg: dict = {
                        "maxOutputTokens": 6144,
                        "responseMimeType": "application/json",
                    }
                    if use_temperature:
                        gen_cfg["temperature"] = 0.08
                    if use_thinking:
                        tc = _thinking_config(model, light=light_thinking)
                        if tc:
                            gen_cfg["thinkingConfig"] = tc

                    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
                    r = requests.post(url, json=body, timeout=(15, timeout))

                    if r.status_code == 200:
                        parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        text_parts = [p.get("text", "") for p in parts if not p.get("thought") and p.get("text")]
                        txt = "\n".join(text_parts) if text_parts else (parts[-1].get("text", "") if parts else "")
                        s, e = txt.find("{"), txt.rfind("}")
                        if s == -1 or e <= s:
                            break
                        parsed = json.loads(txt[s:e + 1])
                        if hasattr(pool, "_advance"):
                            pool._advance()
                        breaker.record_success(f"model:{model}")
                        return parsed
                    elif r.status_code == 401 or (r.status_code == 403 and "api key" in r.text.lower()):
                        pool.mark_dead(f"HTTP {r.status_code}")
                        dead_skips += 1
                        if dead_skips >= 20:
                            break
                        continue
                    elif r.status_code == 400:
                        low_err = r.text.lower()
                        if use_thinking and "thinking" in low_err:
                            use_thinking = False
                            continue
                        if use_temperature and ("temperature" in low_err or "sampling" in low_err):
                            use_temperature = False
                            continue
                        breaker.record_failure(f"model:{model}")
                        break
                    elif r.status_code == 429:
                        pool.mark_failed(75)
                        valid_tries += 1
                        time.sleep(2)
                        continue
                    elif r.status_code in (403, 404):
                        breaker.record_failure(f"model:{model}")
                        break
                    else:
                        valid_tries += 1
                        if hasattr(pool, "_advance"):
                            pool._advance()
                        time.sleep(3)
                        continue
                except requests.exceptions.Timeout:
                    if hasattr(pool, "_advance"):
                        pool._advance()
                    break
                except Exception:
                    break
    except Exception:
        pass
    return None


PASS1_PROMPT = """أنت أستاذ لغوي متخصص في الصرف والنحو العربي والتشكيل الصوتي الدقيق للقراءة الجهرية (TTS).

المهمة: اضبط النص التالي بالتشكيل الكامل والدقيق (100%) بعد فهم المعنى الكامل للجمل وإعرابها.

قواعد الصرف والنحو والنطق الإلزامية:
1. افهم معنى كل جملة أولاً لتفرق بدقة بين المبني للمعلوم والمجهول والمتشابهات السياقية.
2. الشدّة (`ّ`) ضرورية جداً للنطق السليم: ضع الشدّة على كل حرف مشدد وعلى الحرف الشمسي بعد (الـ) الشمسية.
3. اسم القناة «مَلَّاكُ المَعْرِفَةِ» يُضبط دائماً بفتح الميم وتشديد اللام مع الفتح: مَلَّاكُ المَعْرِفَةِ.
4. اضبط أواخر الكلمات إعرابياً حسب موقعها، وضع سكوناً (`ْ`) على الحرف الساكن الأخير قبل الفاصلة والنقطة.
5. حافظ على جميع الكلمات والمسافات وعلامات الترقيم كما هي تماماً دون حذف أو زيادة كلمة.

النص:
{text}

أعد JSON فقط:
{{
  "grammar_analysis": "خلاصة سريعة للمواضع الحساسة",
  "diacritized": "النص مضبوطاً بالشكل الكامل..."
}}"""


PASS2_VERIFY_PROMPT = """أنت مدقق صرفي ونحوي صارم تراجع نصاً مشكولاً قبل إرساله لقارئ صوتي آلي.

النص الأصلي:
{original}

التشكيل المقترح:
{draft}

راجع التشكيل المقترح كلمةً بكلمة وصحح أي خطأ في الشدّة أو الإعراب أو الصرف (وتأكد أن اسم القناة مضبوط دائماً هكذا: مَلَّاكُ المَعْرِفَةِ)، وتأكد من سلامة الحروف دون تغيير أو إضافة كلمات.

أعد JSON فقط:
{{
  "final_text": "النص النهائي بعد تصحيح كل الحركات والشدات..."
}}"""


def smart_diacritize(text: str, cache_file: Path | None = None) -> str:
    if not text or not re.search(r"[\u0621-\u064A]", text):
        return text

    if cache_file and cache_file.exists():
        cached = cache_file.read_text(encoding="utf-8").strip()
        if cached:
            return _enforce_mallak(cached)

    try:
        p1 = _call_gemini_json(PASS1_PROMPT.format(text=text))
        draft = (p1 or {}).get("diacritized", "").strip()
        if not draft:
            return _enforce_mallak(text)

        p2 = _call_gemini_json(PASS2_VERIFY_PROMPT.format(original=text, draft=draft))
        verified = (p2 or {}).get("final_text", "").strip() or draft

        final_text = _enforce_mallak(_merge_diacritics_safely(text, verified))

        diac_count = len(re.findall(r"[\u064B-\u0652]", final_text))
        letter_count = max(1, len(re.findall(r"[\u0621-\u064A]", final_text)))
        ratio = diac_count / letter_count
        print(f"   🔤 تشكيل صرفي/نحوي ({diac_count}/{letter_count} = {ratio:.2f} حركة/حرف)", flush=True)

        if cache_file and ratio >= 0.35:
            cache_file.write_text(final_text, encoding="utf-8")
        return final_text
    except Exception as e:
        print(f"   ⚠️ تعذر التشكيل: {str(e)[:80]}", flush=True)
        return _enforce_mallak(text)


TRANSLATE_PROMPT = """You are an authoritative Islamic translator specializing in Quranic/Hadith/Fiqh English translation and concise on-screen video subtitles.

Translate the numbered Arabic subtitle chunks below into **accurate, natural, crystal-clear English** for on-screen captions.

Full Arabic context (for pronoun & theological accuracy ONLY — do NOT merge chunks):
{context}

Segment type: {stype}

STRICT RULES:
1. Return EXACTLY {n_chunks} translations in the `"translations"` array, matching chunk `0` to `{last_idx}` one-by-one in exact order.
2. Islamic precision:
   - Always use **"Allah"** (never "God").
   - Translate "صلى الله عليه وسلم" concisely as **"peace be upon him"**.
   - Translate "رضي الله عنه / عنها" as **"may Allah be pleased with him/her"** and "رحمه الله" as **"may Allah have mercy on him"**.
   - Always render the channel name "مَلَّاكُ المَعْرِفَةِ" / "ملاك المعرفة" as **"Mallak Al-Ma'rifah"** (never "Angel of Knowledge").
   - Keep Hadith/Fiqh rulings and Duas 100% faithful to the exact Arabic meaning with zero distortion or omission.
3. Subtitle readability: keep each chunk's English clear, natural, and concise (plain text, NO markdown, NO brackets unless brief clarification, NO Arabic script).

Chunks to translate:
{chunks_block}

Return ONLY valid JSON:
{{
  "translations": ["English for chunk 0", "..."]
}}"""


def _clean_en_subtitle(s: str) -> str:
    s = re.sub(r"[*#@_`]", "", (s or "")).strip()
    s = re.sub(r"[\u0600-\u06FF]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def translate_chunks_batch(
    chunks: list[str],
    stype: str,
    full_context: str,
    cache_file: Path | None = None,
) -> list[str]:
    if not ENABLE_EN_SUBTITLES or not chunks or stype == "quran":
        return [""] * len(chunks)

    if cache_file and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(cached, list) and len(cached) == len(chunks) and all(str(x).strip() for x in cached):
                return [str(x).strip() for x in cached]
        except Exception:
            pass

    plain_chunks = [re.sub(r"[\u064B-\u065F\u0670]", "", c).strip() for c in chunks]
    plain_ctx = re.sub(r"[\u064B-\u065F\u0670]", "", full_context).strip()
    chunks_block = "\n".join(f"[{i}] {ch}" for i, ch in enumerate(plain_chunks))

    prompt = TRANSLATE_PROMPT.format(
        context=plain_ctx,
        stype=stype,
        n_chunks=len(plain_chunks),
        last_idx=len(plain_chunks) - 1,
        chunks_block=chunks_block,
    )

    translations: list[str] = []
    for _ in range(2):
        res = _call_gemini_json(prompt, timeout=65, light_thinking=True)
        arr = (res or {}).get("translations")
        if isinstance(arr, list) and len(arr) == len(plain_chunks):
            cleaned = [_clean_en_subtitle(str(x)) for x in arr]
            if all(cleaned):
                translations = cleaned
                break

    # احتياط: إذا اختلف العدد أو نقصت شريحة، ترجم الشرائح الفارغة إفرادياً
    if len(translations) != len(plain_chunks):
        translations = [""] * len(plain_chunks)
        for i, ch in enumerate(plain_chunks):
            single_prompt = TRANSLATE_PROMPT.format(
                context=plain_ctx,
                stype=stype,
                n_chunks=1,
                last_idx=0,
                chunks_block=f"[0] {ch}",
            )
            res1 = _call_gemini_json(single_prompt, timeout=45, light_thinking=True)
            arr1 = (res1 or {}).get("translations")
            if isinstance(arr1, list) and arr1:
                translations[i] = _clean_en_subtitle(str(arr1[0]))

    if cache_file and any(translations):
        try:
            cache_file.write_text(json.dumps(translations, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    return translations


def translate_title_en(title: str, cache_file: Path | None = None) -> str:
    if not ENABLE_EN_SUBTITLES or not title:
        return ""
    if cache_file and cache_file.exists():
        cached = cache_file.read_text(encoding="utf-8").strip()
        if cached:
            return cached
    res = translate_chunks_batch([title], "title", title, cache_file=None)
    title_en = res[0] if res else ""
    if title_en and cache_file:
        try:
            cache_file.write_text(title_en, encoding="utf-8")
        except Exception:
            pass
    return title_en


def sanitize_for_tts(t: str) -> str:
    if not t:
        return ""
    t = t.replace("ﷺ", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    t = t.replace("صلى الله عليه وسلم", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    t = arabize_numbers(t)
    t = re.sub(r"\[.*?\]|\(.*?\)|﴿.*?﴾", "", t, flags=re.DOTALL)
    t = re.sub(r"[^\u0621-\u064A\u064B-\u065F\u0670\s،؛؟!.:]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return _enforce_mallak(t)


def _load_local_uthmani_cache():
    if QURAN_LOCAL_CACHE or not AYAH_LIBRARY.exists():
        return
    try:
        with gzip.open(AYAH_LIBRARY, "rt", encoding="utf-8") as f:
            for a in json.load(f):
                s = int(a.get("surah") or a.get("surah_number") or 0)
                v = int(a.get("ayah") or a.get("ayah_number") or 0)
                txt = (a.get("text") or "").strip()
                if s and v and txt:
                    QURAN_LOCAL_CACHE[f"{s}:{v}"] = txt
    except Exception:
        pass


def get_simple_ayah_text(surah: int, ayah: int) -> str:
    key = f"{surah}:{ayah}"
    if key in QURAN_SIMPLE_CACHE:
        return QURAN_SIMPLE_CACHE[key]
    try:
        r = requests.get(f"https://api.alquran.cloud/v1/surah/{surah}/quran-simple", timeout=15)
        if r.status_code == 200:
            for item in r.json().get("data", {}).get("ayahs", []):
                a_num = int(item.get("numberInSurah", 0))
                if a_num:
                    QURAN_SIMPLE_CACHE[f"{surah}:{a_num}"] = (item.get("text") or "").strip()
            if key in QURAN_SIMPLE_CACHE:
                return QURAN_SIMPLE_CACHE[key]
    except Exception as e:
        print(f"   ⚠️ تعذر جلب النص الإملائي للآية {key}: {e}", flush=True)

    _load_local_uthmani_cache()
    return QURAN_LOCAL_CACHE.get(key, "")


def prepare_quran_for_gemini_tts(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"[\u06D6-\u06ED۞۩]", "", text)
    t = t.replace("ٱ", "ا").replace("\u06E1", "\u0652")
    t = t.replace("وٰ", "ا").replace("ىٰ", "ى")
    t = t.replace("ٰ", "ا")

    muqattaat = {
        "الم": "أَلِفْ لَامْ مِيمْ",
        "المص": "أَلِفْ لَامْ مِيمْ صَادْ",
        "الر": "أَلِفْ لَامْ رَا",
        "المر": "أَلِفْ لَامْ مِيمْ رَا",
        "كهيعص": "كَافْ هَا يَا عَيْنْ صَادْ",
        "طه": "طَا هَا",
        "طسم": "طَا سِينْ مِيمْ",
        "طس": "طَا سِينْ",
        "يس": "يَا سِينْ",
        "ص": "صَادْ",
        "حم": "حَا مِيمْ",
        "ق": "قَافْ",
        "ن": "نُونْ",
    }
    out_tokens = []
    for tok in t.split():
        skel = re.sub(r"[\u064B-\u065F\u0670]", "", tok)
        if skel in muqattaat:
            out_tokens.append(muqattaat[skel])
        else:
            out_tokens.append(tok)
    t = " ".join(out_tokens)

    t = re.sub(r"[^\u0621-\u064A\u064B-\u065F\s،؛؟!.:]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def apply_mosque_reverb(input_wav: Path, output_wav: Path) -> bool:
    try:
        af = (
            "apad=pad_dur=0.65,"
            "highpass=f=85,lowpass=f=7800,"
            "equalizer=f=240:t=q:w=1.2:g=2.5,"
            "aecho=0.86:0.84:55|115|185:0.24|0.13|0.06,"
            "loudnorm=I=-15:TP=-1.5:LRA=10,"
            "alimiter=limit=0.95,"
            "afade=t=in:st=0:d=0.02"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_wav), "-af", af,
             "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(output_wav)],
            check=True, capture_output=True, timeout=60,
        )
        return output_wav.exists() and output_wav.stat().st_size > 1000
    except Exception:
        return False


def download_human_ayah(surah: int, ayah: int, reciter_id: str, out_path: Path) -> bool:
    if out_path.exists() and out_path.stat().st_size > 1000:
        return True
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE_URL}/{reciter_id}/{filename}"
    try:
        r = requests.get(url, timeout=25)
        if r.status_code == 200 and len(r.content) > 1000:
            out_path.write_bytes(r.content)
            return True
    except Exception as e:
        print(f"   ⚠️ فشل تحميل {filename} ({reciter_id}): {str(e)[:80]}", flush=True)
    return False


def get_dur(p) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True,
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def normalize_wav(input_path: str, output_wav: str, gentle_loudnorm: bool = False, volume_mult: float = 1.0) -> bool:
    try:
        filters = [
            "silenceremove=start_periods=1:start_duration=0:start_threshold=-48dB",
            "areverse",
            "silenceremove=start_periods=1:start_duration=0:start_threshold=-48dB",
            "areverse",
        ]
        if gentle_loudnorm:
            filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if volume_mult != 1.0:
            filters.append(f"volume={volume_mult:.2f},alimiter=limit=0.95")
        filters.append("afade=t=in:st=0:d=0.015")

        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-af", ",".join(filters),
             "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", output_wav],
            check=True, capture_output=True, timeout=60,
        )
        dur = get_dur(output_wav)
        if dur > 0.2:
            tmp = str(output_wav) + ".tmp.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-i", output_wav,
                 "-af", f"afade=t=out:st={max(0.0, dur - 0.03):.3f}:d=0.03",
                 "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", tmp],
                check=True, capture_output=True, timeout=60,
            )
            shutil.move(tmp, output_wav)
        return Path(output_wav).exists() and Path(output_wav).stat().st_size > 1000
    except Exception:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", output_wav],
            check=True, capture_output=True, timeout=60,
        )
        return True


def split_text(text: str, max_plain_chars: int = 165) -> list[str]:
    """يقسّم النص بناءً على عدد الحروف الصافية (بدون التشكيل) لتناسق البطاقات والترجمة."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!؟])\s+", text) if s.strip()]

    # تفتيت الجمل الطويلة جداً على الفواصل أولاً حتى لا تفيض عن الدفتر
    granular: list[str] = []
    for sent in sentences:
        if _plain_len(sent) <= max_plain_chars:
            granular.append(sent)
        else:
            clauses = [c.strip() for c in re.split(r"(?<=[،؛:])\s+", sent) if c.strip()]
            for cl in clauses:
                if _plain_len(cl) <= max_plain_chars:
                    granular.append(cl)
                else:
                    words = cl.split()
                    mid = len(words) // 2
                    if mid > 0:
                        granular.append(" ".join(words[:mid]))
                        granular.append(" ".join(words[mid:]))
                    else:
                        granular.append(cl)

    chunks, current = [], ""
    for s in granular:
        trial = f"{current} {s}".strip() if current else s
        if _plain_len(trial) <= max_plain_chars:
            current = trial
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)
    return chunks


def _export_final_mp3(wav_list: list[Path], out_mp3: str, work: Path):
    concat_file = work / f"concat_{Path(out_mp3).stem}.txt"
    concat_file.write_text("\n".join(f"file '{p.resolve()}'" for p in wav_list), encoding="utf-8")
    merged_wav = work / f"merged_{Path(out_mp3).stem}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(merged_wav)],
        check=True, capture_output=True, timeout=300,
    )
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(merged_wav), "-c:a", "libmp3lame", "-b:a", "96k", str(out_mp3)],
        check=True, capture_output=True, timeout=300,
    )


def _build_gemini_quran_chunks(
    idx: int, s: int, ayahs: list[int], sname: str, display: str,
    ep_cache: Path, work: Path, silence_path: Path, wav_out: list[Path],
) -> dict | None:
    print(f"   🕌 آيات تلاوة Gemini {s}:{ayahs} → ({YT_QURAN_VOICE}) + صدى مسجد هادئ", flush=True)
    q_chunks = []
    for a in ayahs:
        simple_raw = get_simple_ayah_text(s, a)
        tts_ayah = prepare_quran_for_gemini_tts(simple_raw)
        if not tts_ayah:
            raise RuntimeError(f"❌ تعذر تحضير نص الآية {s}:{a} لتلاوة Gemini")

        a_hash = hashlib.md5(f"{YT_QURAN_VOICE}:{tts_ayah}".encode("utf-8")).hexdigest()[:8]
        reverb_wav = ep_cache / f"yt_gemini_quran_{s}_{a}_{a_hash}_mosque.wav"

        if not reverb_wav.exists() or reverb_wav.stat().st_size < 1000:
            temp_mp3 = work / f"yt_q_{s}_{a}.mp3"
            dry_wav = work / f"yt_q_{s}_{a}_dry.wav"
            if not generate_audio(tts_ayah, str(temp_mp3), voice=YT_QURAN_VOICE):
                raise RuntimeError(f"❌ فشل توليد قراءة Gemini للآية {s}:{a}")
            normalize_wav(str(temp_mp3), str(dry_wav), gentle_loudnorm=True, volume_mult=1.0)
            if not apply_mosque_reverb(dry_wav, reverb_wav):
                shutil.copyfile(dry_wav, reverb_wav)
            time.sleep(0.5)

        wav_out.extend([reverb_wav, silence_path])
        q_chunks.append({
            "surah": s,
            "ayah": a,
            "surah_name": sname,
            "reciter": "",
            "text": f"{display} - آية {a}",
            "text_en": "",  # القرآن الكريم لا يُترجم أبداً
            "duration": round(get_dur(reverb_wav) + SILENCE_DUR, 3),
        })
    if not q_chunks:
        return None
    return {
        "index": idx,
        "type": "quran",
        "surah": s,
        "surah_name": sname,
        "ayah_numbers": ayahs,
        "display": display,
        "reciter": "",
        "chunks": q_chunks,
    }


def _build_husary_quran_chunks(
    idx: int, s: int, ayahs: list[int], sname: str, display: str,
    ep_cache: Path, silence_path: Path, wav_out: list[Path],
) -> dict | None:
    yt_chunks = []
    for a in ayahs:
        raw_mp3 = ep_cache / f"yt_husary_{s}_{a}.mp3"
        norm_wav = ep_cache / f"yt_husary_{s}_{a}_norm.wav"
        if not norm_wav.exists():
            if not download_human_ayah(s, a, RECITER_YT["id"], raw_mp3):
                raise RuntimeError(f"❌ فشل تحميل آية الحصري {s}:{a}")
            normalize_wav(str(raw_mp3), str(norm_wav), gentle_loudnorm=True, volume_mult=RECITER_VOLUME_MULT)

        wav_out.extend([norm_wav, silence_path])
        yt_chunks.append({
            "surah": s,
            "ayah": a,
            "surah_name": sname,
            "reciter": RECITER_YT["name"],
            "text": f"{display} - آية {a}",
            "text_en": "",  # القرآن الكريم لا يُترجم أبداً
            "duration": round(get_dur(norm_wav) + SILENCE_DUR, 3),
        })
    if not yt_chunks:
        return None
    return {
        "index": idx,
        "type": "quran",
        "surah": s,
        "surah_name": sname,
        "ayah_numbers": ayahs,
        "display": display,
        "reciter": RECITER_YT["name"],
        "chunks": yt_chunks,
    }


def build_episode_audio(
    json_path: str,
    out_yt_mp3: str,
    out_social_mp3: str,
    mode: str = "both",
    out_yt_gemini_mp3: str | None = None,
):
    build_yt = mode in ("yt", "both", "all3")
    build_social = mode in ("social", "both", "all3")
    build_yt_gemini = (mode == "all3")

    if build_yt_gemini and not out_yt_gemini_mp3:
        out_yt_gemini_mp3 = str(out_yt_mp3).replace(".mp3", "_gemini.mp3")

    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))

    yt_quran_mode = os.getenv("YT_QURAN_MODE", "").strip().lower()
    if build_yt_gemini:
        yt_quran_mode = "husary"
    elif yt_quran_mode not in ("gemini", "husary"):
        yt_quran_mode = "gemini" if str(ep.get("story_id", "")).startswith("daily_") else "husary"

    work = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    ep_name = Path(json_path).stem
    ep_cache = CACHE_DIR / ep_name
    ep_cache.mkdir(parents=True, exist_ok=True)

    silence_path = work / "silence.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono",
         "-t", str(SILENCE_DUR), "-c:a", "pcm_s16le", str(silence_path)],
        check=True, capture_output=True,
    )

    segments_list = ep.get("segments", [])
    yt_wavs, social_wavs, yt_gemini_wavs = [], [], []
    yt_meta, social_meta, yt_gemini_meta = [], [], []

    title_ar = ep.get("title", "")
    title_hash = hashlib.md5(title_ar.encode("utf-8")).hexdigest()[:8]
    title_en = translate_title_en(title_ar, cache_file=ep_cache / f"title_en_{title_hash}.txt")

    reciter_bag = RECITER_LIST_SOCIAL.copy()
    random.shuffle(reciter_bag)
    reciter_idx = 0
    last_reciter_id = None

    def next_social_reciter():
        nonlocal reciter_bag, reciter_idx, last_reciter_id
        if reciter_idx >= len(reciter_bag):
            reciter_bag = RECITER_LIST_SOCIAL.copy()
            random.shuffle(reciter_bag)
            if len(reciter_bag) > 1 and reciter_bag[0]["id"] == last_reciter_id:
                reciter_bag[0], reciter_bag[1] = reciter_bag[1], reciter_bag[0]
            reciter_idx = 0
        chosen = reciter_bag[reciter_idx]
        reciter_idx += 1
        last_reciter_id = chosen["id"]
        return chosen

    try:
        for idx, seg in enumerate(segments_list):
            stype = seg.get("type")

            if stype in ("narration", "teaser", "cta", "outro", "intro", "summary", "hadith", "fiqh", "adhkar", "dua"):
                raw_text = sanitize_for_tts(seg.get("text", ""))
                if not raw_text:
                    continue

                seg_voice = seg.get("voice") or VOICE_BY_TYPE.get(stype, "Charon")
                print(f"   🎙️ الفقرة {idx} [{stype}] ← النبرة: {seg_voice}", flush=True)

                txt_md5 = hashlib.md5(raw_text.encode("utf-8")).hexdigest()[:8]
                diac_cache = ep_cache / f"seg_{idx}_diac_v3_{txt_md5}.txt"
                diac_text = smart_diacritize(raw_text, cache_file=diac_cache)
                chunks = [
                    c for c in split_text(diac_text)
                    if c.strip() and re.search(r"[\u0621-\u064A]", c)
                ]
                if not chunks:
                    continue

                en_hash = hashlib.md5(("||".join(chunks)).encode("utf-8")).hexdigest()[:8]
                en_cache = ep_cache / f"seg_{idx}_en_v1_{en_hash}.json"
                en_chunks = translate_chunks_batch(chunks, stype, raw_text, cache_file=en_cache)

                chunk_timings = []
                for c_idx, chunk in enumerate(chunks):
                    text_hash = hashlib.md5(f"{seg_voice}:{chunk}".encode("utf-8")).hexdigest()[:8]
                    cached_wav = ep_cache / f"seg_{idx}_{c_idx}_{text_hash}.wav"

                    if not cached_wav.exists() or cached_wav.stat().st_size < 1000:
                        temp_mp3 = work / f"temp_{idx}_{c_idx}.mp3"
                        if not generate_audio(chunk, str(temp_mp3), voice=seg_voice):
                            raise RuntimeError(f"❌ فشل توليد صوت السرد للمقطع {idx}:{c_idx}")
                        normalize_wav(str(temp_mp3), str(cached_wav), gentle_loudnorm=True, volume_mult=1.0)
                        time.sleep(0.5)

                    dur = get_dur(cached_wav)
                    if build_yt:
                        yt_wavs.extend([cached_wav, silence_path])
                    if build_social:
                        social_wavs.extend([cached_wav, silence_path])
                    if build_yt_gemini:
                        yt_gemini_wavs.extend([cached_wav, silence_path])

                    chunk_timings.append({
                        "text": chunk,
                        "text_en": en_chunks[c_idx] if c_idx < len(en_chunks) else "",
                        "duration": round(dur + SILENCE_DUR, 3),
                    })

                if chunk_timings:
                    meta_entry = {
                        "index": idx,
                        "type": stype,
                        "display": "",
                        "reciter": "",
                        "chunks": chunk_timings,
                    }
                    if build_yt:
                        yt_meta.append(meta_entry)
                    if build_social:
                        social_meta.append(meta_entry)
                    if build_yt_gemini:
                        yt_gemini_meta.append(meta_entry)

            elif stype == "quran":
                s = int(seg.get("surah") or 0)
                ayahs = [int(a) for a in (seg.get("ayah_numbers") or []) if str(a).isdigit()]
                sname = str(seg.get("surah_name", "")).strip()
                if not s or not ayahs:
                    continue
                display = seg.get("display") or (f"{sname}: {ayahs[0]}" if sname else f"{s}:{ayahs[0]}")

                if build_yt:
                    if yt_quran_mode == "gemini":
                        entry = _build_gemini_quran_chunks(idx, s, ayahs, sname, display, ep_cache, work, silence_path, yt_wavs)
                    else:
                        entry = _build_husary_quran_chunks(idx, s, ayahs, sname, display, ep_cache, silence_path, yt_wavs)
                    if entry:
                        yt_meta.append(entry)

                if build_yt_gemini:
                    g_entry = _build_gemini_quran_chunks(idx, s, ayahs, sname, display, ep_cache, work, silence_path, yt_gemini_wavs)
                    if g_entry:
                        yt_gemini_meta.append(g_entry)

                if build_social:
                    reciter_social = next_social_reciter()
                    print(f"   🎙️ آيات السوشيال {s}:{ayahs} → {reciter_social['name']}", flush=True)
                    social_chunks = []
                    for a in ayahs:
                        used_reciter = reciter_social
                        raw_mp3 = ep_cache / f"soc_{used_reciter['id']}_{s}_{a}.mp3"
                        norm_wav = ep_cache / f"soc_{used_reciter['id']}_{s}_{a}_norm.wav"
                        if not norm_wav.exists():
                            if not download_human_ayah(s, a, used_reciter["id"], raw_mp3):
                                ok_fb = False
                                for fb in RECITER_LIST_SOCIAL:
                                    fb_mp3 = ep_cache / f"soc_{fb['id']}_{s}_{a}.mp3"
                                    if download_human_ayah(s, a, fb["id"], fb_mp3):
                                        used_reciter = fb
                                        raw_mp3 = fb_mp3
                                        norm_wav = ep_cache / f"soc_{fb['id']}_{s}_{a}_norm.wav"
                                        ok_fb = True
                                        break
                                if not ok_fb:
                                    raise RuntimeError(f"❌ فشل تحميل آية {s}:{a}")
                            normalize_wav(str(raw_mp3), str(norm_wav), gentle_loudnorm=True, volume_mult=RECITER_VOLUME_MULT)

                        social_wavs.extend([norm_wav, silence_path])
                        social_chunks.append({
                            "surah": s,
                            "ayah": a,
                            "surah_name": sname,
                            "reciter": used_reciter["name"],
                            "text": f"{display} - آية {a}",
                            "text_en": "",  # القرآن الكريم لا يُترجم أبداً
                            "duration": round(get_dur(norm_wav) + SILENCE_DUR, 3),
                        })
                    if social_chunks:
                        social_meta.append({
                            "index": idx,
                            "type": "quran",
                            "surah": s,
                            "surah_name": sname,
                            "ayah_numbers": ayahs,
                            "display": display,
                            "reciter": reciter_social["name"],
                            "chunks": social_chunks,
                        })

        def _save_track(wavs, mp3_out, meta_list, tag):
            if not wavs:
                return
            _export_final_mp3(wavs, mp3_out, work)
            Path(str(mp3_out).replace(".mp3", "_meta.json")).write_text(
                json.dumps({
                    "episode": ep.get("episode_number", 1),
                    "title": title_ar,
                    "title_en": title_en,
                    "story_id": ep.get("story_id", ""),
                    "total_duration": get_dur(mp3_out),
                    "segments": meta_list,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"✅ ({tag}) {mp3_out} | {get_dur(mp3_out)/60:.1f} دقيقة")

        if build_yt:
            _save_track(yt_wavs, out_yt_mp3, yt_meta, "YT")
        if build_social:
            _save_track(social_wavs, out_social_mp3, social_meta, "Social")
        if build_yt_gemini and out_yt_gemini_mp3:
            _save_track(yt_gemini_wavs, out_yt_gemini_mp3, yt_gemini_meta, "YT-Gemini")
    finally:
        cleanup_dir(work)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("episode_json")
    parser.add_argument("yt_mp3")
    parser.add_argument("social_mp3")
    parser.add_argument("--mode", choices=["social", "yt", "both", "all3"], default="both")
    parser.add_argument("--yt-gemini-mp3", default=None)
    args = parser.parse_args()
    build_episode_audio(
        args.episode_json,
        args.yt_mp3,
        args.social_mp3,
        mode=args.mode,
        out_yt_gemini_mp3=args.yt_gemini_mp3,
    )
