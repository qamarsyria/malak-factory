"""نظام صوتي هجين: Gemini → Azure → Piper مع إزالة التشكيل لـ Piper."""
import asyncio
import base64
import os
import re
import subprocess
import time
import wave
from pathlib import Path
import requests

from config import GEMINI_API_KEY


# ============================================================
# إعدادات
# ============================================================
AZURE_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_REGION = os.getenv("AZURE_SPEECH_REGION", "eastus")

PIPER_MODEL = "/tmp/piper/ar_JO-kareem-medium.onnx"
PIPER_CONFIG = "/tmp/piper/ar_JO-kareem-medium.onnx.json"
PIPER_LENGTH_SCALE = 1.0

TARGET_SAMPLE_RATE = "44100"
TARGET_CHANNELS = "1"
TARGET_BITRATE = "128k"


# ============================================================
# دوال مساعدة
# ============================================================
def _remove_tashkeel(text):
    """يزيل التشكيل العربي (لـ Piper فقط)."""
    tashkeel = re.compile(r"[\u064B-\u0652\u0670\u0640\u06D6-\u06ED]")
    return tashkeel.sub("", text)


def _clean_for_gemini(text):
    """تنظيف خفيف لـ Gemini (يحتفظ بالتشكيل)."""
    text = re.sub(r"[ۖۗۘۙۚۛ]", "", text)
    text = text.replace("۝", " ")
    return text.strip()


def _clean_for_piper(text):
    """تنظيف كامل لـ Piper (بلا تشكيل)."""
    text = _clean_for_gemini(text)
    text = _remove_tashkeel(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _get_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _normalize_audio(input_path, output_path):
    """يوحّد الصيغة (44100 Hz, mono)."""
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
# المحرك 1: Gemini TTS (الأساسي)
# ============================================================
def _gemini_tts(text, out_mp3, voice="Charon"):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY غير موجود")

    clean = _clean_for_gemini(text)
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
        ["ffmpeg", "-y", "-i", wav,
         "-codec:a", "libmp3lame", "-q:a", "4",
         out_mp3],
        check=True, capture_output=True, timeout=60,
    )


# ============================================================
# المحرك 2: Azure TTS (الاحتياطي الأول)
# ============================================================
def _azure_tts(text, out_mp3, voice="ar-EG-ShakirNeural", rate="-10%"):
    """
    Azure Neural TTS — 500K حرف مجاناً شهرياً.
    voices: ar-EG-ShakirNeural, ar-SA-HamedNeural, ar-SY-LaithNeural
    """
    if not AZURE_KEY:
        raise RuntimeError("AZURE_SPEECH_KEY غير موجود")

    clean = _clean_for_gemini(text)
    url = (
        f"https://{AZURE_REGION}.tts.speech.microsoft.com/"
        "cognitiveservices/v1"
    )
    ssml = f"""<speak version='1.0' xml:lang='ar-EG'>
        <voice name='{voice}'>
            <prosody rate='{rate}'>{clean}</prosody>
        </voice>
    </speak>"""

    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
    }

    r = requests.post(url, headers=headers, data=ssml.encode("utf-8"), timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Azure HTTP {r.status_code}: {r.text[:200]}")

    Path(out_mp3).write_bytes(r.content)
    if Path(out_mp3).stat().st_size < 2048:
        raise RuntimeError("Azure: ملف صغير جداً")


# ============================================================
# المحرك 3: Piper (الاحتياطي الأخير)
# ============================================================
def _piper_tts(text, out_mp3, length_scale=PIPER_LENGTH_SCALE):
    if not Path(PIPER_MODEL).exists():
        raise RuntimeError(f"Piper model غير موجود")

    clean = _clean_for_piper(text)
    wav = out_mp3.replace(".mp3", ".wav")
    raw = out_mp3.replace(".mp3", "_raw.mp3")

    proc = subprocess.run(
        ["python", "-m", "piper",
         "--model", PIPER_MODEL,
         "--config", PIPER_CONFIG,
         "--output_file", wav,
         "--length-scale", str(length_scale)],
        input=clean.encode("utf-8"),
        capture_output=True,
        timeout=180,
    )

    if proc.returncode != 0:
        raise RuntimeError(f"Piper فشل")

    if not Path(wav).exists() or Path(wav).stat().st_size < 2048:
        raise RuntimeError("Piper: ملف صغير")

    subprocess.run(
        ["ffmpeg", "-y", "-i", wav,
         "-codec:a", "libmp3lame", "-q:a", "4",
         raw],
        check=True, capture_output=True, timeout=60,
    )
    _normalize_audio(raw, out_mp3)


# ============================================================
# الواجهة الموحّدة — تجرب المحركات بالترتيب
# ============================================================
def generate_speech(text, out_mp3, voice=None, max_retries=2):
    """
    تجرب المحركات بالترتيب: Gemini → Azure → Piper.
    """
    if not text or not text.strip():
        raise ValueError("النص فارغ")

    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    text = text.strip()

    # المحركات بالترتيب
    engines = [
        ("Gemini", lambda: _gemini_tts(text, out_mp3, voice=voice or "Charon")),
        ("Azure", lambda: _azure_tts(text, out_mp3, voice="ar-EG-ShakirNeural", rate="-10%")),
        ("Piper", lambda: _piper_tts(text, out_mp3)),
    ]

    for engine_name, engine_fn in engines:
        for attempt in range(1, max_retries + 1):
            try:
                print(f"   🔊 [{engine_name}] محاولة {attempt}/{max_retries}...")
                engine_fn()
                duration = _get_duration(out_mp3)
                if duration > 0.5:
                    print(f"   ✅ {engine_name}: {duration:.1f} ثانية")
                    return duration
            except Exception as e:
                err = str(e)[:150]
                print(f"   ⚠️  {engine_name} محاولة {attempt}: {err}")
                # إعادة المحاولة عند 429 (quota)
                if "429" in err and attempt < max_retries:
                    wait = 20 * attempt
                    print(f"   ⏳ انتظار {wait} ث...")
                    time.sleep(wait)
                elif attempt < max_retries:
                    time.sleep(3)

    raise RuntimeError("كل المحركات فشلت")
