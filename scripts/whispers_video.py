#!/usr/bin/env python3
"""
scripts/whispers_video.py
=========================
المخرج السينمائي لمشروع «همسات ملك» (9:16 WhatsApp Status):
- مشاهد أنيميشن محتشم وفانتازيا (محلية من assets/scenes/ أو Pixabay مفلتر أو مجرّة مرسومة).
- طبقات سحرية متحركة: جنّيات ضوئية بأجنحة رقيقة، فراشات متوهجة، وبوكيه ذهبي.
- خط عربي أصيل (Amiri عبر libass/HarfBuzz) متزامن بالملّي ثانية مع الصوت + توهج ذهبي للأسماء الثلاثة.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENES_ROOT = REPO_ROOT / "assets" / "scenes"

VIDEO_W = 1080
VIDEO_H = 1920
FPS = 30
NUM_SCENES = 4
CROSSFADE = 1.0

PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()

_HARAKAT_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]")


# ==============================================================================
# 🎨 1) اختيار وتأمين المشاهد (محلي أولاً ثم Pixabay آمن ثم مجرّة مرسومة)
# ==============================================================================
def _get_local_scenes(category: str) -> list[Path]:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"}
    cat_dir = SCENES_ROOT / category
    files: list[Path] = []
    if cat_dir.exists():
        files.extend([p for p in cat_dir.iterdir() if p.is_file() and p.suffix.lower() in exts])
    if len(files) < NUM_SCENES and SCENES_ROOT.exists():
        extra = [p for p in SCENES_ROOT.rglob("*") if p.is_file() and p.suffix.lower() in exts and p not in files]
        random.shuffle(extra)
        files.extend(extra)
    random.shuffle(files)
    return files[:NUM_SCENES]


def _is_pixabay_safe(tags: str, category: str) -> bool:
    tl = (tags or "").lower()
    blacklist = [
        "bikini", "swim", "lingerie", "sexy", "nude", "kiss", "alcohol", "wine", "beer",
        "skeleton", "skull", "bone", "robot", "cyborg", "alien", "zombie", "horror",
        "scary", "creepy", "monster", "demon", "ghost", "devil", "satan", "occult",
        "gore", "blood", "war", "military", "weapon", "gun", "bomb", "death", "grave",
        "witch", "vampire", "clown", "spider", "snake",
    ]
    if any(b in tl for b in blacklist):
        return False

    if category == "prayer_niqab":
        whitelist = ["rose", "flower", "butterfly", "cat", "kitten", "moon", "lantern", "mosque", "sky", "garden"]
    elif category == "modest_love":
        whitelist = ["rose", "flower", "butterfly", "cat", "kitten", "rain", "window", "lantern", "candle", "garden", "bokeh"]
    else:
        whitelist = ["butterfly", "forest", "glow", "firefly", "aurora", "nebula", "star", "galaxy", "flower", "magic", "lake"]
    return any(w in tl for w in whitelist)


def _pixabay_queries(category: str) -> list[tuple[str, str]]:
    if category == "prayer_niqab":
        return [
            ("white roses butterflies", "film"),
            ("cute kitten flowers", "film"),
            ("crescent moon stars night", "film"),
            ("islamic lantern glowing", "film"),
            ("butterfly flowers animation", "animation"),
        ]
    if category == "modest_love":
        return [
            ("rain window night cozy", "film"),
            ("kitten watching stars", "film"),
            ("pink roses soft bokeh", "film"),
            ("lantern flowers garden", "film"),
        ]
    return [
        ("glowing forest fireflies", "film"),
        ("blue butterfly glowing", "animation"),
        ("aurora stars lake night", "film"),
        ("golden bokeh lights", "animation"),
        ("milky way stars reflection", "film"),
    ]


def _fetch_pixabay_video(query: str, vtype: str, category: str, work_dir: Path, used: set[str]) -> Path | None:
    if not PIXABAY_KEY:
        return None
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PIXABAY_KEY,
                "q": query,
                "video_type": vtype,
                "per_page": 40,
                "safesearch": "true",
            },
            timeout=20,
        )
        if r.status_code != 200:
            return None
        hits = r.json().get("hits", [])
        random.shuffle(hits)
        for h in hits:
            tags = h.get("tags", "")
            if not _is_pixabay_safe(tags, category):
                continue
            url = (h.get("videos") or {}).get("medium", {}).get("url")
            if url and url not in used:
                out = work_dir / f"px_{h['id']}.mp4"
                with open(out, "wb") as f:
                    for ch in requests.get(url, timeout=60, stream=True).iter_content(8192):
                        f.write(ch)
                used.add(url)
                print(f"   ✅ [Pixabay] {query} ({tags[:35]})", flush=True)
                return out
    except Exception:
        pass
    return None


def _make_celestial_fallback(out_jpg: Path, category: str, seed_idx: int = 0) -> Path:
    """يرسم لوحة سماء ليلية/فجرية حالمة بالهلال والنجوم وأزهار الضوء عند غياب الصور."""
    img = Image.new("RGB", (VIDEO_W, VIDEO_H))
    draw = ImageDraw.Draw(img, "RGBA")

    palettes = {
        "prayer_niqab": ((14, 24, 48), (62, 42, 76), (175, 118, 130)),
        "fantasy_fairies": ((8, 16, 44), (20, 62, 92), (82, 46, 112)),
        "modest_love": ((24, 14, 38), (74, 38, 68), (168, 108, 118)),
    }
    c1, c2, c3 = palettes.get(category, palettes["fantasy_fairies"])
    for y in range(VIDEO_H):
        t = y / VIDEO_H
        if t < 0.55:
            k = t / 0.55
            r = int(c1[0] + (c2[0] - c1[0]) * k)
            g = int(c1[1] + (c2[1] - c1[1]) * k)
            b = int(c1[2] + (c2[2] - c1[2]) * k)
        else:
            k = (t - 0.55) / 0.45
            r = int(c2[0] + (c3[0] - c2[0]) * k)
            g = int(c2[1] + (c3[1] - c2[1]) * k)
            b = int(c2[2] + (c3[2] - c2[2]) * k)
        draw.line([(0, y), (VIDEO_W, y)], fill=(r, g, b))

    # هالة سديمية ناعمة
    glow = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for _ in range(5):
        gx = random.randint(150, VIDEO_W - 150)
        gy = random.randint(250, VIDEO_H - 400)
        gr = random.randint(220, 420)
        col = random.choice([(120, 220, 255, 45), (255, 200, 140, 40), (220, 140, 255, 40)])
        gd.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=col)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=90))
    img.paste(glow, (0, 0), glow)

    # نجوم متلألئة
    stars = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stars)
    for _ in range(180):
        sx, sy = random.randint(0, VIDEO_W), random.randint(0, int(VIDEO_H * 0.85))
        sr = random.choice([1, 1, 2, 3])
        sa = random.randint(100, 245)
        sd.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=(255, 250, 230, sa))

    # هلال ناعم في الأعلى
    if seed_idx % 2 == 0:
        mx, my, mr = int(VIDEO_W * 0.75), 320, 75
        sd.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=(255, 240, 195, 210))
        sd.ellipse([mx - mr + 28, my - mr - 14, mx + mr + 28, my + mr - 14], fill=(0, 0, 0, 0))
        # قناع قطع الهلال
        moon_mask = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
        md = ImageDraw.Draw(moon_mask)
        md.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=(255, 242, 200, 220))
        md.ellipse([mx - mr + 30, my - mr - 12, mx + mr + 30, my + mr - 12], fill=(0, 0, 0, 0))
        moon_mask = moon_mask.filter(ImageFilter.GaussianBlur(radius=1.2))
        img.paste(moon_mask, (0, 0), moon_mask)

    img.paste(stars, (0, 0), stars)
    img.save(out_jpg, "JPEG", quality=94)
    return out_jpg


# ==============================================================================
# ✨ 2) طبقات الفانتازيا (جنيات ضوئية + فراشات مضيئة + بوكيه حالم)
# ==============================================================================
def _create_fairies_layer(out_png: Path):
    """يرسم جنيات ضوئية صغيرة لطيفة (كرات نور بأجنحة فراشة مضيئة رقيقة) وغبار سحري."""
    W, H = VIDEO_W + 400, VIDEO_H + 400
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # غبار النجوم
    for _ in range(130):
        x, y = random.randint(0, W), random.randint(0, H)
        r = random.randint(2, 4)
        col = random.choice([(255, 248, 210, 220), (190, 245, 255, 200), (255, 210, 240, 190)])
        draw.ellipse([x - r, y - r, x + r, y + r], fill=col)

    # 12 جنية ضوئية صغيرة
    for _ in range(12):
        cx = random.randint(150, W - 150)
        cy = random.randint(150, H - 250)
        sz = random.randint(16, 28)
        tint = random.choice([(255, 232, 160), (170, 242, 255), (255, 195, 235)])

        # أجنحة رقيقة
        draw.polygon([(cx, cy), (cx - sz, cy - sz // 2), (cx - int(sz * 0.7), cy - int(sz * 1.1))], fill=(*tint, 95))
        draw.polygon([(cx, cy), (cx + sz, cy - sz // 2), (cx + int(sz * 0.7), cy - int(sz * 1.1))], fill=(*tint, 95))
        draw.polygon([(cx, cy), (cx - int(sz * 0.7), cy + sz // 2), (cx - sz // 3, cy + sz)], fill=(*tint, 70))
        draw.polygon([(cx, cy), (cx + int(sz * 0.7), cy + sz // 2), (cx + sz // 3, cy + sz)], fill=(*tint, 70))

        # هالة ضوئية دافئة
        hr = int(sz * 1.1)
        draw.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=(*tint, 55))
        cr = max(4, sz // 4)
        draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(255, 255, 245, 240))

    img = img.filter(ImageFilter.GaussianBlur(radius=1.3))
    img.save(out_png)


def _create_butterflies_layer(out_png: Path):
    """يرسم فراشات مضيئة شبه شفافة بأحجام مختلفة."""
    W, H = VIDEO_W + 400, VIDEO_H + 400
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for _ in range(14):
        cx = random.randint(120, W - 120)
        cy = random.randint(120, H - 120)
        s = random.randint(20, 44)
        angle = random.uniform(-0.45, 0.45)
        col = random.choice([
            (255, 222, 130, 155),  # ذهبي
            (180, 238, 255, 145),  # فيروزي كريستال
            (255, 196, 222, 145),  # وردي ناعم
            (255, 248, 232, 165),  # لؤلؤي
        ])

        def rot(px, py):
            ca, sa = math.cos(angle), math.sin(angle)
            return (cx + px * ca - py * sa, cy + px * sa + py * ca)

        # جناح علوي أيسر وأيمن + جناح سفلي
        left_top = [rot(0, 0), rot(-s * 1.1, -s * 0.9), rot(-s * 1.25, -s * 0.1), rot(-s * 0.35, s * 0.15)]
        right_top = [rot(0, 0), rot(s * 1.1, -s * 0.9), rot(s * 1.25, -s * 0.1), rot(s * 0.35, s * 0.15)]
        left_bot = [rot(0, 0), rot(-s * 0.85, s * 0.2), rot(-s * 0.65, s * 0.85), rot(-s * 0.1, s * 0.45)]
        right_bot = [rot(0, 0), rot(s * 0.85, s * 0.2), rot(s * 0.65, s * 0.85), rot(s * 0.1, s * 0.45)]

        draw.polygon(left_top, fill=col)
        draw.polygon(right_top, fill=col)
        draw.polygon(left_bot, fill=(col[0], col[1], col[2], int(col[3] * 0.78)))
        draw.polygon(right_bot, fill=(col[0], col[1], col[2], int(col[3] * 0.78)))
        draw.ellipse([cx - 3, cy - s * 0.3, cx + 3, cy + s * 0.35], fill=(255, 252, 235, 210))

    img = img.filter(ImageFilter.GaussianBlur(radius=1.0))
    img.save(out_png)


def _create_bokeh_layer(out_png: Path):
    W, H = VIDEO_W + 400, VIDEO_H + 400
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for _ in range(55):
        x, y = random.randint(0, W), random.randint(0, H)
        r = random.randint(24, 85)
        alpha = random.randint(35, 115)
        color = random.choice([(255, 215, 110), (255, 235, 175), (210, 185, 255), (175, 235, 255)])
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(*color, alpha))
    img = img.filter(ImageFilter.GaussianBlur(radius=16))
    img.save(out_png)


# ==============================================================================
# ✍️ 3) توليد ملف الترجمة الفخمة ASS (HarfBuzz + توهج الأسماء الثلاثة)
# ==============================================================================
def _ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _wrap_arabic_lines(text: str, max_chars_per_line: int = 34) -> str:
    """توزيع أنيق للسطر حتى لا يمتد لأطراف الشاشة."""
    words = text.split()
    lines, cur = [], []
    cur_len = 0
    for w in words:
        plain_len = len(_HARAKAT_RE.sub("", w))
        if cur and (cur_len + plain_len + 1) > max_chars_per_line:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = plain_len
        else:
            cur.append(w)
            cur_len += plain_len + (1 if cur_len else 0)
    if cur:
        lines.append(" ".join(cur))
    return r"\N".join(lines)


def _highlight_names_ass(wrapped_text: str) -> str:
    """يُلبّس أسماءها الثلاثة (ملك، مريم، ملاك) توهجاً ذهنياً ذهبياً لطيفاً داخل الجملة."""
    out_lines = []
    for line in wrapped_text.split(r"\N"):
        tokens = line.split(" ")
        new_toks = []
        for tok in tokens:
            skel = _HARAKAT_RE.sub("", tok)
            skel_clean = re.sub(r"[^\u0621-\u064A]", "", skel)
            if skel_clean in ("ملك", "مريم", "ملاك"):
                # ذهبي متوهج #FFD86B -> ASS BGR = &H006BD8FF&
                new_toks.append(r"{\c&H006BD8FF&\3c&H60103855&\blur2}" + tok + r"{\r}")
            else:
                new_toks.append(tok)
        out_lines.append(" ".join(new_toks))
    return r"\N".join(out_lines)


def _write_ass_subtitles(ep: dict, meta: dict | None, total_dur: float, out_ass: Path):
    title = (ep.get("title") or "همسة لروحها").strip()
    # إزالة التشكيل عن «ملك» إن وردت في العنوان
    title = re.sub(r"م[\u064B-\u065F]*ل[\u064B-\u065F]*ك[\u064B-\u065F]*", "ملك", title)

    header = f"""[Script Info]
