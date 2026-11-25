"""
islamic_cards.py — مولد البطاقات والدفاتر الإسلامية 3D بملمس صوفي حقيقي + خط عربي متقن
- يولّد بالذكاء الاصطناعي (FLUX / Juggernaut XL عبر AI Horde أو ZeroGPU) خلفيات قطنية/صوفية 3D
  فارغة تماماً بدون أي نص مشوه (أو يحمّلها من assets/templates/)، ثم يطبع فوقها النص العربي
  المضبوط بالرسم والدمج الواقعي داخل ألياف القماش.
- يعالج التداخلات والهوامش وتمركز النص والأرقام بدقة تامة، ويعمل في القصص واليوميات معاً.
"""

from __future__ import annotations

import os
import re
import time
import tempfile
import threading
from pathlib import Path
import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, features
import arabic_reshaper

try:
    from bidi.algorithm import get_display as bidi_display
except ImportError:
    from bidi import get_display as bidi_display

_REPO_ROOT = Path(__file__).resolve().parent.parent
HAS_RAQM = features.check("raqm")
_EASTERN_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_TPL_LOCK = threading.Lock()

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
    v_center: bool = False,
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

    dy = y - (bbox[1] + bbox[3]) // 2 if v_center else y
    if HAS_RAQM:
        draw.text((dx, dy), pt, font=font, fill=fill, direction="rtl")
    else:
        draw.text((dx, dy), pt, font=font, fill=fill)


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


def _split_clauses(text: str, max_clauses: int = 3) -> list[str]:
    """تقسيم النص إلى عبارات متوازنة بدون تكرار توقيع القناة في نهاية البطاقات."""
    cleaned = re.sub(r"(?:مَلَاك|ملاك)\s+(?:المَعْرِفَة|المعرفة)[.!؟،\s]*$", "", text.strip()).strip()
    if not cleaned:
        cleaned = text.strip()

    raw = [c.strip() for c in re.split(r"(?<=[.،؛:؟!])\s+", cleaned) if c.strip()]
    if not raw:
        return [cleaned] if cleaned else []

    merged: list[str] = []
    for seg in raw:
        if merged and len(merged[-1]) < 28:
            merged[-1] = f"{merged[-1]} {seg}".strip()
        elif merged and len(seg) < 20:
            merged[-1] = f"{merged[-1]} {seg}".strip()
        else:
            merged.append(seg)

    while len(merged) > max_clauses:
        best_i = min(range(len(merged) - 1), key=lambda i: len(merged[i]) + len(merged[i + 1]))
        merged[best_i : best_i + 2] = [f"{merged[best_i]} {merged[best_i + 1]}"]

    if len(merged) == 1 and len(merged[0]) > 88:
        words = merged[0].split()
        mid = len(words) // 2
        return [" ".join(words[:mid]), " ".join(words[mid:])]

    return merged


