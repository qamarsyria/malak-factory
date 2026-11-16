import os, sys, json, subprocess
from pathlib import Path

social_mp3 = os.path.abspath(sys.argv[1])
meta_json = os.path.abspath(sys.argv[2])
output_mp3 = os.path.abspath(sys.argv[3])

print("🔪 جاري فصل السرد عن الآيات...")
meta = json.loads(Path(meta_json).read_text(encoding="utf-8"))
segments = meta["segments"]

os.makedirs("temp_chunks", exist_ok=True)

current_time = 0.0
chunk_info = []

# 1. تقطيع الملف الصوتي
for seg_idx, seg in enumerate(segments):
    is_quran = (seg["type"] == "quran")
    for c_idx, chunk in enumerate(seg["chunks"]):
        dur = chunk["duration"]
        chunk_file = f"temp_chunks/seg_{seg_idx}_chunk_{c_idx}.wav"
        subprocess.run(["ffmpeg", "-y", "-ss", str(current_time), "-t", str(dur), "-i", social_mp3, "-ar", "48000", "-ac", "1", chunk_file], capture_output=True)
        current_time += dur
        chunk_info.append({"file": os.path.abspath(chunk_file), "is_quran": is_quran, "duration": dur})

# 2. تجميع الآيات
quran_files = [item["file"] for item in chunk_info if item["is_quran"]]
if not quran_files:
    print("⚠️ لا توجد آيات. سيتم نسخ الملف الأصلي.")
    subprocess.run(["ffmpeg", "-y", "-i", social_mp3, "-c", "copy", output_mp3], check=True)
    sys.exit(0)

with open("temp_chunks/quran_concat.txt", "w") as f:
    for qf in quran_files:
        f.write(f"file '{qf}'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "temp_chunks/quran_concat.txt", "-c", "copy", os.path.abspath("temp_chunks/quran_all.wav")], check=True)

# 3. مسارات RVC (مطلقة + فحص وجود)
PY = sys.executable
rvc_dir = os.path.abspath("rvc")

model_clean = os.path.join(rvc_dir, "assets/weights/clean.pth")
model_rough = os.path.join(rvc_dir, "assets/weights/rough.pth")
model_malak = os.path.join(rvc_dir, "assets/weights/malak.pth")

idx_clean = os.path.join(rvc_dir, "clean.index")
idx_rough = os.path.join(rvc_dir, "rough.index")
idx_malak = os.path.join(rvc_dir, "malak.index")

input_all = os.path.abspath("temp_chunks/quran_all.wav")

# 🚨 فحص الموديلات
for m in [model_clean, model_rough, model_malak]:
    if not os.path.exists(m):
        print(f"❌ الموديل مفقود: {m}")
        sys.exit(1)

print("🎙️ تحويل الآيات بالبصمة الثلاثية...")
for name, model, idx, pitch in [("clean", model_clean, idx_clean, "-6"), ("rough", model_rough, idx_rough, "-4"), ("malak", model_malak, idx_malak, "0")]:
    out_wav = os.path.abspath(f"temp_chunks/quran_{name}.wav")
    subprocess.run([PY, os.path.join(rvc_dir, "infer/cli.py"), "--model", model, "--input", input_all, "--output", out_wav, "--index", idx, "--f0-method", "rmvpe", "--pitch", pitch, "--protect", "0.4", "--index-rate", "0.4", "--rms-mix-rate", "0.8"], check=True)

# 4. الخلط والصدى
print("🔬 الخلط والصدى...")
subprocess.run(["ffmpeg", "-y", "-i", os.path.abspath("temp_chunks/quran_clean.wav"), "-i", os.path.abspath("temp_chunks/quran_rough.wav"), "-i", os.path.abspath("temp_chunks/quran_malak.wav"), "-filter_complex", "[0:a]volume=0.9[a1]; [1:a]volume=0.05[a2]; [2:a]volume=0.05[a3]; [a1][a2][a3]amix=inputs=3:normalize=0,alimiter", os.path.abspath("temp_chunks/quran_mixed.wav")], check=True)
subprocess.run(["ffmpeg", "-y", "-i", os.path.abspath("temp_chunks/quran_mixed.wav"), "-af", "aecho=0.8:0.9:150|300|450:0.3|0.15|0.075", os.path.abspath("temp_chunks/quran_rvc.wav")], check=True)

# 5. إعادة التقطيع والدمج
current_time_quran = 0.0
for item in chunk_info:
    if item["is_quran"]:
        dur = item["duration"]
        rvc_chunk_file = os.path.abspath(f"temp_chunks/rvc_{os.path.basename(item['file'])}")
        subprocess.run(["ffmpeg", "-y", "-ss", str(current_time_quran), "-t", str(dur), "-i", os.path.abspath("temp_chunks/quran_rvc.wav"), "-ar", "48000", "-ac", "1", rvc_chunk_file], capture_output=True)
        current_time_quran += dur
        item["file"] = rvc_chunk_file

print("🔗 الدمج النهائي...")
with open("temp_chunks/final_concat.txt", "w") as f:
    for item in chunk_info:
        f.write(f"file '{item['file']}'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "temp_chunks/final_concat.txt", "-c:a", "libmp3lame", "-b:a", "192k", output_mp3], check=True)
print("✅ اكتمل الدمج بنجاح!")
