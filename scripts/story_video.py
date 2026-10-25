import json, os, subprocess, tempfile, random, requests, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from config import VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR, PIXABAY_KEY
from video import _find_arabic_font, _shape, _wrap

FORBIDDEN_TAGS = ["woman", "women", "girl", "female", "child", "boy", "man", "men", "people", "person", "face", "human", "body", "hand", "skin"]
def is_image_safe(tags_str):
    tags_list = [t.strip().lower() for t in str(tags_str).split(",")]
    return not any(forbidden in tags_list for forbidden in FORBIDDEN_TAGS)

def fetch_media(prompt, work_dir):
    safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt)[:30]
    out_path = work_dir / f"bg_{safe_prompt}.jpg"
    if out_path.exists(): return str(out_path)
    if PIXABAY_KEY:
        try:
            r = requests.get("https://pixabay.com/api/", params={"key": PIXABAY_KEY, "q": prompt, "image_type": "photo", "orientation": "vertical", "per_page": 15, "safesearch": "true", "min_width": 1080}, timeout=20)
            for hit in r.json().get("hits", []):
                if is_image_safe(hit.get("tags", "")) and hit.get("largeImageURL"):
                    out_path.write_bytes(requests.get(hit["largeImageURL"], timeout=30).content)
                    return str(out_path)
        except: pass
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10))
    img.save(out_path)
    return str(out_path)

def make_overlay(seg, width, height, work_dir, episode_title=""):
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()
    is_quran = seg.get("type") == "quran"
    text = seg.get("text", "")

    if episode_title:
        try: font_title = ImageFont.truetype(font_path, 45, layout_engine=ImageFont.Layout.RAQM)
        except: font_title = ImageFont.truetype(font_path, 45)
        title_shaped = _shape(episode_title)
        bbox = draw.textbbox((0, 0), title_shaped, font=font_title)
        title_bg = Image.new("RGBA", (VIDEO_W, 90), (0, 0, 0, 160))
        overlay.paste(title_bg, (0, 20), title_bg)
        draw = ImageDraw.Draw(overlay)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, 40), title_shaped, font=font_title, fill="#D4AF37", stroke_width=2, stroke_fill="black")

    # 🌟 الحل الجذري: تصغير حجم الخط تلقائياً ليتسع النص
    max_height = VIDEO_H - 400
    max_width = VIDEO_W - 120
    start_size = 45 if is_quran else 55
    color = ACCENT_COLOR if is_quran else TEXT_COLOR
    
    dummy_img = Image.new("RGB", (10,10))
    dummy_draw = ImageDraw.Draw(dummy_img)
    
    final_font, final_lines, final_line_h = None, [], 0
    for size in range(start_size, 24, -2):
        try: font = ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.RAQM)
        except: font = ImageFont.truetype(font_path, size)
        shaped = _shape(text)
        lines = _wrap(shaped, font, max_width, dummy_draw)
        line_h = int(size * 1.6)
        if len(lines) * line_h <= max_height:
            final_font, final_lines, final_line_h = font, lines, line_h
            break
            
    if not final_font:
        final_font = ImageFont.truetype(font_path, 24)
        final_lines = _wrap(_shape(text), final_font, max_width, dummy_draw)
        final_line_h = int(24 * 1.6)

    total_h = len(final_lines) * final_line_h
    y = max(150, (VIDEO_H - total_h) // 2)

    text_bg = Image.new("RGBA", (width, total_h + 60), (0, 0, 0, 140))
    overlay.paste(text_bg, (0, y - 30), text_bg)
    draw = ImageDraw.Draw(overlay)

    for line in final_lines:
        bbox = draw.textbbox((0, 0), line, font=final_font)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y), line, font=final_font, fill=color, stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
        y += final_line_h

    if is_quran and seg.get("display"):
        try: font_disp = ImageFont.truetype(font_path, 40, layout_engine=ImageFont.Layout.RAQM)
        except: font_disp = ImageFont.truetype(font_path, 40)
        disp = _shape(f"📖 {seg['display']}")
        bbox = draw.textbbox((0, 0), disp, font=font_disp)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y + 20), disp, font=font_disp, fill="#D4AF37", stroke_width=2, stroke_fill="black")

    try: font_wm = ImageFont.truetype(font_path, 30, layout_engine=ImageFont.Layout.RAQM)
    except: font_wm = ImageFont.truetype(font_path, 30)
    wm_shaped = _shape("ملاك المعرفة")
    bbox = draw.textbbox((0, 0), wm_shaped, font=font_wm)
    draw.text((VIDEO_W - (bbox[2] - bbox[0]) - 40, VIDEO_H - 80), wm_shaped, font=font_wm, fill=(212, 175, 55, 128))

    if seg.get("type") == "teaser" and seg.get("teaser_data"):
        td = seg["teaser_data"]
        strip_h = 340
        strip = Image.new("RGBA", (VIDEO_W, strip_h), (0, 0, 0, 220))
        overlay.paste(strip, (0, VIDEO_H - strip_h), strip)
        draw = ImageDraw.Draw(overlay)
        try: font_next, font_url = ImageFont.truetype(font_path, 42, layout_engine=ImageFont.Layout.RAQM), ImageFont.truetype(font_path, 32, layout_engine=ImageFont.Layout.RAQM)
        except: font_next, font_url = ImageFont.truetype(font_path, 42), ImageFont.truetype(font_path, 32)
        
        y_td = VIDEO_H - strip_h + 40
        elements = [("⏭ الحلقة القادمة", font_next, ACCENT_COLOR, 0), (td.get("next_episode_title", ""), font_next, TEXT_COLOR, 70), (f"🌐 {td.get('url', '').replace('https://', '')}", font_url, ACCENT_COLOR, 150)]
        for txt, font, c, offset in elements:
            if txt:
                bbox = draw.textbbox((0, 0), _shape(txt), font=font)
                draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y_td + offset), _shape(txt), font=font, fill=c, stroke_width=2, stroke_fill="black")

    overlay_path = work_dir / f"overlay_{seg['index']:03d}.png"
    overlay.save(overlay_path)
    return str(overlay_path)

def build_video(audio_path, meta_path, output_mp4):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_video_"))
    concat_lines = []

    for seg in meta["segments"]:
        duration = seg["duration"]
        bg_path = fetch_media(seg.get("visual", "mosque"), work_dir)
        overlay = make_overlay(seg, VIDEO_W, VIDEO_H, work_dir, meta.get("title", ""))
        out_seg = work_dir / f"seg_{seg['index']:03d}.mp4"

        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", bg_path, "-i", overlay, "-filter_complex", "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];[bg][1:v]overlay", "-t", str(duration), "-r", "24", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "33", "-pix_fmt", "yuv420p", str(out_seg)]
        subprocess.run(cmd, check=True, capture_output=True)
        concat_lines.append(f"file '{out_seg}'")

    last_seg_path = work_dir / f"seg_{meta['segments'][-1]['index']:03d}.mp4"
    concat_lines.append(f"file '{last_seg_path}'")

    (work_dir / "concat.txt").write_text("\n".join(concat_lines))
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "concat.txt"), "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-shortest", output_mp4], check=True)

if __name__ == "__main__":
    build_video(sys.argv[1], sys.argv[2], sys.argv[3])
