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

print(f"⏳ Step 1: Pre-cleaning input audio...")

raw_input = os.path.abspath("fixed_input.wav")
pre_cleaned_input = os.path.abspath("pre_cleaned.wav")

subprocess.run(["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "48000", "-ac", "1", raw_input], capture_output=True)

# فلاتر "قبل" (إزالة الصدى والوشة من القارئ)
pre_filters = (
    "highpass=f=90,"
    "lowpass=f=12000,"
    "afftdn=nr=10:nf=-30,"
    "agate=threshold=0.05:ratio=2:attack=10:release=50"
)

subprocess.run([
    "ffmpeg", "-y", "-i", raw_input,
    "-af", pre_filters,
    pre_cleaned_input
], capture_output=True)

# ═══════════════════════════════════════════════════════════
# 🧠 Step 2: فحص الصوت (مع سقف للأنوثة 10-12)
# ═══════════════════════════════════════════════════════════
print(f"⏳ Step 2: Analyzing pitch (Target 215Hz for Femininity)...")
TARGET_PITCH_HZ = 215.0
auto_pitch = 10

try:
    import librosa
    y, sr = librosa.load(pre_cleaned_input, sr=None)
    f0 = librosa.yin(y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C7'))
    f0 = f0[(f0 > 60) & (f0 < 500)]
    
    if len(f0) > 0:
        input_pitch_hz = float(np.median(f0))
        raw_shift = round(12 * math.log2(TARGET_PITCH_HZ / input_pitch_hz))
        
        # 🔥 السقف: منع الرفع لأكثر من 12 (أوكتاف كامل) وعدم النزول عن 8 (عشان الأنوثة)
        auto_pitch = max(8, min(12, raw_shift))
        
        print(f"🎯 Input: {input_pitch_hz:.1f} Hz | Raw: {raw_shift} | Final: {auto_pitch}")
    else:
        print("⚠️ Pitch not detected, using default 10.")
except Exception as e:
    print(f"⚠️ Pitch analysis failed: {e}")

# ═══════════════════════════════════════════════════════════
# ⚙️ Step 3: RVC Conversion (التوازن الذهبي)
# ═══════════════════════════════════════════════════════════
print(f"⏳ Step 3: Converting (Pitch: {auto_pitch} | Index: 0.95 | Protect: 0.33)...")

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
    "--protect", "0.33",       # 🔥 الإصلاح: 0.33 عشان الحروف ترجع طبيعية
    "--index-rate", "0.95",    # 🔥 الإصلاح: 0.95 عشان نمسح جرس القارئ
    "--rms-mix-rate", "0.15",  # 🔥 الإصلاح: 0.15 عشان نقلل ترددات القارئ
    "--index", INDEX_PATH,
    "--overwrite"
]

result = subprocess.run(cmd, cwd=RVC_DIR, env=env)
if result.returncode != 0:
    sys.exit(1)

# ═══════════════════════════════════════════════════════════
# 🧼 Step 4: Post-Filters (فلتر الأنوثة + وضوح الحروف)
# ═══════════════════════════════════════════════════════════
print("⏳ Step 4: Applying Final Polish...")

post_filters = (
    "highpass=f=80,"
    "lowpass=f=14000,"
    "equalizer=f=250:t=q:w=1:g=-3,"           # قطع ترددات الرجولة (200-300Hz)
    "equalizer=f=900:t=q:w=1:g=3,"            # 🔥 تكبير ترددات الأنوثة (800-1000Hz)
    "equalizer=f=2500:t=q:w=2:g=2,"           # إبراز وضوح الحروف (النون والميم)
    "equalizer=f=4500:t=q:w=2:g=-2,"          # تقليل الحدة الزائدة
    "acompressor=threshold=0.1:ratio=3:attack=20:release=250,"
    "alimiter=level_in=1:level_out=0.95"
)

subprocess.run([
    "ffmpeg", "-y", "-i", rvc_output,
    "-af", post_filters,
    "-ar", "48000", "-ac", "1",
    OUTPUT_AUDIO
], capture_output=True)

for f in [rvc_output, raw_input, pre_cleaned_input]:
    if os.path.exists(f): os.remove(f)

print("✅ Done:", OUTPUT_AUDIO)
