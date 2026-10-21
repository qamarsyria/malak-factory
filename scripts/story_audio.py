"""بناء صوت حلقة قصة: سرد Gemini + آيات EveryAyah + دمج نظيف."""
import os
import subprocess
import tempfile
import json
from pathlib import Path

import requests

from config import QURAN_BASE, STORY_RECITER
from voice import generate as gemini_generate

TARGET_SR = "44100"
TARGET_CH = "1"
TARGET_BR = "192k"


def normalize(input_path: str, output_path: str):
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-ar", TARGET_SR,
        "-ac", TARGET_CH,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-codec:a", "libmp3lame",
        "-b:a", TARGET_BR,
        output_path,
    ], check=True, capture_output=True, timeout=120)


def fetch_ayah(surah: int, ayah: int, out_path: str):
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE}/{STORY_RECITER}/{filename}"
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"فشل جلب الآية {surah}:{ayah} - HTTP {r.status_code}")
    Path(out_path).write_bytes(r.content)


def add_silence(input_path: str, output_path: str, duration: float = 0.6):
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"apad=pad_dur={duration}",
        "-codec:a", "libmp3lame",
        "-b:a", TARGET_BR,
        output_path,
    ], check=True, capture_output=True, timeout=60)


def build_episode_audio(episode_json_path: str, output_mp3: str = "out/episode.mp3"):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))

    surah = ep.get("surah", 12)
    segments = ep.get("segments", [])

    Path("out").mkdir(exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))

    parts = []

    for i, seg in enumerate(segments):
        seg_type = seg.get("type")
        raw_path = work_dir / f"raw_{i:03d}.mp3"
        norm_path = work_dir / f"norm_{i:03d}.mp3"
        padded_path = work_dir / f"pad_{i:03d}.mp3"

        try:
            if seg_type == "narration":
                text = seg.get("text", "").strip()
                if not text:
                    continue
                print(f"[{i}] سرد ({len(text)} حرف)...")
                gemini_generate(text, str(raw_path), voice="Charon", item_type="story")

            elif seg_type == "quran":
                ayah_nums = seg.get("ayah_numbers", [])
                if not ayah_nums:
                    continue

                if len(ayah_nums) == 1:
                    print(f"[{i}] آية {surah}:{ayah_nums[0]}...")
                    fetch_ayah(surah, ayah_nums[0], str(raw_path))
                else:
                    print(f"[{i}] {len(ayah_nums)} آية ({surah}:{ayah_nums[0]}-{ayah_nums[-1]})...")
                    sub_parts = []
                    for j, ay in enumerate(ayah_nums):
                        sub_p = work_dir / f"sub_{i:03d}_{j:02d}.mp3"
                        fetch_ayah(surah, ay, str(sub_p))
                        sub_parts.append(str(sub_p))

                    list_file = work_dir / f"list_{i:03d}.txt"
                    with open(list_file, "w") as f:
                        for sp in sub_parts:
                            f.write(f"file '{sp}'\n")

                    subprocess.run([
                        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(list_file),
                        "-codec:a", "libmp3lame", "-b:a", TARGET_BR,
                        str(raw_path),
                    ], check=True, capture_output=True, timeout=120)

            else:
                print(f"[{i}] نوع غير معروف: {seg_type} — تخطي")
                continue

            normalize(str(raw_path), str(norm_path))
            add_silence(str(norm_path), str(padded_path), duration=0.6)

            parts.append(str(padded_path))
            print(f"   ✅ [{i}] جاهز")

        except Exception as e:
            print(f"   ❌ [{i}] فشل: {e}")
            raise

    if not parts:
        raise RuntimeError("لا مقاطع للدمج")

    print(f"\n🔗 دمج {len(parts)} مقطع...")
    list_file = work_dir / "final.txt"
    with open(list_file, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-codec:a", "libmp3lame", "-b:a", TARGET_BR,
        output_mp3,
    ], check=True, capture_output=True, timeout=180)

    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", output_mp3,
    ], capture_output=True, text=True, timeout=10)
    duration = float(probe.stdout.strip() or 0)

    print(f"\n✅ الحلقة: {output_mp3}")
    print(f"⏱️  المدة: {duration/60:.1f} دقيقة ({duration:.0f} ثانية)")

    return output_mp3


if __name__ == "__main__":
    build_episode_audio(
        "data/stories/yusuf/episode-01.json",
        "out/episode-01.mp3",
    )
