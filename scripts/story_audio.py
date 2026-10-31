import os, sys, json, subprocess, tempfile, shutil, re, time
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir
from gemini_tts_pool import gemini_tts_with_pool

TARGET_SR = "24000"
SILENCE_DUR = 0.4 # فاصل زمني بين الفقرات

def clean(t):
    t = re.sub(r'\[.*?\]|\(.*?\)|﴿.*?﴾', '', t)
    t = re.sub(r'[a-zA-Z*#@_]', '', t)
    return re.sub(r"\s+", " ", re.sub(r"[ۖۗۘۙۚۛ۞]", "", t.replace("۝", " "))).strip()

def get_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True)
    try: return round(float(r.stdout.strip()), 3)
    except: return 0.0

def fetch_ayah(s, a):
    try:
        r = requests.get(f"{QURAN_API}/ayah/{s}:{a}", timeout=10)
        if r.status_code == 200: return r.json()["data"]["text"]
    except: pass
    return ""

def split_text(text, max_chars=800):
    # دمج الجمل في فقرات كبيرة (لتوفير الحصة)
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

    for idx, seg in enumerate(ep.get("segments", [])):
        stype = seg.get("type")
        text, display = "", ""
        
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = clean(seg["text"])
        elif stype == "quran":
            s, ayahs = seg["surah"], seg["ayah_numbers"]
            text = " ".join(filter(None, (fetch_ayah(s, a) for a in ayahs)))
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
            
            for attempt in range(3):
                try:
                    time.sleep(5) # تأخير 5 ثوانٍ لتجنب حظر المفاتيح
                    
                    if stype == "quran":
                        # تعليمات تجويد متوازنة (بدون مبالغة)
                        style = "Recite with accurate Tajweed, moderate pace, clear pronunciation, without exaggeration."
                    else:
                        style = "Calm, slow narrative voice, with natural pauses between sentences."
                        
                    gemini_tts_with_pool(chunk, str(temp_mp3), voice="Charon", style=style)
                    
                    if temp_mp3.exists() and temp_mp3.stat().st_size > 3000:
                        subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                        success = True
                        break
                except Exception as e:
                    print(f"   ⚠️ محاولة {attempt+1} فشلت: {str(e)[:80]}", flush=True)
                    time.sleep(5)

            if not success:
                print(f"   ❌ فشل تام. تخطي فقرة.", flush=True)
                continue

            # إصلاح الخشّة
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
