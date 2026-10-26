"""توليد الصوت المستقر: Edge TTS للسرد + Gemini TTS للآيات (مع حماية)."""
import os, sys, json, asyncio, subprocess, tempfile, traceback, shutil
from pathlib import Path
import requests
import edge_tts

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from voice import generate as gemini_generate
from sfx_manager import fetch_sfx
from utilities import cleanup_dir, notify_telegram, safe_copy

TARGET_BR = "192k"
SFX_VOLUME = 0.05

# إعدادات Edge TTS (سرعة -12% التي كانت تعجبك)
EDGE_VOICE = "ar-SA-HamedNeural"
EDGE_RATE = "-12%" 
EDGE_VOLUME = "+0%"

# إعدادات Gemini للآيات
GEMINI_VOICE_QURAN = "Fenrir"
QURAN_STYLE = "(recite in a slow, solemn, tajweed-compliant tone with full tashkeel and proper waqf)"

MAX_GEMINI_TTS_PER_EPISODE = 8

def normalize(input_path, output_path):
    subprocess.run(["ffmpeg", "-y", "-i", str(input_path), "-ar", "44100", "-ac", "1", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-codec:a", "libmp3lame", "-b:a", TARGET_BR, str(output_path)], check=True, capture_output=True)

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
    try:
        asyncio.run(_edge_tts_async(text, output_path))
    except Exception as e:
        print(f"⚠️ فشل Edge TTS: {e}. سيتم توليد صمت للمقطع.", flush=True)
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-c:a", "libmp3lame", str(output_path)], check=True, capture_output=True)

def try_gemini_quran(text, output_path):
    try:
        gemini_generate(text, str(output_path), voice=GEMINI_VOICE_QURAN, item_type="quran", emotion_hint=QURAN_STYLE)
        return True
    except Exception:
        return False

def build_episode_audio(episode_json_path, output_mp3):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    parts, meta_segments, idx = [], [], 0
    all_segs = ep.get("segments", [])
    total = len(all_segs)
    gemini_quran_count = 0
    gemini_available = True

    try:
        for pos, seg in enumerate(all_segs):
            seg_type = seg.get("type")
            raw_path = work_dir / f"raw_{idx:03d}.mp3"
            fade_path = work_dir / f"fade_{idx:03d}.mp3"
            norm_path = work_dir / f"norm_{idx:03d}.mp3"
            is_first = (pos == 0)
            is_last = (pos == total - 1)

            # 1. السرد (Edge TTS فقط - مجاني ولا ينفد)
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

            # 2. الآيات (Gemini أولاً، ثم Edge كاحتياط مع النص الحقيقي)
            elif seg_type == "quran":
                surah, ayahs = seg["surah"], seg["ayah_numbers"]
                sname = seg.get("surah_name") or f"سورة {surah}"
                if len(ayahs) == 1: txt, disp = fetch_ayah_text(surah, ayahs[0]), f"{sname}: {ayahs[0]}"
                else: txt, disp = " ".join(filter(None, (fetch_ayah_text(surah, a) for a in ayahs))), f"{sname}: {ayahs[0]}-{ayahs[-1]}"

                if not txt: idx += 1; continue
                generated = False

                # محاولة Gemini
                if gemini_available and gemini_quran_count < MAX_GEMINI_TTS_PER_EPISODE:
                    generated = try_gemini_quran(txt, str(raw_path))
                    if generated: gemini_quran_count += 1
                    else: gemini_available = False

                # إذا فشل Gemini، نستخدم Edge TTS لقراءة نص الآية (وليس رقمها)
                if not generated: 
                    edge_tts_generate(txt, str(raw_path))

                if is_first or is_last: add_fade(str(raw_path), str(fade_path))
                else: safe_copy(str(raw_path), str(fade_path))

                normalize(str(fade_path), str(norm_path))
                meta_segments.append({"index": idx, "type": "quran", "text": txt, "display": disp, "visual": "quran book", "duration": get_duration(str(norm_path)), "voice": "gemini" if generated else "edge"})
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
            json.dump({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "story_id": ep.get("story_id", ""), "total_duration": get_duration(output_mp3), "gemini_quran_count": gemini_quran_count, "segments": meta_segments}, f, ensure_ascii=False, indent=2)
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
