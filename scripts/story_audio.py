"""توليد الصوت النهائي لخط القصص — v4.

المحرك الوحيد: Gemini TTS (Charon، ar-XA) لكل شيء:
- السرد (narration/teaser/cta/outro/summary)
- الآيات (quran)

لماذا Gemini فقط:
- Edge TTS كان يقرأ التشكيل الخاطئ من mishkal فيصبح النطق أعوج.
- اختلاف مصادر الصوت (Gemini WAV، Edge MP3، صمت MP3) كان سبب "الخشة".
- Gemini TTS يقرأ العربية الفصحى بدقة بدون حاجة لتشكيل كامل.

الحلول الجذرية:
1. مصدر صوت واحد (Gemini WAV 24kHz) لكل المقاطع → لا اختلاف codec.
2. trim_silence على كل مقطع (بداية ونهاية).
3. fade 100ms + crossfade 150ms بين المقاطع.
4. concat filter مع إعادة ترميز (ليس demuxer copy) → انتقالات سلسة.
5. بدون mishkal، بدون Edge، بدون تشكيل يدوي معقد.

قيود صارمة:
- ممنوع أي صوت نسائي (Charon ذكر فقط).
- سقف Gemini: 40 استدعاء لكل حلقة (يكفي 20 مقطع × 2 محاولات).
- احتياطي أخير: Edge TTS فقط إذا فشل Gemini تماماً (نادراً).
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

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from sfx_manager import fetch_sfx
from utilities import cleanup_dir, notify_telegram, safe_copy


# ═══ الإعدادات — Gemini فقط، صوت ذكر ═══
TARGET_BR = "192k"
TARGET_SR = "44100"
SFX_VOLUME = 0.15

GEMINI_VOICE = "Charon"
GEMINI_LANGUAGE = "ar-XA"

MAX_GEMINI_CALLS_PER_EPISODE = 40
MAX_CONSECUTIVE_FAILURES = 3

# ═══ إعدادات الانتقالات ═══
FADE_IN_OUT = 0.10        # 100ms fade in/out
CROSSFADE = 0.15          # 150ms crossfade بين المقاطع
TRIM_THRESHOLD = "-40dB"
TRIM_MIN_DURATION = 0.30


# ═══ إصلاحات يدوية بسيطة (بدون mishkal) ═══
# فقط الكلمات الحرجة التي قد تُقرأ خطأً بدون تشكيل
CRITICAL_FIXES = {
    "الله": "اللَّه",
    "بالله": "بِاللَّه",
    "والله": "وَاللَّه",
    "لله": "لِلَّه",
    "الرحمن": "الرَّحْمَن",
    "الرحيم": "الرَّحِيم",
    "القرآن": "الْقُرْآن",
    "محمد": "مُحَمَّد",
    "صلى الله عليه وسلم": "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ",
    "رضي الله عنه": "رَضِيَ اللَّهُ عَنْهُ",
    "رضي الله عنها": "رَضِيَ اللَّهُ عَنْهَا",
    "عليه السلام": "عَلَيْهِ السَّلَامُ",
}


def apply_critical_fixes(text: str) -> str:
    """إصلاحات يدوية فقط للكلمات الحرجة — لا mishkal."""
    for plain, diacritized in sorted(
        CRITICAL_FIXES.items(), key=lambda x: -len(x[0])
    ):
        pattern = rf'(?<![ء-ي\u064B-\u0652]){re.escape(plain)}(?![ء-ي\u064B-\u0652])'
        text = re.sub(pattern, diacritized, text)
    return text


def _clean_text(text: str) -> str:
    """تنظيف النص قبل الإرسال لـ Gemini."""
    text = re.sub(r'\[.*?\]', '', text)  # إزالة [آية: X:Y]
    text = re.sub(r'\(.*?\)', '', text)  # إزالة (تلميحات)
    text = re.sub(r"[ۖۗۘۙۚۛ۞]", "", text)
    text = text.replace("۝", " ")
    return re.sub(r"\s+", " ", text).strip()


# ═══ أدوات الصوت ═══
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


def trim_silence(input_path, output_path):
    """قص الصمت من البداية والنهاية."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path),
             "-af",
             f"silenceremove=start_periods=1:start_threshold={TRIM_THRESHOLD}:start_silence={TRIM_MIN_DURATION},"
             f"areverse,"
             f"silenceremove=start_periods=1:start_threshold={TRIM_THRESHOLD}:start_silence={TRIM_MIN_DURATION},"
             f"areverse",
             "-c:a", "pcm_s16le",
             str(output_path)],
            check=True, capture_output=True, timeout=60,
        )
        if Path(output_path).stat().st_size < 5000:
            safe_copy(input_path, output_path)
    except Exception:
        safe_copy(input_path, output_path)


