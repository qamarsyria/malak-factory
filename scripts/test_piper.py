"""اختبار Piper TTS: length-scale + sample rate."""
import subprocess
from pathlib import Path
import requests
import base64
import wave
from config import GEMINI_API_KEY


TEST_TEXT = "يقول الله تعالى مبيناً عظمة كتابه، حيث تبدأ السورة بالحروف المقطعة."
PIPER_MODEL = "/tmp/piper/ar_JO-kareem-medium.onnx"
PIPER_CONFIG = "/tmp/piper/ar_JO-kareem-medium.onnx.json"


def piper_generate(name, length_scale):
    """يولّد بـ Piper مع length_scale محدد."""
    out_wav = f"out/test-{name}.wav"
    out_mp3 = f"out/test-{name}.mp3"

    print(f"\n{'='*60}")
    print(f"🎵 {name} (length_scale={length_scale})")
    print(f"{'='*60}")

    # استخدم --length-scale (شرطة، الصيغة الصحيحة)
    cmd = [
        "python", "-m", "piper",
        "--model", PIPER_MODEL,
        "--config", PIPER_CONFIG,
        "--output_file", out_wav,
        "--length-scale", str(length_scale),
    ]
    print(f"الأمر: {' '.join(cmd)}")

    proc = subprocess.run(
        cmd,
        input=TEST_TEXT.encode("utf-8"),
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="ignore")[:400]
        print(f"❌ فشل: {err}")
        return None

    if not Path(out_wav).exists():
        print(f"❌ الملف غير موجود")
        return None

    # معلومات الملف
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=sample_rate,duration,channels",
         "-of", "json", out_wav],
        capture_output=True, text=True,
    )
    print(f"📊 WAV: {probe.stdout[:300]}")

    # تحويل إلى MP3
    subprocess.run(
        ["ffmpeg", "-y", "-i", out_wav,
         "-codec:a", "libmp3lame", "-q:a", "4",
         out_mp3],
        check=True, capture_output=True, timeout=60,
    )
    print(f"✅ {out_mp3}")


def gemini_generate():
    """يولّد بـ Gemini TTS (للمقارنة)."""
    out_mp3 = "out/test-gemini.mp3"
    print(f"\n{'='*60}")
    print(f"🎵 Gemini TTS (Charon)")
    print(f"{'='*60}")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash-preview-tts:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "contents": [{"parts": [{"text": TEST_TEXT}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": "Charon"}
                }
            }
        }
    }
    r = requests.post(url, json=body, timeout=120)
    if r.status_code != 200:
        print(f"❌ HTTP {r.status_code}: {r.text[:200]}")
        return

    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        print(f"❌ no candidates")
        return
    parts = (cands[0].get("content") or {}).get("parts") or []
    inline = parts[0].get("inlineData") or {}
    audio_b64 = inline.get("data")
    if not audio_b64:
        print(f"❌ no audio")
        return

    raw = base64.b64decode(audio_b64)
    wav = out_mp3.replace(".mp3", ".wav")
    with wave.open(wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(raw)

    subprocess.run(
        ["ffmpeg", "-y", "-i", wav,
         "-codec:a", "libmp3lame", "-q:a", "4",
         out_mp3],
        check=True, capture_output=True, timeout=60,
    )
    print(f"✅ {out_mp3}")


def main():
    Path("out").mkdir(exist_ok=True)
    print("=" * 60)
    print("🧪 اختبار Piper TTS")
    print("=" * 60)
    print(f"النص: {TEST_TEXT}")

    # 3 اختبارات Piper
    piper_generate("piper-default", 1.0)
    piper_generate("piper-slow15", 1.5)
    piper_generate("piper-slow20", 2.0)

    # Gemini TTS للمقارنة
    gemini_generate()

    print("\n" + "=" * 60)
    print("✅ اكتمل — اسمع الملفات:")
    print("   out/test-piper-default.mp3")
    print("   out/test-piper-slow15.mp3")
    print("   out/test-piper-slow20.mp3")
    print("   out/test-gemini.mp3")
    print("=" * 60)


if __name__ == "__main__":
    main()
