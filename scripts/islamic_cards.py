"""
islamic_cards.py — مولد البطاقات والدفاتر الإسلامية المطرّزة ثلاثية الأبعاد (Pure Pillow + RAQM)
- يقدّم نمطين بطابع ديني وقور:
  1) الدفتر المخملي الإسلامي المطرّز (notebook): للأحاديث والنصوص والفقه والـ CTA.
  2) الشرائط المطرّزة المتراصة التفاعلية (stacked_pills): لمقطع التشجيع على الاشتراك والأذكار والأحاديث المقسمة.
- يبرز العبارة المنطوقة حالياً بتوهج ذهبي متزامن مع الصوت، ويعمل في القصص واليوميات بدون أي API خارجي.
"""

from __future__ import annotations

import math
import random
import re
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, features
import arabic_reshaper

try:
    from bidi.algorithm import get_display as bidi_display
except ImportError:
    from bidi import get_display as bidi_display

HAS_RAQM = features.check("raqm")

_EASTERN_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

HEADER_BY_TYPE = {
    "cta":     "الدَّالُّ عَلَى الخَيْرِ كَفَاعِلِهِ",
    "outro":   "شَارِكِ الخَيْرَ وَانْشُرِ العِلْمَ",
    "hadith":  "مِنْ هَدْيِ النُّبُوَّةِ الشَّرِيفَةِ",
    "fiqh":    "فَائِدَةٌ شَرْعِيَّةٌ مُيَسَّرَةٌ",
    "adhkar":  "ذِكْرٌ وَطُمَأْنِينَةٌ",
    "summary": "خُلَاصَةٌ وَعِبْرَةٌ",
}


def _prep_txt(text: str) -> str:
    if HAS_RAQM:
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


def _draw_rtl(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font,
    fill,
    align: str = "center",
):
    pt = _prep_txt(text)
    bbox = _measure(draw, text, font)
    tw = bbox[2] - bbox[0]
    if align == "center":
        dx = x - tw // 2 - bbox[0]
    elif align == "right":
        dx = x - bbox[2]
    else:
        dx = x - bbox[0]
    if HAS_RAQM:
        draw.text((dx, y), pt, font=font, fill=fill, direction="rtl")
    else:
        draw.text((dx, y), pt, font=font, fill=fill)


def _draw_embossed_rtl(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font,
    ink_fill=(34, 26, 18, 255),
    highlight_fill=(255, 252, 244, 190),
    align: str = "center",
):
    _draw_rtl(draw, x, y + 2, text, font, highlight_fill, align=align)
    _draw_rtl(draw, x, y, text, font, ink_fill, align=align)


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


def _split_clauses(text: str, max_clauses: int = 4) -> list[str]:
    """تقسيم النص إلى عبارات متزنة الإيقاع (1-4 بطاقات)."""
    raw = [c.strip() for c in re.split(r"(?<=[.،؛:؟!])\s+", text.strip()) if c.strip()]
    if not raw:
        return [text.strip()] if text.strip() else []

    # دمج العبارات القصيرة جداً مع جارتها
    merged: list[str] = []
    for seg in raw:
        if merged and len(merged[-1]) < 26:
            merged[-1] = f"{merged[-1]} {seg}".strip()
        elif merged and len(seg) < 18:
            merged[-1] = f"{merged[-1]} {seg}".strip()
        else:
            merged.append(seg)

    while len(merged) > max_clauses:
        # دمج أقصر جزأين متجاورين
        best_i = min(range(len(merged) - 1), key=lambda i: len(merged[i]) + len(merged[i + 1]))
        merged[best_i : best_i + 2] = [f"{merged[best_i]} {merged[best_i + 1]}"]

    # لو بقيت عبارة واحدة طويلة (>85 حرف) نقسمها بنعومة لجزأين متوازنين
    if len(merged) == 1 and len(merged[0]) > 85:
        words = merged[0].split()
        mid = len(words) // 2
        return [" ".join(words[:mid]), " ".join(words[mid:])]

    return merged


