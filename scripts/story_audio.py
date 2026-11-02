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
    from tilavet_phonemizer import Phonemizer as TilavetPhonemizer
    print("✅ tilavet-phonemizer loaded")
except Exception as e:
    print(f"❌ Failed to load tilavet-phonemizer: {e}")
    TilavetPhonemizer = None

TARGET_SR = "24000"
SILENCE_DUR = 0.4

# ============================================================
# 1. دالة تطبيق صدى المسجد + الدفء والخشوع
# ============================================================
def apply_mosque_reverb(input_wav: str, output_wav: str) -> bool:
    """يضيف صدى مسجد + يبطئ الإيقاع + يزيد الدفء (لتوليد الخشوع)."""
    try:
        filters = (
            # 1. إبطاء السرعة بنسبة 8% (سر الخشوع)
            "atempo=0.92,"
            # 2. تعميق الصوت وزيادة الدفء (ترددات منخفضة)
            "equalizer=f=120:t=q:w=1.0:g=4,"
            # 3. تقليل الحدة والجفاف (ترددات عالية)
            "equalizer=f=4000:t=q:w=1.0:g=-4,"
            # 4. صدى المسجد (مخفض 50%)
            "aecho=0.8:0.7:60|180|320:0.15|0.10|0.05,"
            # 5. ضاغط صوت لمنع التشويش
            "acompressor=threshold=0.1:ratio=3:attack=20:release=200" 
        )
        
        subprocess.run([
            "ffmpeg", "-y", "-i", input_wav,
            "-af", filters,
            "-ar", TARGET_SR, "-ac", "1",
            output_wav
        ], check=True, capture_output=True, timeout=60)
        
        return Path(output_wav).exists() and Path(output_wav).stat().st_size > 2000
    except Exception as e:
        print(f"   ⚠️ فشل تطبيق الصدى: {e}")
        try: shutil.copy(input_wav, output_wav)
        except: pass
        return False

# ============================================================
# 2. دوال التجويد والمسافات
# ============================================================
def phonemes_to_arabic_script(phoneme_text: str) -> str:
    """يحول سلسلة فونيمات كلمة واحدة إلى نص عربي متصل."""
    if not phoneme_text or not phoneme_text.strip():
        return ""

    tokens = phoneme_text.strip().split()
    arabic_chars = []
    i = 0

    CONS_MAP = {
        "'": "ء", "2": "ء", "b": "ب", "t": "ت", "th": "ث", "j": "ج", "H": "ح",
        "kh": "خ", "d": "د", "dh": "ذ", "r": "ر", "z": "ز", "s": "س",
        "sh": "ش", "S": "ص", "D": "ض", "T": "ط", "Z": "ظ", "3": "ع",
        "gh": "غ", "f": "ف", "q": "ق", "k": "ك", "l": "ل", "L": "ل",
        "m": "م", "n": "ن", "h": "ه", "w": "و", "y": "ي",
    }
    SHORT_VOWELS = {"a": "َ", "i": "ِ", "u": "ُ"}
    LONG_VOWELS = {"aa": "َا", "ii": "ِي", "uu": "ُو", "aa6": "َا", "ii6": "ِي", "uu6": "ُو"}

    while i < len(tokens):
        token = tokens[i]

        if token in LONG_VOWELS:
            arabic_chars.append(LONG_VOWELS[token])
            i += 1
            continue

        if token in CONS_MAP:
            char = CONS_MAP[token]
            arabic_chars.append(char)

            # التحقق من الشدة
            if i + 1 < len(tokens) and tokens[i + 1] == token:
                arabic_chars.append("ّ")
                i += 2
                if i < len(tokens):
                    if tokens[i] in SHORT_VOWELS:
                        arabic_chars.append(SHORT_VOWELS[tokens[i]])
                        i += 1
                    elif tokens[i] in LONG_VOWELS:
                        arabic_chars.append(LONG_VOWELS[tokens[i]])
                        i += 1
                continue

            # التحقق من الحركة القصيرة
            if i + 1 < len(tokens) and tokens[i + 1] in SHORT_VOWELS:
                arabic_chars.append(SHORT_VOWELS[tokens[i + 1]])
                i += 2
                continue
                
            # التحقق من المد الطويل
            if i + 1 < len(tokens) and tokens[i + 1] in LONG_VOWELS:
                arabic_chars.append(LONG_VOWELS[tokens[i + 1]])
                i += 2
                continue

            arabic_chars.append("ْ")
            i += 1
            continue

        if token in SHORT_VOWELS:
            arabic_chars.append(SHORT_VOWELS[token])
            i += 1
            continue
        if token in LONG_VOWELS:
            arabic_chars.append(LONG_VOWELS[token])
            i += 1
            continue

        i += 1

    result = "".join(arabic_chars)
    
    # 🌟 إصلاح همزة الوصل (ءَل -> ال) لكي يقرأها Gemini بسلاسة
    if result.startswith("ءَل") or result.startswith("ءَ"):
        result = "ا" + result[2:] if result.startswith("ءَل") else "ا" + result[1:]

    return result


