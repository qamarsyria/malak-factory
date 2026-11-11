import os
import subprocess
import requests
from huggingface_hub import hf_hub_download

print("⏳ تحميل النموذج من HuggingFace...")
# سحب النموذج من المستودع اللي رفعناه
model_path = hf_hub_download(
    repo_id="Emad12088/gemini-voice-model",
    filename="G_2333333.pth",
    token=os.environ.get("HF_TOKEN")
)

print("⏳ تحميل مقطع تلاوة السديس...")
subprocess.run(["wget", "-q", "-O", "sadis.mp3", "https://everyayah.com/data/Abdurrahmaan_As-Sudais_192kbps/112001.mp3"], check=True)

print("🚀 جاري التحويل...")
# تشغيل التحويل
cmd = [
    "python", "-m", "infer.cli",
    "--model", model_path,
    "--input", "sadis.mp3",
    "--output", "gemini_voice.wav",
    "--f0-method", "rmvpe",
    "--index-rate", "0.0"
]

env = os.environ.copy()
env["PYTHONPATH"] = "Retrieval-based-Voice-Conversion-WebUI"
subprocess.run(cmd, env=env, check=True, cwd="Retrieval-based-Voice-Conversion-WebUI")

print("🎉 اكتمل التحويل! جاري الإرسال للتلغرام...")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# إرسال الصوت
with open("Retrieval-based-Voice-Conversion-WebUI/gemini_voice.wav", 'rb') as f:
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendAudio", data={"chat_id": CHAT_ID, "caption": "🎧 مبروك! التحويل اشتغل على GitHub Actions!"}, files={'audio': f})

print("🏁 انتهت العملية بالكامل!")
