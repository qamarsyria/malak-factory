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

print(f"⏳ Step 1: Pre-Filtering (De-reverb & Noise Reduction)...")
raw_input = os.path.abspath("fixed_input.wav")
pre_cleaned = os.path.abspath("pre_cleaned.wav")

subprocess.run(["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "48000", "-ac", "1", raw_input], capture_output=True)

# 🔥 الفلاتر القبلية (اللي بنختبرها هلأ)
pre_filters = (
    "highpass=f=90,"                          
    "lowpass=f=12000,"                        
    "afftdn=nr=20:nf=-25,"                    # إزالة الصدى والضوضاء
    "agate=threshold=0.05:ratio=2:attack=10:release=50" # قطع أطراف الصدى
)

subprocess.run(["ffmpeg", "-y", "-i", raw_input, "-af", pre_filters, pre_cleaned], capture_output=True)

# ═══════════════════════════════════════════════════════════
# 🧠 العقل المدبر (الكود الذهبي)
# ═══════════════════════════════════════════════════════════
print(f"⏳ Step 2: Analyzing Pitch...")
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
        print(f"🎯 Input: {input_pitch_hz:.1f} Hz | Target: {TARGET_PITCH_HZ} Hz | Shift: {auto_pitch}")
except Exception as e:
    print(f"⚠️ Pitch failed, using default 4.")

print(f"⏳ Step 3: RVC Conversion (Pitch: {auto_pitch})...")
env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

rvc_output = os.path.abspath("rvc_raw.wav")
cmd = [
    sys.executable, "-m", "infer.cli",
    "--model", MODEL_PATH, "--input", pre_cleaned, "--output", rvc_output,
    "--f0-method", "rmvpe", "--pitch", str(auto_pitch),
    "--protect", "0.5", "--index-rate", "0.8", "--rms-mix-rate", "0.25",
    "--index", INDEX_PATH, "--overwrite"
]
subprocess.run(cmd, cwd=RVC_DIR, env=env)

print(f"⏳ Step 4: Post-Filters (Final Polish)...")
# فلاتر البوست (نفس الكود الذهبي)
post_filters = (
    "highpass=f=90,"
    "lowpass=f=14000,"
    "afftdn=nf=-30,"
    "equalizer=f=350:t=q:w=2:g=-3,"           
    "equalizer=f=2500:t=q:w=2:g=2,"           
    "equalizer=f=4500:t=q:w=2:g=-2,"          
    "acompressor=threshold=0.089:ratio=3:attack=20:release=250,"
    "alimiter=level_in=1:level_out=0.9"
)

subprocess.run(["ffmpeg", "-y", "-i", rvc_output, "-af", post_filters, "-ar", "48000", "-ac", "1", OUTPUT_AUDIO], capture_output=True)

for f in [rvc_output, raw_input, pre_cleaned]:
    if os.path.exists(f): os.remove(f)

print("✅ Done:", OUTPUT_AUDIO)
