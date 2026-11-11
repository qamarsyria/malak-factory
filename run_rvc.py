import os
import subprocess
import requests
import urllib.request
from huggingface_hub import hf_hub_download

# ==========================================
# 1. تحميل النموذج ومقطع الصوت
# ==========================================
print("⏳ تحميل النموذج من HuggingFace...")
model_path = hf_hub_download(
    repo_id="Emad12088/gemini-voice-model",
    filename="G_2333333.pth",
    token=os.environ.get("HF_TOKEN")
)

print("⏳ تحميل مقطع تلاوة السديس...")
audio_url = "https://everyayah.com/data/Abdurrahmaan_As-Sudais_192kbps/112001.mp3"
urllib.request.urlretrieve(audio_url, "sadis.mp3")

# ==========================================
# 2. تجهيز المتغيرات للتحويل
# ==========================================
# تحديد المسارات المطلقة لتجنب مشاكل التنقل بين المجلدات
current_dir = os.getcwd()
rvc_dir = os.path.join(current_dir, "Retrieval-based-Voice-Conversion-WebUI")
input_audio = os.path.join(current_dir, "sadis.mp3")
output_audio = os.path.join(current_dir, "gemini_voice.wav")

# تجهيز بيئة التشغيل لـ RVC
env = os.environ.copy()
env["PYTHONPATH"] = rvc_dir

print("🚀 جاري التحويل (قد يستغرق دقيقة)...")
# أمر التحويل
cmd = [
    "python", "-m", "infer.cli",
    "--model", model_path,
    "--input", input_audio,
    "--output", output_audio,
    "--f0-method", "rmvpe",
    "--index-rate", "0.0"
]

# تشغيل الأمر داخل مجلد RVC
subprocess.run(cmd, env=env, cwd=rvc_dir, check=True)

# ==========================================
# 3. إرسال الصوت إلى التلغرام
# ==========================================
print("🎉 اكتمل التحويل! جاري الإرسال للتلغرام...")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if os.path.exists(output_audio):
    with open(output_audio, 'rb') as f:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendAudio", 
            data={
                "chat_id": CHAT_ID, 
                "caption": "🎧 مبروك! التحويل اشتغل على GitHub Actions بنجاح!"
            }, 
            files={'audio': f}
        )
    print("🏁 انتهت العملية بالكامل!")
else:
    print("❌ لم يتم العثور على ملف الصوت الناتج.")
