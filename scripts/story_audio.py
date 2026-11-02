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
    print("✅ tilavet-phonemizer loaded")
except Exception as e:
    print(f"❌ Failed to load tilavet-phonemizer: {e}")
    TilavetPhonemizer = None

TARGET_SR = "24000"
SILENCE_DUR = 0.4
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"

# ============================================================
# 1. دوال الصدى والتحويل الصوتي
# ============================================================
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

def phonemes_to_arabic_script(phoneme_text: str) -> str:
    if not phoneme_text or not phoneme_text.strip(): return ""
    tokens = phoneme_text.strip().split(); arabic_chars = []; i = 0
    CONS_MAP = {"'": "ء", "2": "ء", "b": "ب", "t": "ت", "th": "ث", "j": "ج", "H": "ح", "kh": "خ", "d": "د", "dh": "ذ", "r": "ر", "z": "ز", "s": "س", "sh": "ش", "S": "ص", "D": "ض", "T": "ط", "Z": "ظ", "3": "ع", "gh": "غ", "f": "ف", "q": "ق", "k": "ك", "l": "ل", "L": "ل", "m": "م", "n": "ن", "h": "ه", "w": "و", "y": "ي"}
    SHORT_VOWELS = {"a": "َ", "i": "ِ", "u": "ُ"}; LONG_VOWELS = {"aa": "َا", "ii": "ِي", "uu": "ُو", "aa6": "َا", "ii6": "ِي", "uu6": "ُو"}
    while i < len(tokens):
        token = tokens[i]
        if token in LONG_VOWELS: arabic_chars.append(LONG_VOWELS[token]); i += 1; continue
        if token in CONS_MAP:
            char = CONS_MAP[token]
            if char == "ء":
                next_token = tokens[i+1] if i+1 < len(tokens) else ""
                if next_token in ("i", "ii"): char = "ئ"
                elif next_token in ("u", "uu"): char = "ؤ"
                else: char = "ء"
            arabic_chars.append(char)
            if i + 1 < len(tokens) and tokens[i + 1] == token:
                arabic_chars.append("ّ"); i += 2
                if i < len(tokens):
                    if tokens[i] in SHORT_VOWELS: arabic_chars.append(SHORT_VOWELS[tokens[i]]); i += 1
                    elif tokens[i] in LONG_VOWELS: arabic_chars.append(LONG_VOWELS[tokens[i]]); i += 1
                continue
            if i + 1 < len(tokens) and tokens[i + 1] in SHORT_VOWELS: arabic_chars.append(SHORT_VOWELS[tokens[i + 1]]); i += 2; continue
            if i + 1 < len(tokens) and tokens[i + 1] in LONG_VOWELS: arabic_chars.append(LONG_VOWELS[tokens[i + 1]]); i += 2; continue
            arabic_chars.append("ْ"); i += 1; continue
        i += 1
    result = "".join(arabic_chars)
    result = re.sub(r'ُوْ?\b', 'ُوا', result); result = re.sub(r'ُوَ\b', 'ُوا', result)
    result = re.sub(r'تَ\b', 'ةَ', result); result = re.sub(r'تُ\b', 'ةُ', result); result = re.sub(r'تِ\b', 'ةِ', result)
    result = re.sub(r'َ(n|ن)\b', 'ً', result); result = re.sub(r'ُ(n|ن)\b', 'ٌ', result); result = re.sub(r'ِ(n|ن)\b', 'ٍ', result)
    if result.startswith("َ"): result = "ا" + result[1:]
    elif result.startswith("ِ"): result = "ا" + result[1:]
    elif result.startswith("ُ"): result = "ا" + result[1:]
    result = result.replace("ءَل", "ال").replace("أَل", "ال")
    result = result.replace("ٌْ", "ٌ").replace("ًْ", "ً").replace("ٍْ", "ٍ").replace("ّْ", "ّ")
    return result

_tilavet_pm = None
def get_tilavet_phonemizer():
    global _tilavet_pm
    if _tilavet_pm is None and TilavetPhonemizer is not None:
        try: _tilavet_pm = TilavetPhonemizer()
        except Exception as e: pass
    return _tilavet_pm

def generate_quran_audio_gemini(uthmani_text: str, out_mp3: str) -> bool:
    try:
        pm = get_tilavet_phonemizer()
        if not pm: return False
        words = uthmani_text.split(); phonetic_words = []
        for word in words:
            result = pm.phonemize(word)
            phoneme_text = result.text if hasattr(result, 'text') else str(result)
            if phoneme_text:
                arabic_word = phonemes_to_arabic_script(phoneme_text)
                if arabic_word: phonetic_words.append(arabic_word)
        arabic_phonetic = " ".join(phonetic_words)
        if not arabic_phonetic or len(arabic_phonetic) < 5: return False
        print(f"   🔄 النص الصوتي: {arabic_phonetic[:80]}...")
        style_prompt = "أنت قارئ قرآن خاشع. اقرأ هذا النص القرآني بصوت دافئ، حنون، وخاشع، يملؤه السكينة والطمأنينة والهيبة. اقرأ ببطء وتأنٍ، مع إطالة المدود والوقوف عند الوقفات، بنبرة هادئة جداً وتلامس شغاف القلب."
        gemini_tts_with_pool(arabic_phonetic, out_mp3, voice="Charon", style=style_prompt)
        return Path(out_mp3).exists() and Path(out_mp3).stat().st_size > 3000
    except Exception as e:
        return False

