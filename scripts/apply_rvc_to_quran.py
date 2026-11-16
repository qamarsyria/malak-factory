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

for seg_idx, seg in enumerate(segments):
    is_quran = (seg["type"] == "quran")
    for c_idx, chunk in enumerate(seg["chunks"]):
        dur = chunk["duration"]
        chunk_file = f"temp_chunks/seg_{seg_idx}_chunk_{c_idx}.wav"
        subprocess.run(["ffmpeg", "-y", "-ss", str(current_time), "-t", str(dur), "-i", social_mp3, "-ar", "48000", "-ac", "1", chunk_file], capture_output=True)
        current_time += dur
        chunk_info.append({"file": os.path.abspath(chunk_file), "is_quran": is_quran, "duration": dur})

PY = sys.executable
rvc_dir = os.path.abspath("rvc")

model_clean = os.path.join(rvc_dir, "assets/weights/clean.pth")
model_rough = os.path.join(rvc_dir, "assets/weights/rough.pth")
model_malak = os.path.join(rvc_dir, "assets/weights/malak.pth")
idx_clean = os.path.join(rvc_dir, "clean.index")
idx_rough = os.path.join(rvc_dir, "rough.index")
idx_malak = os.path.join(rvc_dir, "malak.index")

for m in [model_clean, model_rough, model_malak]:
    if not os.path.exists(m):
        print(f"❌ الموديل مفقود: {m}")
        sys.exit(1)

env = os.environ.copy()
env["PYTHONPATH"] = rvc_dir

print("🎙️ تحويل الآيات (آية آية)...")
for item in chunk_info:
    if not item["is_quran"]:
        continue
    
    input_wav = item["file"]
    base_name = os.path.basename(input_wav).replace(".wav", "")
    
    for name, model, idx, pitch in [("clean", model_clean, idx_clean, "-6"), ("rough", model_rough, idx_rough, "-4"), ("malak", model_malak, idx_malak, "0")]:
        out_wav = os.path.abspath(f"temp_chunks/rvc_{base_name}_{name}.wav")
        subprocess.run([PY, "infer/cli.py", "--model", model, "--input", input_wav, "--output", out_wav, "--index", idx, "--f0-method", "rmvpe", "--pitch", pitch, "--protect", "0.4", "--index-rate", "0.4", "--rms-mix-rate", "0.8"], cwd=rvc_dir, env=env, check=True)
    
    mixed_wav = os.path.abspath(f"temp_chunks/mix_{base_name}.wav")
    echo_wav = os.path.abspath(f"temp_chunks/echo_{base_name}.wav")
    
    subprocess.run(["ffmpeg", "-y", "-i", os.path.abspath(f"temp_chunks/rvc_{base_name}_clean.wav"), "-i", os.path.abspath(f"temp_chunks/rvc_{base_name}_rough.wav"), "-i", os.path.abspath(f"temp_chunks/rvc_{base_name}_malak.wav"), "-filter_complex", "[0:a]volume=0.9[a1]; [1:a]volume=0.05[a2]; [2:a]volume=0.05[a3]; [a1][a2][a3]amix=inputs=3:normalize=0,alimiter", mixed_wav], check=True)
    
    # 🌟 التعديلات: سرعة +10% (atempo=1.1) + صدى أخف بكثير
    subprocess.run(["ffmpeg", "-y", "-i", mixed_wav, "-af", "atempo=1.1,aecho=0.8:0.85:100|200:0.15|0.08", echo_wav], check=True)
    
    item["file"] = echo_wav

print("🔗 الدمج النهائي...")
with open("temp_chunks/final_concat.txt", "w") as f:
    for item in chunk_info:
        f.write(f"file '{item['file']}'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "temp_chunks/final_concat.txt", "-c:a", "libmp3lame", "-b:a", "192k", output_mp3], check=True)
print("✅ اكتمل الدمج بنجاح!")
