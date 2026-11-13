import os
import subprocess
import sys

if len(sys.argv) < 3:
    print("❌ Usage: python malak_converter.py <input_audio> <output_audio>")
    sys.exit(1)

INPUT_AUDIO = sys.argv[1]
OUTPUT_AUDIO = sys.argv[2]

RVC_DIR = "Retrieval-based-Voice-Conversion-WebUI"
MODEL_PATH = "malak_voice.pth"
INDEX_PATH = "added_malak_voice.index"

print(f"⏳ جاري تحويل {INPUT_AUDIO} إلى صوت ملك...")

# RVC يحتاج الصوت 48kHz
fixed_input = "fixed_input.wav"
subprocess.run(["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "48000", "-ac", "1", fixed_input], capture_output=True)

env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

cmd = [
    sys.executable, "-m", "infer.cli",
    "--model", MODEL_PATH,
    "--input", fixed_input,
    "--output", OUTPUT_AUDIO,
    "--f0-method", "pm",
    "--pitch", "0", 
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
