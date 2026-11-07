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

from gemini_tts_pool import gemini_tts_with_pool

TARGET_SR = "24000"
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"

ROMANTIC_MUSIC = _REPO_ROOT / "assets" / "audio" / "romantic_music.mp3"
NATURE_SFX = _REPO_ROOT / "assets" / "audio" / "nature_sounds.mp3"

# ═══════════════════════════════════════════════════════════
# 🌟 التشكيل التلقائي (Smart Diacritizer) - كما في الملفات القديمة
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
    
    # 🌟 تطبيق التشكيل التلقائي قبل التوليد
    print(f"📝 تطبيق التشكيل التلقائي...")
    diacritized_text = smart_diacritize(full_text)
    
    # تنظيف النص
    diacritized_text = sanitize_for_tts(diacritized_text)
    
    # تحديد نوع الخلفية
    bg_path = ROMANTIC_MUSIC if ep.get("category") == "romantic" else NATURE_SFX
    
    # توليد الصوت
    voice_mp3 = work / "voice.mp3"
    print(f"🎙️ توليد الصوت لـ: {diacritized_text[:50]}...")
    if not generate_audio(diacritized_text, str(voice_mp3)):
        return False
    
    # المزج
    if bg_path.exists():
        print(f"🎵 مزج الخلفية: {bg_path.name} بنسبة 15%...")
        mix_background(str(voice_mp3), str(bg_path), str(out_mp3))
    else:
        print(f"⚠️ ملف الخلفية غير موجود: {bg_path}، سيتم استخدام الصوت فقط.")
        shutil.copy(voice_mp3, out_mp3)
    
    print(f"✅ تم إنشاء الصوت: {out_mp3}")
    cleanup_dir(work)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    build_whisper_audio(sys.argv[1], sys.argv[2])
