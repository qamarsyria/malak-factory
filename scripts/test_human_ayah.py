import os
import subprocess
import requests
from pathlib import Path

SURAH = 1      # رقم السورة (يمكنك تغييره)
AYAH = 1       # رقم الآية
RECITER = "Husary_128kbps" 
OUTPUT_FILE = "out/test_human_ayah.mp3"
TEMP_FILE = "out/temp_human_ayah.mp3"

def download_human_ayah(surah, ayah, reciter):
    print(f"📥 تحميل الآية {surah}:{ayah} بصوت بشري ({reciter})...")
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"https://everyayah.com/data/{reciter}/{filename}"
    
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            os.makedirs("out", exist_ok=True)
            with open(TEMP_FILE, "wb") as f:
                f.write(r.content)
            print(f"✅ تم التحميل.")
            return True
        else:
            print(f"❌ فشل التحميل، كود: {r.status_code}")
    except Exception as e:
        print(f"❌ خطأ: {e}")
    return False

def apply_stealth_filters(input_path, output_path):
    """تطبيق فلتر يغير البصمة الصوتية لكسر Content ID"""
    print("🎛️ تطبيق الفلتر الخفي (Stealth Mix) لكسر بصمة يوتيوب...")
    try:
        # 1. asetrate: يغير التردد (Pitch) بشكل طفيف جداً (خفض 3%)
        # 2. atempo: يعيد السرعة لطبيعتها بعد تغيير التردد (1.03)
        # 3. aecho: صدى خفيف جداً يكسر البصمة ويعطي طابع خشوع
        # 4. highpass/lowpass: قص الترددات العالية جداً والمنخفضة جداً لتغيير شكل الموجة
        filters = (
            "asetrate=22050*0.97,"   # خفض النبرة 3%
            "atempo=1.03,"           # تعويض السرعة
            "aecho=0.8:0.88:40:0.15," # صدى خفيف جداً
            "highpass=f=80,"         # قطع الترددات المنخفضة جداً
            "lowpass=f=10000"        # قطع الترددات العالية جداً
        )
        
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-af", filters,
            "-ar", "44100", "-ac", "1", 
            output_path
        ], check=True, capture_output=True, timeout=60)
        
        print(f"✅ تم إنشاء الملف المعدل: {output_path}")
        return True
    except Exception as e:
        print(f"⚠️ فشل الفلتر: {e}")
        return False

if __name__ == "__main__":
    if download_human_ayah(SURAH, AYAH, RECITER):
        if apply_stealth_filters(TEMP_FILE, OUTPUT_FILE):
            if os.path.exists(TEMP_FILE): os.remove(TEMP_FILE)
