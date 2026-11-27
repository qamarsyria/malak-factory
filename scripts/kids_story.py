#!/usr/bin/env python3
"""
scripts/kids_story.py
=====================
مولد قصص الأطفال الإسلامية الحوارية + الصوت متعدد الشخصيات (Gemini TTS):
- يستخدم البصمة الكرتونية 3D المستخرجة للشخصيتين الطفوليتين (الولد + البنت) في كل مشهد.
- يولّد حواراً مرحاً ومبسّطاً بلهجة شامية بيضاء لطيفة مع أصوات متعددة (Puck للولد، Leda للبنت، Charon للراوي).
- يحفظ ملف الحلقة + ملف الصوت المجمع + توقيتات المشاهد بدقة ليدخل مباشرة إلى kids_video.py.
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = _REPO_ROOT / "out" / "kids"
STATE_FILE = _REPO_ROOT / "state" / "kids_state.json"

BOY_NAME = os.getenv("KIDS_BOY_NAME", "زَيد").strip() or "زَيد"
GIRL_NAME = os.getenv("KIDS_GIRL_NAME", "نُور").strip() or "نُور"

# البصمة الكرتونية 3D الثابتة المستخرجة من الصورتين
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

KIDS_STORY_TOPICS = [
    {"id": "k01_ant", "title": "النملة الذكية وسليمان", "lesson": "حب الخير للناس والتنبيه على الخطر بأسلوب لطيف", "setting": "lush sunlit green valley with tiny giant-perspective grass and sparkling flowers"},
    {"id": "k02_hoopoe", "title": "الهدهد الأمين", "lesson": "الصدق في نقل الخبر والهمة العالية في طاعة الله", "setting": "sunny desert oasis with palm trees and ancient golden stone arches"},
    {"id": "k03_whale", "title": "تسبيح يونس في الأعماق", "lesson": "الاستغفار واللجوء إلى الله وقت الضيق وعدم اليأس أبداً", "setting": "calm moonlit ocean shore with gentle glowing turquoise waves"},
    {"id": "k04_elephant", "title": "أصحاب الفيل وحماية البيت", "lesson": "الله يحمي بيته الحرام ولا يغلب قدرة الله شيء", "setting": "warm golden desert valley beneath dramatic sunlit clouds and birds high in the sky"},
    {"id": "k05_crow", "title": "الغراب الذي علّم الإنسان", "lesson": "التأمل في مخلوقات الله والرحمة والتعلم", "setting": "peaceful rocky hillside under soft blue morning sky"},
    {"id": "k06_cave", "title": "فتية الكهف والكلب الوفي", "lesson": "الثبات على الإيمان وحفظ الله لمن يتوكل عليه", "setting": "sunlit rocky cave entrance surrounded by green vines and morning mist"},
    {"id": "k07_two_gardens", "title": "صاحب الجنتين والشكر", "lesson": "شكر الله على النعم وعدم الغرور بالمال", "setting": "vibrant paradise-like orchard full of grapevines, date palms, and a flowing brook"},
    {"id": "k08_honesty", "title": "أمانة بائع اللبن", "lesson": "مراقبة الله في السر والعلن حتى لو ما حدا شايفنا", "setting": "cozy traditional Middle Eastern clay home courtyard lit by a warm lantern"},
    {"id": "k09_parents", "title": "أجمل هدية لماما وبابا", "lesson": "بر الوالدين والكلمة الحلوة والدعاء لهما", "setting": "warm sunny living room with soft cushions and a glowing garden outside the window"},
    {"id": "k10_sharing", "title": "سر السعادة في العطاء", "lesson": "الصدقة ومشاركة الألعاب والطعام تزيد البركة", "setting": "sunny neighborhood park bench under blooming trees"},
    {"id": "k11_bismillah", "title": "قوة كلمة بسم الله", "lesson": "التسمية قبل الأكل واللعب والخروج تحفظنا بالبركة", "setting": "bright cheerful wooden dining table with fresh fruit and warm sunlight"},
    {"id": "k12_kindness_animals", "title": "الرفق بالقطة والعصفور", "lesson": "الرحمة بالحيوان تدخل الجنة", "setting": "cozy sunlit stone alley with a small cute kitten near a water bowl"},
    {"id": "k13_truth", "title": "الصدق ينجّي دائماً", "lesson": "قول الحقيقة بشجاعة مهما كان الموقف صعباً", "setting": "bright wooden schoolroom desk with storybooks and sunlight"},
    {"id": "k14_harm_removal", "title": "الحجر الذي في الطريق", "lesson": "إماطة الأذى عن الطريق صدقة يحبها الله", "setting": "clean cobblestone garden path with blooming flowers"},
    {"id": "k15_dua_night", "title": "دعاء قبل النوم والأحلام الجميلة", "lesson": "الأذكار قبل النوم تحمينا وتملأ قلبنا سكينة", "setting": "cozy kids bedroom window overlooking a starry crescent moon night sky"},
]


def _get_gemini_keys() -> list[str]:
    raw = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get("GEMINI_API_KEY", "")
    return [k.strip() for k in re.split(r"[\s,;]+", raw.strip()) if len(k.strip()) > 20]


def _call_gemini_json(prompt: str) -> dict | None:
    keys = _get_gemini_keys()
    if not keys:
        raise RuntimeError("❌ GEMINI_API_KEYS غير موجود.")
    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"):
        for k in keys:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k}",
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.65, "maxOutputTokens": 4096, "responseMimeType": "application/json"},
                    },
                    timeout=55,
                )
                if r.status_code == 200:
                    parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                    txt = "".join(p.get("text", "") for p in parts if not p.get("thought") and p.get("text"))
                    s, e = txt.find("{"), txt.rfind("}")
                    if s != -1 and e > s:
                        return json.loads(txt[s : e + 1])
                elif r.status_code in (401, 403):
                    continue
            except Exception:
                continue
    return None


def _tts_segment(text: str, voice: str, out_wav: Path) -> bool:
    keys = _get_gemini_keys()
    clean = re.sub(r"[()\[\]﴿﴾«»*#@_]", " ", text).strip()
    if not clean:
        return False

    style_hint = (
        "اقرأ بحماس ولطف وابتسامة طفولية بريئة وبوضوح تام: "
        if voice in ("Leda", "Puck")
        else "اقرأ بصوت حكواتي دافئ حنون وواضح للأطفال: "
    )

    for model in ("gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts", "gemini-2.5-flash-tts"):
        for k in keys:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k}",
                    json={
                        "contents": [{"parts": [{"text": f"{style_hint}\n{clean}"}]}],
                        "generationConfig": {
                            "responseModalities": ["AUDIO"],
                            "speechConfig": {
                                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
                            },
                        },
                    },
                    timeout=55,
                )
                if r.status_code != 200:
                    continue
                parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                for p in parts:
                    inline = p.get("inlineData") or p.get("inline_data")
                    if inline and inline.get("data"):
                        raw = base64.b64decode(inline["data"])
                        mime = (inline.get("mimeType") or inline.get("mime_type") or "").lower()
                        tmp = out_wav.with_suffix(".raw_audio")
                        tmp.write_bytes(raw)
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
                        tmp.unlink(missing_ok=True)
                        if out_wav.exists() and out_wav.stat().st_size > 4000:
                            return True
            except Exception:
                continue
    return False


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

    prompt = f"""أنت مؤلف قصص أطفال إسلامية محترف تكتب بلهجة شامية بيضاء لطيفة، مرحة، ومحببة للأطفال.

