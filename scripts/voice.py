"""توليد الصوت العربي — Piper (صوت رجل) مع بدائل."""
import os
import subprocess
import time
from pathlib import Path

PIPER_MODEL = "/tmp/piper/ar_JO-kareem-medium.onnx"
PIPER_CONFIG = "/tmp/piper/ar_JO-kareem-medium.onnx.json"


def _piper_to_wav(text, wav_path):
    if not os.path.exists(PIPER_MODEL):
        raise RuntimeError(f"نموذج Piper غير موجود")
    if not os.path.exists(PIPER_CONFIG):
        raise RuntimeError(f"ملف إعدادات Piper غير موجود")

    proc = subprocess.run(
        [
            "python", "-m", "piper",
            "--model", PIPER_MODEL,
            "--config", PIPER_CONFIG,
            "--output_file", wav_path,
        ],
        input=text.encode("utf-8"),
        capture_output=True,
        timeout=180,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"Piper فشل: {err}")


def _wav_to_mp3(wav_path, mp3_path):
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", wav_path,
            "-codec:a", "libmp3lame", "-q:a", "4",
            mp3_path,
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _espeak(text, out_mp3):
    wav = out_mp3.replace(".mp3", ".wav")
    subprocess.run(
        ["espeak-ng", "-v", "ar", "-s", "145", "-p", "35", "-w", wav, text],
        check=True, capture_output=True, timeout=60,
    )
    _wav_to_mp3(wav, out_mp3)


def _gtts(text, out_path):
    from gtts import gTTS
    gTTS(text=text, lang="ar", slow=False).save(out_path)


def _is_ok(out_path):
    p = Path(out_path)
    return p.exists() and p.stat().st_size > 2048


def generate(text, out_path="out/voice.mp3"):
    if not text or not text.strip():
        raise ValueError("النص فارغ")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    text = text.strip()

    # 1) Piper (صوت رجل — الأفضل)
    try:
        print("🔊 محاولة Piper (صوت رجل)...")
        wav = out_path.replace(".mp3", ".wav")
        _piper_to_wav(text, wav)
        _wav_to_mp3(wav, out_path)
        if _is_ok(out_path):
            size = Path(out_path).stat().st_size
            print(f"✅ الصوت (Piper رجل): {out_path} ({size} بايت)")
            return out_path
    except Exception as e:
        print(f"⚠️  Piper فشل: {str(e)[:150]}")

    # 2) espeak-ng (صوت رجل — جودة أقل)
    try:
        print("🔊 محاولة espeak-ng (صوت رجل)...")
        _espeak(text, out_path)
        if _is_ok(out_path):
            size = Path(out_path).stat().st_size
            print(f"✅ الصوت (espeak): {out_path} ({size} بايت)")
            return out_path
    except Exception as e:
        print(f"⚠️  espeak فشل: {str(e)[:150]}")

    # 3) gTTS (صوت أنثوي — آخر حل)
    print("⚠️  استخدام gTTS (صوت أنثوي)")
    for attempt in range(1, 4):
        try:
            _gtts(text, out_path)
            if _is_ok(out_path):
                size = Path(out_path).stat().st_size
                print(f"✅ الصوت (gTTS): {out_path} ({size} بايت)")
                return out_path
        except Exception as e:
            print(f"⚠️  gTTS {attempt}/3 فشل: {e}")
            time.sleep(2)

    raise RuntimeError("كل محركات الصوت فشلت")