def convert_to_wav_44k(input_path, output_path):
    """تحويل أي صيغة إلى WAV 44100 mono — لتوحيد المصدر."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path),
         "-af", "aresample=resampler=soxr",
         "-ar", TARGET_SR, "-ac", "1",
         "-c:a", "pcm_s16le",
         str(output_path)],
        check=True, capture_output=True, timeout=120,
    )


def apply_fade(input_wav, output_wav, duration=FADE_IN_OUT):
    """fade in/out قصير."""
    dur = get_duration(input_wav)
    if dur <= duration * 2:
        safe_copy(input_wav, output_wav)
        return
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_wav),
         "-af",
         f"afade=t=in:st=0:d={duration},"
         f"afade=t=out:st={max(0, dur - duration)}:d={duration}",
         "-c:a", "pcm_s16le",
         str(output_wav)],
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


# ═══ Gemini TTS — موحّد للسرد والآيات ═══
def gemini_tts(text: str, output_wav: Path) -> bool:
    """توليد صوت عبر Gemini TTS (Charon، ar-XA)."""
    try:
        from gemini_tts_pool import get_pool, discover_tts_models
    except ImportError:
        return False

    try:
        pool = get_pool()
    except Exception:
        return False

    models = discover_tts_models(prefer_lite=False)
    if not models:
        return False

    for model in models[:3]:
        for attempt in range(2):
            try:
                key = pool.get_current()
            except Exception:
                return False

            body = {
                "contents": [{"parts": [{"text": text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "languageCode": GEMINI_LANGUAGE,
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}
                        },
                    },
                },
            }

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={key}"
            )

            try:
                r = requests.post(url, json=body, timeout=180)

                if r.status_code == 429:
                    pool.mark_daily_exhausted()
                    return False

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
                with wave.open(str(output_wav), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(raw)

                pool._advance()
                return True

            except Exception as e:
                print(f"   ⚠️ Gemini استثناء: {str(e)[:80]}", flush=True)
                pool.mark_failed(60)
                continue

    return False


# ═══ Edge TTS — احتياطي أخير فقط ═══
_EDGE_AVAILABLE = False
try:
    import edge_tts
    _EDGE_AVAILABLE = True
except ImportError:
    pass


async def _edge_async(text: str, output_path: str):
    comm = edge_tts.Communicate(text, "ar-SA-HamedNeural", rate="-12%")
    await comm.save(output_path)


def edge_tts_fallback(text: str, output_mp3: str):
    if not _EDGE_AVAILABLE:
        raise RuntimeError("Edge TTS غير متاح أيضاً")
    asyncio.run(_edge_async(text, output_mp3))
    if not Path(output_mp3).exists() or Path(output_mp3).stat().st_size < 1024:
        raise RuntimeError("Edge TTS: ملف صغير")


# ═══ بناء الحلقة — concat filter مع إعادة ترميز ═══
def build_episode_audio(episode_json_path: str, output_mp3: str):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    all_segs = ep.get("segments", [])
    total = len(all_segs)

    gemini_ok = 0
    gemini_fail = 0
    gemini_calls = 0
    edge_fallback = 0
    consecutive_failures = 0

    # قائمة ملفات WAV 44.1kHz (موحّدة)
    segment_wavs = []
    meta_segments = []
    idx = 0

    try:
        for pos, seg in enumerate(all_segs):
            seg_type = seg.get("type")
            wav_out = work_dir / f"seg_{idx:03d}.wav"
            trimmed = work_dir / f"trim_{idx:03d}.wav"
            final_wav = work_dir / f"final_{idx:03d}.wav"

            # ═══ السرد ═══
            if seg_type in ("narration", "teaser", "cta", "outro", "intro", "summary"):
                text = seg["text"]
                # إصلاحات بسيطة فقط (بدون mishkal)
                text = apply_critical_fixes(text)
                clean = _clean_text(text)
                if not clean:
                    print(f"   ⏭️ سرد فارغ [{idx}]", flush=True)
                    idx += 1
                    continue

                seg["text"] = text  # تحديث meta

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    # احتياطي: Edge
                    print(f"🔊 Edge TTS [سرد {idx}] (احتياطي بسبب فشل Gemini)...", flush=True)
                    tmp_mp3 = work_dir / f"edge_{idx:03d}.mp3"
                    edge_tts_fallback(clean, str(tmp_mp3))
                    convert_to_wav_44k(tmp_mp3, wav_out)
                    edge_fallback += 1
                else:
                    if gemini_calls >= MAX_GEMINI_CALLS_PER_EPISODE:
                        print(f"⏭️ تجاوز سقف Gemini — تخطي سرد {idx}", flush=True)
                        idx += 1
                        continue

                    print(f"🎙️ Gemini [سرد {idx}]...", flush=True)
                    gemini_calls += 1
                    if gemini_tts(clean, wav_out):
                        if wav_out.exists() and wav_out.stat().st_size > 4096:
                            convert_to_wav_44k(wav_out, wav_out)
                            gemini_ok += 1
                            consecutive_failures = 0
                            print(f"   ✅ {get_duration(wav_out):.1f}s", flush=True)
                        else:
                            gemini_fail += 1
                            consecutive_failures += 1
                            print(f"   ⚠️ ملف صغير (متتالي: {consecutive_failures})", flush=True)
                            # Edge فوري لهذا المقطع
                            tmp_mp3 = work_dir / f"edge_{idx:03d}.mp3"
                            edge_tts_fallback(clean, str(tmp_mp3))
                            convert_to_wav_44k(tmp_mp3, wav_out)
                            edge_fallback += 1
                    else:
                        gemini_fail += 1
                        consecutive_failures += 1
                        print(f"   ⚠️ فشل (متتالي: {consecutive_failures})", flush=True)
                        tmp_mp3 = work_dir / f"edge_{idx:03d}.mp3"
                        edge_tts_fallback(clean, str(tmp_mp3))
                        convert_to_wav_44k(tmp_mp3, wav_out)
                        edge_fallback += 1

                # trim → fade
                trim_silence(wav_out, trimmed)
                apply_fade(trimmed, final_wav)

                meta_segments.append({
                    "index": idx, "type": seg_type, "text": text,
                    "visual": seg.get("visual", "mosque"),
                    "duration": get_duration(final_wav),
                    "engine": "gemini" if gemini_ok > 0 else "edge",
                })
                segment_wavs.append(final_wav)

            # ═══ الآيات ═══
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

                clean = _clean_text(txt)

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"🔊 Edge TTS [آية {disp}] (احتياطي)...", flush=True)
                    tmp_mp3 = work_dir / f"edge_{idx:03d}.mp3"
                    edge_tts_fallback(clean, str(tmp_mp3))
                    convert_to_wav_44k(tmp_mp3, wav_out)
                    edge_fallback += 1
                else:
                    if gemini_calls >= MAX_GEMINI_CALLS_PER_EPISODE:
                        print(f"⏭️ تجاوز سقف Gemini — تخطي آية {disp}", flush=True)
                        idx += 1
                        continue

                    print(f"🎙️ Gemini [آية {disp}]...", flush=True)
                    gemini_calls += 1
                    if gemini_tts(clean, wav_out):
                        if wav_out.exists() and wav_out.stat().st_size > 4096:
                            convert_to_wav_44k(wav_out, wav_out)
                            gemini_ok += 1
                            consecutive_failures = 0
                            print(f"   ✅ {get_duration(wav_out):.1f}s", flush=True)
                        else:
                            gemini_fail += 1
                            consecutive_failures += 1
                            print(f"   ⚠️ ملف صغير (متتالي: {consecutive_failures})", flush=True)
                            tmp_mp3 = work_dir / f"edge_{idx:03d}.mp3"
                            edge_tts_fallback(clean, str(tmp_mp3))
                            convert_to_wav_44k(tmp_mp3, wav_out)
                            edge_fallback += 1
                    else:
                        gemini_fail += 1
                        consecutive_failures += 1
                        print(f"   ⚠️ فشل (متتالي: {consecutive_failures})", flush=True)
                        tmp_mp3 = work_dir / f"edge_{idx:03d}.mp3"
                        edge_tts_fallback(clean, str(tmp_mp3))
                        convert_to_wav_44k(tmp_mp3, wav_out)
                        edge_fallback += 1

                # trim → fade
                trim_silence(wav_out, trimmed)
                apply_fade(trimmed, final_wav)

                meta_segments.append({
                    "index": idx, "type": "quran", "text": clean,
                    "display": disp, "visual": "quran book",
                    "duration": get_duration(final_wav),
                })
                segment_wavs.append(final_wav)

            idx += 1

        if not segment_wavs:
            raise RuntimeError("لا مقاطع صوتية")

        # ═══ دمج باستخدام concat filter مع crossfade ═══
        print(f"\n🔗 دمج {len(segment_wavs)} مقطع بـ crossfade...", flush=True)

        # بناء filter_complex: crossfade بين كل مقطعين
        filter_parts = []
        inputs = []
        for i, wav in enumerate(segment_wavs):
            inputs.extend(["-i", str(wav)])

        if len(segment_wavs) == 1:
            filter_complex = "[0:a]anull[out]"
        else:
            # crossfade تسلسلي
            # [0][1]acrossfade=d=0.15:c1=tri:c2=tri[x1];
            # [x1][2]acrossfade=d=0.15:c1=tri:c2=tri[x2]; ...
            prev = "[0:a]"
            for i in range(1, len(segment_wavs)):
                label = f"[x{i}]" if i < len(segment_wavs) - 1 else "[out]"
                filter_parts.append(
                    f"{prev}[{i}:a]acrossfade=d={CROSSFADE}:c1=tri:c2=tri{label}"
                )
                prev = label
            filter_complex = ";".join(filter_parts)

        concat_cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-ar", TARGET_SR, "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", TARGET_BR,
            str(output_mp3),
        ]

        Path(output_mp3).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(concat_cmd, check=True, capture_output=True, timeout=600)

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
                    "crossfade_used": CROSSFADE,
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
        print(f"   استدعاءات: {gemini_calls}/{MAX_GEMINI_CALLS_PER_EPISODE}", flush=True)
        print(f"   crossfade: {CROSSFADE}s", flush=True)
        print("═" * 60, flush=True)

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
