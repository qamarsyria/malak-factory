import os, sys, json, subprocess, tempfile, shutil, re, time, asyncio
from pathlib import Path
import requests
import edge_tts

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir, notify_telegram
from gemini_tts_pool import gemini_tts_with_pool

TARGET_SR = "24000"
SILENCE_DUR = 0.4

SPELLING_FIXES = {
    "تعالى": "تَعَالَى",
    "منشغلة": "مُنْشَغِلَة",
    "إرهاصا": "إِرْهَاصًا",
    "اللله": "اللَّه",
    "الله": "اللَّه",
    "بالله": "بِاللَّه",
    "والله": "وَاللَّه",
}

def sanitize_for_tts(t):
    t = re.sub(r'\[.*?\]|\(.*?\)|﴿.*?﴾', '', t)
    t = re.sub(r'[a-zA-Z*#@_\[\]\(\)]', '', t)
    return re.sub(r"\s+", " ", re.sub(r"[ۖۗۘۙۚۛ۞]", "", t.replace("۝", " "))).strip()

def apply_spelling_fixes(t):
    for original, fixed in SPELLING_FIXES.items():
        pattern = rf'(?<![ء-ي\u064B-\u0652]){re.escape(original)}(?![ء-ي\u064B-\u0652])'
        t = re.sub(pattern, fixed, t)
    return t

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

def apply_cocktail_mix(input_path, output_path):
    try:
        # تسريع 1.25 + تضخيم 8dB (بدون صفير)
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-af", "atempo=1.25,equalizer=f=250:t=q:w=1.0:g=6,equalizer=f=3000:t=q:w=1.0:g=-4,bass=g=8,alimiter=limit=0.9",
            "-ar", TARGET_SR, "-ac", "1", str(output_path)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except Exception as e:
        print(f"   ⚠️ فشل تطبيق الخلطة: {e}")
        shutil.copy(input_path, output_path)
        return False

def fetch_ayah_audio(s, a, out_path):
    filename = f"{s:03d}{a:03d}.mp3"
    url = f"https://everyayah.com/data/Abdullah_Basfar_192kbps/{filename}"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 200 and len(r.content) > 10000:
            temp_mp3 = out_path.replace(".wav", "_temp.mp3")
            Path(temp_mp3).write_bytes(r.content)
            apply_cocktail_mix(temp_mp3, out_path)
            Path(temp_mp3).unlink(missing_ok=True)
            return True
    except Exception as e: pass
    return False

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

    # تتبع ما إذا كنا قد أخذنا السرد والتلاوة في وضع الاختبار
    taken_narration = False
    taken_quran = False

    for idx, seg in enumerate(segments_list):
        stype = seg.get("type")
        is_ending_segment = (total_segs - idx) <= 3
        
        # ═══ منطق وضع الاختبار الذكي ═══
        if test_mode:
            if stype == "narration" and not taken_narration:
                taken_narration = True
            elif stype == "quran" and not taken_quran:
                taken_quran = True
            elif not is_ending_segment:
                continue # تخطي أي مقطع آخر
            
        text, display = "", ""
        
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = sanitize_for_tts(seg["text"])
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
            
            if stype == "quran":
                success = fetch_ayah_audio(s, ayahs[0], str(raw_wav))
                if success: print(f"   ✅ تلاوة بشرية (سريعة ومضخمة)", flush=True)
            
            if not success:
                try:
                    chunk_fixed = apply_spelling_fixes(chunk)
                    gemini_tts_with_pool(chunk_fixed, str(temp_mp3), voice="Charon")
                    if temp_mp3.exists() and temp_mp3.stat().st_size > 3000:
                        subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                        success = True
                        print(f"   ✅ Gemini", flush=True)
                except Exception: pass

            if not success:
                print(f"   🚨 Edge TTS...", flush=True)
                chunk_fixed = apply_spelling_fixes(chunk)
                try:
                    edge_generate(chunk_fixed, str(temp_mp3))
                    if temp_mp3.exists() and temp_mp3.stat().st_size > 2000:
                        subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                        success = True
                except Exception: pass

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
