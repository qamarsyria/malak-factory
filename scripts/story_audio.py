"""توليد الصوت النهائي: Edge TTS للسرد + SILMA TTS للآيات (تجويد تلقائي بدون بصمة)."""
import os, sys, json, asyncio, subprocess, tempfile, traceback, shutil, re
from pathlib import Path
import requests
import edge_tts

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from sfx_manager import fetch_sfx
from utilities import cleanup_dir, notify_telegram, safe_copy

# محاولة استيراد SILMA TTS والـ Phonemizer
try:
    from silma_tts.api import SilmaTTS
    HAS_SILMA = True
except ImportError:
    HAS_SILMA = False
    print("⚠️ مكتبة silma-tts غير مثبتة، سيتم استخدام Edge TTS للآيات.")

try:
    from quranic_phonemizer import Phonemizer
    HAS_PHONEMIZER = True
except ImportError:
    HAS_PHONEMIZER = False
    print("⚠️ مكتبة quranic-phonemizer غير مثبتة، سيتم تمرير النص العادي.")

TARGET_BR = "192k"
SFX_VOLUME = 0.05
EDGE_VOICE = "ar-SA-HamedNeural"
EDGE_RATE = "-12%" 
EDGE_VOLUME = "+0%"

# تهيئة SILMA والـ Phonemizer
silma_engine = None
if HAS_SILMA:
    try: silma_engine = SilmaTTS()
    except Exception as e: print(f"⚠️ فشل تهيئة SILMA: {e}")

phonemizer = None
if HAS_PHONEMIZER:
    try: phonemizer = Phonemizer()
    except Exception as e: print(f"⚠️ فشل تهيئة Phonemizer: {e}")

def normalize(input_path, output_path):
    try:
        subprocess.run(["ffmpeg", "-y", "-i", str(input_path), "-ar", "44100", "-ac", "1", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-codec:a", "libmp3lame", "-b:a", TARGET_BR, str(output_path)], check=True, capture_output=True)
    except Exception:
        safe_copy(input_path, output_path)

def fetch_ayah_text(surah, ayah):
    try:
        r = requests.get(f"{QURAN_API}/ayah/{surah}:{ayah}", timeout=10)
        if r.status_code == 200: return r.json()["data"]["text"]
    except Exception: pass
    return ""

def get_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=15)
    try: return round(float(r.stdout.strip()), 3)
    except Exception: return 0.0

def add_fade(input_path, output_path):
    dur = get_duration(input_path)
    if dur <= 0: safe_copy(input_path, output_path); return
    subprocess.run(["ffmpeg", "-y", "-i", str(input_path), "-af", f"afade=t=in:st=0:d=0.2,afade=t=out:st={max(0, dur - 0.25)}:d=0.25", "-c:a", "libmp3lame", "-b:a", TARGET_BR, str(output_path)], check=True, capture_output=True)

async def _edge_tts_async(text, output_path):
    communicate = edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE, volume=EDGE_VOLUME)
    await communicate.save(str(output_path))

def edge_tts_generate(text, output_path):
    clean_text = re.sub(r'\[.*?\]', '', text)
    clean_text = re.sub(r'\(.*?\)', '', clean_text).strip()
    if not clean_text: clean_text = "تلاوة"
    try:
        asyncio.run(_edge_tts_async(clean_text, output_path))
        if os.path.getsize(output_path) < 100: raise Exception("ملف فارغ")
    except Exception as e:
        print(f"⚠️ فشل Edge TTS: {e}. سيتم توليد صمت.", flush=True)
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2", "-c:a", "libmp3lame", "-b:a", "192k", str(output_path)], check=True, capture_output=True)

def ensure_ref_audio(work_dir):
    """إنشاء ملف صوتي مرجعي تلقائياً إذا لم يكن موجوداً، ليعمل SILMA افتراضياً."""
    ref_audio = Path(__file__).parent / "silma_ref.wav"
    if ref_audio.exists(): return ref_audio
    
    print("🎙️ إنشاء ملف صوتي مرجعي افتراضي لـ SILMA...", flush=True)
    temp_ref = work_dir / "temp_ref.mp3"
    try:
        asyncio.run(_edge_tts_async("بسم الله الرحمن الرحيم", str(temp_ref)))
        subprocess.run(["ffmpeg", "-y", "-i", str(temp_ref), "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", str(ref_audio)], check=True, capture_output=True)
        return ref_audio
    except Exception as e:
        print(f"⚠️ فشل إنشاء الملف المرجعي: {e}", flush=True)
        return None

def try_silma_quran(surah, ayahs, output_path, fallback_text, work_dir):
    if not silma_engine: return False
    try:
        ref_audio = ensure_ref_audio(work_dir)
        if not ref_audio: return False

        gen_text = fallback_text
        if phonemizer:
            try:
                ref = f"{surah}:{ayahs[0]}" if len(ayahs) == 1 else f"{surah}:{ayahs[0]}-{surah}:{ayahs[-1]}"
                result = phonemizer.phonemize(ref)
                if hasattr(result, 'text'):
                    gen_text = result.text
                elif hasattr(result, 'arabic_text'):
                    gen_text = result.arabic_text
                else:
                    gen_text = " ".join(result.phonemes()) 
            except Exception: pass
        
        print(f"   محاولة SILMA (تجويد)...", flush=True)
        wav, sr, spec = silma_engine.infer(ref_file=str(ref_audio), ref_text="", gen_text=gen_text, file_wave=str(output_path))
        return True
    except Exception as e:
        print(f"   ⚠️ فشل SILMA: {e}", flush=True)
        return False

