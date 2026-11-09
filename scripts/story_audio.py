import os, sys, json, subprocess, tempfile, shutil, re, time, gzip, hashlib, argparse
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir
from gemini_tts_pool import gemini_tts_with_pool

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
    if not Path(input_wav).exists(): return False
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_wav], capture_output=True, text=True)
        dur = float(probe.stdout.strip() or 0)
        if dur > 0.5:
            fade_filter = f",afade=t=out:st={max(0, dur - 0.2)}:d=0.2" if is_last else ""
            tempo_filter = "atempo=1.15," if speed_up else ""
            subprocess.run(["ffmpeg", "-y", "-i", input_wav, "-af", f"{tempo_filter}aresample=async=1:first_pts=0{fade_filter}", "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
            return True
        else:
            shutil.copy(input_wav, output_wav); return True
    except:
        try: shutil.copy(input_wav, output_wav)
        except: pass
        return False

def apply_mosque_reverb(input_wav, output_wav):
    try:
        # رفع الصوت 20% لتوازن التلاوة مع السرد
        filters = ("volume=1.20,atempo=0.92,equalizer=f=120:t=q:w=1.0:g=4,equalizer=f=4000:t=q:w=1.0:g=-4,aecho=0.8:0.7:60|180|320:0.15|0.10|0.05,acompressor=threshold=0.1:ratio=3:attack=20:release=200")
        subprocess.run(["ffmpeg", "-y", "-i", input_wav, "-af", filters, "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
        return True
    except:
        shutil.copy(input_wav, output_wav); return False

def apply_stealth_filter(input_wav, output_wav):
    try:
        # 🌟 رفع صوت التلاوة البشرية 35%
        filters = ("volume=1.35,atempo=1.05,equalizer=f=200:t=q:w=1.0:g=2,aecho=0.8:0.9:20:0.1,highpass=f=100,lowpass=f=12000")
        subprocess.run(["ffmpeg", "-y", "-i", input_wav, "-af", filters, "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
        return True
    except:
        shutil.copy(input_wav, output_wav); return False

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

def sanitize_for_tts(t):
    if not t: return ""
    t = t.replace("ﷺ", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    t = re.sub(r'\[.*?\]', '', t, flags=re.DOTALL)
    t = re.sub(r'\(.*?\)', '', t, flags=re.DOTALL)
    t = re.sub(r'﴿.*?﴾', '', t, flags=re.DOTALL)
    t = re.sub(r'[\u06D6-\u06ED]', ' ', t) # مسح رموز التجويد
    t = re.sub(r'[^\u0621-\u064A\u064B-\u0652\u0670\s،؟۔!.]', '', t)
    return re.sub(r"\s+", " ", t).strip()

def get_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    try: return round(float(r.stdout.strip()), 3)
    except: return 0.0

def split_text(text, max_chars=150):
    sentences = re.split(r'(?<=[.!؟])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_chars: current += " " + s
        else:
            if current: chunks.append(current.strip())
            current = s
    if current: chunks.append(current.strip())
    return chunks

def build_mode(mode, json_path, out_mp3, out_meta):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix=f"aud_{mode}_"))
    ep_name = Path(json_path).stem
    ep_cache = CACHE_DIR / ep_name
    ep_cache.mkdir(parents=True, exist_ok=True)

    silence_path = work / "silence.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", str(SILENCE_DUR), "-c:a", "pcm_s16le", str(silence_path)], check=True, capture_output=True)

    yt_wavs = []
    yt_meta = []

    for idx, seg in enumerate(ep.get("segments", [])):
        stype = seg.get("type")
        
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = sanitize_for_tts(seg.get("text", ""))
            if not text: continue
            
            chunks = split_text(text)
            chunk_timings = []
            
            for c_idx, chunk in enumerate(chunks):
                if not chunk.strip(): continue
                text_hash = hashlib.md5(chunk.encode('utf-8')).hexdigest()[:8]
                is_last_chunk = (c_idx == len(chunks) - 1)
                cached_wav = ep_cache / f"seg_{idx}_{c_idx}_{text_hash}.wav"
                
                if not cached_wav.exists() or cached_wav.stat().st_size < 1000:
                    temp_mp3 = work / f"t_{idx}_{c_idx}.mp3"
                    if gemini_tts_with_pool(chunk, str(temp_mp3)):
                        subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(cached_wav)], check=True, capture_output=True)
                    time.sleep(1)
                    
                if cached_wav.exists():
                    faded = work / f"f_{idx}_{c_idx}.wav"
                    if trim_and_fade(str(cached_wav), str(faded), speed_up=False, is_last=is_last_chunk):
                        yt_wavs.extend([faded, silence_path])
                        chunk_timings.append({"text": chunk, "duration": get_dur(faded) + SILENCE_DUR})
            
            if chunk_timings:
                yt_meta.append({"index": idx, "type": stype, "display": "", "chunks": chunk_timings})

        elif stype == "quran":
            s, ayahs = seg.get("surah", 0), seg.get("ayah_numbers", [])
            if not ayahs: continue
            display = f"{seg.get('surah_name', '')}: {ayahs[0] if ayahs else ''}"
            
            chunk_timings = []
            for a in ayahs:
                text_a = sanitize_for_tts(get_local_ayah_text(s, a))
                if not text_a: continue
                
                text_hash = hashlib.md5(text_a.encode('utf-8')).hexdigest()[:8]
                
                if mode == "gemini":
                    # 🌟 مسار جيميني
                    cache_a = ep_cache / f"qgem_{s}_{a}_{text_hash}.wav"
                    if not cache_a.exists():
                        tmp = work / "tmp.mp3"
                        if gemini_tts_with_pool(text_a, str(tmp)):
                            subprocess.run(["ffmpeg", "-y", "-i", str(tmp), "-ar", TARGET_SR, "-ac", "1", str(cache_a)], check=True, capture_output=True)
                        time.sleep(1)
                    
                    if cache_a.exists():
                        trimmed = ep_cache / f"trim_gem_{s}_{a}_{text_hash}.wav"
                        if trim_and_fade(str(cache_a), str(trimmed), speed_up=True, is_last=True):
                            final = ep_cache / f"final_gem_{s}_{a}_{text_hash}.wav"
                            apply_mosque_reverb(str(trimmed), str(final))
                            if final.exists():
                                yt_wavs.extend([final, silence_path])
                                chunk_timings.append({"text": text_a, "duration": get_dur(final) + SILENCE_DUR})
                                
                else:
                    # 🌟 مسار بشري
                    cache_a = ep_cache / f"human_{s}_{a}.mp3"
                    if download_human_ayah(s, a, str(cache_a)):
                        trimmed = ep_cache / f"trim_hum_{s}_{a}.wav"
                        if trim_and_fade(str(cache_a), str(trimmed), speed_up=True, is_last=True):
                            stealth = ep_cache / f"stealth_{s}_{a}.wav"
                            reverb = ep_cache / f"reverb_{s}_{a}.wav"
                            apply_stealth_filter(str(trimmed), str(stealth))
                            apply_mosque_reverb(str(stealth), str(reverb))
                            if reverb.exists():
                                yt_wavs.extend([reverb, silence_path])
                                chunk_timings.append({"text": text_a, "duration": get_dur(reverb) + SILENCE_DUR})
            
            if chunk_timings:
                yt_meta.append({"index": idx, "type": stype, "display": display, "chunks": chunk_timings})

    if yt_wavs:
        concat = work / "concat.txt"
        concat.write_text("\n".join(f"file '{p.resolve()}'" for p in yt_wavs), encoding="utf-8")
        merged = work / "merged.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(merged)], check=True, capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(merged), "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)], check=True, capture_output=True)
        
        meta_data = {
            "episode": ep.get("episode_number", 1),
            "title": ep.get("title", ""),
            "total_duration": get_dur(out_mp3),
            "segments": yt_meta
        }
        Path(out_meta).write_text(json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ ({mode}) {out_mp3} | {get_dur(out_mp3)/60:.1f} دقيقة")
    
    cleanup_dir(work)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["human", "gemini"])
    parser.add_argument("--json", required=True)
    parser.add_argument("--out_audio", required=True)
    parser.add_argument("--out_meta", required=True)
    args = parser.parse_args()
    build_mode(args.mode, args.json, args.out_audio, args.out_meta)
