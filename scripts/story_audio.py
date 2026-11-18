"""
story_audio.py — المرحلة 4: بناء الصوت (نسخة Social طبيعية بقراء متعددين + نسخة YT بالحصري)
- يحافظ على التشكيل السليم للسرد عبر التحقق الحرفي ليظهر بالتشكيل على الشاشة ويُقرأ بدقة.
- ينوّع القراء داخل الحلقة الواحدة في نسخة Social بصوت طبيعي صافٍ دون تشويه المدود.
- يخزّن surah و ayah و surah_name و reciter داخل كل chunk لمنع انزياح الآيات في الفيديو.
- يدعم: --mode social | yt | both

الاستخدام:
    python story_audio.py <episode.json> <yt.mp3> <social.mp3> [--mode social|yt|both]
"""

import os
import sys
import json
import subprocess
import tempfile
import shutil
import re
import time
import hashlib
import asyncio
import random
import argparse
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from utilities import cleanup_dir
from model_discovery import discover_and_cache
from gemini_tts_pool import get_pool, gemini_tts_with_pool

TTS_ENGINE = os.environ.get("TTS_ENGINE", "1").strip()

if TTS_ENGINE == "1":
    print("🔊 [TTS] المحرك: Gemini", flush=True)

    def generate_audio(text: str, out_path: str) -> bool:
        try:
            gemini_tts_with_pool(text, out_path)
            return True
        except Exception as e:
            print(f"   ⚠️ Gemini TTS فشل: {str(e)[:120]}", flush=True)
            return False
else:
    print("🔊 [TTS] المحرك: Edge", flush=True)
    import edge_tts

    def generate_audio(text: str, out_path: str) -> bool:
        try:
            async def _run():
                comm = edge_tts.Communicate(text, "ar-SA-HamedNeural", rate="-5%")
                await comm.save(out_path)
            asyncio.run(_run())
            return Path(out_path).exists() and Path(out_path).stat().st_size > 1000
        except Exception as e:
            print(f"   ⚠️ Edge فشل: {str(e)[:120]}", flush=True)
            return False