# ==============================================================================
# توليد وكاش القوالب المخملية/الصوفية 3D الفارغة بالذكاء الاصطناعي (مرة واحدة لكل خادم)
# ==============================================================================
def _ai_generate_blank_plush(prompt: str, out_path: Path, W: int, H: int) -> bool:
    neg = (
        "text, words, letters, writing, arabic, calligraphy, numbers, watermark, logo, "
        "signature, human, face, hands, couple, pink, red heart, cartoon, vector, 2d flat"
    )
    # 1) محاولة ZeroGPU السريعة إن توفر gradio_client
    try:
        from gradio_client import Client as GradioClient
        tok = (os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACE_HUB_TOKEN", "")).strip() or None
        kwargs = {"token": tok} if tok else {}
        client = GradioClient("black-forest-labs/FLUX.1-schnell", **kwargs)
        res = client.predict(
            prompt=prompt,
            seed=42,
            randomize_seed=False,
            width=W,
            height=H,
            num_inference_steps=4,
            api_name="/infer",
        )
        fpath = res[0] if isinstance(res, (list, tuple)) else res
        if isinstance(fpath, dict):
            fpath = fpath.get("path") or fpath.get("url")
        if fpath and os.path.exists(str(fpath)):
            with Image.open(str(fpath)) as im:
                im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                im.save(out_path, "JPEG", quality=93)
            return out_path.exists() and out_path.stat().st_size > 20000
    except Exception:
        pass

    # 2) محاولة AI Horde المجاني بنماذج الواقعية الملموسة
    try:
        headers = {
            "apikey": os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000",
            "Client-Agent": "MalakAlMarifa:3.0:plush-template",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": f"{prompt} ### {neg}",
            "params": {
                "sampler_name": "k_euler_a",
                "width": 576,
                "height": 1024,
                "steps": 22,
                "cfg_scale": 6.5,
                "seed": "77421",
                "n": 1,
            },
            "nsfw": False,
            "censor_nsfw": True,
            "r2": True,
            "models": ["Juggernaut XL", "RealVisXL", "AlbedoBase XL (SDXL)"],
        }
        r = requests.post("https://aihorde.net/api/v2/generate/async", headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 202):
            job_id = r.json().get("id")
            if job_id:
                deadline = time.time() + 75
                while time.time() < deadline:
                    time.sleep(4)
                    chk = requests.get(f"https://aihorde.net/api/v2/generate/check/{job_id}", headers=headers, timeout=12)
                    if chk.status_code == 200 and chk.json().get("done"):
                        st = requests.get(f"https://aihorde.net/api/v2/generate/status/{job_id}", headers=headers, timeout=15)
                        gens = st.json().get("generations") or [] if st.status_code == 200 else []
                        if gens and gens[0].get("img"):
                            ir = requests.get(gens[0]["img"], timeout=25)
                            if ir.status_code == 200 and len(ir.content) > 8000:
                                tmp = out_path.with_suffix(".webp")
                                tmp.write_bytes(ir.content)
                                with Image.open(tmp) as im:
                                    im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                                    im.save(out_path, "JPEG", quality=93)
                                tmp.unlink(missing_ok=True)
                                return out_path.exists() and out_path.stat().st_size > 20000
                        break
    except Exception:
        pass
    return False


def _get_plush_template(kind: str, W: int, H: int) -> Image.Image | None:
    """يجلب القالب المخملي الفارغ من assets/templates أو الكاش أو يولّده بالـ AI مرة واحدة."""
    repo_tpl = _REPO_ROOT / "assets" / "templates" / f"{kind}.jpg"
    if repo_tpl.exists() and repo_tpl.stat().st_size > 15000:
        with Image.open(repo_tpl) as im:
            return ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)

    cache_dir = Path(tempfile.gettempdir()) / "islamic_plush_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{kind}_{W}x{H}.jpg"

    with _TPL_LOCK:
        if cached.exists() and cached.stat().st_size > 15000:
            with Image.open(cached) as im:
                return im.convert("RGB")

        prompts = {
            "notebook_plush_blank": (
                "vertical 9:16 overhead macro photography of a thick cream-white fluffy quilted wool-felt "
                "stitched notebook page resting centered on a warm dark walnut wooden desk, soft cozy "
                "3D plush wool fibers, subtle stitched perimeter thread, tiny delicate realistic cream white "
                "jasmine flowers and dried golden leaves resting only on the far outer corners of the desk, "
                "soft warm morning sunlight across a completely clean blank empty cream page center, "
                "ZERO text, ZERO letters, ZERO lines, ZERO numbers, 8k photorealistic macro"
            ),
            "fabric_plush_blank": (
                "vertical 9:16 overhead macro photography of soft fluffy warm cream-ivory and subtle pale "
                "sage quilted plush felt wool texture, soft 3D velvety wool fibers, delicate warm golden "
                "studio glow in center, subtle vignette edges, completely blank empty texture, "
                "ZERO text, ZERO letters, ZERO objects, 8k photorealistic macro"
            ),
        }
        prompt = prompts.get(kind, prompts["fabric_plush_blank"])
        if os.getenv("AI_PLUSH_TEMPLATES", "1") == "1" and _ai_generate_blank_plush(prompt, cached, W, H):
            with Image.open(cached) as im:
                return im.convert("RGB")
    return None


