import os, sys, json, subprocess
from pathlib import Path

social_mp3 = os.path.abspath(sys.argv[1])
meta_json = os.path.abspath(sys.argv[2])
output_mp3 = os.path.abspath(sys.argv[3])

def get_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0

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
        chunk_info.append({
            "file": os.path.abspath(chunk_file),
            "is_quran": is_quran,
            "duration": dur,
            "reciter": chunk.get("reciter", "")  # 🌟 نقرأ القارئ
        })

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

print("🎙️ تحويل الآيات بالبصمة الثلاثية...")
for item in chunk_info:
    if not item["is_quran"]:
        continue  # السرد لا يمر بـ RVC
    
    input_wav = item["file"]
    base_name = os.path.basename(input_wav).replace(".wav", "")
    reciter = item.get("reciter", "Yasser_Ad-Dussary_128kbps")
    
    # 🌟 التسريع حسب القارئ
    if "Yasser" in reciter:
        tempo = 1.05  # ياسر: 5%
        tempo_label = "5% (Yasser)"
    else:
        tempo = 1.15  # الحصري: 15%
        tempo_label = "15% (Husary)"
    
    print(f"   🎵 {base_name} - {tempo_label}")
    
    # RVC (3 طبقات)
    for name, model, idx, pitch in [("clean", model_clean, idx_clean, "-6"), ("rough", model_rough, idx_rough, "-4"), ("malak", model_malak, idx_malak, "0")]:
        out_wav = os.path.abspath(f"temp_chunks/rvc_{base_name}_{name}.wav")
        subprocess.run([PY, "infer/cli.py", "--model", model, "--input", input_wav, "--output", out_wav, "--index", idx, "--f0-method", "rmvpe", "--pitch", pitch, "--protect", "0.4", "--index-rate", "0.4", "--rms-mix-rate", "0.8"], cwd=rvc_dir, env=env, check=True)
    
    # Mix (3 طبقات)
    mixed_wav = os.path.abspath(f"temp_chunks/mix_{base_name}.wav")
    echo_wav = os.path.abspath(f"temp_chunks/echo_{base_name}.wav")
    subprocess.run([
        "ffmpeg", "-y", 
        "-i", os.path.abspath(f"temp_chunks/rvc_{base_name}_clean.wav"),
        "-i", os.path.abspath(f"temp_chunks/rvc_{base_name}_rough.wav"),
        "-i", os.path.abspath(f"temp_chunks/rvc_{base_name}_malak.wav"),
        "-filter_complex", "[0:a]volume=0.9[a1]; [1:a]volume=0.05[a2]; [2:a]volume=0.05[a3]; [a1][a2][a3]amix=inputs=3:normalize=0,alimiter",
        mixed_wav
    ], check=True)
    
    # 🌟 التسريع + الصدى (بعد RVC فقط)
    dur_before_tempo = get_dur(mixed_wav)
    subprocess.run(["ffmpeg", "-y", "-i", mixed_wav, "-af", f"atempo={tempo},aecho=0.8:0.85:100|200:0.15|0.08", echo_wav], check=True)
    new_dur = get_dur(echo_wav)
    
    print(f"   ✅ {base_name}: {dur_before_tempo:.2f}s → {new_dur:.2f}s (tempo={tempo})")
    
    item["file"] = echo_wav
    item["duration"] = new_dur

print("🔗 الدمج النهائي...")
with open("temp_chunks/final_concat.txt", "w") as f:
    for item in chunk_info:
        f.write(f"file '{item['file']}'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "temp_chunks/final_concat.txt", "-c:a", "libmp3lame", "-b:a", "192k", output_mp3], check=True)

# 🌟 تحديث الميتاداتا بالمدد الجديدة (عشان الفيديو يتزامن)
flat_idx = 0
for seg in meta["segments"]:
    for chunk in seg.get("chunks", []):
        if flat_idx < len(chunk_info):
            chunk["duration"] = chunk_info[flat_idx]["duration"]
        flat_idx += 1

meta["total_duration"] = sum(c["duration"] for seg in meta["segments"] for c in seg.get("chunks", []))
with open(meta_json, "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)

print("✅ اكتمل الدمج بنجاح!")
