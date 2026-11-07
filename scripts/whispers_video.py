import os, sys, re, json, math, subprocess, tempfile, random, requests
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from config import VIDEO_W, VIDEO_H, PIXABAY_KEY
from utilities import cleanup_dir

def get_visual_query(category):
    if category == "romantic":
        return random.choice(["rose garden butterflies", "romantic sunset love", "flowers blooming romantic"])
    elif category == "dua":
        return random.choice(["islamic mosque sunset", "praying hands islamic", "quran book glowing"])
    else:
        return random.choice(["nature waterfall", "rain forest", "birds in tree"])

def fetch_video(query, work_dir):
    if not PIXABAY_KEY: return ""
    try:
        r = requests.get("https://pixabay.com/api/videos/", params={"key": PIXABAY_KEY, "q": query, "video_type": "film", "per_page": 10}, timeout=20)
        if r.status_code == 200:
            hits = r.json().get("hits", [])
            if hits:
                url = hits[0].get("videos", {}).get("medium", {}).get("url")
                if url:
                    slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:20]
                    out = work_dir / f"{slug}_{hits[0]['id']}.mp4"
                    with open(out, "wb") as f:
                        for chunk in requests.get(url, timeout=60, stream=True).iter_content(8192): f.write(chunk)
                    return str(out)
    except: pass
    return ""

def build_whisper_video(audio_path, json_path, out_mp4):
    ep = json.loads(Path(json_path).read_text(encoding="utf-8"))
    category = ep.get("category", "romantic")
    
    work_dir = Path(tempfile.mkdtemp(prefix="whisper_vid_"))
    query = get_visual_query(category)
    video_path = fetch_video(query, work_dir)
    
    if not video_path:
        black = work_dir / "black.jpg"
        Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10)).save(black)
        video_path = str(black)
        is_img = True
    else:
        is_img = False
        
    bg_video = work_dir / "bg.mp4"
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path], capture_output=True, text=True).stdout.strip()
    duration = float(dur or 10.0)
    
    cmd = ["ffmpeg", "-y"] + (["-loop", "1"] if is_img else ["-stream_loop", "-1"]) + ["-i", video_path, "-t", str(duration), "-vf", f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,crop={VIDEO_W}:{VIDEO_H},fps=24,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", str(bg_video)]
    subprocess.run(cmd, check=True, capture_output=True)
    
    subprocess.run(["ffmpeg", "-y", "-i", str(bg_video), "-i", audio_path, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-c:a", "aac", "-t", str(duration), str(out_mp4)], check=True, capture_output=True)
    print(f"✅ فيديو: {out_mp4} | الخلفية: {query}")
    cleanup_dir(work_dir)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 4: sys.exit(1)
    build_whisper_video(sys.argv[1], sys.argv[2], sys.argv[3])
