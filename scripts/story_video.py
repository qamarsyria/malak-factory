import os, re, sys, json, math, subprocess, tempfile, random
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR, PIXABAY_KEY, BG_VIDEOS_DIR, BG_IMAGES_DIR
from utilities import cleanup_dir

def _find_arabic_font():
    candidates = ["/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf", "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf", "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-BoldItalic.ttf"]
    for c in candidates:
        if os.path.exists(c): return c
    r = subprocess.run(["fc-list", ":lang=ar", "--format=%{file}\n"], capture_output=True, text=True)
    files = [f.strip() for f in r.stdout.splitlines() if f.strip()]
    return files[0] if files else ""

def _shape(text):
    return arabic_reshaper.reshape(str(text))

def _wrap(text, font, max_w, draw):
    words = text.split()
    lines, current = [], ""
    for w in words:
        trial = (current + " " + w).strip() if current else w
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_w: current = trial
        else:
            if current: lines.append(current)
            current = w
    if current: lines.append(current)
    return lines

def get_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=15)
    try: return float(r.stdout.strip())
    except: return 0.0

def _find_local(query):
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    for base in (Path(BG_VIDEOS_DIR), Path(BG_IMAGES_DIR)):
        if not base.exists(): continue
        for f in base.glob(f"{slug}*"):
            if f.suffix.lower() in (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png") and f.stat().st_size > 10000: return str(f)
    return None

def fetch_media(query, work_dir, used_urls):
    local = _find_local(query)
    if local: return local
    if PIXABAY_KEY:
        try:
            r = requests.get("https://pixabay.com/api/videos/", params={"key": PIXABAY_KEY, "q": query, "video_type": "film", "safesearch": "true", "per_page": 10, "min_width": 1080}, timeout=20)
            if r.status_code == 200:
                random.shuffle(r.json().get("hits", []))
                for hit in r.json().get("hits", []):
                    url = hit.get("videos", {}).get("medium", {}).get("url") or hit.get("videos", {}).get("small", {}).get("url")
                    if not url or url in used_urls: continue
                    slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:30]
                    out = work_dir / f"vid_{slug}_{hit['id']}.mp4"
                    try:
                        with open(out, "wb") as f:
                            for chunk in requests.get(url, timeout=60, stream=True).iter_content(8192): f.write(chunk)
                        used_urls.add(url)
                        return str(out)
                    except: pass
        except: pass
    out = work_dir / "black.jpg"
    if not out.exists(): Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10)).save(out)
    return str(out)

def _fit_text(text, font_path, max_width, max_height, start_size, min_size):
    dummy = Image.new("RGB", (10, 10)); ddraw = ImageDraw.Draw(dummy)
    for size in range(start_size, min_size - 1, -2):
        try: font = ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.RAQM)
        except: font = ImageFont.truetype(font_path, size)
        lines = _wrap(_shape(text), font, max_width, ddraw)
        if len(lines) * int(size * 1.6) <= max_height: return font, lines, int(size * 1.6)
    font = ImageFont.truetype(font_path, min_size)
    return font, _wrap(_shape(text), font, max_width, ddraw), int(min_size * 1.6)

def _draw_centered(draw, y, text, font, fill, stroke_w, stroke_fill):
    shaped = _shape(text)
    bbox = draw.textbbox((0, 0), shaped, font=font)
    x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), shaped, font=font, fill=fill, stroke_width=stroke_w, stroke_fill=stroke_fill)

