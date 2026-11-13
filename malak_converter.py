import os
import subprocess
import sys

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

print(f"⏳ Step 1: Converting {INPUT_AUDIO} to Malak Voice via RVC...")

fixed_input = os.path.abspath("fixed_input.wav")
subprocess.run(["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "48000", "-ac", "1", fixed_input], capture_output=True)

env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

# الخطوة 1: تحويل RVC
rvc_output = os.path.abspath("rvc_raw_output.wav")
cmd = [
    sys.executable, "-m", "infer.cli",
    "--model", MODEL_PATH,
    "--input", fixed_input,
    "--output", rvc_output,
    "--f0-method", "rmvpe",
    "--pitch", "2",           # الحدة اللي اخترتها
    "--protect", "0.33",
    "--index-rate", "0",      
    "--index", INDEX_PATH,
    "--overwrite"
]

result = subprocess.run(cmd, cwd=RVC_DIR, env=env)

if result.returncode != 0:
    print("❌ RVC Conversion failed!")
    sys.exit(1)

print("✅ Step 1: RVC Conversion Done.")

# ═══════════════════════════════════════════════════════════
# 🧼 الخطوة 2: تنظيف الصوت (إزالة الوشة والهسيس)
# ═══════════════════════════════════════════════════════════
print("⏳ Step 2: Cleaning audio (removing noise/fuzz)...")

# سلسلة الفلاتر الاحترافية
audio_filters = (
    "highpass=f=80,"                          # قطع الترددات المنخفضة جداً
    "lowpass=f=15000,"                        # قطع الترددات العالية جداً (فوق السمع)
    "afftdn=nf=-25,"                          # 🔥 إزالة الضوضاء (Denoiser)
    "equalizer=f=3500:t=q:w=2:g=-4,"          # تخفيف حدة السكات
    "equalizer=f=200:t=q:w=1:g=2,"            # إضافة دفء للصوت
    "acompressor=threshold=0.089:ratio=3:attack=20:release=250,"  # توحيد الصوت
    "alimiter=level_in=1:level_out=0.9"       # منع التشوه
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
    print(res_clean.stderr[-500:])
    os.replace(rvc_output, OUTPUT_AUDIO)
else:
    print("✅ Step 2: Audio cleaned successfully.")

# تنظيف الملفات المؤقتة
if os.path.exists(rvc_output) and rvc_output != OUTPUT_AUDIO:
    os.remove(rvc_output)
if os.path.exists(fixed_input):
    os.remove(fixed_input)

print("✅ Done:", OUTPUT_AUDIO)
