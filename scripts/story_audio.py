import os
import sys
import json
import subprocess
import tempfile
import shutil
import re
import requests
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
    t = re.sub(r'\[.*?\]|\(.*?\)|﴿.*?﴾', '', t) # إزالة أي آية بين الأقواس
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

def build_episode_audio(json_path, out_mp3):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="ep_"))
    
    silence = work / "sil.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", str(SILENCE_DUR), "-c:a", "libmp3lame", str(silence)], check=True, capture_output=True)

    seg_files = []
    meta_segments = []

    for idx, seg in enumerate(ep.get("segments", [])):
        stype = seg.get("type")
        text, clean_txt, display = "", "", ""
        
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = fix_text(seg["text"])
            clean_txt = clean(text)
            seg["text"] = text
        elif stype == "quran":
            s, ayahs = seg["surah"], seg["ayah_numbers"]
            txt = " ".join(filter(None, (fetch_ayah(s, a) for a in ayahs)))
            text, clean_txt = txt, clean(txt)
            display = f"{seg.get('surah_name', '')}: {ayahs[0]}"
        else:
            continue
            
        if not clean_txt: continue
        
        raw = work / f"raw_{idx:03d}.mp3"
        print(f"🎙️ Gemini [{stype} {idx}]...", flush=True)
        
        success = False
        for attempt in range(3):
            try:
                gemini_tts_with_pool(clean_txt, str(raw), voice="Charon")
                if raw.exists() and raw.stat().st_size > 5000:
                    success = True; break
            except Exception as e:
                print(f"   ⚠️ خطأ: {str(e)[:80]}", flush=True)
                
        if not success:
            # إصلاح: عدم الانهيار، بل وضع صمت 3 ثوانٍ
            print(f"❌ فشل Gemini في المقطع {idx}. تم وضع صمت بدلاً منه.", flush=True)
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", "3", "-c:a", "libmp3lame", str(raw)], check=True, capture_output=True)

        raw_dur = get_dur(raw)
        faded = work / f"faded_{idx:03d}.mp3"
        if raw_dur > 0.1:
            subprocess.run(["ffmpeg", "-y", "-i", str(raw), "-af", f"afade=t=in:st=0:d=0.05,afade=t=out:st={max(0,raw_dur-0.05)}:d=0.05", "-c:a", "libmp3lame", str(faded)], check=True, capture_output=True)
        else:
            shutil.copy(raw, faded)

        seg_files.append(faded)
        
        total_seg_dur = raw_dur
        if idx < len(ep.get("segments", [])) - 1:
            seg_files.append(silence)
            total_seg_dur += SILENCE_DUR

        meta_segments.append({
            "index": idx, 
            "type": stype, 
            "text": text, 
            "display": display, 
            "duration": total_seg_dur,
            "audio_dur": raw_dur,
            "engine": "gemini"
        })

    if not seg_files: raise RuntimeError("لا مقاطع")

    print(f"\n🔗 دمج {len(seg_files)} مقطع...", flush=True)
    concat = work / "final.txt"
    concat.write_text("\n".join(f"file '{p.resolve()}'" for p in seg_files), encoding="utf-8")
    
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c:a", "libmp3lame", "-b:a", TARGET_BR, str(out_mp3)], check=True, capture_output=True)
    
    meta_path = str(out_mp3).replace(".mp3", "_meta.json")
    Path(meta_path).write_text(json.dumps({
        "episode": ep.get("episode_number", 1), 
        "title": ep.get("title", ""), 
        "story_id": ep.get("story_id", ""), 
        "total_duration": get_dur(out_mp3), 
        "segments": meta_segments
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {out_mp3} | {get_dur(out_mp3)/60:.1f} دقيقة", flush=True)
    cleanup_dir(work)

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    try: build_episode_audio(sys.argv[1], sys.argv[2])
    except Exception as e: 
        import traceback; traceback.print_exc(); sys.exit(1)
