"""توليد الصوت النهائي لخط القصص — v9.
- إصلاح استبدال (ﷺ) بالكلمة كاملة قبل النطق.
- معالجة الرمز في جميع المقاطع (سرد، خاتمة، تشويق).
- تحسين استقرار Gemini TTS.
"""
import os
import sys
import json
import asyncio
import subprocess
import tempfile
import shutil
import re
import base64
import wave
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir, notify_telegram, safe_copy

try:
    from gemini_tts_pool import gemini_tts_with_pool
    HAS_GEMINI_POOL = True
except ImportError:
    HAS_GEMINI_POOL = False

try:
    import edge_tts
    HAS_EDGE = True
except ImportError:
    HAS_EDGE = False


TARGET_SR = "24000"
TARGET_BR = "192k"
MAX_GEMINI_CALLS = 40
MAX_CONSECUTIVE_FAILURES = 3
EDGE_VOICE = "ar-SA-HamedNeural"

# ═══ إصلاحات الكلمات الحرجة ═══
CRITICAL_FIXES = {
    "ﷺ": "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ",
    "صلى الله عليه وسلم": "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ",
    "رضي الله عنه": "رَضِيَ اللَّهُ عَنْهُ",
    "رضي الله عنها": "رَضِيَ اللَّهُ عَنْهَا",
    "رضي الله عنهم": "رَضِيَ اللَّهُ عَنْهُمْ",
    "عليه السلام": "عَلَيْهِ السَّلَامُ",
    "عليها السلام": "عَلَيْهَا السَّلَامُ",
    "عليهم السلام": "عَلَيْهِمُ السَّلَامُ",
    "الله": "اللَّه", "بالله": "بِاللَّه", "والله": "وَاللَّه",
    "لله": "لِلَّه", "الرحمن": "الرَّحْمَن", "الرحيم": "الرَّحِيم",
    "القرآن": "الْقُرْآن", "محمد": "مُحَمَّد", "رسول": "رَسُول",
    "النبي": "النَّبِيّ", "الأنبياء": "الْأَنْبِيَاء", "الصديق": "الصِّدِّيق",
    "الشيطان": "الشَّيْطَان", "الملائكة": "الْمَلَائِكَة", "الجن": "الْجِنّ",
    "قال": "قَالَ", "قالت": "قَالَتْ", "قالوا": "قَالُوا", 
    "إبراهيم": "إِبْرَاهِيم", "إسماعيل": "إِسْمَاعِيل", "إسحاق": "إِسْحَاق",
    "يعقوب": "يَعْقُوب", "يوسف": "يُوسُف", "موسى": "مُوسَى", "عيسى": "عِيسَى",
    "داود": "دَاوُد", "سليمان": "سُلَيْمَان", "يونس": "يُونُس", "أيوب": "أَيُّوب",
    "زكريا": "زَكَرِيَّا", "يحيى": "يَحْيَى", "مريم": "مَرْيَم", "آدم": "آدَم",
    "نوح": "نُوح", "هود": "هُود", "صالح": "صَالِح", "لوط": "لُوط",
    "شعيب": "شُعَيْب", "هارون": "هَارُون", "مكة": "مَكَّة", "المدينة": "الْمَدِينَة",
    "الكعبة": "الْكَعْبَة", "المسجد": "الْمَسْجِد", "القدس": "الْقُدْس",
    "الشام": "الشَّام", "مصر": "مِصْر", "بابل": "بَابِل", "فلسطين": "فِلَسْطِين",
    "على": "عَلَى", "إلى": "إِلَى", "هدى": "هُدًى", "مصطفى": "مُصْطَفًى",
    "التقوى": "التَّقْوَى", "الأولى": "الْأُولَى", "الأخرى": "الْأُخْرَى",
    "الأدنى": "الْأَدْنَى", "الأعلى": "الْأَعْلَى", "الدنيا": "الدُّنْيَا",
    "الآخرة": "الْآخِرَة", "مرضى": "مَرْضَى", "أسرى": "أَسْرَى", "جرحى": "جَرْحَى",
    "دعوى": "دَعْوَى", "نجوى": "نَجْوَى", "مأوى": "مَأْوَى", "سلوى": "سَلْوَى",
    "بلوى": "بَلْوَى", "شكوى": "شَكْوَى", "رؤيا": "رُؤْيَا", "ذكرى": "ذِكْرَى",
    "أنثى": "أُنْثَى", "مولى": "مَوْلَى", "أولى": "أُولَى", "قصوى": "قُصْوَى",
    "مجتبى": "مُجْتَبَى", "مرتضى": "مُرْتَضَى", "مستشفى": "مُسْتَشْفَى",
    "معنى": "مَعْنَى", "مسمى": "مُسَمَّى", "مبنى": "مَبْنَى",
    "مستوى": "مُسْتَوَى", "منتدى": "مُنْتَدَى", "ملتقى": "مُلْتَقَى",
    "محتوى": "مُحْتَوَى",
}


