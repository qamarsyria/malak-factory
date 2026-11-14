# extract_raa.py — نسخة نهائية v2.1
# - تحميل مباشر من everyayah.com
# - بدون Whisper، بدون placeholders
# - إنتاج raw + pitch-matched
# - الأمان: لا تلمس أي ملف ذهبي

import os
import math
import subprocess
import sys
import urllib.request

RECITER_NAME = "Husary"
BASE_URL = "https://everyayah.com/data/Husary_128kbps/"
USER_PITCH_HZ = float(os.environ.get("USER_PITCH_HZ", "117.2"))
HUSARY_PITCH_HZ = 165.8

TARGETS = [
    ("001", "الفاتحة", [1, 2, 3, 6, 7]),
    ("093", "الضحى",   list(range(1, 12))),
    ("094", "الشرح",   list(range(1, 9))),
    ("097", "القدر",   [1, 2, 3, 4, 5]),
    ("102", "التكاثر", list(range(1, 9))),
    ("105", "الفيل",   [1, 2, 3, 4, 5]),
    ("106", "قريش",    [1, 2, 3, 4]),
    ("110", "النصر",   [1, 2, 3]),
    ("112", "الإخلاص", [1, 2, 3, 4]),
    ("113", "الفلق",   [1, 2, 3, 4, 5]),
    ("114", "الناس",   [1, 2, 3, 4, 5, 6]),
]

SEMITONES = round(12 * math.log2(USER_PITCH_HZ / HUSARY_PITCH_HZ), 2)
print(f"🎼 إزاحة التردد: {SEMITONES:+.2f} نصف تون ({HUSARY_PITCH_HZ} → {USER_PITCH_HZ} Hz)")


def download(url, path):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            with open(path, "wb") as f:
                f.write(r.read())
        return True
    except Exception as e:
        print(f"   ⚠️ {url} → {e}")
        return False


def to_wav(src, dst):
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-ar", "48000", "-ac", "1", dst],
        capture_output=True,
    )
    return r.returncode == 0


def pitch_shift(src, dst, semitones):
    factor = 2 ** (semitones / 12)
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", src,
         "-af", f"rubberband=pitch={factor:.6f}",
         "-ar", "48000", "-ac", "1", dst],
        capture_output=True,
    )
    if r.returncode != 0:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", src,
             "-af", f"asetrate=48000*{factor:.6f},aresample=48000,atempo={1/factor:.6f}",
             "-ar", "48000", "-ac", "1", dst],
            capture_output=True,
        )
    return r.returncode == 0


def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def main():
    print(f"🎯 الراء من: {RECITER_NAME}")
    print(f"📡 المصدر: everyayah.com")
    print(f"🎼 ترددك: {USER_PITCH_HZ} Hz")
    total = sum(len(t[2]) for t in TARGETS)
    print(f"📋 {total} آية مستهدفة\n")

    os.makedirs("raa_raw", exist_ok=True)
    os.makedirs("raa_matched", exist_ok=True)

    raw_list, matched_list = [], []

    for surah_num, surah_name, ayahs in TARGETS:
        print(f"📖 {surah_name} ({surah_num})")
        for ayah in ayahs:
            mp3_name = f"{surah_num}{ayah:03d}.mp3"
            mp3_path = f"raa_raw/{mp3_name}"
            raw_wav = f"raa_raw/{surah_num}_{ayah:03d}.wav"
            matched_wav = f"raa_matched/{surah_num}_{ayah:03d}_p.wav"

            if not download(f"{BASE_URL}{mp3_name}", mp3_path):
                continue
            if not to_wav(mp3_path, raw_wav):
                print(f"   ❌ تحويل فشل: {mp3_name}")
                continue

            raw_list.append(raw_wav)
            print(f"   ✅ {ayah}: {os.path.getsize(raw_wav)//1024} KB", end="")

            if pitch_shift(raw_wav, matched_wav, SEMITONES):
                matched_list.append(matched_wav)
                print("  +matched")
            else:
                print("  (pitch فشل)")

    if not raw_list:
        print("\n❌ لم يتم تحميل أي آية")
        sys.exit(1)

    print(f"\n🔗 دمج {len(raw_list)} آية → raa_raw_master.wav")
    with open("_c1.txt", "w") as f:
        for w in raw_list:
            f.write(f"file '{os.path.abspath(w)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "_c1.txt",
         "-c", "copy", "raa_raw_master.wav"],
        capture_output=True,
    )

    print(f"🔗 دمج {len(matched_list)} آية → raa_matched_master.wav")
    with open("_c2.txt", "w") as f:
        for w in matched_list:
            f.write(f"file '{os.path.abspath(w)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "_c2.txt",
         "-c", "copy", "raa_matched_master.wav"],
        capture_output=True,
    )

    print(f"\n✅ النتائج:")
    for f in ["raa_raw_master.wav", "raa_matched_master.wav"]:
        if os.path.exists(f):
            print(f"   {f}: {os.path.getsize(f)//1024} KB | {duration(f):.1f}s")

    with open("raa_manifest.txt", "w", encoding="utf-8") as f:
        f.write(f"القارئ: {RECITER_NAME}\n")
        f.write(f"ترددك: {USER_PITCH_HZ} Hz\n")
        f.write(f"تردد الحصري: {HUSARY_PITCH_HZ} Hz\n")
        f.write(f"إزاحة: {SEMITONES:+.2f} نصف تون\n")
        f.write(f"عدد الآيات: {len(raw_list)}\n\n")
        f.write("== RAW ==\n")
        for w in raw_list:
            f.write(f"{w}\n")
        f.write("\n== MATCHED ==\n")
        for w in matched_list:
            f.write(f"{w}\n")


if __name__ == "__main__":
    main()
