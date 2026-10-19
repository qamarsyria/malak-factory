"""مولد الصوت — يعتمد حصرياً على Gemini TTS."""
import os
from pathlib import Path
from gemini_tts_pool import gemini_tts_with_pool

def generate(text: str, output_path: str, voice="Charon") -> str:
    """
    يولد الصوت باستخدام Gemini TTS فقط.
    """
    # إصلاح: إذا كان المتغير قاموساً، نستخرج منه اسم صوت Gemini
    if isinstance(voice, dict):
        voice_name = voice.get("gemini", "Charon")
    else:
        voice_name = str(voice)

    print(f"🎙️ توليد الصوت عبر Gemini (صوت: {voice_name})...")
    
    # التأكد من وجود مجلد المخرجات
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    # استدعاء Gemini TTS
    result = gemini_tts_with_pool(text, output_path, voice_name)
    
    return result
