import json, os, subprocess, tempfile, sys, requests
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from config import VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR, PIXABAY_KEY
from video import _find_arabic_font, _shape, _wrap
from utilities import cleanup_dir, notify_telegram

FORBIDDEN_TAGS = {
    "woman", "women", "girl", "female", "child", "boy", "man", "men",
    "people", "person", "face", "human", "body", "hand", "skin",
    "crowd", "family", "couple", "wedding", "dance", "dancing",
    "worship", "idol", "temple", "church", "cross", "crucifix", "statue",
    "alcohol", "wine", "beer", "drunk", "pig", "dog", "bride", "groom",
    "nude", "naked", "bikini", "lingerie", "kiss", "romance",
}


def is_image_safe(tags_str):
    tags = {t.strip().lower() for t in str(tags_str).replace(",", " ").split()}
    return not (tags & FORBIDDEN_TAGS)


def fetch_media(prompt, work_dir):
    safe = "".join(c if c.isalnum() else "_" for c in prompt)[:30]
    out = work_dir / f"bg_{safe}.jpg"
    if out.exists():
        return str(out)
    if PIXABAY_KEY:
        try:
            r = requests.get("https://pixabay.com/api/", params={
                "key": PIXABAY_KEY, "q": prompt, "image_type": "photo",
                "orientation": "vertical", "per_page": 20,
                "safesearch": "true", "min_width": 1080,
            }, timeout=20)
            for hit in r.json().get("hits", []):
                if is_image_safe(hit.get("tags", "")) and hit.get("largeImageURL"):
                    img = requests.get(hit["largeImageURL"], timeout=30)
                    if img.status_code == 200 and len(img.content) > 10000:
                        out.write_bytes(img.content)
                        return str(out)
        except Exception:
            pass
    Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10)).save(out)
    return str(out)


def _fit_text(text, font_path, max_width, max_height, start_size, min_size=22):
    """يختار أصغر حجم يلائم."""
    dummy = Image.new("RGB", (10, 10))
    ddraw = ImageDraw.Draw(dummy)
    for size in range(start_size, min_size - 1, -2):
        try:
            font = ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            font = ImageFont.truetype(font_path, size)
        lines = _wrap(_shape(text), font, max_width, ddraw)
        if len(lines) * int(size * 1.6) <= max_height:
            return font, lines, int(size * 1.6)
    font = ImageFont.truetype(font_path, min_size)
    return font, _wrap(_shape(text), font, max_width, ddraw), int(min_size * 1.6)


