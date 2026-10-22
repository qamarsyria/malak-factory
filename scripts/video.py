"""مولد الصوت — يعتمد حصرياً على Gemini TTS + قص الصمت + نبرة ديناميكية."""
import os
import subprocess
from pathlib import Path
from gemini_tts_pool import gemini_tts_with_pool
from config import (
    STYLE_HINTS,
    TRIM_SILENCE_ENABLED,
    TRIM_SILENCE_THRESHOLD,
    TRIM_SILENCE_MIN_LENGTH,
)


def trim_silence(input_path: str) -> str:
    if not TRIM_SILENCE_ENABLED:
        return input_path
    output_path = input_path.replace(".mp3", "_trim.mp3")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-af", f"silenceremove=stop_periods=-1:stop_duration={TRIM_SILENCE_MIN_LENGTH}:stop_threshold={TRIM_SILENCE_THRESHOLD}",
            "-codec:a", "libmp3lame", "-b:a", "192k", output_path,
        ], check=True, capture_output=True, timeout=60)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            os.replace(output_path, input_path)
            print("✂️ تم قص الصمت الزائد من نهاية الصوت")
        else:
            if os.path.exists(output_path): os.remove(output_path)
            print("⚠️ الصوت بعد القص فارغ، تم تخطي القص")
        return input_path
    except Exception as e:
        print(f"⚠️ فشل قص الصمت: {e}")
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except Exception: pass
        return input_path


def generate(text: str, output_path: str, voice="Charon",
             item_type: str = None, emotion_hint: str = None,
             prefer_lite: bool = False) -> str:
    """
    يولد الصوت باستخدام Gemini TTS.
    - emotion_hint: تلميح نبرة مخصص (له الأولوية).
    - item_type: نوع المحتوى (fallback: STYLE_HINTS).
    - prefer_lite: يفضل الموديلات الخفيفة (حصة أكبر).
    """
    if isinstance(voice, dict):
        voice_name = voice.get("gemini", "Charon")
    else:
        voice_name = str(voice)

    # اختيار التلميح: emotion_hint (يدوي أو من Gemini Flash) > STYLE_HINTS
    style = ""
    if emotion_hint and emotion_hint.strip():
        style = emotion_hint.strip()
    elif item_type and item_type in STYLE_HINTS:
        style = STYLE_HINTS[item_type]

    text_with_style = f"{style} {text}" if style else text

    print(f"🎙️ توليد الصوت عبر Gemini (صوت: {voice_name}, نوع: {item_type or 'عام'}, تلميح: {style[:50] or 'لا يوجد'})...")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    result = gemini_tts_with_pool(text_with_style, output_path, voice_name, prefer_lite=prefer_lite)
    result = trim_silence(result)
    return result
