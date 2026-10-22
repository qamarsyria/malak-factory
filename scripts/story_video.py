"""بناء فيديو القصة: دعم الصور والفيديوهات + علامة تجارية + حماية شرعية."""
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
    "quran book",
    "nature landscape"
]

# مقاطع الفيديو الطبيعية التي سنستخدمها للآيات القرآنية
NATURE_VIDEO_QUERIES = [
    "waterfall nature",
    "birds flying sky",
    "ocean waves",
    "river flowing",
    "clouds sky",
    "sunset desert"
]

WATERMARK_TEXT = "ملاك المعرفة"
WATERMARK_OPACITY = 0.5

# 🛡️ كلمات ممنوعة (لا نقبل أي صورة تحتوي عليها)
FORBIDDEN_TAGS = [
    "woman", "women", "girl", "female", "child", "boy", "man", "men", 
    "people", "person", "face", "human", "body", "hand", "skin"
]

def is_image_safe(tags_str):
    tags = str(tags_str).lower()
    return not any(forbidden in tags for forbidden in FORBIDDEN_TAGS)

def fetch_image_for_prompt(prompt: str, work_dir: Path) -> str:
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
                else:
                    print(f"🛡️ تم رفض صورة غير لائقة: {hit.get('tags')}")
        except Exception as e:
            print(f"⚠️ فشل جلب صورة {prompt}: {e}")

    fallback = random.choice(SAFE_FALLBACKS)
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10))
    img.save(out_path)
    return str(out_path)

def fetch_video_for_prompt(prompt: str, work_dir: Path) -> str:
    safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt)[:30]
    out_path = work_dir / f"bg_{safe_prompt}.mp4"
    if out_path.exists(): return str(out_path)

    if PIXABAY_KEY:
        try:
            r = requests.get("https://pixabay.com/api/videos/", params={
                "key": PIXABAY_KEY, "q": prompt, "per_page": 15, "safesearch": "true"
            }, timeout=20)
            for hit in r.json().get("hits", []):
                if is_image_safe(hit.get("tags", "")):
                    videos = hit.get("videos", {})
                    url = videos.get("medium", {}).get("url") or videos.get("small", {}).get("url")
                    if url:
                        out_path.write_bytes(requests.get(url, timeout=60).content)
                        return str(out_path)
        except Exception as e:
            print(f"⚠️ فشل جلب فيديو {prompt}: {e}")
            
    return fetch_image_for_prompt(prompt, work_dir)

def make_overlay(seg, width, height, work_dir):
    """يرسم طبقة شفافة تحتوي على النصوص والعلامة المائية لدمجها مع الخلفية"""
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()
    is_quran = seg.get("type") == "quran"
    text = seg.get("text", "")
    display = seg.get("display", "")

    # 1. لفظ الجلالة للقرآن (شفاف وخلف النص)
    if is_quran:
        try: font_allah = ImageFont.truetype(font_path, 450, layout_engine=ImageFont.Layout.RAQM)
        except: font_allah = ImageFont.truetype(font_path, 450)
        allah_shaped = _shape("ﷲ")
        bbox = draw.textbbox((0, 0), allah_shaped, font=font_allah)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        y = (VIDEO_H - (bbox[3] - bbox[1])) // 2
        draw.text((x, y), allah_shaped, font=font_allah, fill=(212, 175, 55, 60))

    # 2. النص (سرد أو آية)
    if is_quran:
        try:
            font_main = ImageFont.truetype(font_path, 45, layout_engine=ImageFont.Layout.RAQM)
            font_display = ImageFont.truetype(font_path, 35, layout_engine=ImageFont.Layout.RAQM)
        except: 
            font_main = ImageFont.truetype(font_path, 45)
            font_display = ImageFont.truetype(font_path, 35)
        color = ACCENT_COLOR
    else:
        try: font_main = ImageFont.truetype(font_path, 55, layout_engine=ImageFont.Layout.RAQM)
        except: font_main = ImageFont.truetype(font_path, 55)
        color = TEXT_COLOR

    shaped = _shape(text)
    lines = _wrap(shaped, font_main, VIDEO_W - 120, draw)
    line_h = int(font_main.size * 1.6)
    total_h = len(lines) * line_h
    y = max(120, (VIDEO_H - total_h) // 2)

    # إضافة طبقة ظل خفيفة خلف النص ليكون مقروءاً على أي خلفية
    text_bg = Image.new("RGBA", (width, total_h + 60), (0, 0, 0, 130))
    overlay.paste(text_bg, (0, y - 30), text_bg)
    draw = ImageDraw.Draw(overlay)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_main)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, font=font_main, fill=color, stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE)
        y += line_h

    # 3. اسم السورة ورقم الآية
    if is_quran and display:
        display_shaped = _shape(f"📖 {display}")
        bbox = draw.textbbox((0, 0), display_shaped, font=font_display)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y + 20), display_shaped, font=font_display, fill="#D4AF37", stroke_width=2, stroke_fill="black")

    # 4. العلامة المائية (ملاك المعرفة)
    try: font_wm = ImageFont.truetype(font_path, 30, layout_engine=ImageFont.Layout.RAQM)
    except: font_wm = ImageFont.truetype(font_path, 30)
    wm_shaped = _shape(WATERMARK_TEXT)
    bbox = draw.textbbox((0, 0), wm_shaped, font=font_wm)
    x = VIDEO_W - (bbox[2] - bbox[0]) - 40
    y_wm = VIDEO_H - (bbox[3] - bbox[1]) - 40
    draw.text((x, y_wm), wm_shaped, font=font_wm, fill=(212, 175, 55, 128), stroke_width=1, stroke_fill=(0,0,0,128))

    # 5. Teaser Overlay
    if seg.get("type") == "teaser":
        td = seg.get("teaser_data", {})
        strip_h = 340
        strip = Image.new("RGBA", (VIDEO_W, strip_h), (0, 0, 0, 210))
        overlay.paste(strip, (0, VIDEO_H - strip_h), strip)
        draw = ImageDraw.Draw(overlay)

        try:
            font_next = ImageFont.truetype(font_path, 42, layout_engine=ImageFont.Layout.RAQM)
            font_url = ImageFont.truetype(font_path, 32, layout_engine=ImageFont.Layout.RAQM)
        except: 
            font_next = ImageFont.truetype(font_path, 42)
            font_url = ImageFont.truetype(font_path, 32)

        next_title = _shape(td.get("next_episode_title", ""))
        url_text = td.get("url", "").replace("https://", "").replace("http://", "").rstrip("/")
        platform = _shape(td.get("platform_name", "ملاك المعرفة"))
        
        y = VIDEO_H - strip_h + 40
        label_next = _shape("⏭ الحلقة القادمة")
        bbox = draw.textbbox((0, 0), label_next, font=font_next)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y), label_next, font=font_next, fill=ACCENT_COLOR, stroke_width=2, stroke_fill="black")
        y += 70
        if next_title:
            bbox = draw.textbbox((0, 0), next_title, font=font_next)
            draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y), next_title, font=font_next, fill=TEXT_COLOR, stroke_width=2, stroke_fill="black")
            y += 80
        if url_text:
            label_url = _shape(f"🌐 {url_text}")
            bbox = draw.textbbox((0, 0), label_url, font=font_url)
            draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y), label_url, font=font_url, fill=ACCENT_COLOR, stroke_width=1, stroke_fill="black")
            y += 55
        if platform:
            bbox = draw.textbbox((0, 0), platform, font=font_url)
            draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y), platform, font=font_url, fill=TEXT_COLOR, stroke_width=1, stroke_fill="black")

    overlay_path = work_dir / f"overlay_{seg['index']:03d}.png"
    overlay.save(overlay_path)
    return str(overlay_path)

