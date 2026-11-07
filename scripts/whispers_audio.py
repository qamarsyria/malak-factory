import os, sys, json, subprocess, tempfile, shutil, re, random
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

def download_freesound(query, out_path):
    """يحمّل أول نتيجة من Freesound."""
    if not FREESOUND_API_KEY: return False
    try:
        r = requests.get("https://freesound.org/apiv2/search/text/", 
                        params={"query": query, "token": FREESOUND_API_KEY, "fields": "id,name,previews", "page_size": 10}, 
                        timeout=20)
        if r.status_code != 200: return False
        results = r.json().get("results", [])
        if not results: return False
        
        # نختار عشوائياً من أول 5 نتائج
        random.shuffle(results[:5])
        for sound in results[:5]:
            previews = sound.get("previews", {})
            url = previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3")
            if url:
                print(f"⬇️ تحميل ({query}): {sound.get('name')[:40]}...")
                with open(out_path, "wb") as f:
                    f.write(requests.get(url, timeout=60).content)
                return True
    except: pass
    return False

def get_romantic_music(out_path):
    """يحاول عدة كلمات بحث لحد ما يلاقي موسيقى رومانسية."""
    queries = [
        "romantic piano emotional", 
        "romantic guitar love",
        "love theme instrumental",
        "emotional cinematic piano",
        "romantic ambient music"
    ]
    for q in queries:
        if download_freesound(q, out_path):
            return True
    return False

def get_dua_music(out_path):
    """للدعاء: موسيقى إسلامية هادئة."""
    queries = ["islamic ambient", "quran recitation soft", "oud peaceful", "islamic nasheed instrumental"]
    for q in queries:
        if download_freesound(q, out_path):
            return True
    return False

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
    
    text = " ".join([s["text"] for s in ep.get("segments", []) if s.get("type") == "narration"])
    if not text: return False
    
    print(f"📝 التشكيل...")
    text = smart_diacritize(text)
    
    voice_mp3 = work / "voice.mp3"
    print(f"🎙️ توليد الصوت...")
    try:
        gemini_tts_with_pool(text, str(voice_mp3))
    except Exception as e:
        print(f"❌ فشل: {e}")
        return False
    
    bg_mp3 = work / "bg.mp3"
    category = ep.get("category", "romantic")
    
    if category == "dua":
        has_bg = get_dua_music(str(bg_mp3))
    else:
        has_bg = get_romantic_music(str(bg_mp3))
    
    if has_bg:
        print(f"🎵 مزج الموسيقى بنسبة 15%...")
        mix_background(str(voice_mp3), str(bg_mp3), str(out_mp3))
    else:
        print(f"⚠️ لم نجد موسيقى، استخدام الصوت فقط.")
        shutil.copy(voice_mp3, out_mp3)
    
    print(f"✅ تم: {out_mp3}")
    cleanup_dir(work)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    build_whisper_audio(sys.argv[1], sys.argv[2])
