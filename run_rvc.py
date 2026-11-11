import os
import sys
import logging
import traceback
import requests
import urllib.request
import torch
import json
from collections import OrderedDict
from huggingface_hub import hf_hub_download, snapshot_download
import soundfile as sf

# إعداد السجلات عشان نشوف الأخطاء بالتفصيل لو صارت
logging.basicConfig(level=logging.INFO)

# 1. تحميل الموديل الصوتي الخاص بالمستخدم
print("⏳ تحميل النموذج الخاص بك من HuggingFace...")
model_path = hf_hub_download(
    repo_id="Emad12088/gemini-voice-model",
    filename="G_2333333.pth",
    token=os.environ.get("HF_TOKEN")
)

current_dir = os.getcwd()
rvc_dir = os.path.join(current_dir, "Retrieval-based-Voice-Conversion-WebUI")

# 2. تحميل النماذج الأساسية لـ RVC (HuBERT)
print("⏳ تحميل النماذج الأساسية لـ RVC...")
os.makedirs(os.path.join(rvc_dir, "assets", "hubert_base"), exist_ok=True)
HF_REPO = "lj1995/VoiceConversionWebUI"
snapshot_download(HF_REPO, allow_patterns=["hubert_base/*"], local_dir=os.path.join(rvc_dir, "assets"))

print("⏳ تحميل مقطع تلاوة السديس...")
audio_url = "https://everyayah.com/data/Abdurrahmaan_As-Sudais_192kbps/112001.mp3"
urllib.request.urlretrieve(audio_url, os.path.join(current_dir, "sadis.mp3"))

# 3. استخراج النموذج الصغير (Small Model)
print("⏳ جاري استخراج النموذج الصغير...")
cpt = torch.load(model_path, map_location="cpu")
model_state_dict = cpt["model"] if "model" in cpt else cpt

extract_model = OrderedDict()
for k, v in model_state_dict.items():
    extract_model[k[7:] if k.startswith("module.") else k] = v

# قراءة ملف الإعدادات الافتراضي
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

# 4. تشغيل التحويل باستخدام API مباشر (لتجنب أخطاء الـ CLI)
print("🚀 جاري التحويل (API مباشر)...")

input_audio = os.path.join(current_dir, "sadis.mp3")
output_audio = os.path.join(current_dir, "gemini_voice.wav")
model_full_path = os.path.join(current_dir, small_model_path)

sys.path.append(rvc_dir)
os.chdir(rvc_dir)

try:
    from infer.modules.vc.modules import VC
    from configs.config import Config
    
    config = Config()
    vc = VC(config)
    
    print("Loading model...")
    vc.get_vc(model_full_path)
    
    print("Running inference with PM...")
    # استخدام pm بدل rmvpe لتجنب الانهيار على المعالج
    audio = vc.vc_single(
        sid=0,
        input_audio_path=input_audio,
        f0_up_key=0,
        f0_file=None,
        f0_method="pm", # 🔧 التغيير الجوهري هنا
        file_index=None,
        index_rate=0.0,
        filter_radius=3,
        resample_sr=0,
        rms_mix_rate=1.0,
        protect=0.33
    )
    
    if isinstance(audio, int) and audio == -1:
        raise Exception("فشل التحويل داخل RVC (رجع -1)")
    
    if isinstance(audio, tuple):
        audio = audio[0]
        
    sf.write(output_audio, audio, 48000)
    print("✅ تم حفظ الملف بنجاح!")
    
except Exception as e:
    print(f"❌ خطأ: {e}")
    traceback.print_exc()
    raise

# 5. إرسال الصوت
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
