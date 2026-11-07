import os
import sys
import json
import subprocess
import tempfile
import shutil
import re
import random
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import PIXABAY_KEY
from gemini_tts_pool import gemini_tts_with_pool
from utilities import cleanup_dir # 🌟 تم إضافة الاستيراد لحل الخطأ

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

# ═══════════════════════════════════════════════════════════
# 🌟 تحميل الأصوات من النت (Pixabay)
# ═══════════════════════════════════════════════════════════
def download_web_audio(query, out_path):
    if not PIXABAY_KEY: return False
    try:
        r = requests.get("https://pixabay.com/api/audio/", params={"key": PIXABAY_KEY, "q": query, "per_page": 3}, timeout=20)
        if r.status_code == 200:
            hits = r.json().get("hits", [])
            if hits:
                url = hits[0].get("audio") or hits[0].get("previewURL")
                if url:
                    with open(out_path, "wb") as f:
                        f.write(requests.get(url, timeout=60).content)
                    return True
    except: pass
    return False

def select_bg_query(category):
    if category == "dua":
        return "islamic nasheed", "دعاء"
    return "romantic music", "موسيقى رومانسية"

def mix_background(voice, bg, out, volume=0.15):
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", voice, "-i", bg,
            "-filter_complex", f"[1:a]volume={volume}[bg];[0:a][bg]amix=inputs=2:duration=first",
            "-c:a", "libmp3lame", "-b:a", "192k", out
        ], check=True, capture_output=True)
        return True
    except:
        shutil.copy(voice, out)
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
    
    category = ep.get("category", "romantic")
    query, bg_name = select_bg_query(category)
    
    voice_mp3 = work / "voice.mp3"
    print(f"🎙️ توليد الصوت لـ: {diacritized_text[:50]}...")
    try:
        gemini_tts_with_pool(diacritized_text, str(voice_mp3))
    except Exception as e:
        print(f"❌ فشل Gemini: {e}")
        return False
    
    bg_mp3 = work / "bg.mp3"
    if download_web_audio(query, str(bg_mp3)):
        print(f"🎵 تحميل خلفية ({bg_name}) من Pixabay...")
        mix_background(str(voice_mp3), str(bg_mp3), str(out_mp3))
    else:
        print(f"⚠️ لم يتم تحميل خلفية ({bg_name})، استخدام الصوت فقط.")
        shutil.copy(voice_mp3, out_mp3)
    
    print(f"✅ تم إنشاء الصوت: {out_mp3}")
    cleanup_dir(work) # 🌟 الآن تعمل بدون خطأ
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    build_whisper_audio(sys.argv[1], sys.argv[2])
