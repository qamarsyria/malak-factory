import os
import re
import sys
import json
import math
import subprocess
import tempfile
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import VIDEO_W, VIDEO_H, TEXT_COLOR, ACCENT_COLOR
from utilities import cleanup_dir

WHISPERS_VIDEOS_DIR = _REPO_ROOT / "assets" / "whispers_videos"

def get_random_whisper_video():
    if not WHISPERS_VIDEOS_DIR.exists():
        print("⚠️ مجلد فيديوهات همسات ملك غير موجود!")
        return None
    videos = list(WHISPERS_VIDEOS_DIR.glob("*.mp4")) + list(WHISPERS_VIDEOS_DIR.glob("*.mov"))
    if not videos:
        print("⚠️ لا توجد فيديوهات في مجلد همسات ملك!")
        return None
    return random.choice(videos)

def get_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=15)
    try: return float(r.stdout.strip())
    except: return 0.0

def build_whisper_video(audio_path, out_mp4):
    work = Path(tempfile.mkdtemp(prefix="whisper_video_"))
    
    bg_video = get_random_whisper_video()
    if not bg_video:
        print("❌ لا توجد فيديوهات خلفية!")
        return False
    
    audio_dur = get_duration(audio_path)
    processed_bg = work / "bg.mp4"
    
    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(bg_video),
        "-t", str(audio_dur), "-vf", f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,crop={VIDEO_W}:{VIDEO_H},fps=24,format=yuv420p,setsar=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-pix_fmt", "yuv420p", str(processed_bg)
    ], check=True, capture_output=True)
    
    subprocess.run([
        "ffmpeg", "-y", "-i", str(processed_bg), "-i", audio_path,
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-t", str(audio_dur), str(out_mp4)
    ], check=True, capture_output=True)
    
    print(f"✅ تم إنشاء فيديو همسات ملك: {out_mp4}")
    cleanup_dir(work)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(1)
    build_whisper_video(sys.argv[1], sys.argv[2])