def apply_critical_fixes(text: str) -> str:
    """استبدال الرموز والكلمات الحرجة بتشكيل صحيح."""
    # استبدال الرمز (ﷺ) مباشرة قبل أي معالجة أخرى
    text = text.replace("ﷺ", "صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ")
    
    for plain, diacritized in sorted(
        CRITICAL_FIXES.items(), key=lambda x: -len(x[0])
    ):
        if plain == "ﷺ":
            continue
        pattern = rf'(?<![ء-ي\u064B-\u0652]){re.escape(plain)}(?![ء-ي\u064B-\u0652])'
        text = re.sub(pattern, diacritized, text)
    return text


def _clean_text(text: str) -> str:
    """تنظيف النص من الرموز غير المرغوبة قبل الإرسال."""
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\(.*?\)', '', text)
    text = re.sub(r"[ۖۗۘۙۚۛ۞]", "", text)
    text = text.replace("۝", " ")
    return re.sub(r"\s+", " ", text).strip()


def get_duration(path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def normalize_mp3(input_path, output_path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path),
         "-ar", TARGET_SR, "-ac", "1",
         "-codec:a", "libmp3lame", "-b:a", TARGET_BR,
         str(output_path)],
        check=True, capture_output=True, timeout=120,
    )


def add_fade_in_only(input_mp3, output_mp3, duration=0.15):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_mp3),
         "-af", f"afade=t=in:st=0:d={duration}",
         "-ar", TARGET_SR, "-ac", "1",
         "-codec:a", "libmp3lame", "-b:a", TARGET_BR,
         str(output_mp3)],
        check=True, capture_output=True, timeout=60,
    )


def fetch_ayah_text(surah: int, ayah: int) -> str:
    try:
        r = requests.get(f"{QURAN_API}/ayah/{surah}:{ayah}", timeout=10)
        if r.status_code == 200:
            return r.json()["data"]["text"]
    except Exception:
        pass
    return ""


async def _edge_async(text: str, output_path: str):
    comm = edge_tts.Communicate(text, EDGE_VOICE, rate="-12%")
    await comm.save(output_path)


def edge_tts_generate(text: str, output_path: str):
    if not HAS_EDGE:
        raise RuntimeError("Edge TTS غير متاح")
    asyncio.run(_edge_async(text, output_path))
    if not Path(output_path).exists() or Path(output_path).stat().st_size < 1024:
        raise RuntimeError("Edge TTS: ملف صغير")


