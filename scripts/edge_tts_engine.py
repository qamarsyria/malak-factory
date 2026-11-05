"""محرك Edge-TTS كبديل مؤقت لـ Gemini عند نفاد الحصة.
يُستخدم عبر TTS_ENGINE=edge. لا يحتاج أي مفتاح API.
"""
import asyncio
import os
from pathlib import Path

import edge_tts

# صوت فصحى سعودي رجالي مهيب — مناسب للقصص والقرآن
DEFAULT_VOICE = os.getenv("EDGE_TTS_VOICE", "ar-SA-HamedNeural")
DEFAULT_RATE  = os.getenv("EDGE_TTS_RATE",  "-8%")
DEFAULT_PITCH = os.getenv("EDGE_TTS_PITCH", "+0Hz")


async def _synth_async(text: str, out_mp3: str, voice: str, rate: str, pitch: str):
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await comm.save(out_mp3)


def edge_tts_synth(text: str, out_mp3: str,
                   voice: str = DEFAULT_VOICE,
                   rate: str = DEFAULT_RATE,
                   pitch: str = DEFAULT_PITCH) -> bool:
    if not text or not text.strip():
        return False
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(_synth_async(text.strip(), out_mp3, voice, rate, pitch))
        size = Path(out_mp3).stat().st_size
        return size > 1000
    except Exception as e:
        print(f"   ⚠️ edge-tts فشل: {str(e)[:150]}", flush=True)
        return False


def edge_tts_with_pool(text: str, out_mp3: str, voice: str = None,
                       prefer_lite: bool = False) -> str:
    """توقيع متوافق مع gemini_tts_with_pool — يُستبدل به مباشرة."""
    voice_name = voice or DEFAULT_VOICE
    if not edge_tts_synth(text, out_mp3, voice=voice_name):
        raise RuntimeError(f"❌ edge-tts فشل للنص: {text[:60]}...")
    return str(out_mp3)
