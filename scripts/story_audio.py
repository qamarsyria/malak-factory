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
from gemini_tts_pool import gemini_tts_with_pool

TARGET_SR = "24000"
TARGET_BR = "192k"
SILENCE_DUR = 0.3

FIXES = {
    "ﷺ": "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ",
    "صلى الله عليه وسلم": "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ",
    "رضي الله عنه": "رَضِيَ اللَّهُ عَنْهُ", 
    "رضي الله عنها": "رَضِيَ اللَّهُ عَنْهَا",
    "عليه السلام": "عَلَيْهِ السَّلَامُ", 
    "عليها السلام": "عَلَيْهَا السَّلَامُ",
    "الله": "اللَّه", 
    "القرآن": "الْقُرْآن", 
    "محمد": "مُحَمَّد", 
    "رسول": "رَسُول"
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

def split_into_small_chunks(text, max_words=6):
    # تقسيم النص إلى جمل قصيرة جداً لضمان سرعة الاستجابة ومنع الفشل
    words = text.split()
    chunks, current = [], []
    for w in words:
        current.append(w)
        if len(current) >= max_words or any(p in w for p in [".", "؟", "!", "،", "؛"]):
            chunks.append(" ".join(current))
            current = []
    if current: chunks.append(" ".join(current))
    return chunks

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
        
        # تقسيم النص إلى جمل صغيرة
        chunks = split_into_small_chunks(clean_text, max_words=6)
        chunk_files = []
        chunk_timings = []
        
        print(f"🎙️ المقطع {idx+1} [{stype}] - {len(chunks)} جملة...", flush=True)
        
        for c_idx, chunk in enumerate(chunks):
            if not chunk.strip(): continue
            
            raw_chunk = work / f"c_{idx}_{c_idx}.mp3"
            
            success = False
            for attempt in range(4): # محاولات أكثر لجمل قصيرة
                try:
                    gemini_tts_with_pool(chunk, str(raw_chunk), voice="Charon")
                    if raw_chunk.exists() and raw_chunk.stat().st_size > 2000:
                        success = True; break
                except Exception as e:
                    print(f"   ⚠️ محاولة {attempt+1} فشلت: {str(e)[:50]}", flush=True)
                    time.sleep(2) # انتظار قبل المحاولة التالية
                    
            if not success:
                print(f"   ❌ فشل في جملة: {chunk[:20]}... تم وضع صمت.", flush=True)
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", "1.5", "-c:a", "libmp3lame", str(raw_chunk)], check=True, capture_output=True)

            c_dur = get_dur(raw_chunk)
            chunk_files.append(raw_chunk)
            
            # إضافة صمت قصير جداً بين الجمل (0.2 ثانية)
            silent_c = work / f"sc_{idx}_{c_idx}.mp3"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", "0.2", "-c:a", "libmp3lame", str(silent_c)], check=True, capture_output=True)
            chunk_files.append(silent_c)
            
            # تسجيل توقيت الجملة (النص + الوقت الدقيق)
            chunk_timings.append({"text": chunk, "duration": c_dur + 0.2})
            
            time.sleep(1) # تأخير بسيط جداً بين طلبات Gemini لمنع الحظر (Rate Limit)
            
        # دمج جمل المقطع الواحد
        seg_concat = work / f"seg_{idx}.txt"
        seg_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in chunk_files), encoding="utf-8")
        seg_audio = work / f"seg_{idx}.mp3"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(seg_concat), "-c:a", "libmp3lame", str(seg_audio)], check=True, capture_output=True)
        
        # إضافة صمت بعد المقطع كاملاً
        if idx < len(ep.get("segments", [])) - 1:
            chunk_timings.append({"text": "", "duration": SILENCE_DUR, "is_silence": True})
            
        all_segments_meta.append({
            "index": idx,
            "type": stype,
            "display": display,
            "chunks": chunk_timings, # التوقيتات الدقيقة هنا
            "audio_file": str(seg_audio)
        })

    # الدمج النهائي
    final_concat = work / "final.txt"
    final_concat.write_text("\n".join(f"file '{s['audio_file']}'" for s in all_segments_meta), encoding="utf-8")
    
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(final_concat), "-c:a", "libmp3lame", "-b:a", TARGET_BR, str(out_mp3)], check=True, capture_output=True)
    
    meta_path = str(out_mp3).replace(".mp3", "_meta.json")
    Path(meta_path).write_text(json.dumps({
        "episode": ep.get("episode_number", 1), 
        "title": ep.get("title", ""), 
        "story_id": ep.get("story_id", ""), 
        "total_duration": get_dur(out_mp3), 
        "segments": all_segments_meta
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {out_mp3} | {get_dur(out_mp3)/60:.1f} دقيقة", flush=True)
    cleanup_dir(work)

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    try: build_episode_audio(sys.argv[1], sys.argv[2])
    except Exception as e: 
        import traceback; traceback.print_exc(); sys.exit(1)
