import os, sys, json, subprocess, tempfile, shutil, re, time, gzip, hashlib, asyncio
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir

# ═══════════════════════════════════════════════════════════
# 🌟 مفتاح التبديل: 1 = Gemini | 2 = Edge TTS
# ═══════════════════════════════════════════════════════════
TTS_ENGINE = os.environ.get("TTS_ENGINE", "1").strip()

if TTS_ENGINE == "1":
    print("🔊 [TTS] المحرك: Gemini (الجودة العالية)", flush=True)
    from gemini_tts_pool import gemini_tts_with_pool as _engine_generate
    def generate_audio(text, out_path):
        try:
            _engine_generate(text, out_path)
            return True
        except Exception as e:
            print(f"   ⚠️ Gemini فشل: {str(e)[:100]}", flush=True)
            return False
else:
    print("🔊 [TTS] المحرك: Edge TTS (البديل السريع)", flush=True)
    import edge_tts
    def generate_audio(text, out_path):
        try:
            async def _run():
                comm = edge_tts.Communicate(text, "ar-SY-LaithNeural", rate="-5%")
                await comm.save(out_path)
            asyncio.run(_run())
            return Path(out_path).exists() and Path(out_path).stat().st_size > 1000
        except Exception as e:
            print(f"   ⚠️ Edge فشل: {str(e)[:100]}", flush=True)
            return False

TARGET_SR = "24000"
SILENCE_DUR = 0.15 
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"
RECITER = "Alafasy_128kbps" 
QURAN_BASE_URL = "https://everyayah.com/data"

_ayah_lib = {}
try:
    lib_path = _REPO_ROOT / "data" / "libraries" / "ayah-library.json.gz"
    if lib_path.exists():
        with gzip.open(lib_path, "rt", encoding="utf-8") as f:
            data = json.load(f)
            for item in data:
                key = f"{item['surah_number']}:{item['ayah_number']}"
                _ayah_lib[key] = item['text']
        print(f"✅ تم تحميل {len(_ayah_lib)} آية.")
except Exception as e:
    print(f"⚠️ فشل تحميل الآيات: {e}")

def get_local_ayah_text(surah, ayah): return _ayah_lib.get(f"{surah}:{ayah}", "")

def trim_and_fade(input_wav, output_wav, speed_up=False, is_last=False):
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_wav], capture_output=True, text=True)
        dur = float(probe.stdout.strip() or 0)
        if dur > 0.5:
            fade_filter = f",afade=t=out:st={max(0, dur - 0.2)}:d=0.2" if is_last else ""
            tempo_filter = "atempo=1.15," if speed_up else ""
            subprocess.run([
                "ffmpeg", "-y", "-i", input_wav, 
                "-af", f"{tempo_filter}aresample=async=1:first_pts=0{fade_filter}", 
                "-ar", TARGET_SR, "-ac", "1", output_wav
            ], check=True, capture_output=True, timeout=60)
            return True
        else:
            shutil.copy(input_wav, output_wav)
            return True
    except: 
        shutil.copy(input_wav, output_wav)
        return False