RECITER_YT = {
    "id": "Husary_128kbps",
    "name": "الشيخ محمود خليل الحصري",
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
SILENCE_DUR = 0.20
CACHE_DIR = _REPO_ROOT / "out" / "audio_cache"

_ARABIC_UNITS    = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"]
_ARABIC_TEENS    = ["عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
                    "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
_ARABIC_TENS     = ["", "عشرة", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_ARABIC_HUNDREDS = ["", "مئة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]


def _num_to_arabic(n: int) -> str:
    if n == 0:
        return "صفر"
    if n < 10:
        return _ARABIC_UNITS[n]
    if n < 20:
        return _ARABIC_TEENS[n - 10]
    if n < 100:
        t, u = divmod(n, 10)
        return _ARABIC_TENS[t] if u == 0 else f"{_ARABIC_UNITS[u]} و{_ARABIC_TENS[t]}"
    if n < 1000:
        h, r = divmod(n, 100)
        p = _ARABIC_HUNDREDS[h]
        return p if r == 0 else f"{p} و{_num_to_arabic(r)}"
    if n < 1_000_000:
        th, r = divmod(n, 1000)
        if th == 1:
            p = "ألف"
        elif th == 2:
            p = "ألفان"
        elif 3 <= th <= 10:
            p = f"{_ARABIC_UNITS[th]} آلاف"
        else:
            p = f"{_num_to_arabic(th)} ألف"
        return p if r == 0 else f"{p} و{_num_to_arabic(r)}"
    return str(n)


def arabize_numbers(text: str) -> str:
    return re.sub(r"\b(\d{1,6})\b", lambda m: _num_to_arabic(int(m.group(0))), text)


def _strip_tashkeel(text: str) -> str:
    return re.sub(r"[\u064B-\u065F\u0670\s،؛؟!.:]+", "", text or "")


DIACRITIZE_PROMPT = """أضف التشكيل الكامل والدقيق (الحركات والشدة) للنص العربي التالي حسب النحو وسياق الجملة.

قواعد صارمة جداً:
1. لا تغيّر ولا تحذف ولا تضف أي حرف أو كلمة أو علامة ترقيم.
2. لا تدمج أي كلمتين ولا تحذف المسافات.
3. أعد النص المشكول فقط دون أي شرح أو مقدمات.

النص:
{text}"""


def smart_diacritize(text: str, cache_file: Path | None = None) -> str:
    if not text or not re.search(r"[\u0621-\u064A]", text):
        return text

    if cache_file and cache_file.exists():
        cached = cache_file.read_text(encoding="utf-8").strip()
        if cached and _strip_tashkeel(cached) == _strip_tashkeel(text):
            return cached

    try:
        pool = get_pool()
        models = discover_and_cache(force=False)[:3]
        for model in models:
            for _ in range(2):
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": DIACRITIZE_PROMPT.format(text=text)}]}],
                    "generationConfig": {"temperature": 0.05, "maxOutputTokens": 2500},
                }
                r = requests.post(url, json=body, timeout=60)
                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if parts:
                        diac = parts[0].get("text", "").strip()
                        if _strip_tashkeel(diac) == _strip_tashkeel(text) and len(diac.split()) == len(text.split()):
                            pool._advance()
                            if cache_file:
                                cache_file.write_text(diac, encoding="utf-8")
                            return diac
                        print("   ⚠️ التشكيل غيّر أحرفاً في النص — الاحتفاظ بالأصل", flush=True)
                        return text
                elif r.status_code == 429:
                    pool.mark_failed(70)
                    time.sleep(1.5)
                elif r.status_code in (400, 404):
                    break
    except Exception as e:
        print(f"   ⚠️ تعذر التشكيل: {str(e)[:80]}", flush=True)
    return text


def sanitize_for_tts(t: str) -> str:
    if not t:
        return ""
    t = t.replace("ﷺ", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    t = t.replace("صلى الله عليه وسلم", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    t = arabize_numbers(t)
    t = re.sub(r"\[.*?\]|\(.*?\)|﴿.*?﴾", "", t, flags=re.DOTALL)
    t = re.sub(r"[^\u0621-\u064A\u064B-\u065F\u0670\s،؛؟!.:]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def download_human_ayah(surah: int, ayah: int, reciter_id: str, out_path: Path) -> bool:
    if out_path.exists() and out_path.stat().st_size > 1000:
        return True
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE_URL}/{reciter_id}/{filename}"
    try:
        r = requests.get(url, timeout=25)
        if r.status_code == 200 and len(r.content) > 1000:
            out_path.write_bytes(r.content)
            return True
    except Exception as e:
        print(f"   ⚠️ فشل تحميل {filename} ({reciter_id}): {str(e)[:80]}", flush=True)
    return False


def get_dur(p) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True,
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def normalize_wav(input_path: str, output_wav: str, gentle_loudnorm: bool = False) -> bool:
    """يحول الملف لـ WAV موحد دون تغيير السرعة أو النغمة، مع قص الصمت الطرفي فقط وتلاشي ناعم."""
    try:
        filters = [
            "silenceremove=start_periods=1:start_duration=0:start_threshold=-48dB",
            "areverse",
            "silenceremove=start_periods=1:start_duration=0:start_threshold=-48dB",
            "areverse",
        ]
        if gentle_loudnorm:
            filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        filters.append("afade=t=in:st=0:d=0.015")

        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-af", ",".join(filters),
             "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", output_wav],
            check=True, capture_output=True, timeout=60,
        )
        dur = get_dur(output_wav)
        if dur > 0.2:
            tmp = str(output_wav) + ".tmp.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-i", output_wav,
                 "-af", f"afade=t=out:st={max(0.0, dur - 0.03):.3f}:d=0.03",
                 "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", tmp],
                check=True, capture_output=True, timeout=60,
            )
            shutil.move(tmp, output_wav)
        return Path(output_wav).exists() and Path(output_wav).stat().st_size > 1000
    except Exception:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", output_wav],
            check=True, capture_output=True, timeout=60,
        )
        return True


def split_text(text: str, max_chars: int = 360) -> list[str]:
    sentences = re.split(r"(?<=[.!؟])\s+", text)
    chunks, current = [], ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(current) + len(s) + 1 <= max_chars:
            current = f"{current} {s}".strip()
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)
    return chunks


def _export_final_mp3(wav_list: list[Path], out_mp3: str, work: Path):
    concat_file = work / f"concat_{Path(out_mp3).stem}.txt"
    concat_file.write_text("\n".join(f"file '{p.resolve()}'" for p in wav_list), encoding="utf-8")
    merged_wav = work / f"merged_{Path(out_mp3).stem}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-ar", TARGET_SR, "-ac", "1", "-c:a", "pcm_s16le", str(merged_wav)],
        check=True, capture_output=True, timeout=300,
    )
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(merged_wav), "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)],
        check=True, capture_output=True, timeout=300,
    )


