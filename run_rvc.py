import os
import sys
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
# 1. تحميل النموذج والنماذج الأساسية
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

# ==========================================
# 2. تحميل الصوت وتحويله إلى WAV باستخدام librosa (بدون ffmpeg)
# ==========================================
print("⏳ [2/4] تحميل الصوت وتحويله إلى WAV...")
audio_url = "https://everyayah.com/data/Abdurrahmaan_As-Sudais_192kbps/112001.mp3"
mp3_path = os.path.join(current_dir, "sadis.mp3")
wav_path = os.path.join(current_dir, "sadis.wav")

urllib.request.urlretrieve(audio_url, mp3_path)

# 🔧 استخدام librosa بدلاً من ffmpeg
try:
    y, sr = librosa.load(mp3_path, sr=48000, mono=True)
    sf.write(wav_path, y, sr)
    print("✅ تم تحويل الصوت إلى WAV بنجاح (بدون ffmpeg)!")
except Exception as e:
    print("❌ فشل تحويل الصوت:", e)
    raise

# ==========================================
# 3. استخراج النموذج الصغير
# ==========================================
print("⏳ [3/4] جاري استخراج النموذج الصغير...")
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
# 4. تشغيل التحويل
# ==========================================
print("🚀 [4/4] جاري التحويل...")
output_audio = os.path.join(current_dir, "gemini_voice.wav")

env = os.environ.copy()
env["PYTHONPATH"] = rvc_dir

cmd = [
    "python", "-m", "infer.cli",
    "--model", small_model_path,
    "--input", wav_path,
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
# 5. إرسال الصوت للتلغرام
# ==========================================
print("🎉 اكتمل التحويل! جاري الإرسال للتلغرام...")
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
