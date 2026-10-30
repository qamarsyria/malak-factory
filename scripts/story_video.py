import os
import re
import sys
import json
import subprocess
import tempfile
import random
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W,
    ACCENT_COLOR, PIXABAY_KEY, BG_VIDEOS_DIR, BG_IMAGES_DIR
)
from video import _find_arabic_font, _shape, _wrap
from utilities import cleanup_dir, notify_telegram

def split_text_for_captions(text, max_words=4):
    """تقسيم النص إلى جمل صغيرة (4 كلمات) لعرضها ديناميكياً."""
    words = text.split()
    chunks = []
    current = []
    for w in words:
        current.append(w)
        if len(current) >= max_words or any(p in w for p in [".", "؟", "!", "،"]):
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks

def _fit_text(text, font_path, max_width, max_height, start_size, min_size):
    dummy = Image.new("RGB", (10, 10))
    ddraw = ImageDraw.Draw(dummy)
    for size in range(start_size, min_size - 1, -2):
        try: 
            font = ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.RAQM)
        except: 
            font = ImageFont.truetype(font_path, size)
        lines = _wrap(_shape(text), font, max_width, ddraw)
        if len(lines) * int(size * 1.6) <= max_height:
            return font, lines, int(size * 1.6)
    font = ImageFont.truetype(font_path, min_size)
    return font, _wrap(_shape(text), font, max_width, ddraw), int(min_size * 1.6)

def _draw_centered(draw, y, text, font, fill, stroke_w, stroke_fill):
    shaped = _shape(text)
    bbox = draw.textbbox((0, 0), shaped, font=font)
    x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), shaped, font=font, fill=fill, stroke_width=stroke_w, stroke_fill=stroke_fill)

def make_caption_overlay(chunk_text, seg_type, out_path):
    """إنشاء صورة شفافة تحتوي على جزء صغير من النص فقط."""
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()
    
    is_quran = seg_type == "quran"
    color = ACCENT_COLOR if is_quran else TEXT_COLOR
    font, lines, lh = _fit_text(chunk_text, font_path, VIDEO_W - 120, VIDEO_H - 400, 70, 45)
    
    total_h = len(lines) * lh
    y = max(200, (VIDEO_H - total_h) // 2)
    
    # خلفية سوداء شفافة للنص ليكون مقروءاً
    bg = Image.new("RGBA", (VIDEO_W, total_h + 60), (0, 0, 0, 170))
    overlay.paste(bg, (0, y - 30), bg)
    
    for line in lines:
        _draw_centered(draw, y, line, font, color, TEXT_STROKE_W, TEXT_STROKE)
        y += lh
        
    overlay.save(out_path)

def fetch_media(query, work_dir):
    if not PIXABAY_KEY: 
        out = work_dir / "black.jpg"
        Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10)).save(out)
        return str(out)
    try:
        r = requests.get("https://pixabay.com/api/videos/", params={"key": PIXABAY_KEY, "q": query, "per_page": 3}, timeout=20)
        hits = r.json().get("hits", [])
        if hits:
            url = hits[0]["videos"]["medium"]["url"]
            out = work_dir / f"bg_{random.randint(1,999)}.mp4"
            out.write_bytes(requests.get(url, timeout=30).content)
            return str(out)
    except: pass
    out = work_dir / f"black_{random.randint(1,999)}.jpg"
    Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10)).save(out)
    return str(out)

def build_video(audio_path, meta_path, output_mp4):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_vid_"))
    concat_lines = []

    try:
        for i, seg in enumerate(meta["segments"]):
            duration = seg["duration"]
            if duration <= 0: continue
            
            print(f"🎬 مقطع {i+1}: {seg['type']} ({duration:.1f}s)", flush=True)
            
            media = fetch_media("islamic mosque nature", work_dir)
            
            text = seg.get("text", "")
            chunks = split_text_for_captions(text, max_words=4)
            if not chunks: chunks = [""]
            
            time_per_chunk = duration / len(chunks)
            
            # بناء أوامر ffmpeg (تم إصلاح فهرسة المدخلات هنا)
            inputs = ["-stream_loop", "-1", "-i", media]
            filters = [f"[0:v]scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,crop={VIDEO_W}:{VIDEO_H}[bg0]"]
            
            current_video = "[bg0]"
            input_idx = 1 # المدخل 0 هو الفيديو الخلفي
            
            for c_idx, chunk in enumerate(chunks):
                if not chunk.strip(): continue
                overlay_png = work_dir / f"ov_{i}_{c_idx}.png"
                make_caption_overlay(chunk, seg["type"], overlay_png)
                
                inputs.extend(["-i", str(overlay_png)])
                
                start_t = c_idx * time_per_chunk
                end_t = (c_idx + 1) * time_per_chunk
                
                next_video = f"[v{i}_{c_idx}]"
                filters.append(f"{current_video}[{input_idx}:v]overlay=enable='between(t,{start_t:.3f},{end_t:.3f})'{next_video}")
                current_video = next_video
                input_idx += 1 # تحديث رقم المدخل للصورة التالية
            
            out_seg = work_dir / f"seg_{i}.mp4"
            cmd = ["ffmpeg", "-y"] + inputs + [
                "-filter_complex", ";".join(filters),
                "-map", current_video,
                "-t", str(duration), "-r", "24",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                str(out_seg)
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            concat_lines.append(f"file '{out_seg}'")

        (work_dir / "concat.txt").write_text("\n".join(concat_lines), encoding="utf-8")
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "concat.txt"),
            "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-shortest", output_mp4
        ], check=True, capture_output=True)
        
        print(f"✅ فيديو: {output_mp4}", flush=True)
    finally:
        cleanup_dir(work_dir)

if __name__ == "__main__":
    if len(sys.argv) < 4: sys.exit(1)
    build_video(sys.argv[1], sys.argv[2], sys.argv[3])
