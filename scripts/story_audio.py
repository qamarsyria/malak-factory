"""
story_audio.py — المرحلة 4: الصوت
يقرأ generated/{story}-{ep}.json ويُنتج:
  - _yt.mp3:     سرد Gemini + آيات الحصري (بدون مؤثرات، RVC يُطبَّق لاحقاً)
  - social.mp3:  سرد Gemini + آيات قارئ عشوائي (مع stealth filter + reverb)
  - _yt_meta.json + social_meta.json (تفاصيل كل مقطع، تشمل اسم القارئ)

السرد Gemini يُولَّد مرة واحدة ويُستخدم في النسختين (توفير مفاتيح).
smart_diacritize يبقى للأدق، مع prompt صارم + فحص عدد الكلمات.

الاستخدام:
    python story_audio.py <episode.json> <yt.mp3> <social.mp3>
"""

import os, sys, json, subprocess, tempfile, shutil, re, time, hashlib, asyncio, random
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from utilities import cleanup_dir

# ═══════════════════════════════════════════════════════════
# محرك TTS
# ═══════════════════════════════════════════════════════════
TTS_ENGINE = os.environ.get("TTS_ENGINE", "1").strip()

if TTS_ENGINE == "1":
    print("🔊 [TTS] المحرك: Gemini", flush=True)
    from gemini_tts_pool import gemini_tts_with_pool as _engine_generate

    def generate_audio(text, out_path):
        try:
            _engine_generate(text, out_path)
            return True
        except Exception as e:
            print(f"   ⚠️ Gemini فشل: {str(e)[:100]}", flush=True)
            return False
else:
    print("🔊 [TTS] المحرك: Edge", flush=True)
    import edge_tts

    def generate_audio(text, out_path):
        try:
            async def _run():
                comm = edge_tts.Communicate(text, "ar-SY-LaithNeural", rate="-5%")
                await comm.save(out_path)
            asyncio.run(_run())
            return Path(out_path).exists() and Path(out_path).stat().st_size > 1000
        except Exception as e:
            print(f"   ⚠️ Edge فشل: {str(e)[:100]}", flush=True)
            return False

# ═══════════════════════════════════════════════════════════
# القراء
# ═══════════════════════════════════════════════════════════
RECITER_YT = {
    "id": "Husary_128kbps",
    "name": "الشيخ محمود خليل الحصري"
}

RECITER_LIST_SOCIAL = [
    {"id": "Alafasy_128kbps",                "name": "مشاري راشد العفاسي"},
    {"id": "Abdurrahmaan_As-Sudais_192kbps", "name": "عبد الرحمن السديس"},
    {"id": "Ghamadi_40kbps",                 "name": "سعد الغامدي"},
    {"id": "Yasser_Ad-Dussary_128kbps",      "name": "ياسر الدوسري"},
    {"id": "Nasser_Alqatami_128kbps",        "name": "ناصر القطامي"},
]

QURAN_BASE_URL = "https://everyayah.com/data"
TARGET_SR = "24000"
SILENCE_DUR = 0.15
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"