def build_episode_audio(episode_json_path, output_mp3):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    parts, meta_segments, idx = [], [], 0
    all_segs = ep.get("segments", [])
    total = len(all_segs)

    try:
        for pos, seg in enumerate(all_segs):
            seg_type = seg.get("type")
            raw_path = work_dir / f"raw_{idx:03d}.mp3"
            fade_path = work_dir / f"fade_{idx:03d}.mp3"
            norm_path = work_dir / f"norm_{idx:03d}.mp3"
            is_first = (pos == 0)
            is_last = (pos == total - 1)

            # 1. السرد (Edge TTS)
            if seg_type in ("narration", "teaser", "cta", "outro", "intro", "summary"):
                edge_tts_generate(seg["text"], str(raw_path))
                src = str(raw_path)
                sfx = fetch_sfx(seg.get("sfx", "")) if seg.get("sfx") else ""
                if sfx and os.path.exists(sfx):
                    mixed = work_dir / f"mixed_{idx:03d}.mp3"
                    subprocess.run(["ffmpeg", "-y", "-i", str(raw_path), "-stream_loop", "-1", "-i", sfx, "-filter_complex", f"[1:a]volume={SFX_VOLUME}[sfx];[0:a][sfx]amix=inputs=2:duration=first[out]", "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", str(mixed)], check=True, capture_output=True)
                    src = str(mixed)

                if is_first or is_last: add_fade(src, str(fade_path))
                else: safe_copy(src, str(fade_path))

                normalize(str(fade_path), str(norm_path))
                meta = {"index": idx, "type": seg_type, "text": seg["text"], "visual": seg.get("visual", "mosque"), "duration": get_duration(str(norm_path))}
                if seg_type == "teaser" and seg.get("next_episode_title"):
                    meta["teaser_data"] = {"next_episode_title": seg.get("next_episode_title"), "url": seg.get("url"), "platform_name": "ملاك المعرفة"}
                meta_segments.append(meta)
                parts.append(str(norm_path))

            # 2. الآيات (SILMA أولاً، ثم Edge TTS كاحتياط)
            elif seg_type == "quran":
                surah, ayahs = seg["surah"], seg["ayah_numbers"]
                sname = seg.get("surah_name") or f"سورة {surah}"
                
                if len(ayahs) == 1: txt, disp = fetch_ayah_text(surah, ayahs[0]), f"{sname}: {ayahs[0]}"
                else: txt, disp = " ".join(filter(None, (fetch_ayah_text(surah, a) for a in ayahs))), f"{sname}: {ayahs[0]}-{ayahs[-1]}"

                if not txt: idx += 1; continue
                generated = False

                if HAS_SILMA and silma_engine:
                    print(f"🎙️ SILMA TTS [آية {disp}]...", flush=True)
                    generated = try_silma_quran(surah, ayahs, str(raw_path), txt, work_dir)

                if not generated:
                    print(f"🎙️ Edge TTS [آية {disp}] (احتياط)...", flush=True)
                    edge_tts_generate(txt, str(raw_path))

                if is_first or is_last: add_fade(str(raw_path), str(fade_path))
                else: safe_copy(str(raw_path), str(fade_path))

                normalize(str(fade_path), str(norm_path))
                meta_segments.append({"index": idx, "type": "quran", "text": txt, "display": disp, "visual": "quran book", "duration": get_duration(str(norm_path)), "voice": "silma" if generated else "edge"})
                parts.append(str(norm_path))
            idx += 1

        if not parts: raise RuntimeError("لا مقاطع صوتية")
        concat_file = work_dir / "final.txt"
        concat_file.write_text("\n".join([f"file '{p}'" for p in parts]), encoding="utf-8")
        merged = work_dir / "merged_raw.mp3"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-codec:a", "libmp3lame", "-b:a", TARGET_BR, str(merged)], check=True, capture_output=True)
        Path(output_mp3).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(merged), str(output_mp3))
        
        meta_path = str(output_mp3).replace(".mp3", "_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "story_id": ep.get("story_id", ""), "total_duration": get_duration(output_mp3), "segments": meta_segments}, f, ensure_ascii=False, indent=2)
        print(f"✅ صوت: {output_mp3} | {get_duration(output_mp3)/60:.1f} د", flush=True)
    finally:
        cleanup_dir(work_dir)

if __name__ == "__main__":
    try:
        if len(sys.argv) < 3: sys.exit(1)
        build_episode_audio(sys.argv[1], sys.argv[2])
    except Exception as e:
        traceback.print_exc()
        notify_telegram(f"فشل story_audio: {e}", "error")
        sys.exit(1)
