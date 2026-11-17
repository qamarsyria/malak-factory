"""
story_video.py — بناء الفيديو (نسختان)
- yt.mp4    : 1920×1080، بدون اسم قارئ
- social.mp4: 1080×1920، مع اسم قارئ

يدعم المنقبات: عندما يكون المشهد عن امرأة (مريم، آسية، خديجة...)
يبحث عن "woman in niqab" بدلاً من منظر طبيعي.
"""

import os
import re
import sys
import json
import random
import subprocess
import tempfile
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import PIXABAY_KEY, BG_VIDEOS_DIR, BG_IMAGES_DIR
from utilities import cleanup_dir
from filters_loader import is_safe_tag, is_safe_query

# ═══════════════════════════════════════════════════════════
# الأبعاد
# ═══════════════════════════════════════════════════════════
DIMENSIONS = {
    "yt":     {"w": 1920, "h": 1080},
    "social": {"w": 1080, "h": 1920},
}

# ═══════════════════════════════════════════════════════════
# الخط العربي
# ═══════════════════════════════════════════════════════════
def find_arabic_font() -> str:
    candidates = [
        "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/hosny-amiri/Amiri-Bold.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    try:
        r = subprocess.run(["fc-list", ":lang=ar", "--format=%{file}\n"],
                           capture_output=True, text=True)
        files = [f.strip() for f in r.stdout.splitlines() if f.strip()]
        if files:
            return files[0]
    except Exception:
        pass
    raise RuntimeError("❌ لم يُعثر على أي خط عربي")


try:
    FONT_PATH = find_arabic_font()
    print(f"✅ الخط: {FONT_PATH}")
except Exception as e:
    print(f"⚠️ {e}")
    FONT_PATH = None


# ═══════════════════════════════════════════════════════════
# كلمات تدل على أن المشهد عن امرأة
# ═══════════════════════════════════════════════════════════
WOMEN_KEYWORDS = [
    "مريم", "آسية", "خديجة", "عائشة", "فاطمة", "حفصة", "زينب",
    "المرأة", "النساء", "امرأة", "أم ", "بنت", "زوجة", "ابنة",
    "حامل", "الحمل", "وضع", "رضيع", "مولود", "ولادة",
]

# ═══════════════════════════════════════════════════════════
# كلمات مفتاحية → استعلامات بصرية
# ═══════════════════════════════════════════════════════════
KEYWORD_MAP = {
    # ماء
    "بحر": ["ocean waves cinematic", "sea sunset calm"],
    "موج": ["ocean waves", "stormy sea"],
    "ماء": ["river flowing", "waterfall nature"],
    "نهر": ["river flowing", "river sunset"],
    "عين": ["spring water nature", "natural spring"],
    "بئر": ["ancient well desert", "old stone well"],
    "مطر": ["rain falling", "rain window"],
    "سيل": ["flood water"],
    "طوفان": ["flood water", "massive flood"],

    # جبال / صحراء
    "جبل": ["mountain peak misty", "mountain sunset"],
    "صحراء": ["desert dunes sunset", "sahara desert"],
    "رمل": ["sand dunes desert", "desert sand wind"],
    "واد": ["valley mountains", "green valley"],

    # سماء / ليل
    "سماء": ["sky clouds", "sky sunset"],
    "نجم": ["starry night milky way", "night sky stars"],
    "قمر": ["moon night sky", "full moon"],
    "غيم": ["clouds sky", "clouds sunset"],

    # نبات
    "شجر": ["trees forest", "forest sun rays"],
    "نخل": ["palm trees", "date palm"],
    "زهر": ["flower bloom", "rose flower"],
    "ثمر": ["fruit tree", "orchard"],

    # حيوانات
    "طير": ["bird flying", "bird nature"],
    "فراش": ["butterfly flowers", "butterfly garden"],
    "خيل": ["horse running", "horse desert"],
    "نحل": ["bee flower", "bee hive"],

    # قوة / غضب
    "نار": ["fire flames", "campfire"],
    "بركان": ["volcano eruption", "lava volcano"],
    "زلزال": ["earthquake crack", "rocky cliff"],

    # مكان
    "مسجد": ["mosque architecture", "mosque sunset"],
    "كعبة": ["kaaba mecca", "kaaba aerial"],
    "قدس": ["al aqsa mosque", "jerusalem mosque"],
    "شام": ["old damascus", "damascus aerial"],
    "دمشق": ["old damascus", "damascus city"],
    "مصر": ["ancient egypt nile", "nile river"],

    # مفاهيم
    "رؤيا": ["starry night sky", "clouds night"],
    "حلم": ["foggy morning", "sunrise mystical"],
    "ملك": ["ancient palace", "royal palace"],
    "سجن": ["dark stone wall", "cell window"],
    "عرش": ["ancient throne", "golden throne"],
}

# استعلامات المنقبات (تُستخدم فقط إذا المشهد عن امرأة)
NIQAB_SCENES = [
    "woman in niqab",
    "niqab woman praying",
    "niqab woman reading quran",
    "niqab woman walking",
    "muslim woman niqab",
]

DEFAULT_VISUALS = [
    "islamic architecture mosque sunset",
    "starry night desert",
    "ancient stone ruins",
    "mountain misty cinematic",
    "old damascus alley",
]


def scene_is_about_woman(text: str) -> bool:
    return any(kw in text for kw in WOMEN_KEYWORDS)


def suggest_visual_query(scene_text: str) -> str:
    # 1. إذا المشهد عن امرأة → منقبة
    if scene_is_about_woman(scene_text):
        return random.choice(NIQAB_SCENES)

    # 2. كلمة مفتاحية
    for kw, queries in KEYWORD_MAP.items():
        if kw in scene_text:
            return random.choice(queries)

    # 3. افتراضي
    return random.choice(DEFAULT_VISUALS)


# ═══════════════════════════════════════════════════════════
# scenes
# ═══════════════════════════════════════════════════════════
def detect_scenes(segments: list) -> list:
    scenes = []
    current = None

    for seg in segments:
        stype = seg.get("type", "")
        if stype == "narration" or stype in ("summary", "teaser", "cta"):
            current = {
                "scene_id": len(scenes),
                "segments": [seg],
                "keywords_text": _extract_text(seg),
            }
            scenes.append(current)
        else:
            if current is None:
                current = {"scene_id": 0, "segments": [], "keywords_text": ""}
                scenes.append(current)
            current["segments"].append(seg)

    return scenes


def _extract_text(seg: dict) -> str:
    chunks = seg.get("chunks", [])
    return " ".join(c.get("text", "") for c in chunks)[:500]


# ═══════════════════════════════════════════════════════════
# جلب وسائط
# ═══════════════════════════════════════════════════════════
def _find_local(query: str) -> str | None:
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    for base in (Path(BG_VIDEOS_DIR), Path(BG_IMAGES_DIR)):
        if not base.exists():
            continue
        for f in base.glob(f"*{slug}*"):
            if f.suffix.lower() in (".mp4", ".mov", ".webm", ".jpg", ".png") and f.stat().st_size > 10000:
                return str(f)
    return None


def fetch_media(query: str, work_dir: Path, used_urls: set) -> str | None:
    if not is_safe_query(query):
        print(f"   ❌ استعلام غير آمن: {query}")
        return None

    local = _find_local(query)
    if local:
        print(f"   📁 محلي: {Path(local).name[:50]}")
        return local

    if not PIXABAY_KEY:
        return None

    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PIXABAY_KEY,
                "q": query,
                "video_type": "film",
                "safesearch": "true",
                "per_page": 30,
                "min_width": 1080,
            },
            timeout=20,
        )
        if r.status_code != 200:
            return None

        hits = r.json().get("hits", [])
        random.shuffle(hits)

        for hit in hits:
            tags = hit.get("tags", "")
            if not is_safe_tag(tags):
                continue

            url = (hit.get("videos", {}).get("large", {}).get("url")
                   or hit.get("videos", {}).get("medium", {}).get("url"))
            if not url or url in used_urls:
                continue

            slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:30]
            out = work_dir / f"vid_{slug}_{hit['id']}.mp4"

            try:
                with open(out, "wb") as f:
                    for chunk in requests.get(url, timeout=60, stream=True).iter_content(8192):
                        f.write(chunk)
                if out.stat().st_size > 50000:
                    used_urls.add(url)
                    print(f"   ✅ {query} → {out.name[:50]}")
                    return str(out)
            except Exception as e:
                print(f"   ⚠️ تحميل فشل: {str(e)[:80]}")
    except Exception as e:
        print(f"   ⚠️ Pixabay فشل: {str(e)[:100]}")

    return None


