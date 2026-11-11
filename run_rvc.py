import os
import sys
import subprocess
import requests
import urllib.request
import torch
import json
from collections import OrderedDict
from huggingface_hub import hf_hub_download, snapshot_download

# ==========================================
# 0. تثبيت التبعيات الحرجة داخل السكربت لضمان التوافق
# ==========================================
print("⏳ [0/4] تثبيت التبعيات الحرجة (faiss, numpy, torchcrepe)...")
subprocess.run([sys.executable, "-m", "pip", "install", "-q", 
                "numpy==1.23.5", 
                "faiss-cpu==1.7.3", 
                "torchcrepe"], check=True)

# ==========================================
# 1. تحميل النموذج الخاص بك والنماذج الأساسية
# ==========================================
print("⏳ [1/4] تحميل النموذج الخاص بك من HuggingFace...")
model_path = hf_hub_download(
    repo_id="Emad12088/gemini-voice-model",
    filename="G_2333333.pth",
    token=os.environ.get("HF_TOKEN")
)

current_dir = os.getcwd()
rvc_dir = os.path.join(current_dir, "Retrieval-based-Voice-Conversion-WebUI")

print("⏳ تحميل النماذج الأساسية لـ RVC (HuBERT)...")
os.makedirs(os.path.join(rvc_dir, "assets", "hubert_base"), exist_ok=True)
HF_REPO = "lj1995/VoiceConversionWebUI"
snapshot_download(HF_REPO, allow_patterns=["hubert_base/*"], local_dir=os.path.join(rvc_dir, "assets"))

print("⏳ تحميل مقطع تلاوة السديس...")
audio_url = "https://everyayah.com/data/Abdurrahmaan_As-Sudais_192kbps/112001.mp3"
urllib.request.urlretrieve(audio_url, os.path.join(current_dir, "sadis.mp3"))

# ==========================================
# 2. استخراج النموذج الصغير (Small Model)
# ==========================================
print("⏳ [2/4] جاري استخراج النموذج الصغير...")
cpt = torch.load(model_path, map_location="cpu")
model_state_dict = cpt["model"] if "model" in cpt else cpt

extract_model = OrderedDict()
for k, v in model_state_dict.items():
    extract_model[k[7:] if k.startswith("module.") else k] = v

config_path = os.path.join(rvc_dir, "configs", "v2", "48k.json")
with open(config_path, "r") as f:
    config = json.load(f)

new_cpt = {
    "weight": extract_model,
    "config": config,
    "info": "extracted_model",
    "sr": "48k",
    "f0": 1,
    "version": "v2"
}

small_model_path = os.path.join(current_dir, "my_voice_model.pth")
torch.save(new_cpt, small_model_path)
print("✅ تم إنشاء النموذج الصغير بنجاح!")

# ==========================================
# 3. تشغيل التحويل باستخدام CLI وطريقة pm
# ==========================================
print("🚀 [3/4] جاري التحويل...")

input_audio = os.path.join(current_dir, "sadis.mp3")
output_audio = os.path.join(current_dir, "gemini_voice.wav")

env = os.environ.copy()
env["PYTHONPATH"] = rvc_dir

# استخدام pm خفيفة على المعالج مع التأكد من المسارات المطلقة
cmd = [
    "python", "-m", "infer.cli",
    "--model", small_model_path,
    "--input", input_audio,
    "--output", output_audio,
    "--f0-method", "pm",
    "--index-rate", "0.0"
]

try:
    result = subprocess.run(cmd, env=env, cwd=rvc_dir, check=True, capture_output=True, text=True)
    print(result.stdout)
except subprocess.CalledProcessError as e:
    print(f"❌ فشل التحويل. رمز الخطأ: {e.returncode}")
    print("STDOUT:", e.stdout)
    print("STDERR:", e.stderr)
    raise

# ==========================================
# 4. إرسال الصوت إلى التلغرام
# ==========================================
print("🎉 [4/4] اكتمل التحويل! جاري الإرسال للتلغرام...")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if os.path.exists(output_audio):
    with open(output_audio, 'rb') as f:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendAudio",
            data={"chat_id": CHAT_ID, "caption": "🎧 مبروك! التحويل اشتغل على GitHub Actions بنجاح!"},
            files={'audio': f}
        )
    print("🏁 انتهت العملية بالكامل!")
else:
    print("❌ لم يتم العثور على ملف الصوت الناتج.")