def make_caption_overlay(chunk_text, display_ref, is_quran, out_path):
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()
    
    # ═══ إصلاح النص المشوه: إزالة أي رموز غريبة ═══
    chunk_text = re.sub(r'[*#@_\[\]\(\)]', '', chunk_text)
    chunk_text = re.sub(r'[a-zA-Z]', '', chunk_text).strip()
    if not chunk_text and not is_quran: 
        overlay.save(out_path); return

    color = ACCENT_COLOR if is_quran else TEXT_COLOR
    display_text = chunk_text + (f"\n﴿ {display_ref} ﴾" if is_quran and display_ref else "")
    font, lines, lh = _fit_text(display_text, font_path, VIDEO_W - 120, VIDEO_H - 400, 70, 45)
    
    total_h = len(lines) * lh
    y = max(200, (VIDEO_H - total_h) // 2)
    
    for i, line in enumerate(lines):
        line_color = color if i < len(lines)-1 or not is_quran else "#D4AF37"
        _draw_centered(draw, y, line, font, line_color, 6 if not is_quran else 8, "black")
        y += lh
        
    try: wm_font = ImageFont.truetype(font_path, 40, layout_engine=ImageFont.Layout.RAQM)
    except: wm_font = ImageFont.truetype(font_path, 40)
    wm_text = _shape("ملاك المعرفة")
    wm_bbox = draw.textbbox((0,0), wm_text, font=wm_font)
    draw.text((VIDEO_W - (wm_bbox[2] - wm_bbox[0]) - 40, VIDEO_H - 80), wm_text, font=wm_font, fill="#D4AF37", stroke_width=4, stroke_fill="black")
        
    overlay.save(out_path)

def build_video(audio_path, meta_path, output_mp4):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_vid_"))
    concat_lines, used_urls = [], set()

    try:
        for i, seg in enumerate(meta["segments"]):
            seg_duration = sum(c["duration"] for c in seg.get("chunks", []))
            if seg_duration <= 0: continue
            
            num_bgs = max(1, math.ceil(seg_duration / 15))
            bg_files = [fetch_media("islamic nature", work_dir, used_urls) for _ in range(num_bgs)]
            bg_duration = seg_duration / num_bgs
            processed_bgs = []
            
            for b_idx, bg in enumerate(bg_files):
                temp_bg = work_dir / f"bg_{i}_{b_idx}.mp4"
                is_img = bg.endswith(('.jpg', '.jpeg', '.png'))
                cmd_bg = ["ffmpeg", "-y"] + (["-loop", "1", "-i", bg] if is_img else ["-stream_loop", "-1", "-i", bg]) + ["-t", str(bg_duration), "-vf", f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,crop={VIDEO_W}:{VIDEO_H},fps=24,format=yuv420p,setsar=1", "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p", str(temp_bg)]
                subprocess.run(cmd_bg, check=True, capture_output=True)
                processed_bgs.append(temp_bg)
                
            bg_concat_txt = work_dir / f"bg_concat_{i}.txt"
            with open(bg_concat_txt, "w") as f:
                for p_bg in processed_bgs: f.write(f"file '{p_bg.resolve()}'\n")
            bg_video_path = work_dir / f"bg_{i}.mp4"
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(bg_concat_txt), "-c", "copy", str(bg_video_path)], check=True, capture_output=True)

            is_quran = seg.get("type") == "quran"
            display_ref = seg.get("display", "")
            chunks = seg.get("chunks", [])
            text_inputs, text_filters, actual_chunks = [], [], 0
            
            for c_idx, chunk_data in enumerate(chunks):
                c_text = chunk_data.get("text", "").replace("اللله", "الله").replace("الرَّحْمَان", "الرَّحْمَن") # إرجاع النص الأصلي للعرض
                c_dur = chunk_data.get("duration", 0)
                if not c_text.strip(): continue
                
                overlay_png = work_dir / f"ov_{i}_{c_idx}.png"
                make_caption_overlay(c_text, display_ref, is_quran, overlay_png)
                text_inputs.extend(["-loop", "1", "-i", str(overlay_png)])
                text_filters.append(f"[{actual_chunks}:v]trim=start=0:end={c_dur},setpts=PTS-STARTPTS,format=rgba[t{actual_chunks}]")
                actual_chunks += 1
            
            text_video_path = work_dir / f"text_{i}.mov"
            if actual_chunks == 0:
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black@0.0:s={VIDEO_W}x{VIDEO_H}:d={seg_duration},format=rgba", "-c:v", "qtrle", "-pix_fmt", "rgba", str(text_video_path)], check=True, capture_output=True)
            else:
                text_filters.append(f"{''.join(f'[t{k}]' for k in range(actual_chunks))}concat=n={actual_chunks}:v=1:a=0[text]")
                subprocess.run(["ffmpeg", "-y"] + text_inputs + ["-filter_complex", ";".join(text_filters), "-map", "[text]", "-t", str(seg_duration), "-c:v", "qtrle", "-pix_fmt", "rgba", str(text_video_path)], check=True, capture_output=True)

            out_seg = work_dir / f"seg_{i}.mp4"
            subprocess.run(["ffmpeg", "-y", "-i", str(bg_video_path), "-i", str(text_video_path), "-filter_complex", "[0:v][1:v]overlay", "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p", str(out_seg)], check=True, capture_output=True)
            concat_lines.append(f"file '{out_seg}'")

        audio_dur = get_duration(audio_path)
        (work_dir / "concat.txt").write_text("\n".join(concat_lines), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "concat.txt"), "-i", audio_path, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-t", str(audio_dur), str(output_mp4)], check=True, capture_output=True)
        print(f"✅ فيديو: {output_mp4}", flush=True)
    finally:
        cleanup_dir(work_dir)

if __name__ == "__main__":
    if len(sys.argv) < 4: sys.exit(1)
    build_video(sys.argv[1], sys.argv[2], sys.argv[3])
