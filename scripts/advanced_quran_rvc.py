import os
import subprocess
import sys
from faster_whisper import WhisperModel
from pydub import AudioSegment
import warnings
warnings.filterwarnings("ignore")

INPUT_AUDIO = sys.argv[1]
OUTPUT_AUDIO = sys.argv[2]
MODEL_PATH = os.path.abspath("malak_voice.pth")
INDEX_PATH = os.path.abspath("added_malak_voice.index")
RVC_DIR = "Retrieval-based-Voice-Conversion-WebUI"
TEMP_DIR = "tmp_chunks"

os.makedirs(TEMP_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# Step 1: Slow down
# ─────────────────────────────────────────────
print("⏳ Step 1: Slowing down audio...")
slowed_audio = os.path.abspath("slowed_input.wav")
res = subprocess.run(
    ["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "16000", "-ac", "1", "-af", "atempo=0.8", slowed_audio],
    capture_output=True, text=True
)
if res.returncode != 0:
    print("❌ Slow-down failed:", res.stderr[-500:])
    sys.exit(1)
print(f"✅ Slowed audio: {os.path.getsize(slowed_audio)} bytes")

# ─────────────────────────────────────────────
# Step 2: Whisper
# ─────────────────────────────────────────────
print("⏳ Step 2: Whisper alignment (language=ar)...")
segments_list = []
try:
    model = WhisperModel("small", device="cpu", compute_type="float32")
    segments, info = model.transcribe(
        slowed_audio,
        language="ar",
        word_timestamps=True,
        vad_filter=True,
        beam_size=5
    )
    segments_list = list(segments)
    print(f"✅ Language: {info.language} (prob {info.language_probability:.2f})")
    print(f"✅ Segments found: {len(segments_list)}")
    for seg in segments_list:
        print(f"   → {seg.text.strip()}")
        if seg.words:
            for w in seg.words:
                print(f"      word: '{w.word.strip()}' [{w.start:.2f}s - {w.end:.2f}s]")
except Exception as e:
    print(f"❌ Whisper failed: {e}")

# ─────────────────────────────────────────────
# Step 3: Slice
# ─────────────────────────────────────────────
print("⏳ Step 3: Slicing...")
audio = AudioSegment.from_wav(slowed_audio)
chunk_files = []

for i, segment in enumerate(segments_list):
    if not segment.words:
        continue
    for j, word in enumerate(segment.words):
        start_ms = max(0, int(word.start * 1000) - 30)
        end_ms = min(len(audio), int(word.end * 1000) + 30)
        if end_ms - start_ms < 50:
            continue
        chunk = audio[start_ms:end_ms]
        chunk_file = os.path.abspath(f"{TEMP_DIR}/word_{i:03d}_{j:03d}.wav")
        chunk.export(chunk_file, format="wav")
        chunk_files.append(chunk_file)

# Fallback: if whisper failed, use whole audio as one chunk
if not chunk_files:
    print("⚠️ No chunks from Whisper. Fallback: treating whole audio as one chunk.")
    fallback = os.path.abspath(f"{TEMP_DIR}/whole.wav")
    audio.export(fallback, format="wav")
    chunk_files = [fallback]

print(f"✅ Chunks: {len(chunk_files)}")

# ─────────────────────────────────────────────
# Step 4: RVC per chunk
# ─────────────────────────────────────────────
print("⏳ Step 4: RVC per chunk...")
env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

converted = []
for idx, chunk_file in enumerate(chunk_files):
    out = os.path.abspath(f"{TEMP_DIR}/conv_{idx:03d}.wav")
    cmd = [
        sys.executable, "-m", "infer.cli",
        "--model", MODEL_PATH, "--input", chunk_file, "--output", out,
        "--f0-method", "rmvpe", "--pitch", "12",
        "--protect", "0.5", "--index-rate", "0.8", "--rms-mix-rate", "0.25",
        "--index", INDEX_PATH, "--overwrite"
    ]
    r = subprocess.run(cmd, cwd=RVC_DIR, env=env, capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 100:
        converted.append(out)
        print(f"   ✅ {idx+1}/{len(chunk_files)}")
    else:
        print(f"   ❌ {idx+1} failed: {r.stderr[-200:]}")

if not converted:
    print("❌ No chunks converted!")
    sys.exit(1)

# ─────────────────────────────────────────────
# Step 5: Merge
# ─────────────────────────────────────────────
print("⏳ Step 5: Merging...")
merged = AudioSegment.empty()
for f in converted:
    merged += AudioSegment.from_wav(f)
merged_path = os.path.abspath("merged_slowed.wav")
merged.export(merged_path, format="wav")
print(f"✅ Merged: {os.path.getsize(merged_path)} bytes")

# ─────────────────────────────────────────────
# Step 6: Speed up + polish
# ─────────────────────────────────────────────
print("⏳ Step 6: Final polish...")
post_filters = (
    "atempo=1.25,"
    "highpass=f=90,"
    "lowpass=f=14000,"
    "afftdn=nf=-30,"
    "equalizer=f=350:t=q:w=2:g=-3,"
    "equalizer=f=1200:t=q:w=2:g=2.5,"
    "equalizer=f=2500:t=q:w=2:g=2,"
    "equalizer=f=4500:t=q:w=2:g=-2,"
    "equalizer=f=8000:t=q:w=3:g=-3,"
    "acompressor=threshold=0.089:ratio=3:attack=20:release=250,"
    "alimiter=level_in=1:level_out=0.9"
)
r = subprocess.run(
    ["ffmpeg", "-y", "-i", merged_path, "-af", post_filters, "-ar", "48000", "-ac", "1", "-b:a", "192k", OUTPUT_AUDIO],
    capture_output=True, text=True
)
if r.returncode != 0:
    print("❌ Final filter failed:", r.stderr[-500:])
    sys.exit(1)

print(f"✅ DONE! Output: {os.path.getsize(OUTPUT_AUDIO)} bytes")