def _make_wood_table_bg(W: int, H: int) -> Image.Image:
    base = Image.new("RGB", (W, H), (82, 58, 40))
    grain = Image.effect_noise((W // 8, H), 20).resize((W, H), Image.Resampling.BILINEAR).convert("RGB")
    mixed = ImageChops.soft_light(base, grain)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(H):
        ratio = y / max(1, H)
        alpha = int(90 * abs(ratio - 0.45) * 1.4)
        od.line([(0, y), (W, y)], fill=(16, 10, 6, min(155, alpha)))
    mixed_rgba = mixed.convert("RGBA")
    mixed_rgba.alpha_composite(overlay)
    return mixed_rgba.filter(ImageFilter.GaussianBlur(radius=2.6)).convert("RGB")


def _make_plush_fabric_bg(W: int, H: int) -> Image.Image:
    tpl = _get_plush_template("fabric_plush_blank", W, H)
    if tpl is not None:
        return tpl
    base = Image.new("RGB", (W, H), (229, 232, 222))
    fibers = Image.effect_noise((W, H), 15).convert("RGB")
    soft = ImageChops.soft_light(base, fibers)
    return soft.filter(ImageFilter.GaussianBlur(radius=0.8)).convert("RGB")


def _draw_stitched_rect(draw: ImageDraw.ImageDraw, box: list[int], radius: int, color, dash: int = 10, gap: int = 7):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=(*color[:3], 70), width=1)
    for x in range(x0 + radius, x1 - radius, dash + gap):
        ex = min(x + dash, x1 - radius)
        draw.line([(x, y0), (ex, y0)], fill=color, width=2)
        draw.line([(x, y1), (ex, y1)], fill=color, width=2)
    for y in range(y0 + radius, y1 - radius, dash + gap):
        ey = min(y + dash, y1 - radius)
        draw.line([(x0, y), (x0, ey)], fill=color, width=2)
        draw.line([(x1, y), (x1, ey)], fill=color, width=2)


def _draw_3d_plush_pill(
    canvas: Image.Image,
    box: list[int],
    radius: int,
    fill_rgb: tuple[int, int, int],
    fabric_src: Image.Image | None = None,
    active: bool = False,
):
    x0, y0, x1, y1 = box
    pw, ph = max(20, x1 - x0), max(20, y1 - y0)

    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle([x0 + 2, y0 + (9 if active else 7), x1 + 2, y1 + (9 if active else 7)], radius=radius, fill=(26, 18, 10, 110 if active else 80))
    sh = sh.filter(ImageFilter.GaussianBlur(radius=10))
    canvas.alpha_composite(sh)

    if active:
        glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.rounded_rectangle([x0 - 7, y0 - 7, x1 + 7, y1 + 7], radius=radius + 7, fill=(218, 172, 58, 105))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=13))
        canvas.alpha_composite(glow)

    pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    if fabric_src is not None:
        crop = fabric_src.crop((max(0, x0), max(0, y0), min(fabric_src.width, x0 + pw), min(fabric_src.height, y0 + ph))).resize((pw, ph))
        tint = Image.new("RGB", (pw, ph), fill_rgb)
        base_rgb = Image.blend(crop, tint, 0.58)
    else:
        tex = Image.effect_noise((pw, ph), 12).convert("RGB")
        base_rgb = ImageChops.soft_light(Image.new("RGB", (pw, ph), fill_rgb), tex)

    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1], radius=radius, fill=255)
    pill.paste(base_rgb.convert("RGBA"), (0, 0), mask)

    bevel = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bevel)
    bd.rounded_rectangle([3, 3, pw - 4, ph - 6], radius=max(4, radius - 3), outline=(255, 255, 250, 190), width=4)
    bd.rounded_rectangle([3, 7, pw - 4, ph - 3], radius=max(4, radius - 3), outline=(85, 68, 48, 55), width=4)
    bevel = bevel.filter(ImageFilter.GaussianBlur(radius=2.5))
    pill = Image.alpha_composite(pill, bevel)

    pd = ImageDraw.Draw(pill)
    stitch_col = (196, 148, 44, 245) if active else (168, 142, 102, 165)
    _draw_stitched_rect(pd, [10, 10, pw - 11, ph - 11], max(4, radius - 9), stitch_col, dash=9, gap=6)

    if active:
        pd.rounded_rectangle([1, 1, pw - 2, ph - 2], radius=radius, outline=(212, 168, 55, 250), width=3)
    else:
        pd.rounded_rectangle([1, 1, pw - 2, ph - 2], radius=radius, outline=(185, 165, 130, 140), width=1)

    canvas.alpha_composite(pill, dest=(x0, y0))


