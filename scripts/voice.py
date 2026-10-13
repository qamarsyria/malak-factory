"""توليد الصوت العربي — gTTS (Google) كأساس، edge-tts كبديل."""
import asyncio
import time
from pathlib import Path


def _gtts(text, out_path):
    """المحرك الأساسي: Google TTS."""
    from gtts import gTTS
    tts = gTTS(text=text, lang="ar", slow=False)
    tts.save(out_path)


async def _edge_async(text, out_path):
    import edge_tts
    from config import TTS_VOICE, TTS_RATE, TTS_VOLUME
    communicate = edge_tts.Communicate(
        text, TTS_VOICE, rate=TTS_RATE, volume=TTS_VOLUME
    )
    await communicate.save(out_path)


def _edge(text, out_path):
    asyncio.run(_edge_async(text, out_path))


def _is_ok(out_path):
    p = Path(out_path)
    return p.exists() and p.stat().st_size > 2048


def generate(text, out_path="out/voice.mp3"):
    """يولّد الصوت. gTTS أولاً، ثم edge-tts كبديل."""
    if not text or not text.strip():
        raise ValueError("النص فارغ")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    text = text.strip()

    # المحرك الأساسي: gTTS
    for attempt in range(1, 4):
        try:
            print(f"🔊 محاولة gTTS {attempt}/3...")
            _gtts(text, out_path)
            if _is_ok(out_path):
                size = Path(out_path).stat().st_size
                print(f"✅ الصوت (gTTS): {out_path} ({size} بايت)")
                return out_path
            print(f"⚠️  الملف صغير جداً")
        except Exception as e:
            print(f"⚠️  gTTS فشل: {e}")
            time.sleep(2)

    # البديل: edge-tts
    for attempt in range(1, 3):
        try:
            print(f"🔊 محاولة edge-tts (بديل) {attempt}/2...")
            _edge(text, out_path)
            if _is_ok(out_path):
                size = Path(out_path).stat().st_size
                print(f"✅ الصوت (edge-tts): {out_path} ({size} بايت)")
                return out_path
        except Exception as e:
            print(f"⚠️  edge-tts فشل: {e}")
            time.sleep(2)

    raise RuntimeError("كل محركات الصوت فشلت")
