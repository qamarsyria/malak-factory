"""بناء صوت حلقة قصة: سرد + آيات + مؤثرات صوتية هادئة (بدون موسيقى)."""
import os
import subprocess
import tempfile
import json
import re
from pathlib import Path

import requests

from config import QURAN_BASE, STORY_RECITER
from voice import generate as gemini_generate
from story_director import direct_scene
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
    """يمزج السرد مع المؤثر الصوتي (بنسبة 15% للمؤثر فقط للسرد)."""
    if not sfx_path or not os.path.exists(sfx_path):
        subprocess.run(["ffmpeg", "-y", "-i", voice_path, "-c", "copy", output_path], check=True, capture_output=True)
        return

    subprocess.run([
        "ffmpeg", "-y",
        "-i", voice_path,
        "-stream_loop", "-1", "-i", sfx_path,
        "-filter_complex",
        "[1:a]volume=0.15[sfx];[0:a][sfx]amix=inputs=2:duration=first:dropout_transition=2[out]",
        "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        output_path
    ], check=True, capture_output=True, timeout=120)


def add_fade_in_out(input_path: str, output_path: str):
    """يضيف fade-in (0.1ث) و fade-out (0.15ث) بطريقة آمنة مع جميع إصدارات ffmpeg."""
    duration = get_duration(input_path)
    if duration <= 0.5:
        subprocess.run(["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path], check=True, capture_output=True)
        return

    # حساب وقت بدء الـ fade-out رياضياً (رقم موجب دائماً)
    fade_out_start = max(0.0, duration - 0.15)

    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"afade=t=in:st=0:d=0.1,afade=t=out:st={fade_out_start:.3f}:d=0.15",
        "-c:a", "libmp3lame", "-b:a", "192k",
        output_path,
    ], check=True, capture_output=True, timeout=60)


def split_into_sentences(text):
    parts = re.split(r'(?<=[.!؟])\s+', text)
    return [p.strip() for p in parts if p.strip()]


def build_episode_audio(episode_json_path: str, output_mp3: str = "out/episode.mp3"):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    surah = ep.get("surah", 12)
    segments = ep.get("segments", [])

    Path("out").mkdir(exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))

    parts = []
    meta_segments = []
    idx_counter = 0

    for seg in segments:
        seg_type = seg.get("type")

        if seg_type == "narration":
            full_text = seg.get("text", "").strip()
            if not full_text:
                continue

            sentences = split_into_sentences(full_text)
            merged_sentences = []
            buf = ""
            for s in sentences:
                if len(s) < 15:
                    buf += " " + s
                else:
                    if buf:
                        merged_sentences.append(buf.strip())
                        buf = ""
                    merged_sentences.append(s)
            if buf:
                merged_sentences.append(buf.strip())

            for s in merged_sentences:
                raw_path = work_dir / f"raw_{idx_counter:03d}.mp3"
                mixed_path = work_dir / f"mixed_{idx_counter:03d}.mp3"
                fade_path = work_dir / f"fade_{idx_counter:03d}.mp3"
                norm_path = work_dir / f"norm_{idx_counter:03d}.mp3"

                print(f"[{idx_counter}] سرد: {s[:40]}...")

                scene_data = direct_scene(s)
                gemini_generate(s, str(raw_path), voice="Charon", item_type="story")

                sfx_path = fetch_sfx(scene_data["sfx"])
                mix_audio_with_sfx(str(raw_path), sfx_path, str(mixed_path))

                # إضافة fade in/out لإزالة القطشة (بطريقة آمنة)
                add_fade_in_out(str(mixed_path), str(fade_path))

                normalize(str(fade_path), str(norm_path))

                dur = get_duration(str(norm_path))
                meta_segments.append({
                    "index": idx_counter, "type": "narration",
                    "text": s, "visual": scene_data["visual"], "duration": round(dur, 3)
                })
                parts.append(str(norm_path))
                idx_counter += 1

        elif seg_type == "quran":
            ayah_nums = seg.get("ayah_numbers", [])
            if not ayah_nums:
                continue

            raw_path = work_dir / f"raw_{idx_counter:03d}.mp3"
            norm_path = work_dir / f"norm_{idx_counter:03d}.mp3"

            print(f"[{idx_counter}] آية: {ayah_nums}")

            if len(ayah_nums) == 1:
                fetch_ayah_audio(surah, ayah_nums[0], str(raw_path))
                txt = fetch_ayah_text(surah, ayah_nums[0])
            else:
                sub_parts = []
                txt_list = []
                for j, ay in enumerate(ayah_nums):
                    sub_p = work_dir / f"sub_{idx_counter:03d}_{j:02d}.mp3"
                    fetch_ayah_audio(surah, ay, str(sub_p))
                    sub_parts.append(str(sub_p))
                    txt_list.append(fetch_ayah_text(surah, ay))
                txt = " ".join(txt_list)

                list_file = work_dir / f"list_{idx_counter:03d}.txt"
                with open(list_file, "w") as f:
                    for sp in sub_parts:
                        f.write(f"file '{sp}'\n")
                subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(raw_path)], check=True)

            # الآيات: بدون مؤثرات، بدون fade (احتراماً للقرآن)
            normalize(str(raw_path), str(norm_path))

            dur = get_duration(str(norm_path))
            meta_segments.append({
                "index": idx_counter, "type": "quran",
                "text": txt, "visual": "ancient quran book mosque", "duration": round(dur, 3)
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