Title: {title}
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TitleTop,Amiri,46,&H209CE0FF,&H000000FF,&H90100818,&H80000000,0,0,0,0,100,100,0,0,1,2.5,2,8,70,70,150,1
Style: WhisperLine,Amiri,64,&H00F8F5EE,&H000000FF,&H70120A1E,&H80000000,0,0,0,0,100,100,0,0,1,3.5,3,2,75,75,330,1
Style: NameHalo,Amiri,94,&H006BD8FF,&H000000FF,&H500C2B42,&H80000000,1,0,0,0,100,100,0,0,1,4.5,4,2,60,60,540,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []

    # 1) عنوان لطيف في الأعلى خلال أول 3.5 ثوانٍ
    t_end_title = min(total_dur, 3.8)
    events.append(
        f"Dialogue: 0,{_ass_time(0.2)},{_ass_time(t_end_title)},TitleTop,,0,0,0,,"
        + r"{\fad(500,600)}"
        + f"🌙 {title}"
    )

    # 2) المقاطع المتزامنة بالملّي ثانية
    segs = (meta or {}).get("segments") or ep.get("segments") or []
    if segs and all("start_sec" in s and "end_sec" in s for s in segs):
        for s in segs:
            st = float(s["start_sec"])
            en = min(total_dur, float(s["end_sec"]) + 0.35)
            disp = (s.get("display_text") or s.get("text") or "").strip()
            if not disp:
                continue
            wrapped = _highlight_names_ass(_wrap_arabic_lines(disp))
            events.append(
                f"Dialogue: 1,{_ass_time(st)},{_ass_time(en)},WhisperLine,,0,0,0,,"
                + r"{\fad(350,300)}"
                + wrapped
            )
            # في جملة الذروة، عرض هالة للأسماء المذكورة بنعومة فوق السطر
            if s.get("is_climax") and s.get("featured_names"):
                names_str = "  •  ".join(s["featured_names"])
                events.append(
                    f"Dialogue: 2,{_ass_time(st + 0.25)},{_ass_time(en)},NameHalo,,0,0,0,,"
                    + r"{\fad(550,450)\blur2.5}"
                    + f"✦ {names_str} ✦"
                )
    else:
        # احتياط تقسيم متساوٍ لو لم يوجد _meta.json
        n = max(1, len(segs))
        chunk_d = total_dur / n
        for i, s in enumerate(segs):
            st, en = i * chunk_d, (i + 1) * chunk_d
            disp = (s.get("display_text") or s.get("text") or "").strip()
            if not disp:
                continue
            wrapped = _highlight_names_ass(_wrap_arabic_lines(disp))
            events.append(
                f"Dialogue: 1,{_ass_time(st)},{_ass_time(en)},WhisperLine,,0,0,0,,"
                + r"{\fad(350,300)}"
                + wrapped
            )

    out_ass.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


