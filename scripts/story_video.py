"""بناء الفيديو: فيديوهات متحركة من Pixabay + صور احتياطية + overlays.
v2 — يحل مشكلة تكرار نفس الصورة:
- جلب فيديو متحرك أولاً (طيور، مطر، بحر، جبال، صحراء، نجوم...).
- استخراج كلمات مفتاحية من نص المقطع لاختيار خلفية مناسبة.
- تنويع pools حسب نوع المقطع (quran/narration/summary/teaser/cta).
- البحث في مجلدات محلية أولاً، ثم Pixabay.
"""
import os
import re
import sys
import json
import subprocess
import tempfile
import random
import traceback
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    VIDEO_W, VIDEO_H, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W,
    ACCENT_COLOR, PIXABAY_KEY,
    BG_VIDEOS_DIR, BG_IMAGES_DIR,
)
from video import _find_arabic_font, _shape, _wrap
from utilities import cleanup_dir, notify_telegram


# ═══ فلترة أمنية للصور والفيديوهات ═══
FORBIDDEN_TAGS = {
    "woman", "women", "girl", "female", "child", "boy", "man", "men",
    "people", "person", "face", "human", "body", "hand", "skin",
    "crowd", "family", "couple", "wedding", "dance", "dancing",
    "idol", "church", "cross", "crucifix",
    "alcohol", "wine", "beer", "drunk", "pig", "dog", "bride", "groom",
    "nude", "naked", "bikini", "lingerie", "kiss", "romance",
}


def is_media_safe(tags_str: str) -> bool:
    tags = {t.strip().lower() for t in str(tags_str).replace(",", " ").split()}
    return not (tags & FORBIDDEN_TAGS)


# ═══ خريطة الكلمات المفتاحية → query ═══
KEYWORD_MAP = {
    # ماء
    "بحر": "sea waves ocean",
    "يم": "sea waves ocean",
    "موج": "ocean waves",
    "نهر": "river flowing",
    "ماء": "water flowing",
    "بئر": "stone well water",
    "شلال": "waterfall nature",
    "مطر": "rain falling",
    "غيوم": "clouds moving sky",
    "سحاب": "clouds sky",
    "رعد": "thunder storm",
    "برق": "lightning storm",

    # يابسة
    "جبل": "mountain peak",
    "طور": "mountain peak",
    "صخر": "rocky mountain",
    "صحراء": "desert dunes",
    "رمل": "sand dunes",
    "واد": "valley nature",
    "أرض": "landscape nature",

    # نباتات
    "نخل": "palm trees",
    "شجر": "trees forest",
    "بستان": "garden trees",
    "جنة": "garden paradise",
    "زرع": "wheat field",
    "ثمر": "fruit tree",

    # سماء
    "نجوم": "starry night sky",
    "نجم": "stars night",
    "قمر": "moon night",
    "شمس": "sunrise sky",
    "فجر": "sunrise dawn",
    "ليل": "night sky dark",
    "ظلام": "dark night",
    "سماء": "sky clouds",

    # مبانٍ
    "بيت": "ancient stone house",
    "مدينة": "ancient city",
    "قصر": "ancient palace",
    "محراب": "mosque interior",
    "مسجد": "mosque architecture",
    "كعبة": "kaaba mecca",
    "مكة": "mecca ancient",
    "بيت المقدس": "jerusalem dome",

    # حيوانات
    "طير": "birds flying sky",
    "طيور": "birds flying",
    "جمل": "camel desert",
    "خيل": "horse running",
    "غنم": "sheep pasture",
    "حوت": "whale ocean",
    "نحل": "bees flowers",

    # ظواهر
    "نار": "fire flames",
    "لهب": "fire flames",
    "ريح": "wind trees",
    "عاصفة": "storm clouds",
    "زلزال": "desert cracked",
    "ظل": "shadow trees",
}