def build_episode_video(audio_path: str, meta_path: str, output_mp4: str = "out/episode.mp4"):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    segments = meta.get("segments", [])
    if not segments: raise RuntimeError("لا مقاطع في الميتا")

    work_dir = Path(tempfile.mkdtemp(prefix="ep_video_"))
    concat_lines = []

    for seg in segments:
        idx = seg["index"]
        duration = seg["duration"]
        is_quran = seg.get("type") == "quran"
        
        # اختيار الخلفية بناءً على نوع المقطع
        use_video = False
        if is_quran:
            if random.random() < 0.5:
                use_video = True
                scene = random.choice(NATURE_VIDEO_QUERIES)
            else:
                scene = random.choice(["quran book", "mosque architecture", "kaaba mecca"])
        else:
            scene = seg.get("visual", "islamic architecture mosque")

        if use_video:
            print(f"🎥 جلب فيديو طبيعي للآية: {scene}...")
            bg_path = fetch_video_for_prompt(scene, work_dir)
        else:
            print(f"🖼️ جلب صورة للمشهد: {scene}...")
            bg_path = fetch_image_for_prompt(scene, work_dir)

        overlay_path = make_overlay(seg, VIDEO_W, VIDEO_H, work_dir)
        
        print(f"🎬 تجهيز المقطع {idx}...")
        out_seg = work_dir / f"seg_{idx:03d}.mp4"
        
        if bg_path.endswith(".mp4"):
            cmd = [
                "ffmpeg", "-y", "-stream_loop", "-1", "-i", bg_path, "-i", overlay_path,
                "-filter_complex", "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];[bg][1:v]overlay",
                "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-threads", "4", "-crf", "28",
                "-pix_fmt", "yuv420p", str(out_seg)
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", bg_path, "-i", overlay_path,
                "-filter_complex", "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];[bg][1:v]overlay",
                "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-threads", "4", "-crf", "28",
                "-pix_fmt", "yuv420p", str(out_seg)
            ]
            
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        concat_lines.append(f"file '{out_seg}'")

    concat_file = work_dir / "concat.txt"
    concat_file.write_text("\n".join(concat_lines), encoding="utf-8")

    print(f"🔗 تركيب الفيديو النهائي...")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", output_mp4,
    ], check=True, capture_output=True, timeout=1800)

    print(f"✅ الفيديو: {output_mp4}")
    return output_mp4

if __name__ == "__main__":
    if len(sys.argv) > 3:
        build_episode_video(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        build_episode_video("out/episode-01.mp3", "out/episode-01_meta.json", "out/episode-01.mp4")
