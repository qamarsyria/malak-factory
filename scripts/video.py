"""تركيب الفيديو: خلفية + نص عربي + صوت."""
import os
import random
import subprocess
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip
from config import (
    VIDEO_W, VIDEO_H, VIDEO_FPS, VIDEO_DURATION_MAX,
    FONT_SIZE, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR,
    PIXABAY_KEY, PIXABAY_QUERY, BG_FALLBACK,
    BG_OVERLAY_OPACITY, KEN_BURNS_ZOOM,
)


def _find_arabic_font():
    """يبحث عن خط عربي متوفر في النظام."""
    if FONT_PATH and os.path.exists(FONT_PATH):
        print(f"✅ الخط (مخصص): {FONT_PATH}")
        return FONT_PATH

    candidates = [
        "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/amiri/Amiri-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            print(f"✅ الخط المكتشف: {c}")
            return c

    try:
        result = subprocess.run(
            ["fc-list", ":lang=ar", "--format=%{file}\n"],
            capture_output=True, text=True, timeout=10,
        )
        files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
        for f in files:
            if "Amiri" in f and "Bold" in f:
                print(f"✅ الخط المكتشف (Amiri Bold): {f}")
                return f
        if files:
            print(f"✅ الخط المكتشف: {files[0]}")
            return files[0]
    except Exception as e:
        print(f"⚠️  fc-list فشل: {e}")

    raise RuntimeError("لا يوجد خط عربي. ثبّت fonts-hosny-amiri في الـ workflow.")


DETECTED_FONT = _find_arabic_font()


def _shape(text):
    """
    يعالج النص العربي: يوصل الحروف (reshape) فقط.
    لا نستخدم bidi لأن RAQM في Pillow يتعامل مع الاتجاه.
    """
    text = str(text)
    return arabic_reshaper.reshape(text)


def _wrap(text, font, max_w, draw):
    """يكسر النص إلى أسطر (بعد الـ reshape)."""
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


def _generate_fallback_bg(out_path):
    """يولّد خلفية تدرج أخضر-ذهبي."""
    print("🎨 توليد خلفية احتياطية برمجياً...")
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), (13, 77, 60))
    draw = ImageDraw.Draw(img)

    for y in range(VIDEO_H):
        ratio = y / VIDEO_H
        r = int(10 + (26 - 10) * ratio)
        g = int(58 + (107 - 58) * ratio)
        b = int(44 + (84 - 44) * ratio)
        draw.line([(0, y), (VIDEO_W, y)], fill=(r, g, b))

    for _ in range(80):
        x = random.randint(0, VIDEO_W)
        y = random.randint(0, VIDEO_H)
        size = random.randint(1, 3)
        draw.ellipse(
            [x - size, y - size, x + size, y + size],
            fill=(212, 175, 55),
        )

    border = 20
    draw.rectangle(
        [border, border, VIDEO_W - border, VIDEO_H - border],
        outline=(212, 175, 55),
        width=3,
    )

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=92)
    print(f"🎨 خلفية مولّدة: {out_path}")
    return out_path


def _fetch_background(out_path="out/bg.jpg"):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    if PIXABAY_KEY:
        try:
            q = random.choice(PIXABAY_QUERY.split(",")).strip()
            r = requests.get(
                "https://pixabay.com/api/",
                params={
                    "key": PIXABAY_KEY, "q": q,
                    "image_type": "photo", "orientation": "vertical",
                    "per_page": 30, "safesearch": "true", "min_width": 1080,
                },
                timeout=20,
            )
            data = r.json()
            hits = data.get("hits") or []
            if hits:
                url = random.choice(hits)["largeImageURL"]
                Path(out_path).write_bytes(requests.get(url, timeout=30).content)
                print(f"🖼️  خلفية من Pixabay: {q}")
                return out_path
        except Exception as e:
            print(f"⚠️  Pixabay فشل: {e}")

    if Path(BG_FALLBACK).exists():
        Path(out_path).write_bytes(Path(BG_FALLBACK).read_bytes())
        print(f"🖼️  خلفية مرفوعة: {BG_FALLBACK}")
        return out_path

    return _generate_fallback_bg(out_path)