# ═══ pools حسب نوع المقطع (احتياطي) ═══
POOLS_BY_TYPE = {
    "quran": ["quran book", "islamic calligraphy", "mosque interior"],
    "narration": [
        "desert dunes", "starry night sky", "mountain peak",
        "river flowing", "palm trees oasis", "clouds moving sky",
        "ancient stone ruins", "sunrise dawn",
    ],
    "summary": ["sunrise sky", "calm sea", "mountain peak", "starry night"],
    "teaser": ["dramatic clouds", "starry night sky", "moon night"],
    "cta": ["mosque architecture", "islamic pattern", "mosque minaret"],
    "intro": ["mosque architecture", "islamic pattern"],
    "outro": ["mosque architecture", "sunrise sky"],
}


def pick_visual_query(seg: dict, idx: int) -> str:
    """اختيار query مناسب: من النص أولاً، ثم pool حسب النوع، ثم افتراضي."""
    # 1. إذا كان seg["visual"] موجود ومحدد وغير عام
    explicit = seg.get("visual", "").strip()
    if explicit and explicit not in ("mosque", "quran book", "islamic architecture"):
        return explicit

    # 2. استخراج كلمات مفتاحية من النص
    text = seg.get("text", "")
    for keyword, query in KEYWORD_MAP.items():
        if keyword in text:
            return query

    # 3. pool حسب نوع المقطع
    seg_type = seg.get("type", "narration")
    pool = POOLS_BY_TYPE.get(seg_type, POOLS_BY_TYPE["narration"])
    return random.choice(pool)


# ═══ جلب وسائط (فيديو أولاً، ثم صورة) ═══
def _find_local(query: str):
    """البحث في المجلدات المحلية."""
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")

    for base in (Path(BG_VIDEOS_DIR), Path(BG_IMAGES_DIR)):
        if not base.exists():
            continue
        for f in base.glob(f"{slug}*"):
            if f.suffix.lower() in (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png"):
                if f.stat().st_size > 10000:
                    return str(f)
    return None


def _download_file(url: str, out_path: Path) -> bool:
    try:
        r = requests.get(url, timeout=60, stream=True)
        if r.status_code != 200:
            return False
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return out_path.stat().st_size > 20000
    except Exception:
        return False


def _fetch_video(query: str, work_dir: Path):
    """جلب فيديو من Pixabay."""
    if not PIXABAY_KEY:
        return None
    try:
        r = requests.get("https://pixabay.com/api/videos/", params={
            "key": PIXABAY_KEY, "q": query,
            "video_type": "film", "safesearch": "true",
            "per_page": 20, "min_width": 1080,
        }, timeout=20)
        if r.status_code != 200:
            return None
        for hit in r.json().get("hits", []):
            if not is_media_safe(hit.get("tags", "")):
                continue
            videos = hit.get("videos", {})
            url = (videos.get("medium", {}).get("url")
                   or videos.get("small", {}).get("url"))
            if not url:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:30]
            out = work_dir / f"vid_{slug}_{hit['id']}.mp4"
            if _download_file(url, out):
                print(f"   🎥 فيديو: {query}", flush=True)
                return str(out)
    except Exception as e:
        print(f"   ⚠️ فشل جلب فيديو '{query}': {str(e)[:80]}", flush=True)
    return None


def _fetch_image(query: str, work_dir: Path):
    """جلب صورة من Pixabay."""
    if not PIXABAY_KEY:
        return None
    try:
        r = requests.get("https://pixabay.com/api/", params={
            "key": PIXABAY_KEY, "q": query, "image_type": "photo",
            "orientation": "vertical", "per_page": 20,
            "safesearch": "true", "min_width": 1080,
        }, timeout=20)
        if r.status_code != 200:
            return None
        for hit in r.json().get("hits", []):
            if not is_media_safe(hit.get("tags", "")):
                continue
            url = hit.get("largeImageURL")
            if not url:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:30]
            out = work_dir / f"img_{slug}_{hit['id']}.jpg"
            if _download_file(url, out):
                print(f"   🖼️ صورة: {query}", flush=True)
                return str(out)
    except Exception as e:
        print(f"   ⚠️ فشل جلب صورة '{query}': {str(e)[:80]}", flush=True)
    return None


