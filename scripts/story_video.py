"""بناء الفيديو: صور + overlays + concat — متوافق مع مقاطع 700 حرف."""
import sys
import json
import subprocess
import tempfile
import traceback
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (  # noqa: E402
    VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W,
    ACCENT_COLOR, PIXABAY_KEY,
)
from video import _find_arabic_font, _shape, _wrap  # noqa: E402
from utilities import cleanup_dir, notify_telegram  # noqa: E402


# ═════════════════════════════════════════════════════════════
# فلترة الصور: منع أي صورة فيها بشر أو رموز دينية غير إسلامية
# ═════════════════════════════════════════════════════════════
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


# ═════════════════════════════════════════════════════════════
# جلب الصور من Pixabay
# ═════════════════════════════════════════════════════════════
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
        except Exception as e:
            print(f"   ⚠️ فشل تحميل صورة '{prompt}': {e}", flush=True)

    # fallback: خلفية سوداء
    Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10)).save(out)
    return str(out)


# ═════════════════════════════════════════════════════════════
# ملاءمة النص: يختار أكبر خط ممكن دون تجاوز الحدود
# ═════════════════════════════════════════════════════════════
def _fit_text(text, font_path, max_width, max_height, start_size, min_size):
    dummy = Image.new("RGB", (10, 10))
    ddraw = ImageDraw.Draw(dummy)
    for size in range(start_size, min_size - 1, -2):
        try:
            font = ImageFont.truetype(
                font_path, size, layout_engine=ImageFont.Layout.RAQM,
            )
        except Exception:
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
    draw.text((x, y), shaped, font=font, fill=fill,
              stroke_width=stroke_w, stroke_fill=stroke_fill)


