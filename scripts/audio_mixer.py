"""دمج صوت السرد (Piper TTS) + الآيات (everyayah)."""
import re
import subprocess
import tempfile
import time
from pathlib import Path
import requests

from config import QURAN_RECITER, QURAN_BASE


# ============================================================
# إعدادات
# ============================================================
PIPER_MODEL = "/tmp/piper/ar_JO-kareem-medium.onnx"
PIPER_CONFIG = "/tmp/piper/ar_JO-kareem-medium.onnx.json"
# length_scale: 1.0 = عادي | 1.3 = بطيء 30% | 1.5 = بطيء 50%
PIPER_LENGTH_SCALE = 1.3


# ============================================================
# دوال مساعدة
# ============================================================
def _clean_for_tts(text):
    text = re.sub(r"[ۖۗۘۙۚۛ]", "", text)
    text = text.replace("۝", " ")
    return text.strip()


def _get_duration(file_path):
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
# Piper TTS — للسرد
# ============================================================
def _piper_tts(text, out_mp3, length_scale=PIPER_LENGTH_SCALE):
    """يولّد صوت بـ Piper، ثم يحوّله mp3."""
    if not Path(PIPER_MODEL).exists():
        raise RuntimeError(f"نموذج Piper غير موجود: {PIPER_MODEL}")

    clean = _clean_for_tts(text)
    if not clean:
        raise RuntimeError("النص فارغ")

    wav_path = out_mp3.replace(".mp3", ".wav")

    # 1) Piper يولّد wav مباشرة
    proc = subprocess.run(
        ["python", "-m", "piper",
         "--model", PIPER_MODEL,
         "--config", PIPER_CONFIG,
         "--output_file", wav_path,
         "--length_scale", str(length_scale)],
        input=clean.encode("utf-8"),
        capture_output=True,
        timeout=180,
    )

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"Piper فشل: {err}")

    if not Path(wav_path).exists() or Path(wav_path).stat().st_size < 2048:
        raise RuntimeError("Piper: ملف صغير جداً")

    # 2) تحويل wav → mp3
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path,
         "-codec:a", "libmp3lame", "-q:a", "4",
         out_mp3],
        check=True, capture_output=True, timeout=60,
    )


# ============================================================
# everyayah — للآيات
# ============================================================
def _fetch_quran_ayah(surah, ayah, out_path):
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE}/{QURAN_RECITER}/{filename}"

    print(f"   📖 {surah}:{ayah} → {filename}")
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} — {url}")

    Path(out_path).write_bytes(r.content)
    if Path(out_path).stat().st_size < 2048:
        raise RuntimeError("ملف صغير جداً")


# ============================================================
# بناء المقاطع
# ============================================================
def generate_quran_audio(segment, out_path):
    ayah_numbers = segment.get("ayah_numbers") or []
    surah = segment.get("_surah") or 12

    if not ayah_numbers:
        raise RuntimeError("لا يوجد أرقام آيات")

    if len(ayah_numbers) == 1:
        _fetch_quran_ayah(surah, ayah_numbers[0], out_path)
        duration = _get_duration(out_path)
        print(f"   ✅ {duration:.1f} ثانية")
        return duration

    temp_dir = Path(tempfile.mkdtemp())
    files = []
    for n, ayah in enumerate(ayah_numbers, 1):
        f = temp_dir / f"ayah-{n:02d}.mp3"
        _fetch_quran_ayah(surah, ayah, str(f))
        files.append(str(f))
        time.sleep(0.2)

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
    text = segment.get("text", "").strip()
    if not text:
        return None

    print(f"   🔊 [narration] {text[:60]}...")
    try:
        _piper_tts(text, out_path)
        duration = _get_duration(out_path)
        print(f"   ✅ {duration:.1f} ثانية")
        return duration
    except Exception as e:
        print(f"   ❌ فشل: {str(e)[:150]}")
        return None


# ============================================================
# بناء الحلقة كاملة
# ============================================================
def build_full_audio(episode, out_path="out/episode-audio.mp3"):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    segments = episode.get("segments", [])
    surah = episode.get("surah", 12)

    print(f"\n🎙️  بناء صوت الحلقة ({len(segments)} مقطع)")
    print(f"   السورة: {surah} | القارئ: {QURAN_RECITER}")
    print(f"   السرد: Piper (length_scale={PIPER_LENGTH_SCALE})")
    print("=" * 60)

    temp_dir = Path(tempfile.mkdtemp())
    segment_files = []
    failed = 0

    for i, seg in enumerate(segments, 1):
        seg_type = seg["type"]
        seg_file = temp_dir / f"seg-{i:02d}.mp3"
        seg["_surah"] = surah

        try:
            if seg_type == "quran":
                duration = generate_quran_audio(seg, str(seg_file))
            elif seg_type in ("narration", "intro", "outro"):
                duration = generate_narration_audio(seg, str(seg_file))
            else:
                duration = None

            if duration:
                segment_files.append(str(seg_file))
            else:
                failed += 1

        except Exception as e:
            print(f"   ❌ فشل المقطع {i}: {str(e)[:150]}")
            failed += 1

    if not segment_files:
        raise RuntimeError("لم يُولَّد أي صوت")

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
    print(f"   المدة: {duration/60:.1f} دقيقة ({duration:.0f} ث)")
    print(f"   نجح: {len(segment_files)} | فشل: {failed}")
    return out_path


if __name__ == "__main__":
    import json
    ep_file = "data/stories/yusuf/episode-01.json"
    episode = json.loads(Path(ep_file).read_text(encoding="utf-8"))
    build_full_audio(episode)
