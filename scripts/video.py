"""تركيب الفيديو: خلفية + نص عربي + صوت."""
import random
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display
from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip
from config import (
    VIDEO_W, VIDEO_H, VIDEO_FPS, VIDEO_DURATION_MAX,
    FONT_PATH, FONT_SIZE, TEXT_COLOR, TEXT_STROKE, TEXT_STROKE_W, ACCENT_COLOR,
    PIXABAY_KEY, PIXABAY_QUERY, BG_FALLBACK,
    BG_OVERLAY_OPACITY, KEN_BURNS_ZOOM,
)


def _shape(text):
    """يعالج النص العربي للعرض."""
    return get_display(arabic_reshaper.reshape(str(text)))


def _wrap(text, font, max_w, draw):
    """يكسر النص إلى أسطر حسب العرض."""
    words = text.split()
    lines, current = [], ""
    for w in words:
        trial = (current + " " + w).strip()
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


def _fetch_background(out_path="out/bg.jpg"):
    """يجرب Pixabay، ثم الخلفية الاحتياطية."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    if PIXABAY_KEY:
        try:
            q = random.choice(PIXABAY_QUERY.split(",")).strip()
            r = requests.get(
                "https://pixabay.com/api/",
                params={
                    "key": PIXABAY_KEY,
                    "q": q,
                    "image_type": "photo",
                    "orientation": "vertical",
                    "per_page": 30,
                    "safesearch": "true",
                    "min_width": 1080,
                },
                timeout=20,
            )
            data = r.json()
            hits = data.get("hits") or []
            if hits:
                url = random.choice(hits)["largeImageURL"]
                img = requests.get(url, timeout=30).content
                Path(out_path).write_bytes(img)
                print(f"🖼️  خلفية من Pixabay: {q}")
                return out_path
        except Exception as e:
            print(f"⚠️  Pixabay فشل: {e}")

    if not Path(BG_FALLBACK).exists():
        raise RuntimeError(
            f"لا توجد خلفية. أضف صورة إلى: {BG_FALLBACK}\n"
            "أو ضع PIXABAY_API_KEY في GitHub Secrets."
        )
    Path(out_path).write_bytes(Path(BG_FALLBACK).read_bytes())
    print(f"🖼️  خلفية احتياطية: {BG_FALLBACK}")
    return out_path


def _make_composite(bg_path, question, answer, attribution):
    """يركّب النص على الخلفية بـ PIL. يرجع مسار PNG."""
    bg = Image.open(bg_path).convert("RGB")

    # قص وتوسيط إلى 1080x1920
    ratio = max(VIDEO_W / bg.width, VIDEO_H / bg.height)
    bg = bg.resize(
        (int(bg.width * ratio), int(bg.height * ratio)),
        Image.LANCZOS,
    )
    left = (bg.width - VIDEO_W) // 2
    top = (bg.height - VIDEO_H) // 2
    bg = bg.crop((left, top, left + VIDEO_W, top + VIDEO_H))

    # طبقة تعتيم
    overlay = Image.new(
        "RGBA", bg.size,
        (0, 0, 0, int(255 * BG_OVERLAY_OPACITY)),
    )
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(bg)

    # تحقق من الخط
    if not Path(FONT_PATH).exists():
        raise RuntimeError(
            f"الخط غير موجود: {FONT_PATH}\n"
            "ارفع ملف Cairo-Bold.ttf إلى assets/fonts/"
        )

    font_answer = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    font_question = ImageFont.truetype(FONT_PATH, int(FONT_SIZE * 0.85))
    font_source = ImageFont.truetype(FONT_PATH, 40)

    # كسر الأسطر
    q_lines = _wrap(_shape(question), font_question, VIDEO_W - 120, draw) if question else []
    a_lines = _wrap(_shape(answer), font_answer, VIDEO_W - 120, draw)

    line_h_q = int(FONT_SIZE * 0.85 * 1.5)
    line_h_a = int(FONT_SIZE * 1.6)
    total_h = len(q_lines) * line_h_q + (60 if q_lines else 0) + len(a_lines) * line_h_a

    y = max(150, (VIDEO_H - total_h) // 2)

    # السؤال (ذهبي)
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

    # الجواب (أبيض)
    for line in a_lines:
        bbox = draw.textbbox((0, 0), line, font=font_answer)
        x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
        draw.text(
            (x, y), line,
            font=font_answer, fill=TEXT_COLOR,
            stroke_width=TEXT_STROKE_W, stroke_fill=TEXT_STROKE,
        )
        y += line_h_a

    # المصدر أسفل
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
    """
    script: dict فيه question, answer
    voice_path: مسار mp3
    """
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
