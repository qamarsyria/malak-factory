import os
import sys
import subprocess
import asyncio
import requests
import edge_tts

SURAH = 1      # يمكنك تغيير رقم السورة
AYAH = 1       # يمكنك تغيير رقم الآية
VOICE = "ar-SA-HamedNeural" 
OUTPUT_FILE = "out/test_ayah.mp3"

def fetch_ayah_text(surah, ayah):
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
    print(f"🎙️ جاري توليد الصوت باستخدام {VOICE}...")
    communicate = edge_tts.Communicate(text, VOICE, rate="-10%")
    await communicate.save(out_path)
    print(f"✅ تم إنشاء الملف الصوتي الخام: {out_path}")

def apply_mosque_effect(input_path, output_path):
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
    print("🧪 بدء تجربة الآية الواحدة (معزولة تماماً)")
    text = fetch_ayah_text(SURAH, AYAH)
    if not text: sys.exit(1)

    raw_file = "out/raw_ayah.mp3"
    os.makedirs("out", exist_ok=True)
    asyncio.run(generate_audio(text, raw_file))

    if os.path.exists(raw_file):
        apply_mosque_effect(raw_file, OUTPUT_FILE)
        if os.path.exists(raw_file): os.remove(raw_file)

    print(f"🎉 التجربة انتهت! الملف: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