def build_episode_audio(json_path: str, out_yt_mp3: str, out_social_mp3: str, mode: str = "both"):
    build_yt = mode in ("yt", "both")
    build_social = mode in ("social", "both")

    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    ep_name = Path(json_path).stem
    ep_cache = CACHE_DIR / ep_name
    ep_cache.mkdir(parents=True, exist_ok=True)

    silence_path = work / "silence.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={TARGET_SR}:cl=mono",
         "-t", str(SILENCE_DUR), "-c:a", "pcm_s16le", str(silence_path)],
        check=True, capture_output=True,
    )

    segments_list = ep.get("segments", [])
    yt_wavs, social_wavs = [], []
    yt_meta, social_meta = [], []

    # تدوير عشوائي للقراء داخل الحلقة في Social بدون تكرار نفس القارئ مرتين متتاليتين
    reciter_bag = RECITER_LIST_SOCIAL.copy()
    random.shuffle(reciter_bag)
    reciter_idx = 0
    last_reciter_id = None

    def next_social_reciter():
        nonlocal reciter_bag, reciter_idx, last_reciter_id
        if reciter_idx >= len(reciter_bag):
            reciter_bag = RECITER_LIST_SOCIAL.copy()
            random.shuffle(reciter_bag)
            if len(reciter_bag) > 1 and reciter_bag[0]["id"] == last_reciter_id:
                reciter_bag[0], reciter_bag[1] = reciter_bag[1], reciter_bag[0]
            reciter_idx = 0
        chosen = reciter_bag[reciter_idx]
        reciter_idx += 1
        last_reciter_id = chosen["id"]
        return chosen

    try:
        for idx, seg in enumerate(segments_list):
            stype = seg.get("type")

            # ═══ السرد والنصوص الملقاة ═══
            if stype in ("narration", "teaser", "cta", "outro", "intro", "summary", "hadith", "fiqh", "adhkar"):
                raw_text = sanitize_for_tts(seg.get("text", ""))
                if not raw_text:
                    continue

                diac_cache = ep_cache / f"seg_{idx}_diac.txt"
                diac_text = smart_diacritize(raw_text, cache_file=diac_cache)
                chunks = split_text(diac_text)
                chunk_timings = []

                for c_idx, chunk in enumerate(chunks):
                    if not chunk.strip() or not re.search(r"[\u0621-\u064A]", chunk):
                        continue

                    text_hash = hashlib.md5(chunk.encode("utf-8")).hexdigest()[:8]
                    cached_wav = ep_cache / f"seg_{idx}_{c_idx}_{text_hash}.wav"

                    if not cached_wav.exists() or cached_wav.stat().st_size < 1000:
                        temp_mp3 = work / f"temp_{idx}_{c_idx}.mp3"
                        if not generate_audio(chunk, str(temp_mp3)):
                            raise RuntimeError(f"❌ فشل توليد صوت السرد للمقطع {idx}:{c_idx}")
                        normalize_wav(str(temp_mp3), str(cached_wav), gentle_loudnorm=True)
                        time.sleep(0.5)

                    dur = get_dur(cached_wav)
                    if build_yt:
                        yt_wavs.extend([cached_wav, silence_path])
                    if build_social:
                        social_wavs.extend([cached_wav, silence_path])

                    chunk_timings.append({
                        "text": chunk,
                        "duration": round(dur + SILENCE_DUR, 3),
                    })

                if chunk_timings:
                    meta_entry = {
                        "index": idx,
                        "type": stype,
                        "display": "",
                        "reciter": "",
                        "chunks": chunk_timings,
                    }
                    if build_yt:
                        yt_meta.append(meta_entry)
                    if build_social:
                        social_meta.append(meta_entry)

            # ═══ الآيات القرآنية ═══
            elif stype == "quran":
                s = int(seg.get("surah") or 0)
                ayahs = [int(a) for a in (seg.get("ayah_numbers") or []) if str(a).isdigit()]
                sname = str(seg.get("surah_name", "")).strip()
                if not s or not ayahs:
                    continue
                display = seg.get("display") or (f"{sname}: {ayahs[0]}" if sname else f"{s}:{ayahs[0]}")

                # ─── نسخة YT: الحصري الصافي (للبصمة v4) ───
                if build_yt:
                    yt_chunks = []
                    for a in ayahs:
                        raw_mp3 = ep_cache / f"yt_husary_{s}_{a}.mp3"
                        norm_wav = ep_cache / f"yt_husary_{s}_{a}_norm.wav"
                        if not norm_wav.exists():
                            if not download_human_ayah(s, a, RECITER_YT["id"], raw_mp3):
                                raise RuntimeError(f"❌ فشل تحميل آية الحصري {s}:{a}")
                            normalize_wav(str(raw_mp3), str(norm_wav), gentle_loudnorm=False)

                        yt_wavs.extend([norm_wav, silence_path])
                        yt_chunks.append({
                            "surah": s,
                            "ayah": a,
                            "surah_name": sname,
                            "reciter": RECITER_YT["name"],
                            "text": f"{display} - آية {a}",
                            "duration": round(get_dur(norm_wav) + SILENCE_DUR, 3),
                        })
                    if yt_chunks:
                        yt_meta.append({
                            "index": idx,
                            "type": "quran",
                            "surah": s,
                            "surah_name": sname,
                            "ayah_numbers": ayahs,
                            "display": display,
                            "reciter": RECITER_YT["name"],
                            "chunks": yt_chunks,
                        })

                # ─── نسخة Social: قارئ مختلف داخل الحلقة بصوت طبيعي صافٍ ───
                if build_social:
                    reciter_social = next_social_reciter()
                    print(f"   🎙️ الآيات {s}:{ayahs} → {reciter_social['name']}", flush=True)
                    social_chunks = []
                    for a in ayahs:
                        raw_mp3 = ep_cache / f"soc_{reciter_social['id']}_{s}_{a}.mp3"
                        norm_wav = ep_cache / f"soc_{reciter_social['id']}_{s}_{a}_norm.wav"
                        if not norm_wav.exists():
                            if not download_human_ayah(s, a, reciter_social["id"], raw_mp3):
                                # احتياطي قارئ بديل لو تعذر رابط آية معين
                                fallback_r = RECITER_LIST_SOCIAL[0]
                                if not download_human_ayah(s, a, fallback_r["id"], raw_mp3):
                                    raise RuntimeError(f"❌ فشل تحميل آية {s}:{a}")
                            normalize_wav(str(raw_mp3), str(norm_wav), gentle_loudnorm=True)

                        social_wavs.extend([norm_wav, silence_path])
                        social_chunks.append({
                            "surah": s,
                            "ayah": a,
                            "surah_name": sname,
                            "reciter": reciter_social["name"],
                            "text": f"{display} - آية {a}",
                            "duration": round(get_dur(norm_wav) + SILENCE_DUR, 3),
                        })
                    if social_chunks:
                        social_meta.append({
                            "index": idx,
                            "type": "quran",
                            "surah": s,
                            "surah_name": sname,
                            "ayah_numbers": ayahs,
                            "display": display,
                            "reciter": reciter_social["name"],
                            "chunks": social_chunks,
                        })

        if build_yt and yt_wavs:
            _export_final_mp3(yt_wavs, out_yt_mp3, work)
            Path(str(out_yt_mp3).replace(".mp3", "_meta.json")).write_text(
                json.dumps({
                    "episode": ep.get("episode_number", 1),
                    "title": ep.get("title", ""),
                    "story_id": ep.get("story_id", ""),
                    "total_duration": get_dur(out_yt_mp3),
                    "segments": yt_meta,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"✅ (YT) {out_yt_mp3} | {get_dur(out_yt_mp3)/60:.1f} دقيقة")

        if build_social and social_wavs:
            _export_final_mp3(social_wavs, out_social_mp3, work)
            Path(str(out_social_mp3).replace(".mp3", "_meta.json")).write_text(
                json.dumps({
                    "episode": ep.get("episode_number", 1),
                    "title": ep.get("title", ""),
                    "story_id": ep.get("story_id", ""),
                    "total_duration": get_dur(out_social_mp3),
                    "segments": social_meta,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"✅ (Social) {out_social_mp3} | {get_dur(out_social_mp3)/60:.1f} دقيقة")
    finally:
        cleanup_dir(work)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("episode_json")
    parser.add_argument("yt_mp3")
    parser.add_argument("social_mp3")
    parser.add_argument("--mode", choices=["social", "yt", "both"], default="both")
    args = parser.parse_args()
    build_episode_audio(args.episode_json, args.yt_mp3, args.social_mp3, mode=args.mode)