# ═══════════════════════════════════════════════════════════
# رسم النص
# ═══════════════════════════════════════════════════════════
def _shape(text: str) -> str:
    return arabic_reshaper.reshape(str(text))


def _wrap(text: str, font, max_w: int, draw) -> list:
    words = text.split()
    lines, current = [], ""
    for w in words:
        trial = (current + " " + w).strip() if current else w
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_w:
            current = trial
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def _draw_centered(draw, y, text, font, fill, stroke_w, stroke_fill, canvas_w):
    shaped = _shape(text)
    bbox = draw.textbbox((0, 0), shaped, font=font)
    x = (canvas_w - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), shaped, font=font, fill=fill,
              stroke_width=stroke_w, stroke_fill=stroke_fill)


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


def make_caption_overlay(chunk_text, display_ref, reciter_name, is_quran,
                         ep_title, out_path, W, H, accent_color="#FFD700",
                         show_reciter=True):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    if not FONT_PATH:
        raise RuntimeError("لا يمكن رسم النص بدون خط عربي")

    # عنوان الحلقة
    if ep_title:
        ep_title = re.sub(r'[a-zA-Z0-9]', '', ep_title)
        try:
            ft = ImageFont.truetype(FONT_PATH, 38, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            ft = ImageFont.truetype(FONT_PATH, 38)
        _draw_centered(draw, 50, ep_title, ft, "#D4AF37", 5, "black", W)

    # نص المقطع
    chunk_text = re.sub(r'[*#@_\[\]\(\)]', '', chunk_text or "")
    chunk_text = re.sub(r'[a-zA-Z0-9]', '', chunk_text).strip()
    chunk_text = chunk_text.replace("ﷺ", "صلى الله عليه وسلم")

    if display_ref:
        display_ref = re.sub(r'[a-zA-Z]', '', display_ref).strip()

    if not chunk_text and not is_quran:
        overlay.save(out_path)
        return

    color = accent_color if is_quran else "white"
    display_text = chunk_text + (f"\n﴿ {display_ref} ﴾" if is_quran and display_ref else "")

    font, lines, lh = _fit_text(display_text, FONT_PATH, W - 120, H - 400, 70, 45)

    total_h = len(lines) * lh
    y = max(200, (H - total_h) // 2)

    for i, line in enumerate(lines):
        line_color = color if i < len(lines) - 1 or not is_quran else "#D4AF37"
        _draw_centered(draw, y, line, font, line_color, 6 if not is_quran else 8, "black", W)
        y += lh

    # اسم القارئ (Social فقط + quran فقط)
    if is_quran and reciter_name and show_reciter:
        try:
            rfont = ImageFont.truetype(FONT_PATH, 32, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            rfont = ImageFont.truetype(FONT_PATH, 32)
        _draw_centered(draw, H - 180, f"بصوت: {reciter_name}", rfont,
                       "#E8D5A0", 4, "black", W)

    # watermark
    try:
        wm_font = ImageFont.truetype(FONT_PATH, 40, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        wm_font = ImageFont.truetype(FONT_PATH, 40)
    wm_text = _shape("ملاك المعرفة")
    wm_bbox = draw.textbbox((0, 0), wm_text, font=wm_font)
    draw.text(
        (W - (wm_bbox[2] - wm_bbox[0]) - 40, H - 80),
        wm_text, font=wm_font, fill="#D4AF37",
        stroke_width=4, stroke_fill="black"
    )

    overlay.save(out_path)


# ═══════════════════════════════════════════════════════════
# معالجة خلفية
# ═══════════════════════════════════════════════════════════
def get_duration(path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def prep_background(bg_file: str, duration: float, W: int, H: int, out_path: Path):
    is_img = bg_file.endswith(('.jpg', '.jpeg', '.png'))
    input_flags = ["-loop", "1"] if is_img else ["-stream_loop", "-1"]
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{H},fps=30,format=yuv420p,setsar=1")
    subprocess.run(
        ["ffmpeg", "-y"] + input_flags + [
            "-i", bg_file, "-t", str(duration),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
            "-pix_fmt", "yuv420p",
            str(out_path)
        ],
        check=True, capture_output=True
    )


# ═══════════════════════════════════════════════════════════
# بناء فيديو
# ═══════════════════════════════════════════════════════════
def build_video(meta: dict, audio_path: str, output_mp4: str,
                W: int, H: int, scene_bg_map: dict, work_dir: Path,
                show_reciter: bool = True):
    segments = meta["segments"]
    ep_title = meta.get("title", "")
    scenes = detect_scenes(segments)

    # إعداد خلفيات
    scene_bg_videos = {}
    for scene in scenes:
        sid = scene["scene_id"]
        bg = scene_bg_map.get(sid)
        if not bg:
            continue
        scene_duration = sum(
            c.get("duration", 0)
            for seg in scene["segments"]
            for c in seg.get("chunks", [])
        )
        if scene_duration <= 0:
            continue
        bg_video = work_dir / f"bg_scene_{sid}_{W}x{H}.mp4"
        prep_background(bg, scene_duration, W, H, bg_video)
        scene_bg_videos[sid] = bg_video

    concat_lines = []
    for seg_idx, seg in enumerate(segments):
        seg_duration = sum(c.get("duration", 0) for c in seg.get("chunks", []))
        if seg_duration <= 0:
            continue

        # ابحث عن scene_id
        scene_id = None
        for scene in scenes:
            if seg in scene["segments"]:
                scene_id = scene["scene_id"]
                break

        bg_video = scene_bg_videos.get(scene_id)
        if not bg_video:
            black = work_dir / "black.jpg"
            if not black.exists():
                Image.new("RGB", (W, H), (10, 10, 10)).save(black)
            bg_video = work_dir / f"black_scene_{scene_id}.mp4"
            prep_background(str(black), seg_duration, W, H, bg_video)

        is_quran = seg.get("type") == "quran"
        display_ref = seg.get("display", "")
        reciter = seg.get("reciter", "")
        chunks = seg.get("chunks", [])

        text_inputs, text_filters = [], []
        actual_chunks = 0

        for c_idx, chunk_data in enumerate(chunks):
            c_text = chunk_data.get("text", "")
            c_dur = chunk_data.get("duration", 0)
            if not c_text.strip():
                continue

            overlay_png = work_dir / f"ov_{seg_idx}_{c_idx}.png"
            make_caption_overlay(
                c_text, display_ref, reciter, is_quran,
                ep_title, overlay_png, W, H,
                show_reciter=show_reciter
            )
            text_inputs.extend(["-loop", "1", "-i", str(overlay_png)])
            text_filters.append(
                f"[{actual_chunks}:v]trim=start=0:end={c_dur},"
                f"setpts=PTS-STARTPTS,format=rgba[t{actual_chunks}]"
            )
            actual_chunks += 1

        text_video_path = work_dir / f"text_{seg_idx}.mov"
        if actual_chunks == 0:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi",
                 "-i", f"color=c=black@0.0:s={W}x{H}:d={seg_duration},format=rgba",
                 "-c:v", "qtrle", "-pix_fmt", "rgba", str(text_video_path)],
                check=True, capture_output=True
            )
        else:
            text_filters.append(
                f"{''.join(f'[t{k}]' for k in range(actual_chunks))}"
                f"concat=n={actual_chunks}:v=1:a=0[text]"
            )
            subprocess.run(
                ["ffmpeg", "-y"] + text_inputs + [
                    "-filter_complex", ";".join(text_filters),
                    "-map", "[text]", "-t", str(seg_duration),
                    "-c:v", "qtrle", "-pix_fmt", "rgba",
                    str(text_video_path)
                ],
                check=True, capture_output=True
            )

        out_seg = work_dir / f"seg_{seg_idx}.mp4"
        subprocess.run(
            ["ffmpeg", "-y",
             "-i", str(bg_video),
             "-i", str(text_video_path),
             "-filter_complex", "[0:v][1:v]overlay",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
             "-pix_fmt", "yuv420p",
             str(out_seg)],
            check=True, capture_output=True
        )
        concat_lines.append(f"file '{out_seg}'")

    audio_dur = get_duration(audio_path)
    (work_dir / f"concat_{W}x{H}.txt").write_text("\n".join(concat_lines), encoding="utf-8")

    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "concat", "-safe", "0",
         "-i", str(work_dir / f"concat_{W}x{H}.txt"),
         "-i", audio_path,
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
         "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k",
         "-t", str(audio_dur),
         output_mp4],
        check=True, capture_output=True
    )
    size_mb = Path(output_mp4).stat().st_size / 1024 / 1024
    print(f"✅ {Path(output_mp4).name}: {size_mb:.1f} MB | {audio_dur/60:.1f} دقيقة")


# ═══════════════════════════════════════════════════════════
# تجهيز الخلفيات (مرة واحدة للنسختين)
# ═══════════════════════════════════════════════════════════
def prepare_scene_backgrounds(meta: dict, work_dir: Path) -> dict:
    scenes = detect_scenes(meta["segments"])
    used_urls = set()
    scene_bg_map = {}

    print(f"🎨 تحضير {len(scenes)} خلفية...")

    for scene in scenes:
        sid = scene["scene_id"]
        query = suggest_visual_query(scene["keywords_text"])
        label = "🕌 منقبة" if query in NIQAB_SCENES else "🎬"
        print(f"   {label} Scene {sid}: '{query}'")

        bg = fetch_media(query, work_dir, used_urls)
        if bg:
            scene_bg_map[sid] = bg
        else:
            print(f"   ⚠️ Scene {sid}: لا خلفية")

    return scene_bg_map


# ═══════════════════════════════════════════════════════════
# الدوال الرئيسية
# ═══════════════════════════════════════════════════════════
def main():
    if len(sys.argv) < 5:
        print("الاستخدام: python story_video.py <meta.json> <audio.mp3> <output.mp4> <yt|social>")
        sys.exit(1)

    meta_path, audio_path, output_path, orientation = sys.argv[1:5]
    if orientation not in DIMENSIONS:
        sys.exit(1)

    dims = DIMENSIONS[orientation]
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    work_dir = Path(tempfile.mkdtemp(prefix=f"ep_{orientation}_"))

    try:
        scene_bg_map = prepare_scene_backgrounds(meta, work_dir)
        show_reciter = (orientation == "social")
        build_video(meta, audio_path, output_path, dims["w"], dims["h"],
                    scene_bg_map, work_dir, show_reciter=show_reciter)
    finally:
        cleanup_dir(work_dir)


def main_both():
    if len(sys.argv) < 6:
        print("الاستخدام: python story_video.py --both <yt_meta> <yt_mp3> <social_meta> <social_mp3>")
        sys.exit(1)

    yt_meta_path, yt_audio, social_meta_path, social_audio = sys.argv[2:6]
    yt_meta = json.loads(Path(yt_meta_path).read_text(encoding="utf-8"))
    social_meta = json.loads(Path(social_meta_path).read_text(encoding="utf-8"))

    work_dir = Path(tempfile.mkdtemp(prefix="ep_video_both_"))

    try:
        # خلفيات مشتركة
        scene_bg_map = prepare_scene_backgrounds(yt_meta, work_dir)

        # YT
        print("\n📹 بناء YT (1920×1080)...")
        yt_out = f"videos/{Path(yt_meta_path).stem.replace('_meta', '')}.mp4"
        Path(yt_out).parent.mkdir(parents=True, exist_ok=True)
        build_video(yt_meta, yt_audio, yt_out, 1920, 1080,
                    scene_bg_map, work_dir, show_reciter=False)

        # Social
        print("\n📱 بناء Social (1080×1920)...")
        social_out = f"videos/{Path(social_meta_path).stem.replace('_meta', '')}.mp4"
        build_video(social_meta, social_audio, social_out, 1080, 1920,
                    scene_bg_map, work_dir, show_reciter=True)

        print("\n✅ اكتمل الفيديوان")
    finally:
        cleanup_dir(work_dir)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--both":
        main_both()
    else:
        main()
