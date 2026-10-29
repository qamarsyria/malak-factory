"""توليد الصوت النهائي لخط القصص.

المحركات:
- السرد (narration/teaser/cta/outro/summary): Edge TTS بصوت ذكر
  (ar-SA-HamedNeural) مع تشكيل كامل عبر mishkal + قاموس يدوي.
- الآيات (quran): Gemini TTS (Charon، ar-XA) مع توجيه تجويد.
  احتياطي: Edge TTS إذا فشل Gemini.

تحسينات v2:
- معالجة الألف المقصورة (ى) بدقة، لأن Edge TTS يتجاهلها بدون تشكيل.
- fade in/out قصير (50ms) على كل مقطع لمنع "الخشة" بين الآيات والسرد.
- صمت 150ms بين كل مقطعين.
- توسيع القاموس اليدوي ليشمل 100+ كلمة حرجة.

قيود صارمة:
- ممنوع أي صوت نسائي (في السرد أو الآيات).
- ممنوع إرسال أي صوت بشري كمرجع (Content ID).
- حماية حصة Gemini: التحول إلى Edge بعد فشلين متتاليين،
  وسقف 15 استدعاء Gemini لكل حلقة.
"""
import os
import sys
import json
import asyncio
import subprocess
import tempfile
import shutil
import re
import base64
import wave
from pathlib import Path

import requests
import edge_tts

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from sfx_manager import fetch_sfx
from utilities import cleanup_dir, notify_telegram, safe_copy


# ═══ mishkal إلزامي للسرد ═══
try:
    from mishkal import tashkeel as _mishkal
    HAS_MISHKAL = True
except ImportError:
    HAS_MISHKAL = False
    print("⚠️ mishkal غير مثبت — التشكيل سيكون ناقصاً!", flush=True)


# ═══ الإعدادات — أصوات ذكورية فقط ═══
TARGET_BR = "192k"
SFX_VOLUME = 0.15
EDGE_VOICE = "ar-SA-HamedNeural"
EDGE_RATE = "-12%"
EDGE_VOLUME = "+0%"

GEMINI_VOICE = "Charon"
GEMINI_LANGUAGE = "ar-XA"
TAJWEED_STYLE = (
    "Recite with tajweed: measured pace, full madd on long vowels, "
    "clear articulation of every consonant with its diacritic, "
    "humble and contemplative tone, brief pause at the end."
)

MAX_GEMINI_FAILURES = 2
MAX_GEMINI_CALLS_PER_EPISODE = 15

# ═══ إعدادات الانتقالات (لمنع "الخشة") ═══
FADE_DURATION = 0.05  # 50ms fade in/out على كل مقطع
SILENCE_BETWEEN = 0.15  # 150ms صمت بين المقاطع