# ============================================================
# 2. دوال مساعدة وتشغيل
# ============================================================
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
        if r.status_code == 200: return r.json()["data"]["text"]
    except Exception as e: pass
    return ""

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
    scene_counts = {}
    
    for idx, seg in enumerate(segments_list):
        stype = seg.get("type")
        is_ending_segment = (total_segs - idx) <= 3

        if test_mode:
            if stype == "narration" and not taken_narration: taken_narration = True
            elif stype == "quran" and not taken_quran: taken_quran = True
            elif not is_ending_segment: continue

        text, display = "", ""
        scene = seg.get("visual", "calm").lower()
        
        if stype not in ("quran", "hadith", "fiqh"):
            scene_counts[scene] = scene_counts.get(scene, 0) + 1

        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"): text = sanitize_for_tts(seg["text"])
        elif stype == "quran":
            if "surah" in seg:
                s, ayahs = seg["surah"], seg["ayah_numbers"]
                text = " ".join(filter(None, (fetch_ayah_uthmani(s, a) for a in ayahs)))
                display = f"{seg.get('surah_name', '')}: {ayahs[0]}"
            else:
                text = seg.get("text", ""); display = seg.get("display", "")
        elif stype in ("hadith", "fiqh", "malak"):
            text = sanitize_for_tts(seg.get("text", "")); display = seg.get("display", "")

        if not text: continue

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
            
            if stype == "quran":
                if generate_quran_audio_gemini(chunk, str(temp_mp3)):
                    subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                    reverb_wav = work / f"reverb_{idx}_{c_idx}.wav"
                    if apply_mosque_reverb(str(raw_wav), str(reverb_wav)): shutil.copy(reverb_wav, raw_wav)
                    print(f"   ✅ Gemini TTS + صدى (تجويد)", flush=True)
            elif stype in ("hadith", "fiqh"):
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

    # دمج الصوت البشري
    voice_concat = work / "voice_concat.txt"
    voice_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in voice_wavs), encoding="utf-8")
    voice_merged = work / "voice_merged.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(voice_concat), "-c", "copy", str(voice_merged)], check=True, capture_output=True)
    
    total_dur = get_dur(voice_merged)
    
    # اختيار موسيقى الحلقة الرئيسية
    is_malak_episode = any(seg.get("type") == "malak" for seg in segments_list)
    main_music = None
    if is_malak_episode:
        main_music = MUSIC_DIR / "romantic.mp3"
    else:
        main_scene = max(scene_counts, key=scene_counts.get) if scene_counts else "calm"
        ya_taybah = AUDIO_DIR / "ya_taybah.mp3"
        if main_scene == "calm" and ya_taybah.exists(): main_music = ya_taybah
        else:
            scene_music_map = {"battle": "battle.mp3", "sad": "sad.mp3", "conspiracy": "conspiracy.mp3", "calm": "calm.mp3", "night": "calm.mp3"}
            main_music = MUSIC_DIR / scene_music_map.get(main_scene, "calm.mp3")
            if not main_music.exists(): main_music = MUSIC_DIR / "calm.mp3"

    # الدمج النهائي: الصوت البشري + موسيقى مستمرة
    final_out = work / "final_with_music.mp3"
    
    if main_music and main_music.exists():
        print(f"🎬 دمج موسيقى الحلقة: {main_music.name} (بنسبة 35%)", flush=True)
        filters = (
            f"[1:a]volume=0.35,afade=t=in:st=0:d=3,afade=t=out:st={total_dur-3}:d=3[music];"
            "[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[out]"
        )
        subprocess.run([
            "ffmpeg", "-y", "-i", str(voice_merged), "-stream_loop", "-1", "-i", str(main_music),
            "-filter_complex", filters, "-map", "[out]", "-t", str(total_dur),
            "-c:a", "libmp3lame", "-b:a", "192k", str(final_out)
        ], check=True, capture_output=True, timeout=180)
    else:
        print("⚠️ لا توجد موسيقى متاحة، سيتم استخدام الصوت البشري فقط.", flush=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(voice_merged), "-c:a", "libmp3lame", "-b:a", "192k", str(final_out)], check=True, capture_output=True)

    # حفظ الملف النهائي
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(final_out, out_mp3)
    
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
