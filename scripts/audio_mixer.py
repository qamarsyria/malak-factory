"""دمج صوت السرد (XTTS v2 + السديس) + الآيات (everyayah السديس)."""
import os
import re
import subprocess
import tempfile
import wave
from pathlib import Path
import numpy as np
import requests

from config import QURAN_BASE


# ============================================================
# إعدادات
# ============================================================
SUDAIS = "Abdurrahmaan_As-Sudais_192kbps"

REFERENCE_SURAH = 12
REFERENCE_AYAH = 4

REFERENCE_DIR = "assets/audio/reference"
REFERENCE_WAV = f"{REFERENCE_DIR}/sudais_ref.wav"

XTTS_DIR = "/tmp/xtts_v2"

TARGET_SAMPLE_RATE = "44100"
TARGET_CHANNELS = "1"
TARGET_BITRATE = "128k"


# ============================================================
# 1. تحميل مرجع السديس
# ============================================================
def download_reference():
    Path(REFERENCE_DIR).mkdir(parents=True, exist_ok=True)

    if Path(REFERENCE_WAV).exists() and Path(REFERENCE_WAV).stat().st_size > 10240:
        print(f"✅ المرجع موجود: {REFERENCE_WAV}")
        return REFERENCE_WAV

    filename = f"{REFERENCE_SURAH:03d}{REFERENCE_AYAH:03d}.mp3"
    url = f"{QURAN_BASE}/{SUDAIS}/{filename}"

    print(f"⬇️  تحميل مرجع السديس: {filename}")
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} لمرجع السديس")

    temp_mp3 = f"{REFERENCE_DIR}/sudais_ref.mp3"
    Path(temp_mp3).write_bytes(r.content)

    subprocess.run(
        ["ffmpeg", "-y", "-i", temp_mp3,
         "-ar", "22050",
         "-ac", "1",
         REFERENCE_WAV],
        check=True, capture_output=True, timeout=60,
    )

    size = Path(REFERENCE_WAV).stat().st_size
    print(f"✅ المرجع جاهز: {REFERENCE_WAV} ({size/1024:.1f} KB)")
    return REFERENCE_WAV


# ============================================================
# 2. تحميل XTTS v2
# ============================================================
def load_xtts():
    os.environ["COQUI_TOS_AGREED"] = "1"

    from huggingface_hub import snapshot_download
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts

    print("⬇️  تحميل XTTS v2...")
    model_path = snapshot_download(
        repo_id="coqui/XTTS-v2",
        cache_dir=XTTS_DIR,
    )

    config = XttsConfig()
    config.load_json(f"{model_path}/config.json")

    model = Xtts.init_from_config(config)
    model.load_checkpoint(
        config,
        checkpoint_dir=model_path,
        use_deepspeed=False,
    )
    model.eval()
    print("✅ XTTS v2 جاهز")
    return model, config, model_path


# ============================================================
# 3. توليد السرد بصوت السديس
# ============================================================
def generate_narration(text, out_mp3, model, config):
    """يولّد السرد بصوت السديس (استنساخ)."""
    clean = _clean_text(text)
    if not clean:
        return None

    print(f"   🔊 [narration] {clean[:60]}...")

    try:
        out = model.synthesize(
            clean,
            config,
            speaker_wav=REFERENCE_WAV,
            gpt_cond_len=6,
            language="ar",
        )

        # ⚠️ الإصلاح: XTTS يُعيد Tensor، نحوله إلى int16
        wav_data = out["wav"]
        if hasattr(wav_data, "cpu"):
            wav_data = wav_data.cpu().numpy()
        wav_int16 = (np.clip(wav_data, -1.0, 1.0) * 32767).astype(np.int16)

        wav_path = out_mp3.replace(".mp3", "_raw.wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(wav_int16.tobytes())

        _normalize(wav_path, out_mp3)

        duration = _duration(out_mp3)
        print(f"   ✅ {duration:.1f} ثانية")
        return duration

    except Exception as e:
        import traceback
        print(f"   ❌ فشل: {type(e).__name__}: {str(e)[:200]}")
        traceback.print_exc()
        return None


# ============================================================
# 4. جلب الآيات
# ============================================================
def fetch_quran(surah, ayah, out_path):
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE}/{SUDAIS}/{filename}"

    print(f"   📖 {surah}:{ayah} → {filename}")

    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")

    temp = out_path.replace(".mp3", "_raw.mp3")
    Path(temp).write_bytes(r.content)
    _normalize(temp, out_path)


