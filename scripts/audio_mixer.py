"""دمج صوت الراوي + الآيات + القرآن الحقيقي من everyayah."""
import base64
import re
import subprocess
import tempfile
import time
import wave
from pathlib import Path
import requests
from config import (
    GEMINI_API_KEY, QURAN_RECITER, QURAN_BASE,
    TTS_DELAY_BETWEEN_SEGMENTS,
)


def _clean_for_tts(text):
    """ينظف النص العربي للـ TTS (يزيل التشكيل الزائد)."""
    text = re.sub(r"[\u064B-\u0652\u0670\u0640]", "", text)
    text = re.sub(r"[ۖۗۘۙۚۛ]", "", text)
    text = text.replace("۝", " ")
    return text.strip()


def _get_duration(file_path):
    """يرجع مدة الملف بالثواني."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", file_path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0


# ============================================================
# 1) Gemini TTS — للسرد
# ============================================================
def _gemini_tts(text, out_mp3, voice="Charon"):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY غير موجود")

    clean = _clean_for_tts(text)
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash-preview-tts:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "contents": [{"parts": [{"text": clean}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice}
                }
            }
        }
    }
    r = requests.post(url, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError("لا يوجد candidates")
    parts = (cands[0].get("content") or {}).get("parts") or []
    if not parts:
        raise RuntimeError("لا يوجد parts")
    inline = parts[0].get("inlineData") or {}
    audio_b64 = inline.get("data")
    if not audio_b64:
        raise RuntimeError("لا يوجد audio data")
    raw = base64.b64decode(audio_b64)
    wav = out_mp3.replace(".mp3", ".wav")
    with wave.open(wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(raw)
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav, "-codec:a", "libmp3lame",
         "-q:a", "4", out_mp3],
        check=True, capture_output=True, timeout=60,
    )


# ============================================================
# 2) everyayah — للآيات
# ============================================================
def _fetch_quran_ayah(surah, ayah, out_path):
    """
    يحمّل آية من everyayah.
    صيغة الملف: {surah:03d}{ayah:03d}.mp3
    مثال: surah=12، ayah=1 → 012001.mp3
    """
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE}/{QURAN_RECITER}/{filename}"

    print(f"   📖 {surah}:{ayah} → {url.split('/')[-1]}")

    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} — {url}")

    Path(out_path).write_bytes(r.content)
    if Path(out_path).stat().st_size < 2048:
        raise RuntimeError("ملف صغير جداً")

    return out_path


# ============================================================
# 3) بناء المقاطع
# ============================================================
def generate_quran_audio(segment, out_path):
    """يولّد صوت آية أو عدة آيات من everyayah."""
    ayah_numbers = segment.get("ayah_numbers") or []
    surah = segment.get("surah")  # قد لا يكون موجوداً

    if not surah:
        # استخرجه من الـ parent (episode)
        surah = segment.get("_surah") or 12

    if not ayah_numbers:
        raise RuntimeError("لا يوجد أرقام آيات")

    if len(ayah_numbers) == 1:
        # آية واحدة
        _fetch_quran_ayah(surah, ayah_numbers[0], out_path)
        duration = _get_duration(out_path)
        print(f"   ✅ {duration:.1f} ثانية")
        return duration

    # عدة آيات — نحمّل كل واحدة، ثم ندمج
    temp_dir = Path(tempfile.mkdtemp())
    files = []
    for n, ayah in enumerate(ayah_numbers, 1):
        f = temp_dir / f"ayah-{n:02d}.mp3"
        _fetch_quran_ayah(surah, ayah, str(f))
        files.append(str(f))
        time.sleep(0.3)

    # دمج
    concat_file = temp_dir / "concat.txt"
    with open(concat_file, "w", encoding="utf-8") as cf:
        for f in files:
            cf.write(f"file '{f}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_file), "-c", "copy", out_path],
        check=True, capture_output=True, timeout=120,
    )

    duration = _get_duration(out_path)
    print(f"   ✅ {len(ayah_numbers)} آيات = {duration:.1f} ثانية")
    return duration


def generate_narration_audio(segment, out_path):
    """يولّد صوت سرد بـ Gemini TTS."""
    text = segment.get("text", "").strip()
    if not text:
        return None

    print(f"   🔊 [narration] {text[:60]}...")
    try:
        _gemini_tts(text, out_path, voice="Charon")
        duration = _get_duration(out_path)
        print(f"   ✅ {duration:.1f} ثانية")
        return duration
    except Exception as e:
        print(f"   ❌ فشل: {e}")
        return None


# ============================================================
# 4) بناء الحلقة كاملة
# ============================================================
def build_full_audio(episode, out_path="out/episode-audio.mp3"):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    segments = episode.get("segments", [])
    surah = episode.get("surah", 12)

    print(f"\n🎙️  بناء صوت الحلقة ({len(segments)} مقطع)")
    print(f"   السورة: {surah} | القارئ: {QURAN_RECITER}")
    print("=" * 60)

    temp_dir = Path(tempfile.mkdtemp())
    segment_files = []
    failed = 0

    for i, seg in enumerate(segments, 1):
        seg_type = seg["type"]
        seg_file = temp_dir / f"seg-{i:02d}.mp3"

        # إضافة السورة للـ segment للاستخدام لاحقاً
        seg["_surah"] = surah

        try:
            if seg_type == "quran":
                duration = generate_quran_audio(seg, str(seg_file))
            elif seg_type in ("narration", "intro", "outro"):
                duration = generate_narration_audio(seg, str(seg_file))
                time.sleep(TTS_DELAY_BETWEEN_SEGMENTS)
            else:
                duration = None

            if duration:
                segment_files.append(str(seg_file))
            else:
                failed += 1

        except Exception as e:
            print(f"   ❌ فشل المقطع {i}: {e}")
            failed += 1

    if not segment_files:
        raise RuntimeError("لم يُولَّد أي صوت")

    # دمج
    print(f"\n🔗 دمج {len(segment_files)} مقطع ({failed} فشل)...")
    concat_file = temp_dir / "concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for sf in segment_files:
            f.write(f"file '{sf}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_file), "-c", "copy", out_path],
        check=True, capture_output=True, timeout=180,
    )

    duration = _get_duration(out_path)
    print(f"\n✅ الحلقة الصوتية: {out_path}")
    print(f"   المدة: {duration/60:.1f} دقيقة ({duration:.0f} ثانية)")
    print(f"   نجح: {len(segment_files)} | فشل: {failed}")
    return out_path


if __name__ == "__main__":
    import json
    ep_file = "data/stories/yusuf/episode-01.json"
    episode = json.loads(Path(ep_file).read_text(encoding="utf-8"))
    build_full_audio(episode)