def _make_composite(bg_path, question, answer, attribution):
    bg = Image.open(bg_path).convert("RGB")

    ratio = max(VIDEO_W / bg.width, VIDEO_H / bg.height)
    bg = bg.resize(
        (int(bg.width * ratio), int(bg.height * ratio)),
        Image.LANCZOS,
    )
    left = (bg.width - VIDEO_W) // 2
    top = (bg.height - VIDEO_H) // 2
    bg = bg.crop((left, top, left + VIDEO_W, top + VIDEO_H))

    overlay = Image.new(
        "RGBA", bg.size,
        (0, 0, 0, int(255 * BG_OVERLAY_OPACITY)),
    )
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(bg)

    font_path = DETECTED_FONT
    # استخدام RAQM layout engine
    try:
        font_answer = ImageFont.truetype(font_path, FONT_SIZE, layout_engine=ImageFont.Layout.RAQM)
        font_question = ImageFont.truetype(font_path, int(FONT_SIZE * 0.85), layout_engine=ImageFont.Layout.RAQM)
        font_source = ImageFont.truetype(font_path, 40, layout_engine=ImageFont.Layout.RAQM)
        print("✅ استخدام RAQM layout engine")
    except Exception as e:
        print(f"⚠️  فشل RAQM، استخدام الافتراضي: {e}")
        font_answer = ImageFont.truetype(font_path, FONT_SIZE)
        font_question = ImageFont.truetype(font_path, int(FONT_SIZE * 0.85))
        font_source = ImageFont.truetype(font_path, 40)

    q_shaped = _shape(question) if question else ""
    a_shaped = _shape(answer)

    q_lines = _wrap(q_shaped, font_question, VIDEO_W - 120, draw) if q_shaped else []
    a_lines = _wrap(a_shaped, font_answer, VIDEO_W - 120, draw)

    line_h_q = int(FONT_SIZE * 0.85 * 1.5)
    line_h_a = int(FONT_SIZE * 1.6)
    total_h = len(q_lines) * line_h_q + (60 if q_lines else 0) + len(a_lines) * line_h_a

    y = max(150, (VIDEO_H - total_h) // 2)

    for line in q_lines:
        bbox = draw.textbbox((0, 0), line, font=font_question)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text(
            (x, y), line,
            font=font_question, fill=ACCENT_COLOR,
            stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE,
        )
        y += line_h_q
    if q_lines:
        y += 60

    for line in a_lines:
        bbox = draw.textbbox((0, 0), line, font=font_answer)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text(
            (x, y), line,
            font=font_answer, fill=TEXT_COLOR,
            stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE,
        )
        y += line_h_a

    if attribution:
        s_text = _shape(f"— {attribution}")
        bbox = draw.textbbox((0, 0), s_text, font=font_source)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text(
            (x, VIDEO_H - 180), s_text,
            font=font_source, fill="#dddddd",
            stroke_width=2, stroke_fill="black",
        )

    out = "out/composite.png"
    bg.save(out, "PNG")
    return out


def build(script, voice_path, out_path="out/video.mp4"):
    Path("out").mkdir(exist_ok=True)

    bg_path = _fetch_background()
    composite = _make_composite(
        bg_path,
        script.get("question", ""),
        script["answer"],
        script.get("attribution", ""),
    )

    audio = AudioFileClip(voice_path)
    duration = min(audio.duration, VIDEO_DURATION_MAX)

    base = ImageClip(composite).set_duration(duration)
    zoomed = base.resize(lambda t: 1 + (KEN_BURNS_ZOOM - 1) * (t / duration))

    final = CompositeVideoClip([zoomed], size=(VIDEO_W, VIDEO_H))
    final = final.set_audio(audio).set_duration(duration)

    final.write_videofile(
        out_path,
        fps=VIDEO_FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=2,
        logger=None,
    )
    print(f"🎬 الفيديو: {out_path} ({duration:.1f} ث)")
    return out_path