def _make_wood_table_bg(W: int, H: int, seed: int = 7) -> Image.Image:
    """خلفية خشب دافئة ناعمة بإيحاء واقعي لطاولة خشبية مموهة."""
    base = Image.new("RGB", (W, H), (88, 64, 44))
    grain = Image.effect_noise((W // 6, H), 18).resize((W, H), Image.Resampling.BILINEAR).convert("RGB")
    mixed = ImageChops.soft_light(base, grain)
    # إضاءة دافئة من الأعلى والوسط + تعتيم ناعم للأطراف
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(H):
        ratio = y / max(1, H)
        alpha = int(85 * abs(ratio - 0.45) * 1.4)
        od.line([(0, y), (W, y)], fill=(18, 12, 8, min(145, alpha)))
    mixed_rgba = mixed.convert("RGBA")
    mixed_rgba.alpha_composite(overlay)
    return mixed_rgba.filter(ImageFilter.GaussianBlur(radius=2.2)).convert("RGB")


def _make_plush_fabric_bg(W: int, H: int, palette: str = "sage_cream") -> Image.Image:
    """خلفية قماش مخملي ناعم بطابع إسلامي هادئ."""
    if palette == "sand_gold":
        c_rgb = (232, 219, 196)
    else:
        c_rgb = (224, 232, 224)  # عاجي مائل للزمردي الهادئ

    base = Image.new("RGB", (W, H), c_rgb)
    fibers = Image.effect_noise((W, H), 16).convert("RGB")
    soft = ImageChops.soft_light(base, fibers)

    vignette = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vignette)
    for step in range(14):
        pad = step * 18
        alpha = int(68 * (1.0 - step / 14.0))
        vd.rectangle([pad, pad, W - pad, H - pad], outline=(62, 52, 38, alpha // 3), width=18)

    out = soft.convert("RGBA")
    out.alpha_composite(vignette)
    return out.filter(ImageFilter.GaussianBlur(radius=1.0)).convert("RGB")


def _draw_stitched_rect(draw: ImageDraw.ImageDraw, box: list[int], radius: int, color, dash: int = 10, gap: int = 7):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=(*color[:3], 85), width=1)
    # خيوط متقطعة نظيفة على الحواف الأربعة
    for x in range(x0 + radius, x1 - radius, dash + gap):
        ex = min(x + dash, x1 - radius)
        draw.line([(x, y0), (ex, y0)], fill=color, width=2)
        draw.line([(x, y1), (ex, y1)], fill=color, width=2)
    for y in range(y0 + radius, y1 - radius, dash + gap):
        ey = min(y + dash, y1 - radius)
        draw.line([(x0, y), (x0, ey)], fill=color, width=2)
        draw.line([(x1, y), (x1, ey)], fill=color, width=2)


def _draw_botanical_accent(canvas: Image.Image, cx: int, cy: int, scale: float = 1.0, flip: bool = False):
    """رسم غصن زيتون وأوراق ناعمة وزهرة صغيرة كزخرفة طبيعية إسلامية."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    s = scale * (-1.0 if flip else 1.0)

    # ساق رفيع
    pts = [(cx, cy), (cx + int(18 * s), cy - int(16 * scale)), (cx + int(36 * s), cy - int(28 * scale))]
    d.line(pts, fill=(92, 122, 82, 225), width=max(2, int(3 * scale)))

    # أوراق خضراء
    leaf_offsets = [(14 * s, -12 * scale, -18), (26 * s, -22 * scale, 12), (38 * s, -30 * scale, -8)]
    for ox, oy, _ in leaf_offsets:
        lx, ly = cx + int(ox), cy + int(oy)
        rx, ry = max(5, int(9 * scale)), max(4, int(5 * scale))
        d.ellipse([lx - rx, ly - ry, lx + rx, ly + ry], fill=(108, 146, 94, 230), outline=(74, 106, 62, 220), width=1)

    # وردة/نجمة ذهبية صغيرة هادئة
    fx, fy = cx + int(4 * s), cy + int(4 * scale)
    r_pet = max(4, int(6 * scale))
    for ang in range(0, 360, 60):
        px = fx + int(math.cos(math.radians(ang)) * r_pet)
        py = fy + int(math.sin(math.radians(ang)) * r_pet)
        d.ellipse([px - 4, py - 4, px + 4, py + 4], fill=(214, 172, 84, 230))
    d.ellipse([fx - 4, fy - 4, fx + 4, fy + 4], fill=(248, 232, 175, 255))

    canvas.alpha_composite(layer)


def _draw_soft_shadow_pill(canvas: Image.Image, box: list[int], radius: int, shadow_alpha: int = 85, dy: int = 7):
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    x0, y0, x1, y1 = box
    sd.rounded_rectangle([x0 + 2, y0 + dy, x1 + 2, y1 + dy], radius=radius, fill=(28, 20, 12, shadow_alpha))
    sh = sh.filter(ImageFilter.GaussianBlur(radius=9))
    canvas.alpha_composite(sh)


def _draw_3d_plush_pill(
    canvas: Image.Image,
    box: list[int],
    radius: int,
    fill_rgb: tuple[int, int, int],
    active: bool = False,
):
    _draw_soft_shadow_pill(canvas, box, radius, shadow_alpha=105 if active else 75, dy=8 if active else 6)

    x0, y0, x1, y1 = box
    pw, ph = max(20, x1 - x0), max(20, y1 - y0)

    # توهج ذهبي دافئ خلف البطاقة النشطة حالياً
    if active:
        glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.rounded_rectangle([x0 - 6, y0 - 6, x1 + 6, y1 + 6], radius=radius + 6, fill=(218, 172, 58, 95))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=12))
        canvas.alpha_composite(glow)

    pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    tex = Image.effect_noise((pw, ph), 11).convert("RGB")
    base = ImageChops.soft_light(Image.new("RGB", (pw, ph), fill_rgb), tex).convert("RGBA")

    # قناع الحواف المستديرة
    mask = Image.new("L", (pw, ph), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, pw - 1, ph - 1], radius=radius, fill=255)
    pill.paste(base, (0, 0), mask)

    # لمعة 3D علوية وظل سفلي داخلي لإحساس الانتفاخ المخملي
    bevel = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bevel)
    bd.rounded_rectangle([2, 2, pw - 3, ph - 5], radius=max(4, radius - 2), outline=(255, 255, 250, 175), width=3)
    bd.rounded_rectangle([2, 6, pw - 3, ph - 2], radius=max(4, radius - 2), outline=(90, 74, 52, 48), width=3)
    bevel = bevel.filter(ImageFilter.GaussianBlur(radius=2))
    pill = Image.alpha_composite(pill, bevel)

    # خياطة محيطية
    pd = ImageDraw.Draw(pill)
    stitch_col = (196, 148, 44, 245) if active else (168, 142, 102, 165)
    _draw_stitched_rect(pd, [9, 9, pw - 10, ph - 10], max(4, radius - 8), stitch_col, dash=9, gap=6)

    if active:
        pd.rounded_rectangle([1, 1, pw - 2, ph - 2], radius=radius, outline=(212, 168, 55, 245), width=3)
    else:
        pd.rounded_rectangle([1, 1, pw - 2, ph - 2], radius=radius, outline=(185, 165, 130, 130), width=1)

    canvas.alpha_composite(pill, dest=(x0, y0))


def _render_notebook_slides(
    clauses: list[str],
    header_label: str,
    ep_title: str,
    font_path: str,
    base_png_path: Path,
    W: int,
    H: int,
    c_dur: float,
) -> list[tuple[Path, float]]:
    """نمط الدفتر المخملي الإسلامي المطرّز (مستوحى من الصورة 2 بأسلوب ديني وقور)."""
    full_text = " ".join(clauses)
    dummy = Image.new("RGB", (10, 10))
    dd = ImageDraw.Draw(dummy)

    page_x0, page_y0, page_x1, page_y1 = 46, 132, W - 46, H - 110
    text_max_w = (page_x1 - page_x0) - 86
    text_max_h = (page_y1 - page_y0) - 210

    # اختيار مقاس الخط الأنسب ليتسع للنص بأكبر وضوح على الدفتر
    chosen_font = None
    clause_lines_map: list[list[str]] = []
    lh = 64

    for sz in range(38, 25, -2):
        f = _load_font(font_path, sz)
        grouped = [_wrap_text(cl, f, text_max_w, dd) for cl in clauses]
        total_lines = sum(len(g) for g in grouped)
        cand_lh = max(56, int(sz * 1.72))
        if total_lines * cand_lh <= text_max_h:
            chosen_font = f
            clause_lines_map = grouped
            lh = cand_lh
            break

    if chosen_font is None:
        chosen_font = _load_font(26, font_path) if False else _load_font(font_path, 26)
        clause_lines_map = [_wrap_text(cl, chosen_font, text_max_w, dd) for cl in clauses]
        lh = 52

    # تحضير الخلفية الثابتة للدفتر
    bg = _make_wood_table_bg(W, H).convert("RGBA")

    # شريط العنوان العلوي الخفيف
    top_d = ImageDraw.Draw(bg)
    title_font = _load_font(font_path, 24)
    clean_title = re.sub(r"[a-zA-Z]", "", ep_title or "").strip()
    if clean_title:
        _draw_rtl(top_d, W // 2, 34, clean_title, title_font, (238, 212, 146, 255), align="center")

    # صفحة الدفتر المخملية 3D
    _draw_3d_plush_pill(bg, [page_x0, page_y0, page_x1, page_y1], radius=34, fill_rgb=(245, 239, 226), active=False)

    # خطوط الدفتر الأفقية الغائرة (الملمس المبطن المميّز للصورة 2)
    pd = ImageDraw.Draw(bg)
    first_line_y = page_y0 + 148
    for ry in range(first_line_y, page_y1 - 65, lh):
        pd.line([(page_x0 + 30, ry + 2), (page_x1 - 30, ry + 2)], fill=(255, 254, 250, 195), width=2)
        pd.line([(page_x0 + 30, ry), (page_x1 - 30, ry)], fill=(208, 196, 174, 155), width=2)

    # تجليد حلقي جانبي أنيق (يمين الصفحة للغة العربية)
    spine_x = page_x1 - 14
    for ry in range(page_y0 + 55, page_y1 - 45, 62):
        pd.ellipse([spine_x - 10, ry - 7, spine_x + 12, ry + 7], fill=(176, 142, 74, 245), outline=(110, 86, 40, 230), width=2)
        pd.ellipse([page_x1 - 28, ry - 4, page_x1 - 18, ry + 4], fill=(150, 134, 108, 210))

    # شارة الترويسة أعلى الصفحة
    hdr_box = [W // 2 - 210, page_y0 + 26, W // 2 + 210, page_y0 + 96]
    _draw_3d_plush_pill(bg, hdr_box, radius=28, fill_rgb=(234, 224, 200), active=True)
    hdr_font = _load_font(font_path, 28)
    _draw_embossed_rtl(
        ImageDraw.Draw(bg), W // 2, page_y0 + 42, header_label, hdr_font,
        ink_fill=(68, 48, 22, 255), align="center",
    )

    # زخرفة أغصان زيتون وورود هادئة
    _draw_botanical_accent(bg, page_x0 + 36, page_y1 - 38, scale=1.15, flip=False)
    _draw_botanical_accent(bg, page_x1 - 54, page_y0 + 56, scale=0.95, flip=True)

    # التوقيع الهادئ أسفل الصفحة
    wm_font = _load_font(font_path, 26)
    _draw_embossed_rtl(
        ImageDraw.Draw(bg), W // 2, page_y1 - 56, "مَلَاك المَعْرِفَة", wm_font,
        ink_fill=(136, 102, 42, 240), align="center",
    )

    # حساب إجمالي الأحرف لتوزيع الأزمنة بدقة
    total_chars = max(1, sum(len(cl) for cl in clauses))
    outputs: list[tuple[Path, float]] = []

    for active_idx in range(len(clauses)):
        frame = bg.copy()
        hl_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hld = ImageDraw.Draw(hl_layer)
        txt_d = ImageDraw.Draw(frame)

        cur_y = first_line_y - lh + 10
        for cl_i, lines in enumerate(clause_lines_map):
            is_act = (cl_i == active_idx) and (len(clauses) > 1)
            for ln in lines:
                if cur_y + lh > page_y1 - 68:
                    break
                if is_act:
                    hld.rounded_rectangle(
                        [page_x0 + 32, cur_y + 4, page_x1 - 34, cur_y + lh - 4],
                        radius=14,
                        fill=(218, 174, 62, 42),
                    )
                ink_col = (20, 14, 8, 255) if (cl_i == active_idx or len(clauses) == 1) else (92, 82, 68, 210)
                _draw_embossed_rtl(
                    txt_d, page_x1 - 44, cur_y + 4, ln, chosen_font,
                    ink_fill=ink_col, align="right",
                )
                cur_y += lh

        frame = Image.alpha_composite(frame, hl_layer).convert("RGB")
        out_png = base_png_path.with_name(f"{base_png_path.stem}_card_{active_idx}.png")
        frame.save(out_png)
        dur = max(0.35, c_dur * (len(clauses[active_idx]) / total_chars)) if len(clauses) > 1 else c_dur
        outputs.append((out_png, dur))

    if len(outputs) > 1:
        diff = c_dur - sum(d for _, d in outputs)
        lp, ld = outputs[-1]
        outputs[-1] = (lp, max(0.35, ld + diff))
    return outputs


def _render_stacked_pills_slides(
    clauses: list[str],
    header_label: str,
    ep_title: str,
    font_path: str,
    base_png_path: Path,
    W: int,
    H: int,
    c_dur: float,
) -> list[tuple[Path, float]]:
    """نمط الشرائط المطرّزة المتراصة التفاعلية (مستوحى من الصورة 1 بألوان عاجي/زمردي/ذهبي وقورة)."""
    bg = _make_plush_fabric_bg(W, H, palette="sage_cream").convert("RGBA")
    dummy = Image.new("RGB", (10, 10))
    dd = ImageDraw.Draw(dummy)

    bd = ImageDraw.Draw(bg)
    title_font = _load_font(font_path, 24)
    clean_title = re.sub(r"[a-zA-Z]", "", ep_title or "").strip()
    if clean_title:
        _draw_embossed_rtl(bd, W // 2, 34, clean_title, title_font, ink_fill=(98, 76, 36, 235), align="center")

    # ترويسة علوية مزخرفة
    hdr_box = [70, 96, W - 70, 192]
    _draw_3d_plush_pill(bg, hdr_box, radius=42, fill_rgb=(248, 242, 228), active=True)
    hdr_font = _load_font(font_path, 34)
    _draw_embossed_rtl(
        ImageDraw.Draw(bg), W // 2, 118, header_label, hdr_font,
        ink_fill=(56, 42, 18, 255), align="center",
    )
    _draw_botanical_accent(bg, 68, 178, scale=0.95, flip=False)
    _draw_botanical_accent(bg, W - 68, 178, scale=0.95, flip=True)

    # حساب مقاسات كل بطاقة متراصة
    n = len(clauses)
    top_area = 224
    bot_area = H - 104
    avail_h = bot_area - top_area
    gap_y = 24
    max_pill_h = max(135, (avail_h - gap_y * max(0, n - 1)) // max(1, n))

    pill_x0, pill_x1 = 44, W - 44
    txt_max_w = (pill_x1 - pill_x0) - 118  # ترك مساحة للشارة الرقمية يميناً

    wrapped_clauses: list[tuple[object, list[str], int]] = []
    for cl in clauses:
        chosen_f, chosen_lines, chosen_lh = _load_font(font_path, 26), _wrap_text(cl, _load_font(font_path, 26), txt_max_w, dd), 42
        for sz in range(36, 23, -2):
            f = _load_font(font_path, sz)
            lns = _wrap_text(cl, f, txt_max_w, dd)
            lh_c = max(44, int(sz * 1.55))
            if len(lns) * lh_c <= max_pill_h - 36 and len(lns) <= 3:
                chosen_f, chosen_lines, chosen_lh = f, lns, lh_c
                break
        wrapped_clauses.append((chosen_f, chosen_lines, chosen_lh))

    pill_heights = [max(118, min(max_pill_h, len(lns) * lh_c + 44)) for _, lns, lh_c in wrapped_clauses]
    total_stack_h = sum(pill_heights) + gap_y * max(0, n - 1)
    start_y = top_area + max(0, (avail_h - total_stack_h) // 2)

    total_chars = max(1, sum(len(cl) for cl in clauses))
    outputs: list[tuple[Path, float]] = []

    for active_idx in range(n):
        frame = bg.copy()
        cur_y = start_y

        for idx, (cl_font, lns, lh_c) in enumerate(wrapped_clauses):
            ph = pill_heights[idx]
            is_act = (idx == active_idx)
            # تناوب لوني هادئ: عاجي كريمي وزمردي فاتح جداً، والنشطة تضيء بالذهبي
            fill_rgb = (252, 248, 236) if is_act else ((238, 244, 236) if idx % 2 == 1 else (244, 238, 224))
            _draw_3d_plush_pill(frame, [pill_x0, cur_y, pill_x1, cur_y + ph], radius=34, fill_rgb=fill_rgb, active=is_act)

            fd = ImageDraw.Draw(frame)

            # ميدالية رقم إسلامية يمين كل شريط
            med_cx = pill_x1 - 50
            med_cy = cur_y + ph // 2
            med_r = 25
            med_fill = (206, 162, 54, 255) if is_act else (164, 146, 108, 215)
            fd.ellipse([med_cx - med_r, med_cy - med_r, med_cx + med_r, med_cy + med_r], fill=med_fill, outline=(255, 250, 235, 230), width=2)
            num_font = _load_font(font_path, 26)
            ar_num = str(idx + 1).translate(_EASTERN_DIGITS)
            _draw_rtl(fd, med_cx, med_cy - 18, ar_num, num_font, (255, 255, 255, 255), align="center")

            # نص الشريط
            txt_cx = (pill_x0 + (pill_x1 - 86)) // 2
            block_h = len(lns) * lh_c
            ty = cur_y + (ph - block_h) // 2
            ink = (22, 16, 10, 255) if is_act else (78, 72, 60, 225)
            for ln in lns:
                _draw_embossed_rtl(fd, txt_cx, ty, ln, cl_font, ink_fill=ink, align="center")
                ty += lh_c

            cur_y += ph + gap_y

        wm_font = _load_font(font_path, 26)
        _draw_embossed_rtl(
            ImageDraw.Draw(frame), W // 2, H - 64, "مَلَاك المَعْرِفَة", wm_font,
            ink_fill=(136, 102, 42, 240), align="center",
        )

        out_png = base_png_path.with_name(f"{base_png_path.stem}_pill_{active_idx}.png")
        frame.convert("RGB").save(out_png)
        dur = max(0.35, c_dur * (len(clauses[active_idx]) / total_chars)) if n > 1 else c_dur
        outputs.append((out_png, dur))

    if len(outputs) > 1:
        diff = c_dur - sum(d for _, d in outputs)
        lp, ld = outputs[-1]
        outputs[-1] = (lp, max(0.35, ld + diff))
    return outputs


def render_islamic_card_slides(
    chunk_text: str,
    seg_type: str,
    ep_title: str,
    font_path: str,
    base_png_path: Path,
    W: int,
    H: int,
    c_dur: float,
    card_style: str = "auto",
) -> list[tuple[Path, float]]:
    """
    يبني شرائح عالية الدقة للحديث / النص الشرعي / مقطع التشجيع على الاشتراك والمشاركة (CTA).
    """
    clean_body = re.sub(r"[*#@_\[\]<>]", "", chunk_text or "")
    clean_body = re.sub(r"[a-zA-Z]", "", clean_body)
    clean_body = clean_body.replace("ﷺ", "صلى الله عليه وسلم")
    clean_body = re.sub(r"\s+", " ", clean_body).strip()
    if not clean_body:
        return []

    header_label = HEADER_BY_TYPE.get(seg_type, "تَذْكِيرٌ وَفَائِدَةٌ")
    clauses = _split_clauses(clean_body, max_clauses=4)

    if card_style == "auto":
        # الدفتر المخملي للنصوص الشرعية والأحاديث، والشرائط المتراصة لمقاطع الـ CTA والأذكار (أو بتناوب ذكي)
        if seg_type in ("cta", "outro", "adhkar"):
            card_style = "stacked_pills"
        else:
            card_style = "notebook"

    if card_style == "stacked_pills" and 2 <= len(clauses) <= 4 and len(clean_body) <= 280:
        return _render_stacked_pills_slides(
            clauses, header_label, ep_title, font_path, base_png_path, W, H, c_dur
        )
    return _render_notebook_slides(
        clauses, header_label, ep_title, font_path, base_png_path, W, H, c_dur
    )
