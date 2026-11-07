import os
import sys
import json
import subprocess
import tempfile
import shutil
import re
import time
import gzip
import hashlib
import asyncio
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import PIXABAY_KEY
from gemini_tts_pool import gemini_tts_with_pool
from utilities import cleanup_dir

TARGET_SR = "24000"
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"
SFX_CACHE = _REPO_ROOT / "assets" / "audio" / "cache"
SFX_CACHE.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════
# 🌟 تحميل الموسيقى/المؤثرات من الويب (Pixabay)
# ═══════════════════════════════════════════════════════════
def download_audio_from_web(query, out_path):
    """يبحث ويحمل ملف صوتي من Pixabay بناءً على كلمة البحث."""
    if not PIXABAY_KEY:
        print("⚠️ PIXABAY_API_KEY غير موجود، سيتم استخدام الصوت بدون خلفية.")
        return False
        
    # البحث في Pixabay (قسم الموسيقى/الصوت)
    url = "https://pixabay.com/api/audio/"
    params = {
        "key": PIXABAY_KEY,
        "q": query,
        "per_page": 5,
    }
    
    try:
        print(f"🔍 البحث عن خلفية صوتية: {query}...")
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            print(f"   ⚠️ فشل البحث: HTTP {r.status_code}")
            return False
            
        hits = r.json().get("hits", [])
        if not hits:
            print(f"   ⚠️ لا توجد نتائج لـ: {query}")
            return False
            
        # اختيار أول ملف
        audio_url = hits[0].get("audio") or hits[0].get("previewURL")
        if not audio_url:
            return False
            
        # تحميل الملف
        print(f"⬇️ تحميل: {os.path.basename(audio_url)}...")
        audio_data = requests.get(audio_url, timeout=60).content
        with open(out_path, "wb") as f:
            f.write(audio_data)
            
        return True
    except Exception as e:
        print(f"   ⚠️ خطأ في تحميل الخلفية: {str(e)[:100]}")
        return False

# ═══════════════════════════════════════════════════════════
# 🌟 التشكيل التلقائي
# ═══════════════════════════════════════════════════════════
def smart_diacritize(text):
    if not text or not text.strip(): return text
    if not re.search(r'[\u0600-\u06FF]', text): return text
    
    prompt = f"""أنت خبير في اللغة العربية والنحو والصرف.
المهمة: أضف التشكيل الكامل (الحركات) للنص التالي بناءً على موقعه في الجملة (الإعراب).
لا تغير أي كلمة، فقط أضف التشكيل الصحيح.
رجع النص المشكول فقط، بدون أي شرح.

النص:
{text}
"""
    try:
        from gemini_tts_pool import get_pool
        pool = get_pool()
        key = pool.get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1000}
        }
        r = requests.post(url, json=body, timeout=60)
        if r.status_code == 200:
            parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            if parts:
                diacritized = parts[0].get("text", "").strip()
                if diacritized and len(diacritized) > 5:
                    return diacritized
    except Exception as e:
        print(f"   ⚠️ التشكيل الذكي فشل: {str(e)[:100]}", flush=True)
    return text

def sanitize_for_tts(t):
    if not t: return ""
    t = t.replace("ﷺ", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    t = t.replace("صلى الله عليه وسلم", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    t = re.sub(r'\[.*?\]', '', t, flags=re.DOTALL)
    t = re.sub(r'\(.*?\)', '', t, flags=re.DOTALL)
    t = re.sub(r'﴿.*?﴾', '', t, flags=re.DOTALL)
    t = re.sub(r'[^\u0600-\u06FF\s،؟۔!.]', '', t)
    t = re.sub(r"[ۖۗۘۙۚۛ۞۝]", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def generate_audio(text, out_path):
    try:
        gemini_tts_with_pool(text, out_path)
        return True
    except Exception as e:
        print(f"   ⚠️ Gemini فشل: {str(e)[:100]}", flush=True)
        return False

def mix_background(voice_mp3, bg_mp3, out_mp3, volume=0.15):
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", voice_mp3, "-i", bg_mp3,
            "-filter_complex", f"[1:a]volume={volume}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0",
            "-c:a", "libmp3lame", "-b:a", "192k", out_mp3
        ], check=True, capture_output=True, timeout=120)
        return True
    except Exception as e:
        print(f"   ⚠️ فشل مزج الخلفية: {e}")
        shutil.copy(voice_mp3, out_mp3)
        return False

def build_whisper_audio(json_path, out_mp3):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="whisper_audio_"))
    
    segments = ep.get("segments", [])
    full_text = " ".join([s["text"] for s in segments if s.get("type") == "narration"])
    
    if not full_text:
        print("❌ لا يوجد نص.")
        return False
    
    print(f"📝 تطبيق التشكيل التلقائي...")
    diacritized_text = smart_diacritize(full_text)
    diacritized_text = sanitize_for_tts(diacritized_text)
    
    category = ep.get("category", "religious")
    
    # 🌟 تحميل الخلفية من الويب
    bg_mp3 = work / "background.mp3"
    if category == "romantic":
        query = "romantic ambient music"
    else:
        query = "nature sounds birds"
        
    has_bg = download_audio_from_web(query, str(bg_mp3))
    
    voice_mp3 = work / "voice.mp3"
    print(f"🎙️ توليد الصوت لـ: {diacritized_text[:50]}...")
    if not generate_audio(diacritized_text, str(voice_mp3)):
        return False
    
    if has_bg:
        print(f"🎵 مزج الخلفية بنسبة 15%...")
        mix_background(str(voice_mp3), str(bg_mp3), str(out_mp3), volume=0.15)
    else:
        print(f"⚠️ سيتم استخدام الصوت فقط.")
        shutil.copy(voice_mp3, out_mp3)
    
    print(f"✅ تم إنشاء الصوت: {out_mp3}")
    cleanup_dir(work)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    build_whisper_audio(sys.argv[1], sys.argv[2])
