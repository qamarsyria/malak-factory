"""بناء صوت حلقة قصة: سرد بنبرة ديناميكية + آيات + مؤثرات خافتة (5%)."""
import os
import subprocess
import tempfile
import json
import re
import sys
from pathlib import Path

import requests

from config import QURAN_BASE, STORY_RECITER
from voice import generate as gemini_generate
from smart_director import direct_scene
from sfx_manager import fetch_sfx

TARGET_SR = "44100"
TARGET_CH = "1"
TARGET_BR = "192k"
SFX_VOLUME = 0.05


def normalize(input_path: str, output_path: str):
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-ar", TARGET_SR, "-ac", TARGET_CH,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-codec:a", "libmp3lame", "-b:a", TARGET_BR,
        output_path,
    ], check=True, capture_output=True, timeout=120)


def fetch_ayah_audio(surah: int, ayah: int, out_path: str):
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{QURAN_BASE}/{STORY_RECITER}/{filename}"
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"فشل جلب الآية {surah}:{ayah} - HTTP {r.status_code}")
    Path(out_path).write_bytes(r.content)


def fetch_ayah_text(surah: int, ayah: int) -> str:
    try:
        url = f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah}/ar.alafasy"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()['data']['text']
    except Exception:
        pass
    return ""


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
    if not sfx_path or not os.path.exists(sfx_path):
        subprocess.run(["ffmpeg", "-y", "-i", voice_path, "-c", "copy", output_path], check=True, capture_output=True)
        return
    subprocess.run([
        "ffmpeg", "-y", "-i", voice_path, "-stream_loop", "-1", "-i", sfx_path,
        "-filter_complex",
        f"[1:a]volume={SFX_VOLUME}[sfx];[0:a][sfx]amix=inputs=2:duration=first:dropout_transition=2[out]",
        "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", output_path
    ], check=True, capture_output=True, timeout=120)


def add_fade_in_out(input_path: str, output_path: str):
    duration = get_duration(input_path)
    if duration <= 0.5:
        subprocess.run(["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path], check=True, capture_output=True)
        return
    fade_out_start = max(0.0, duration - 0.15)
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"afade=t=in:st=0:d=0.1,afade=t=out:st={fade_out_start:.3f}:d=0.15",
        "-c:a", "libmp3lame", "-b:a", "192k", output_path,
    ], check=True, capture_output=True, timeout=60)


def build_episode_audio(episode_json_path: str, output_mp3: str = "out/episode.mp3"):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    surah_default = ep.get("surah", 12)
    segments = ep.get("segments", [])

    Path("out").mkdir(exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    parts, meta_segments = [], []
    idx_counter = 0

    for seg in segments:
        seg_type = seg.get("type")

        if seg_type in ("narration", "teaser"):
            full_text = seg.get("text", "").strip()
            if not full_text: continue

            raw_path = work_dir / f"raw_{idx_counter:03d}.mp3"
            mixed_path = work_dir / f"mixed_{idx_counter:03d}.mp3"
            fade_path = work_dir / f"fade_{idx_counter:03d}.mp3"
            norm_path = work_dir / f"norm_{idx_counter:03d}.mp3"

            print(f"[{idx_counter}] {seg_type}: {full_text[:60]}...")

            if seg.get("emotion_hint"):
                direction = {
                    "visual": seg.get("visual", "islamic architecture mosque"),
                    "sfx": seg.get("sfx", ""),
                    "voice_style": seg["emotion_hint"],
                }
            else:
                direction = direct_scene(full_text)

            voice_style = direction.get("voice_style", "")

            gemini_generate(
                full_text, str(raw_path),
                voice="Charon", item_type="story",
                emotion_hint=voice_style,
            )

            sfx_path = ""
            if direction.get("sfx"):
                sfx_path = fetch_sfx(direction["sfx"])

            mix_audio_with_sfx(str(raw_path), sfx_path, str(mixed_path))
            add_fade_in_out(str(mixed_path), str(fade_path))
            normalize(str(fade_path), str(norm_path))

            dur = get_duration(str(norm_path))

            meta_entry = {
                "index": idx_counter,
                "type": seg_type,
                "text": full_text,
                "visual": direction.get("visual", "islamic architecture mosque"),
                "duration": round(dur, 3),
            }

            if seg_type == "teaser":
                meta_entry["teaser_data"] = {
                    "next_episode_title": seg.get("next_episode_title", ""),
                    "url": seg.get("url", ""),
                    "platform_name": seg.get("platform_name", "ملاك المعرفة"),
                }

            meta_segments.append(meta_entry)
            parts.append(str(norm_path))
            idx_counter += 1

        elif seg_type == "quran":
            ayah_nums = seg.get("ayah_numbers", [])
            if not ayah_nums: continue

            seg_surah = seg.get("surah", surah_default)
            surah_name = seg.get("surah_name", "")

            raw_path = work_dir / f"raw_{idx_counter:03d}.mp3"
            norm_path = work_dir / f"norm_{idx_counter:03d}.mp3"

            print(f"[{idx_counter}] آية: {surah_name} {ayah_nums} (بدون مؤثرات)")

            if len(ayah_nums) == 1:
                fetch_ayah_audio(seg_surah, ayah_nums[0], str(raw_path))
                txt = fetch_ayah_text(seg_surah, ayah_nums[0])
            else:
                sub_parts, txt_list = [], []
                for j, ay in enumerate(ayah_nums):
                    sub_p = work_dir / f"sub_{idx_counter:03d}_{j:02d}.mp3"
                    fetch_ayah_audio(seg_surah, ay, str(sub_p))
                    sub_parts.append(str(sub_p))
                    txt_list.append(fetch_ayah_text(seg_surah, ay))
                txt = " ".join(txt_list)
                list_file = work_dir / f"list_{idx_counter:03d}.txt"
                with open(list_file, "w") as f:
                    for sp in sub_parts:
                        f.write(f"file '{sp}'\n")
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                     "-i", str(list_file), "-c", "copy", str(raw_path)],
                    check=True,
                )

            normalize(str(raw_path), str(norm_path))
            dur = get_duration(str(norm_path))

            meta_segments.append({
                "index": idx_counter,
                "type": "quran",
                "text": txt,
                "surah": seg_surah,
                "surah_name": surah_name,
                "ayah_numbers": ayah_nums,
                "visual": "ancient quran book mosque",
                "duration": round(dur, 3),
            })
            parts.append(str(norm_path))
            idx_counter += 1

    print(f"\n🔗 دمج {len(parts)} مقطع...")
    list_file = work_dir / "final.txt"
    with open(list_file, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-codec:a", "libmp3lame", "-b:a", TARGET_BR, output_mp3,
    ], check=True)

    duration = get_duration(output_mp3)
    meta_path = output_mp3.replace(".mp3", "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "episode": ep.get("episode_number", 1),
            "surah": surah_default,
            "title": ep.get("title", ""),
            "total_duration": round(duration, 3),
            "segments": meta_segments,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n✅ الحلقة: {output_mp3} | ⏱️ المدة: {duration/60:.1f} دقيقة")
    return output_mp3

if __name__ == "__main__":
    if len(sys.argv) > 2:
        build_episode_audio(sys.argv[1], sys.argv[2])
    else:
        build_episode_audio("data/stories/yusuf/episode-01.json", "out/episode-01.mp3")