def fetch_media(query: str, work_dir: Path, used_queries: list = None):
    """
    جلب وسائط: محلي أولاً، ثم فيديو، ثم صورة، ثم أسود.
    used_queries: لتجنب التكرار (نحاول query مختلف إن سبق).
    """
    if used_queries is None:
        used_queries = []

    # إذا query استُخدم سابقاً، نغير عشوائياً ضمن pool
    if query in used_queries:
        alt_pool = POOLS_BY_TYPE.get("narration", [])
        for _ in range(5):
            alt = random.choice(alt_pool)
            if alt not in used_queries:
                query = alt
                break

    used_queries.append(query)

    # 1. محلي
    local = _find_local(query)
    if local:
        return local

    # 2. فيديو من Pixabay
    video = _fetch_video(query, work_dir)
    if video:
        return video

    # 3. صورة من Pixabay
    image = _fetch_image(query, work_dir)
    if image:
        return image

    # 4. fallback: صورة سوداء
    out = work_dir / "black.jpg"
    if not out.exists():
        Image.new("RGB", (VIDEO_W, VIDEO_H), (10, 10, 10)).save(out)
    return str(out)


# ═══ تجهيز النص (fit) ═══
def _fit_text(text, font_path, max_width, max_height, start_size, min_size):
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


def _draw_centered(draw, y, text, font, fill, stroke_w, stroke_fill):
    shaped = _shape(text)
    bbox = draw.textbbox((0, 0), shaped, font=font)
    x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), shaped, font=font, fill=fill,
              stroke_width=stroke_w, stroke_fill=stroke_fill)


