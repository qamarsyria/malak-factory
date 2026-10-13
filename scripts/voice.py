"""توليد الصوت العربي عبر edge-tts."""
import asyncio
from pathlib import Path
import edge_tts
from config import TTS_VOICE, TTS_RATE, TTS_VOLUME


async def _synth(text, out_path):
    communicate = edge_tts.Communicate(
        text,
        TTS_VOICE,
        rate=TTS_RATE,
        volume=TTS_VOLUME,
    )
    await communicate.save(out_path)


def generate(text, out_path="out/voice.mp3", retries=3):
    """يولّد الصوت. يعيد المحاولة عند الفشل."""
    if not text or not text.strip():
        raise ValueError("النص فارغ")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            print(f"🔊 محاولة edge-tts {attempt}/{retries}...")
            asyncio.run(_synth(text.strip(), out_path))
            if Path(out_path).exists() and Path(out_path).stat().st_size > 1024:
                print(f"✅ الصوت: {out_path} ({Path(out_path).stat().st_size} بايت)")
                return out_path
        except Exception as e:
            last_err = e
            print(f"⚠️  فشلت المحاولة {attempt}: {e}")

    raise RuntimeError(f"edge-tts فشل: {last_err}")
