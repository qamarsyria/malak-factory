"""بناء فيديو القصة مع صور وألوان تتغير لكل جملة، ونصوص الآيات الحقيقية."""
import json
import os
import subprocess
import tempfile
import random
import requests
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper

from config import VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR, PIXABAY_KEY
from video import _find_arabic_font, _shape, _wrap

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
            }, timeout=20)
            hits = r.json().get("hits", [])
            if hits:
                url = random.choice(hits)["largeImageURL"]
                out_path.write_bytes(requests.get(url, timeout=30).content)
                return str(out_path)
        except Exception as e:
            print(f"⚠️ فشل جلب صورة {prompt}: {e}")
            
    # احتياطي: خلفية داكنة
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10))
    img.save(out_path)
    return str(out_path)

def make_segment_image(bg_path: str, text: str, output_path: str, is_quran: bool = False):
    bg = Image.open(bg_path).convert("RGB")
    
    ratio = max(VIDEO_W / bg.width, VIDEO_H / bg.height)
    bg = bg.resize((int(bg.width * ratio), int(bg.height * ratio)), Image.LANCZOS)
    left = (bg.width - VIDEO_W) // 2
    top = (bg.height - VIDEO_H) // 2
    bg = bg.crop((left, top, left + VIDEO_W, top + VIDEO_H))

    # تعتيم الصورة لإبراز النص
    overlay_dark = Image.new("RGBA", bg.size, (0, 0, 0, 140))
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

    try:
        font_brand = ImageFont.truetype(font_path, 32, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        font_brand = ImageFont.truetype(font_path, 32)

    brand_text = _shape("ملاك المعرفة")
    bbox = draw.textbbox((0, 0), brand_text, font=font_brand)
    draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, VIDEO_H - 80), brand_text, font=font_brand, fill="#D4AF37", stroke_width=2, stroke_fill="black")

    bg.save(output_path, "PNG")
    return output_path

def build_episode_video(audio_path: str, meta_path: str, output_mp4: str = "out/episode.mp4"):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    segments = meta.get("segments", [])

    if not segments:
        raise RuntimeError("لا مقاطع في الميتا")

    Path("out").mkdir(exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="ep_video_"))

    concat_lines = []
    for seg in segments:
        idx = seg["index"]
        prompt = seg.get("visual", "islamic nature")
        is_quran = (seg["type"] == "quran")
        
        print(f"🖼️ تجهيز صورة: {prompt}...")
        bg_path = fetch_image_for_prompt(prompt, work_dir)
        img_path = work_dir / f"seg_{idx:03d}.png"

        make_segment_image(bg_path, seg.get("text", ""), str(img_path), is_quran=is_quran)

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
    build_episode_video(
        "out/episode-01.mp3",
        "out/episode-01_meta.json",
        "out/episode-01.mp4",
    )