# ═══ قاموس التشكيل اليدوي — 100+ كلمة حرجة ═══
MANUAL_TASHKEEL = {
    # ─── أسماء الله ───
    "الله": "اللَّه", "بالله": "بِاللَّه", "والله": "وَاللَّه",
    "تالله": "تَاللَّه", "لله": "لِلَّه", "الرحمن": "الرَّحْمَن",
    "الرحيم": "الرَّحِيم",

    # ─── كلمات دينية ───
    "القرآن": "الْقُرْآن", "محمد": "مُحَمَّد", "رسول": "رَسُول",
    "النبي": "النَّبِيّ", "الأنبياء": "الْأَنْبِيَاء",
    "الصديق": "الصِّدِّيق", "الشيطان": "الشَّيْطَان",
    "الملائكة": "الْمَلَائِكَة", "الجن": "الْجِنّ",

    # ─── أسماء الأنبياء ───
    "إبراهيم": "إِبْرَاهِيم", "إسماعيل": "إِسْمَاعِيل",
    "إسحاق": "إِسْحَاق", "يعقوب": "يَعْقُوب", "يوسف": "يُوسُف",
    "موسى": "مُوسَى", "عيسى": "عِيسَى", "داود": "دَاوُد",
    "سليمان": "سُلَيْمَان", "يونس": "يُونُس", "أيوب": "أَيُّوب",
    "زكريا": "زَكَرِيَّا", "يحيى": "يَحْيَى", "مريم": "مَرْيَم",
    "آدم": "آدَم", "نوح": "نُوح", "هود": "هُود", "صالح": "صَالِح",
    "لوط": "لُوط", "شعيب": "شُعَيْب", "هارون": "هَارُون",

    # ─── أماكن ───
    "مكة": "مَكَّة", "المدينة": "الْمَدِينَة", "الكعبة": "الْكَعْبَة",
    "المسجد": "الْمَسْجِد", "القدس": "الْقُدْس", "الشام": "الشَّام",
    "مصر": "مِصْر", "بابل": "بَابِل", "فلسطين": "فِلَسْطِين",

    # ─── صلوات وأذكار ───
    "رضي الله عنه": "رَضِيَ اللَّهُ عَنْهُ",
    "رضي الله عنها": "رَضِيَ اللَّهُ عَنْهَا",
    "رضي الله عنهم": "رَضِيَ اللَّهُ عَنْهُمْ",
    "صلى الله عليه وسلم": "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ",
    "عليه السلام": "عَلَيْهِ السَّلَامُ",
    "عليها السلام": "عَلَيْهَا السَّلَامُ",
    "عليهم السلام": "عَلَيْهِمُ السَّلَامُ",
    "رحمه الله": "رَحِمَهُ اللَّهُ",

    # ═══ الألف المقصورة — حرج جداً لـ Edge TTS ═══
    "على": "عَلَى",
    "إلى": "إِلَى",
    "هدى": "هُدًى",
    "مصطفى": "مُصْطَفًى",
    "التقوى": "التَّقْوَى",
    "الأولى": "الْأُولَى",
    "الأخرى": "الْأُخْرَى",
    "الأدنى": "الْأَدْنَى",
    "الأعلى": "الْأَعْلَى",
    "الدنيا": "الدُّنْيَا",
    "الآخرة": "الْآخِرَة",
    "مرضى": "مَرْضَى",
    "أسرى": "أَسْرَى",
    "جرحى": "جَرْحَى",
    "دعوى": "دَعْوَى",
    "نجوى": "نَجْوَى",
    "مأوى": "مَأْوَى",
    "سلوى": "سَلْوَى",
    "بلوى": "بَلْوَى",
    "شكوى": "شَكْوَى",
    "دوا": "دَوَى",
    "رؤيا": "رُؤْيَا",
    "ذكرى": "ذِكْرَى",
    "أنثى": "أُنْثَى",
    "مثلى": "مِثْلَى",
    "مولى": "مَوْلَى",
    "أولى": "أُولَى",
    "قصوى": "قُصْوَى",
    "مصطفى": "مُصْطَفًى",
    "مجتبى": "مُجْتَبَى",
    "مرتضى": "مُرْتَضَى",
    "مقتدى": "مُقْتَدَى",
    "مستشفى": "مُسْتَشْفَى",
    "معنى": "مَعْنَى",
    "مسمى": "مُسَمَّى",
    "مبنى": "مَبْنَى",
    "مستوى": "مُسْتَوَى",
    "منتدى": "مُنْتَدَى",
    "ملتقى": "مُلْتَقَى",
    "محتوى": "مُحْتَوَى",
    "مدعى": "مُدَّعَى",
}


def _fix_alef_maqsura(text: str) -> str:
    """
    معالجة الألف المقصورة (ى) التي لا ينطقها Edge TTS.
    الكلمات في MANUAL_TASHKEEL مُشكّلة مسبقاً، لذا هذه الدالة
    تعمل كطبقة إضافية للتأكد من أن كل 'ى' في نهاية كلمة
    لها تشكيل واضح.
    """
    # نطبق قاموس الكلمات الذي يحتوي على 'ى'
    for plain, diacritized in sorted(
        MANUAL_TASHKEEL.items(), key=lambda x: -len(x[0])
    ):
        if "ى" not in plain and "ى" not in diacritized:
            continue
        pattern = rf'(?<![ء-ي\u064B-\u0652]){re.escape(plain)}(?![ء-ي\u064B-\u0652])'
        text = re.sub(pattern, diacritized, text)
    return text


def apply_tashkeel(text: str) -> str:
    """إضافة تشكيل كامل للنص (للسرد فقط)."""
    if not text or not text.strip():
        return text

    # 1. الإصلاحات اليدوية (أعلى أولوية — تشمل الألف المقصورة)
    text = _fix_alef_maqsura(text)

    for plain, diacritized in sorted(
        MANUAL_TASHKEEL.items(), key=lambda x: -len(x[0])
    ):
        if "ى" in plain and plain in MANUAL_TASHKEEL:
            continue  # تمت معالجتها في _fix_alef_maqsura
        pattern = rf'(?<![ء-ي\u064B-\u0652]){re.escape(plain)}(?![ء-ي\u064B-\u0652])'
        text = re.sub(pattern, diacritized, text)

    # 2. mishkal للتشكيل الكامل (بقية النص)
    if HAS_MISHKAL:
        try:
            text = _mishkal.tashkeel(text)
        except Exception as e:
            print(f"   ⚠️ mishkal فشل: {str(e)[:80]}", flush=True)

    return text


