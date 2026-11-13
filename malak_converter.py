import os
import subprocess
import sys
import math
import warnings

warnings.filterwarnings("ignore")

if len(sys.argv) < 3:
    print("❌ Usage: python malak_converter.py <input_audio> <output_audio>")
    sys.exit(1)

INPUT_AUDIO = os.path.abspath(sys.argv[1])
OUTPUT_AUDIO = os.path.abspath(sys.argv[2])
MODEL_PATH = os.path.abspath("malak_voice.pth")
INDEX_PATH = os.path.abspath("added_malak_voice.index")
RVC_DIR = "Retrieval-based-Voice-Conversion-WebUI"

if not os.path.exists(INPUT_AUDIO):
    print(f"❌ File {INPUT_AUDIO} not found!")
    sys.exit(1)

print(f"⏳ Step 1: Analyzing input voice pitch...")

fixed_input = os.path.abspath("fixed_input.wav")
subprocess.run(["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "48000", "-ac", "1", fixed_input], capture_output=True)

# ═══════════════════════════════════════════════════════════
# 🧠 العقل المدبر (نسخة محسنة - هدف 190 هرتز)
# ═══════════════════════════════════════════════════════════
TARGET_PITCH_HZ = 190.0 # 🔥 نزلنا الهدف من 210 لـ 190 عشان نقلل الحدة
auto_pitch = 4

try:
    import librosa
    import numpy as np
    
    y, sr = librosa.load(fixed_input, sr=None)
    f0, voiced_flag, voiced_probs = librosa.pyin(y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C7'))
    f0_voiced = f0[voiced_flag]
    
    if len(f0_voiced) > 0:
        input_pitch_hz = np.median(f0_voiced)
        auto_pitch = round(12 * math.log2(TARGET_PITCH_HZ / input_pitch_hz))
        print(f"🎯 Input Pitch: {input_pitch_hz:.1f} Hz | Target: {TARGET_PITCH_HZ} Hz")
        print(f"🔥 Auto-Calculated Pitch Shift: {auto_pitch} semitones")
    else:
        print("⚠️ Could not detect pitch, using default pitch 4.")
except Exception as e:
    print(f"⚠️ Pitch analysis failed ({e}), using default pitch 4.")

# ═══════════════════════════════════════════════════════════
# ⚙️ Step 2: RVC Conversion (إعدادات مضادة للبلغم)
# ═══════════════════════════════════════════════════════════
print(f"⏳ Step 2: Converting to Malak Voice via RVC...")

env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

rvc_output = os.path.abspath("rvc_raw_output.wav")
cmd = [
    sys.executable, "-m", "infer.cli",
    "--model", MODEL_PATH,
    "--input", fixed_input,
    "--output", rvc_output,
    "--f0-method", "rmvpe",
    "--pitch", str(auto_pitch),
    "--protect", "0.5",          # 🔥 رفعنا الحماية عشان نقلل خلط الصوت
    "--index-rate", "0.8",       # 🔥 استخدام البصمة عشان نمسح التخنيقة
    "--rms-mix-rate", "0.25",    # 🔥 منع التلاعب بمستوى الصوت (يمسح البلغم)
    "--index", INDEX_PATH,
    "--overwrite"
]

result = subprocess.run(cmd, cwd=RVC_DIR, env=env)

if result.returncode != 0:
    print("❌ RVC Conversion failed!")
    sys.exit(1)

print("✅ Step 2: RVC Conversion Done.")

# ═══════════════════════════════════════════════════════════
# 🧼 Step 3: Cleaning Audio (فلاتر موسعة)
# ═══════════════════════════════════════════════════════════
print("⏳ Step 3: Cleaning audio...")

audio_filters = (
    "highpass=f=90,"                          # قطع الترددات المنخفضة جداً (سبب البلغم)
    "lowpass=f=14000,"                        # قطع الترددات العالية جداً (سبب الحدة)
    "afftdn=nf=-30,"                          # إزالة ضوضاء متوسطة
    "equalizer=f=350:t=q:w=2:g=-3,"           # 🔥 قطع "البلغم" (ترددات 350Hz)
    "equalizer=f=2500:t=q:w=2:g=2,"           # إبراز وضوح الحروف
    "equalizer=f=4500:t=q:w=2:g=-2,"          # تخفيف الحدة الإضافية
    "acompressor=threshold=0.089:ratio=3:attack=20:release=250,"
    "alimiter=level_in=1:level_out=0.9"
)

cleaned_output = OUTPUT_AUDIO
ffmpeg_clean_cmd = [
    "ffmpeg", "-y", "-i", rvc_output,
    "-af", audio_filters,
    "-ar", "48000", "-ac", "1",
    cleaned_output
]

res_clean = subprocess.run(ffmpeg_clean_cmd, capture_output=True, text=True)

if res_clean.returncode != 0:
    print("⚠️ Cleaning failed, keeping raw RVC output.")
    os.replace(rvc_output, OUTPUT_AUDIO)
else:
    print("✅ Step 3: Audio cleaned successfully.")

if os.path.exists(rvc_output) and rvc_output != OUTPUT_AUDIO:
    os.remove(rvc_output)
if os.path.exists(fixed_input):
    os.remove(fixed_input)

print("✅ Done:", OUTPUT_AUDIO)