def apply_mosque_reverb(input_wav, output_wav):
    try:
        filters = ("atempo=0.92,equalizer=f=120:t=q:w=1.0:g=4,equalizer=f=4000:t=q:w=1.0:g=-4,aecho=0.8:0.7:60|180|320:0.15|0.10|0.05,acompressor=threshold=0.1:ratio=3:attack=20:release=200")
        subprocess.run(["ffmpeg", "-y", "-i", input_wav, "-af", filters, "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
        return True
    except: 
        shutil.copy(input_wav, output_wav)
        return False

def apply_stealth_filter(input_wav, output_wav):
    try:
        filters = ("atempo=1.05,equalizer=f=200:t=q:w=1.0:g=2,aecho=0.8:0.9:20:0.1,highpass=f=100,lowpass=f=12000")
        subprocess.run(["ffmpeg", "-y", "-i", input_wav, "-af", filters, "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
        return True
    except: 
        shutil.copy(input_wav, output_wav)
        return False

def download_human_ayah(surah, ayah, out_path):
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE_URL}/{RECITER}/{filename}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            with open(out_path, "wb") as f: f.write(r.content)
            return True
    except: pass
    return False

# ═══════════════════════════════════════════════════════════
# 🌟 المشكّل الذكي
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

def get_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    try: return round(float(r.stdout.strip()), 3)
    except: return 0.0

def split_text(text, max_chars=500): 
    sentences = re.split(r'(?<=[.!؟])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_chars: current += " " + s
        else:
            if current: chunks.append(current.strip())
            current = s
    if current: chunks.append(current.strip())
    return chunks

def build_episode_audio(json_path, out_yt_mp3, out_social_mp3):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    ep_name = Path(json_path).stem
    ep_cache = CACHE_DIR / ep_name
    ep_cache.mkdir(parents=True, exist_ok=True)

    silence_path = work / "silence.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", str(SILENCE_DUR), "-c:a", "pcm_s16le", str(silence_path)], check=True, capture_output=True)

    segments_list = ep.get("segments", [])
    yt_wavs, social_wavs = [], []
    yt_meta, social_meta = [], []

    for idx, seg in enumerate(segments_list):
        stype = seg.get("type")
        
        # ═══ 1. السرد ═══
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = sanitize_for_tts(seg.get("text", ""))
            if not text: 
                continue
            
            # 🌟 التشكيل الذكي (مرة واحدة فقط)
            text = smart_diacritize(text)
            
            chunks = split_text(text)
            chunk_timings = []
            
            for c_idx, chunk in enumerate(chunks):
                if not chunk.strip() or not re.search(r'[\u0600-\u06FF]', chunk): continue
                text_hash = hashlib.md5(chunk.encode('utf-8')).hexdigest()[:8]
                is_last_chunk = (c_idx == len(chunks) - 1)
                cached_wav = ep_cache / f"seg_{idx}_{c_idx}_{text_hash}.wav"
                
                if not cached_wav.exists() or cached_wav.stat().st_size < 1000:
                    temp_mp3 = work / f"temp_{idx}_{c_idx}.mp3"
                    if generate_audio(chunk, str(temp_mp3)):
                        subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(cached_wav)], check=True, capture_output=True)
                    time.sleep(1) 
                    
                if cached_wav.exists():
                    faded = work / f"faded_{idx}_{c_idx}.wav"
                    trim_and_fade(str(cached_wav), str(faded), speed_up=False, is_last=is_last_chunk)
                    yt_wavs.extend([faded, silence_path])
                    social_wavs.extend([faded, silence_path])
                    chunk_timings.append({"text": chunk, "duration": get_dur(faded) + SILENCE_DUR})
            
            if chunk_timings:
                meta_entry = {"index": idx, "type": stype, "display": "", "chunks": chunk_timings}
                yt_meta.append(meta_entry)
                social_meta.append(meta_entry)

        # ═══ 2. الآيات ═══
        elif stype == "quran":
            s, ayahs = seg.get("surah", 0), seg.get("ayah_numbers", [])
            if not ayahs: continue
            display = f"{seg.get('surah_name', '')}: {ayahs[0] if ayahs else ''}"
            
            yt_chunk_timings = []
            for a in ayahs:
                text_a = sanitize_for_tts(get_local_ayah_text(s, a))
                if not text_a: continue
                
                text_hash = hashlib.md5(text_a.encode('utf-8')).hexdigest()[:8]
                cache_a = ep_cache / f"engine_{s}_{a}_{text_hash}.wav"
                    
                if not cache_a.exists():
                    tmp_mp3 = work / "tmp_yt.mp3"
                    if generate_audio(text_a, str(tmp_mp3)):
                        subprocess.run(["ffmpeg", "-y", "-i", str(tmp_mp3), "-ar", TARGET_SR, "-ac", "1", str(cache_a)], check=True, capture_output=True)
                    time.sleep(1)
                
                trimmed_a = ep_cache / f"trimmed_yt_{s}_{a}_{text_hash}.wav"
                trim_and_fade(str(cache_a), str(trimmed_a), speed_up=True, is_last=True) 
                
                final_a = ep_cache / f"final_yt_{s}_{a}_{text_hash}.wav"
                if not final_a.exists():
                    apply_mosque_reverb(str(trimmed_a), str(final_a))
                
                if final_a.exists():
                    yt_wavs.extend([final_a, silence_path])
                    yt_chunk_timings.append({"text": text_a, "duration": get_dur(final_a) + SILENCE_DUR})
            
            if yt_chunk_timings:
                yt_meta.append({"index": idx, "type": stype, "display": display, "chunks": yt_chunk_timings})

            soc_chunk_timings = []
            for a in ayahs:
                text_a = sanitize_for_tts(get_local_ayah_text(s, a))
                if not text_a: continue
                
                cache_a = ep_cache / f"human_{s}_{a}.mp3"
                if not download_human_ayah(s, a, str(cache_a)): continue
                
                trimmed_h = ep_cache / f"trimmed_human_{s}_{a}.wav"
                trim_and_fade(str(cache_a), str(trimmed_h), speed_up=True, is_last=True)

                stealth_a = ep_cache / f"stealth_{s}_{a}.wav"
                reverb_a = ep_cache / f"reverb_{s}_{a}.wav"
                if not reverb_a.exists():
                    apply_stealth_filter(str(trimmed_h), str(stealth_a))
                    apply_mosque_reverb(str(stealth_a), str(reverb_a))
                
                if reverb_a.exists():
                    social_wavs.extend([reverb_a, silence_path])
                    soc_chunk_timings.append({"text": text_a, "duration": get_dur(reverb_a) + SILENCE_DUR})
            
            if soc_chunk_timings:
                social_meta.append({"index": idx, "type": stype, "display": display, "chunks": soc_chunk_timings})

    if yt_wavs:
        yt_concat = work / "yt_concat.txt"
        yt_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in yt_wavs), encoding="utf-8")
        yt_merged = work / "yt_merged.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(yt_concat), "-c", "copy", str(yt_merged)], check=True, capture_output=True)
        Path(out_yt_mp3).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(yt_merged), "-c:a", "libmp3lame", "-b:a", "192k", str(out_yt_mp3)], check=True, capture_output=True)
        Path(str(out_yt_mp3).replace(".mp3", "_meta.json")).write_text(json.dumps({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "total_duration": get_dur(out_yt_mp3), "segments": yt_meta}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ (يوتيوب) {out_yt_mp3} | {get_dur(out_yt_mp3)/60:.1f} دقيقة")

    if social_wavs:
        soc_concat = work / "soc_concat.txt"
        soc_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in social_wavs), encoding="utf-8")
        soc_merged = work / "soc_merged.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(soc_concat), "-c", "copy", str(soc_merged)], check=True, capture_output=True)
        Path(out_social_mp3).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(soc_merged), "-c:a", "libmp3lame", "-b:a", "192k", str(out_social_mp3)], check=True, capture_output=True)
        Path(str(out_social_mp3).replace(".mp3", "_meta.json")).write_text(json.dumps({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "total_duration": get_dur(out_social_mp3), "segments": social_meta}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ (سوشيال) {out_social_mp3} | {get_dur(out_social_mp3)/60:.1f} دقيقة")

    cleanup_dir(work)

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"❌ الاستخدام: story_audio.py <json> <yt_mp3> <social_mp3>")
        sys.exit(1)
    build_episode_audio(sys.argv[1], sys.argv[2], sys.argv[3])
