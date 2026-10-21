"""بناء صوت حلقة قصة: سرد + آيات + مؤثرات صوتية طبيعية (بدون موسيقى)."""
import os
import subprocess
import tempfile
import json
from pathlib import Path

import requests

from config import QURAN_BASE, STORY_RECITER, SCENE_MAP
from voice import generate as gemini_generate
from scene_detector import detect_scene
from sfx_manager import fetch_sfx

TARGET_SR = "44100"
TARGET_CH = "1"
TARGET_BR = "192k"

def normalize(input_path: str, output_path: str):
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-ar", TARGET_SR, "-ac", TARGET_CH,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-codec:a", "libmp3lame", "-b:a", TARGET_BR,
        output_path,
    ], check=True, capture_output=True, timeout=120)

def fetch_ayah(surah: int, ayah: int, out_path: str):
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE}/{STORY_RECITER}/{filename}"
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"فشل جلب الآية {surah}:{ayah} - HTTP {r.status_code}")
    Path(out_path).write_bytes(r.content)

def get_duration(path: str) -> float:
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ], capture_output=True, text=True, timeout=10)
    try:
        return float(probe.stdout.strip() or 0)
    except Exception:
        return 0.0

def mix_audio_with_sfx(voice_path: str, sfx_path: str, output_path: str):
    """يمزج السرد مع المؤثر الصوتي (بنسبة 30% للمؤثر)."""
    if not sfx_path or not os.path.exists(sfx_path):
        subprocess.run(["ffmpeg", "-y", "-i", voice_path, "-c", "copy", output_path], check=True, capture_output=True)
        return

    subprocess.run([
        "ffmpeg", "-y",
        "-i", voice_path,
        "-stream_loop", "-1", "-i", sfx_path,
        "-filter_complex",
        "[1:a]volume=0.30[sfx];[0:a][sfx]amix=inputs=2:duration=first:dropout_transition=3[out]",
        "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        output_path
    ], check=True, capture_output=True, timeout=120)

def build_episode_audio(episode_json_path: str, output_mp3: str = "out/episode.mp3"):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    surah = ep.get("surah", 12)
    segments = ep.get("segments", [])

    Path("out").mkdir(exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))

    parts = []
    meta_segments = []

    for i, seg in enumerate(segments):
        seg_type = seg.get("type")
        raw_path = work_dir / f"raw_{i:03d}.mp3"
        norm_path = work_dir / f"norm_{i:03d}.mp3"
        mixed_path = work_dir / f"mixed_{i:03d}.mp3"

        try:
            if seg_type == "narration":
                text = seg.get("text", "").strip()
                if not text: continue
                
                print(f"[{i}] سرد ({len(text)} حرف)...")
                
                scene = detect_scene(text)
                gemini_generate(text, str(raw_path), voice="Charon", item_type="story")
                
                sfx_query = SCENE_MAP.get(scene, {}).get("sfx_queries", ["wind"])[0]
                sfx_path = fetch_sfx(sfx_query)
                
                mix_audio_with_sfx(str(raw_path), sfx_path, str(mixed_path))
                raw_path = mixed_path
                
                meta_text = text
                meta_title = ""
                meta_scene = scene

            elif seg_type == "quran":
                ayah_nums = seg.get("ayah_numbers", [])
                if not ayah_nums: continue
                
                print(f"[{i}] آية ({len(ayah_nums)})...")
                if len(ayah_nums) == 1:
                    fetch_ayah(surah, ayah_nums[0], str(raw_path))
                else:
                    sub_parts = []
                    for j, ay in enumerate(ayah_nums):
                        sub_p = work_dir / f"sub_{i:03d}_{j:02d}.mp3"
                        fetch_ayah(surah, ay, str(sub_p))
                        sub_parts.append(str(sub_p))
                    list_file = work_dir / f"list_{i:03d}.txt"
                    with open(list_file, "w") as f:
                        for sp in sub_parts: f.write(f"file '{sp}'\n")
                    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(raw_path)], check=True)
                
                meta_text = ""
                meta_title = f"سورة يوسف — الآيات {ayah_nums[0]}-{ayah_nums[-1]}" if len(ayah_nums) > 1 else f"سورة يوسف — الآية {ayah_nums[0]}"
                meta_scene = "calm"

            else:
                continue

            normalize(str(raw_path), str(norm_path))
            
            seg_duration = get_duration(str(norm_path))
            meta_segments.append({
                "index": i, "type": seg_type, "text": meta_text,
                "title": meta_title, "duration": round(seg_duration, 3),
                "scene": meta_scene
            })
            parts.append(str(norm_path))
            print(f"   ✅ [{i}] جاهز ({seg_duration:.1f} ث)")

        except Exception as e:
            print(f"   ❌ [{i}] فشل: {e}")
            raise

    print(f"\n🔗 دمج {len(parts)} مقطع...")
    list_file = work_dir / "final.txt"
    with open(list_file, "w") as f:
        for p in parts: f.write(f"file '{p}'\n")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-codec:a", "libmp3lame", "-b:a", TARGET_BR, output_mp3,
    ], check=True)

    duration = get_duration(output_mp3)
    meta_path = output_mp3.replace(".mp3", "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "episode": ep.get("episode_number", 1), "surah": surah,
            "title": ep.get("title", ""), "total_duration": round(duration, 3),
            "segments": meta_segments,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n✅ الحلقة: {output_mp3} | ⏱️ المدة: {duration/60:.1f} دقيقة")
    return output_mp3

if __name__ == "__main__":
    build_episode_audio(
        "data/stories/yusuf/episode-01.json",
        "out/episode-01.mp3",
    )