# ═══════════════════════════════════════════════════════════
# الأرقام العربية
# ═══════════════════════════════════════════════════════════
_ARABIC_UNITS = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_ARABIC_TEENS = ["عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
                 "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
_ARABIC_TENS = ["", "عشرة", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_ARABIC_HUNDREDS = ["", "مئة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]


def _num_to_arabic(n: int) -> str:
    if n == 0:
        return "صفر"
    if n < 10:
        return _ARABIC_UNITS[n]
    if n < 20:
        return _ARABIC_TEENS[n - 10]
    if n < 100:
        tens, units = divmod(n, 10)
        return _ARABIC_TENS[tens] if units == 0 else f"{_ARABIC_UNITS[units]} و{_ARABIC_TENS[tens]}"
    if n < 1000:
        h, rem = divmod(n, 100)
        prefix = _ARABIC_HUNDREDS[h]
        return prefix if rem == 0 else f"{prefix} و{_num_to_arabic(rem)}"
    th, rem = divmod(n, 1000)
    prefix = f"{_num_to_arabic(th)} ألف" if th > 1 else "ألف"
    return prefix if rem == 0 else f"{prefix} و{_num_to_arabic(rem)}"


def arabize_numbers(text: str) -> str:
    def _replace(m):
        return _num_to_arabic(int(m.group(0)))
    return re.sub(r'\b(\d{1,4})\b', _replace, text)


# ═══════════════════════════════════════════════════════════
# التشكيل الذكي (مع prompt صارم + word count)
# ═══════════════════════════════════════════════════════════
DIACRITIZE_PROMPT = """أضف التشكيل الكامل (الحركات) للنص العربي التالي بناءً على سياق الجملة.

قواعد صارمة:
1. لا تحذف أي مسافة بين الكلمات.
2. لا تدمج كلمتين أبداً.
3. لا تغير أي حرف.
4. أعد النص المشكول فقط، بلا أي شرح.

النص:
{text}"""


def smart_diacritize(text: str) -> str:
    if not text or not text.strip():
        return text
    if not re.search(r'[\u0600-\u06FF]', text):
        return text

    try:
        from gemini_tts_pool import get_pool
        pool = get_pool()
        key = pool.get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
        body = {
            "contents": [{"parts": [{"text": DIACRITIZE_PROMPT.format(text=text)}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2000}
        }
        r = requests.post(url, json=body, timeout=60)
        if r.status_code == 200:
            parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            if parts:
                diacritized = parts[0].get("text", "").strip()
                # فحص أمان: عدد الكلمات يجب أن يبقى نفسه
                original_words = len(text.split())
                new_words = len(diacritized.split())
                if new_words != original_words:
                    print(f"   ⚠️ التشكيل غيّر عدد الكلمات ({original_words} → {new_words})، استخدام الأصلي", flush=True)
                    return text
                if len(diacritized) > 5:
                    return diacritized
    except Exception as e:
        print(f"   ⚠️ التشكيل فشل: {str(e)[:100]}", flush=True)
    return text


# ═══════════════════════════════════════════════════════════
# تنظيف النص
# ═══════════════════════════════════════════════════════════
def sanitize_for_tts(t: str) -> str:
    if not t:
        return ""
    t = t.replace("ﷺ", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    t = t.replace("صلى الله عليه وسلم", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    t = arabize_numbers(t)
    t = re.sub(r'\[.*?\]', '', t, flags=re.DOTALL)
    t = re.sub(r'\(.*?\)', '', t, flags=re.DOTALL)
    t = re.sub(r'﴿.*?﴾', '', t, flags=re.DOTALL)
    t = re.sub(r'[^\u0600-\u06FF\s،؟۔!.]', '', t)
    t = re.sub(r"[ۖۗۘۙۚۛ۞۝]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ═══════════════════════════════════════════════════════════
# تحميل آية من everyayah
# ═══════════════════════════════════════════════════════════
def download_human_ayah(surah: int, ayah: int, reciter_id: str, out_path: Path) -> bool:
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE_URL}/{reciter_id}/{filename}"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and len(r.content) > 1000:
            out_path.write_bytes(r.content)
            return True
    except Exception as e:
        print(f"   ⚠️ فشل تحميل {filename}: {str(e)[:80]}", flush=True)
    return False


# ═══════════════════════════════════════════════════════════
# معالجة الصوت
# ═══════════════════════════════════════════════════════════
def get_dur(p) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except:
        return 0.0


def trim_and_fade(input_wav: str, output_wav: str, speed_up: bool = False, is_last: bool = False):
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_wav],
            capture_output=True, text=True
        )
        dur = float(probe.stdout.strip() or 0)
        if dur > 0.5:
            fade_filter = f",afade=t=out:st={max(0, dur - 0.2)}:d=0.2" if is_last else ""
            tempo_filter = "atempo=1.15," if speed_up else ""
            subprocess.run([
                "ffmpeg", "-y", "-i", input_wav,
                "-af", f"{tempo_filter}aresample=async=1:first_pts=0{fade_filter}",
                "-ar", TARGET_SR, "-ac", "1", output_wav
            ], check=True, capture_output=True, timeout=60)
            return True
        shutil.copy(input_wav, output_wav)
        return True
    except:
        shutil.copy(input_wav, output_wav)
        return False


def apply_stealth_filter(input_wav: str, output_wav: str):
    """مؤثر Social فقط — لتجنب copyright claims"""
    try:
        filters = (
            "atempo=1.05,"
            "equalizer=f=200:t=q:w=1.0:g=2,"
            "aecho=0.8:0.9:20:0.1,"
            "highpass=f=100,lowpass=f=12000"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_wav, "-af", filters,
             "-ar", TARGET_SR, "-ac", "1", output_wav],
            check=True, capture_output=True, timeout=60
        )
        return True
    except:
        shutil.copy(input_wav, output_wav)
        return False


def apply_mosque_reverb(input_wav: str, output_wav: str):
    """صدى خفيف — Social فقط (YT يأخذ الصدى من RVC)"""
    try:
        filters = (
            "atempo=0.92,"
            "equalizer=f=120:t=q:w=1.0:g=4,"
            "equalizer=f=4000:t=q:w=1.0:g=-4,"
            "aecho=0.8:0.7:60|180|320:0.15|0.10|0.05,"
            "acompressor=threshold=0.1:ratio=3:attack=20:release=200"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_wav, "-af", filters,
             "-ar", TARGET_SR, "-ac", "1", output_wav],
            check=True, capture_output=True, timeout=60
        )
        return True
    except:
        shutil.copy(input_wav, output_wav)
        return False


# ═══════════════════════════════════════════════════════════
# تقسيم النص
# ═══════════════════════════════════════════════════════════
def split_text(text: str, max_chars: int = 500):
    sentences = re.split(r'(?<=[.!؟])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) < max_chars:
            current += " " + s
        else:
            if current:
                chunks.append(current.strip())
            current = s
    if current:
        chunks.append(current.strip())
    return chunks


# ═══════════════════════════════════════════════════════════
# بناء الحلقة الصوتية
# ═══════════════════════════════════════════════════════════
def build_episode_audio(json_path: str, out_yt_mp3: str, out_social_mp3: str):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    ep_name = Path(json_path).stem
    ep_cache = CACHE_DIR / ep_name
    ep_cache.mkdir(parents=True, exist_ok=True)

    # اختيار قارئ عشوائي للـ social
    reciter_social = random.choice(RECITER_LIST_SOCIAL)
    print(f"🎙️ قارئ Social: {reciter_social['name']}", flush=True)

    # ملف الصمت
    silence_path = work / "silence.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono",
         "-t", str(SILENCE_DUR), "-c:a", "pcm_s16le", str(silence_path)],
        check=True, capture_output=True
    )

    segments_list = ep.get("segments", [])
    yt_wavs, social_wavs = [], []
    yt_meta, social_meta = [], []

    for idx, seg in enumerate(segments_list):
        stype = seg.get("type")

        # ═══ السرد (Gemini TTS — مرة واحدة للنسختين) ═══
        if stype in ("narration", "teaser", "cta", "outro", "intro", "summary"):
            text = sanitize_for_tts(seg.get("text", ""))
            if not text:
                continue

            text = smart_diacritize(text)
            chunks = split_text(text)
            chunk_timings = []

            for c_idx, chunk in enumerate(chunks):
                if not chunk.strip() or not re.search(r'[\u0600-\u06FF]', chunk):
                    continue

                text_hash = hashlib.md5(chunk.encode("utf-8")).hexdigest()[:8]
                is_last = (c_idx == len(chunks) - 1)
                cached_wav = ep_cache / f"seg_{idx}_{c_idx}_{text_hash}.wav"

                if not cached_wav.exists() or cached_wav.stat().st_size < 1000:
                    temp_mp3 = work / f"temp_{idx}_{c_idx}.mp3"
                    if generate_audio(chunk, str(temp_mp3)):
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", str(temp_mp3),
                             "-ar", TARGET_SR, "-ac", "1", str(cached_wav)],
                            check=True, capture_output=True
                        )
                    time.sleep(1)

                if cached_wav.exists():
                    faded = work / f"faded_{idx}_{c_idx}.wav"
                    trim_and_fade(str(cached_wav), str(faded), speed_up=False, is_last=is_last)
                    yt_wavs.extend([faded, silence_path])
                    social_wavs.extend([faded, silence_path])
                    chunk_timings.append({
                        "text": chunk,
                        "duration": get_dur(faded) + SILENCE_DUR
                    })

            if chunk_timings:
                meta_entry = {
                    "index": idx,
                    "type": stype,
                    "display": "",
                    "reciter": "",
                    "chunks": chunk_timings
                }
                yt_meta.append(meta_entry)
                social_meta.append(meta_entry)

        # ═══ الآيات ═══
        elif stype == "quran":
            s = seg.get("surah", 0)
            ayahs = seg.get("ayah_numbers", [])
            if not ayahs:
                continue
            display = f"{seg.get('surah_name', '')}: {ayahs[0] if ayahs else ''}"

            # ─── YT: الحصري (بدون مؤثرات، RVC يُطبَّق لاحقاً) ───
            yt_chunks = []
            for a in ayahs:
                cached_a = ep_cache / f"yt_husary_{s}_{a}.mp3"
                if not cached_a.exists():
                    if not download_human_ayah(s, a, RECITER_YT["id"], cached_a):
                        print(f"   ⚠️ فشل تحميل YT {s}:{a}، تخطي", flush=True)
                        continue

                trimmed = ep_cache / f"yt_husary_{s}_{a}_trimmed.wav"
                trim_and_fade(str(cached_a), str(trimmed), speed_up=False, is_last=True)

                yt_wavs.extend([trimmed, silence_path])
                yt_chunks.append({
                    "text": f"{display} - آية {a}",
                    "duration": get_dur(trimmed) + SILENCE_DUR
                })

            if yt_chunks:
                yt_meta.append({
                    "index": idx,
                    "type": "quran",
                    "surah": s,
                    "surah_name": seg.get("surah_name", ""),
                    "ayah_numbers": ayahs,
                    "display": display,
                    "reciter": RECITER_YT["name"],
                    "chunks": yt_chunks
                })

            # ─── Social: قارئ عشوائي + stealth + reverb ───
            social_chunks = []
            for a in ayahs:
                cached_a = ep_cache / f"soc_{reciter_social['id']}_{s}_{a}.mp3"
                if not cached_a.exists():
                    if not download_human_ayah(s, a, reciter_social["id"], cached_a):
                        print(f"   ⚠️ فشل تحميل Social {s}:{a}", flush=True)
                        continue

                trimmed = ep_cache / f"soc_{reciter_social['id']}_{s}_{a}_trimmed.wav"
                trim_and_fade(str(cached_a), str(trimmed), speed_up=True, is_last=True)

                stealth = ep_cache / f"soc_stealth_{s}_{a}.wav"
                reverb = ep_cache / f"soc_reverb_{s}_{a}.wav"
                if not reverb.exists():
                    apply_stealth_filter(str(trimmed), str(stealth))
                    apply_mosque_reverb(str(stealth), str(reverb))

                if reverb.exists():
                    social_wavs.extend([reverb, silence_path])
                    social_chunks.append({
                        "text": f"{display} - آية {a}",
                        "duration": get_dur(reverb) + SILENCE_DUR
                    })

            if social_chunks:
                social_meta.append({
                    "index": idx,
                    "type": "quran",
                    "surah": s,
                    "surah_name": seg.get("surah_name", ""),
                    "ayah_numbers": ayahs,
                    "display": display,
                    "reciter": reciter_social["name"],
                    "chunks": social_chunks
                })

    # ═══ الدمج النهائي — YT ═══
    if yt_wavs:
        yt_concat = work / "yt_concat.txt"
        yt_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in yt_wavs), encoding="utf-8")
        yt_merged = work / "yt_merged.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(yt_concat), "-c", "copy", str(yt_merged)],
            check=True, capture_output=True
        )
        Path(out_yt_mp3).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(yt_merged),
             "-c:a", "libmp3lame", "-b:a", "192k", str(out_yt_mp3)],
            check=True, capture_output=True
        )
        Path(str(out_yt_mp3).replace(".mp3", "_meta.json")).write_text(
            json.dumps({
                "episode": ep.get("episode_number", 1),
                "title": ep.get("title", ""),
                "story_id": ep.get("story_id", ""),
                "total_duration": get_dur(out_yt_mp3),
                "segments": yt_meta
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"✅ (YT) {out_yt_mp3} | {get_dur(out_yt_mp3)/60:.1f} دقيقة | قارئ: {RECITER_YT['name']}")

    # ═══ الدمج النهائي — Social ═══
    if social_wavs:
        soc_concat = work / "soc_concat.txt"
        soc_concat.write_text("\n".join(f"file '{p.resolve()}'" for p in social_wavs), encoding="utf-8")
        soc_merged = work / "soc_merged.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(soc_concat), "-c", "copy", str(soc_merged)],
            check=True, capture_output=True
        )
        Path(out_social_mp3).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(soc_merged),
             "-c:a", "libmp3lame", "-b:a", "192k", str(out_social_mp3)],
            check=True, capture_output=True
        )
        Path(str(out_social_mp3).replace(".mp3", "_meta.json")).write_text(
            json.dumps({
                "episode": ep.get("episode_number", 1),
                "title": ep.get("title", ""),
                "story_id": ep.get("story_id", ""),
                "total_duration": get_dur(out_social_mp3),
                "segments": social_meta
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"✅ (Social) {out_social_mp3} | {get_dur(out_social_mp3)/60:.1f} دقيقة | قارئ: {reciter_social['name']}")

    cleanup_dir(work)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("الاستخدام: python story_audio.py <episode.json> <yt.mp3> <social.mp3>")
        sys.exit(1)

    build_episode_audio(sys.argv[1], sys.argv[2], sys.argv[3])