# ═════════════════════════════════════════════════════════════
# بناء الـ Overlay (الطبقة الشفافة فوق الصورة)
# ═════════════════════════════════════════════════════════════
def make_overlay(seg, work_dir, episode_title=""):
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()

    is_quran = seg.get("type") == "quran"
    is_cta = seg.get("type") in ("cta", "outro")
    text = seg.get("text", "")

    # ─────────────────────────────────────────────────────────
    # شريط العنوان العلوي (يظهر في كل المقاطع)
    # ─────────────────────────────────────────────────────────
    if episode_title:
        try:
            ft = ImageFont.truetype(
                font_path, 44, layout_engine=ImageFont.Layout.RAQM,
            )
        except Exception:
            ft = ImageFont.truetype(font_path, 44)
        overlay.paste(Image.new("RGBA", (VIDEO_W, 100), (0, 0, 0, 180)), (0, 15))
        draw = ImageDraw.Draw(overlay)
        _draw_centered(draw, 38, episode_title, ft, "#D4AF37", 2, "black")

    # ─────────────────────────────────────────────────────────
    # CTA: لوحة ذهبية بإطار + أيقونات "شارك" و "اشترك"
    # ─────────────────────────────────────────────────────────
    if is_cta:
        panel_x = 40
        panel_y = 170
        panel_w = VIDEO_W - 80
        panel_h = VIDEO_H - 290

        panel = Image.new("RGBA", (panel_w, panel_h), (12, 8, 0, 235))
        pdraw = ImageDraw.Draw(panel)
        pdraw.rectangle(
            [0, 0, panel_w - 1, panel_h - 1],
            outline=(212, 175, 55, 255), width=8,
        )
        overlay.paste(panel, (panel_x, panel_y))
        draw = ImageDraw.Draw(overlay)

        # نص CTA بحجم كبير وذهبي
        font, lines, lh = _fit_text(
            text, font_path, panel_w - 80, panel_h - 200,
            start_size=58, min_size=34,
        )
        total_h = len(lines) * lh
        y = panel_y + 50 + max(0, (panel_h - 180 - total_h) // 2)
        for line in lines:
            _draw_centered(draw, y, line, font, "#F5D76E", 3, "black")
            y += lh

        # أيقونات أسفل اللوحة
        try:
            fi = ImageFont.truetype(
                font_path, 44, layout_engine=ImageFont.Layout.RAQM,
            )
        except Exception:
            fi = ImageFont.truetype(font_path, 44)
        icon_y = panel_y + panel_h - 90
        _draw_centered(
            draw, icon_y,
            "◆ شارك الفيديو    ◆ اشترك في القناة",
            fi, "#FFD700", 2, "black",
        )

    # ─────────────────────────────────────────────────────────
    # نص عادي: سرد (700 حرف) أو آية
    # ─────────────────────────────────────────────────────────
    else:
        start = 56 if is_quran else 60
        color = ACCENT_COLOR if is_quran else TEXT_COLOR
        font, lines, lh = _fit_text(
            text, font_path,
            VIDEO_W - 100, VIDEO_H - 340,
            start_size=start, min_size=40,
        )
        total_h = len(lines) * lh
        y = max(140, (VIDEO_H - total_h) // 2)

        # خلفية سوداء شفافة خلف النص
        bg_h = total_h + 70
        bg = Image.new("RGBA", (VIDEO_W, bg_h), (0, 0, 0, 150))
        overlay.paste(bg, (0, y - 35), bg)
        draw = ImageDraw.Draw(overlay)

        for line in lines:
            _draw_centered(draw, y, line, font, color, TEXT_STROKE_W, TEXT_STROKE)
            y += lh

        # للآيات: إضافة سطر المرجع (سورة: آية)
        if is_quran and seg.get("display"):
            try:
                fd = ImageFont.truetype(
                    font_path, 42, layout_engine=ImageFont.Layout.RAQM,
                )
            except Exception:
                fd = ImageFont.truetype(font_path, 42)
            _draw_centered(
                draw, y + 25,
                f"﴿ {seg['display']} ﴾",
                fd, "#D4AF37", 2, "black",
            )

    # ─────────────────────────────────────────────────────────
    # علامة مائية ثابتة (أسفل يمين)
    # ─────────────────────────────────────────────────────────
    try:
        fw = ImageFont.truetype(
            font_path, 30, layout_engine=ImageFont.Layout.RAQM,
        )
    except Exception:
        fw = ImageFont.truetype(font_path, 30)
    wm = _shape("ملاك المعرفة")
    bbox = draw.textbbox((0, 0), wm, font=fw)
    draw.text(
        (VIDEO_W - (bbox[2] - bbox[0]) - 40, VIDEO_H - 70),
        wm, font=fw, fill=(212, 175, 55, 130),
    )

    # ─────────────────────────────────────────────────────────
    # شريط Teaser (الحلقة القادمة)
    # ─────────────────────────────────────────────────────────
    if seg.get("type") == "teaser" and seg.get("teaser_data"):
        td = seg["teaser_data"]
        strip_h = 320
        overlay.paste(
            Image.new("RGBA", (VIDEO_W, strip_h), (0, 0, 0, 225)),
            (0, VIDEO_H - strip_h),
        )
        draw = ImageDraw.Draw(overlay)

        try:
            fn = ImageFont.truetype(
                font_path, 42, layout_engine=ImageFont.Layout.RAQM,
            )
            fu = ImageFont.truetype(
                font_path, 30, layout_engine=ImageFont.Layout.RAQM,
            )
        except Exception:
            fn = ImageFont.truetype(font_path, 42)
            fu = ImageFont.truetype(font_path, 30)

        y0 = VIDEO_H - strip_h + 30
        _draw_centered(draw, y0, "▶ الحلقة القادمة", fn, ACCENT_COLOR, 2, "black")
        _draw_centered(
            draw, y0 + 70, td.get("next_episode_title", ""),
            fn, TEXT_COLOR, 2, "black",
        )
        _draw_centered(
            draw, y0 + 160,
            f"◆ {td.get('url', '').replace('https://', '')}",
            fu, ACCENT_COLOR, 2, "black",
        )

    # ─────────────────────────────────────────────────────────
    # حفظ الـ overlay
    # ─────────────────────────────────────────────────────────
    path = work_dir / f"overlay_{seg['index']:03d}.png"
    overlay.save(path)
    return str(path)


# ═════════════════════════════════════════════════════════════
# بناء الفيديو الكامل
# ═════════════════════════════════════════════════════════════
def build_video(audio_path, meta_path, output_mp4):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_video_"))
    concat_lines = []

    try:
        total_segs = len(meta["segments"])
        print(f"🎬 بناء الفيديو: {total_segs} مقطع", flush=True)

        for i, seg in enumerate(meta["segments"], 1):
            duration = seg["duration"]
            if duration <= 0:
                print(f"   ⚠️ تخطي مقطع {seg['index']} (مدة 0)", flush=True)
                continue

            print(f"   [{i}/{total_segs}] {seg.get('type', '?')} "
                  f"({duration:.1f}s)", flush=True)

            bg = fetch_media(seg.get("visual", "mosque"), work_dir)
            ov = make_overlay(seg, work_dir, meta.get("title", ""))
            out_seg = work_dir / f"seg_{seg['index']:03d}.mp4"

            subprocess.run([
                "ffmpeg", "-y", "-loop", "1", "-i", bg, "-i", ov,
                "-filter_complex",
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920[bg];[bg][1:v]overlay",
                "-t", str(duration), "-r", "24",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-pix_fmt", "yuv420p", str(out_seg),
            ], check=True, capture_output=True)

            concat_lines.append(f"file '{out_seg}'")

        if not concat_lines:
            raise RuntimeError("لا مقاطع فيديو صالحة")

        # دمج كل المقاطع + الصوت
        (work_dir / "concat.txt").write_text(
            "\n".join(concat_lines), encoding="utf-8",
        )
        Path(output_mp4).parent.mkdir(parents=True, exist_ok=True)

        print("🔗 دمج المقاطع مع الصوت...", flush=True)
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(work_dir / "concat.txt"),
            "-i", audio_path,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-shortest", output_mp4,
        ], check=True, capture_output=True)

        size_mb = Path(output_mp4).stat().st_size / (1024 * 1024)
        print(f"✅ فيديو: {output_mp4} ({size_mb:.1f} MB)", flush=True)
    finally:
        cleanup_dir(work_dir)


if __name__ == "__main__":
    try:
        if len(sys.argv) < 4:
            print("الاستخدام: story_video.py <audio.mp3> <meta.json> <out.mp4>")
            sys.exit(1)
        build_video(sys.argv[1], sys.argv[2], sys.argv[3])
    except Exception as e:
        traceback.print_exc()
        notify_telegram(f"فشل story_video: {e}", "error")
        sys.exit(1)
