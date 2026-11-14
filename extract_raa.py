import json
import urllib.request
import subprocess
import os
from faster_whisper import WhisperModel
from pydub import AudioSegment

def extract_raa():
    with open("best_match.json", "r") as f:
        best = json.load(f)
    
    reciter = best["folder"]
    print(f"🎯 استخراج الراء من: {best['name']}")
    
    # سورة القدر (فيها راء بكثرة) - آيات 1-5
    os.makedirs("raa_samples", exist_ok=True)
    mp3_files = []
    
    for i in range(1, 6):
        fn = f"097{i:03d}.mp3"
        url = f"https://everyayah.com/data/{reciter}/{fn}"
        path = f"raa_samples/{fn}"
        urllib.request.urlretrieve(url, path)
        mp3_files.append(path)
    
    # دمج الآيات
    with open("raa_samples/list.txt", "w") as f:
        for m in mp3_files: f.write(f"file '{m}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "raa_samples/list.txt", "-ar", "16000", "-ac", "1", "raa_surah.wav"], capture_output=True)
    
    # استخدام Whisper للتعرف على الكلمات
    print("⏳ جاري البحث عن كلمات فيها حرف الراء...")
    model = WhisperModel("base", device="cpu", compute_type="float32")
    segments, _ = model.transcribe("raa_surah.wav", language="ar", word_timestamps=True)
    
    audio = AudioSegment.from_wav("raa_surah.wav")
    saved_count = 0
    
    for segment in segments:
        if not segment.words: continue
        for word in segment.words:
            # البحث عن الكلمات اللي فيها "ر"
            if "ر" in word.word:
                start_ms = int(word.start * 1000) - 50
                end_ms = int(word.end * 1000) + 50
                if start_ms < 0: start_ms = 0
                
                chunk = audio[start_ms:end_ms]
                out_file = f"raa_samples/raa_{saved_count:03d}.wav"
                chunk.export(out_file, format="wav")
                print(f"   ✅ تم استخراج: {word.word.strip()} ({start_ms}-{end_ms}ms)")
                saved_count += 1
    
    if saved_count == 0:
        print("⚠️ ما تم العثور على كلمات فيها راء!")
    else:
        print(f"\n🎉 تم استخراج {saved_count} عينة راء!")
        
    # تجميعهم في ZIP
    subprocess.run(["zip", "-r", "raa_samples.zip", "raa_samples"], capture_output=True)

if __name__ == "__main__":
    extract_raa()
