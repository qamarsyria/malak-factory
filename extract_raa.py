# extract_raa.py — نسخة مُصلحة (بدون Whisper، بدون افتراضات)
# تُحمّل آيات الحصري الغنية بالراء مباشرة من everyayah.com
# الأمان: لا تلمس malak_converter.py ولا أي ملف ذهبي

import os
import subprocess
import sys
import urllib.request

RECITER_NAME = "Husary"
BASE_URL = "https://everyayah.com/data/Husary_128kbps/"

# آيات غنية بحرف الراء (مختارة بعناية)
# المصدر: نص قرآني معروف — لا حاجة لـ Whisper
TARGETS = [
    ("001", "الفاتحة", [1, 2, 3, 6, 7]),          # الرحمن الرحيم، رب، الصراط، غير
    ("093", "الضحى",   [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]),  # ربك، فارج، الرحمه
    ("097", "القدر",   [1, 2, 3, 4, 5]),           # القدر، ربهم
    ("105", "الفيل",   [1, 2, 3, 4, 5]),           # بربك
    ("106", "قريش",    [1, 2, 3, 4]),              # رحلة، فليعبدوا رب
    ("110", "النصر",   [1, 2, 3]),                 # ربك
    ("102", "التكاثر", [1, 2, 3, 4, 5, 6, 7, 8]),  # لم تعلمون... ربهم
]


def download_mp3(url, path):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            with open(path, "wb") as f:
                f.write(response.read())
        return True
    except Exception as e:
        print(f"   ⚠️ فشل تحميل {url}: {e}")
        return False


def mp3_to_wav(mp3, wav):
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", mp3, "-ar", "48000", "-ac", "1", wav],
        capture_output=True,
    )
    return result.returncode == 0


def main():
    print(f"🎯 استخراج الراء من: {RECITER_NAME}")
    print(f"📡 المصدر: everyayah.com (مباشر، لا يحتاج VPN)")
    total = sum(len(t[2]) for t in TARGETS)
    print(f"📋 {total} آية مستهدفة\n")

    os.makedirs("raa_ayahs", exist_ok=True)
    downloaded_wavs = []

    for surah_num, surah_name, ayahs in TARGETS:
        print(f"📖 سورة {surah_name} ({surah_num})")
        for ayah_num in ayahs:
            mp3_name = f"{surah_num}{ayah_num:03d}.mp3"
            url = f"{BASE_URL}{mp3_name}"
            mp3_path = f"raa_ayahs/{mp3_name}"
            wav_path = f"raa_ayahs/{surah_num}_{ayah_num:03d}.wav"

            if not download_mp3(url, mp3_path):
                print(f"   ❌ آية {ayah_num}: فشل التحميل")
                continue

            if not mp3_to_wav(mp3_path, wav_path):
                print(f"   ❌ آية {ayah_num}: فشل التحويل")
                continue

            size_kb = os.path.getsize(wav_path) // 1024
            downloaded_wavs.append(wav_path)
            print(f"   ✅ آية {ayah_num}: {size_kb} KB")

    if not downloaded_wavs:
        print("\n❌ لم يتم تحميل أي آية! تحقق من المصدر")
        sys.exit(1)

    print(f"\n🔗 دمج {len(downloaded_wavs)} آية في raa_master.wav...")
    with open("raa_concat.txt", "w") as f:
        for wav in downloaded_wavs:
            f.write(f"file '{os.path.abspath(wav)}'\n")

    result = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", "raa_concat.txt", "-c", "copy", "raa_master.wav",
        ],
        capture_output=True,
    )

    if not os.path.exists("raa_master.wav"):
        print("❌ فشل الدمج!")
        sys.exit(1)

    master_size = os.path.getsize("raa_master.wav")
    duration = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", "raa_master.wav",
        ],
        capture_output=True, text=True,
    ).stdout.strip()

    print(f"\n✅ تم! raa_master.wav")
    print(f"   الحجم: {master_size // 1024} KB")
    print(f"   المدة: {float(duration):.1f} ثانية")
    print(f"   الآيات: {len(downloaded_wavs)}")

    with open("raa_manifest.txt", "w", encoding="utf-8") as f:
        f.write(f"القارئ: {RECITER_NAME}\n")
        f.write(f"المدة: {duration} ثانية\n")
        f.write(f"عدد الآيات: {len(downloaded_wavs)}\n\n")
        for wav in downloaded_wavs:
            f.write(f"{wav}\n")

    print(f"📝 Manifest: raa_manifest.txt")


if __name__ == "__main__":
    main()
