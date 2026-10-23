import json, os, subprocess, tempfile, random, requests, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from config import VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR, PIXABAY_KEY
from video import _find_arabic_font, _shape, _wrap

SAFE_FALLBACKS = ["islamic mosque architecture", "kaaba mecca", "sand dunes desert", "starry night sky", "quran book"]
NATURE_VIDEO_QUERIES = ["waterfall nature", "birds flying sky", "ocean waves", "river flowing", "clouds sky"]
WATERMARK_TEXT = "ملاك المعرفة"
FORBIDDEN_TAGS = ["woman", "women", "girl", "female", "child", "boy", "man", "men", "people", "person", "face", "human", "body", "hand", "skin"]

def is_image_safe(tags_str):
    tags_list = [t.strip().lower() for t in str(tags_str).split(",")]
    return not any(forbidden in tags_list for forbidden in FORBIDDEN_TAGS)

def fetch_media(prompt: str, work_dir: Path, force_video=False) -> str:
    safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt)[:30]
    ext = ".mp4" if force_video else ".jpg"
    out_path = work_dir / f"bg_{safe_prompt}{ext}"
    if out_path.exists(): return str(out_path)

    if PIXABAY_KEY:
        try:
            if force_video:
                r = requests.get("https://pixabay.com/api/videos/", params={"key": PIXABAY_KEY, "q": prompt, "per_page": 15, "safesearch": "true"}, timeout=20)
                for hit in r.json().get("hits", []):
                    if is_image_safe(hit.get("tags", "")):
                        url = hit.get("videos", {}).get("medium", {}).get("url") or hit.get("videos", {}).get("small", {}).get("url")
                        if url:
                            out_path.write_bytes(requests.get(url, timeout=60).content)
                            return str(out_path)
            else:
                r = requests.get("https://pixabay.com/api/", params={"key": PIXABAY_KEY, "q": prompt, "image_type": "photo", "orientation": "vertical", "per_page": 15, "safesearch": "true"}, timeout=20)
                for hit in r.json().get("hits", []):
                    if is_image_safe(hit.get("tags", "")) and hit.get("largeImageURL"):
                        out_path.write_bytes(requests.get(hit["largeImageURL"], timeout=30).content)
                        return str(out_path)
        except Exception: pass

    # Fallback to image if video fails
    if force_video: return fetch_media("nature landscape", work_dir, False)
    
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10))
    img.save(out_path)
    return str(out_path)

