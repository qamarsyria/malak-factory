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

print(f"⏳ Converting {INPUT_AUDIO} to Malak Voice...")

fixed_input = os.path.abspath("fixed_input.wav")
subprocess.run(["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "48000", "-ac", "1", fixed_input], capture_output=True)

env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

# 🔥 إعدادات كاجل الأصلية
cmd = [
    sys.executable, "-m", "infer.cli",
    "--model", MODEL_PATH,
    "--input", fixed_input,
    "--output", OUTPUT_AUDIO,
    "--f0-method", "rmvpe",
    "--pitch", "12",         # 12 عشان نحول من صوت رجل (السديس) لصوت بنت (ملك)
    "--protect", "0.33",
    "--index-rate", "0",     # صفر زي كاجل
    "--index", INDEX_PATH,
    "--overwrite"
]

result = subprocess.run(cmd, cwd=RVC_DIR, env=env)

if result.returncode != 0:
    print("❌ Conversion failed!")
    sys.exit(1)

print("✅ Done:", OUTPUT_AUDIO)
