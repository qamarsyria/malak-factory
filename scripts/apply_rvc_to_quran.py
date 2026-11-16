import os, sys, json, subprocess
from pathlib import Path

social_mp3 = os.path.abspath(sys.argv[1])
meta_json = os.path.abspath(sys.argv[2])
output_mp3 = os.path.abspath(sys.argv[3])

def get_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    try: return round(float(r.stdout.strip()), 3)
    except: return 0.0

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
            "reciter": chunk.get("reciter", "Yasser_Ad-Dussary_128kbps")
        })

PY = sys.executable
rvc_dir = os.path.abspath("rvc")

for m in ["assets/weights/clean.pth", "assets/weights/rough.pth", "assets/weights/malak.pth"]:
    if not os.path.exists(os.path.join(rvc_dir, m)):
        print(f"❌ الموديل مفقود: {m}")
        sys.exit(1)

env = os.environ.copy()
env["PYTHONPATH"] = rvc_dir

print("🎙️ تحويل الآيات...")
for item in chunk_info:
    if not item["is_quran"]: continue
    
    input_wav = item["file"]
    base_name = os.path.basename(input_wav).replace(".wav", "")
    reciter = item["reciter"]
    
    # 🌟 التسريع: ياسر 5% / الحصري 20%
    tempo = 1.05 if "Yasser" in reciter else 1.20
    tempo_label = "5% (Yasser)" if "Yasser" in reciter else "20% (Husary)"
    print(f"   🎵 {base_name} - {tempo_label}")
    
    # RVC (3 طبقات)
    for name, model, idx, pitch in [
        ("clean", "assets/weights/clean.pth", "clean.index", "-6"),
        ("rough", "assets/weights/rough.pth", "rough.index", "-4"),
        ("malak", "assets/weights/malak.pth", "malak.index", "0")
    ]:
        out_wav = os.path.abspath(f"temp_chunks/rvc_{base_name}_{name}.wav")
        subprocess.run([PY, "infer/cli.py", "--model", model, "--input", input_wav, "--output", out_wav, "--index", idx, "--f0-method", "rmvpe", "--pitch", pitch, "--protect", "0.4", "--index-rate", "0.4", "--rms-mix-rate", "0.8"], cwd=rvc_dir, env=env, check=True)
    
    # Mix
    mixed_wav = os.path.abspath(f"temp_chunks/mix_{base_name}.wav")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", os.path.abspath(f"temp_chunks/rvc_{base_name}_clean.wav"),
        "-i", os.path.abspath(f"temp_chunks/rvc_{base_name}_rough.wav"),
        "-i", os.path.abspath(f"temp_chunks/rvc_{base_name}_malak.wav"),
        "-filter_complex", "[0:a]volume=0.9[a1]; [1:a]volume=0.05[a2]; [2:a]volume=0.05[a3]; [a1][a2][a3]amix=inputs=3:normalize=0,alimiter",
        mixed_wav
    ], check=True)
    
    # Speedup + صدى خفيف (بعد RVC)
    echo_wav = os.path.abspath(f"temp_chunks/final_{base_name}.wav")
    subprocess.run(["ffmpeg", "-y", "-i", mixed_wav, "-af", f"atempo={tempo},aecho=0.8:0.85:100|200:0.15|0.08", echo_wav], check=True)
    
    item["file"] = echo_wav
    item["duration"] = get_dur(echo_wav)

print("🔗 الدمج النهائي...")
with open("temp_chunks/final_concat.txt", "w") as f:
    for item in chunk_info:
        f.write(f"file '{item['file']}'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "temp_chunks/final_concat.txt", "-c:a", "libmp3lame", "-b:a", "192k", output_mp3], check=True)

# تحديث الميتاداتا
flat_idx = 0
for seg in meta["segments"]:
    for chunk in seg.get("chunks", []):
        if flat_idx < len(chunk_info):
            chunk["duration"] = chunk_info[flat_idx]["duration"]
        flat_idx += 1

meta["total_duration"] = sum(c["duration"] for seg in meta["segments"] for c in seg.get("chunks", []))
with open(meta_json, "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)

print("✅ اكتمل!")