def make_overlay(seg, width, height, work_dir, episode_title=""):
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()
    is_quran = seg.get("type") == "quran"
    
    # 🌟 1. عنوان الحلقة أعلى الشاشة
    if episode_title:
        try: font_title = ImageFont.truetype(font_path, 45, layout_engine=ImageFont.Layout.RAQM)
        except: font_title = ImageFont.truetype(font_path, 45)
        title_shaped = _shape(episode_title)
        bbox = draw.textbbox((0, 0), title_shaped, font=font_title)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        title_bg = Image.new("RGBA", (VIDEO_W, 90), (0, 0, 0, 160))
        overlay.paste(title_bg, (0, 20), title_bg)
        draw = ImageDraw.Draw(overlay)
        draw.text((x, 40), title_shaped, font=font_title, fill="#D4AF37", stroke_width=2, stroke_fill="black")

    # 🌟 2. لفظ الجلالة للقرآن
    if is_quran:
        try: font_allah = ImageFont.truetype(font_path, 400, layout_engine=ImageFont.Layout.RAQM)
        except: font_allah = ImageFont.truetype(font_path, 400)
        allah_shaped = _shape("ﷲ")
        bbox = draw.textbbox((0, 0), allah_shaped, font=font_allah)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, (VIDEO_H - (bbox[3] - bbox[1])) // 2), allah_shaped, font=font_allah, fill=(212, 175, 55, 50))

    # 🌟 3. نص الآية أو السرد
    if is_quran:
        try: font_main, font_display = ImageFont.truetype(font_path, 45, layout_engine=ImageFont.Layout.RAQM), ImageFont.truetype(font_path, 40, layout_engine=ImageFont.Layout.RAQM)
        except: font_main, font_display = ImageFont.truetype(font_path, 45), ImageFont.truetype(font_path, 40)
        color = ACCENT_COLOR
    else:
        try: font_main = ImageFont.truetype(font_path, 55, layout_engine=ImageFont.Layout.RAQM)
        except: font_main = ImageFont.truetype(font_path, 55)
        color = TEXT_COLOR

    shaped = _shape(seg.get("text", ""))
    lines = _wrap(shaped, font_main, VIDEO_W - 120, draw)
    line_h = int(font_main.size * 1.6)
    total_h = len(lines) * line_h
    y = max(150, (VIDEO_H - total_h) // 2)

    text_bg = Image.new("RGBA", (width, total_h + 60), (0, 0, 0, 140))
    overlay.paste(text_bg, (0, y - 30), text_bg)
    draw = ImageDraw.Draw(overlay)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_main)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y), line, font=font_main, fill=color, stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
        y += line_h

    # 🌟 4. اسم السورة ورقم الآية
    if is_quran and seg.get("display"):
        display_shaped = _shape(f"📖 {seg['display']}")
        bbox = draw.textbbox((0, 0), display_shaped, font=font_display)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y + 20), display_shaped, font=font_display, fill="#D4AF37", stroke_width=2, stroke_fill="black")

    # 5. Watermark
    try: font_wm = ImageFont.truetype(font_path, 30, layout_engine=ImageFont.Layout.RAQM)
    except: font_wm = ImageFont.truetype(font_path, 30)
    wm_shaped = _shape(WATERMARK_TEXT)
    bbox = draw.textbbox((0, 0), wm_shaped, font=font_wm)
    draw.text((VIDEO_W - (bbox[2] - bbox[0]) - 40, VIDEO_H - (bbox[3] - bbox[1]) - 40), wm_shaped, font=font_wm, fill=(212, 175, 55, 128))

    # 6. Teaser
    if seg.get("type") == "teaser":
        td = seg.get("teaser_data", {})
        strip_h = 340
        strip = Image.new("RGBA", (VIDEO_W, strip_h), (0, 0, 0, 220))
        overlay.paste(strip, (0, VIDEO_H - strip_h), strip)
        draw = ImageDraw.Draw(overlay)
        try: font_next, font_url = ImageFont.truetype(font_path, 42, layout_engine=ImageFont.Layout.RAQM), ImageFont.truetype(font_path, 32, layout_engine=ImageFont.Layout.RAQM)
        except: font_next, font_url = ImageFont.truetype(font_path, 42), ImageFont.truetype(font_path, 32)
        
        y = VIDEO_H - strip_h + 40
        for txt, font, color, offset in [
            ("⏭ الحلقة القادمة", font_next, ACCENT_COLOR, 0),
            (td.get("next_episode_title", ""), font_next, TEXT_COLOR, 70),
            (f"🌐 {td.get('url', '').replace('https://', '')}", font_url, ACCENT_COLOR, 150),
            (td.get("platform_name", "ملاك المعرفة"), font_url, TEXT_COLOR, 205)
        ]:
            if txt:
                bbox = draw.textbbox((0, 0), _shape(txt), font=font)
                draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y + offset), _shape(txt), font=font, fill=color, stroke_width=2, stroke_fill="black")

    overlay_path = work_dir / f"overlay_{seg['index']:03d}.png"
    overlay.save(overlay_path)
    return str(overlay_path)

def build_episode_video(audio_path, meta_path, output_mp4):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    segments = meta.get("segments", [])
    episode_title = meta.get("title", "") # 🌟 قراءة عنوان الحلقة
    work_dir = Path(tempfile.mkdtemp(prefix="ep_video_"))
    concat_lines = []

    for seg in segments:
        idx, duration, is_quran = seg["index"], seg["duration"], seg.get("type") == "quran"
        
        scene = random.choice(NATURE_VIDEO_QUERIES) if is_quran and random.random() < 0.5 else (seg.get("visual", "islamic mosque") if not is_quran else "quran book")
        bg_path = fetch_media(scene, work_dir, force_video=(is_quran and scene in NATURE_VIDEO_QUERIES))
        overlay_path = make_overlay(seg, VIDEO_W, VIDEO_H, work_dir, episode_title)
        
        out_seg = work_dir / f"seg_{idx:03d}.mp4"
        if bg_path.endswith(".mp4"):
            cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", bg_path, "-i", overlay_path, "-filter_complex", "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];[bg][1:v]overlay", "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-threads", "4", "-crf", "28", "-pix_fmt", "yuv420p", str(out_seg)]
        else:
            cmd = ["ffmpeg", "-y", "-loop", "1", "-i", bg_path, "-i", overlay_path, "-filter_complex", "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];[bg][1:v]overlay", "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-threads", "4", "-crf", "28", "-pix_fmt", "yuv420p", str(out_seg)]
        subprocess.run(cmd, check=True, capture_output=True)
        concat_lines.append(f"file '{out_seg}'")

    (work_dir / "concat.txt").write_text("\n".join(concat_lines), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "concat.txt"), "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", output_mp4], check=True, capture_output=True)

if __name__ == "__main__":
    build_episode_video(sys.argv[1], sys.argv[2], sys.argv[3])
