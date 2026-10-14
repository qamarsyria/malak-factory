"""توليد الصوت العربي — Gemini TTS مع أصوات متعددة."""
import base64
import subprocess
import time
import wave
from pathlib import Path
import requests
from config import GEMINI_API_KEY, TTS_VOICE, TTS_VOICE_FEMALE


def _wav_to_mp3(wav_path, mp3_path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path,
         "-codec:a", "libmp3lame", "-q:a", "4",
         mp3_path],
        check=True, capture_output=True, timeout=60,
    )


def _is_ok(p):
    return Path(p).exists() and Path(p).stat().st_size > 2048


def _gemini_tts(text, out_mp3, voice):
    """Gemini TTS مع صوت محدد."""
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
    _wav_to_mp3(wav, out_mp3)


def _gtts(text, out_path):
    from gtts import gTTS
    gTTS(text=text, lang="ar", slow=False).save(out_path)


def generate(text, out_path="out/voice.mp3", voice=None):
    """
    يولّد الصوت.
    voice: اسم صوت Gemini ('Charon', 'Kore', ...)
    إذا None → يستخدم TTS_VOICE من config
    """
    if not text or not text.strip():
        raise ValueError("النص فارغ")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    text = text.strip()
    voice = voice or TTS_VOICE

    engines = [
        (f"Gemini TTS ({voice})", lambda t, o: _gemini_tts(t, o, voice)),
        ("gTTS (احتياطي)", _gtts),
    ]

    for name, fn in engines:
        try:
            print(f"🔊 محاولة {name}...")
            fn(text, out_path)
            if _is_ok(out_path):
                size = Path(out_path).stat().st_size
                print(f"✅ الصوت ({name}): {out_path} ({size} بايت)")
                return out_path
        except Exception as e:
            print(f"⚠️  {name} فشل: {str(e)[:200]}")
            time.sleep(1)

    raise RuntimeError("كل محركات الصوت فشلت")