def make_overlay(seg, width, height, work_dir, episode_title=""):
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()

    is_quran = seg.get("type") == "quran"
    is_cta = seg.get("type") in ("cta", "outro")
    text = seg.get("text", "")

    # شريط العنوان العلوي
    if episode_title:
        try:
            ft = ImageFont.truetype(font_path, 45, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            ft = ImageFont.truetype(font_path, 45)
        shaped = _shape(episode_title)
        bbox = draw.textbbox((0, 0), shaped, font=ft)
        overlay.paste(Image.new("RGBA", (VIDEO_W, 90), (0, 0, 0, 170)), (0, 20))
        draw = ImageDraw.Draw(overlay)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, 40), shaped, font=ft,
                  fill="#D4AF37", stroke_width=2, stroke_fill="black")

    # CTA: خلفية ذهبية + إطار + أيقونة
    if is_cta:
        panel_h = VIDEO_H - 380
        panel = Image.new("RGBA", (VIDEO_W - 80, panel_h), (15, 10, 0, 230))
        pdraw = ImageDraw.Draw(panel)
        # إطار ذهبي
        pdraw.rectangle([0, 0, panel.width - 1, panel.height - 1],
                        outline=(212, 175, 55, 255), width=6)
        overlay.paste(panel, (40, 190))
        draw = ImageDraw.Draw(overlay)

        font, lines, lh = _fit_text(text, font_path, VIDEO_W - 160, panel_h - 120, start_size=62)
        total_h = len(lines) * lh
        y = 190 + 60 + max(0, (panel_h - 120 - total_h) // 2)
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y), line, font=font,
                      fill="#F5D76E", stroke_width=3, stroke_fill="black")
            y += lh

        # أيقونة مشاركة أسفل
        try:
            fi = ImageFont.truetype(font_path, 50, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            fi = ImageFont.truetype(font_path, 50)
        icon = _shape("📤 شارك  ❤️ اشترك")
        bbox = draw.textbbox((0, 0), icon, font=fi)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, 190 + panel_h - 90),
                  icon, font=fi, fill="#FFD700", stroke_width=2, stroke_fill="black")

    else:
        # نص عادي (سرد/قرآن)
        start = 45 if is_quran else 55
        color = ACCENT_COLOR if is_quran else TEXT_COLOR
        font, lines, lh = _fit_text(text, font_path, VIDEO_W - 120, VIDEO_H - 400, start_size=start)
        total_h = len(lines) * lh
        y = max(150, (VIDEO_H - total_h) // 2)

        bg = Image.new("RGBA", (width, total_h + 60), (0, 0, 0, 145))
        overlay.paste(bg, (0, y - 30), bg)
        draw = ImageDraw.Draw(overlay)
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y), line, font=font,
                      fill=color, stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
            y += lh

        if is_quran and seg.get("display"):
            try:
                fd = ImageFont.truetype(font_path, 40, layout_engine=ImageFont.Layout.RAQM)
            except Exception:
                fd = ImageFont.truetype(font_path, 40)
            disp = _shape(f"📖 {seg['display']}")
            bbox = draw.textbbox((0, 0), disp, font=fd)
            draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y + 20),
                      disp, font=fd, fill="#D4AF37", stroke_width=2, stroke_fill="black")

    # علامة مائية
    try:
        fw = ImageFont.truetype(font_path, 30, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        fw = ImageFont.truetype(font_path, 30)
    wm = _shape("ملاك المعرفة")
    bbox = draw.textbbox((0, 0), wm, font=fw)
    draw.text((VIDEO_W - (bbox[2] - bbox[0]) - 40, VIDEO_H - 80),
              wm, font=fw, fill=(212, 175, 55, 128))

    # شريط teaser أسفل
    if seg.get("type") == "teaser" and seg.get("teaser_data"):
        td = seg["teaser_data"]
        strip_h = 320
        overlay.paste(Image.new("RGBA", (VIDEO_W, strip_h), (0, 0, 0, 220)),
                      (0, VIDEO_H - strip_h))
        draw = ImageDraw.Draw(overlay)
        try:
            fn = ImageFont.truetype(font_path, 42, layout_engine=ImageFont.Layout.RAQM)
            fu = ImageFont.truetype(font_path, 30, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            fn = ImageFont.truetype(font_path, 42)
            fu = ImageFont.truetype(font_path, 30)
        y0 = VIDEO_H - strip_h + 30
        items = [
            ("⏭ الحلقة القادمة", fn, ACCENT_COLOR, 0),
            (td.get("next_episode_title", ""), fn, TEXT_COLOR, 70),
            (f"🌐 {td.get('url','').replace('https://','')}", fu, ACCENT_COLOR, 150),
        ]
        for txt, f, c, off in items:
            if txt:
                s = _shape(txt)
                bbox = draw.textbbox((0, 0), s, font=f)
                draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y0 + off),
                          s, font=f, fill=c, stroke_width=2, stroke_fill="black")

    path = work_dir / f"overlay_{seg['index']:03d}.png"
    overlay.save(path)
    return str(path)


def build_video(audio_path, meta_path, output_mp4):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_video_"))
    concat_lines = []

    try:
        for seg in meta["segments"]:
            bg = fetch_media(seg.get("visual", "mosque"), work_dir)
            ov = make_overlay(seg, VIDEO_W, VIDEO_H, work_dir, meta.get("title", ""))
            out_seg = work_dir / f"seg_{seg['index']:03d}.mp4"

            subprocess.run([
                "ffmpeg", "-y", "-loop", "1", "-i", bg, "-i", ov,
                "-filter_complex",
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];[bg][1:v]overlay",
                "-t", str(seg["duration"]), "-r", "24",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-pix_fmt", "yuv420p", str(out_seg),
            ], check=True, capture_output=True)

            concat_lines.append(f"file '{out_seg}'")

        (work_dir / "concat.txt").write_text("\n".join(concat_lines))
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(work_dir / "concat.txt"),
            "-i", audio_path,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-shortest", output_mp4,
        ], check=True, capture_output=True)
        print(f"✅ فيديو: {output_mp4}", flush=True)

    finally:
        cleanup_dir(work_dir)


if __name__ == "__main__":
    try:
        build_video(sys.argv[1], sys.argv[2], sys.argv[3])
    except Exception as e:
        notify_telegram(f"فشل story_video: {e}", "error")
        sys.exit(1)
