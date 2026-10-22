"""بناء فيديو القصة: صورة لكل مشهد + نص + علامة تجارية + teaser overlay."""
import json
import os
import subprocess
import tempfile
import random
import requests
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper

from config import VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR, PIXABAY_KEY
from video import _find_arabic_font, _shape, _wrap

SAFE_FALLBACKS = [
    "islamic mosque architecture",
    "kaaba mecca",
    "sand dunes desert",
    "starry night sky",
    "mosque minaret sky",
]

WATERMARK_TEXT = "ملاك المعرفة"
WATERMARK_OPACITY = 0.5


def fetch_image_for_prompt(prompt: str, work_dir: Path) -> str:
    safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt)[:30]
    out_path = work_dir / f"bg_{safe_prompt}.jpg"

    if out_path.exists():
        return str(out_path)

    if PIXABAY_KEY:
        try:
            r = requests.get("https://pixabay.com/api/", params={
                "key": PIXABAY_KEY, "q": prompt, "image_type": "photo",
                "orientation": "vertical", "per_page": 5, "safesearch": "true",
                "min_width": 1080,
            }, timeout=20)
            hits = r.json().get("hits", [])
            if hits:
                url = random.choice(hits)["largeImageURL"]
                out_path.write_bytes(requests.get(url, timeout=30).content)
                return str(out_path)
        except Exception as e:
            print(f"⚠️ فشل جلب صورة {prompt}: {e}")

    fallback = random.choice(SAFE_FALLBACKS)
    if PIXABAY_KEY:
        try:
            r = requests.get("https://pixabay.com/api/", params={
                "key": PIXABAY_KEY, "q": fallback, "image_type": "photo",
                "orientation": "vertical", "per_page": 5, "safesearch": "true",
            }, timeout=20)
            hits = r.json().get("hits", [])
            if hits:
                url = random.choice(hits)["largeImageURL"]
                out_path.write_bytes(requests.get(url, timeout=30).content)
                return str(out_path)
        except Exception:
            pass

    img = Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10))
    img.save(out_path)
    return str(out_path)


def _add_watermark(bg_img: Image.Image, font_path: str) -> Image.Image:
    watermark_layer = Image.new("RGBA", bg_img.size, (0, 0, 0, 0))
    wm_draw = ImageDraw.Draw(watermark_layer)

    try:
        font = ImageFont.truetype(font_path, 30, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        font = ImageFont.truetype(font_path, 30)

    shaped = _shape(WATERMARK_TEXT)
    bbox = wm_draw.textbbox((0, 0), shaped, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    margin = 40
    x = VIDEO_W - tw - margin
    y = VIDEO_H - th - margin

    alpha = int(255 * WATERMARK_OPACITY)
    wm_draw.text(
        (x, y), shaped,
        font=font,
        fill=(212, 175, 55, alpha),
        stroke_width=1,
        stroke_fill=(0, 0, 0, alpha),
    )

    return Image.alpha_composite(bg_img.convert("RGBA"), watermark_layer).convert("RGB")


def _add_teaser_overlay(bg_img: Image.Image, draw: ImageDraw.Draw, font_path: str, teaser_data: dict):
    strip_h = 340
    strip = Image.new("RGBA", (VIDEO_W, strip_h), (0, 0, 0, 210))
    bg_img.paste(strip, (0, VIDEO_H - strip_h), strip)

    try:
        font_next = ImageFont.truetype(font_path, 42, layout_engine=ImageFont.Layout.RAQM)
        font_url = ImageFont.truetype(font_path, 32, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        font_next = ImageFont.truetype(font_path, 42)
        font_url = ImageFont.truetype(font_path, 32)

    next_title = _shape(teaser_data.get("next_episode_title", ""))
    url_text = teaser_data.get("url", "").replace("https://", "").replace("http://", "").rstrip("/")
    platform = _shape(teaser_data.get("platform_name", "ملاك المعرفة"))

    y = VIDEO_H - strip_h + 40

    label_next = _shape("⏭ الحلقة القادمة")
    bbox = draw.textbbox((0, 0), label_next, font=font_next)
    x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), label_next, font=font_next, fill=ACCENT_COLOR, stroke_width=2, stroke_fill="black")
    y += 70

    if next_title:
        bbox = draw.textbbox((0, 0), next_title, font=font_next)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), next_title, font=font_next, fill=TEXT_COLOR, stroke_width=2, stroke_fill="black")
        y += 80

    if url_text:
        label_url = _shape(f"🌐 {url_text}")
        bbox = draw.textbbox((0, 0), label_url, font=font_url)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), label_url, font=font_url, fill=ACCENT_COLOR, stroke_width=1, stroke_fill="black")
        y += 55

    if platform:
        bbox = draw.textbbox((0, 0), platform, font=font_url)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), platform, font=font_url, fill=TEXT_COLOR, stroke_width=1, stroke_fill="black")


