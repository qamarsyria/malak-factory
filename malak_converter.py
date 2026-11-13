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

print(f"⏳ Step 1: Pre-cleaning input audio (De-reverb & Noise Reduction)...")

raw_input = os.path.abspath("fixed_input.wav")
pre_cleaned_input = os.path.abspath("pre_cleaned.wav")

# 1. استخراج الصوت
subprocess.run(["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "48000", "-ac", "1", raw_input], capture_output=True)

# 2. فلاتر "قبل" (إزالة الصدى والوشة من القارئ)
pre_filters = (
    "highpass=f=90,"                          # قطع الزمجره
    "lowpass=f=12000,"                        # قطع الهسيس
    "afftdn=nr=15:nf=-25,"                    # إزالة الضوضاء والصدى (De-reverb)
    "agate=threshold=0.05:ratio=2:attack=10:release=50" # قطع أطراف الصدى
)

subprocess.run([
    "ffmpeg", "-y", "-i", raw_input,
    "-af", pre_filters,
    pre_cleaned_input
], capture_output=True)

# ═══════════════════════════════════════════════════════════
# 🧠 Step 2: فحص الصوت (مع سقف للحدة)
# ═══════════════════════════════════════════════════════════
print(f"⏳ Step 2: Analyzing pitch (Pitch Cap 5)...")
TARGET_PITCH_HZ = 200.0
auto_pitch = 4

try:
    import librosa
    y, sr = librosa.load(pre_cleaned_input, sr=None)
    f0 = librosa.yin(y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C7'))
    f0 = f0[(f0 > 60) & (f0 < 500)]
    
    if len(f0) > 0:
        input_pitch_hz = float(np.median(f0))
        raw_shift = round(12 * math.log2(TARGET_PITCH_HZ / input_pitch_hz))
        
        # 🔥 السقف: منع الرفع لأكثر من 5 درجات عشان ما يطلع حاد
        auto_pitch = min(5, max(-4, raw_shift))
        
        print(f"🎯 Input Pitch: {input_pitch_hz:.1f} Hz | Raw Shift: {raw_shift} | Capped Shift: {auto_pitch}")
    else:
        print("⚠️ Pitch not detected, using default 4.")
except Exception as e:
    print(f"⚠️ Pitch analysis failed: {e}")

# ═══════════════════════════════════════════════════════════
# ⚙️ Step 3: RVC Conversion
# ═══════════════════════════════════════════════════════════
print(f"⏳ Step 3: Converting to Malak Voice (Pitch: {auto_pitch})...")

env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

rvc_output = os.path.abspath("rvc_raw_output.wav")
cmd = [
    sys.executable, "-m", "infer.cli",
    "--model", MODEL_PATH,
    "--input", pre_cleaned_input,
    "--output", rvc_output,
    "--f0-method", "rmvpe",
    "--pitch", str(auto_pitch),
    "--protect", "0.15",       # حماية منخفضة لمسح صوت القارئ
    "--index-rate", "0.85",    # استخدام بصمة ملك بقوة
    "--rms-mix-rate", "0.25",
    "--index", INDEX_PATH,
    "--overwrite"
]

result = subprocess.run(cmd, cwd=RVC_DIR, env=env)
if result.returncode != 0:
    sys.exit(1)

# ═══════════════════════════════════════════════════════════
# 🧼 Step 4: Post-Filters (تنظيف نهائي لوضوح الحروف)
# ═══════════════════════════════════════════════════════════
print("⏳ Step 4: Applying final polish (Post-Filters)...")

post_filters = (
    "highpass=f=80,"
    "equalizer=f=2500:t=q:w=2:g=2.5,"         # إبراز النون والراء
    "equalizer=f=400:t=q:w=2:g=-2,"           # تقليل البلغم
    "acompressor=threshold=0.1:ratio=3:attack=20:release=250,"
    "alimiter=level_in=1:level_out=0.95"
)

subprocess.run([
    "ffmpeg", "-y", "-i", rvc_output,
    "-af", post_filters,
    "-ar", "48000", "-ac", "1",
    OUTPUT_AUDIO
], capture_output=True)

# تنظيف الملفات المؤقتة
for f in [rvc_output, raw_input, pre_cleaned_input]:
    if os.path.exists(f): os.remove(f)

print("✅ Done:", OUTPUT_AUDIO)