def build_episode_audio(episode_json_path: str, output_mp3: str):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    all_segs = ep.get("segments", [])

    segment_files = []
    meta_segments = []
    gemini_ok = 0
    gemini_fail = 0
    gemini_calls = 0
    edge_fallback = 0
    consecutive_failures = 0
    idx = 0

    try:
        for pos, seg in enumerate(all_segs):
            seg_type = seg.get("type")
            is_first = (pos == 0)

            text = ""
            display = ""
            clean = ""

            if seg_type in ("narration", "teaser", "cta", "outro", "intro", "summary"):
                text = apply_critical_fixes(seg["text"])
                clean = _clean_text(text)
                if not clean:
                    idx += 1
                    continue
                seg["text"] = text

            elif seg_type == "quran":
                surah = seg["surah"]
                ayahs = seg["ayah_numbers"]
                sname = seg.get("surah_name") or f"سورة {surah}"

                if len(ayahs) == 1:
                    txt = fetch_ayah_text(surah, ayahs[0])
                    display = f"{sname}: {ayahs[0]}"
                else:
                    txt = " ".join(filter(None, (fetch_ayah_text(surah, a) for a in ayahs)))
                    display = f"{sname}: {ayahs[0]}-{ayahs[-1]}"

                if not txt:
                    print(f"   ⏭️ لا نص للآية {display}", flush=True)
                    idx += 1
                    continue
                text = txt
                clean = _clean_text(txt)

            else:
                idx += 1
                continue

            raw_mp3 = work_dir / f"raw_{idx:03d}.mp3"
            generated_by_gemini = False

            can_use_gemini = (
                HAS_GEMINI_POOL
                and consecutive_failures < MAX_CONSECUTIVE_FAILURES
                and gemini_calls < MAX_GEMINI_CALLS
            )

            if can_use_gemini:
                gemini_calls += 1
                try:
                    print(f"🎙️ Gemini [{seg_type} {idx}]...", flush=True)
                    gemini_tts_with_pool(clean, str(raw_mp3), voice="Charon")

                    if raw_mp3.exists() and raw_mp3.stat().st_size > 5000:
                        gemini_ok += 1
                        consecutive_failures = 0
                        generated_by_gemini = True
                        print(f"   ✅ {get_duration(raw_mp3):.1f}s", flush=True)
                    else:
                        gemini_fail += 1
                        consecutive_failures += 1
                        print(f"   ⚠️ ملف صغير (متتالي: {consecutive_failures})", flush=True)
                except Exception as e:
                    gemini_fail += 1
                    consecutive_failures += 1
                    print(f"   ⚠️ {str(e)[:100]} (متتالي: {consecutive_failures})", flush=True)

            if not generated_by_gemini:
                print(f"🔊 Edge TTS [{seg_type} {idx}] (احتياطي)...", flush=True)
                tmp_edge = work_dir / f"edge_{idx:03d}.mp3"
                try:
                    edge_tts_generate(clean, str(tmp_edge))
                    normalize_mp3(tmp_edge, raw_mp3)
                    edge_fallback += 1
                except Exception as e:
                    print(f"   ❌ Edge TTS فشل: {e}", flush=True)
                    idx += 1
                    continue

            if is_first:
                faded = work_dir / f"faded_{idx:03d}.mp3"
                add_fade_in_only(raw_mp3, faded)
                segment_files.append(faded)
            else:
                segment_files.append(raw_mp3)

            meta_segments.append({
                "index": idx,
                "type": seg_type,
                "text": text[:300],
                "display": display,
                "duration": get_duration(raw_mp3),
                "engine": "gemini" if generated_by_gemini else "edge",
            })

            idx += 1

        if not segment_files:
            raise RuntimeError("لا مقاطع صوتية")

        print(f"\n🔗 دمج {len(segment_files)} مقطع (concat + copy)...", flush=True)
        concat_file = work_dir / "final.txt"
        concat_file.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in segment_files),
            encoding="utf-8",
        )

        Path(output_mp3).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_file),
             "-c", "copy",
             str(output_mp3)],
            check=True, capture_output=True, timeout=600,
        )

        meta_path = str(output_mp3).replace(".mp3", "_meta.json")
        Path(meta_path).write_text(
            json.dumps({
                "episode": ep.get("episode_number", 1),
                "title": ep.get("title", ""),
                "story_id": ep.get("story_id", ""),
                "total_duration": get_duration(output_mp3),
                "stats": {
                    "gemini_ok": gemini_ok,
                    "gemini_fail": gemini_fail,
                    "edge_fallback": edge_fallback,
                },
                "segments": meta_segments,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("═" * 60, flush=True)
        print(f"✅ {output_mp3} | {get_duration(output_mp3)/60:.1f} دقيقة", flush=True)
        print(f"   Gemini: {gemini_ok} ✅ | Edge: {edge_fallback}", flush=True)
        print("═" * 60, flush=True)

    finally:
        cleanup_dir(work_dir)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("الاستخدام: story_audio.py <episode.json> <out.mp3>")
        sys.exit(1)
    try:
        build_episode_audio(sys.argv[1], sys.argv[2])
    except Exception as e:
        import traceback
        traceback.print_exc()
        notify_telegram(f"فشل story_audio: {e}", "error")
        sys.exit(1)
