import os, sys, json, subprocess, tempfile, shutil, re, time, gzip, hashlib, asyncio
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir

# ═══════════════════════════════════════════════════════════
# 🌟 TTS_ENGINE = "1" → Gemini TTS (السرد حصراً)
# ═══════════════════════════════════════════════════════════
TTS_ENGINE = "1"  # 1=Gemini | 2=Edge TTS

if TTS_ENGINE == "1":
    print("🔊 [TTS] المحرك: Gemini TTS", flush=True)
    from gemini_tts_pool import gemini_tts_with_pool as _engine_generate
    def generate_audio(text, out_path):
        try:
            _engine_generate(text, out_path)
            return Path(out_path).exists() and Path(out_path).stat().st_size > 1000
        except Exception as e:
            print(f"   ⚠️ Gemini TTS فشل: {str(e)[:100]}", flush=True)
            return False
else:
    print("🔊 [TTS] المحرك: Edge TTS", flush=True)
    import edge_tts
    def generate_audio(text, out_path):
        try:
            async def _run():
                comm = edge_tts.Communicate(text, "ar-SY-LaithNeural", rate="-12%")
                await comm.save(out_path)
            asyncio.run(_run())
            return Path(out_path).exists() and Path(out_path).stat().st_size > 1000
        except Exception as e:
            print(f"   ⚠️ Edge TTS فشل: {str(e)[:100]}", flush=True)
            return False

TARGET_SR = "24000"
SILENCE_DUR = 0.35  # صمت طبيعي بين الجمل
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"
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
except Exception:
    pass

def get_local_ayah_text(surah, ayah):
    return _ayah_lib.get(f"{surah}:{ayah}", "")

def trim_and_fade(input_wav, output_wav, speed_up=False, is_last=False):
    """speed_up=False دائماً للسرد والتلاوة - التسريع فقط بعد RVC"""
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_wav], capture_output=True, text=True)
        dur = float(probe.stdout.strip() or 0)
        if dur > 0.5:
            fade_filter = f",afade=t=out:st={max(0, dur - 0.2)}:d=0.2" if is_last else ""
            subprocess.run([
                "ffmpeg", "-y", "-i", input_wav,
                "-af", f"aresample=async=1:first_pts=0{fade_filter}",
                "-ar", TARGET_SR, "-ac", "1", output_wav
            ], check=True, capture_output=True, timeout=60)
            return True
        else:
            shutil.copy(input_wav, output_wav)
            return True
    except Exception:
        shutil.copy(input_wav, output_wav)
        return False

