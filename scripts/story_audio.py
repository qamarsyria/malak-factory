import os, subprocess, tempfile, json, sys, random
from pathlib import Path
import requests
from config import QURAN_API
from voice import generate as gemini_generate
from smart_director import direct_scene
from sfx_manager import fetch_sfx
from utilities import cleanup_dir, notify_telegram

TARGET_BR = "192k"
SFX_VOLUME = 0.05

# أصوات Gemini محسّنة
VOICE_NARRATION = "Charon"
VOICE_QURAN = "Fenrir"  # أعمق وأخشع
QURAN_STYLE = "(recite in a slow, solemn, tajweed-compliant tone with pauses at proper waqf marks)"


def normalize(input_path, output_path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-ar", "44100", "-ac", "1",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
         "-codec:a", "libmp3lame", "-b:a", TARGET_BR, output_path],
        check=True, capture_output=True,
    )


def fetch_ayah_text(surah, ayah):
    try:
        r = requests.get(f"{QURAN_API}/ayah/{surah}:{ayah}", timeout=10)
        if r.status_code == 200:
            return r.json()["data"]["text"]
    except Exception as e:
        print(f"   ⚠️ فشل جلب آية {surah}:{ayah}: {e}", flush=True)
    return ""


def get_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def add_fade(input_path, output_path):
    dur = get_duration(input_path)
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-af",
         f"afade=t=in:st=0:d=0.15,afade=t=out:st={max(0, dur-0.2)}:d=0.2",
         "-c:a", "libmp3lame", "-b:a", "192k", output_path],
        check=True, capture_output=True,
    )


def build_episode_audio(episode_json_path, output_mp3):
    ep = json.loads(Path(episode_json_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    parts, meta_segments, idx = [], [], 0

    try:
        for seg in ep.get("segments", []):
            seg_type = seg.get("type")
            raw_path = work_dir / f"raw_{idx:03d}.mp3"
            fade_path = work_dir / f"fade_{idx:03d}.mp3"
            norm_path = work_dir / f"norm_{idx:03d}.mp3"

            if seg_type in ("narration", "teaser", "cta", "outro"):
                # نبرة السرد/الختامي
                style = seg.get("emotion_hint") or "(in a warm, narrative tone)"
                visual = seg.get("visual", "mosque")
                gemini_generate(seg["text"], str(raw_path), voice=VOICE_NARRATION,
                                item_type="story", emotion_hint=style)

                sfx = fetch_sfx(seg.get("sfx", "")) if seg.get("sfx") else ""
                mixed = work_dir / f"mixed_{idx:03d}.mp3"
                if sfx and os.path.exists(sfx):
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", str(raw_path), "-stream_loop", "-1", "-i", sfx,
                         "-filter_complex",
                         f"[1:a]volume={SFX_VOLUME}[sfx];[0:a][sfx]amix=inputs=2:duration=first[out]",
                         "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", str(mixed)],
                        check=True, capture_output=True,
                    )
                    add_fade(str(mixed), str(fade_path))
                else:
                    add_fade(str(raw_path), str(fade_path))

                normalize(str(fade_path), str(norm_path))
                meta = {
                    "index": idx, "type": seg_type, "text": seg["text"],
                    "visual": visual, "duration": get_duration(str(norm_path)),
                }
                if seg_type == "teaser" and seg.get("next_episode_title"):
                    meta["teaser_data"] = {
                        "next_episode_title": seg.get("next_episode_title"),
                        "url": seg.get("url"),
                        "platform_name": "ملاك المعرفة",
                    }
                meta_segments.append(meta)
                parts.append(str(norm_path))

            elif seg_type == "quran":
                surah, ayahs = seg["surah"], seg["ayah_numbers"]
                sname = seg.get("surah_name", f"سورة {surah}")

                if len(ayahs) == 1:
                    txt = fetch_ayah_text(surah, ayahs[0])
                    disp = f"{sname}: {ayahs[0]}"
                else:
                    txt = " ".join(filter(None, (fetch_ayah_text(surah, a) for a in ayahs)))
                    disp = f"{sname}: {ayahs[0]}-{ayahs[-1]}"

                if not txt:
                    print(f"   ⚠️ تخطي آية {disp} (نص فارغ)", flush=True)
                    idx += 1
                    continue

                # TTS محسّن للقرآن: صوت أعمق + نبرة خشوع + وقفات
                gemini_generate(txt, str(raw_path), voice=VOICE_QURAN,
                                item_type="quran", emotion_hint=QURAN_STYLE)
                normalize(str(raw_path), str(norm_path))
                meta_segments.append({
                    "index": idx, "type": "quran", "text": txt, "display": disp,
                    "visual": "quran book", "duration": get_duration(str(norm_path)),
                })
                parts.append(str(norm_path))

            idx += 1

        if not parts:
            raise RuntimeError("لا توجد مقاطع صوتية")

        (work_dir / "final.txt").write_text("\n".join([f"file '{p}'" for p in parts]))
        merged = work_dir / "merged_raw.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(work_dir / "final.txt"),
             "-codec:a", "libmp3lame", "-b:a", TARGET_BR, str(merged)],
            check=True, capture_output=True,
        )

        # كسر البصمة (6%+)
        print("🛡️ كسر البصمة...", flush=True)
        dsp = (
            "asetrate=44100*1.065,"          # +6.5% حدة
            "aresample=44100,"
            "atempo=1.05,"                    # +5% سرعة
            "equalizer=f=1200:width_type=h:width=300:g=-2,"
            "equalizer=f=3500:width_type=h:width=600:g=-1.5"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(merged), "-af", dsp,
             "-codec:a", "libmp3lame", "-b:a", TARGET_BR, output_mp3],
            check=True, capture_output=True,
        )

        meta_path = str(output_mp3).replace(".mp3", "_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "episode": ep.get("episode_number", 1),
                "title": ep.get("title", ""),
                "total_duration": get_duration(output_mp3),
                "segments": meta_segments,
            }, f, ensure_ascii=False, indent=2)

        print(f"✅ صوت: {output_mp3} | {get_duration(output_mp3)/60:.1f} دقيقة", flush=True)

    finally:
        cleanup_dir(work_dir)


if __name__ == "__main__":
    try:
        build_episode_audio(sys.argv[1], sys.argv[2])
    except Exception as e:
        notify_telegram(f"فشل story_audio: {e}", "error")
        sys.exit(1)