# ==============================================================================
# 🎬 4) المخرج الرئيسي للفيديو
# ==============================================================================
def build_whisper_video(audio_path: str, json_path: str, out_mp4: str) -> bool:
    audio_p = Path(audio_path).resolve()
    ep_p = Path(json_path).resolve()
    out_p = Path(out_mp4).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    ep = json.loads(ep_p.read_text(encoding="utf-8"))
    meta = None
    for cand in [
        audio_p.with_name(f"{audio_p.stem}_meta.json"),
        audio_p.with_name(f"{audio_p.stem.replace('_malak', '')}_meta.json"),
    ]:
        if cand.exists():
            meta = json.loads(cand.read_text(encoding="utf-8"))
            break

    category = ep.get("scene_category") or ep.get("category") or "fantasy_fairies"
    if category == "romantic":
        category = "fantasy_fairies"
    elif category == "dua":
        category = "prayer_niqab"

    dur_out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_p)],
        capture_output=True, text=True,
    ).stdout.strip()
    total_dur = max(6.0, float(dur_out or 15.0))
    print(f"⏱️ مدة الصوت: {total_dur:.2f} ث | المحور البصري: {category}", flush=True)

    work = Path(tempfile.mkdtemp(prefix="whisper_vid_"))
    try:
        # 1) تجميع المشاهد
        scenes = _get_local_scenes(category)
        used_urls: set[str] = set()
        if len(scenes) < NUM_SCENES and PIXABAY_KEY:
            px_queries = _pixabay_queries(category)
            random.shuffle(px_queries)
            for q, vt in px_queries:
                if len(scenes) >= NUM_SCENES:
                    break
                vid = _fetch_pixabay_video(q, vt, category, work, used_urls)
                if vid:
                    scenes.append(vid)

        while len(scenes) < NUM_SCENES:
            fb_img = work / f"celestial_{len(scenes)}.jpg"
            _make_celestial_fallback(fb_img, category, seed_idx=len(scenes))
            scenes.append(fb_img)

        scene_dur = (total_dur + (len(scenes) - 1) * CROSSFADE) / len(scenes)
        frames_per_scene = max(30, int(round(scene_dur * FPS)))
        print(f"🎬 تجهيز {len(scenes)} مشاهد × {scene_dur:.1f} ث...", flush=True)

        # 2) معالجة كل مشهد (توحيد المقاس والتلوين السينمائي)
        processed: list[Path] = []
        for i, sp in enumerate(scenes):
            sc_out = work / f"scene_{i:02d}.mp4"
            is_img = sp.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")

            if is_img:
                # حركة Ken Burns حريرية للصور بدون تكرار فك التشفير
                zoom_dir = "min(zoom+0.0007,1.12)" if i % 2 == 0 else "max(1.12-0.0007*on,1.0)"
                vf = (
                    f"scale={VIDEO_W*2}:{VIDEO_H*2}:force_original_aspect_ratio=increase,"
                    f"crop={VIDEO_W*2}:{VIDEO_H*2},"
                    f"zoompan=z='{zoom_dir}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d={frames_per_scene}:s={VIDEO_W}x{VIDEO_H}:fps={FPS},"
                    "colorbalance=rs=0.05:gs=0.01:bs=0.03,"
                    "eq=contrast=1.06:saturation=1.14,"
                    "vignette=PI/5,format=yuv420p"
                )
                cmd = [
                    "ffmpeg", "-y", "-i", str(sp), "-t", f"{scene_dur:.3f}",
                    "-vf", vf, "-r", str(FPS),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", str(sc_out),
                ]
            else:
                vf = (
                    f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
                    f"crop={VIDEO_W}:{VIDEO_H},fps={FPS},"
                    "colorbalance=rs=0.05:gs=0.01:bs=0.03,"
                    "eq=contrast=1.06:saturation=1.14,"
                    "vignette=PI/5,format=yuv420p"
                )
                cmd = [
                    "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(sp), "-t", f"{scene_dur:.3f}",
                    "-vf", vf, "-an",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", str(sc_out),
                ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
            processed.append(sc_out)

        # 3) انتقال ناعم بين المشاهد (Crossfade)
        bg_vid = work / "bg_merged.mp4"
        if len(processed) == 1:
            shutil.copy2(processed[0], bg_vid)
        else:
            inputs = []
            for p in processed:
                inputs.extend(["-i", str(p)])
            fc, last, offset = [], "[0:v]", 0.0
            for i in range(1, len(processed)):
                offset += scene_dur - CROSSFADE
                nxt = f"[x{i}]"
                fc.append(f"{last}[{i}:v]xfade=transition=fade:duration={CROSSFADE}:offset={offset:.3f}{nxt}")
                last = nxt
            subprocess.run(
                ["ffmpeg", "-y"] + inputs + [
                    "-filter_complex", ";".join(fc), "-map", last,
                    "-t", f"{total_dur:.3f}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", str(bg_vid),
                ],
                check=True, capture_output=True, timeout=240,
            )

        # 4) توليد طبقات السحر (جنيات، فراشات، بوكيه) + ملف الترجمة ASS
        fairies_png = work / "fairies.png"
        butterflies_png = work / "butterflies.png"
        bokeh_png = work / "bokeh.png"
        ass_file = work / "subtitles.ass"

        _create_fairies_layer(fairies_png)
        _create_butterflies_layer(butterflies_png)
        _create_bokeh_layer(bokeh_png)
        _write_ass_subtitles(ep, meta, total_dur, ass_file)

        # 5) التركيب النهائي + دمج الصوت + ضغط واتساب الذكي
        print("✨ دمج الجنيات الضوئية والفراشات والخط العربي المتزامن...", flush=True)
        ass_escaped = str(ass_file).replace("\\", "/").replace(":", "\\:")
        fc_final = (
            "[1:v]format=rgba,colorchannelmixer=aa=0.85[fr];"
            "[2:v]format=rgba,colorchannelmixer=aa=0.78[bf];"
            "[3:v]format=rgba,colorchannelmixer=aa=0.38[bk];"
            "[0:v][bk]overlay=x='-200+70*cos(t/9)':y='-200+55*sin(t/11)':shortest=1[v1];"
            "[v1][fr]overlay=x='-200+90*sin(t/5)':y='-200+70*cos(t/6)':shortest=1[v2];"
            "[v2][bf]overlay=x='-200+80*cos(t/7)':y='-200+85*sin(t/8)':shortest=1[v3];"
            f"[v3]ass='{ass_escaped}':fontsdir=/usr/share/fonts,format=yuv420p[vout]"
        )

        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(bg_vid),
                "-loop", "1", "-i", str(fairies_png),
                "-loop", "1", "-i", str(butterflies_png),
                "-loop", "1", "-i", str(bokeh_png),
                "-i", str(audio_p),
                "-filter_complex", fc_final,
                "-map", "[vout]", "-map", "4:a",
                "-t", f"{total_dur:.3f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-maxrate", "3.2M", "-bufsize", "6.4M",
                "-profile:v", "high", "-level", "4.0",
                "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                str(out_p),
            ],
            check=True, capture_output=True, timeout=360,
        )

        size_mb = out_p.stat().st_size / (1024 * 1024)
        print(f"✅ تم إخراج التحفة الفنية: {out_p.name} ({size_mb:.2f} MB)", flush=True)
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python scripts/whispers_video.py <audio.mp3> <episode.json> <out.mp4>")
        sys.exit(1)
    build_whisper_video(sys.argv[1], sys.argv[2], sys.argv[3])