def make_segment_image(
    bg_path: str,
    text: str,
    output_path: str,
    is_quran: bool = False,
    is_teaser: bool = False,
    teaser_data: dict = None,
):
    bg = Image.open(bg_path).convert("RGB")

    ratio = max(VIDEO_W / bg.width, VIDEO_H / bg.height)
    bg = bg.resize((int(bg.width * ratio), int(bg.height * ratio)), Image.LANCZOS)
    left = (bg.width - VIDEO_W) // 2
    top = (bg.height - VIDEO_H) // 2
    bg = bg.crop((left, top, left + VIDEO_W, top + VIDEO_H))

    overlay_dark = Image.new("RGBA", bg.size, (0, 0, 0, 150))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay_dark).convert("RGB")

    draw = ImageDraw.Draw(bg)
    font_path = _find_arabic_font()

    if is_quran:
        try:
            font_main = ImageFont.truetype(font_path, 45, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            font_main = ImageFont.truetype(font_path, 45)
        color = ACCENT_COLOR
    else:
        try:
            font_main = ImageFont.truetype(font_path, 55, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            font_main = ImageFont.truetype(font_path, 55)
        color = TEXT_COLOR

    shaped = _shape(text)
    lines = _wrap(shaped, font_main, VIDEO_W - 120, draw)

    line_h = int(font_main.size * 1.6)
    total_h = len(lines) * line_h
    y = max(120, (VIDEO_H - total_h) // 2)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_main)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, font=font_main, fill=color, stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
        y += line_h

    bg = _add_watermark(bg, font_path)
    draw = ImageDraw.Draw(bg)

    if is_teaser and teaser_data:
        _add_teaser_overlay(bg, draw, font_path, teaser_data)

    bg.save(output_path, "PNG")
    return output_path


def build_episode_video(audio_path: str, meta_path: str, output_mp4: str = "out/episode.mp4"):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    segments = meta.get("segments", [])

    if not segments:
        raise RuntimeError("لا مقاطع في الميتا")

    Path("out").mkdir(exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="ep_video_"))

    current_scene = None
    current_bg_path = None
    bg_cache = {}

    concat_lines = []
    for seg in segments:
        idx = seg["index"]
        scene = seg.get("visual", "islamic architecture mosque")
        is_quran = (seg["type"] == "quran")
        is_teaser = (seg["type"] == "teaser")
        teaser_data = seg.get("teaser_data") if is_teaser else None

        if scene != current_scene:
            current_scene = scene
            if scene not in bg_cache:
                print(f"🖼️ جلب صورة للمشهد: {scene}...")
                bg_cache[scene] = fetch_image_for_prompt(scene, work_dir)
            current_bg_path = bg_cache[scene]

        print(f"🖼️ المقطع {idx} (المشهد: {scene}, teaser: {is_teaser})...")
        img_path = work_dir / f"seg_{idx:03d}.png"

        make_segment_image(
            current_bg_path,
            seg.get("text", ""),
            str(img_path),
            is_quran=is_quran,
            is_teaser=is_teaser,
            teaser_data=teaser_data,
        )

        concat_lines.append(f"file '{img_path}'")
        concat_lines.append(f"duration {seg['duration']}")

    concat_lines.append(f"file '{work_dir / ('seg_' + str(segments[-1]['index']).zfill(3) + '.png')}'")

    concat_file = work_dir / "concat.txt"
    concat_file.write_text("\n".join(concat_lines), encoding="utf-8")

    print(f"🎬 تركيب الفيديو ({len(segments)} مقطع)...")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-i", audio_path, "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", output_mp4,
    ], check=True, capture_output=True, timeout=1800)

    print(f"✅ الفيديو: {output_mp4} ({os.path.getsize(output_mp4)/1024/1024:.1f} MB)")
    return output_mp4

if __name__ == "__main__":
    if len(sys.argv) > 3:
        build_episode_video(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        build_episode_video("out/episode-01.mp3", "out/episode-01_meta.json", "out/episode-01.mp4")
