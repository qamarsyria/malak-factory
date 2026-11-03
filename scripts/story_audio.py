import os, sys, json, subprocess, tempfile, shutil, re, time
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir, notify_telegram
from gemini_tts_pool import gemini_tts_with_pool
from sfx_manager import ensure_assets, MUSIC_DIR, SFX_DIR, AUDIO_DIR

TARGET_SR = "24000"
SILENCE_DUR = 0.4
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"

# 🌟 الإعدادات الجديدة للصوت البشري (الآيات فقط)
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
        print(f"   ⚠️ فشل تطبيق الصدى: {e}")
        try: shutil.copy(input_wav, output_wav)
        except: pass
        return False

def apply_stealth_filter(input_wav: str, output_wav: str) -> bool:
    try:
        filters = (
            "atempo=1.05,"                 
            "equalizer=f=200:t=q:w=1.0:g=2," 
            "aecho=0.8:0.9:20:0.1,"        
            "highpass=f=100,"              
            "lowpass=f=12000"              
        )
        subprocess.run(["ffmpeg", "-y", "-i", input_wav, "-af", filters, "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
        return Path(output_wav).exists() and Path(output_wav).stat().st_size > 2000
    except Exception as e:
        print(f"   ⚠️ فشل تطبيق الفلتر السري: {e}")
        shutil.copy(input_wav, output_wav)
        return False

def download_human_ayah(surah, ayah, out_path):
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE_URL}/{RECITER}/{filename}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            with open(out_path, "wb") as f:
                f.write(r.content)
            return True
    except Exception as e:
        print(f"   ⚠️ فشل تحميل الآية البشرية: {e}")
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

def mix_audio_layers(voice_wav: str, output_wav: str, music_file: str, sfx_file: str) -> bool:
    try:
        v_dur = get_dur(voice_wav)
        inputs = ["-i", voice_wav]
        filters = []
        idx = 1
        if music_file and Path(music_file).exists():
            inputs.extend(["-stream_loop", "-1", "-i", str(music_file)])
            filters.append(f"[{idx}:a]volume=0.12,afade=t=in:st=0:d=2,afade=t=out:st={v_dur-2}:d=2[music]")
            idx += 1
        else:
            filters.append(f"[0:a]anull[music]")
        if sfx_file and Path(sfx_file).exists():
            inputs.extend(["-stream_loop", "-1", "-i", str(sfx_file)])
            filters.append(f"[{idx}:a]volume=0.35,afade=t=in:st=0:d=1,afade=t=out:st={v_dur-1}:d=1[sfx]")
            filters.append(f"[0:a][music][sfx]amix=inputs=3:duration=first:normalize=0[out]")
        else:
            filters.append(f"[0:a][music]amix=inputs=2:duration=first:normalize=0[out]")
        subprocess.run(["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(filters), "-map", "[out]", "-t", str(v_dur), "-ar", TARGET_SR, "-ac", "1", output_wav], check=True, capture_output=True, timeout=60)
        return True
    except Exception as e:
        print(f"   ⚠️ فشل دمج الموسيقى/المؤثرات: {e}")
        shutil.copy(voice_wav, output_wav)
        return False

def build_episode_audio(json_path, out_mp3):
    ensure_assets() 
    
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    
    ep_name = Path(json_path).stem
    ep_cache = CACHE_DIR / ep_name
    ep_cache.mkdir(parents=True, exist_ok=True)

    silence_path = work / "silence.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono", "-t", str(SILENCE_DUR), "-c:a", "pcm_s16le", str(silence_path)], check=True, capture_output=True)

    segments_list = ep.get("segments", [])
    total_segs = len(segments_list)
    test_mode = os.environ.get("TEST_MODE") == "1"

    print(f"⚙️ وضع الاختبار: {'مفعل' if test_mode else 'معطل'}")

    taken_narration = False; taken_quran = False
    voice_wavs = []
    all_segments_meta = []
    
    for idx, seg in enumerate(segments_list):
        stype = seg.get("type")
        is_ending_segment = (total_segs - idx) <= 3

        if test_mode:
            if stype == "narration" and not taken_narration: taken_narration = True
            elif stype == "quran" and not taken_quran: taken_quran = True
            elif not is_ending_segment: continue

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

        # 🌟 النظام الهجين: الآيات بصوت بشري
        if stype == "quran" and "surah" in seg:
            print(f"🎙️ المقطع {idx+1} [quran] - تحميل صوت بشري...", flush=True)
            s, ayahs = seg["surah"], seg["ayah_numbers"]
            human_ayeh_wavs = []
            
            for a in ayahs:
                cached_ayeh = ep_cache / f"human_{s}_{a}.mp3"
                if not cached_ayeh.exists():
                    if download_human_ayah(s, a, str(cached_ayeh)):
                        stealth_out = ep_cache / f"stealth_{s}_{a}.wav"
                        reverb_out = ep_cache / f"reverb_{s}_{a}.wav"
                        apply_stealth_filter(str(cached_ayeh), str(stealth_out))
                        apply_mosque_reverb(str(stealth_out), str(reverb_out))
                        shutil.copy(reverb_out, cached_ayeh.with_suffix('.wav'))
                
                final_ayeh_wav = cached_ayeh.with_suffix('.wav')
                if final_ayeh_wav.exists():
                    human_ayeh_wavs.append(final_ayeh_wav)
                    voice_wavs.append(final_ayeh_wav); voice_wavs.append(silence_path)
                    
            if human_ayeh_wavs:
                dur = sum(get_dur(w) for w in human_ayeh_wavs) + (SILENCE_DUR * len(human_ayeh_wavs))
                all_segments_meta.append({"index": idx, "type": stype, "display": display, "chunks": [{"text": "آية", "duration": dur}]})
            continue 

        # 🌟 السرد والحديث (Gemini TTS)
        chunks = split_text(text)
        print(f"🎙️ المقطع {idx+1} [{stype}] - {len(chunks)} فقرة...", flush=True)
        chunk_timings = []

        for c_idx, chunk in enumerate(chunks):
            if not chunk.strip(): continue

            cached_wav = ep_cache / f"seg_{idx}_{c_idx}.wav"
            if cached_wav.exists() and cached_wav.stat().st_size > 1000:
                print(f"   ⏭️ [كاش] استخدام المقطع المحفوظ", flush=True)
                voice_wavs.append(cached_wav); voice_wavs.append(silence_path)
                chunk_timings.append({"text": chunk, "duration": get_dur(cached_wav) + SILENCE_DUR})
                continue

            raw_wav = work / f"c_{idx}_{c_idx}.wav"
            temp_mp3 = work / f"temp_{idx}_{c_idx}.mp3"
            
            if stype in ("hadith", "fiqh"):
                style_prompt = "أنت راوي حديث نبوي. اقرأ النص بصوت وقور، خاشع، وبطيء، يملؤه الهيبة والسكينة."
                gemini_tts_with_pool(chunk, str(temp_mp3), voice="Charon", style=style_prompt)
                if temp_mp3.exists() and temp_mp3.stat().st_size > 3000:
                    subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                    reverb_wav = work / f"reverb_{idx}_{c_idx}.wav"
                    if apply_mosque_reverb(str(raw_wav), str(reverb_wav)): shutil.copy(reverb_wav, raw_wav)
                    print(f"   ✅ Gemini TTS + صدى ({stype})", flush=True)
            elif stype == "malak":
                style_prompt = "أنت تهمس بكلمات حب صوفي، دافئة، حنونة، وعذبة. اقرأ النص بنبرة هادئة جداً تلامس الروح."
                gemini_tts_with_pool(chunk, str(temp_mp3), voice="Charon", style=style_prompt)
                if temp_mp3.exists() and temp_mp3.stat().st_size > 3000:
                    subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                    print(f"   ✅ Gemini TTS (همسة)", flush=True)
            else: 
                gemini_tts_with_pool(chunk, str(temp_mp3), voice="Charon")
                if temp_mp3.exists() and temp_mp3.stat().st_size > 3000:
                    subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                    print(f"   ✅ Gemini TTS ({stype})", flush=True)

            if stype in ("quran", "hadith", "fiqh"):
                pass 
            elif stype == "malak":
                music = MUSIC_DIR / "romantic.mp3"
                mixed_wav = work / f"mixed_{idx}_{c_idx}.wav"
                if mix_audio_layers(str(raw_wav), str(mixed_wav), str(music), ""):
                    shutil.copy(mixed_wav, raw_wav)
            else: 
                scene_music_map = {"battle": "battle.mp3", "sad": "sad.mp3", "conspiracy": "conspiracy.mp3", "calm": "calm.mp3", "night": "calm.mp3"}
                music_name = scene_music_map.get(scene, "calm.mp3")
                music = MUSIC_DIR / music_name
                if not music.exists(): music = MUSIC_DIR / "calm.mp3"
                scene_sfx_map = {"battle": "sword.mp3", "sad": "heartbeat.mp3", "conspiracy": "whisper.mp3", "calm": "birds.mp3", "night": "wind.mp3"}
                sfx_name = scene_sfx_map.get(scene)
                sfx = SFX_DIR / sfx_name if sfx_name else ""
                mixed_wav = work / f"mixed_{idx}_{c_idx}.wav"
                if mix_audio_layers(str(raw_wav), str(mixed_wav), str(music), str(sfx)):
                    shutil.copy(mixed_wav, raw_wav)

            time.sleep(5) 
            faded = work / f"faded_{idx}_{c_idx}.wav"
            c_dur = get_dur(raw_wav)
            if c_dur > 0.2:
                subprocess.run(["ffmpeg", "-y", "-i", str(raw_wav), "-af", "afade=t=in:st=0:d=0.05,afade=t=out:st=" + str(c_dur-0.05) + ":d=0.05", str(faded)], check=True, capture_output=True)
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
    Path(meta_path).write_text(json.dumps({
        "episode": ep.get("episode_number", 1), 
        "title": ep.get("title", ""), 
        "story_id": ep.get("story_id", ""), 
        "total_duration": total_dur,
        "segments": all_segments_meta
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {out_mp3} | {total_dur/60:.1f} دقيقة", flush=True)
    cleanup_dir(work)

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    build_episode_audio(sys.argv[1], sys.argv[2])
