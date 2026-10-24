import json, os, subprocess, tempfile, random, requests, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from config import VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR, PIXABAY_KEY
from video import _find_arabic_font, _shape, _wrap

FORBIDDEN_TAGS = ["woman", "women", "girl", "female", "child", "boy", "man", "men", "people", "person", "face", "human", "body", "hand", "skin"]

def is_image_safe(tags_str):
    tags_list = [t.strip().lower() for t in str(tags_str).split(",")]
    return not any(forbidden in tags_list for forbidden in FORBIDDEN_TAGS)

def fetch_media(prompt: str, work_dir: Path) -> str:
    safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt)[:30]
    out_path = work_dir / f"bg_{safe_prompt}.jpg"
    if out_path.exists(): return str(out_path)

    if PIXABAY_KEY:
        try:
            r = requests.get("https://pixabay.com/api/", params={
                "key": PIXABAY_KEY, "q": prompt, "image_type": "photo",
                "orientation": "vertical", "per_page": 15, "safesearch": "true", "min_width": 1080
            }, timeout=20)
            for hit in r.json().get("hits", []):
                if is_image_safe(hit.get("tags", "")) and hit.get("largeImageURL"):
                    out_path.write_bytes(requests.get(hit["largeImageURL"], timeout=30).content)
                    return str(out_path)
        except Exception: pass

    img = Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10))
    img.save(out_path)
    return str(out_path)

def make_overlay(seg, width, height, work_dir, episode_title=""):
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()
    is_quran = seg.get("type") == "quran"

    # 1. عنوان الحلقة الثابت في الأعلى
    if episode_title:
        try: font_title = ImageFont.truetype(font_path, 45, layout_engine=ImageFont.Layout.RAQM)
        except: font_title = ImageFont.truetype(font_path, 45)
        title_shaped = _shape(episode_title)
        bbox = draw.textbbox((0, 0), title_shaped, font=font_title)
        title_bg = Image.new("RGBA", (VIDEO_W, 90), (0, 0, 0, 160))
        overlay.paste(title_bg, (0, 20), title_bg)
        draw = ImageDraw.Draw(overlay)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, 40), title_shaped, font=font_title, fill="#D4AF37", stroke_width=2, stroke_fill="black")

    # 2. النص (سرد أو آية أو أوترو)
    if is_quran:
        try: font_main, font_disp = ImageFont.truetype(font_path, 45, layout_engine=ImageFont.Layout.RAQM), ImageFont.truetype(font_path, 40, layout_engine=ImageFont.Layout.RAQM)
        except: font_main, font_disp = ImageFont.truetype(font_path, 45), ImageFont.truetype(font_path, 40)
        color = ACCENT_COLOR
    else:
        try: font_main = ImageFont.truetype(font_path, 55, layout_engine=ImageFont.Layout.RAQM)
        except: font_main = ImageFont.truetype(font_path, 55)
        color = TEXT_COLOR

    shaped = _shape(seg.get("text", ""))
    lines = _wrap(shaped, font_main, VIDEO_W - 120, draw)
    line_h = int(font_main.size * 1.6)
    y = max(150, (VIDEO_H - len(lines) * line_h) // 2)

    text_bg = Image.new("RGBA", (width, len(lines) * line_h + 60), (0, 0, 0, 140))
    overlay.paste(text_bg, (0, y - 30), text_bg)
    draw = ImageDraw.Draw(overlay)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_main)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y), line, font=font_main, fill=color, stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
        y += line_h

    # 3. اسم السورة والآية
    if is_quran and seg.get("display"):
        disp = _shape(f"📖 {seg['display']}")
        bbox = draw.textbbox((0, 0), disp, font=font_disp)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y + 20), disp, font=font_disp, fill="#D4AF37", stroke_width=2, stroke_fill="black")

    # 4. العلامة المائية
    try: font_wm = ImageFont.truetype(font_path, 30, layout_engine=ImageFont.Layout.RAQM)
    except: font_wm = ImageFont.truetype(font_path, 30)
    wm_shaped = _shape("ملاك المعرفة")
    bbox = draw.textbbox((0, 0), wm_shaped, font=font_wm)
    draw.text((VIDEO_W - (bbox[2] - bbox[0]) - 40, VIDEO_H - 80), wm_shaped, font=font_wm, fill=(212, 175, 55, 128))

    # 5. شريط الـ Teaser
    if seg.get("type") == "teaser" and seg.get("teaser_data"):
        td = seg["teaser_data"]
        strip_h = 340
        strip = Image.new("RGBA", (VIDEO_W, strip_h), (0, 0, 0, 220))
        overlay.paste(strip, (0, VIDEO_H - strip_h), strip)
        draw = ImageDraw.Draw(overlay)
        try: font_next, font_url = ImageFont.truetype(font_path, 42, layout_engine=ImageFont.Layout.RAQM), ImageFont.truetype(font_path, 32, layout_engine=ImageFont.Layout.RAQM)
        except: font_next, font_url = ImageFont.truetype(font_path, 42), ImageFont.truetype(font_path, 32)
        
        y = VIDEO_H - strip_h + 40
        elements = [
            ("⏭ الحلقة القادمة", font_next, ACCENT_COLOR, 0),
            (td.get("next_episode_title", ""), font_next, TEXT_COLOR, 70),
            (f"🌐 {td.get('url', '').replace('https://', '')}", font_url, ACCENT_COLOR, 150),
            (td.get("platform_name", "ملاك المعرفة"), font_url, TEXT_COLOR, 205)
        ]
        for txt, font, color, offset in elements:
            if txt:
                bbox = draw.textbbox((0, 0), _shape(txt), font=font)
                draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y + offset), _shape(txt), font=font, fill=color, stroke_width=2, stroke_fill="black")

    overlay_path = work_dir / f"overlay_{seg['index']:03d}.png"
    overlay.save(overlay_path)
    return str(overlay_path)

def build_video(audio_path, meta_path, output_mp4):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_video_"))
    concat_lines = []

    for seg in meta["segments"]:
        # 🌟 هنا السر: نأخذ المدة الدقيقة من ملف الميتا
        duration = seg["duration"]
        
        bg_path = fetch_media(seg.get("visual", "mosque"), work_dir)
        overlay = make_overlay(seg, VIDEO_W, VIDEO_H, work_dir, meta.get("title", ""))
        out_seg = work_dir / f"seg_{seg['index']:03d}.mp4"

        # 🌟 إجبار معدل الإطارات على 30 لضمان عدم فقدان أي جزء من الثانية
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", bg_path, "-i", overlay,
            "-filter_complex", "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];[bg][1:v]overlay",
            "-t", str(duration), "-r", "30", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p", str(out_seg)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        concat_lines.append(f"file '{out_seg}'")

    (work_dir / "concat.txt").write_text("\n".join(concat_lines))
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "concat.txt"),
        "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", output_mp4
    ], check=True)

if __name__ == "__main__":
    build_video(sys.argv[1], sys.argv[2], sys.argv[3])
