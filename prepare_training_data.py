# prepare_training_data_v2.py — نسخة بسيطة (بدون pitch shift، بدون تنظيف قوي)
import os, subprocess, sys, urllib.request, zipfile

SR = 40000
BASE = "https://everyayah.com/data/Husary_128kbps/"

# آيات فيها راء (كثيرة)
TARGETS = [
    ("001", [1,2,3,6,7]), ("093", list(range(1,12))), ("094", list(range(1,9))),
    ("097", [1,2,3,4,5]), ("102", list(range(1,9))), ("105", [1,2,3,4,5]),
    ("106", [1,2,3,4]), ("110", [1,2,3]), ("112", [1,2,3,4]),
    ("113", [1,2,3,4,5]), ("114", [1,2,3,4,5,6]),
]

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def download(url, path):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            open(path, "wb").write(r.read())
        return True
    except Exception as e:
        print(f"⚠️ {e}"); return False

def to_wav(src, dst):
    return run(["ffmpeg","-y","-i",src,"-ar",str(SR),"-ac","1",dst]).returncode == 0

def main():
    os.makedirs("dataset", exist_ok=True)
    for f in os.listdir("dataset"): os.remove(f"dataset/{f}")
    os.makedirs("_work", exist_ok=True)

    # === 1. صوتك: تحويل فقط، بدون تنظيف قوي ===
    print("\n🎤 [1/2] تجهيز صوتك (تنظيف خفيف جداً فقط)")
    if not os.path.exists("my_voice.wav"):
        print("❌ my_voice.wav مفقود"); sys.exit(1)

    # highpass يقتل الهمهمة الكهربائية، lowpass خفيف يقتل الهدر العلوي. لا شيء آخر.
    clean = "_work/voice_clean.wav"
    r = run(["ffmpeg","-y","-i","my_voice.wav",
             "-af","highpass=f=80,lowpass=f=11000",
             "-ar",str(SR),"-ac","1",clean])
    if r.returncode != 0:
        print("❌ تنظيف فشل"); sys.exit(1)
    print(f"   ✅ {os.path.getsize(clean)//1024} KB")

    # تقسيم 10 ثوانٍ (RVC يحتاج مقاطع قصيرة)
    run(["ffmpeg","-y","-i",clean,"-ar",str(SR),"-ac","1",
         "-f","segment","-segment_time","10","-reset_timestamps","1",
         "dataset/user_%04d.wav"])
    n_user = len([f for f in os.listdir("dataset") if f.startswith("user_")])
    print(f"   ✅ {n_user} مقطع من صوتك")

    # === 2. الحصري: بدون أي pitch shift، بتردده الأصلي 100% ===
    print("\n🎼 [2/2] الحصري (بتردده الأصلي، بدون أي تعديل)")
    total = sum(len(t[1]) for t in TARGETS)
    done = 0
    for surah, ayahs in TARGETS:
        print(f"📖 سورة {surah}")
        for ayah in ayahs:
            mp3 = f"_work/{surah}{ayah:03d}.mp3"
            wav = f"dataset/raa_{surah}_{ayah:03d}.wav"
            if not download(f"{BASE}{surah}{ayah:03d}.mp3", mp3): continue
            if not to_wav(mp3, wav): continue
            done += 1
            print(f"   ✅ {surah}:{ayah} ({os.path.getsize(wav)//1024} KB)")

    print(f"\n✅ {done}/{total} آية من الحصري (بصوته الأصلي)")

    # === 3. زيادة وزن ملفات الراء (نسخ مكررة) ===
    print("\n⚖️ مضاعفة وزن ملفات الراء ×3 (ليتعلمها النموذج بقوة)")
    raa_files = [f for f in os.listdir("dataset") if f.startswith("raa_")]
    for i in range(2):  # نسختان إضافيتان
        for f in raa_files:
            src = f"dataset/{f}"
            dst = f"dataset/raa_dup{i}_{f[4:]}"
            if os.path.exists(src):
                subprocess.run(["cp", src, dst], capture_output=True)
    print(f"   ✅ {len(raa_files)*3} ملف راء إجمالي (بعد المضاعفة)")

    # === 4. تقرير + zip ===
    all_files = [f for f in os.listdir("dataset") if f.endswith(".wav")]
    total_sec = 0
    for f in all_files:
        try:
            r = run(["ffprobe","-v","error","-show_entries","format=duration",
                     "-of","default=noprint_wrappers=1:nokey=1",f"dataset/{f}"])
            total_sec += float(r.stdout.strip() or 0)
        except: pass

    print(f"\n📊 التقرير:")
    print(f"   ملفات صوتك: {n_user}")
    print(f"   ملفات الحصري: {len(raa_files)*3}")
    print(f"   المدة الكلية: {total_sec/60:.1f} دقيقة")

    with open("dataset/_manifest.txt","w",encoding="utf-8") as f:
        f.write(f"صوتك: {n_user} مقطع\n")
        f.write(f"الحصري: {len(raa_files)} آية × 3 (مضاعفة وزن)\n")
        f.write(f"ترددك الأصلي: 117.2 Hz\n")
        f.write(f"تردد الحصري الأصلي: 165.8 Hz\n")
        f.write(f"تنظيف صوتك: highpass=80,lowpass=11000 فقط\n")
        f.write(f"الحصري: بدون أي تعديل\n")
        f.write(f"معدل العينات: {SR}\n")

    with zipfile.ZipFile("training_data.zip","w",zipfile.ZIP_DEFLATED) as z:
        for f in all_files: z.write(f"dataset/{f}", f)
        z.write("dataset/_manifest.txt","_manifest.txt")

    print(f"\n🎯 training_data.zip: {os.path.getsize('training_data.zip')//(1024*1024)} MB")

if __name__ == "__main__":
    main()