def _blend_text_into_plush(base_rgb: Image.Image, text_overlay_rgba: Image.Image) -> Image.Image:
    """يدمج الحبر بنعومة في ألياف القماش (Multiply جزئي) ليبدو مطبوعاً/مطرزاً على المخمل."""
    composed = Image.alpha_composite(base_rgb.convert("RGBA"), text_overlay_rgba).convert("RGB")
    # تمرير نسيج الخلفية بنسبة لطيفة فوق الحروف لمنحها ملمس الورق القطني الواقعي
    textured = ImageChops.multiply(composed, Image.blend(Image.new("RGB", base_rgb.size, (255, 255, 255)), base_rgb, 0.14))
    return textured


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
    """نمط الدفتر المخملي الإسلامي بملمس صوفي حقيقي + محاذاة وتوسيط كامل خالٍ من التداخل."""
    dummy = Image.new("RGB", (10, 10))
    dd = ImageDraw.Draw(dummy)

    page_x0, page_y0, page_x1, page_y1 = 44, 126, W - 44, H - 98
    safe_x0, safe_x1 = page_x0 + 44, page_x1 - 44
    text_max_w = safe_x1 - safe_x0

    hdr_y0, hdr_y1 = page_y0 + 26, page_y0 + 94
    zone_top = hdr_y1 + 32   # 32px مسافة آمنة تماماً أسفل الشارة
    zone_bot = page_y1 - 86  # 86px مسافة آمنة تماماً فوق التوقيع السفلي
    text_max_h = zone_bot - zone_top

    chosen_font = _load_font(font_path, 28)
    clause_lines_map: list[list[str]] = []
    lh = 56

    for sz in range(42, 27, -2):
        f = _load_font(font_path, sz)
        grouped = [_wrap_text(cl, f, text_max_w, dd) for cl in clauses]
        total_lines = sum(len(g) for g in grouped)
        cand_lh = max(62, int(sz * 1.75))
        if total_lines * cand_lh <= text_max_h:
            chosen_font = f
            clause_lines_map = grouped
            lh = cand_lh
            break
    else:
        clause_lines_map = [_wrap_text(cl, chosen_font, text_max_w, dd) for cl in clauses]

    total_lines = max(1, sum(len(g) for g in clause_lines_map))
    total_text_h = total_lines * lh
    # توسيط عمودي متوازن حتى لا يبقى النصف السفلي فارغاً!
    text_start_y = zone_top + max(0, (text_max_h - total_text_h) // 2)

    ai_tpl = _get_plush_template("notebook_plush_blank", W, H)
    if ai_tpl is not None:
        bg = ai_tpl.convert("RGBA")
        # تصفية ناعمة متدرجة داخل منطقة الكتابة مع الحفاظ على الملمس الصوفي وإطار الخشب والزهور حوله
        clean_patch = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cd = ImageDraw.Draw(clean_patch)
        cd.rounded_rectangle([page_x0, page_y0, page_x1, page_y1], radius=34, fill=(246, 240, 228, 175))
        clean_patch = clean_patch.filter(ImageFilter.GaussianBlur(radius=6))
        bg = Image.alpha_composite(bg, clean_patch)
        pd = ImageDraw.Draw(bg)
        _draw_stitched_rect(pd, [page_x0 + 10, page_y0 + 10, page_x1 - 10, page_y1 - 10], 26, (186, 152, 88, 195), dash=10, gap=6)
    else:
        bg = _make_wood_table_bg(W, H).convert("RGBA")
        _draw_3d_plush_pill(bg, [page_x0, page_y0, page_x1, page_y1], radius=34, fill_rgb=(245, 239, 226), active=False)

    # العنوان العلوي والتوقيع السفلي
    top_d = ImageDraw.Draw(bg)
    title_font = _load_font(font_path, 24)
    clean_title = re.sub(r"[a-zA-Z]", "", ep_title or "").strip()
    if clean_title:
        _draw_rtl(top_d, W // 2, 42, clean_title, title_font, (240, 214, 148, 255), align="center", v_center=True)

    # خطوط السطور الغائرة متطابقة بدقة مع كل سطر نصي
    pd = ImageDraw.Draw(bg)
    first_rule = text_start_y + int(lh * 0.85)
    for ry in range(first_rule, min(zone_bot + 16, first_rule + total_lines * lh), lh):
        pd.line([(safe_x0 - 8, ry + 2), (safe_x1 + 8, ry + 2)], fill=(255, 254, 250, 200), width=2)
        pd.line([(safe_x0 - 8, ry), (safe_x1 + 8, ry)], fill=(198, 184, 162, 150), width=2)

    # شارة الترويسة أعلى الصفحة
    hdr_box = [W // 2 - 208, hdr_y0, W // 2 + 208, hdr_y1]
    _draw_3d_plush_pill(bg, hdr_box, radius=28, fill_rgb=(236, 226, 204), active=True)
    hdr_font = _load_font(font_path, 28)
    _draw_rtl(ImageDraw.Draw(bg), W // 2, (hdr_y0 + hdr_y1) // 2, header_label, hdr_font, (62, 44, 18, 255), align="center", v_center=True)

    wm_font = _load_font(font_path, 26)
    _draw_rtl(ImageDraw.Draw(bg), W // 2, page_y1 - 42, "مَلَاك المَعْرِفَة", wm_font, (132, 98, 38, 235), align="center", v_center=True)

    bg_rgb = bg.convert("RGB")
    total_chars = max(1, sum(len(cl) for cl in clauses))
    outputs: list[tuple[Path, float]] = []

    for active_idx in range(len(clauses)):
        txt_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hld = ImageDraw.Draw(txt_layer)

        cur_y = text_start_y
        for cl_i, lines in enumerate(clause_lines_map):
            is_act = (cl_i == active_idx) and (len(clauses) > 1)
            for ln in lines:
                if cur_y + lh > zone_bot + 8:
                    break
                if is_act:
                    hld.rounded_rectangle(
                        [safe_x0 - 12, cur_y + 4, safe_x1 + 12, cur_y + lh - 6],
                        radius=14,
                        fill=(216, 170, 56, 48),
                    )
                ink = (22, 15, 8, 248) if (cl_i == active_idx or len(clauses) == 1) else (92, 82, 68, 205)
                _draw_rtl(hld, W // 2, cur_y + int(lh * 0.44), ln, chosen_font, ink, align="center", v_center=True)
                cur_y += lh

        final_img = _blend_text_into_plush(bg_rgb, txt_layer)
        out_png = base_png_path.with_name(f"{base_png_path.stem}_card_{active_idx}.png")
        final_img.save(out_png)
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
    """نمط الشرائط الصوفية المتراصة بملمس 3D حقيقي وبدون بطاقات أو أشكال زائدة."""
    fabric_bg = _make_plush_fabric_bg(W, H)
    bg = fabric_bg.convert("RGBA")
    dummy = Image.new("RGB", (10, 10))
    dd = ImageDraw.Draw(dummy)

    bd = ImageDraw.Draw(bg)
    title_font = _load_font(font_path, 24)
    clean_title = re.sub(r"[a-zA-Z]", "", ep_title or "").strip()
    if clean_title:
        _draw_rtl(bd, W // 2, 46, clean_title, title_font, (92, 72, 34, 235), align="center", v_center=True)

    hdr_box = [76, 98, W - 76, 188]
    _draw_3d_plush_pill(bg, hdr_box, radius=40, fill_rgb=(248, 242, 228), fabric_src=fabric_bg, active=True)
    hdr_font = _load_font(font_path, 34)
    _draw_rtl(ImageDraw.Draw(bg), W // 2, 143, header_label, hdr_font, (52, 38, 16, 255), align="center", v_center=True)

    n = len(clauses)
    top_area = 228
    bot_area = H - 110
    avail_h = bot_area - top_area
    gap_y = 28
    max_pill_h = max(140, (avail_h - gap_y * max(0, n - 1)) // max(1, n))

    pill_x0, pill_x1 = 44, W - 44
    txt_max_w = (pill_x1 - pill_x0) - 122

    wrapped_clauses: list[tuple[object, list[str], int]] = []
    for cl in clauses:
        chosen_f = _load_font(font_path, 26)
        chosen_lines = _wrap_text(cl, chosen_f, txt_max_w, dd)
        chosen_lh = 42
        for sz in range(38, 23, -2):
            f = _load_font(font_path, sz)
            lns = _wrap_text(cl, f, txt_max_w, dd)
            lh_c = max(46, int(sz * 1.56))
            if len(lns) * lh_c <= max_pill_h - 38 and len(lns) <= 3:
                chosen_f, chosen_lines, chosen_lh = f, lns, lh_c
                break
        wrapped_clauses.append((chosen_f, chosen_lines, chosen_lh))

    pill_heights = [max(128, min(max_pill_h, len(lns) * lh_c + 48)) for _, lns, lh_c in wrapped_clauses]
    total_stack_h = sum(pill_heights) + gap_y * max(0, n - 1)
    start_y = top_area + max(0, (avail_h - total_stack_h) // 2)

    total_chars = max(1, sum(len(cl) for cl in clauses))
    outputs: list[tuple[Path, float]] = []

    for active_idx in range(n):
        frame = bg.copy()
        txt_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cur_y = start_y

        for idx, (cl_font, lns, lh_c) in enumerate(wrapped_clauses):
            ph = pill_heights[idx]
            is_act = (idx == active_idx)
            fill_rgb = (252, 248, 236) if is_act else ((238, 244, 236) if idx % 2 == 1 else (244, 238, 224))
            _draw_3d_plush_pill(
                frame, [pill_x0, cur_y, pill_x1, cur_y + ph],
                radius=36, fill_rgb=fill_rgb, fabric_src=fabric_bg, active=is_act,
            )

            td = ImageDraw.Draw(txt_layer)
            med_cx = pill_x1 - 52
            med_cy = cur_y + ph // 2
            med_r = 25
            med_fill = (206, 162, 54, 255) if is_act else (164, 146, 108, 220)
            td.ellipse([med_cx - med_r, med_cy - med_r, med_cx + med_r, med_cy + med_r], fill=med_fill, outline=(255, 250, 235, 235), width=2)
            num_font = _load_font(font_path, 26)
            ar_num = str(idx + 1).translate(_EASTERN_DIGITS)
            _draw_rtl(td, med_cx, med_cy, ar_num, num_font, (255, 255, 255, 255), align="center", v_center=True)

            txt_cx = (pill_x0 + (pill_x1 - 92)) // 2
            block_h = len(lns) * lh_c
            ty = cur_y + (ph - block_h) // 2 + lh_c // 2
            ink = (20, 14, 8, 250) if is_act else (78, 72, 60, 225)
            for ln in lns:
                _draw_rtl(td, txt_cx, ty, ln, cl_font, ink, align="center", v_center=True)
                ty += lh_c

            cur_y += ph + gap_y

        wm_font = _load_font(font_path, 26)
        _draw_rtl(ImageDraw.Draw(txt_layer), W // 2, H - 56, "مَلَاك المَعْرِفَة", wm_font, (132, 98, 38, 235), align="center", v_center=True)

        final_img = _blend_text_into_plush(frame.convert("RGB"), txt_layer)
        out_png = base_png_path.with_name(f"{base_png_path.stem}_pill_{active_idx}.png")
        final_img.save(out_png)
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
    clean_body = re.sub(r"[*#@_\[\]<>]", "", chunk_text or "")
    clean_body = re.sub(r"[a-zA-Z]", "", clean_body)
    clean_body = clean_body.replace("ﷺ", "صلى الله عليه وسلم")
    clean_body = re.sub(r"\s+", " ", clean_body).strip()
    if not clean_body:
        return []

    header_label = HEADER_BY_TYPE.get(seg_type, "تَذْكِيرٌ وَفَائِدَةٌ")
    clauses = _split_clauses(clean_body, max_clauses=3)

    if card_style == "auto":
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
