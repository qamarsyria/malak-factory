"""توليد صوت السرد باستخدام XTTS v2 مع استنساخ صوت السديس."""
import os
import re
import subprocess
import tempfile
import wave
from pathlib import Path
import requests
import torch

# استيراد TTS
os.environ["COQUI_TOS_AGREED"] = "1"
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts

from config import QURAN_RECITER, QURAN_BASE

# ============================================================
# إعدادات
# ============================================================
# مسار ملف صوت السديس المرجعي (يجب أن ترفعه إلى المستودع)
REFERENCE_AUDIO = "assets/audio/reference/sudais_reference.wav"

# مسار موديل XTTS v2 (سيتم تحميله تلقائياً)
XTTS_MODEL_DIR = "/tmp/xtts_model"

# صيغة موحّدة للدمج
TARGET_SAMPLE_RATE = "44100"
TARGET_CHANNELS = "1"
TARGET_BITRATE = "128k"


# ============================================================
# تحميل موديل XTTS v2 (مرة واحدة)
# ============================================================
def load_xtts_model():
    """يحمل موديل XTTS v2 من HuggingFace."""
    print("⬇️  تحميل موديل XTTS v2...")

    # استخدام snapshot_download لتحميل الموديل
    from huggingface_hub import snapshot_download
    model_path = snapshot_download(
        repo_id="coqui/XTTS-v2",
        cache_dir=XTTS_MODEL_DIR
    )

    # تحميل الإعدادات
    config = XttsConfig()
    config.load_json(f"{model_path}/config.json")

    # تهيئة الموديل
    model = Xtts.init_from_config(config)
    model.load_checkpoint(
        config,
        checkpoint_dir=model_path,
        use_deepspeed=False,
    )
    model.cpu()  # تشغيل على CPU (لا يحتاج GPU)
    model.eval()

    print("✅ XTTS v2 جاهز")
    return model, config


# ============================================================
# توليد الصوت بـ XTTS v2
# ============================================================
def generate_narration_audio(segment, out_path, model, config):
    """يولّد صوت السرد باستخدام XTTS v2 مع صوت السديس."""
    text = segment.get("text", "").strip()
    if not text:
        return None

    print(f"   🔊 [narration] {text[:60]}...")

    try:
        # توليد الصوت
        # XTTS v2 يدعم النص العربي مع التشكيل
        outputs = model.synthesize(
            text,
            config,
            speaker_wav=REFERENCE_AUDIO,  # <-- صوت السديس
            language="ar",                # العربية
        )

        # حفظ الملف
        wav_path = out_path.replace(".mp3", ".wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)  # XTTS v2 ينتج 24000 هرتز
            wf.writeframes(outputs["wav"])

        # تحويل إلى mp3
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path,
             "-ar", TARGET_SAMPLE_RATE,
             "-ac", TARGET_CHANNELS,
             "-codec:a", "libmp3lame",
             "-b:a", TARGET_BITRATE,
             out_path],
            check=True, capture_output=True, timeout=120,
        )

        duration = len(outputs["wav"]) / (24000 * 2)  # 2 بايت لكل عينة
        print(f"   ✅ {duration:.1f} ثانية")
        return duration

    except Exception as e:
        print(f"   ❌ فشل: {str(e)[:150]}")
        return None


# ============================================================
# دوال بناء الآيات (كما هي)
# ============================================================
def _fetch_quran_ayah(surah, ayah, out_path):
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE}/{QURAN_RECITER}/{filename}"

    print(f"   📖 {surah}:{ayah} → {filename}")
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")

    Path(out_path).write_bytes(r.content)

    # توحيد الصيغة
    subprocess.run(
        ["ffmpeg", "-y", "-i", out_path,
         "-ar", TARGET_SAMPLE_RATE,
         "-ac", TARGET_CHANNELS,
         "-codec:a", "libmp3lame",
         "-b:a", TARGET_BITRATE,
         out_path + ".tmp"],
        check=True, capture_output=True, timeout=120,
    )
    os.replace(out_path + ".tmp", out_path)


# ============================================================
# بناء الحلقة كاملة
# ============================================================
def build_full_audio(episode, out_path="out/episode-audio.mp3"):
    """يبني صوت الحلقة كاملة."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    segments = episode.get("segments", [])
    surah = episode.get("surah", 12)

    print(f"\n🎙️  بناء صوت الحلقة ({len(segments)} مقطع)")
    print(f"   السورة: {surah} | القارئ: {QURAN_RECITER}")
    print(f"   السرد: XTTS v2 + صوت السديس")
    print("=" * 60)

    # تحميل الموديل مرة واحدة
    model, config = load_xtts_model()

    temp_dir = Path(tempfile.mkdtemp())
    segment_files = []
    failed = 0

    for i, seg in enumerate(segments, 1):
        seg_type = seg["type"]
        seg_file = temp_dir / f"seg-{i:02d}.mp3"

        try:
            if seg_type == "quran":
                ayah_numbers = seg.get("ayah_numbers") or []
                if len(ayah_numbers) == 1:
                    _fetch_quran_ayah(surah, ayah_numbers[0], str(seg_file))
                else:
                    # دمج آيات متعددة
                    temp_ayahs = []
                    for n, ayah in enumerate(ayah_numbers, 1):
                        f = temp_dir / f"ayah-{n:02d}.mp3"
                        _fetch_quran_ayah(surah, ayah, str(f))
                        temp_ayahs.append(str(f))
                    with open(temp_dir / "concat.txt", "w") as f:
                        for tf in temp_ayahs:
                            f.write(f"file '{tf}'\n")
                    subprocess.run(
                        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                         "-i", str(temp_dir / "concat.txt"), "-c", "copy",
                         str(seg_file)],
                        check=True, capture_output=True, timeout=120,
                    )
                duration = 0  # سيتم حسابه لاحقاً

            elif seg_type in ("narration", "intro", "outro"):
                duration = generate_narration_audio(
                    seg, str(seg_file), model, config
                )
            else:
                duration = None

            if seg_file.exists():
                segment_files.append(str(seg_file))
            else:
                failed += 1

        except Exception as e:
            print(f"   ❌ فشل المقطع {i}: {str(e)[:150]}")
            failed += 1

    if not segment_files:
        raise RuntimeError("لم يُولَّد صوت")

    # دمج كل المقاطع
    print(f"\n🔗 دمج {len(segment_files)} مقطع...")
    concat_file = temp_dir / "concat_final.txt"
    with open(concat_file, "w") as f:
        for sf in segment_files:
            f.write(f"file '{sf}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_file), "-c", "copy", out_path],
        check=True, capture_output=True, timeout=180,
    )

    print(f"\n✅ الحلقة: {out_path}")
    print(f"   نجح: {len(segment_files)} | فشل: {failed}")
    return out_path


if __name__ == "__main__":
    import json
    ep_file = "data/stories/yusuf/episode-01.json"
    episode = json.loads(Path(ep_file).read_text(encoding="utf-8"))
    build_full_audio(episode)
