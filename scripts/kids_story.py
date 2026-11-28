#!/usr/bin/env python3
"""
scripts/kids_story.py
=====================
مولد قصص الأطفال الإسلامية الحوارية + الصوت متعدد الشخصيات:
- الشخصيات: زَيْد (Puck) + نُور (Leda) + مَيَّار باللهجة العراقية الموصلية (Zephyr / ar-IQ-RanaNeural) + الحكواتي (Charon).
- يلتزم ببصمة 3D Pixar ثابتة ومميزة لكل شخصية، ويمنع أي تجسيد للأنبياء أو البالغين.
- يضبط اسم القناة بدون شدة على اللام: مَلَاكُ المَعْرِفَةِ.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = _REPO_ROOT / "out" / "kids"
STATE_FILE = _REPO_ROOT / "state" / "kids_state.json"

BOY_NAME = os.getenv("KIDS_BOY_NAME", "زَيْد").strip() or "زَيْد"
GIRL_NAME = os.getenv("KIDS_GIRL_NAME", "نُور").strip() or "نُور"
MAYAR_NAME = os.getenv("KIDS_MAYAR_NAME", "مَيَّار").strip() or "مَيَّار"

_DEAD_KEYS: set[str] = set()

BOY_CHARACTER_DNA = (
    "a cute cheerful 7-year-old Middle Eastern boy with warm light-olive skin, "
    "thick jet-black hair swept neatly upward in front, defined dark eyebrows, expressive dark brown eyes, "
    "gentle friendly smile, wearing a clean crisp white collared polo shirt and dark trousers"
)

GIRL_CHARACTER_DNA = (
    "an adorable 6-year-old Middle Eastern girl with fair skin, soft rosy cheeks, round sweet face, "
    "big innocent dark brown eyes, warm gentle smile, wearing a delicate ivory-white lace-textured modest hijab "
    "and a cozy cream-white zip hoodie over a soft light blouse"
)

MAYAR_CHARACTER_DNA = (
    "a lively cheerful 6-year-old Iraqi girl from Mosul with warm golden-olive skin, bright sparkling hazel-brown eyes, "
    "sweet dimpled smile, wearing a soft sky-blue embroidered modest hijab and a neat pastel-teal cardigan over a light dress"
)

KIDS_STORY_TOPICS = [
    {"id": "k01_ant", "title": "النملة الذكية وسليمان", "lesson": "حب الخير للناس والتنبيه على الخطر بأسلوب لطيف", "setting": "lush sunlit green valley with tall grass and sparkling flowers", "fb_query": "green meadow flowers"},
    {"id": "k02_hoopoe", "title": "الهدهد الأمين", "lesson": "الصدق في نقل الخبر والهمة العالية في طاعة الله", "setting": "sunny desert oasis with palm trees and ancient golden stone arches", "fb_query": "desert oasis palm trees"},
    {"id": "k03_whale", "title": "تسبيح يونس في الأعماق", "lesson": "الاستغفار واللجوء إلى الله وقت الضيق وعدم اليأس أبداً", "setting": "calm moonlit ocean shore with gentle glowing turquoise waves", "fb_query": "moonlit ocean waves"},
    {"id": "k04_elephant", "title": "أصحاب الفيل وحماية البيت", "lesson": "الله يحمي بيته الحرام ولا يغلب قدرة الله شيء", "setting": "warm golden desert valley beneath sunlit clouds", "fb_query": "golden desert clouds"},
    {"id": "k05_crow", "title": "الغراب الذي علّم الإنسان", "lesson": "التأمل في مخلوقات الله والرحمة والتعلم", "setting": "peaceful rocky hillside under soft blue morning sky", "fb_query": "morning mountains sky"},
    {"id": "k06_cave", "title": "فتية الكهف والكلب الوفي", "lesson": "الثبات على الإيمان وحفظ الله لمن يتوكل عليه", "setting": "sunlit rocky cave entrance surrounded by green vines and morning mist", "fb_query": "sunlit forest cave"},
    {"id": "k07_two_gardens", "title": "صاحب الجنتين والشكر", "lesson": "شكر الله على النعم وعدم الغرور بالمال", "setting": "vibrant paradise-like orchard full of grapevines, date palms, and a flowing brook", "fb_query": "fruit orchard stream"},
    {"id": "k08_honesty", "title": "أمانة بائع اللبن", "lesson": "مراقبة الله في السر والعلن حتى لو لم يرنا أحد", "setting": "cozy traditional Middle Eastern clay home courtyard lit by a warm lantern", "fb_query": "traditional courtyard lantern"},
    {"id": "k09_parents", "title": "أجمل هدية لماما وبابا", "lesson": "بر الوالدين والكلمة الحلوة والدعاء لهما", "setting": "warm sunny living room with soft cushions and a glowing garden outside the window", "fb_query": "sunny cozy window garden"},
    {"id": "k10_sharing", "title": "سر السعادة في العطاء", "lesson": "الصدقة ومشاركة الألعاب والطعام تزيد البركة", "setting": "sunny neighborhood park bench under blooming trees", "fb_query": "sunny park blooming trees"},
    {"id": "k11_bismillah", "title": "قوة كلمة بسم الله", "lesson": "التسمية قبل الأكل واللعب والخروج تحفظنا بالبركة", "setting": "bright cheerful wooden dining table with fresh fruit and warm sunlight", "fb_query": "fresh fruit sunlit table"},
    {"id": "k12_kindness_animals", "title": "الرفق بالقطة والعصفور", "lesson": "الرحمة بالحيوان تدخل الجنة", "setting": "cozy sunlit stone alley with a small cute kitten near a water bowl", "fb_query": "cute kitten garden"},
    {"id": "k13_truth", "title": "الصدق ينجّي دائماً", "lesson": "قول الحقيقة بشجاعة مهما كان الموقف صعباً", "setting": "bright wooden schoolroom desk with storybooks and sunlight", "fb_query": "storybook wooden desk"},
    {"id": "k14_harm_removal", "title": "الحجر الذي في الطريق", "lesson": "إماطة الأذى عن الطريق صدقة يحبها الله", "setting": "clean cobblestone garden path with blooming flowers", "fb_query": "garden flower path"},
    {"id": "k15_dua_night", "title": "دعاء قبل النوم والأحلام الجميلة", "lesson": "الأذكار قبل النوم تحمينا وتملأ قلبنا سكينة", "setting": "cozy kids bedroom window overlooking a starry crescent moon night sky", "fb_query": "starry night crescent moon"},
]

_FORBIDDEN_ACTION_RE = re.compile(
    r"\b(?:prophet|sulayman|solomon|yunus|jonah|musa|moses|ibrahim|abraham|adam|nuh|noah|"
    r"angel|angels|gabriel|companion|sahabi|woman|women|lady|mother|king|queen|man|men)\b",
    re.IGNORECASE,
)


def _get_gemini_keys() -> list[str]:
    raw = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get("GEMINI_API_KEY", "")
    return [
        k.strip() for k in re.split(r"[\s,;]+", raw.strip())
        if len(k.strip()) > 20 and k.strip() not in _DEAD_KEYS
    ]


def _enforce_malak(text: str) -> str:
    """يضبط اسم القناة بفتح الميم وبدون شدة على اللام: مَلَاكُ المَعْرِفَةِ."""
    return re.sub(
        r"(?<![\u0621-\u064A])م[\u064B-\u065F]*ل[\u064B-\u065F]*ا[\u064B-\u065F]*ك([\u064B-\u065F]*)"
        r"\s+ال[\u064B-\u065F]*م[\u064B-\u065F]*ع[\u064B-\u065F]*ر[\u064B-\u065F]*ف[\u064B-\u065F]*ة([\u064B-\u065F]*)",
        lambda m: f"مَلَاك{m.group(1) or 'ُ'} المَعْرِفَة{m.group(2) or 'ِ'}",
        text or "",
    )


def _clean_tts_text(text: str) -> str:
    t = (text or "").replace("ﷺ", "صلى الله عليه وسلم")
    t = re.sub(r"[()\[\]﴿﴾«»\"'`*#@_•/\\|~=+—–\-]", " ", t)
    t = re.sub(r"[a-zA-Z]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return _enforce_malak(t)


def _discover_text_models(keys: list[str]) -> list[str]:
    defaults = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    for k in keys[:3]:
        try:
            r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={k}", timeout=12)
            if r.status_code in (401, 403):
                _DEAD_KEYS.add(k)
                continue
            if r.status_code == 200:
                found = []
                for m in r.json().get("models", []):
                    name = m.get("name", "").replace("models/", "")
                    nl = name.lower()
                    if "generateContent" not in m.get("supportedGenerationMethods", []):
                        continue
                    if any(b in nl for b in ("tts", "image", "imagen", "veo", "embedding", "gemma", "live", "gemini-1.", "gemini-2.0")):
                        continue
                    if any(tag in nl for tag in ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite")):
                        found.append(name)
                if found:
                    for d in defaults:
                        if d not in found:
                            found.append(d)
                    return found[:6]
        except Exception:
            continue
    return defaults


def _call_gemini_json(prompt: str) -> dict | None:
    keys = _get_gemini_keys()
    if not keys:
        raise RuntimeError("❌ GEMINI_API_KEYS غير موجود أو كل المفاتيح معطلة.")

    models = _discover_text_models(keys)
    print(f"🤖 موديلات النص المتاحة: {models[:3]}", flush=True)

    for model in models:
        use_temperature = True
        tries_503 = 0
        for k in list(_get_gemini_keys()):
            if k in _DEAD_KEYS:
                continue
            try:
                gen_cfg: dict = {
                    "maxOutputTokens": 4096,
                    "responseMimeType": "application/json",
                }
                if use_temperature:
                    gen_cfg["temperature"] = 0.65
                if "gemini-3" in model.lower():
                    gen_cfg["thinkingConfig"] = {"thinkingLevel": "low"}

                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k}",
                    json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen_cfg},
                    timeout=(10, 35),
                )
                if r.status_code == 200:
                    parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                    txt = "".join(p.get("text", "") for p in parts if not p.get("thought") and p.get("text"))
                    s, e = txt.find("{"), txt.rfind("}")
                    if s != -1 and e > s:
                        print(f"   ✅ تم تأليف القصة عبر {model}", flush=True)
                        return json.loads(txt[s : e + 1])
                elif r.status_code == 401 or (r.status_code == 403 and "api key" in r.text.lower()):
                    _DEAD_KEYS.add(k)
                    continue
                elif r.status_code == 400 and ("temperature" in r.text.lower() or "thinking" in r.text.lower()):
                    use_temperature = False
                    r2 = requests.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k}",
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {"maxOutputTokens": 4096, "responseMimeType": "application/json"},
                        },
                        timeout=(10, 35),
                    )
                    if r2.status_code == 200:
                        parts = (r2.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                        txt = "".join(p.get("text", "") for p in parts if not p.get("thought") and p.get("text"))
                        s, e = txt.find("{"), txt.rfind("}")
                        if s != -1 and e > s:
                            return json.loads(txt[s : e + 1])
                elif r.status_code == 429:
                    time.sleep(1.0)
                    continue
                elif r.status_code == 503:
                    tries_503 += 1
                    if tries_503 >= 3:
                        print(f"   ⏭️ {model}: مزدحم (503) — الانتقال للموديل التالي", flush=True)
                        break
                    time.sleep(1.5)
                    continue
                elif r.status_code == 404:
                    break
            except Exception as e:
                print(f"   ⚠️ {model}: {str(e)[:75]}", flush=True)
                continue
    return None


def _edge_tts_fallback(clean: str, voice: str, out_wav: Path) -> bool:
    try:
        import edge_tts
    except ImportError:
        return False

    edge_map = {
        "Leda":   ("ar-SY-AmanyNeural", "+10Hz"),
        "Zephyr": ("ar-IQ-RanaNeural",  "+12Hz"),  # نبرة عراقية حقيقية لمَيَّار
        "Puck":   ("ar-SY-LaithNeural", "+12Hz"),
        "Charon": ("ar-SA-HamedNeural", "+0Hz"),
    }
    e_voice, pitch = edge_map.get(voice, ("ar-SA-HamedNeural", "+0Hz"))
    tmp_mp3 = out_wav.with_suffix(".edge.mp3")
    try:
        async def _run():
            comm = edge_tts.Communicate(clean, e_voice, rate="-4%", pitch=pitch)
            await comm.save(str(tmp_mp3))
        asyncio.run(_run())
        if tmp_mp3.exists() and tmp_mp3.stat().st_size > 1000:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp_mp3), "-ar", "24000", "-ac", "1", str(out_wav)],
                check=True, timeout=45,
            )
            return out_wav.exists() and out_wav.stat().st_size > 4000
    except Exception as e:
        print(f"   ⚠️ Edge TTS فشل: {str(e)[:80]}", flush=True)
    finally:
        tmp_mp3.unlink(missing_ok=True)
    return False


def _tts_segment(text: str, voice: str, out_wav: Path) -> bool:
    clean = _clean_tts_text(text)
    if not clean:
        return False

    for model in ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"):
        for k in list(_get_gemini_keys()):
            if k in _DEAD_KEYS:
                continue
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k}",
                    json={
                        "contents": [{"parts": [{"text": clean}]}],
                        "generationConfig": {
                            "responseModalities": ["AUDIO"],
                            "speechConfig": {
                                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
                            },
                        },
                    },
                    timeout=(10, 40),
                )
                if r.status_code == 200:
                    parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                    for p in parts:
                        inline = p.get("inlineData") or p.get("inline_data")
                        if inline and inline.get("data"):
                            raw = base64.b64decode(inline["data"])
                            mime = (inline.get("mimeType") or inline.get("mime_type") or "").lower()
                            tmp = out_wav.with_suffix(".raw_audio")
                            tmp.write_bytes(raw)
                            try:
                                if "wav" in mime:
                                    subprocess.run(
                                        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-ar", "24000", "-ac", "1", str(out_wav)],
                                        check=True, timeout=45,
                                    )
                                else:
                                    subprocess.run(
                                        ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(tmp), str(out_wav)],
                                        check=True, timeout=45,
                                    )
                            finally:
                                tmp.unlink(missing_ok=True)
                            if out_wav.exists() and out_wav.stat().st_size > 4000:
                                return True
                elif r.status_code == 401 or (r.status_code == 403 and "api key" in r.text.lower()):
                    _DEAD_KEYS.add(k)
                    continue
                elif r.status_code == 429:
                    time.sleep(1.0)
                    continue
                elif r.status_code == 404:
                    break
            except Exception:
                continue

    print(f"   ℹ️ التبديل لصوت Edge TTS الاحتياطي ({voice})...", flush=True)
    return _edge_tts_fallback(clean, voice, out_wav)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    state = {"idx": 0, "ep_count": 0}
    if STATE_FILE.exists():
        try:
            state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass

    topic = KIDS_STORY_TOPICS[state["idx"] % len(KIDS_STORY_TOPICS)]
    state["idx"] += 1
    state["ep_count"] += 1

    prompt = f"""أنت مؤلف قصص أطفال إسلامية محترف تكتب حواراً مرحاً وبسيطاً ومفهوماً للأطفال.

