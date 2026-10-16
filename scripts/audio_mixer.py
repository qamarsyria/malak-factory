"""Fasih-TTS-V1 + مرجع السديس + everyayah للسرد والآيات."""
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

TARGET_SAMPLE_RATE = "44100"
TARGET_CHANNELS = "1"
TARGET_BITRATE = "128k"


# ============================================================
# دوال مساعدة
# ============================================================
def _clean_text(text):
    text = re.sub(r"[ۖۗۘۙۚۛ]", "", text)
    text = text.replace("۝", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _get_duration(path):
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
        raise RuntimeError(f"HTTP {r.status_code}")

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
    print(f"✅ المرجع: {REFERENCE_WAV} ({size/1024:.1f} KB)")
    return REFERENCE_WAV


# ============================================================
# 2. تحميل Fasih-TTS-V1
# ============================================================
def load_fasih():
    os.environ["COQUI_TOS_AGREED"] = "1"

    from huggingface_hub import snapshot_download
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts

    print("⬇️  تحميل Fasih-TTS-V1...")
    model_path = snapshot_download(
        repo_id="NightPrince/Fasih-TTS-V1",
        cache_dir="/tmp/fasih_v1",
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
    print("✅ Fasih-TTS-V1 جاهز")
    return model, config


# ============================================================
# 3. توليد السرد (مع مرجع السديس + تقسيم جمل)
# ============================================================
def split_into_sentences(text):
    """يقسّم النص إلى جمل."""
    text = re.sub(r'([.!؟?])\s+', r'\1\n', text)
    text = re.sub(r'([۔])\s+', r'\1\n', text)
    sentences = [s.strip() for s in text.split('\n') if s.strip()]
    return sentences


def generate_fasih(text, out_mp3, model, config):
    """يولّد السرد مع استنساخ صوت السديس وتقسيم الجمل."""
    clean = _clean_text(text)
    if not clean:
        return None

    print(f"   🔊 [Fasih] {clean[:60]}...")

    # 1) تقسيم إلى جمل
    sentences = split_into_sentences(clean)
    if not sentences:
        sentences = [clean]

    # 2) دمج الجمل القصيرة
    merged = []
    current = ""
    for s in sentences:
        if len(current) + len(s) < 200:
            current = (current + " " + s).strip()
        else:
            if current:
                merged.append(current)
            current = s
    if current:
        merged.append(current)

    print(f"   📝 {len(merged)} جملة")

    temp_dir = Path(tempfile.mkdtemp())
    sentence_files = []

    for idx, sentence in enumerate(merged, 1):
        if len(sentence) < 5:
            continue

        try:
            out = model.synthesize(
                sentence,
                config,
                speaker_wav=REFERENCE_WAV,   # ← مرجع السديس
                gpt_cond_len=6,
                language="ar",
            )

            wav_data = out["wav"]
            if hasattr(wav_data, "cpu"):
                wav_data = wav_data.cpu().numpy()
            wav_int16 = (np.clip(wav_data, -1.0, 1.0) * 32767).astype(np.int16)

            sfile = temp_dir / f"sentence-{idx:03d}.wav"
            with wave.open(str(sfile), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(wav_int16.tobytes())

            # صمت 250ms
            silence = temp_dir / f"silence-{idx:03d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi",
                 "-i", "anullsrc=channel_layout=mono:sample_rate=24000",
                 "-t", "0.25", str(silence)],
                check=True, capture_output=True, timeout=30,
            )

            sentence_files.append(str(sfile))
            sentence_files.append(str(silence))

        except Exception as e:
            print(f"   ⚠️  جملة {idx} فشلت: {str(e)[:100]}")

    if not sentence_files:
        return None

    # 3) دمج الجمل
    wav_raw = out_mp3.replace(".mp3", "_raw.wav")
    with open(temp_dir / "sent_concat.txt", "w") as f:
        for sf in sentence_files:
            f.write(f"file '{sf}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(temp_dir / "sent_concat.txt"),
         "-c", "copy", wav_raw],
        check=True, capture_output=True, timeout=180,
    )

    _normalize(wav_raw, out_mp3)
    duration = _get_duration(out_mp3)
    print(f"   ✅ {duration:.1f} ثانية")
    return duration


# ============================================================
# 4. جلب الآيات
# ============================================================
def _fetch_quran(surah, ayah, out_path):
    """يجلب آية من everyayah (السديس)."""
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
# 5. بناء الحلقة
# ============================================================
def build_episode(episode, out_path="out/episode-audio.mp3"):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    download_reference()
    model, config = load_fasih()

    segments = episode.get("segments", [])
    surah = episode.get("surah", 12)

    print(f"\n🎙️  بناء صوت الحلقة ({len(segments)} مقطع)")
    print(f"   السورة: {surah} | الصوت: Fasih-TTS-V1 + السديس")
    print("=" * 60)

    temp_dir = Path(tempfile.mkdtemp())
    segment_files = []
    failed = 0

    for i, seg in enumerate(segments, 1):
        seg_type = seg["type"]
        seg_file = temp_dir / f"seg-{i:02d}.mp3"

        try:
            if seg_type == "quran":
                ayahs = seg.get("ayah_numbers") or []
                if len(ayahs) == 1:
                    _fetch_quran(surah, ayahs[0], str(seg_file))
                    segment_files.append(str(seg_file))
                else:
                    for n, a in enumerate(ayahs, 1):
                        f = temp_dir / f"ayah-{i:02d}-{n:02d}.mp3"
                        _fetch_quran(surah, a, str(f))
                        segment_files.append(str(f))

            elif seg_type in ("narration", "intro", "outro"):
                text = seg.get("text", "").strip()
                if not text:
                    print(f"   ⏭️  [{i}] {seg_type} — بدون نص")
                    continue
                duration = generate_fasih(text, str(seg_file), model, config)
                if duration:
                    segment_files.append(str(seg_file))
                else:
                    failed += 1

        except Exception as e:
            print(f"   ❌ فشل المقطع {i}: {str(e)[:150]}")
            failed += 1

    if not segment_files:
        raise RuntimeError("لم يُولَّد صوت")

    # دمج
    print(f"\n🔗 دمج {len(segment_files)} مقطع...")
    with open(temp_dir / "final.txt", "w") as f:
        for sf in segment_files:
            f.write(f"file '{sf}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(temp_dir / "final.txt"), "-c", "copy", out_path],
        check=True, capture_output=True, timeout=180,
    )

    duration = _get_duration(out_path)
    print(f"\n✅ الحلقة: {out_path}")
    print(f"   المدة: {duration/60:.1f} دقيقة")
    print(f"   نجح: {len(segment_files)} | فشل: {failed}")
    return out_path


if __name__ == "__main__":
    import json
    ep = json.loads(Path("data/stories/yusuf/episode-01.json").read_text(encoding="utf-8"))
    build_episode(ep)
