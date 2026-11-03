import os
import subprocess
import requests

# غيرت القارئ إلى العفاسي لأنه أسرع وأعذب من الحصري
SURAH = 1      
AYAH = 1       
RECITER = "Alafasy_128kbps" # مشاري العفاسي
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
    """فلتر خفي يكسر بصمة يوتيوب بدون التأثير على السرعة أو جودة الصوت"""
    print("🎛️ تطبيق الفلتر الخفي (بدون تبطيء)...")
    try:
        # لقد حذفنا asetrate الذي كان يسبب البطء الشديد
        # نستخدم تعديل الترددات والصدى الخفيف جداً لتغيير بصمة الصوت
        filters = (
            "atempo=1.05,"                 # تسريع بسيط جداً (5%) لجعله أكثر حيوية
            "equalizer=f=200:t=q:w=1.0:g=2," # تعميق الصوت قليلاً
            "aecho=0.8:0.9:20:0.1,"        # صدى خفيف جداً (لتغيير البصمة)
            "highpass=f=100,"              # قطع الترددات المنخفضة جداً
            "lowpass=f=12000"              # قطع الترددات العالية جداً
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
