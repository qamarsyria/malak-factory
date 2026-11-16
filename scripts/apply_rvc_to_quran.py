import os, sys, json, subprocess
from pathlib import Path

social_mp3 = sys.argv[1]
meta_json = sys.argv[2]
output_mp3 = sys.argv[3]

print("🔪 جاري فصل السرد عن الآيات...")
meta = json.loads(Path(meta_json).read_text(encoding="utf-8"))
segments = meta["segments"]

os.makedirs("temp_chunks", exist_ok=True)

current_time = 0.0
chunk_info = []

# 1. تقطيع الملف الصوتي حسب المدة المسجلة في الـ meta
for seg_idx, seg in enumerate(segments):
    is_quran = (seg["type"] == "quran")
    for c_idx, chunk in enumerate(seg["chunks"]):
        dur = chunk["duration"]
        chunk_file = f"temp_chunks/seg_{seg_idx}_chunk_{c_idx}.wav"
        subprocess.run(["ffmpeg", "-y", "-ss", str(current_time), "-t", str(dur), "-i", social_mp3, "-ar", "48000", "-ac", "1", chunk_file], capture_output=True)
        current_time += dur
        chunk_info.append({"file": chunk_file, "is_quran": is_quran, "duration": dur})

# 2. تجميع كل الآيات في ملف واحد لتسريع الـ RVC
quran_files = [item["file"] for item in chunk_info if item["is_quran"]]
if not quran_files:
    print("⚠️ لا توجد آيات. سيتم نسخ الملف الأصلي.")
    subprocess.run(["ffmpeg", "-y", "-i", social_mp3, "-c", "copy", output_mp3], check=True)
    sys.exit(0)

with open("temp_chunks/quran_concat.txt", "w") as f:
    for qf in quran_files:
        f.write(f"file '{os.path.abspath(qf)}'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "temp_chunks/quran_concat.txt", "-c", "copy", "temp_chunks/quran_all.wav"], check=True)

# 3. تطبيق الـ RVC (Triple Layer) على الآيات فقط
print("🎙️ تحويل الآيات بالبصمة الثلاثية...")
PY = "python"
rvc_dir = "rvc"

subprocess.run([PY, f"{rvc_dir}/infer/cli.py", "--model", f"{rvc_dir}/assets/weights/clean.pth", "--input", "temp_chunks/quran_all.wav", "--output", "temp_chunks/quran_clean.wav", "--index", f"{rvc_dir}/clean.index", "--f0-method", "rmvpe", "--pitch", "-6", "--protect", "0.4", "--index-rate", "0.4", "--rms-mix-rate", "0.8"], check=True)
subprocess.run([PY, f"{rvc_dir}/infer/cli.py", "--model", f"{rvc_dir}/assets/weights/rough.pth", "--input", "temp_chunks/quran_all.wav", "--output", "temp_chunks/quran_rough.wav", "--index", f"{rvc_dir}/rough.index", "--f0-method", "rmvpe", "--pitch", "-4", "--protect", "0.4", "--index-rate", "0.4", "--rms-mix-rate", "0.8"], check=True)
subprocess.run([PY, f"{rvc_dir}/infer/cli.py", "--model", f"{rvc_dir}/assets/weights/malak.pth", "--input", "temp_chunks/quran_all.wav", "--output", "temp_chunks/quran_malak.wav", "--index", f"{rvc_dir}/malak.index", "--f0-method", "rmvpe", "--pitch", "0", "--protect", "0.4", "--index-rate", "0.4", "--rms-mix-rate", "0.8"], check=True)

# 4. الخلط بالنسب (900 / 50 / 50) + الصدى
print("🔬 الخلط والصدى...")
subprocess.run(["ffmpeg", "-y", "-i", "temp_chunks/quran_clean.wav", "-i", "temp_chunks/quran_rough.wav", "-i", "temp_chunks/quran_malak.wav", "-filter_complex", "[0:a]volume=0.9[a1]; [1:a]volume=0.05[a2]; [2:a]volume=0.05[a3]; [a1][a2][a3]amix=inputs=3:normalize=0,alimiter", "temp_chunks/quran_mixed.wav"], check=True)
subprocess.run(["ffmpeg", "-y", "-i", "temp_chunks/quran_mixed.wav", "-af", "aecho=0.8:0.9:150|300|450:0.3|0.15|0.075", "temp_chunks/quran_rvc.wav"], check=True)

# 5. إعادة تقطيع الآيات الـ RVC بنفس المدد الأصلية
current_time_quran = 0.0
for item in chunk_info:
    if item["is_quran"]:
        dur = item["duration"]
        rvc_chunk_file = f"temp_chunks/rvc_{os.path.basename(item['file'])}"
        subprocess.run(["ffmpeg", "-y", "-ss", str(current_time_quran), "-t", str(dur), "-i", "temp_chunks/quran_rvc.wav", "-ar", "48000", "-ac", "1", rvc_chunk_file], capture_output=True)
        current_time_quran += dur
        item["file"] = rvc_chunk_file

# 6. إعادة دمج السرد (Gemini) مع الآيات (RVC) بالترتيب
print("🔗 الدمج النهائي...")
with open("temp_chunks/final_concat.txt", "w") as f:
    for item in chunk_info:
        f.write(f"file '{os.path.abspath(item['file'])}'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "temp_chunks/final_concat.txt", "-c:a", "libmp3lame", "-b:a", "192k", output_mp3], check=True)
print("✅ اكتمل الدمج بنجاح!")
