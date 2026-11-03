import os
import subprocess
import requests
import asyncio
import edge_tts

# إعدادات التجربة
SURAH = 2
AYAH = 30
RECITER = "Alafasy_128kbps"
NARRATION_TEXT = "في قديم الزمان، خلق الله آدم من طين، وأمر الملائكة بالسجود له."
MUSIC_FILE = "assets/audio/music/calm.mp3"

def apply_stealth(input_path, output_path):
    print("🛡️ تطبيق الفلتر السري على الصوت البشري...")
    filters = (
        "atempo=1.05,equalizer=f=200:t=q:w=1.0:g=2,"
        "aecho=0.8:0.9:20:0.1,highpass=f=100,lowpass=f=12000"
    )
    subprocess.run(["ffmpeg", "-y", "-i", input_path, "-af", filters, "-ar", "24000", "-ac", "1", output_path], check=True, capture_output=True)

def apply_reverb(input_path, output_path):
    print("🕌 تطبيق صدى المسجد على الصوت البشري...")
    filters = (
        "atempo=0.92,equalizer=f=120:t=q:w=1.0:g=4,equalizer=f=4000:t=q:w=1.0:g=-4,"
        "aecho=0.8:0.7:60|180|320:0.15|0.10|0.05"
    )
    subprocess.run(["ffmpeg", "-y", "-i", input_path, "-af", filters, "-ar", "24000", "-ac", "1", output_path], check=True, capture_output=True)

def add_music(voice_path, output_path, music_path):
    print("🎵 إضافة موسيقى خلفية للسرد...")
    if not os.path.exists(music_path):
        subprocess.run(["cp", voice_path, output_path])
        return
    filters = (
        "[1:a]volume=0.12,afade=t=in:st=0:d=2,afade=t=out:st=5:d=2[music];"
        "[0:a][music]amix=inputs=2:duration=first:normalize=0[out]"
    )
    subprocess.run(["ffmpeg", "-y", "-i", voice_path, "-stream_loop", "-1", "-i", music_path, "-filter_complex", filters, "-map", "[out]", output_path], check=True, capture_output=True)

async def generate_narration():
    print("🎙️ توليد السرد (Edge TTS)...")
    communicate = edge_tts.Communicate(NARRATION_TEXT, "ar-SA-HamedNeural", rate="-10%")
    await communicate.save("out/test_narration_raw.mp3")

def main():
    os.makedirs("out", exist_ok=True)
    print("🚀 بدء تجربة النظام الهجين...")

    # 1. السرد
    asyncio.run(generate_narration())
    add_music("out/test_narration_raw.mp3", "out/test_narration_mixed.mp3", MUSIC_FILE)

    # 2. الآية البشرية
    print(f"📥 تحميل الآية {SURAH}:{AYAH} بصوت {RECITER}...")
    url = f"https://everyayah.com/data/{RECITER}/{SURAH:03d}{AYAH:03d}.mp3"
    r = requests.get(url, timeout=15)
    with open("out/test_human_ayah_raw.mp3", "wb") as f:
        f.write(r.content)
    
    # تطبيق الفلاتر
    apply_stealth("out/test_human_ayah_raw.mp3", "out/test_human_ayah_stealth.mp3")
    apply_reverb("out/test_human_ayah_stealth.mp3", "out/test_human_ayah_final.mp3")

    # 3. الدمج النهائي
    print("🔗 دمج السرد مع الآية...")
    with open("out/concat_test.txt", "w") as f:
        f.write("file 'test_narration_mixed.mp3'\n")
        f.write("file 'test_human_ayah_final.mp3'\n")
    
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "out/concat_test.txt", "-c", "copy", "out/FINAL_HYBRID_TEST.mp3"], check=True)

    print("✅ انتهت التجربة! الملف الجاهز: out/FINAL_HYBRID_TEST.mp3")

if __name__ == "__main__":
    main()