def apply_mosque_reverb(input_wav, output_wav):
    """يُطبق فقط على yt_wavs (يوتيوب)، وليس على social_wavs"""
    try:
        filters = ("atempo=0.92,equalizer=f=120:t=q:w=1.0:g=4,equalizer=f=4000:t=q:w=1.0:g=-4,aecho=0.8:0.7:60|180|320:0.15|0.10|0.05,acompressor=threshold=0.1:ratio=3:attack=20:release=200")
        subprocess.run(["ffmpeg", "-y", "-i", input_wav, "-af", filters, "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
        return True
    except Exception:
        shutil.copy(input_wav, output_wav)
        return False

# 🌟 ترجع اسم القارئ أو None
def download_human_ayah(surah, ayah, out_path):
    filename = f"{surah:03d}{ayah:03d}.mp3"
    import random
    reciters = ["Yasser_Ad-Dussary_128kbps", "Husary_128kbps"]
    reciter = random.choice(reciters)
    url = f"{QURAN_BASE_URL}/{reciter}/{filename}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            with open(out_path, "wb") as f:
                f.write(r.content)
            return reciter  # 🌟 نرجع اسم القارئ
    except Exception:
        pass
    return None

def smart_diacritize(text):
    """تشكيل بسيط + maxOutputTokens كبير + فحص دقيق"""
    if not text or not text.strip():
        return text
    if not re.search(r'[\u0600-\u06FF]', text):
        return text
    
    prompt = f"""أضف التشكيل الكامل (الحركات) للنص التالي.
لا تغير أي كلمة. لا تحذف أي كلمة. لا تشرح. أرجع النص المشكول فقط.

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
            "generationConfig": {"temperature": 0.05, "maxOutputTokens": 8192}
        }
        r = requests.post(url, json=body, timeout=120)
        if r.status_code == 200:
            parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            if parts:
                diacritized = parts[0].get("text", "").strip()
                
                if not diacritized:
                    print("   ⚠️ التشكيل رجع فارغ. استخدام النص الأصلي.")
                    return text
                
                ratio = len(diacritized) / len(text)
                diacritic_count = len(re.findall(r'[\u064B-\u065F]', diacritized))
                original_diacritic_count = len(re.findall(r'[\u064B-\u065F]', text))
                
                if diacritic_count <= original_diacritic_count:
                    print(f"   ⚠️ التشكيل ما أضاف حركات. استخدام النص الأصلي.")
                    return text
                
                if 0.95 < ratio < 1.8:
                    print(f"   ✅ التشكيل نجح (ratio={ratio:.2f}, حركات={diacritic_count})")
                    return diacritized
                else:
                    print(f"   ⚠️ التشكيل مشوه (ratio={ratio:.2f}). استخدام النص الأصلي.")
                    return text
    except Exception as e:
        print(f"   ⚠️ التشكيل فشل: {str(e)[:100]}", flush=True)
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
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0

def split_text(text, max_chars=250):
    """جمل أقصر = إيقاع طبيعي"""
    sentences = re.split(r'(?<=[.!؟])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_chars:
            current += " " + s
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
        
        # ═══════════════════════════════════════════════
        # السرد: Gemini TTS، لا تسريع
        # ═══════════════════════════════════════════════
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = sanitize_for_tts(seg.get("text", ""))
            if not text: continue
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
                    # 🌟 السرد: لا تسريع (speed_up=False)
                    trim_and_fade(str(cached_wav), str(faded), speed_up=False, is_last=is_last_chunk)
                    yt_wavs.extend([faded, silence_path])
                    social_wavs.extend([faded, silence_path])
                    chunk_timings.append({"text": chunk, "duration": get_dur(faded) + SILENCE_DUR})
            
            if chunk_timings:
                meta_entry = {"index": idx, "type": stype, "display": "", "chunks": chunk_timings}
                yt_meta.append(meta_entry)
                social_meta.append(meta_entry)
        
        # ═══════════════════════════════════════════════
        # التلاوة: خام بدون reverb، بدون تسريع (لتدخل RVC نظيفة)
        # ═══════════════════════════════════════════════
        elif stype == "quran":
            s, ayahs = seg.get("surah", 0), seg.get("ayah_numbers", [])
            if not ayahs: continue
            display = f"{seg.get('surah_name', '')}: {ayahs[0] if ayahs else ''}"
            
            # YT: TTS + reverb (كما هو)
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
                trim_and_fade(str(cache_a), str(trimmed_a), speed_up=False, is_last=True)
                final_a = ep_cache / f"final_yt_{s}_{a}_{text_hash}.wav"
                if not final_a.exists():
                    apply_mosque_reverb(str(trimmed_a), str(final_a))
                if final_a.exists():
                    yt_wavs.extend([final_a, silence_path])
                    yt_chunk_timings.append({"text": text_a, "duration": get_dur(final_a) + SILENCE_DUR})
            if yt_chunk_timings:
                yt_meta.append({"index": idx, "type": stype, "display": display, "chunks": yt_chunk_timings})
            
            # Social: تحميل القارئ + تخزين اسمه (بدون reverb، بدون تسريع)
            soc_chunk_timings = []
            for a in ayahs:
                text_a = sanitize_for_tts(get_local_ayah_text(s, a))
                if not text_a: continue
                
                cache_a = ep_cache / f"human_{s}_{a}.mp3"
                reciter = download_human_ayah(s, a, str(cache_a))
                if not reciter: 
                    print(f"   ⚠️ فشل تحميل {s}:{a}")
                    continue
                
                trimmed_h = ep_cache / f"trimmed_human_{s}_{a}.wav"
                # 🌟 بدون تسريع، بدون reverb (ستمر بـ RVC لاحقاً)
                trim_and_fade(str(cache_a), str(trimmed_h), speed_up=False, is_last=True)
                
                if trimmed_h.exists():
                    social_wavs.extend([trimmed_h, silence_path])
                    soc_chunk_timings.append({
                        "text": text_a, 
                        "duration": get_dur(trimmed_h) + SILENCE_DUR,
                        "reciter": reciter  # 🌟 حفظ القارئ للاستخدام في apply_rvc
                    })
            
            if soc_chunk_timings:
                social_meta.append({"index": idx, "type": stype, "display": display, "chunks": soc_chunk_timings})
    
    # ═══════════════════════════════════════════════
    # دمج YT
    # ═══════════════════════════════════════════════
    if yt_wavs:
        yt_concat = work / "yt_concat.txt"
        yt_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in yt_wavs), encoding="utf-8")
        yt_merged = work / "yt_merged.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(yt_concat), "-c", "copy", str(yt_merged)], check=True, capture_output=True)
        Path(out_yt_mp3).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(yt_merged), "-c:a", "libmp3lame", "-b:a", "192k", str(out_yt_mp3)], check=True, capture_output=True)
        Path(str(out_yt_mp3).replace(".mp3", "_meta.json")).write_text(json.dumps({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "total_duration": get_dur(out_yt_mp3), "segments": yt_meta}, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # ═══════════════════════════════════════════════
    # دمج Social (السرد Gemini + التلاوة الخام)
    # ═══════════════════════════════════════════════
    if social_wavs:
        soc_concat = work / "soc_concat.txt"
        soc_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in social_wavs), encoding="utf-8")
        soc_merged = work / "soc_merged.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(soc_concat), "-c", "copy", str(soc_merged)], check=True, capture_output=True)
        Path(out_social_mp3).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(soc_merged), "-c:a", "libmp3lame", "-b:a", "192k", str(out_social_mp3)], check=True, capture_output=True)
        Path(str(out_social_mp3).replace(".mp3", "_meta.json")).write_text(json.dumps({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "total_duration": get_dur(out_social_mp3), "segments": social_meta}, ensure_ascii=False, indent=2), encoding="utf-8")
    
    cleanup_dir(work)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="human")
    parser.add_argument("--json", required=True)
    parser.add_argument("--out_audio", required=True)
    parser.add_argument("--out_meta", required=True)
    args = parser.parse_args()
    build_episode_audio(args.json, "/tmp/dummy_yt.mp3", args.out_audio)
