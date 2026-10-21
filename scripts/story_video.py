"""بناء فيديو القصة من الصوت + النصوص + خلفيات متغيرة حسب المشهد."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper

from config import VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR, SCENE_MAP
from video import _find_arabic_font, _shape, _wrap

def fetch_scene_bg(scene: str, work_dir: Path) -> str:
    """جلب خلفية تناسب المشهد من Pixabay."""
    import requests, random
    from config import PIXABAY_KEY
    
    query = SCENE_MAP.get(scene, {}).get("bg_query", "islamic nature")
    out_path = work_dir / f"bg_{scene}.jpg"
    
    if out_path.exists():
        return str(out_path)
        
    if PIXABAY_KEY:
        try:
            r = requests.get("https://pixabay.com/api/", params={
                "key": PIXABAY_KEY, "q": query, "image_type": "photo",
                "orientation": "vertical", "per_page": 10, "safesearch": "true",
            }, timeout=20)
            hits = r.json().get("hits", [])
            if hits:
                url = random.choice(hits)["largeImageURL"]
                out_path.write_bytes(requests.get(url, timeout=30).content)
                return str(out_path)
        except Exception as e:
            print(f"⚠️ فشل جلب خلفية {scene}: {e}")
            
    # احتياطي: خلفية سوداء
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10))
    img.save(out_path)
    return str(out_path)

def make_segment_image(bg_path: str, text: str, title: str, scene: str,
                       output_path: str, is_quran: bool = False):
    bg = Image.open(bg_path).convert("RGB")
    
    # قص وتكبير
    ratio = max(VIDEO_W / bg.width, VIDEO_H / bg.height)
    bg = bg.resize((int(bg.width * ratio), int(bg.height * ratio)), Image.LANCZOS)
    left = (bg.width - VIDEO_W) // 2
    top = (bg.height - VIDEO_H) // 2
    bg = bg.crop((left, top, left + VIDEO_W, top + VIDEO_H))

    # فلتر الألوان (حسب المشهد)
    tint = SCENE_MAP.get(scene, {}).get("color_tint", (0, 0, 0, 120))
    overlay = Image.new("RGBA", bg.size, tint)
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    # تعتيم إضافي لضمان وضوح النص
    overlay_dark = Image.new("RGBA", bg.size, (0, 0, 0, 80))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay_dark).convert("RGB")

    draw = ImageDraw.Draw(bg)
    font_path = _find_arabic_font()

    try:
        font_main = ImageFont.truetype(font_path, 55, layout_engine=ImageFont.Layout.RAQM)
        font_title = ImageFont.truetype(font_path, 45, layout_engine=ImageFont.Layout.RAQM)
    except:
        font_main = ImageFont.truetype(font_path, 55)
        font_title = ImageFont.truetype(font_path, 45)

    text_to_draw = title if is_quran else text
    font_use = font_title if is_quran else font_main
    color = ACCENT_COLOR if is_quran else TEXT_COLOR

    shaped = _shape(text_to_draw)
    lines = _wrap(shaped, font_use, VIDEO_W - 120, draw)

    line_h = int(font_use.size * 1.6)
    total_h = len(lines) * line_h
    y = max(120, (VIDEO_H - total_h) // 2)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_use)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, font=font_use, fill=color, stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
        y += line_h

    # علامة تجارية
    try:
        font_brand = ImageFont.truetype(font_path, 32, layout_engine=ImageFont.Layout.RAQM)
    except:
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
        scene = seg.get("scene", "calm")
        
        print(f"🖼️ تجهيز مشهد {scene} للمقطع {idx}...")
        bg_path = fetch_scene_bg(scene, work_dir)
        img_path = work_dir / f"seg_{idx:03d}.png"

        make_segment_image(bg_path, seg.get("text", ""), seg.get("title", ""), scene, str(img_path), is_quran=(seg["type"] == "quran"))

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
