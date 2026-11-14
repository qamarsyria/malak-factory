import os
import subprocess
import sys
import math
import warnings
import numpy as np

warnings.filterwarnings("ignore")

if len(sys.argv) < 3:
    sys.exit(1)

INPUT_AUDIO = os.path.abspath(sys.argv[1])
OUTPUT_AUDIO = os.path.abspath(sys.argv[2])
MODEL_PATH = os.path.abspath("malak_voice.pth")
INDEX_PATH = os.path.abspath("added_malak_voice.index")
RVC_DIR = "Retrieval-based-Voice-Conversion-WebUI"

# 🔥 تعديل الحدة اليدوي (زيادة أو نقصان درجة)
PITCH_OFFSET = int(os.environ.get("PITCH_OFFSET", "0"))

print(f"⏳ Step 1: Minimal Pre-Filter...")
raw_input = os.path.abspath("fixed_input.wav")
pre_cleaned = os.path.abspath("pre_cleaned.wav")

subprocess.run(["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "48000", "-ac", "1", raw_input], capture_output=True)

pre_filters = (
    "highpass=f=60,"
    "lowpass=f=15500,"
    "afftdn=nr=2:nf=-50"
)
subprocess.run(["ffmpeg", "-y", "-i", raw_input, "-af", pre_filters, pre_cleaned], capture_output=True)

# ═══════════════════════════════════════════════════════════
# 🧠 العقل المدبر + PITCH_OFFSET
# ═══════════════════════════════════════════════════════════
print(f"⏳ Step 2: Analyzing Pitch (Offset: {PITCH_OFFSET:+d})...")
TARGET_PITCH_HZ = 190.0
auto_pitch = 4

try:
    import librosa
    y, sr = librosa.load(pre_cleaned, sr=None)
    f0, voiced_flag, _ = librosa.pyin(y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C7'))
    f0_voiced = f0[voiced_flag]
    
    if len(f0_voiced) > 0:
        input_pitch_hz = np.median(f0_voiced)
        auto_pitch = round(12 * math.log2(TARGET_PITCH_HZ / input_pitch_hz))
        print(f"🎯 Input: {input_pitch_hz:.1f} Hz | Auto: {auto_pitch}")
except Exception as e:
    print(f"⚠️ Pitch failed, using default 4.")

# 🔥 إضافة التعديل اليدوي
final_pitch = auto_pitch + PITCH_OFFSET
print(f"🔥 Final Pitch (Auto + Offset): {auto_pitch} + {PITCH_OFFSET} = {final_pitch}")

# ═══════════════════════════════════════════════════════════
# ⚙️ RVC Conversion
# ═══════════════════════════════════════════════════════════
print(f"⏳ Step 3: RVC Conversion (Pitch: {final_pitch})...")
env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

rvc_output = os.path.abspath("rvc_raw.wav")
cmd = [
    sys.executable, "-m", "infer.cli",
    "--model", MODEL_PATH, "--input", pre_cleaned, "--output", rvc_output,
    "--f0-method", "rmvpe", "--pitch", str(final_pitch),
    "--protect", "0.3",
    "--index-rate", "0.4",
    "--rms-mix-rate", "0.25",
    "--index", INDEX_PATH, "--overwrite"
]
subprocess.run(cmd, cwd=RVC_DIR, env=env)

# ═══════════════════════════════════════════════════════════
# 🧼 Post-Filters (مع تخفيف الغباش في المدود)
# ═══════════════════════════════════════════════════════════
print(f"⏳ Step 4: Final Polish (Reducing Muddy Reverb in Mudd)...")

post_filters = (
    "highpass=f=90,"
    "lowpass=f=14000,"
    "afftdn=nf=-30,"
    "equalizer=f=350:t=q:w=2:g=-3,"
    "equalizer=f=1200:t=q:w=2:g=2.5,"
    "equalizer=f=2500:t=q:w=2:g=2,"
    "equalizer=f=4500:t=q:w=2:g=-2,"
    "equalizer=f=8000:t=q:w=3:g=-3,"          # 🔥 تخفيف الغباش في المدود (ترددات عالية)
    "deesser=i=0.3:m=0.5:f=6000,"             # 🔥 تقليل حدة السين أثناء المد
    "acompressor=threshold=0.089:ratio=3:attack=20:release=250,"
    "alimiter=level_in=1:level_out=0.9"
)

subprocess.run(["ffmpeg", "-y", "-i", rvc_output, "-af", post_filters, "-ar", "48000", "-ac", "1", OUTPUT_AUDIO], capture_output=True)

for f in [rvc_output, raw_input, pre_cleaned]:
    if os.path.exists(f): os.remove(f)

print("✅ Done:", OUTPUT_AUDIO)
