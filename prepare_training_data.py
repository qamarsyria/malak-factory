# prepare_training_data.py — نسخة نهائية v3
# ينتج: dataset/ = (صوتك النظيف مقسّم) + (راء الحصري منزّلة لترددك)
# الأمان: لا يلمس malak_converter.py ولا ملفات ملك
# الإصلاحات: asetrate+soxr+atempo لكل الملفات (لا تفاوت)، تنظيف صوتك، zip جاهز

import os
import math
import subprocess
import sys
import urllib.request
import zipfile

RECITER = "Husary"
BASE_URL = "https://everyayah.com/data/Husary_128kbps/"
USER_PITCH_HZ = float(os.environ.get("USER_PITCH_HZ", "130.0"))
HUSARY_PITCH_HZ = 165.8
SR = 40000  # RVC v2 standard

# معاملات ثابتة
FACTOR = USER_PITCH_HZ / HUSARY_PITCH_HZ
SEMITONES = round(12 * math.log2(FACTOR), 2)
ATEMPO = 1.0 / FACTOR

print(f"🎼 ترددك: {USER_PITCH_HZ} Hz | الحصري: {HUSARY_PITCH_HZ} Hz")
print(f"🎼 إزاحة: {SEMITONES:+.2f} نصف تون | atempo={ATEMPO:.4f}")

# آيات فيها راء (مرجع نصي قرآني — لا ASR)
TARGETS = [
    ("001", "الفاتحة",   [1, 2, 3, 6, 7]),
    ("093", "الضحى",     list(range(1, 12))),
    ("094", "الشرح",     list(range(1, 9))),
    ("097", "القدر",     [1, 2, 3, 4, 5]),
    ("102", "التكاثر",   list(range(1, 9))),
    ("105", "الفيل",     [1, 2, 3, 4, 5]),
    ("106", "قريش",      [1, 2, 3, 4]),
    ("110", "النصر",     [1, 2, 3]),
    ("112", "الإخلاص",   [1, 2, 3, 4]),
    ("113", "الفلق",     [1, 2, 3, 4, 5]),
    ("114", "الناس",     [1, 2, 3, 4, 5, 6]),
]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def download(url, path):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            with open(path, "wb") as f:
                f.write(r.read())
        return True
    except Exception as e:
        print(f"   ⚠️ {e}")
        return False


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ============ 1. تنظيف صوتك ============
def clean_voice(src, dst):
    """تنظيف متعدد الطبقات (آمن - لا يشوه الصوت)"""
    filters = (
        "highpass=f=80,"           # يقتل الهمهمة الكهربائية
        "lowpass=f=12000,"          # يقتل الوشة عالية التردد
        "afftdn=nr=8:nf=-45:tn=1,"  # دنويزر FFT معتدل (لا يشوه)
        "dynaudnorm=f=150:g=15"     # يوازن المستويات
    )
    r = run(["ffmpeg", "-y", "-i", src, "-af", filters,
             "-ar", str(SR), "-ac", "1", dst])
    return r.returncode == 0


# ============ 2. تقسيم صوتك (10 ثواني) ============
def split_voice(src, out_dir, prefix="user_"):
    pattern = os.path.join(out_dir, f"{prefix}%04d.wav")
    r = run(["ffmpeg", "-y", "-i", src, "-ar", str(SR), "-ac", "1",
             "-f", "segment", "-segment_time", "10",
             "-reset_timestamps", "1", pattern])
    return r.returncode == 0


# ============ 3. معالجة الراء (موحدة لكل الملفات) ============
def process_raa(src, dst):
    """asetrate (يغير التردد) + soxr (جودة عالية) + atempo (يعيد الطول)"""
    new_rate = int(SR * FACTOR)
    filters = (
        f"asetrate={new_rate},"
        f"aresample={SR}:resampler=soxr:precision=28,"
        f"atempo={ATEMPO:.6f}"
    )
    r = run(["ffmpeg", "-y", "-i", src, "-af", filters,
             "-ar", str(SR), "-ac", "1", dst])
    return r.returncode == 0


