import os
import sys
import json
import subprocess
import tempfile
import shutil
import re
import requests
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir, notify_telegram
# سنستخدم دالة توليد مباشرة بدلاً من النظام القديم لتجنب الانتظار
from gemini_tts_pool import get_pool, discover_tts_models

TARGET_SR = "24000"
TARGET_BR = "192k"
SILENCE_DUR = 0.3

FIXES = {
    "ﷺ": "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ", "صلى الله عليه وسلم": "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ",
    "رضي الله عنه": "رَضِيَ اللَّهُ عَنْهُ", "رضي الله عنها": "رَضِيَ اللَّهُ عَنْهَا",
    "عليه السلام": "عَلَيْهِ السَّلَامُ", "عليها السلام": "عَلَيْهَا السَّلَامُ",
    "الله": "اللَّه", "القرآن": "الْقُرْآن", "محمد": "مُحَمَّد", "رسول": "رَسُول"
}

def fix_text(t):
    t = t.replace("ﷺ", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    for k, v in sorted(FIXES.items(), key=lambda x: -len(x[0])):
        t = re.sub(rf'(?<![ء-ي\u064B-\u0652]){re.escape(k)}(?![ء-ي\u064B-\u0652])', v, t)
    return t

def clean(t):
    t = re.sub(r'\[.*?\]|\(.*?\)|﴿.*?﴾', '', t)
    t = re.sub(r'[a-zA-Z]', '', t)
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

def split_into_sentences(text, max_chars=250):
    sentences = re.split(r'(?<=[.!؟])\s+', text)
    chunks, current = [], ""
    for sentence in sentences:
        if not sentence.strip(): continue
        if len(sentence) > max_chars:
            words = sentence.split()
            for w in words:
                if len(current) + len(w) + 1 <= max_chars:
                    current = (current + " " + w).strip()
                else:
                    chunks.append(current)
                    current = w
        else:
            if len(current) + len(sentence) + 1 <= max_chars:
                current = (current + " " + sentence).strip()
            else:
                chunks.append(current)
                current = sentence
    if current: chunks.append(current)
    return chunks

# دالة توليد مباشرة بدون نظام الانتظار الطويل
def generate_audio_fast(text, out_file):
    import base64
    import wave
    pool = get_pool()
    models = discover_tts_models()
    
    for model in models[:2]:
        for _ in range(len(pool.keys)):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": text}]}],
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {"languageCode": "ar-XA", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Charon"}}},
                    },
                }
                r = requests.post(url, json=body, timeout=60)
                if r.status_code == 429:
                    pool.mark_failed(60) # تبريد سريع بدون انتظار
                    continue
                if r.status_code != 200: continue
                
                data = r.json()
                parts = (data.get("candidates", [{}])[0].get("content", {})).get("parts", [])
                if not parts: continue
                inline = parts[0].get("inlineData", {})
                audio_b64 = inline.get("data")
                if not audio_b64: continue
                
                raw = base64.b64decode(audio_b64)
                wav_file = str(out_file).replace(".mp3", ".wav")
                with wave.open(wav_file, "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
                    wf.writeframes(raw)
                
                subprocess.run(["ffmpeg", "-y", "-i", wav_file, "-c:a", "libmp3lame", str(out_file)], check=True, capture_output=True)
                return True
            except Exception:
                continue
    return False

def build_episode_audio(json_path, out_mp3):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="ep_"))
    
    silence = work / "sil.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", str(SILENCE_DUR), "-c:a", "libmp3lame", str(silence)], check=True, capture_output=True)

    all_segments_meta = []

    for idx, seg in enumerate(ep.get("segments", [])):
        stype = seg.get("type")
        text, display = "", ""
        
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = fix_text(seg["text"])
        elif stype == "quran":
            s, ayahs = seg["surah"], seg["ayah_numbers"]
            text = " ".join(filter(None, (fetch_ayah(s, a) for a in ayahs)))
            display = f"{seg.get('surah_name', '')}: {ayahs[0]}"
        else:
            continue
            
        clean_text = clean(text)
        if not clean_text: continue
        
        chunks = split_into_sentences(clean_text, max_chars=250)
        chunk_files = []
        chunk_timings = []
        
        print(f"🎙️ المقطع {idx+1} [{stype}] - {len(chunks)} فقرة...", flush=True)
        
        for c_idx, chunk in enumerate(chunks):
            if not chunk.strip(): continue
            
            raw_chunk = work / f"c_{idx}_{c_idx}.mp3"
            success = generate_audio_fast(chunk, raw_chunk)
                    
            if not success:
                print(f"   ❌ فشل: {chunk[:20]}... تم وضع صمت.", flush=True)
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", "2", "-c:a", "libmp3lame", str(raw_chunk)], check=True, capture_output=True)

            c_dur = get_dur(raw_chunk)
            chunk_files.append(raw_chunk)
            
            silent_c = work / f"sc_{idx}_{c_idx}.mp3"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", "0.2", "-c:a", "libmp3lame", str(silent_c)], check=True, capture_output=True)
            chunk_files.append(silent_c)
            
            chunk_timings.append({"text": chunk, "duration": c_dur + 0.2})
            
        seg_concat = work / f"seg_{idx}.txt"
        seg_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in chunk_files), encoding="utf-8")
        seg_audio = work / f"seg_{idx}.mp3"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(seg_concat), "-c:a", "libmp3lame", str(seg_audio)], check=True, capture_output=True)
        
        if idx < len(ep.get("segments", [])) - 1:
            chunk_timings.append({"text": "", "duration": SILENCE_DUR, "is_silence": True})
            
        all_segments_meta.append({
            "index": idx, "type": stype, "display": display,
            "chunks": chunk_timings, "audio_file": str(seg_audio)
        })

    final_concat = work / "final.txt"
    final_concat.write_text("\n".join(f"file '{s['audio_file']}'" for s in all_segments_meta), encoding="utf-8")
    
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(final_concat), "-c:a", "libmp3lame", "-b:a", TARGET_BR, str(out_mp3)], check=True, capture_output=True)
    
    meta_path = str(out_mp3).replace(".mp3", "_meta.json")
    Path(meta_path).write_text(json.dumps({
        "episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "story_id": ep.get("story_id", ""), 
        "total_duration": get_dur(out_mp3), "segments": all_segments_meta
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {out_mp3} | {get_dur(out_mp3)/60:.1f} دقيقة", flush=True)
    cleanup_dir(work)

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    build_episode_audio(sys.argv[1], sys.argv[2])