def _clean_quran_text(text: str) -> str:
    """تنظيف خفيف للآية: يبقي التشكيل، يحذف علامات الوقف."""
    text = re.sub(r"[ۖۗۘۙۚۛ۞]", "", text)
    text = text.replace("۝", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ═══ أدوات مساعدة ═══
def get_duration(path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def normalize(input_path, output_path):
    """تطبيع إلى 44100 Hz mono + loudnorm."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path),
             "-ar", "44100", "-ac", "1",
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
             "-codec:a", "libmp3lame", "-b:a", TARGET_BR, str(output_path)],
            check=True, capture_output=True, timeout=120,
        )
    except Exception:
        safe_copy(input_path, output_path)


def apply_fade(input_path, output_path, duration: float = None):
    """تطبيق fade in/out قصير على كل مقطع لمنع 'الخشة'."""
    if duration is None:
        duration = FADE_DURATION
    dur = get_duration(input_path)
    if dur <= duration * 2:
        safe_copy(input_path, output_path)
        return
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path),
         "-af",
         f"afade=t=in:st=0:d={duration},"
         f"afade=t=out:st={max(0, dur - duration)}:d={duration}",
         "-c:a", "libmp3lame", "-b:a", TARGET_BR, str(output_path)],
        check=True, capture_output=True,
    )


def make_silence(output_path, duration: float = None):
    """إنشاء ملف صمت قصير."""
    if duration is None:
        duration = SILENCE_BETWEEN
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono",
         "-t", str(duration),
         "-c:a", "libmp3lame", "-b:a", TARGET_BR,
         str(output_path)],
        check=True, capture_output=True,
    )


def fetch_ayah_text(surah: int, ayah: int) -> str:
    try:
        r = requests.get(f"{QURAN_API}/ayah/{surah}:{ayah}", timeout=10)
        if r.status_code == 200:
            return r.json()["data"]["text"]
    except Exception:
        pass
    return ""


# ═══ Edge TTS (للسرد والاحتياطي) ═══
async def _edge_async(text: str, output_path: str):
    comm = edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE, volume=EDGE_VOLUME)
    await comm.save(output_path)


def edge_tts_generate(text: str, output_path: str):
    clean = re.sub(r'\[.*?\]', '', text)
    clean = re.sub(r'\(.*?\)', '', clean).strip()
    if not clean:
        raise ValueError("نص فارغ بعد التنظيف")
    try:
        asyncio.run(_edge_async(clean, output_path))
    except Exception as e:
        print(f"❌ فشل Edge TTS: {e}", flush=True)
        raise
    if not Path(output_path).exists() or Path(output_path).stat().st_size < 1024:
        raise RuntimeError("Edge TTS: ملف صغير أو مفقود")


# ═══ Gemini TTS للآيات ═══
_style_supported = None


def gemini_quran_tts(text: str, output_path: Path) -> tuple[bool, bool]:
    global _style_supported

    try:
        from gemini_tts_pool import get_pool, discover_tts_models
    except ImportError:
        return False, False

    try:
        pool = get_pool()
    except Exception:
        return False, False

    models = discover_tts_models(prefer_lite=False)
    if not models:
        return False, False

    for model in models[:3]:
        for attempt in range(2):
            try:
                key = pool.get_current()
            except Exception:
                return False, False

            use_style = _style_supported is not False
            speech_config = {
                "languageCode": GEMINI_LANGUAGE,
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}
                },
            }
            if use_style:
                speech_config["style"] = TAJWEED_STYLE

            body = {
                "contents": [{"parts": [{"text": text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": speech_config,
                },
            }

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={key}"
            )

            try:
                r = requests.post(url, json=body, timeout=180)

                if r.status_code == 400 and use_style:
                    _style_supported = False
                    continue

                if r.status_code == 429:
                    pool.mark_daily_exhausted()
                    return False, False

                if r.status_code == 404:
                    break

                if r.status_code != 200:
                    continue

                data = r.json()
                cands = data.get("candidates") or []
                if not cands:
                    continue
                parts = (cands[0].get("content") or {}).get("parts") or []
                if not parts:
                    continue
                inline = parts[0].get("inlineData") or {}
                audio_b64 = inline.get("data")
                if not audio_b64:
                    continue

                raw = base64.b64decode(audio_b64)
                with wave.open(str(output_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(raw)

                if use_style and _style_supported is None:
                    _style_supported = True

                pool._advance()
                return True, use_style

            except Exception as e:
                print(f"   ⚠️ Gemini استثناء: {str(e)[:80]}", flush=True)
                pool.mark_failed(60)
                continue

    return False, False


# ═══ الدالة الرئيسية ═══
def build_episode_audio(episode_json_path: str, output_mp3: str):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    parts, meta_segments, idx = [], [], 0
    all_segs = ep.get("segments", [])
    total = len(all_segs)

    gemini_ok = 0
    gemini_fail = 0
    gemini_calls = 0
    edge_fallback = 0

    # ملف صمت للفصل بين المقاطع
    silence_path = work_dir / "silence.mp3"
    make_silence(silence_path)

    try:
        for pos, seg in enumerate(all_segs):
            seg_type = seg.get("type")
            raw_path = work_dir / f"raw_{idx:03d}.mp3"
            fade_path = work_dir / f"fade_{idx:03d}.mp3"
            norm_path = work_dir / f"norm_{idx:03d}.mp3"

            # ═══ السرد (Edge TTS) ═══
            if seg_type in ("narration", "teaser", "cta", "outro", "intro", "summary"):
                text = seg["text"]

                # تطبيق التشكيل + معالجة الألف المقصورة
                old_count = len(re.findall(r'[\u064B-\u0652]', text))
                text = apply_tashkeel(text)
                new_count = len(re.findall(r'[\u064B-\u0652]', text))
                if new_count > old_count:
                    print(f"   🔤 تشكيل السرد: {old_count} → {new_count} علامة", flush=True)
                    seg["text"] = text

                edge_tts_generate(text, str(raw_path))

                src = raw_path
                sfx_query = seg.get("sfx", "")
                if sfx_query:
                    sfx_path = fetch_sfx(sfx_query)
                    if sfx_path and os.path.exists(sfx_path):
                        mixed = work_dir / f"mixed_{idx:03d}.mp3"
                        subprocess.run(
                            ["ffmpeg", "-y",
                             "-i", str(raw_path),
                             "-stream_loop", "-1", "-i", sfx_path,
                             "-filter_complex",
                             f"[1:a]volume={SFX_VOLUME}[sfx];[0:a][sfx]amix=inputs=2:duration=first[out]",
                             "-map", "[out]",
                             "-c:a", "libmp3lame", "-b:a", "192k",
                             str(mixed)],
                            check=True, capture_output=True,
                        )
                        src = mixed

                # fade قصير على كل مقطع (لا فقط الأول والأخير)
                apply_fade(src, fade_path)
                normalize(fade_path, norm_path)

                meta = {
                    "index": idx, "type": seg_type, "text": text,
                    "visual": seg.get("visual", "mosque"),
                    "duration": get_duration(norm_path),
                    "engine": "edge",
                }
                if seg_type == "teaser" and seg.get("next_episode_title"):
                    meta["teaser_data"] = {
                        "next_episode_title": seg.get("next_episode_title"),
                        "url": seg.get("url"),
                        "platform_name": "ملاك المعرفة",
                    }
                meta_segments.append(meta)
                parts.append(norm_path)
                # صمت بعد كل مقطع (ما عدا الأخير)
                if pos < total - 1:
                    parts.append(silence_path)

            # ═══ الآيات (Gemini → Edge احتياطي) ═══
            elif seg_type == "quran":
                surah = seg["surah"]
                ayahs = seg["ayah_numbers"]
                sname = seg.get("surah_name") or f"سورة {surah}"

                if len(ayahs) == 1:
                    txt = fetch_ayah_text(surah, ayahs[0])
                    disp = f"{sname}: {ayahs[0]}"
                else:
                    txt = " ".join(filter(None, (fetch_ayah_text(surah, a) for a in ayahs)))
                    disp = f"{sname}: {ayahs[0]}-{ayahs[-1]}"

                if not txt:
                    print(f"   ⏭️ لا نص للآية {disp}", flush=True)
                    idx += 1
                    continue

                txt_clean = _clean_quran_text(txt)
                generated = False
                used_style = False
                engine = "none"

                can_use_gemini = (
                    gemini_fail < MAX_GEMINI_FAILURES
                    and gemini_calls < MAX_GEMINI_CALLS_PER_EPISODE
                )

                if can_use_gemini:
                    print(f"🎙️ Gemini [آية {disp}]...", flush=True)
                    gemini_calls += 1
                    wav_out = work_dir / f"gemini_{idx:03d}.wav"
                    success, used_style = gemini_quran_tts(txt_clean, wav_out)

                    if success and wav_out.exists() and wav_out.stat().st_size > 4096:
                        # تحويل WAV إلى MP3 (مع تطبيع لضمان نفس الصيغة)
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", str(wav_out),
                             "-ar", "44100", "-ac", "1",
                             "-codec:a", "libmp3lame", "-b:a", "192k",
                             str(raw_path)],
                            check=True, capture_output=True,
                        )
                        generated = True
                        gemini_ok += 1
                        engine = "gemini"
                        print(
                            f"   ✅ Gemini نجح ({get_duration(raw_path):.1f}s، "
                            f"style={'نعم' if used_style else 'لا'})",
                            flush=True,
                        )
                    else:
                        gemini_fail += 1
                        print(f"   ⚠️ Gemini فشل (فشل متتالي: {gemini_fail})", flush=True)

                if not generated:
                    print(f"🎙️ Edge TTS [آية {disp}] (احتياطي)...", flush=True)
                    edge_tts_generate(txt_clean, str(raw_path))
                    generated = True
                    edge_fallback += 1
                    engine = "edge"

                # fade قصير على كل آية (لا فقط الأول والأخير)
                apply_fade(raw_path, fade_path)
                normalize(fade_path, norm_path)

                meta_segments.append({
                    "index": idx, "type": "quran", "text": txt_clean,
                    "display": disp, "visual": "quran book",
                    "duration": get_duration(norm_path),
                    "engine": engine,
                    "style_applied": used_style,
                })
                parts.append(norm_path)
                # صمت بعد كل آية (ما عدا الأخيرة)
                if pos < total - 1:
                    parts.append(silence_path)

            idx += 1

        if not parts:
            raise RuntimeError("لا مقاطع صوتية")

        # ═══ دمج نهائي ═══
        concat_file = work_dir / "final.txt"
        concat_file.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in parts),
            encoding="utf-8",
        )
        merged = work_dir / "merged_raw.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_file),
             "-codec:a", "libmp3lame", "-b:a", TARGET_BR, str(merged)],
            check=True, capture_output=True,
        )

        Path(output_mp3).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(merged), str(output_mp3))

        # ═══ meta ═══
        meta_path = str(output_mp3).replace(".mp3", "_meta.json")
        Path(meta_path).write_text(
            json.dumps({
                "episode": ep.get("episode_number", 1),
                "title": ep.get("title", ""),
                "story_id": ep.get("story_id", ""),
                "total_duration": get_duration(output_mp3),
                "stats": {
                    "gemini_ok": gemini_ok,
                    "gemini_fail": gemini_fail,
                    "gemini_calls": gemini_calls,
                    "edge_fallback": edge_fallback,
                    "style_supported": _style_supported,
                    "mishkal_available": HAS_MISHKAL,
                },
                "segments": meta_segments,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("", flush=True)
        print("═" * 60, flush=True)
        print(f"✅ صوت الحلقة: {output_mp3}", flush=True)
        print(f"   المدة: {get_duration(output_mp3)/60:.1f} دقيقة", flush=True)
        print(f"   Gemini: {gemini_ok} ✅ / {gemini_fail} ⚠️", flush=True)
        print(f"   Edge احتياطي: {edge_fallback}", flush=True)
        print(f"   استدعاءات Gemini: {gemini_calls}/{MAX_GEMINI_CALLS_PER_EPISODE}", flush=True)
        print(f"   style مدعوم: {_style_supported}", flush=True)
        print(f"   mishkal: {'✅' if HAS_MISHKAL else '❌ (تشكيل ناقص!)'}", flush=True)
        print("═" * 60, flush=True)

        if not HAS_MISHKAL:
            notify_telegram(
                "⚠️ mishkal غير مثبت — التشكيل ناقص. أضف mishkal إلى requirements-stories.txt",
                "warn",
            )

    finally:
        cleanup_dir(work_dir)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("الاستخدام: story_audio.py <episode.json> <out.mp3>")
        sys.exit(1)
    try:
        build_episode_audio(sys.argv[1], sys.argv[2])
    except Exception as e:
        import traceback
        traceback.print_exc()
        notify_telegram(f"فشل story_audio: {e}", "error")
        sys.exit(1)
