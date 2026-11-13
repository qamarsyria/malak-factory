import os
import subprocess
import sys

# إعدادات المسارات
RVC_DIR = "Retrieval-based-Voice-Conversion-WebUI"
MODEL_PATH = "malak_voice.pth"
INDEX_PATH = "added_malak_voice.index"
INPUT_AUDIO = "input.mp3"  # الملف اللي بدنا نحوله
OUTPUT_AUDIO = "malak_output.wav"

print("⏳ جاري تشغيل التحويل...")

env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

# أمر التحويل (نفس الإعدادات اللي جربناها في كاجل)
cmd = [
    sys.executable, "-m", "infer.cli",
    "--model", MODEL_PATH,
    "--input", INPUT_AUDIO,
    "--output", OUTPUT_AUDIO,
    "--f0-method", "pm",
    "--pitch", "0", # صفر لأن صوت المدخل (جيميني) بنت، وبنحوله لبنت. (لو رجل بنخليها 8)
    "--protect", "0.33",
    "--index-rate", "0.75",
    "--index", INDEX_PATH,
    "--overwrite"
]

result = subprocess.run(cmd, cwd=RVC_DIR)

if result.returncode != 0:
    print("❌ فشل التحويل")
    sys.exit(1)

print("✅ تم التحويل بنجاح! الملف الجاهز هو:", OUTPUT_AUDIO)
