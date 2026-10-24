import os, subprocess, tempfile, json, sys
from pathlib import Path
import requests
from config import QURAN_BASE, STORY_RECITER
from voice import generate as gemini_generate
from smart_director import direct_scene
from sfx_manager import fetch_sfx

TARGET_BR = "192k"
SFX_VOLUME = 0.05

def normalize(input_path, output_path):
    subprocess.run(["ffmpeg", "-y", "-i", input_path, "-ar", "44100", "-ac", "1", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-codec:a", "libmp3lame", "-b:a", TARGET_BR, output_path], check=True, capture_output=True)

def fetch_ayah_audio(surah, ayah, out_path):
    url = f"{QURAN_BASE}/{STORY_RECITER}/{surah:03d}{ayah:03d}.mp3"
    r = requests.get(url, timeout=60)
    if r.status_code == 200: Path(out_path).write_bytes(r.content)

def fetch_ayah_text(surah, ayah):
    try:
        r = requests.get(f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah}/ar.alafasy", timeout=10)
        if r.status_code == 200: return r.json()['data']['text']
    except: pass
    return ""

def get_duration(path):
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path], capture_output=True, text=True, timeout=10)
    try: return round(float(probe.stdout.strip()), 3) # دقة 3 أرقام عشرية
    except: return 0.0

def add_fade(input_path, output_path):
    dur = get_duration(input_path)
    subprocess.run(["ffmpeg", "-y", "-i", input_path, "-af", f"afade=t=in:st=0:d=0.1,afade=t=out:st={max(0, dur-0.15)}:d=0.15", "-c:a", "libmp3lame", "-b:a", "192k", output_path], check=True, capture_output=True)

def main(json_path, out_mp3):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_audio_"))
    parts, meta_segments, idx = [], [], 0

    for seg in ep.get("segments", []):
        seg_type = seg.get("type")
        raw_path = work_dir / f"raw_{idx:03d}.mp3"
        fade_path = work_dir / f"fade_{idx:03d}.mp3"
        norm_path = work_dir / f"norm_{idx:03d}.mp3"

        if seg_type in ("narration", "teaser"):
            direction = direct_scene(seg["text"]) if not seg.get("emotion_hint") else {"visual": "mosque", "sfx": "", "voice_style": seg["emotion_hint"]}
            gemini_generate(seg["text"], str(raw_path), voice="Charon", item_type="story", emotion_hint=direction.get("voice_style", ""))
            sfx = fetch_sfx(direction["sfx"]) if direction.get("sfx") else ""
            if sfx and os.path.exists(sfx):
                subprocess.run(["ffmpeg", "-y", "-i", str(raw_path), "-stream_loop", "-1", "-i", sfx, "-filter_complex", f"[1:a]volume={SFX_VOLUME}[sfx];[0:a][sfx]amix=inputs=2:duration=first:dropout_transition=2[out]", "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", str(norm_path)], check=True, capture_output=True)
                add_fade(str(norm_path), str(fade_path))
                final_seg = fade_path
            else:
                add_fade(str(raw_path), str(fade_path))
                final_seg = fade_path
            
            meta = {"index": idx, "type": seg_type, "text": seg["text"], "visual": direction.get("visual"), "duration": get_duration(str(final_seg))}
            if seg_type == "teaser": meta["teaser_data"] = {"next_episode_title": seg.get("next_episode_title", ""), "url": seg.get("url", ""), "platform_name": "ملاك المعرفة"}
            meta_segments.append(meta)
            parts.append(str(final_seg))

        elif seg_type == "quran":
            surah, ayahs = seg["surah"], seg["ayah_numbers"]
            if len(ayahs) == 1:
                fetch_ayah_audio(surah, ayahs[0], str(raw_path))
                txt = fetch_ayah_text(surah, ayahs[0])
                disp = f"{seg['surah_name']}: {ayahs[0]}"
            else:
                subs = []
                for i, a in enumerate(ayahs):
                    p = work_dir / f"sub_{i}.mp3"
                    fetch_ayah_audio(surah, a, str(p))
                    subs.append(str(p))
                (work_dir / "list.txt").write_text("\n".join([f"file '{s}'" for s in subs]))
                subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "list.txt"), "-c", "copy", str(raw_path)], check=True)
                disp = f"{seg['surah_name']}: {ayahs[0]}-{ayahs[-1]}"
            
            normalize(str(raw_path), str(norm_path))
            meta_segments.append({"index": idx, "type": "quran", "text": txt, "display": disp, "visual": "quran book", "duration": get_duration(str(norm_path))})
            parts.append(str(norm_path))

        idx += 1

    (work_dir / "final.txt").write_text("\n".join([f"file '{p}'" for p in parts]))
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "final.txt"), "-codec:a", "libmp3lame", "-b:a", TARGET_BR, out_mp3], check=True)
    
    meta_path = out_mp3.replace(".mp3", "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"episode": ep.get("episode_number", 1), "title": ep.get("title", ""), "total_duration": get_duration(out_mp3), "segments": meta_segments}, f, ensure_ascii=False, indent=2)
    print(f"✅ الحلقة: {out_mp3} | ⏱️ المدة: {get_duration(out_mp3)/60:.1f} دقيقة")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
