import os, sys, json, subprocess, tempfile, shutil, re, time, asyncio
from pathlib import Path
import requests
import edge_tts

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir, notify_telegram
from gemini_tts_pool import gemini_tts_with_pool

try:
    from quranic_phonemizer import Phonemizer
    print("✅ quranic-phonemizer loaded successfully")
except Exception as e:
    print(f"❌ Failed to load quranic-phonemizer: {e}")
    Phonemizer = None

TARGET_SR = "24000"
SILENCE_DUR = 0.4

_quran_phonemizer = None
def get_quran_phonemizer():
    global _quran_phonemizer
    if _quran_phonemizer is None and Phonemizer is not None:
        try:
            _quran_phonemizer = Phonemizer()
            print("✅ Phonemizer initialized")
        except Exception as e:
            print(f"⚠️ Failed to initialize Phonemizer: {e}")
    return _quran_phonemizer

def generate_quran_audio_espeak(uthmani_text: str, out_wav: str) -> bool:
    """يولد صوت الآية من الفونيمات التجويدية عبر espeak-ng."""
    try:
        pm = get_quran_phonemizer()
        if not pm:
            return False

        phonemes = pm.phonemize(uthmani_text)
        if not phonemes:
            print("   ⚠️ No phonemes generated")
            return False

        espeak_input = f"[[{phonemes}]]"
        subprocess.run([
            "espeak-ng",
            "-v", "ar",
            "-s", "125",
            "-p", "45",
            "-a", "180",
            "-w", out_wav,
            espeak_input,
        ], check=True, capture_output=True, timeout=120)

        return Path(out_wav).exists() and Path(out_wav).stat().st_size > 2000
    except Exception as e:
        print(f"   ⚠️ espeak-ng failed: {e}")
        return False

def sanitize_for_tts(t):
    t = re.sub(r'\[.*?\]|\(.*?\)|﴿.*?﴾', '', t)
    t = re.sub(r'[a-zA-Z*#@_\[\]\(\)]', '', t)
    return re.sub(r"\s+", " ", re.sub(r"[ۖۗۘۙۚۛ۞]", "", t.replace("۝", " "))).strip()

def get_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    try: return round(float(r.stdout.strip()), 3)
    except: return 0.0

def fetch_ayah_uthmani(s, a):
    try:
        url = f"https://api.alquran.cloud/v1/ayah/{s}:{a}/quran-uthmani"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()["data"]["text"]
    except Exception as e:
        print(f"⚠️ فشل جلب الآية {s}:{a} - {e}")
    return ""

async def _edge_tts_async(text, out_path):
    comm = edge_tts.Communicate(text, "ar-SA-HamedNeural", rate="-10%")
    await comm.save(out_path)

def edge_generate(text, out_path):
    asyncio.run(_edge_tts_async(text, out_path))

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

def build_episode_audio(json_path, out_mp3):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    final_wavs, all_segments_meta = [], []
    
    silence_path = work / "silence.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", str(SILENCE_DUR), "-c:a", "pcm_s16le", str(silence_path)], check=True, capture_output=True)

    segments_list = ep.get("segments", [])
    total_segs = len(segments_list)
    test_mode = os.environ.get("TEST_MODE") == "1"
    
    print(f"⚙️ وضع الاختبار: {'مفعل' if test_mode else 'معطل'}")

    taken_narration = False
    taken_quran = False

    for idx, seg in enumerate(segments_list):
        stype = seg.get("type")
        is_ending_segment = (total_segs - idx) <= 3
        
        if test_mode:
            if stype == "narration" and not taken_narration:
                taken_narration = True
            elif stype == "quran" and not taken_quran:
                taken_quran = True
            elif not is_ending_segment:
                continue
            
        text, display = "", ""
        
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = sanitize_for_tts(seg["text"])
        elif stype == "quran":
            s, ayahs = seg["surah"], seg["ayah_numbers"]
            text = " ".join(filter(None, (fetch_ayah_uthmani(s, a) for a in ayahs)))
            display = f"{seg.get('surah_name', '')}: {ayahs[0]}"
        
        if not text: continue
        
        chunks = split_text(text)
        chunk_timings = []
        
        print(f"🎙️ المقطع {idx+1} [{stype}] - {len(chunks)} فقرة...", flush=True)
        
        for c_idx, chunk in enumerate(chunks):
            if not chunk.strip(): continue
            
            raw_wav = work / f"c_{idx}_{c_idx}.wav"
            temp_mp3 = work / f"temp_{idx}_{c_idx}.mp3"
            success = False
            
            # ═══ 1. الآيات: espeak-ng + تجويد ═══
            if stype == "quran":
                if generate_quran_audio_espeak(chunk, str(raw_wav)):
                    success = True
                    print(f"   ✅ espeak-ng (تجويد)", flush=True)
                else:
                    print(f"   ⚠️ espeak فشل، سيتم استخدام Gemini للآية كاحتياطي...", flush=True)
            
            # ═══ 2. السرد أو الاحتياطي: Gemini TTS ═══
            if not success:
                try:
                    gemini_tts_with_pool(chunk, str(temp_mp3), voice="Charon")
                    if temp_mp3.exists() and temp_mp3.stat().st_size > 3000:
                        subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                        success = True
                        print(f"   ✅ Gemini ({stype})", flush=True)
                except Exception as e:
                    print(f"   ⚠️ Gemini فشل: {e}", flush=True)

            # ═══ 3. احتياطي أخير: Edge TTS ═══
            if not success:
                print(f"   🚨 Edge TTS احتياطي...", flush=True)
                try:
                    edge_generate(chunk, str(temp_mp3))
                    if temp_mp3.exists() and temp_mp3.stat().st_size > 2000:
                        subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                        success = True
                except Exception as e:
                    print(f"   ⚠️ Edge TTS فشل: {e}", flush=True)

            if not success: continue

            faded = work / f"faded_{idx}_{c_idx}.wav"
            c_dur = get_dur(raw_wav)
            if c_dur > 0.2:
                subprocess.run(["ffmpeg", "-y", "-i", str(raw_wav), "-af", "afade=t=in:st=0:d=0.05,afade=t=out:st=" + str(c_dur-0.05) + ":d=0.05", str(faded)], check=True, capture_output=True)
            else:
                shutil.copy(raw_wav, faded)

            final_wavs.append(faded)
            final_wavs.append(silence_path)
            chunk_timings.append({"text": chunk, "duration": get_dur(faded) + SILENCE_DUR})
            
        if chunk_timings:
            all_segments_meta.append({"index": idx, "type": stype, "display": display, "chunks": chunk_timings})

    if not final_wavs: raise RuntimeError("لا يوجد صوت")

    final_concat = work / "final.txt"
    final_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in final_wavs), encoding="utf-8")
    merged_wav = work / "merged.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(final_concat), "-c", "copy", str(merged_wav)], check=True, capture_output=True)
    
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(merged_wav), "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)], check=True, capture_output=True)
    
    meta_path = str(out_mp3).replace(".mp3", "_meta.json")
    Path(meta_path).write_text(json.dumps({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "story_id": ep.get("story_id", ""), "total_duration": get_dur(out_mp3), "segments": all_segments_meta}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {out_mp3} | {get_dur(out_mp3)/60:.1f} دقيقة", flush=True)
    cleanup_dir(work)

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    build_episode_audio(sys.argv[1], sys.argv[2])