القصة المطلوبة: «{topic['title']}»
الدرس المستفاد: {topic['lesson']}
البيئة: {topic['setting']}

الشخصيات في الحلقة (يجب أن تشارك الشخصيات الثلاث في الحوار):
1) الولد «{BOY_NAME}» (`boy`): فضولي وذكي وطيب، يتحدث بعربية مبسطة لطيفة.
2) البنت «{GIRL_NAME}» (`girl`): لطيفة ومبتسمة وتسأل أسئلة ذكية بعربية مبسطة لطيفة.
3) الصديقة العراقية «{MAYAR_NAME}» (`mayar`): طفلة مرحة محبوبة من **أهل الموصل في العراق**؛ يجب أن تتحدث حواراتها **بلهجة أهل الموصل العراقية الأصيلة والمهذبة والمفهومة** (باستخدام كلمات مصلاوية مثل: هَسَّعْ، أَشْقَدْ حِلُو، أَشُّون، صِدُقْ، خُوش فِكْرَة، كِلِّشْ، مَا يْخَالِفْ، يَابَه، عِينِي، مع المحافظة على حرف القاف المصلاوي وضبط الكلمات الموصلية بالتشكيل ليقرأها الصوت بوضوح تام!). يجب أن تظهر «{MAYAR_NAME}» مرتين على الأقل في الحلقة.
4) الراوي الحكيم (`narrator`): يمهّد باختصار ويربط القصة بالدرس بلغة عربية سهلة.

