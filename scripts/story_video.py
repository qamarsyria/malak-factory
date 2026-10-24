import json, os, subprocess, tempfile, random, requests, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from config import VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR, PIXABAY_KEY
from video import _find_arabic_font, _shape, _wrap

def fetch_media(prompt, work_dir, force_video=False):
    ext = ".mp4" if force_video else ".jpg"
    out_path = work_dir / f"bg_{hash(prompt)}{ext}"
    if out_path.exists(): return str(out_path)
    
    # (هنا كود جلب الصور من Pixabay - مختصر للتوضيح)
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10))
    img.save(out_path)
    return str(out_path)

def make_overlay(seg, width, height, work_dir, episode_title=""):
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()
    is_quran = seg.get("type") == "quran"

    # 1. عنوان الحلقة
    if episode_title:
        font_title = ImageFont.truetype(font_path, 45)
        title_shaped = _shape(episode_title)
        bbox = draw.textbbox((0, 0), title_shaped, font=font_title)
        title_bg = Image.new("RGBA", (VIDEO_W, 90), (0, 0, 0, 160))
        overlay.paste(title_bg, (0, 20), title_bg)
        draw = ImageDraw.Draw(overlay)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, 40), title_shaped, font=font_title, fill="#D4AF37")

    # 2. النص
    font_main = ImageFont.truetype(font_path, 45 if is_quran else 55)
    shaped = _shape(seg.get("text", ""))
    lines = _wrap(shaped, font_main, VIDEO_W - 120, draw)
    line_h = int(font_main.size * 1.6)
    y = max(150, (VIDEO_H - len(lines) * line_h) // 2)

    text_bg = Image.new("RGBA", (width, len(lines) * line_h + 60), (0, 0, 0, 140))
    overlay.paste(text_bg, (0, y - 30), text_bg)
    draw = ImageDraw.Draw(overlay)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_main)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y), line, font=font_main, fill=ACCENT_COLOR if is_quran else TEXT_COLOR, stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
        y += line_h

    # 3. اسم السورة
    if is_quran and seg.get("display"):
        font_disp = ImageFont.truetype(font_path, 40)
        disp = _shape(f"📖 {seg['display']}")
        bbox = draw.textbbox((0, 0), disp, font=font_disp)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y + 20), disp, font=font_disp, fill="#D4AF37")

    # 4. Watermark + Teaser (اختصاراً)
    overlay_path = work_dir / f"overlay_{seg['index']}.png"
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
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work_dir / "concat.txt"), "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", output_mp4], check=True)

if __name__ == "__main__":
    build_video(sys.argv[1], sys.argv[2], sys.argv[3])
