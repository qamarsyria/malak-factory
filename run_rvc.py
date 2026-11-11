import os
import subprocess
import requests
import urllib.request
import torch
import json
import shutil
from collections import OrderedDict
from huggingface_hub import hf_hub_download, snapshot_download
import librosa
import soundfile as sf

BASE = "/kaggle/working"
os.chdir(BASE)

# 1. مسح وتحميل RVC
rvc_dir = os.path.join(BASE, "Retrieval-based-Voice-Conversion-WebUI")
if os.path.exists(rvc_dir):
    shutil.rmtree(rvc_dir, ignore_errors=True)

print("⏳ جاري تحميل كود RVC...")
subprocess.run(["git", "clone", "-q", "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git", rvc_dir], check=True)
os.chdir(rvc_dir)
subprocess.run(["git", "checkout", "-q", "81eed5e8f68b6bed1789f682fe78cdd324495afc"], check=True)

# 2. تثبيت التبعيات
print("⏳ تثبيت التبعيات...")
subprocess.run(["pip", "install", "-q", "numpy<2", "scipy", "librosa", "soundfile", "ffmpeg-python", "transformers", "onnxruntime-gpu", "av", "praat-parselmouth", "faiss-cpu", "pyworld", "torchcrepe", "huggingface_hub"], check=True)

# 3. تحميل النماذج
print("⏳ تحميل النماذج...")
HF_TOKEN = "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"
model_path = hf_hub_download(repo_id="Emad12088/gemini-voice-model", filename="G_2333333.pth", token=HF_TOKEN)

os.makedirs("assets/hubert_base", exist_ok=True)
os.makedirs("assets/rmvpe", exist_ok=True)
HF_REPO = "lj1995/VoiceConversionWebUI"
snapshot_download(HF_REPO, allow_patterns=["hubert_base/*"], local_dir="assets")
hf_hub_download(HF_REPO, "rmvpe.pt", local_dir="assets/rmvpe")

# 4. تحميل الصوت
print("⏳ تحميل الصوت...")
audio_url = "https://everyayah.com/data/Abdurrahmaan_As-Sudais_192kbps/112001.mp3"
mp3_path = os.path.join(BASE, "sadis.mp3")
wav_path = os.path.join(BASE, "sadis.wav")
urllib.request.urlretrieve(audio_url, mp3_path)

y, sr = librosa.load(mp3_path, sr=48000, mono=True)
sf.write(wav_path, y, sr)

# 5. استخراج النموذج الصغير
print("⏳ استخراج النموذج الصغير...")
cpt = torch.load(model_path, map_location="cpu")
model_state_dict = cpt["model"] if "model" in cpt else cpt
extract_model = OrderedDict((k[7:] if k.startswith("module.") else k, v) for k, v in model_state_dict.items())

config_path = os.path.join(rvc_dir, "configs", "v2", "48k.json")
with open(config_path, "r") as f:
    config = json.load(f)

new_cpt = {"weight": extract_model, "config": config, "info": "extracted_model", "sr": "48k", "f0": 1, "version": "v2"}
small_model_path = os.path.join(BASE, "my_voice_model.pth")
torch.save(new_cpt, small_model_path)

# 6. تشغيل التحويل (حلول الذاكرة)
print("🚀 جاري التحويل (pm + OMP=1)...")
output_audio = os.path.join(BASE, "gemini_voice.wav")

env = os.environ.copy()
env["PYTHONPATH"] = rvc_dir
env["OMP_NUM_THREADS"] = "1" # 🔧 تقليل استهلاك الذاكرة

cmd = [
    "python", "-m", "infer.cli",
    "--model", small_model_path,
    "--input", wav_path,
    "--output", output_audio,
    "--f0-method", "pm", # 🔧 خوارزمية أخف
    "--index-rate", "0.0",
    "--fp16" # 🔧 دقة أقل = ذاكرة أقل
]

try:
    result = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)
    print(result.stdout)
    print("✅ اكتمل التحويل!")
except subprocess.CalledProcessError as e:
    print(f"❌ فشل: {e.returncode}")
    print(e.stdout)
    print(e.stderr)
    raise

# 7. إرسال للتلغرام
print("🎉 جاري الإرسال للتلغرام...")
TOKEN = "8959255443:AAE7OLIMa9ZFyFlcNQrJ2UO9nquaCvktvsc"
CHAT_ID = "-1004433624098"
if os.path.exists(output_audio):
    with open(output_audio, 'rb') as f:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendAudio", data={"chat_id": CHAT_ID, "caption": "🎧 مبروك! التحويل اشتغل على Kaggle GPU!"}, files={'audio': f})
    print("🏁 انتهت العملية! تفقد التلغرام.")
else:
    print("❌ لم يتم العثور على ملف الصوت الناتج.")