القصة المطلوبة: «{topic['title']}»
الدرس المستفاد: {topic['lesson']}
البيئة: {topic['setting']}

الشخصيتان الرئيسيتان في الحلقة:
1) الولد «{BOY_NAME}» (فضولي وذكي وطيب).
2) البنت «{GIRL_NAME}» (لطيفة ومبتسمة وتسأل أسئلة ذكية).
3) الراوي الحكيم (صوت الجد/الراوي الدافئ الذي يقص الحدث ويربطه بالدرس).

ضوابط إلزامية:
- يُمنع منعاً باتاً تجسيد الأنبياء أو الملائكة أو الصحابة أو الذات الإلهية في أي وصف مشهد!
- قسّم القصة إلى 6 أو 7 مشاهد قصيرة وسريعة ومشوّقة (كل جملة لا تزيد عن 20–28 كلمة حتى لا يمل الطفل).
- اجعل الأولوية لحوار لطيف بين «{BOY_NAME}» و«{GIRL_NAME}» والراوي مع تشكيلة نطق واضحة (خصوصاً أواخر الكلمات المهمة).
- لا تكتب أقواس () أو رموز أو إيموجي داخل `text`.
- في كل مشهد اكتب `scene_focus_en` بالإنكليزية يصف ما يفعله «{BOY_NAME}» و«{GIRL_NAME}» أو المخلوق/البيئة في هذا المشهد ليُدمج مع بصمتهما الكرتونية!

