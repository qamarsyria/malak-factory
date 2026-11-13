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
    print(f"❌ الملف {INPUT_AUDIO} غير موجود!")
    sys.exit(1)

print(f"⏳ جاري تحويل {INPUT_AUDIO} إلى صوت ملك...")

fixed_input = os.path.abspath("fixed_input.wav")
res = subprocess.run(["ffmpeg", "-y", "-i", INPUT_AUDIO, "-ar", "48000", "-ac", "1", fixed_input], capture_output=True, text=True)

if not os.path.exists(fixed_input):
    print(f"❌ فشل FFmpeg:\n{res.stderr}")
    sys.exit(1)

env = os.environ.copy()
env["PYTHONPATH"] = os.path.abspath(RVC_DIR)
env["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

cmd = [
    sys.executable, "-m", "infer.cli",
    "--model", MODEL_PATH,
    "--input", fixed_input,
    "--output", OUTPUT_AUDIO,
    "--f0-method", "pm",     # 🔥 التعديل 1: pm بدل rmvpe (أنعم للـ TTS)
    "--pitch", "0",          # 🔥 التعديل 2: 0 بدل 3 (عشان ما يبرج)
    "--protect", "0.5",      # 🔥 التعديل 3: حماية أعلى للحروف
    "--index-rate", "0.5",   # 🔥 التعديل 4: index أقل عشان صوت طبيعي
    "--index", INDEX_PATH,
    "--overwrite"
]

result = subprocess.run(cmd, cwd=RVC_DIR, env=env)

if result.returncode != 0:
    print("❌ فشل التحويل في RVC")
    sys.exit(1)

print("✅ تم التحويل بنجاح! الملف الجاهز هو:", OUTPUT_AUDIO)
