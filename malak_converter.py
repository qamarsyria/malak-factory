import os
import subprocess
import sys

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

# فلاتر قبل التحويل (خفيفة عشان ما نمسح تفاصيل الصوت)
pre_filters = (
    "highpass=f=80,"
    "lowpass=f=12000,"
    "afftdn=nf=-30"
)

subprocess.run([
    "ffmpeg", "-y", "-i", raw_input,
    "-af", pre_filters,
    pre_cleaned_input
], capture_output=True)

# ═══════════════════════════════════════════════════════════
# ⚙️ Step 2: RVC Conversion (الرقم الثابت 7)
# ═══════════════════════════════════════════════════════════
print(f"⏳ Step 2: Converting to Malak Voice (Fixed Pitch: 7, Index: 0.6)...")

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
    "--pitch", "7",            # 🔥 الحدة الثابتة (حل وسط بين الأنوثة والحدة)
    "--protect", "0.4",        # 🔥 حماية الحروف
    "--index-rate", "0.6",     # 🔥 ننزل الـ Index عشان نمسح الخشة
    "--rms-mix-rate", "0.3",   # 🔥 مستوى طبيعي
    "--index", INDEX_PATH,
    "--overwrite"
]

result = subprocess.run(cmd, cwd=RVC_DIR, env=env)
if result.returncode != 0:
    sys.exit(1)

# ═══════════════════════════════════════════════════════════
# 🧼 Step 3: Post-Filters (بسيطة جداً)
# ═══════════════════════════════════════════════════════════
print("⏳ Step 3: Applying Final Polish...")

# شلنا كل الـ equalizers القوية اللي كانت بتعمل خشة
post_filters = (
    "highpass=f=80,"
    "lowpass=f=14000,"
    "equalizer=f=2500:t=q:w=2:g=1.5,"         # إبراز وضوح الحروف شوي بس
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