أعد JSON فقط بهذا الشكل:
{{
  "title": "عنوان جذاب ومختصر للأطفال (3-5 كلمات)",
  "moral_card": "درس القصة في جملة قصيرة مبهجة للأطفال لتُكتب على دفتر النهاية",
  "segments": [
    {{
      "speaker": "boy|girl|narrator",
      "text": "النص المنطوق هنا...",
      "who_visible": "both|boy|girl|nature_only",
      "scene_action_en": "short 12-word English action inside {topic['setting']}"
    }}
  ]
}}"""

    res = _call_gemini_json(prompt)
    if not res or not res.get("segments"):
        raise RuntimeError("❌ تعذر توليد قصة الأطفال من Gemini.")

    voice_map = {"boy": "Puck", "girl": "Leda", "narrator": "Charon"}
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = Path(tempfile.mkdtemp(prefix="kids_tts_"))

    wav_files = []
    built_segs = []
    cursor = 0.0

    for idx, seg in enumerate(res["segments"][:8]):
        sp = seg.get("speaker", "narrator")
        voice = voice_map.get(sp, "Charon")
        txt = re.sub(r"\s+", " ", (seg.get("text") or "").strip())
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

        # بناء وصف المشهد 3D Pixar مدموجاً بالبصمة الكرتونية الثابتة للشخصيتين
        who = seg.get("who_visible", "both")
        act = seg.get("scene_action_en", topic["setting"])
        if who == "boy":
            char_desc = BOY_CHARACTER_DNA
        elif who == "girl":
            char_desc = GIRL_CHARACTER_DNA
        elif who == "both":
            char_desc = f"{BOY_CHARACTER_DNA}, standing happily next to {GIRL_CHARACTER_DNA}"
        else:
            char_desc = "no people, pure storybook scenery"

        full_scene_en = (
            f"{char_desc}, {act}, {topic['setting']}, "
            "3D Pixar and Disney animated storybook movie still, warm golden lighting, vibrant rich colors, "
            "vertical 9:16 portrait composition, no text, no watermark"
        )

        built_segs.append({
            "speaker": sp,
            "voice": voice,
            "text": txt,
            "start_sec": round(cursor, 3),
            "end_sec": round(cursor + dur_s, 3),
            "duration": round(dur_s, 3),
            "scene_prompt_en": full_scene_en,
        })
        cursor += dur_s
        wav_files.append(wav_p)

    # دمج الصوت وإخراجه كـ MP3 نظيف
    concat_txt = work / "audio_list.txt"
    concat_txt.write_text("\n".join(f"file '{w.resolve()}'" for w in wav_files), encoding="utf-8")
    audio_mp3 = OUT_DIR / f"kids_{state['ep_count']:03d}_{ts}.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c:a", "libmp3lame", "-b:a", "128k", str(audio_mp3)],
        check=True, timeout=90,
    )

    ep_json = OUT_DIR / f"kids_{state['ep_count']:03d}_{ts}.json"
    ep_data = {
        "episode_number": state["ep_count"],
        "topic_id": topic["id"],
        "title": (res.get("title") or topic["title"]).strip(),
        "moral_card": (res.get("moral_card") or topic["lesson"]).strip(),
        "total_duration": round(cursor, 3),
        "audio_path": str(audio_mp3),
        "segments": built_segs,
    }
    ep_json.write_text(json.dumps(ep_data, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "latest_kids_ep.txt").write_text(str(ep_json), encoding="utf-8")

    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ تم تأليف قصة الأطفال وصوتها: {ep_json.name} ({cursor:.1f} ث)", flush=True)


if __name__ == "__main__":
    main()
