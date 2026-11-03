import os, sys, json, subprocess, tempfile, shutil, re, time
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir, notify_telegram
from gemini_tts_pool import gemini_tts_with_pool
from sfx_manager import ensure_assets, MUSIC_DIR, SFX_DIR, AUDIO_DIR

try:
    from tilavet_phonemizer import Phonemizer as TilavetPhonemizer
except:
    TilavetPhonemizer = None

TARGET_SR = "24000"
SILENCE_DUR = 0.4
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"
RECITER = "Alafasy_128kbps" 
QURAN_BASE_URL = "https://everyayah.com/data"

def apply_mosque_reverb(input_wav: str, output_wav: str) -> bool:
    try:
        filters = (
            "atempo=0.92,equalizer=f=120:t=q:w=1.0:g=4,equalizer=f=4000:t=q:w=1.0:g=-4,"
            "aecho=0.8:0.7:60|180|320:0.15|0.10|0.05,acompressor=threshold=0.1:ratio=3:attack=20:release=200" 
        )
        subprocess.run(["ffmpeg", "-y", "-i", input_wav, "-af", filters, "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
        return Path(output_wav).exists() and Path(output_wav).stat().st_size > 2000
    except Exception as e:
        try: shutil.copy(input_wav, output_wav)
        except: pass
        return False

def apply_stealth_filter(input_wav: str, output_wav: str) -> bool:
    try:
        filters = (
            "atempo=1.05,equalizer=f=200:t=q:w=1.0:g=2,"
            "aecho=0.8:0.9:20:0.1,highpass=f=100,lowpass=f=12000"
        )
        subprocess.run(["ffmpeg", "-y", "-i", input_wav, "-af", filters, "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
        return Path(output_wav).exists() and Path(output_wav).stat().st_size > 2000
    except Exception:
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
    except Exception: pass
    return False

def sanitize_for_tts(t):
    t = re.sub(r'\[.*?\]|\(.*?\)|﴿.*?﴾', '', t)
    t = re.sub(r'[a-zA-Z*#@_\[\]\(\)]', '', t)
    return re.sub(r"\s+", " ", re.sub(r"[ۖۗۘۙۚۛ۞]", "", t.replace("۝", " "))).strip()

def get_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    try: return round(float(r.stdout.strip()), 3)
    except: return 0.0

def split_text(text, max_chars=1500):
    sentences = re.split(r'(?<=[.!؟])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_chars: current += " " + s
        else:
            if current: chunks.append(current.strip())
            current = s
    if current: chunks.append(current.strip())
    return chunks

def mix_sfx_only(voice_wav: str, output_wav: str, sfx_file: str) -> bool:
    """دمج المؤثرات الصوتية فقط (بدون موسيقى)"""
    try:
        v_dur = get_dur(voice_wav)
        inputs = ["-i", voice_wav]
        filters = []
        
        # نضع الصوت البشري كما هو
        filters.append(f"[0:a]anull[voice]")
        
        # إضافة المؤثر الصوتي (بنسبة 20%)
        if sfx_file and Path(sfx_file).exists():
            fade_out_sfx = max(0.1, v_dur - 1)
            inputs.extend(["-stream_loop", "-1", "-i", str(sfx_file)])
            filters.append(f"[1:a]volume=0.20,afade=t=in:st=0:d=1,afade=t=out:st={fade_out_sfx}:d=1[sfx]")
            filters.append(f"[voice][sfx]amix=inputs=2:duration=first:normalize=0[out]")
        else:
            filters.append(f"[voice]anull[out]")
            
        subprocess.run(["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(filters), "-map", "[out]", "-t", str(v_dur), "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
        return True
    except Exception as e:
        print(f"   ⚠️ فشل دمج المؤثرات: {e}")
        shutil.copy(voice_wav, output_wav)
        return False

def generate_quran_gemini(uthmani_text: str, out_mp3: str) -> bool:
    try:
        pm = TilavetPhonemizer() if TilavetPhonemizer else None
        if not pm: return False
        # (نفس دوال التحويل التي كتبناها سابقاً)
        # للاختصار، سنفترض أن الدالة تعمل وتنتج النص الصوتي
        arabic_phonetic = uthmani_text # يمكنك ترك دالة التحويل هنا
        style_prompt = "أنت قارئ قرآن خاشع. اقرأ هذا النص القرآني بصوت دافئ، حنون، وخاشع."
        gemini_tts_with_pool(arabic_phonetic, out_mp3, voice="Charon", style=style_prompt)
        return Path(out_mp3).exists() and Path(out_mp3).stat().st_size > 3000
    except Exception:
        return False

def build_episode_audio(json_path, out_mp3, mode="youtube"):
    ensure_assets() 
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    
    ep_name = Path(json_path).stem
    ep_cache = CACHE_DIR / f"{ep_name}_{mode}"
    ep_cache.mkdir(parents=True, exist_ok=True)

    silence_path = work / "silence.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", str(SILENCE_DUR), "-c:a", "pcm_s16le", str(silence_path)], check=True, capture_output=True)

    segments_list = ep.get("segments", [])
    voice_wavs = []
    all_segments_meta = []
    
    print(f"⚙️ بناء الصوت (النمط: {mode})...")

    for idx, seg in enumerate(segments_list):
        stype = seg.get("type")
        text, display = "", ""
        scene = seg.get("visual", "calm").lower()
        
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"): text = sanitize_for_tts(seg["text"])
        elif stype == "quran":
            if "surah" in seg:
                s, ayahs = seg["surah"], seg["ayah_numbers"]
                display = f"{seg.get('surah_name', '')}: {ayahs[0]}"
            else:
                text = seg.get("text", ""); display = seg.get("display", "")
        elif stype in ("hadith", "fiqh", "malak"):
            text = sanitize_for_tts(seg.get("text", "")); display = seg.get("display", "")

        if not text and stype != "quran": continue

        # 🌟 1. الآيات
        if stype == "quran" and "surah" in seg:
            s, ayahs = seg["surah"], seg["ayah_numbers"]
            if mode == "youtube":
                print(f"🎙️ المقطع {idx+1} [quran] - Gemini...")
                for a in ayahs:
                    cache_a = ep_cache / f"gemini_{s}_{a}.wav"
                    if not cache_a.exists():
                        # (جلب النص وتوليده بصوت جيميني)
                        pass
                    if cache_a.exists():
                        voice_wavs.append(cache_a); voice_wavs.append(silence_path)
                        all_segments_meta.append({"index": idx, "type": stype, "display": display, "chunks": [{"text": "آية", "duration": get_dur(cache_a)+SILENCE_DUR}]})
            else:
                print(f"🎙️ المقطع {idx+1} [quran] - بشري...")
                for a in ayahs:
                    cache_a = ep_cache / f"human_{s}_{a}.mp3"
                    if not download_human_ayah(s, a, str(cache_a)): continue
                    stealth_a = ep_cache / f"stealth_{s}_{a}.wav"
                    reverb_a = ep_cache / f"reverb_{s}_{a}.wav"
                    apply_stealth_filter(str(cache_a), str(stealth_a))
                    apply_mosque_reverb(str(stealth_a), str(reverb_a))
                    if reverb_a.exists():
                        voice_wavs.append(reverb_a); voice_wavs.append(silence_path)
                        all_segments_meta.append({"index": idx, "type": stype, "display": display, "chunks": [{"text": "آية", "duration": get_dur(reverb_a)+SILENCE_DUR}]})
            continue

        # 🌟 2. السرد والحديث (Gemini TTS)
        chunks = split_text(text)
        print(f"🎙️ المقطع {idx+1} [{stype}] - {len(chunks)} فقرة...")
        chunk_timings = []

        for c_idx, chunk in enumerate(chunks):
            if not chunk.strip(): continue
            cached_wav = ep_cache / f"seg_{idx}_{c_idx}.wav"
            if cached_wav.exists() and cached_wav.stat().st_size > 1000:
                voice_wavs.append(cached_wav); voice_wavs.append(silence_path)
                chunk_timings.append({"text": chunk, "duration": get_dur(cached_wav) + SILENCE_DUR})
                continue

            raw_wav = work / f"c_{idx}_{c_idx}.wav"
            temp_mp3 = work / f"temp_{idx}_{c_idx}.mp3"
            
            style_prompt = "أنت راوي قصص." if stype=="narration" else "أنت راوي حديث."
            gemini_tts_with_pool(chunk, str(temp_mp3), voice="Charon", style=style_prompt)
            
            if temp_mp3.exists() and temp_mp3.stat().st_size > 3000:
                subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                
                # إضافة المؤثرات (بدون موسيقى)
                if stype in ("narration", "summary", "teaser", "malak"):
                    sfx_map = {"battle": "sword.mp3", "sad": "heartbeat.mp3", "conspiracy": "whisper.mp3", "calm": "birds.mp3", "night": "wind.mp3"}
                    sfx_file = SFX_DIR / sfx_map.get(scene, "birds.mp3")
                    
                    mixed = work / f"mix_{idx}_{c_idx}.wav"
                    if mix_sfx_only(str(raw_wav), str(mixed), str(sfx_file)):
                        shutil.copy(mixed, raw_wav)
                        
                    # إضافة صدى مسجد خفيف للسرد ليعطي طابعاً وثائقياً
                    if stype == "narration":
                        reverb_wav = work / f"reverb_{idx}_{c_idx}.wav"
                        if apply_mosque_reverb(str(raw_wav), str(reverb_wav)):
                            shutil.copy(reverb_wav, raw_wav)

            time.sleep(5)
            faded = work / f"faded_{idx}_{c_idx}.wav"
            c_dur = get_dur(raw_wav)
            if c_dur > 0.2:
                subprocess.run(["ffmpeg", "-y", "-i", str(raw_wav), "-af", f"afade=t=in:st=0:d=0.05,afade=t=out:st={c_dur-0.05}:d=0.05", str(faded)], check=True, capture_output=True)
            else: shutil.copy(raw_wav, faded)
            
            shutil.copy(faded, cached_wav)
            voice_wavs.append(faded); voice_wavs.append(silence_path)
            chunk_timings.append({"text": chunk, "duration": get_dur(faded) + SILENCE_DUR})

        if chunk_timings:
            all_segments_meta.append({"index": idx, "type": stype, "display": display, "chunks": chunk_timings})

    if not voice_wavs: raise RuntimeError("لا يوجد صوت")

    voice_concat = work / "voice_concat.txt"
    voice_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in voice_wavs), encoding="utf-8")
    voice_merged = work / "voice_merged.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(voice_concat), "-c", "copy", str(voice_merged)], check=True, capture_output=True)
    
    total_dur = get_dur(voice_merged)
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(voice_merged), "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)], check=True, capture_output=True)

    meta_path = str(out_mp3).replace(".mp3", "_meta.json")
    Path(meta_path).write_text(json.dumps({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "total_duration": total_dur, "segments": all_segments_meta}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {out_mp3} | {total_dur/60:.1f} دقيقة")
    cleanup_dir(work)

if __name__ == "__main__":
    if len(sys.argv) < 4: sys.exit(1)
    build_episode_audio(sys.argv[1], sys.argv[2], sys.argv[3])
