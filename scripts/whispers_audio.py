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

from config import FREESOUND_API_KEY
from gemini_tts_pool import gemini_tts_with_pool
from utilities import cleanup_dir

def smart_diacritize(text):
    if not text or not re.search(r'[\u0600-\u06FF]', text): return text
    try:
        from gemini_tts_pool import get_pool
        key = get_pool().get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
        body = {"contents": [{"parts": [{"text": f"أضف التشكيل للنص التالي:\n{text}"}]}], "generationConfig": {"temperature": 0.1}}
        r = requests.post(url, json=body, timeout=60)
        if r.status_code == 200:
            parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            if parts: return parts[0].get("text", "").strip()
    except: pass
    return text

# ═══════════════════════════════════════════════════════════
# 🌟 تحميل الموسيقى من Freesound (لأن Pixabay API الصوت مقفول)
# ═══════════════════════════════════════════════════════════
def download_freesound_audio(query, out_path):
    if not FREESOUND_API_KEY: 
        print("⚠️ FREESOUND_API_KEY غير موجود.")
        return False
    try:
        # البحث في Freesound
        search_url = "https://freesound.org/apiv2/search/text/"
        params = {
            "query": query,
            "token": FREESOUND_API_KEY,
            "fields": "id,name,previews",
            "page_size": 5,
        }
        r = requests.get(search_url, params=params, timeout=20)
        if r.status_code != 200:
            print(f"   ⚠️ Freesound بحث فشل: HTTP {r.status_code}")
            return False
        
        results = r.json().get("results", [])
        if not results:
            print(f"   ⚠️ لا توجد نتائج في Freesound لـ: {query}")
            return False
        
        # اختيار أول نتيجة فيها preview
        for sound in results:
            previews = sound.get("previews", {})
            # نفضل MP3 لو موجود
            preview_url = previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3")
            if preview_url:
                print(f"⬇️ تحميل من Freesound: {sound.get('name')}...")
                audio_data = requests.get(preview_url, timeout=60).content
                with open(out_path, "wb") as f:
                    f.write(audio_data)
                return True
        return False
    except Exception as e:
        print(f"   ⚠️ خطأ Freesound: {str(e)[:100]}")
        return False

def select_bg_query(category):
    if category == "dua":
        return "islamic nasheed", "دعاء"
    # كلمات بحث للموسيقى الرومانسية
    return "romantic ambient music", "موسيقى رومانسية"

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
    if download_freesound_audio(query, str(bg_mp3)):
        print(f"🎵 تحميل خلفية ({bg_name}) من Freesound...")
        mix_background(str(voice_mp3), str(bg_mp3), str(out_mp3))
    else:
        print(f"⚠️ لم يتم تحميل خلفية ({bg_name})، استخدام الصوت فقط.")
        shutil.copy(voice_mp3, out_mp3)
    
    print(f"✅ تم إنشاء الصوت: {out_mp3}")
    cleanup_dir(work)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    build_whisper_audio(sys.argv[1], sys.argv[2])
