#!/usr/bin/env python3
"""
scripts/romantic_cards.py
=========================
مولد الدفتر الصوفي القطني 3D والوسائد المخيطة لمشروع «همسات ملك» (1080×1920):
- يعمل تلقائياً بالكامل بدون الحاجة لأي ملفات أو قوالب مرفوعة مسبقاً.
- نمط (modest_love): دفتر صوفي قطني بحواف وبرية + أخاديد مضغوطة على السطور + قلب لباد 3D
  + توليب + يدين متشابكتين مخيطتين + تاريخ اليوم وتوقيع همسات ملك + حبر مغروس داخل الصوف.
- نمط (prayer_niqab): وسائد قطنية مخيطة 3D على خلفية فرو وردي ناعم مع ترقيم عربي وإطار ذهبي نشط.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import random
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, features

try:
    import arabic_reshaper
    from bidi.algorithm import get_display as bidi_display
    HAS_RESHAPER = True
except Exception:
    arabic_reshaper = None
    bidi_display = None
    HAS_RESHAPER = False

_REPO_ROOT = Path(__file__).resolve().parent.parent
HAS_RAQM = features.check("raqm")
_TPL_LOCK = threading.Lock()

_AR_DAYS = {
    0: "يوم اثنين",
    1: "يوم ثلاثاء",
    2: "يوم أربعاء",
    3: "يوم خميس",
    4: "يوم جمعة",
    5: "يوم سبت",
    6: "يوم أحد",
}
_AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def _find_amiri_font() -> str:
    candidates = [
        "/usr/share/fonts/amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/amiri/Amiri-Regular.ttf",
        "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/hosny-amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    for base in (Path("/usr/share/fonts/amiri"), Path("/usr/share/fonts")):
        if base.exists():
            for p in base.rglob("*.ttf"):
                if "amiri" in p.name.lower() and "quran" not in p.name.lower():
                    return str(p)
    raise RuntimeError("❌ لم يُعثر على خط Amiri في النظام.")


def _prep_txt(text: str) -> str:
    if HAS_RAQM or not HAS_RESHAPER:
        return text
    return bidi_display(arabic_reshaper.reshape(text))


def _load_font(font_path: str, size: int):
    if HAS_RAQM:
        return ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.RAQM)
    return ImageFont.truetype(font_path, size)


def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int, int, int]:
    pt = _prep_txt(text)
    if HAS_RAQM:
        return draw.textbbox((0, 0), pt, font=font, direction="rtl")
    return draw.textbbox((0, 0), pt, font=font)


def _wrap_text(text: str, font, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    lines: list[str] = []
    for para in text.splitlines():
        words = para.split()
        if not words:
            continue
        cur = ""
        for w in words:
            trial = f"{cur} {w}".strip() if cur else w
            bb = _measure(draw, trial, font)
            if (bb[2] - bb[0]) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    return lines


def _check_valid_hf_token() -> str | None:
    tok = (os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACE_HUB_TOKEN", "")).strip()
    if not tok:
        return None
    try:
        r = requests.get("https://huggingface.co/api/whoami-v2", headers={"Authorization": f"Bearer {tok}"}, timeout=10)
        if r.status_code == 200:
            return tok
    except Exception:
        return tok
    os.environ.pop("HF_TOKEN", None)
    os.environ.pop("HUGGINGFACE_HUB_TOKEN", None)
    return None


def _get_active_horde_models() -> list[str]:
    preferred = ["Juggernaut XL", "RealVisXL", "DreamShaper XL", "AlbedoBase XL (SDXL)", "ICBINP XL"]
    chosen: list[str] = []
    try:
        r = requests.get("https://aihorde.net/api/v2/status/models?type=image", timeout=15)
        if r.status_code == 200:
            active = {m.get("name"): int(m.get("count", 0)) for m in r.json() if isinstance(m, dict)}
            for p in preferred:
                for name, cnt in active.items():
                    if cnt >= 1 and p.lower() in str(name).lower() and name not in chosen:
                        chosen.append(name)
            if len(chosen) < 2:
                for name, cnt in sorted(active.items(), key=lambda kv: -kv[1]):
                    nl = str(name).lower()
                    if cnt >= 2 and "xl" in nl and not any(b in nl for b in ("anime", "pony", "toon", "hentai")):
                        if name not in chosen:
                            chosen.append(name)
                        if len(chosen) >= 4:
                            break
    except Exception:
        pass
    return chosen or ["AlbedoBase XL (SDXL)", "Juggernaut XL"]


# ==============================================================================
# 1. أدوات الخامات الصوفية والدمج ثلاثي الأبعاد (تعمل محلياً بالكامل)
# ==============================================================================

def _draw_wool_embedded_text(
    canvas: Image.Image,
    items: list[tuple[int, int, str, object, tuple[int, int, int], str]],
) -> Image.Image:
    """
    يرسم الخط العربي داخل ألياف الصوف بدون تغميق الخلفية:
    items: [(x, y, text, font, rgb_color, align), ...]
    """
    w, h = canvas.size
    out = canvas.convert("RGBA")

    # استخراج ملمس الصوف المضيء فقط لتسريب الألياف فوق الحبر بنعومة
    gray = canvas.convert("L")
    wool_hi = ImageEnhance.Contrast(gray).enhance(1.75)
    fiber_bleed = wool_hi.point(lambda p: max(0, min(52, int((p - 172) * 0.60))))

    for x, y, text, font, rgb, align in items:
        if not text:
            continue
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)
        pt = _prep_txt(text)
        bb = _measure(md, text, font)
        tw = bb[2] - bb[0]
        if align == "center":
            dx = x - tw // 2 - bb[0]
        elif align == "right":
            dx = x - bb[2]
        else:
            dx = x - bb[0]
        dy = y - (bb[1] + bb[3]) // 2

        if HAS_RAQM:
            md.text((dx, dy), pt, font=font, fill=255, direction="rtl")
        else:
            md.text((dx, dy), pt, font=font, fill=255)

        soft_mask = mask.filter(ImageFilter.GaussianBlur(0.42))
        effective_alpha = ImageChops.subtract(soft_mask, fiber_bleed)

        # إضاءة خفيفة جداً أسفل الحرف توحي بالانغماس في القطن
        rim = Image.new("RGBA", (w, h), (255, 252, 246, 0))
        rim.putalpha(mask.filter(ImageFilter.GaussianBlur(1.1)).point(lambda p: int(p * 0.30)))

        # ظل ناعم غائر
        halo = Image.new("RGBA", (w, h), (68, 48, 36, 0))
        halo.putalpha(mask.filter(ImageFilter.GaussianBlur(1.8)).point(lambda p: int(p * 0.16)))

        ink_layer = Image.new("RGBA", (w, h), (*rgb, 255))
        ink_layer.putalpha(effective_alpha)

        out.alpha_composite(rim, dest=(0, 2))
        out.alpha_composite(halo, dest=(0, 1))
        out.alpha_composite(ink_layer)

    return out


def _apply_plush_noise(img: Image.Image, fine_amt: int = 12, mid_amt: int = 16, coarse_amt: int = 14) -> Image.Image:
    w, h = img.size
    n_coarse = Image.effect_noise((max(16, w // 6), max(16, h // 6)), coarse_amt).resize((w, h), Image.Resampling.BICUBIC).convert("RGB")
    n_mid = Image.effect_noise((max(32, w // 2), max(32, h // 2)), mid_amt).resize((w, h), Image.Resampling.BILINEAR).convert("RGB")
    n_fine = Image.effect_noise((w, h), fine_amt).convert("RGB")

    res = ImageChops.soft_light(img.convert("RGB"), n_coarse)
    res = ImageChops.soft_light(res, n_mid)
    res = ImageChops.soft_light(res, n_fine)
    return res


def _draw_fuzzy_wool_page_base(
    W: int,
    H: int,
    groove_ys: list[int],
    groove_h: int,
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """
    يبني خلفية الطاولة الخشبية + صفحة الصوف القطني السميكة بحواف وبرية + أخاديد السطور + الحلقات اللولبية.
    """
    # 1) طاولة خشب دافئة ضبابية مع إضاءة ناعمة من الزاوية العلوية اليسرى
    desk = Image.new("RGB", (W, H), (112, 82, 58))
    dd = ImageDraw.Draw(desk)
    for y in range(H):
        t = y / H
        c = (
            int(128 - 44 * t),
            int(96 - 34 * t),
            int(68 - 24 * t),
        )
        dd.line([(0, y), (W, y)], fill=c)
    wood_streaks = Image.effect_noise((W // 14, H // 2), 26).resize((W, H), Image.Resampling.BILINEAR).convert("RGB")
    desk = ImageChops.soft_light(desk, wood_streaks).filter(ImageFilter.GaussianBlur(4.5)).convert("RGBA")

    # 2) أبعاد الدفتر (كامل العرض تقريباً مثل الصورة 2 مع هامش خشب علوي ويميني جميل)
    px0, py0, px1, py1 = 44, 152, W - 44, 1690
    pw, ph = px1 - px0, py1 - py0

    # ظل الدفتر العميق على الطاولة
    sh_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh_layer).rounded_rectangle([px0 + 8, py0 + 18, px1 + 10, py1 + 22], radius=56, fill=(24, 12, 6, 150))
    desk.alpha_composite(sh_layer.filter(ImageFilter.GaussianBlur(22)))

    # 3) بناء صفحة الصوف الكريمية مع أخاديد أفقية مضغوطة متطابقة مع السطور
    page_rgb = Image.new("RGB", (pw, ph), (247, 239, 228))
    pd = ImageDraw.Draw(page_rgb)

    # إضاءة ناعمة ثلاثية الأبعاد على الصفحة
    for y in range(ph):
        edge_d = min(y, ph - 1 - y)
        edge_f = min(1.0, edge_d / 38.0)
        base_v = 236 + int(16 * (edge_f ** 0.6))
        pd.line([(0, y), (pw - 1, y)], fill=(base_v, base_v - 7, base_v - 18))

    # تكوين شبكة الأخاديد الكاملة بحيث تمر بالضبط عبر groove_ys وتمتد لبقية الصفحة
    all_grooves = sorted(set(groove_ys))
    if all_grooves:
        step = max(66, groove_h)
        first_g = all_grooves[0]
        g = first_g - step
        while g > 55:
            all_grooves.append(g)
            g -= step
        last_g = max(all_grooves)
        g = last_g + step
        while g < ph + py0 - 45:
            all_grooves.append(g)
            g += step
        all_grooves = sorted(set(all_grooves))

    # تطبيق الأخاديد الغائرة وانتفاخ القطن بينها
    groove_sh = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    groove_hi = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    gsd = ImageDraw.Draw(groove_sh)
    ghd = ImageDraw.Draw(groove_hi)
    for gy_abs in all_grooves:
        gy = gy_abs - py0
        if 36 < gy < ph - 30:
            gsd.line([(28, gy), (pw - 28, gy)], fill=(142, 114, 92, 92), width=6)
            ghd.line([(28, gy + 7), (pw - 28, gy + 7)], fill=(255, 253, 249, 130), width=8)
            ghd.line([(28, gy - 7), (pw - 28, gy - 7)], fill=(255, 252, 246, 85), width=6)

    page_rgba = _apply_plush_noise(page_rgb, fine_amt=14, mid_amt=18, coarse_amt=15).convert("RGBA")
    page_rgba.alpha_composite(groove_sh.filter(ImageFilter.GaussianBlur(3.2)))
    page_rgba.alpha_composite(groove_hi.filter(ImageFilter.GaussianBlur(4.2)))

    # 4) قناع الحواف الوبرية (Fuzzy Fleece Edges)
    # السر: رسم مستطيل مستدير ثم نثر مئات الشعيرات القطنية القصيرة على كامل محيطه
    rng = random.Random(2026)
    fleece_mask = Image.new("L", (pw, ph), 0)
    fmd = ImageDraw.Draw(fleece_mask)
    fmd.rounded_rectangle([14, 14, pw - 15, ph - 15], radius=52, fill=255)

    # شعيرات صوفية متطايرة حول المحيط
    for x in range(60, pw - 60, 4):
        for y_edge, sign in ((14, -1), (ph - 15, 1)):
            flen = rng.randint(4, 13)
            dx = rng.randint(-4, 4)
            fmd.line([(x, y_edge), (x + dx, y_edge + sign * flen)], fill=rng.randint(155, 245), width=rng.randint(2, 4))
    for y in range(60, ph - 60, 4):
        for x_edge, sign in ((14, -1), (pw - 15, 1)):
            flen = rng.randint(4, 13)
            dy = rng.randint(-4, 4)
            fmd.line([(x_edge, y), (x_edge + sign * flen, y + dy)], fill=rng.randint(155, 245), width=rng.randint(2, 4))

    fleece_mask = fleece_mask.filter(ImageFilter.GaussianBlur(0.8))

    # نفخة 3D داخلية على حواف الدفتر
    bevel = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bevel)
    bd.rounded_rectangle([16, 16, pw - 17, ph - 19], radius=50, outline=(255, 254, 250, 210), width=10)
    bd.rounded_rectangle([18, 22, pw - 19, ph - 15], radius=50, outline=(164, 136, 112, 75), width=10)
    page_rgba.alpha_composite(bevel.filter(ImageFilter.GaussianBlur(6.0)))

    final_page = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    final_page.paste(page_rgba, (0, 0), fleece_mask)
    desk.alpha_composite(final_page, dest=(px0, py0))

    # 5) حلقات تجليد معدنية لولبية 3D على اليسار (مع فتحات غائرة في الصوف)
    spiral = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(spiral)
    for ry in range(py0 + 92, py1 - 80, 68):
        # ثقب داكن في الصوف
        sd.ellipse([px0 + 18, ry - 9, px0 + 36, ry + 9], fill=(86, 68, 56, 185))
        # ظل الحلقة
        sd.rounded_rectangle([px0 - 24, ry - 5, px0 + 28, ry + 11], radius=7, fill=(32, 22, 16, 95))
        # جسم الحلقة المعدنية
        sd.rounded_rectangle([px0 - 26, ry - 8, px0 + 28, ry + 6], radius=7, fill=(196, 192, 188, 255), outline=(128, 122, 118, 220), width=2)
        # لمعة معدنية عليا
        sd.line([(px0 - 20, ry - 4), (px0 + 22, ry - 4)], fill=(252, 250, 248, 220), width=3)
    desk.alpha_composite(spiral)

    return desk.convert("RGB"), (px0, py0, px1, py1)


def _draw_3d_felt_heart(canvas: Image.Image, cx: int, cy: int, scale: float = 3.2) -> Image.Image:
    """يرسم قلباً أحمر من اللباد المنفوخ 3D مع ظل على الصوف وإضاءة مخملية."""
    w, h = canvas.size
    pts = []
    for deg in range(0, 360, 3):
        t = math.radians(deg)
        hx = 16 * (math.sin(t) ** 3)
        hy = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + hx * scale, cy + hy * scale))

    # ظل على الصوف
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([(x + 3, y + 8) for x, y in pts], fill=(55, 20, 18, 130))
    out = canvas.convert("RGBA")
    out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(7.0)))

    # جسم القلب اللباد
    hmask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(hmask).polygon(pts, fill=255)
    # تنعيم وزغب لباد بسيط
    hmask = hmask.filter(ImageFilter.GaussianBlur(0.8))

    heart_body = Image.new("RGBA", (w, h), (192, 22, 32, 255))
    hd = ImageDraw.Draw(heart_body)
    # إضاءة مخملية عليا + تظليل حواف سفلي
    hd.ellipse([cx - int(15 * scale), cy - int(16 * scale), cx + int(8 * scale), cy + int(4 * scale)], fill=(232, 68, 76, 175))
    hd.ellipse([cx - int(8 * scale), cy - int(11 * scale), cx + int(2 * scale), cy - int(1 * scale)], fill=(248, 128, 132, 110))
    heart_body = heart_body.filter(ImageFilter.GaussianBlur(10.0))

    # نسيج اللباد الأحمر
    felt_n = Image.effect_noise((w, h), 20).convert("RGB")
    heart_rgb = ImageChops.soft_light(heart_body.convert("RGB"), felt_n).convert("RGBA")

    # حافة ظل داخلية تعمّق الانتفاخ
    edge_sh = hmask.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(4.5))
    edge_ov = Image.new("RGBA", (w, h), (88, 6, 12, 0))
    edge_ov.putalpha(edge_sh.point(lambda p: int(p * 0.65)))
    heart_rgb.alpha_composite(edge_ov)
    heart_rgb.putalpha(hmask)

    out.alpha_composite(heart_rgb)
    return out


def _draw_tulip_sticker(canvas: Image.Image, x: int, y: int, scale: float = 1.0) -> Image.Image:
    """يرسم وردة توليب ناعمة مائلة على الهامش الأيسر للدفتر (مطابقة للصورة 2)."""
    sz = int(260 * scale)
    layer = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # ساق وأوراق خضراء متدرجة
    d.line([(int(76 * scale), int(84 * scale)), (int(198 * scale), int(218 * scale))], fill=(72, 128, 36, 255), width=max(4, int(9 * scale)))
    d.polygon([
        (int(128 * scale), int(146 * scale)),
        (int(88 * scale), int(168 * scale)),
        (int(164 * scale), int(218 * scale)),
        (int(156 * scale), int(168 * scale)),
    ], fill=(84, 146, 42, 245))
    d.polygon([
        (int(138 * scale), int(154 * scale)),
        (int(168 * scale), int(124 * scale)),
        (int(210 * scale), int(196 * scale)),
        (int(156 * scale), int(186 * scale)),
    ], fill=(102, 166, 52, 235))

    # بتلات التوليب الحمراء الوردية المضيئة
    d.ellipse([int(30 * scale), int(18 * scale), int(112 * scale), int(106 * scale)], fill=(224, 24, 68, 255))
    d.ellipse([int(48 * scale), int(26 * scale), int(104 * scale), int(98 * scale)], fill=(246, 72, 112, 235))
    d.ellipse([int(42 * scale), int(16 * scale), int(84 * scale), int(78 * scale)], fill=(252, 134, 158, 170))

    layer = layer.filter(ImageFilter.GaussianBlur(0.6))
    sh = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    sh.putalpha(layer.getchannel("A").point(lambda p: int(p * 0.36)))
    sh = sh.filter(ImageFilter.GaussianBlur(7.0))

    out = canvas.convert("RGBA")
    out.alpha_composite(sh, dest=(x + 4, y + 10))
    out.alpha_composite(layer, dest=(x, y))
    return out


def _draw_stitched_felt_hands_and_daisy(canvas: Image.Image, bx: int, by: int, scale: float = 1.0) -> Image.Image:
    """يرسم يدين متشابكتين من اللباد الأبيض بحواف تطريز سوداء مع زهرة أقحوان صغيرة (مطابق للصورة 2)."""
    sw, sh_h = int(420 * scale), int(410 * scale)
    layer = Image.new("RGBA", (sw, sh_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # زهرة أقحوان صغيرة بين الذراعين
    fx, fy = int(206 * scale), int(84 * scale)
    d.line([(fx, fy + int(18 * scale)), (fx, int(218 * scale))], fill=(72, 112, 48, 255), width=max(4, int(7 * scale)))
    d.ellipse([fx - int(34 * scale), fy + int(48 * scale), fx - int(2 * scale), fy + int(76 * scale)], fill=(86, 134, 54, 255))
    d.ellipse([fx + int(2 * scale), fy + int(60 * scale), fx + int(36 * scale), fy + int(88 * scale)], fill=(86, 134, 54, 255))
    for ang in range(0, 360, 36):
        rad = math.radians(ang)
        px = fx + math.cos(rad) * int(22 * scale)
        py = fy + math.sin(rad) * int(22 * scale)
        d.ellipse([px - int(11 * scale), py - int(11 * scale), px + int(11 * scale), py + int(11 * scale)], fill=(252, 248, 242, 255), outline=(185, 176, 165, 200), width=1)
    d.ellipse([fx - int(12 * scale), fy - int(12 * scale), fx + int(12 * scale), fy + int(12 * scale)], fill=(236, 182, 34, 255))

    # الذراعان اللباديتان المتقابلتان
    arm_col = (244, 236, 224, 255)
    stitch_col = (34, 26, 22, 240)
    # ذراع يسار
    left_arm = [(int(42 * scale), int(128 * scale)), (int(114 * scale), int(92 * scale)), (int(228 * scale), int(250 * scale)), (int(152 * scale), int(288 * scale))]
    d.polygon(left_arm, fill=arm_col, outline=stitch_col, width=max(3, int(4 * scale)))
    # ذراع يمين
    right_arm = [(int(304 * scale), int(84 * scale)), (int(376 * scale), int(122 * scale)), (int(258 * scale), int(298 * scale)), (int(184 * scale), int(258 * scale))]
    d.polygon(right_arm, fill=arm_col, outline=stitch_col, width=max(3, int(4 * scale)))

    # كفّان متشابكان وأصابع مطرزة بالأسود
    d.ellipse([int(128 * scale), int(220 * scale), int(286 * scale), int(356 * scale)], fill=arm_col, outline=stitch_col, width=max(3, int(4 * scale)))
    for fi, (fx0, fy0, fx1, fy1) in enumerate([
        (134, 294, 166, 362),
        (162, 304, 194, 376),
        (190, 306, 222, 374),
        (218, 296, 250, 358),
    ]):
        d.rounded_rectangle([int(fx0 * scale), int(fy0 * scale), int(fx1 * scale), int(fy1 * scale)], radius=int(14 * scale), fill=arm_col, outline=stitch_col, width=max(2, int(3 * scale)))

    # إضافة ملمس صوفي ناعم فوق الذراعين
    wool = Image.effect_noise((sw, sh_h), 14).convert("RGB")
    alpha = layer.getchannel("A")
    textured = ImageChops.soft_light(layer.convert("RGB"), wool).convert("RGBA")
    textured.putalpha(alpha)

    sh = Image.new("RGBA", (sw, sh_h), (0, 0, 0, 0))
    sh.putalpha(alpha.point(lambda p: int(p * 0.34)))
    sh = sh.filter(ImageFilter.GaussianBlur(8.0))

    out = canvas.convert("RGBA")
    out.alpha_composite(sh, dest=(bx + 4, by + 10))
    out.alpha_composite(textured, dest=(bx, by))
    return out


def _try_get_couple_sticker() -> Image.Image | None:
    """
    يحاول مرة واحدة توليد ملصق كوبل رومانسي محتشم بخلفية بيضاء صافية وقصه بنظافة،
    ويُحفظ مؤقتاً لإعادة الاستخدام السريع دون تعطيل الكود عند انقطاع الشبكة.
    """
    cache_path = Path(tempfile.gettempdir()) / "whispers_plush_cache" / "couple_sticker_v2.png"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and cache_path.stat().st_size > 12000:
        try:
            with Image.open(cache_path) as im:
                return im.convert("RGBA")
        except Exception:
            pass

    if os.environ.get("WHISPERS_SKIP_AI_STICKER", "").strip() == "1":
        return None

    try:
        from gradio_client import Client as GradioClient
        tok = _check_valid_hf_token()
        prompt = (
            "romantic modest loving couple sitting gently side by side holding hands, smiling warmly at each other, "
            "man in soft grey blazer and jeans, woman in modest pastel floral maxi dress with flower crown, "
            "soft painterly illustration isolated on pure solid #FFFFFF white background, full body sticker, no text"
        )
        kwargs = {"token": tok} if tok else {}
        client = GradioClient("black-forest-labs/FLUX.1-schnell", **kwargs)
        with ThreadPoolExecutor(max_workers=1) as ex:
            res = ex.submit(
                lambda: client.predict(
                    prompt=prompt, seed=777, randomize_seed=False,
                    width=512, height=640, num_inference_steps=4, api_name="/infer"
                )
            ).result(timeout=35)
        fpath = res[0] if isinstance(res, (list, tuple)) else res
        if isinstance(fpath, dict):
            fpath = fpath.get("path") or fpath.get("url")
        if fpath and os.path.exists(str(fpath)):
            with Image.open(str(fpath)) as raw:
                rgba = raw.convert("RGBA")
                px = rgba.load()
                rw, rh = rgba.size
                # تفريغ الخلفية البيضاء بنعومة
                for yy in range(rh):
                    for xx in range(rw):
                        r, g, b, a = px[xx, yy]
                        lum = (r + g + b) / 3.0
                        sat = max(r, g, b) - min(r, g, b)
                        if lum > 236 and sat < 18:
                            px[xx, yy] = (r, g, b, 0)
                        elif lum > 218 and sat < 22:
                            px[xx, yy] = (r, g, b, int(255 * (236 - lum) / 18.0))
                rgba.save(cache_path)
                return rgba
    except Exception:
        pass
    return None


# ==============================================================================
# 2. نمط الوسائد القطنية المخيطة 3D (مطابق للصورة 1 للأدعية والروحانيات)
# ==============================================================================

def _draw_plush_pillow_box(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    fill_rgb: tuple[int, int, int],
    stitch_rgb: tuple[int, int, int],
    radius: int = 44,
    active_gold: bool = False,
) -> Image.Image:
    x0, y0, x1, y1 = box
    pw, ph = x1 - x0, y1 - y0
    pad = 28
    lw, lh = pw + pad * 2, ph + pad * 2

    sh_m = Image.new("L", (lw, lh), 0)
    ImageDraw.Draw(sh_m).rounded_rectangle([pad, pad + 8, pad + pw, pad + ph + 8], radius=radius, fill=138)
    shadow = Image.new("RGBA", (lw, lh), (72, 34, 46, 0))
    shadow.putalpha(sh_m.filter(ImageFilter.GaussianBlur(14)))

    pmask = Image.new("L", (lw, lh), 0)
    ImageDraw.Draw(pmask).rounded_rectangle([pad, pad, pad + pw, pad + ph], radius=radius, fill=255)

    body = Image.new("RGBA", (lw, lh), (*fill_rgb, 255))
    bevel = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bevel)
    for dy in range(ph):
        t = dy / max(1, ph)
        if t < 0.28:
            a = int(72 * (1.0 - t / 0.28))
            bd.line([(pad, pad + dy), (pad + pw, pad + dy)], fill=(255, 252, 248, a))
        elif t > 0.70:
            a = int(58 * ((t - 0.70) / 0.30))
            bd.line([(pad, pad + dy), (pad + pw, pad + dy)], fill=(116, 62, 74, a))
    body.alpha_composite(bevel)

    # ملمس قطني خفيف فوق الوسادة
    grain = Image.effect_noise((lw, lh), 11).convert("RGB")
    body_rgb = ImageChops.soft_light(body.convert("RGB"), grain).convert("RGBA")

    # تظليل استدارة داخلي
    in_edge = pmask.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(7.0))
    edge_ov = Image.new("RGBA", (lw, lh), (120, 74, 84, 0))
    edge_ov.putalpha(in_edge.point(lambda p: int(p * 0.36)))
    body_rgb.alpha_composite(edge_ov)
    body_rgb.putalpha(pmask)

    # درزات خياطة متقطعة على المحيط الداخلي
    sd = ImageDraw.Draw(body_rgb)
    inset = 14
    sx0, sy0, sx1, sy1 = pad + inset, pad + inset, pad + pw - inset, pad + ph - inset
    for sx in range(sx0 + radius // 2, sx1 - radius // 2, 18):
        sd.line([(sx, sy0), (sx + 9, sy0)], fill=(*stitch_rgb, 185), width=3)
        sd.line([(sx, sy1), (sx + 9, sy1)], fill=(*stitch_rgb, 185), width=3)
    for sy in range(sy0 + radius // 2, sy1 - radius // 2, 18):
        sd.line([(sx0, sy), (sx0, sy + 9)], fill=(*stitch_rgb, 185), width=3)
        sd.line([(sx1, sy), (sx1, sy + 9)], fill=(*stitch_rgb, 185), width=3)

    out = canvas.convert("RGBA")
    if active_gold:
        glow = Image.new("RGBA", (lw, lh), (255, 214, 134, 0))
        glow.putalpha(pmask.filter(ImageFilter.GaussianBlur(15)).point(lambda p: int(p * 0.62)))
        out.alpha_composite(glow, dest=(x0 - pad, y0 - pad))
        sd.rounded_rectangle([pad + 2, pad + 2, pad + pw - 2, pad + ph - 2], radius=radius, outline=(212, 162, 84, 240), width=5)

    out.alpha_composite(shadow, dest=(x0 - pad, y0 - pad))
    out.alpha_composite(body_rgb, dest=(x0 - pad, y0 - pad))
    return out


def _render_stitched_pillows_style(
    seg_texts: list[str],
    valid_segs: list[dict],
    title: str,
    font_path: str,
    base_png_path: Path,
    W: int,
    H: int,
    total_dur: float,
) -> list[tuple[Path, float]]:
    """يبني لوحات الوسائد القطنية المخيطة (مطابقة للصورة 1) مع تمييز الوسادة النشطة بإطار ذهبي."""
    # خلفية فرو وردي ناعم
    fur = Image.new("RGB", (W, H), (236, 202, 210))
    fd = ImageDraw.Draw(fur)
    for y in range(H):
        t = y / H
        c = (
            int(242 - 18 * t),
            int(208 - 22 * t),
            int(215 - 16 * t),
        )
        fd.line([(0, y), (W, y)], fill=c)
    fur = _apply_plush_noise(fur, fine_amt=18, mid_amt=26, coarse_amt=22).convert("RGBA")

    # كرات كرز صوفية زخرفية وأوراق صغيرة في الزوايا
    rng = random.Random(101)
    decor = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(decor)
    for px, py, col in [
        (135, 420, (244, 212, 218)), (178, 455, (248, 222, 226)),
        (910, 410, (198, 52, 68)), (935, 860, (242, 198, 206)),
        (130, 1140, (194, 48, 64)), (895, 1460, (196, 52, 68)),
    ]:
        dd.line([(px, py), (px + rng.randint(-35, 35), py - 45)], fill=(158, 134, 122, 170), width=3)
        dd.ellipse([px - 24, py - 24, px + 24, py + 24], fill=(*col, 240))
    fur.alpha_composite(decor.filter(ImageFilter.GaussianBlur(1.2)))

    # لافتة العنوان العلوية
    fur = _draw_plush_pillow_box(fur, (150, 130, W - 150, 315), (250, 242, 234), (218, 178, 184), radius=64, active_gold=False)

    clean_title = re.sub(r"[a-zA-Z]", "", title or "همسة روحانية").strip()
    clean_title = re.sub(r"م[\u064B-\u065F]*ل[\u064B-\u065F]*ك[\u064B-\u065F]*", "ملك", clean_title)
    t_font = _load_font(font_path, 52)
    fur = _draw_wool_embedded_text(fur, [(W // 2, 222, clean_title, t_font, (46, 30, 24), "center")])

    # توزيع الوسائد على المنطقة الآمنة (350..1580)
    shown_segs = seg_texts[:6]
    n_boxes = max(1, len(shown_segs))
    zone_top, zone_bot = 355, 1585
    gap = 24
    box_h = min(215, (zone_bot - zone_top - gap * (n_boxes - 1)) // n_boxes)
    total_stack_h = n_boxes * box_h + (n_boxes - 1) * gap
    start_y = zone_top + (zone_bot - zone_top - total_stack_h) // 2

    dummy_d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    use_ts = all("start_sec" in s and "end_sec" in s for s in valid_segs)
    outputs: list[tuple[Path, float]] = []

    for act_i in range(len(seg_texts)):
        cur_active = min(act_i, n_boxes - 1)
        frame = fur.copy()
        text_items: list[tuple[int, int, str, object, tuple[int, int, int], str]] = []

        for bi, txt in enumerate(shown_segs):
            by0 = start_y + bi * (box_h + gap)
            by1 = by0 + box_h
            is_cream = (bi % 2 == 0)
            fill_c = (250, 243, 235) if is_cream else (228, 176, 186)
            stitch_c = (208, 176, 168) if is_cream else (188, 126, 138)
            frame = _draw_plush_pillow_box(
                frame,
                (76, by0, W - 76, by1),
                fill_c,
                stitch_c,
                radius=40,
                active_gold=(bi == cur_active),
            )

            # شارة رقم عربية صغيرة على اليسار داخل الوسادة (مثل الصورة 1)
            badge_x, badge_y = 148, (by0 + by1) // 2
            bd = ImageDraw.Draw(frame)
            bd.ellipse([badge_x - 32, badge_y - 32, badge_x + 32, badge_y + 32], fill=(246, 234, 224, 240), outline=(146, 106, 92, 230), width=3)
            num_str = str(bi + 1).translate(_AR_DIGITS)
            n_font = _load_font(font_path, 34)
            text_items.append((badge_x, badge_y, num_str, n_font, (76, 44, 34), "center"))

            # التفاف النص داخل الوسادة
            max_tw = W - 295
            chosen_f = _load_font(font_path, 32)
            lines = [txt]
            for sz in range(46, 28, -2):
                f = _load_font(font_path, sz)
                lns = _wrap_text(txt, f, max_tw, dummy_d)
                lh = int(sz * 1.48)
                if len(lns) <= 3 and len(lns) * lh <= box_h - 24:
                    chosen_f, lines = f, lns
                    break
            lh = int(chosen_f.size * 1.48)
            cy_text = (by0 + by1) // 2 - ((len(lines) - 1) * lh) // 2
            for ln in lines:
                text_items.append((W // 2 + 34, cy_text, ln, chosen_f, (38, 22, 18), "center"))
                cy_text += lh

        # توقيع همسات ملك أسفل الوسائد
        sig_f = _load_font(font_path, 34)
        text_items.append((W // 2, 1650, "همسات ملك", sig_f, (118, 48, 62), "center"))

        final_img = _draw_wool_embedded_text(frame, text_items).convert("RGB")
        out_png = base_png_path.with_name(f"{base_png_path.stem}_pillow_{act_i}.png")
        final_img.save(out_png)

        if use_ts and act_i < len(valid_segs):
            s_obj = valid_segs[act_i]
            nxt = float(valid_segs[act_i + 1]["start_sec"]) if act_i + 1 < len(valid_segs) else total_dur
            dur = max(0.5, (total_dur - float(s_obj["start_sec"])) if act_i == len(valid_segs) - 1 else (nxt - float(s_obj["start_sec"])))
        else:
            dur = total_dur / len(seg_texts)
        outputs.append((out_png, dur))

    return outputs


# ==============================================================================
# 3. الدالة الرئيسية المستدعاة من whispers_video.py
# ==============================================================================

def render_romantic_notebook_slides(
    segments: list[dict],
    title: str,
    scene_category: str,
    base_png_path: Path,
    W: int = 1080,
    H: int = 1920,
    total_dur: float = 20.0,
) -> list[tuple[Path, float]]:
    font_path = _find_amiri_font()
    is_dua = (scene_category == "prayer_niqab")

    valid_segs = [s for s in segments if (s.get("display_text") or s.get("text") or "").strip()]
    seg_texts = [re.sub(r"\s+", " ", (s.get("display_text") or s.get("text") or "").strip()) for s in valid_segs]
    if not seg_texts:
        return []

    # في الوضع الروحاني / الأدعية نفعّل نمط الوسائد القطنية المخيطة (مطابق للصورة 1)
    if is_dua:
        outputs = _render_stitched_pillows_style(
            seg_texts, valid_segs, title, font_path, base_png_path, W, H, total_dur
        )
        if outputs:
            cur_sum = sum(d for _, d in outputs)
            if abs(cur_sum - total_dur) > 0.05:
                lp, ld = outputs[-1]
                outputs[-1] = (lp, max(0.5, ld + (total_dur - cur_sum)))
        return outputs

    # في الوضع الرومانسي نفعّل نمط الدفتر الصوفي القطني 3D (مطابق للصورة 2)
    safe_x0, safe_x1 = 135, W - 110
    text_max_w = safe_x1 - safe_x0
    zone_top, zone_bot = 360, 1185
    text_max_h = zone_bot - zone_top

    dummy_d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    chosen_font = _load_font(font_path, 46)
    grouped_lines: list[list[str]] = []
    lh = 84

    for sz in range(60, 37, -2):
        f = _load_font(font_path, sz)
        grouped = [_wrap_text(txt, f, text_max_w, dummy_d) for txt in seg_texts]
        total_lines = sum(len(g) for g in grouped)
        cand_lh = max(78, int(sz * 1.56))
        if total_lines * cand_lh <= text_max_h:
            chosen_font, grouped_lines, lh = f, grouped, cand_lh
            break
    else:
        grouped_lines = [_wrap_text(txt, chosen_font, text_max_w, dummy_d) for txt in seg_texts]
        lh = max(72, int(chosen_font.size * 1.48))

    total_lines = max(1, sum(len(g) for g in grouped_lines))
    total_text_h = total_lines * lh
    text_start_y = zone_top + max(0, (text_max_h - total_text_h) // 2)

    # حساب إحداثيات الأخاديد الأفقية بحيث يجلس كل سطر داخل أخدود الصوف المضغوط
    line_ys: list[int] = []
    groove_ys: list[int] = []
    cy = text_start_y
    for lines in grouped_lines:
        for _ in lines:
            line_ys.append(cy + int(lh * 0.44))
            groove_ys.append(cy + int(lh * 0.88))
            cy += lh

    # بناء الدفتر الصوفي القطني بالخطوط المضغوطة والقلب والتوليب والأيدي والملصق
    bg, _ = _draw_fuzzy_wool_page_base(W, H, groove_ys, groove_h=lh)
    bg_rgba = bg.convert("RGBA")

    # قلب لباد أحمر 3D في أعلى المنتصف (مثل "صدفة ❤" في الصورة 2)
    bg_rgba = _draw_3d_felt_heart(bg_rgba, cx=W // 2 - 125, cy=252, scale=2.75)

    # وردة توليب على الهامش الأيسر
    bg_rgba = _draw_tulip_sticker(bg_rgba, x=88, y=940, scale=0.92)

    # أيدي متشابكة من اللباد الأبيض وزهرة الأقحوان أسفل اليسار
    bg_rgba = _draw_stitched_felt_hands_and_daisy(bg_rgba, bx=108, by=1215, scale=0.90)

    # ملصق كوبل رومانسي محتشم أسفل اليمين (اختياري بالذكاء الاصطناعي وإلا يكتفى بالقلوب والتوليب والتاريخ)
    couple = _try_get_couple_sticker()
    if couple is not None:
        cw, ch = 365, int(365 * couple.height / max(1, couple.width))
        couple_r = couple.resize((cw, ch), Image.Resampling.LANCZOS)
        sh_c = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        sh_c.putalpha(couple_r.getchannel("A").point(lambda p: int(p * 0.32)))
        sh_c = sh_c.filter(ImageFilter.GaussianBlur(8.0))
        cx_pos, cy_pos = W - cw - 230, 1215
        bg_rgba.alpha_composite(sh_c, dest=(cx_pos + 6, cy_pos + 12))
        bg_rgba.alpha_composite(couple_r, dest=(cx_pos, cy_pos))

    # العناصر الثابتة المكتوبة داخل الصوف (العنوان + التاريخ اليومي + التوقيع)
    now = datetime.now()
    date_str = now.strftime("%Y/%m/%d")
    day_str = _AR_DAYS.get(now.weekday(), "يوم سعيد")

    clean_title = re.sub(r"[a-zA-Z]", "", title or "همسة لروحها").strip()
    clean_title = re.sub(r"م[\u064B-\u065F]*ل[\u064B-\u065F]*ك[\u064B-\u065F]*", "ملك", clean_title)

    title_font = _load_font(font_path, 50)
    date_font = _load_font(font_path, 42)
    sig_font = _load_font(font_path, 38)

    static_items = [
        (W // 2 + 55, 250, clean_title, title_font, (46, 22, 24), "center"),
        (W - 140, 1345, date_str, date_font, (42, 26, 24), "right"),
        (W - 145, 1415, day_str, date_font, (56, 34, 30), "right"),
        (W - 140, 1555, "همسات ملك", sig_font, (132, 34, 48), "right"),
    ]
    bg_rgba = _draw_wool_embedded_text(bg_rgba, static_items)

    # إنتاج الشرائح التدريجية (أو الكاملة للذروة)
    outputs: list[tuple[Path, float]] = []
    use_ts = all("start_sec" in s and "end_sec" in s for s in valid_segs)

    for act_idx in range(len(grouped_lines)):
        text_items: list[tuple[int, int, str, object, tuple[int, int, int], str]] = []
        line_ptr = 0
        for seg_i, lines in enumerate(grouped_lines):
            is_past_or_cur = (seg_i <= act_idx) or (act_idx == len(grouped_lines) - 1)
            # في الشرائح المتسلسلة، السطور المقروءة غامقة ومغروسة واللاحقة باهتة بنعومة
            ink = (26, 18, 16) if (seg_i == act_idx or act_idx == len(grouped_lines) - 1) else (98, 82, 74)
            if not is_past_or_cur:
                ink = (132, 118, 108)
            for ln in lines:
                if line_ptr < len(line_ys):
                    text_items.append((W // 2 + 12, line_ys[line_ptr], ln, chosen_font, ink, "center"))
                    line_ptr += 1

        final_img = _draw_wool_embedded_text(bg_rgba, text_items).convert("RGB")
        out_png = base_png_path.with_name(f"{base_png_path.stem}_rom_{act_idx}.png")
        final_img.save(out_png)

        if use_ts:
            s_obj = valid_segs[act_idx]
            nxt = float(valid_segs[act_idx + 1]["start_sec"]) if act_idx + 1 < len(valid_segs) else total_dur
            dur = max(0.5, (total_dur - float(s_obj["start_sec"])) if act_idx == len(valid_segs) - 1 else (nxt - float(s_obj["start_sec"])))
        else:
            dur = total_dur / len(grouped_lines)
        outputs.append((out_png, dur))

    if outputs:
        cur_sum = sum(d for _, d in outputs)
        if abs(cur_sum - total_dur) > 0.05:
            lp, ld = outputs[-1]
            outputs[-1] = (lp, max(0.5, ld + (total_dur - cur_sum)))

    return outputs