_tilavet_pm = None
def get_tilavet_phonemizer():
    global _tilavet_pm
    if _tilavet_pm is None and TilavetPhonemizer is not None:
        try:
            _tilavet_pm = TilavetPhonemizer()
            print("✅ Tilavet Phonemizer initialized")
        except Exception as e:
            print(f"⚠️ Failed to initialize Tilavet Phonemizer: {e}")
    return _tilavet_pm


def generate_quran_audio_gemini(uthmani_text: str, out_mp3: str) -> bool:
    """يولد صوت الآية كلمة كلمة لضمان فصلها بمسافات واضحة، مع توجيه عاطفي دافئ."""
    try:
        pm = get_tilavet_phonemizer()
        if not pm:
            return False

        words = uthmani_text.split()
        phonetic_words = []
        
        for word in words:
            result = pm.phonemize(word)
            phoneme_text = result.text if hasattr(result, 'text') else str(result)
            if not phoneme_text or not phoneme_text.strip():
                continue
                
            arabic_word = phonemes_to_arabic_script(phoneme_text)
            if arabic_word:
                phonetic_words.append(arabic_word)
                
        arabic_phonetic = " ".join(phonetic_words)
        
        if not arabic_phonetic or len(arabic_phonetic) < 5:
            return False

        print(f"   🔄 النص الصوتي التجويدي: {arabic_phonetic[:80]}...")
        
        # 🌟 التوجيه العاطفي الجديد (بدون حزن)
        style_prompt = "أنت قارئ قرآن خاشع. اقرأ هذا النص القرآني بصوت دافئ، حنون، وخاشع، يملؤه السكينة والطمأنينة والهيبة. اقرأ ببطء وتأنٍ، مع إطالة المدود والوقوف عند الوقفات، بنبرة هادئة جداً وتلامس شغاف القلب."
        
        gemini_tts_with_pool(arabic_phonetic, out_mp3, voice="Charon", style=style_prompt)

        return Path(out_mp3).exists() and Path(out_mp3).stat().st_size > 3000
    except Exception as e:
        print(f"   ⚠️ Gemini TTS فشل: {e}")
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

            # ═══ 1. الآيات: تجويد + Gemini TTS + صدى/دفء ═══
            if stype == "quran":
                if generate_quran_audio_gemini(chunk, str(temp_mp3)):
                    subprocess.run(["ffmpeg", "-y", "-i", str(temp_mp3), "-ar", TARGET_SR, "-ac", "1", str(raw_wav)], check=True, capture_output=True)
                    
                    # ✨ تطبيق صدى المسجد والدفء هنا ✨
                    reverb_wav = work / f"reverb_{idx}_{c_idx}.wav"
                    if apply_mosque_reverb(str(raw_wav), str(reverb_wav)):
                        shutil.copy(reverb_wav, raw_wav)
                        print(f"   ✅ Gemini TTS + صدى/دفء (تجويد)", flush=True)
                    else:
                        print(f"   ✅ Gemini TTS (تجويد بدون صدى)", flush=True)
                        
                    success = True
                else:
                    print(f"   ⚠️ فشل التوليد التجويدي، سيتم استخدام Gemini TTS العادي...", flush=True)

            # ═══ 2. السرد أو الاحتياطي: Gemini TTS العادي ═══
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
