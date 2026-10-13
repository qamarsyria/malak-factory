"""توليد الصوت العربي — Gemini TTS (احترافي) أولاً، ثم بدائل."""
import os
import base64
import subprocess
import time
import wave
import requests
from pathlib import Path
from config import GEMINI_API_KEY

PIPER_MODEL = "/tmp/piper/ar_JO-kareem-medium.onnx"
PIPER_CONFIG = "/tmp/piper/ar_JO-kareem-medium.onnx.json"


def _wav_to_mp3(wav_path, mp3_path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path,
         "-codec:a", "libmp3lame", "-q:a", "4",
         mp3_path],
        check=True, capture_output=True, timeout=60,
    )


def _is_ok(out_path):
    p = Path(out_path)
    return p.exists() and p.stat().st_size > 2048


# ============================================================
# 1) Gemini TTS (الأفضل — صوت رجالي احترافي)
# ============================================================
def _gemini_tts(text, out_mp3):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY غير موجود")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash-preview-tts:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "contents": [{"parts": [{"text": text}]}],
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
        raise RuntimeError("لا يوجد audio data في الاستجابة")

    raw = base64.b64decode(audio_b64)

    wav = out_mp3.replace(".mp3", ".wav")
    with wave.open(wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(raw)

    _wav_to_mp3(wav, out_mp3)


# ============================================================
# 2) Piper (بديل — صوت رجالي، بدون إنترنت)
# ============================================================
def _piper(text, out_mp3):
    if not os.path.exists(PIPER_MODEL):
        raise RuntimeError("نموذج Piper غير موجود")

    wav = out_mp3.replace(".mp3", ".wav")

    try:
        proc = subprocess.run(
            ["piper", "--model", PIPER_MODEL, "--config", PIPER_CONFIG, "--output_file", wav],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=180,
        )
    except FileNotFoundError:
        proc = subprocess.run(
            ["python", "-m", "piper", "--model", PIPER_MODEL, "--config", PIPER_CONFIG, "--output_file", wav],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=180,
        )

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="ignore")
        print(f"   stderr: {err[-500:]}")
        raise RuntimeError(f"Piper returncode {proc.returncode}")

    if not os.path.exists(wav) or os.path.getsize(wav) < 1024:
        raise RuntimeError("Piper لم ينتج ملف صوتي")

    _wav_to_mp3(wav, out_mp3)


# ============================================================
# 3) espeak-ng (بديل أخير — روبوتي)
# ============================================================
def _espeak(text, out_mp3):
    wav = out_mp3.replace(".mp3", ".wav")
    subprocess.run(
        ["espeak-ng", "-v", "ar", "-s", "125", "-p", "35", "-g", "8", "-w", wav, text],
        check=True, capture_output=True, timeout=60,
    )
    _wav_to_mp3(wav, out_mp3)


# ============================================================
# 4) gTTS (آخر حل — أنثوي)
# ============================================================
def _gtts(text, out_path):
    from gtts import gTTS
    gTTS(text=text, lang="ar", slow=False).save(out_path)


# ============================================================
# الواجهة
# ============================================================
def generate(text, out_path="out/voice.mp3"):
    if not text or not text.strip():
        raise ValueError("النص فارغ")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    text = text.strip()

    engines = [
        ("Gemini TTS (احترافي)", _gemini_tts),
        ("Piper (رجل)", _piper),
        ("espeak-ng (روبوتي)", _espeak),
        ("gTTS (أنثوي)", _gtts),
    ]

    for name, fn in engines:
        try:
            print(f"🔊 محاولة {name}...")
            fn(text, out_path)
            if _is_ok(out_path):
                size = Path(out_path).stat().st_size
                print(f"✅ الصوت ({name}): {out_path} ({size} بايت)")
                return out_path
            else:
                print(f"⚠️  {name}: الملف صغير جداً")
        except Exception as e:
            print(f"⚠️  {name} فشل: {str(e)[:200]}")
            time.sleep(1)

    raise RuntimeError("كل محركات الصوت فشلت")
