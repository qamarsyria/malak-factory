"""توليد الصوت العربي — edge-tts (أصوات رجل) مع gTTS كبديل."""
import asyncio
import time
from pathlib import Path


# أصوات رجل عربية متعددة (مرتبة حسب الجودة)
MALE_VOICES = [
    "ar-EG-ShakirNeural",      # مصري، فصيح
    "ar-SA-HamedNeural",       # سعودي
    "ar-SY-LaithNeural",       # سوري
    "ar-JO-TaimNeural",        # أردني
    "ar-AE-HamdanNeural",      # إماراتي
]


async def _edge_async(text, out_path, voice):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate="+0%", volume="+0%")
    await communicate.save(out_path)


def _edge(text, out_path, voice):
    asyncio.run(_edge_async(text, out_path, voice))


def _gtts(text, out_path):
    from gtts import gTTS
    tts = gTTS(text=text, lang="ar", slow=False)
    tts.save(out_path)


def _is_ok(out_path):
    p = Path(out_path)
    return p.exists() and p.stat().st_size > 2048


def generate(text, out_path="out/voice.mp3"):
    if not text or not text.strip():
        raise ValueError("النص فارغ")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    text = text.strip()

    # 1) جرّب edge-tts مع كل الأصوات الرجالية
    for voice in MALE_VOICES:
        for attempt in range(1, 3):
            try:
                print(f"🔊 edge-tts ({voice}) محاولة {attempt}/2...")
                _edge(text, out_path, voice)
                if _is_ok(out_path):
                    size = Path(out_path).stat().st_size
                    print(f"✅ الصوت (edge-tts، {voice}): {out_path} ({size} بايت)")
                    return out_path
            except Exception as e:
                print(f"⚠️  {voice} فشل: {str(e)[:100]}")
                time.sleep(1)

    # 2) إذا فشل كل شيء، نستخدم gTTS كبديل
    print("⚠️  كل أصوات edge-tts فشلت — استخدام gTTS")
    for attempt in range(1, 4):
        try:
            print(f"🔊 gTTS محاولة {attempt}/3...")
            _gtts(text, out_path)
            if _is_ok(out_path):
                size = Path(out_path).stat().st_size
                print(f"✅ الصوت (gTTS): {out_path} ({size} بايت)")
                return out_path
        except Exception as e:
            print(f"⚠️  gTTS فشل: {e}")
            time.sleep(2)

    raise RuntimeError("كل محركات الصوت فشلت")
