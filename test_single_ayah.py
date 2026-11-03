import os
import sys
import subprocess
import asyncio
import requests
from pathlib import Path
import edge_tts

# ============================================================
# إعدادات التجربة
# ============================================================
SURAH = 1      # رقم السورة (مثلاً 1 للفاتحة)
AYAH = 1       # رقم الآية
VOICE = "ar-SA-HamedNeural" # صوت سعودي وقور
OUTPUT_FILE = "test_ayah.mp3"

def fetch_ayah_text(surah, ayah):
    """جلب النص العثماني للآية من الإنترنت"""
    print(f"📖 جاري جلب الآية {surah}:{ayah}...")
    try:
        url = f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah}/quran-uthmani"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            text = r.json()["data"]["text"]
            print(f"✅ النص: {text}")
            return text
    except Exception as e:
        print(f"❌ خطأ في جلب الآية: {e}")
    return None

async def generate_audio(text, out_path):
    """توليد الصوت باستخدام Edge TTS (مجاني، بدون حصة)"""
    print(f"🎙️ جاري توليد الصوت باستخدام {VOICE}...")
    # إبطاء السرعة قليلاً (-10%) لإعطاء طابع الخشوع
    communicate = edge_tts.Communicate(text, VOICE, rate="-10%")
    await communicate.save(out_path)
    print(f"✅ تم إنشاء الملف الصوتي الخام: {out_path}")

def apply_mosque_effect(input_path, output_path):
    """تطبيق صدى المسجد وإبطاء بسيط (بنفس فلاتر المعمل)"""
    print("🎛️ جاري تطبيق صدى المسجد والتحسينات...")
    try:
        filters = (
            "atempo=0.92,"
            "equalizer=f=120:t=q:w=1.0:g=4,"
            "equalizer=f=4000:t=q:w=1.0:g=-4,"
            "aecho=0.8:0.7:60|180|320:0.15|0.10|0.05,"
            "acompressor=threshold=0.1:ratio=3:attack=20:release=200" 
        )
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-af", filters,
            "-ar", "24000", "-ac", "1", 
            output_path
        ], check=True, capture_output=True, timeout=60)
        print(f"✅ تم التطبيق بنجاح: {output_path}")
        return True
    except Exception as e:
        print(f"⚠️ فشل تطبيق الفلاتر: {e}")
        return False

def main():
    print("=" * 50)
    print("🧪 بدء تجربة الآية الواحدة (معزولة تماماً عن المعمل)")
    print("=" * 50)

    # 1. جلب النص
    text = fetch_ayah_text(SURAH, AYAH)
    if not text:
        sys.exit(1)

    # 2. توليد الصوت الخام
    raw_file = "raw_ayah.mp3"
    asyncio.run(generate_audio(text, raw_file))

    # 3. تطبيق الصدى
    if os.path.exists(raw_file):
        apply_mosque_effect(raw_file, OUTPUT_FILE)
        
        # تنظيف الملف المؤقت
        if os.path.exists(raw_file):
            os.remove(raw_file)

    print("=" * 50)
    print(f"🎉 التجربة انتهت! استمع للملف: {OUTPUT_FILE}")
    print("ملاحظة: هذا الصوت مجاني 100% ولا يستهلك حصة Gemini.")
    print("=" * 50)

if __name__ == "__main__":
    main()
