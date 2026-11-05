import os, sys, json, subprocess, tempfile, shutil, re, time, gzip, hashlib
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir
from gemini_tts_pool import gemini_tts_with_pool

try:
    import edge_tts
    HAS_EDGE = True
except ImportError:
    HAS_EDGE = False

TARGET_SR = "24000"
SILENCE_DUR = 0.15 # 🌟 قللنا الصمت من 0.4 إلى 0.15 لمنع التقطيع
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"

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

def generate_edge_audio(text: str, out_mp3: str, voice="ar-SY-LaithNeural"):
    if not HAS_EDGE: return False
    text = text.replace("ﷺ", "صلى الله عليه وسلم")
    try:
        import asyncio
        comm = edge_tts.Communicate(text, voice, rate="-5%")
        asyncio.run(comm.save(out_mp3))
        return Path(out_mp3).exists() and Path(out_mp3).stat().st_size > 1000
    except: return False

def trim_and_fade(input_wav, output_wav, speed_up=False, is_last=False):
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_wav], capture_output=True, text=True)
        dur = float(probe.stdout.strip() or 0)
        if dur > 0.5:
            # 🌟 نطبق الـ fade فقط لو كان آخر مقطع
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

def sanitize_for_tts(t):
    t = re.sub(r'\[.*?\]|\(.*?\)|﴿.*?﴾', '', t)
    t = re.sub(r'[a-zA-Z0-9*#@_\[\]\(\)]', '', t)
    return re.sub(r"\s+", " ", re.sub(r"[ۖۗۘۙۚۛ۞]", "", t.replace("۝", " "))).strip()

def get_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    try: return round(float(r.stdout.strip()), 3)
    except: return 0.0

def split_text(text, max_chars=500): # 🌟 زودنا من 300 إلى 500
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
        
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = sanitize_for_tts(seg.get("text", ""))
            if not text: continue
            chunks = split_text(text)
            yt_timings, soc_timings = [], []

            for c_idx, chunk in enumerate(chunks):
                if not chunk.strip() or not re.search(r'[\u0600-\u06FF]', chunk): continue
                text_hash = hashlib.md5(chunk.encode('utf-8')).hexdigest()[:8]
                is_last_chunk = (c_idx == len(chunks) - 1)
                
                # يوتيوب (جيميني)
                yt_cache = ep_cache / f"yt_{idx}_{c_idx}_{text_hash}.wav"
                if not yt_cache.exists():
                    tmp = work / f"yt_{idx}_{c_idx}.mp3"
                    gemini_tts_with_pool(chunk, str(tmp))
                    subprocess.run(["ffmpeg", "-y", "-i", str(tmp), "-ar", TARGET_SR, "-ac", "1", str(yt_cache)], check=True, capture_output=True)
                    time.sleep(2)
                if yt_cache.exists():
                    faded = work / f"yt_faded_{idx}_{c_idx}.wav"
                    trim_and_fade(str(yt_cache), str(faded), is_last=is_last_chunk) # 🌟 نمرر هل هو آخر مقطع
                    yt_wavs.extend([faded, silence_path])
                    yt_timings.append({"text": chunk, "duration": get_dur(faded) + SILENCE_DUR})

                # سوشيال (Edge)
                soc_cache = ep_cache / f"soc_{idx}_{c_idx}_{text_hash}.wav"
                if not soc_cache.exists():
                    tmp = work / f"soc_{idx}_{c_idx}.mp3"
                    generate_edge_audio(chunk, str(tmp))
                    if tmp.exists():
                        subprocess.run(["ffmpeg", "-y", "-i", str(tmp), "-ar", TARGET_SR, "-ac", "1", str(soc_cache)], check=True, capture_output=True)
                if soc_cache.exists():
                    faded = work / f"soc_faded_{idx}_{c_idx}.wav"
                    trim_and_fade(str(soc_cache), str(faded), is_last=is_last_chunk)
                    social_wavs.extend([faded, silence_path])
                    soc_timings.append({"text": chunk, "duration": get_dur(faded) + SILENCE_DUR})

            if yt_timings: yt_meta.append({"index": idx, "type": stype, "display": "", "chunks": yt_timings})
            if soc_timings: social_meta.append({"index": idx, "type": stype, "display": "", "chunks": soc_timings})

        elif stype == "quran":
            s, ayahs = seg.get("surah", 0), seg.get("ayah_numbers", [])
            if not ayahs: continue
            display = f"{seg.get('surah_name', '')}: {ayahs[0] if ayahs else ''}"
            
            yt_timings, soc_timings = [], []
            for a in ayahs:
                text_a = get_local_ayah_text(s, a)
                if not text_a: continue
                text_hash = hashlib.md5(text_a.encode('utf-8')).hexdigest()[:8]
                cache_a = ep_cache / f"gemini_{s}_{a}_{text_hash}.wav"
                if not cache_a.exists():
                    tmp = work / "tmp_quran.mp3"
                    gemini_tts_with_pool(text_a, str(tmp))
                    subprocess.run(["ffmpeg", "-y", "-i", str(tmp), "-ar", TARGET_SR, "-ac", "1", str(cache_a)], check=True, capture_output=True)
                    time.sleep(2)
                
                trimmed_a = ep_cache / f"trimmed_{s}_{a}_{text_hash}.wav"
                trim_and_fade(str(cache_a), str(trimmed_a), speed_up=True, is_last=True) # 🌟 تسريع 15%
                
                if trimmed_a.exists():
                    yt_wavs.extend([trimmed_a, silence_path])
                    social_wavs.extend([trimmed_a, silence_path])
                    yt_timings.append({"text": text_a, "duration": get_dur(trimmed_a) + SILENCE_DUR})
                    soc_timings.append({"text": text_a, "duration": get_dur(trimmed_a) + SILENCE_DUR})

            if yt_timings: yt_meta.append({"index": idx, "type": stype, "display": display, "chunks": yt_timings})
            if soc_timings: social_meta.append({"index": idx, "type": stype, "display": display, "chunks": soc_timings})

    if yt_wavs:
        yt_concat = work / "yt_concat.txt"
        yt_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in yt_wavs), encoding="utf-8")
        yt_merged = work / "yt_merged.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(yt_concat), "-c", "copy", str(yt_merged)], check=True, capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(yt_merged), "-c:a", "libmp3lame", "-b:a", "192k", str(out_yt_mp3)], check=True, capture_output=True)
        Path(str(out_yt_mp3).replace(".mp3", "_meta.json")).write_text(json.dumps({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "total_duration": get_dur(out_yt_mp3), "segments": yt_meta}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ (يوتيوب) {out_yt_mp3} | {get_dur(out_yt_mp3)/60:.1f} دقيقة")

    if social_wavs:
        soc_concat = work / "soc_concat.txt"
        soc_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in social_wavs), encoding="utf-8")
        soc_merged = work / "soc_merged.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(soc_concat), "-c", "copy", str(soc_merged)], check=True, capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(soc_merged), "-c:a", "libmp3lame", "-b:a", "192k", str(out_social_mp3)], check=True, capture_output=True)
        Path(str(out_social_mp3).replace(".mp3", "_meta.json")).write_text(json.dumps({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "total_duration": get_dur(out_social_mp3), "segments": social_meta}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ (سوشيال) {out_social_mp3} | {get_dur(out_social_mp3)/60:.1f} دقيقة")

    cleanup_dir(work)

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"❌ الاستخدام: story_audio.py <json> <yt_mp3> <social_mp3>")
        sys.exit(1)
    build_episode_audio(sys.argv[1], sys.argv[2], sys.argv[3])