# ============================================================
# دوال مساعدة
# ============================================================
def _clean_text(text):
    text = re.sub(r"[ۖۗۘۙۚۛ]", "", text)
    text = text.replace("۝", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize(input_path, output_path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path,
         "-ar", TARGET_SAMPLE_RATE,
         "-ac", TARGET_CHANNELS,
         "-codec:a", "libmp3lame",
         "-b:a", TARGET_BITRATE,
         output_path],
        check=True, capture_output=True, timeout=120,
    )


def _duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ============================================================
# 5. بناء الحلقة
# ============================================================
def build_full_audio(episode, out_path="out/episode-audio.mp3"):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    segments = episode.get("segments", [])
    surah = episode.get("surah", 12)

    print(f"\n🎙️  بناء صوت الحلقة ({len(segments)} مقطع)")
    print(f"   السورة: {surah} | الصوت: السديس")
    print("=" * 60)

    download_reference()
    model, config, _ = load_xtts()

    temp_dir = Path(tempfile.mkdtemp())
    segment_files = []
    failed_narration = 0
    failed_quran = 0

    for i, seg in enumerate(segments, 1):
        seg_type = seg["type"]
        seg_file = temp_dir / f"seg-{i:02d}.mp3"

        try:
            if seg_type == "quran":
                ayahs = seg.get("ayah_numbers") or []
                if len(ayahs) == 1:
                    fetch_quran(surah, ayahs[0], str(seg_file))
                else:
                    parts = []
                    for n, a in enumerate(ayahs, 1):
                        f = temp_dir / f"ayah-{n:02d}.mp3"
                        fetch_quran(surah, a, str(f))
                        parts.append(str(f))
                    with open(temp_dir / "cc.txt", "w") as f:
                        for p in parts:
                            f.write(f"file '{p}'\n")
                    subprocess.run(
                        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                         "-i", str(temp_dir / "cc.txt"), "-c", "copy",
                         str(seg_file)],
                        check=True, capture_output=True, timeout=120,
                    )

            elif seg_type in ("narration", "intro", "outro"):
                text = seg.get("text", "").strip()
                if not text:
                    print(f"   ⏭️  [{i}] {seg_type} — بدون نص")
                    continue
                result = generate_narration(text, str(seg_file), model, config)
                if not result:
                    failed_narration += 1

            if seg_file.exists() and seg_file.stat().st_size > 2048:
                segment_files.append(str(seg_file))
            else:
                if seg_type == "quran":
                    failed_quran += 1

        except Exception as e:
            print(f"   ❌ فشل المقطع {i}: {type(e).__name__}: {str(e)[:200]}")
            if seg_type == "quran":
                failed_quran += 1
            else:
                failed_narration += 1

    print(f"\n📊 نتيجة التوليد:")
    print(f"   نجح: {len(segment_files)} مقطع")
    print(f"   فشل سرد: {failed_narration}")
    print(f"   فشل قرآن: {failed_quran}")

    if not segment_files:
        raise RuntimeError("لم يُولَّد صوت")

    print(f"\n🔗 دمج {len(segment_files)} مقطع...")
    with open(temp_dir / "final.txt", "w") as f:
        for sf in segment_files:
            f.write(f"file '{sf}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(temp_dir / "final.txt"), "-c", "copy", out_path],
        check=True, capture_output=True, timeout=180,
    )

    duration = _duration(out_path)
    print(f"\n✅ الحلقة: {out_path}")
    print(f"   المدة: {duration/60:.1f} دقيقة")
    return out_path


if __name__ == "__main__":
    import json
    ep = json.loads(Path("data/stories/yusuf/episode-01.json").read_text(encoding="utf-8"))
    build_full_audio(ep)
