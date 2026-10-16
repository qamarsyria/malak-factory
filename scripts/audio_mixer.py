"""Fasih-TTS-V1: النموذج العربي المتخصص."""
import os
import subprocess
import tempfile
import wave
from pathlib import Path

# تفعيل الترخيص
os.environ["COQUI_TOS_AGREED"] = "1"

# استيراد Fasih
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts
import torch

# ============================================================
# تحميل Fasih-TTS-V1
# ============================================================
def load_fasih():
    """يحمل نموذج Fasih-TTS-V1 المتخصص بالعربية."""
    from huggingface_hub import snapshot_download

    print("⬇️  تحميل Fasih-TTS-V1...")
    model_path = snapshot_download(
        repo_id="NightPrince/Fasih-TTS-V1",
        cache_dir="/tmp/fasih_v1"
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
    return model, config, model_path


# ============================================================
# توليد السرد
# ============================================================
def generate_fasih(text, out_mp3, model, config):
    """يولّد السرد بصوت Fasih-TTS-V1 (عربي متخصص)."""
    import numpy as np
    from TTS.tts.models.xtts import load_audio

    clean = text.strip()
    if not clean:
        return None

    print(f"   🔊 [Fasih] {clean[:60]}...")

    try:
        # Fasih-TTS-V1 يستخدم CATT diacritization تلقائياً
        out = model.synthesize(
            clean,
            config,
            speaker_wav=None,  # النموذج أحادي المتحدث (لا يحتاج مرجع)
            gpt_cond_len=6,
            language="ar",
        )

        # تحويل Tensor → int16
        wav_data = out["wav"]
        if hasattr(wav_data, "cpu"):
            wav_data = wav_data.cpu().numpy()
        wav_int16 = (np.clip(wav_data, -1.0, 1.0) * 32767).astype(np.int16)

        # حفظ WAV
        wav_path = out_mp3.replace(".mp3", "_raw.wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(wav_int16.tobytes())

        # تحويل إلى MP3
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path,
             "-codec:a", "libmp3lame", "-b:a", "128k",
             out_mp3],
            check=True, capture_output=True, timeout=120,
        )

        duration = _get_duration(out_mp3)
        print(f"   ✅ {duration:.1f} ثانية")
        return duration

    except Exception as e:
        import traceback
        print(f"   ❌ فشل: {type(e).__name__}: {str(e)[:200]}")
        traceback.print_exc()
        return None


# ============================================================
# دوال مساعدة
# ============================================================
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


# ============================================================
# بناء الحلقة
# ============================================================
def build_episode(episode, out_path="out/episode-audio.mp3"):
    """يبني صوت الحلقة بـ Fasih-TTS-V1."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    # تحميل النموذج
    model, config, _ = load_fasih()

    segments = episode.get("segments", [])
    surah = episode.get("surah", 12)

    print(f"\n🎙️  بناء صوت الحلقة ({len(segments)} مقطع)")
    print(f"   السورة: {surah} | الصوت: Fasih-TTS-V1 (عربي متخصص)")
    print("=" * 60)

    temp_dir = Path(tempfile.mkdtemp())
    segment_files = []
    failed = 0

    for i, seg in enumerate(segments, 1):
        seg_type = seg["type"]
        seg_file = temp_dir / f"seg-{i:02d}.mp3"

        try:
            if seg_type == "quran":
                # الآيات من everyayah (السديس)
                ayahs = seg.get("ayah_numbers") or []
                for n, a in enumerate(ayahs, 1):
                    f = temp_dir / f"ayah-{i:02d}-{n:02d}.mp3"
                    _fetch_quran(surah, a, str(f))
                    segment_files.append(str(f))
                continue

            elif seg_type in ("narration", "intro", "outro"):
                text = seg.get("text", "").strip()
                if not text:
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