# ═══ Overlay ═══
def make_overlay(seg, work_dir, episode_title=""):
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = _find_arabic_font()

    is_quran = seg.get("type") == "quran"
    is_cta = seg.get("type") in ("cta", "outro")
    text = seg.get("text", "")

    # شريط العنوان
    if episode_title:
        try:
            ft = ImageFont.truetype(font_path, 44, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            ft = ImageFont.truetype(font_path, 44)
        overlay.paste(Image.new("RGBA", (VIDEO_W, 100), (0, 0, 0, 180)), (0, 15))
        draw = ImageDraw.Draw(overlay)
        _draw_centered(draw, 38, episode_title, ft, "#D4AF37", 2, "black")

    if is_cta:
        panel_x, panel_y = 40, 170
        panel_w, panel_h = VIDEO_W - 80, VIDEO_H - 290
        panel = Image.new("RGBA", (panel_w, panel_h), (12, 8, 0, 235))
        pdraw = ImageDraw.Draw(panel)
        pdraw.rectangle([0, 0, panel_w - 1, panel_h - 1],
                        outline=(212, 175, 55, 255), width=8)
        overlay.paste(panel, (panel_x, panel_y))
        draw = ImageDraw.Draw(overlay)

        font, lines, lh = _fit_text(text, font_path,
                                    panel_w - 80, panel_h - 200,
                                    start_size=58, min_size=34)
        total_h = len(lines) * lh
        y = panel_y + 50 + max(0, (panel_h - 180 - total_h) // 2)
        for line in lines:
            _draw_centered(draw, y, line, font, "#F5D76E", 3, "black")
            y += lh

        try:
            fi = ImageFont.truetype(font_path, 44, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            fi = ImageFont.truetype(font_path, 44)
        _draw_centered(draw, panel_y + panel_h - 90,
                       "◆ شارك الفيديو    ◆ اشترك في القناة",
                       fi, "#FFD700", 2, "black")
    else:
        start = 56 if is_quran else 60
        color = ACCENT_COLOR if is_quran else TEXT_COLOR
        font, lines, lh = _fit_text(text, font_path,
                                    VIDEO_W - 100, VIDEO_H - 340,
                                    start_size=start, min_size=40)
        total_h = len(lines) * lh
        y = max(140, (VIDEO_H - total_h) // 2)
        bg_h = total_h + 70
        bg = Image.new("RGBA", (VIDEO_W, bg_h), (0, 0, 0, 150))
        overlay.paste(bg, (0, y - 35), bg)
        draw = ImageDraw.Draw(overlay)
        for line in lines:
            _draw_centered(draw, y, line, font, color, TEXT_STROKE_W, TEXT_STROKE)
            y += lh

        if is_quran and seg.get("display"):
            try:
                fd = ImageFont.truetype(font_path, 42, layout_engine=ImageFont.Layout.RAQM)
            except Exception:
                fd = ImageFont.truetype(font_path, 42)
            _draw_centered(draw, y + 25, f"﴿ {seg['display']} ﴾",
                           fd, "#D4AF37", 2, "black")

    # علامة مائية
    try:
        fw = ImageFont.truetype(font_path, 30, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        fw = ImageFont.truetype(font_path, 30)
    wm = _shape("ملاك المعرفة")
    bbox = draw.textbbox((0, 0), wm, font=fw)
    draw.text((VIDEO_W - (bbox[2] - bbox[0]) - 40, VIDEO_H - 70),
              wm, font=fw, fill=(212, 175, 55, 130))

    # شريط Teaser
    if seg.get("type") == "teaser" and seg.get("teaser_data"):
        td = seg["teaser_data"]
        strip_h = 320
        overlay.paste(Image.new("RGBA", (VIDEO_W, strip_h), (0, 0, 0, 225)),
                      (0, VIDEO_H - strip_h))
        draw = ImageDraw.Draw(overlay)
        try:
            fn = ImageFont.truetype(font_path, 42, layout_engine=ImageFont.Layout.RAQM)
            fu = ImageFont.truetype(font_path, 30, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            fn = ImageFont.truetype(font_path, 42)
            fu = ImageFont.truetype(font_path, 30)
        y0 = VIDEO_H - strip_h + 30
        _draw_centered(draw, y0, "▶ الحلقة القادمة", fn, ACCENT_COLOR, 2, "black")
        _draw_centered(draw, y0 + 70, td.get("next_episode_title", ""),
                       fn, TEXT_COLOR, 2, "black")
        _draw_centered(draw, y0 + 160,
                       f"◆ {td.get('url', '').replace('https://', '')}",
                       fu, ACCENT_COLOR, 2, "black")

    path = work_dir / f"overlay_{seg['index']:03d}.png"
    overlay.save(path)
    return str(path)


# ═══ بناء مقطع فيديو واحد ═══
def _make_segment_video(media_path: str, overlay_path: str, duration: float, out_path: Path):
    ext = Path(media_path).suffix.lower()

    if ext in (".mp4", ".mov", ".webm"):
        # فيديو متحرك
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", media_path,
            "-i", overlay_path,
            "-filter_complex",
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920[bg];[bg][1:v]overlay",
            "-t", str(duration),
            "-r", "24",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]
    else:
        # صورة ثابتة
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", media_path,
            "-i", overlay_path,
            "-filter_complex",
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920[bg];[bg][1:v]overlay",
            "-t", str(duration),
            "-r", "24",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]

    subprocess.run(cmd, check=True, capture_output=True)


# ═══ بناء الفيديو الكامل ═══
def build_video(audio_path, meta_path, output_mp4):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix="ep_video_"))
    concat_lines = []
    used_queries = []

    try:
        total_segs = len(meta["segments"])
        print(f"🎬 بناء الفيديو: {total_segs} مقطع", flush=True)

        for i, seg in enumerate(meta["segments"], 1):
            duration = seg["duration"]
            if duration <= 0:
                continue

            query = pick_visual_query(seg, seg["index"])
            print(f"   [{i}/{total_segs}] {seg.get('type','?')} "
                  f"({duration:.1f}s) → '{query}'", flush=True)

            media = fetch_media(query, work_dir, used_queries)
            ov = make_overlay(seg, work_dir, meta.get("title", ""))
            out_seg = work_dir / f"seg_{seg['index']:03d}.mp4"

            _make_segment_video(media, ov, duration, out_seg)
            concat_lines.append(f"file '{out_seg}'")

        if not concat_lines:
            raise RuntimeError("لا مقاطع فيديو صالحة")

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