# ============ التنفيذ ============
def main():
    os.makedirs("dataset", exist_ok=True)
    for old in os.listdir("dataset"):
        p = os.path.join("dataset", old)
        if os.path.isfile(p):
            os.remove(p)

    work = "_work"
    os.makedirs(work, exist_ok=True)

    # --- المرحلة 1: صوتك (تنظيف + تقسيم) ---
    print("\n" + "="*60)
    print("🎤 [1/3] تنظيف صوتك وتقسيمه")
    print("="*60)
    if not os.path.exists("my_voice.wav"):
        print("❌ my_voice.wav غير موجود!")
        sys.exit(1)

    print("   🧹 جاري التنظيف...")
    if not clean_voice("my_voice.wav", f"{work}/my_voice_clean.wav"):
        print("❌ فشل التنظيف")
        sys.exit(1)
    print(f"   ✅ نظيف: {os.path.getsize(f'{work}/my_voice_clean.wav')//1024} KB")

    print("   ✂️  جاري التقسيم إلى 10 ثوانٍ...")
    if not split_voice(f"{work}/my_voice_clean.wav", "dataset", "user_"):
        print("❌ فشل التقسيم")
        sys.exit(1)
    user_files = sorted([f for f in os.listdir("dataset") if f.startswith("user_")])
    print(f"   ✅ {len(user_files)} مقطع جاهز")

    # --- المرحلة 2: الراء من الحصري ---
    print("\n" + "="*60)
    print(f"🎼 [2/3] الراء من {RECITER}")
    print("="*60)
    raa_count = 0
    total_ayahs = sum(len(t[2]) for t in TARGETS)

    for surah_num, surah_name, ayahs in TARGETS:
        print(f"📖 {surah_name}")
        for ayah in ayahs:
            mp3 = f"{work}/{surah_num}{ayah:03d}.mp3"
            raw = f"{work}/{surah_num}_{ayah:03d}_raw.wav"
            out = f"dataset/raa_{surah_num}_{ayah:03d}.wav"

            if not download(f"{BASE_URL}{surah_num}{ayah:03d}.mp3", mp3):
                continue
            # تحويل mp3 → wav بنفس sample rate
            r = run(["ffmpeg", "-y", "-i", mp3, "-ar", str(SR), "-ac", "1", raw])
            if r.returncode != 0:
                continue
            # معالجة موحدة
            if not process_raa(raw, out):
                print(f"   ❌ {surah_num}:{ayah} فشل")
                continue

            raa_count += 1
            print(f"   ✅ {surah_num}:{ayah} ({os.path.getsize(out)//1024} KB)")

    print(f"\n   ✅ {raa_count}/{total_ayahs} آية معالجة")

    # --- المرحلة 3: التقرير + الـ zip ---
    print("\n" + "="*60)
    print("📊 [3/3] التقرير النهائي")
    print("="*60)

    all_files = sorted(os.listdir("dataset"))
    raa_files = [f for f in all_files if f.startswith("raa_")]
    user_files = [f for f in all_files if f.startswith("user_")]

    total_sec = sum(duration(f"dataset/{f}") for f in all_files)

    print(f"   📁 ملفات الراء: {len(raa_files)}")
    print(f"   🎤 مقاطع صوتك: {len(user_files)}")
    print(f"   ⏱️  المدة الكلية: {total_sec/60:.1f} دقيقة")
    print(f"   📦 المجلد: dataset/")

    # manifest
    with open("dataset/_manifest.txt", "w", encoding="utf-8") as f:
        f.write("=== حزمة تدريب صوتك + الراء ===\n")
        f.write(f"ترددك: {USER_PITCH_HZ} Hz\n")
        f.write(f"تردد الحصري: {HUSARY_PITCH_HZ} Hz\n")
        f.write(f"إزاحة: {SEMITONES:+.2f} نصف تون\n")
        f.write(f"طريقة الراء: asetrate+soxr+atempo\n")
        f.write(f"طريقة صوتك: highpass+lowpass+afftdn+dynaudnorm\n")
        f.write(f"معدل العينات: {SR} Hz\n")
        f.write(f"عدد ملفات الراء: {len(raa_files)}\n")
        f.write(f"عدد مقاطع صوتك: {len(user_files)}\n")
        f.write(f"المدة الكلية: {total_sec/60:.1f} دقيقة\n")

    # zip
    print("\n   📦 جاري الضغط...")
    with zipfile.ZipFile("training_data.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in all_files:
            z.write(f"dataset/{f}", f)
        z.write("dataset/_manifest.txt", "_manifest.txt")

    zip_size = os.path.getsize("training_data.zip") // (1024*1024)
    print(f"   ✅ training_data.zip: {zip_size} MB")
    print("\n🎯 ارفع training_data.zip على Kaggle Dataset")


if __name__ == "__main__":
    main()