ضوابط إلزامية:
- يُمنع منعاً باتاً تجسيد الأنبياء أو الملائكة أو الصحابة أو البالغين أو الذات الإلهية في أي وصف مشهد (`scene_action_en` يحتوي فقط على زيد ونور وميار أو الطبيعة والحيوانات اللطيفة).
- قسّم القصة إلى 6 مشاهد حوارية مشوّقة (كل جملة لا تزيد عن 18–24 كلمة حتى لا يمل الطفل).
- لا تضع أقواس () أو رموز أو أرقاماً رقمية أو إيموجي داخل `text`.
- في كل مشهد اكتب `scene_action_en` بالإنكليزية يصف ما يفعله الأطفال أو البيئة في ذلك المشهد.

أعد JSON فقط:
{{
  "title": "عنوان جذاب ومختصر للأطفال (3-5 كلمات)",
  "moral_card": "درس القصة في جملة واحدة قصيرة ومبهجة للأطفال",
  "segments": [
    {{
      "speaker": "narrator|boy|girl|mayar",
      "text": "النص المنطوق هنا...",
      "who_visible": "all|boy|girl|mayar|girls|boy_girl|boy_mayar|nature_only",
      "scene_action_en": "short 12-word English action inside {topic['setting']}"
    }}
  ]
}}"""

    res = _call_gemini_json(prompt)
    if not res or not res.get("segments"):
        raise RuntimeError("❌ تعذر توليد قصة الأطفال من Gemini.")

    voice_map = {
        "boy": "Puck",
        "girl": "Leda",
        "mayar": "Zephyr",
        "narrator": "Charon",
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = Path(tempfile.mkdtemp(prefix="kids_tts_"))

    title_clean = _clean_tts_text(res.get("title") or topic["title"])
    moral_clean = _clean_tts_text(res.get("moral_card") or topic["lesson"])

    raw_segments = list(res["segments"][:6])
    raw_segments.append({
        "type": "moral",
        "speaker": "narrator",
        "text": f"وحكمة قصتنا اليوم يا أصدقاء: {moral_clean}. تابعونا في حكاية جديدة مع {BOY_NAME} و{GIRL_NAME} و{MAYAR_NAME} على قناة مَلَاكُ المَعْرِفَةِ.",
        "who_visible": "all",
        "scene_action_en": "three cute kids smiling happily together under warm golden sunlight",
    })

    wav_files = []
    built_segs = []
    cursor = 0.0

    try:
        for idx, seg in enumerate(raw_segments):
            sp = seg.get("speaker", "narrator")
            voice = voice_map.get(sp, "Charon")
            txt = _clean_tts_text(seg.get("text") or "")
            if not txt:
                continue

            wav_p = work / f"seg_{idx}.wav"
            if not _tts_segment(txt, voice, wav_p):
                raise RuntimeError(f"❌ فشل توليد صوت المقطع {idx}")

            dur_s = float(
                subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(wav_p)],
                    capture_output=True, text=True,
                ).stdout.strip() or 4.0
            )

            who = seg.get("who_visible", "all")
            act = _FORBIDDEN_ACTION_RE.sub("nature", str(seg.get("scene_action_en", topic["setting"])))
            if who == "boy":
                char_desc = BOY_CHARACTER_DNA
            elif who == "girl":
                char_desc = GIRL_CHARACTER_DNA
            elif who == "mayar":
                char_desc = MAYAR_CHARACTER_DNA
            elif who == "girls":
                char_desc = f"{GIRL_CHARACTER_DNA}, standing happily beside {MAYAR_CHARACTER_DNA}"
            elif who == "boy_girl":
                char_desc = f"{BOY_CHARACTER_DNA}, standing happily next to {GIRL_CHARACTER_DNA}"
            elif who == "boy_mayar":
                char_desc = f"{BOY_CHARACTER_DNA}, standing happily next to {MAYAR_CHARACTER_DNA}"
            elif who in ("all", "both"):
                char_desc = f"{BOY_CHARACTER_DNA}, {GIRL_CHARACTER_DNA}, and {MAYAR_CHARACTER_DNA} standing happily together as friends"
            else:
                char_desc = "no people, pure storybook scenery"

            full_scene_en = (
                f"{char_desc}, {act}, {topic['setting']}, "
                "3D Pixar and Disney animated storybook movie still, warm golden lighting, vibrant rich colors, "
                "vertical 9:16 portrait composition, no text, no watermark"
            )

            built_segs.append({
                "type": seg.get("type", "dialogue"),
                "speaker": sp,
                "voice": voice,
                "who_visible": who,
                "text": txt,
                "start_sec": round(cursor, 3),
                "end_sec": round(cursor + dur_s, 3),
                "duration": round(dur_s, 3),
                "scene_prompt_en": full_scene_en,
                "fallback_query": topic.get("fb_query", "sunny nature meadow"),
            })
            cursor += dur_s
            wav_files.append(wav_p)

        concat_txt = work / "audio_list.txt"
        concat_txt.write_text("\n".join(f"file '{w.resolve()}'" for w in wav_files), encoding="utf-8")
        audio_mp3 = OUT_DIR / f"kids_{state['ep_count']:03d}_{ts}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c:a", "libmp3lame", "-b:a", "96k", str(audio_mp3)],
            check=True, timeout=90,
        )

        ep_json = OUT_DIR / f"kids_{state['ep_count']:03d}_{ts}.json"
        ep_data = {
            "episode_number": state["ep_count"],
            "topic_id": topic["id"],
            "title": title_clean,
            "moral_card": moral_clean,
            "total_duration": round(cursor, 3),
            "audio_path": str(audio_mp3),
            "segments": built_segs,
        }
        ep_json.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT_DIR / "latest_kids_ep.txt").write_text(str(ep_json), encoding="utf-8")

        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ تم تأليف قصة الأطفال وصوتها: {ep_json.name} ({cursor:.1f} ث)", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
