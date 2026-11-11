import os
import subprocess
import requests
import urllib.request
import torch
import json
from collections import OrderedDict
from huggingface_hub import hf_hub_download, snapshot_download
import librosa
import soundfile as sf

# ==========================================
# 1. تحميل النموذج
# ==========================================
print("⏳ تحميل النموذج...")
HF_TOKEN = "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"
model_path = hf_hub_download(repo_id="Emad12088/gemini-voice-model", filename="G_2333333.pth", token=HF_TOKEN)

current_dir = os.getcwd()
rvc_dir = os.path.join(current_dir, "Retrieval-based-Voice-Conversion-WebUI")

# ==========================================
# 2. تحميل HuBERT (بدون rmvpe لأننا سنستخدم harvest)
# ==========================================
print("⏳ تحميل HuBERT...")
os.makedirs(os.path.join(rvc_dir, "assets", "hubert_base"), exist_ok=True)
snapshot_download("lj1995/VoiceConversionWebUI", allow_patterns=["hubert_base/*"], local_dir=os.path.join(rvc_dir, "assets"))

# ==========================================
# 3. تحميل الصوت وتحويله إلى WAV
# ==========================================
print("⏳ تحميل الصوت...")
audio_url = "https://everyayah.com/data/Abdurrahmaan_As-Sudais_192kbps/112001.mp3"
mp3_path = os.path.join(current_dir, "sadis.mp3")
wav_path = os.path.join(current_dir, "sadis.wav")
urllib.request.urlretrieve(audio_url, mp3_path)

y, sr = librosa.load(mp3_path, sr=48000, mono=True)
sf.write(wav_path, y, sr)

# ==========================================
# 4. استخراج النموذج الصغير
# ==========================================
print("⏳ استخراج النموذج...")
cpt = torch.load(model_path, map_location="cpu")
model_state_dict = cpt["model"] if "model" in cpt else cpt
extract_model = OrderedDict((k[7:] if k.startswith("module.") else k, v) for k, v in model_state_dict.items())

config_path = os.path.join(rvc_dir, "configs", "v2", "48k.json")
with open(config_path, "r") as f:
    config = json.load(f)

new_cpt = {"weight": extract_model, "config": config, "info": "extracted_model", "sr": "48k", "f0": 1, "version": "v2"}
small_model_path = os.path.join(current_dir, "my_voice_model.pth")
torch.save(new_cpt, small_model_path)

# ==========================================
# 5. تشغيل التحويل باستخدام harvest
# ==========================================
print("🚀 جاري التحويل (harvest على CPU)...")
output_audio = os.path.join(current_dir, "gemini_voice.wav")

cmd = [
    "python", "-m", "infer.cli",
    "--model", small_model_path,
    "--input", wav_path,
    "--output", output_audio,
    "--f0-method", "harvest",  # 🔧 الخوارزمية المستقرة
    "--device", "cpu"          # 🔧 استخدام المعالج
]

try:
    result = subprocess.run(cmd, cwd=rvc_dir, check=True, capture_output=True, text=True)
    print(result.stdout)
    print("✅ اكتمل التحويل!")
except subprocess.CalledProcessError as e:
    print(f"❌ فشل التحويل: {e.returncode}")
    print(e.stdout)
    print(e.stderr)
    raise

# ==========================================
# 6. إرسال الصوت للتلغرام
# ==========================================
print("🎉 جاري الإرسال للتلغرام...")
TOKEN = "8959255443:AAE7OLIMa9ZFyFlcNQrJ2UO9nquaCvktvsc"
CHAT_ID = "-1004433624098"

if os.path.exists(output_audio):
    with open(output_audio, 'rb') as f:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendAudio", data={"chat_id": CHAT_ID, "caption": "🎧 مبروك يا بطل! التحويل اشتغل أخيراً على GitHub Actions!"}, files={'audio': f})
    print("🏁 انتهت العملية بالكامل! تفقد التلغرام.")
else:
    print("❌ لم يتم العثور على ملف الصوت.")
