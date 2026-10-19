"""مولد الصوت — يعتمد حصرياً على Gemini TTS."""
import os
from pathlib import Path
from gemini_tts_pool import gemini_tts_with_pool

def generate_voice(text: str, output_path: str, voice: str = "Charon") -> str:
    """
    يولد الصوت باستخدام Gemini TTS فقط.
    لا توجد بدائل (gTTS/edge-tts) للحفاظ على الجودة.
    """
    print(f"🎙️ توليد الصوت عبر Gemini (صوت: {voice})...")
    
    # التأكد من وجود مجلد المخرجات
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    # استدعاء Gemini TTS
    result = gemini_tts_with_pool(text, output_path, voice)
    
    return result
