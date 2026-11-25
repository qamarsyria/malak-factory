"""
scripts/romantic_cards.py
=========================
مولد الدفتر القطني الصوفي الرومانسي 3D لمشروع «همسات ملك» (1080×1920):
- يولّد بالذكاء الاصطناعي (FLUX / Juggernaut XL) قالب دفتر قطني/صوفي دافئ فارغ تماماً بدون أي نص
  (مع حفظه في الكاش مرة واحدة أو قراءته من assets/templates/)، ثم يدمج فوقه الأشعار والأدعية
  بخط Amiri الأصيل مع امتزاج الحبر بألياف الصوف وتوهج ذهبي-وردي يتتبع المقطع المنطوق حالياً.
- نسخة مخصصة للأشعار والغرام العفيف (ورود وقلب مخملي وتفاصيل دافئة) وأخرى خاشعة للأدعية (ياسمين وفانوس).
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


def _ai_generate_blank_romantic_plush(prompt: str, neg: str, out_path: Path, W: int, H: int) -> bool:
    # 1) Hugging Face ZeroGPU (FLUX.1-schnell)
    try:
        from gradio_client import Client as GradioClient
        tok = (os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACE_HUB_TOKEN", "")).strip() or None
        for attempt_anon in (False, True):
            kwargs = {} if (attempt_anon or not tok) else {"token": tok}
            try:
                client = GradioClient("black-forest-labs/FLUX.1-schnell", **kwargs)
                res = client.predict(
                    prompt=prompt,
                    seed=108,
                    randomize_seed=False,
                    width=720,
                    height=1280,
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
                    return out_path.exists() and out_path.stat().st_size > 25000
                break
            except Exception as e:
                if "401" in str(e) and not attempt_anon:
                    os.environ.pop("HF_TOKEN", None)
                    os.environ.pop("HUGGINGFACE_HUB_TOKEN", None)
                    continue
                break
    except Exception:
        pass

    # 2) AI Horde المجاني بنماذج الواقعية الملموسة
    try:
        headers = {
            "apikey": os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000",
            "Client-Agent": "HamsatMalak:3.0:romantic-plush",
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
                "seed": "291026",
                "n": 1,
            },
            "nsfw": False,
            "censor_nsfw": True,
            "r2": True,
            "models": ["Juggernaut XL", "RealVisXL", "DreamShaper XL", "AlbedoBase XL (SDXL)"],
        }
        r = requests.post("https://aihorde.net/api/v2/generate/async", headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 202):
            job_id = r.json().get("id")
            if job_id:
                deadline = time.time() + 90
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
                                return out_path.exists() and out_path.stat().st_size > 25000
                        break
    except Exception:
        pass
    return False


def _get_romantic_template(kind: str, W: int, H: int) -> Image.Image | None:
    repo_tpl = _REPO_ROOT / "assets" / "templates" / f"{kind}.jpg"
    if repo_tpl.exists() and repo_tpl.stat().st_size > 20000:
        with Image.open(repo_tpl) as im:
            return ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)

    cache_dir = Path(tempfile.gettempdir()) / "whispers_plush_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{kind}_{W}x{H}.jpg"

    with _TPL_LOCK:
        if cached.exists() and cached.stat().st_size > 20000:
            with Image.open(cached) as im:
                return im.convert("RGB")

        if kind == "romantic_dua_notebook":
            prompt = (
                "vertical 9:16 overhead macro studio photography of a thick ivory-cream fluffy quilted "
                "cotton-wool stitched notebook resting on a warm walnut wooden desk, soft horizontal plush "
                "wool ridges, delicate blooming white jasmine flowers along the far left margin and a small "
                "warm glowing golden lantern ornament at the far bottom-right corner, soft serene twilight "
                "window shadows, completely blank empty ivory cream center page area, "
                "ZERO text, ZERO letters, ZERO Arabic writing, ZERO numbers, 8k photorealistic macro"
            )
            neg = (
                "text, words, letters, writing, arabic, calligraphy, numbers, watermark, logo, "
                "couple, people, kissing, cartoon, flat 2d, neon"
            )
        else:
            prompt = (
                "vertical 9:16 overhead macro studio photography of a thick warm cream-white fluffy quilted "
                "cotton-wool stitched notebook resting on a warm dark walnut wooden desk, soft cozy "
                "horizontal plush ridges across the page, a small plush crimson velvet heart resting at the "
                "top center margin, a single red blooming tulip flower on the far left edge, and a romantic "
                "modest loving couple sitting peacefully at the far bottom-right corner edge, warm golden "
                "morning light across a completely blank empty cream wool center area, "
                "ZERO text, ZERO letters, ZERO Arabic writing, ZERO numbers, 8k photorealistic macro"
            )
            neg = (
                "text, words, letters, writing, arabic, calligraphy, numbers, watermark, logo, "
                "nudity, explicit, ugly hands, deformed faces, cartoon, vector, flat 2d"
            )

        if _ai_generate_blank_romantic_plush(prompt, neg, cached, W, H):
            with Image.open(cached) as im:
                return im.convert("RGB")
    return None


def _make_fallback_romantic_notebook(W: int, H: int, is_dua: bool) -> Image.Image:
    # طاولة خشب دافئة مموهة + صفحة قطنية مخملية مبطنة
    desk = Image.new("RGB", (W, H), (88, 62, 46))
    grain = Image.effect_noise((W // 8, H), 20).resize((W, H), Image.Resampling.BILINEAR).convert("RGB")
    desk = ImageChops.soft_light(desk, grain).filter(ImageFilter.GaussianBlur(radius=3)).convert("RGBA")

    px0, py0, px1, py1 = 58, 175, W - 58, H - 175
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([px0 + 4, py0 + 12, px1 + 4, py1 + 12], radius=48, fill=(22, 14, 10, 115))
    sh = sh.filter(ImageFilter.GaussianBlur(radius=16))
    desk.alpha_composite(sh)

    pw, ph = px1 - px0, py1 - py0
    page_rgb = (247, 242, 234) if is_dua else (250, 241, 238)
    tex = Image.effect_noise((pw, ph), 14).convert("RGB")
    page_fill = ImageChops.soft_light(Image.new("RGB", (pw, ph), page_rgb), tex).convert("RGBA")

    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1], radius=46, fill=255)
    page = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    page.paste(page_fill, (0, 0), mask)

    bevel = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bevel)
    bd.rounded_rectangle([4, 4, pw - 5, ph - 8], radius=42, outline=(255, 255, 255, 200), width=5)
    bd.rounded_rectangle([4, 10, pw - 5, ph - 4], radius=42, outline=(120, 92, 74, 55), width=5)
    page = Image.alpha_composite(page, bevel.filter(ImageFilter.GaussianBlur(radius=3)))

    desk.alpha_composite(page, dest=(px0, py0))
    return desk.convert("RGB")


def _blend_ink_into_wool(base_rgb: Image.Image, text_layer_rgba: Image.Image) -> Image.Image:
    composed = Image.alpha_composite(base_rgb.convert("RGBA"), text_layer_rgba).convert("RGB")
    wool_grain = Image.blend(Image.new("RGB", base_rgb.size, (255, 255, 255)), base_rgb, 0.16)
    return ImageChops.multiply(composed, wool_grain)


def render_romantic_notebook_slides(
    segments: list[dict],
    title: str,
    scene_category: str,
    base_png_path: Path,
    W: int = 1080,
    H: int = 1920,
    total_dur: float = 20.0,
) -> list[tuple[Path, float]]:
    """
    يرسم جميع مقاطع الهمسة/الشعر/الدعاء على دفتر قطني رومانسي 3D،
    ويُنتج شريحة لكل مقطع لتنتقل الإضاءة الدافئة إلى المقطع المنطوق حالياً بالتزامن مع الصوت.
    """
    font_path = _find_amiri_font()
    is_dua = (scene_category == "prayer_niqab")
    tpl_kind = "romantic_dua_notebook" if is_dua else "romantic_plush_notebook"

    ai_bg = _get_romantic_template(tpl_kind, W, H)
    if ai_bg is not None:
        bg = ai_bg.convert("RGBA")
        # طبقة تنعيم قطنية لطيفة في وسط الصفحة لضمان وضوح 100% للنص العربي مع بقاء القلب/الزهور/الخشب
        clean_center = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cd = ImageDraw.Draw(clean_center)
        fill_c = (247, 242, 234, 165) if is_dua else (250, 242, 238, 165)
        cd.rounded_rectangle([92, 260, W - 92, 1385], radius=48, fill=fill_c)
        clean_center = clean_center.filter(ImageFilter.GaussianBlur(radius=14))
        bg = Image.alpha_composite(bg, clean_center)
    else:
        bg = _make_fallback_romantic_notebook(W, H, is_dua).convert("RGBA")

    # المنطقة الآمنة للكتابة (تترك أعلى الصفحة للقلب/العنوان وأسفل اليمين للرسم الرومانسي)
    safe_x0, safe_x1 = 128, W - 128
    text_max_w = safe_x1 - safe_x0
    title_y = 250
    zone_top = 345
    zone_bot = 1345
    text_max_h = zone_bot - zone_top

    dummy = Image.new("RGB", (10, 10))
    dd = ImageDraw.Draw(dummy)

    seg_texts = [
        re.sub(r"\s+", " ", (s.get("display_text") or s.get("text") or "").strip())
        for s in segments
        if (s.get("display_text") or s.get("text") or "").strip()
    ]
    if not seg_texts:
        return []

    chosen_font = _load_font(font_path, 42)
    grouped_lines: list[list[str]] = []
    lh = 76

    for sz in range(62, 37, -3):
        f = _load_font(font_path, sz)
        grouped = [_wrap_text(txt, f, text_max_w, dd) for txt in seg_texts]
        total_lines = sum(len(g) for g in grouped)
        cand_lh = max(78, int(sz * 1.65))
        if total_lines * cand_lh <= text_max_h:
            chosen_font = f
            grouped_lines = grouped
            lh = cand_lh
            break
    else:
        grouped_lines = [_wrap_text(txt, chosen_font, text_max_w, dd) for txt in seg_texts]

    total_lines = max(1, sum(len(g) for g in grouped_lines))
    total_text_h = total_lines * lh
    text_start_y = zone_top + max(0, (text_max_h - total_text_h) // 2)

    # خطوط السطور المبطنة الخفيفة على الصفحة القطنية
    bd = ImageDraw.Draw(bg)
    first_rule = text_start_y + int(lh * 0.86)
    for ry in range(first_rule, min(zone_bot + 20, first_rule + total_lines * lh), lh):
        bd.line([(safe_x0 - 10, ry + 3), (safe_x1 + 10, ry + 3)], fill=(255, 255, 252, 185), width=3)
        bd.line([(safe_x0 - 10, ry), (safe_x1 + 10, ry)], fill=(208, 192, 178, 145), width=2)

    # عنوان الهمسة أعلى الصفحة + توقيع أسفل اليسار (بعيداً عن الرسمة أسفل اليمين)
    clean_title = re.sub(r"[a-zA-Z]", "", title or "همسة لروحها").strip()
    clean_title = re.sub(r"م[\u064B-\u065F]*ل[\u064B-\u065F]*ك[\u064B-\u065F]*", "ملك", clean_title)
    title_font = _load_font(font_path, 52)
    _draw_rtl(bd, W // 2, title_y, clean_title, title_font, (128, 42, 54, 245) if not is_dua else (68, 52, 34, 245), align="center", v_center=True)

    sig_font = _load_font(font_path, 38)
    _draw_rtl(bd, 235, 1445, "همسات ملك", sig_font, (132, 62, 72, 230) if not is_dua else (112, 86, 46, 230), align="center", v_center=True)

    bg_rgb = bg.convert("RGB")
    outputs: list[tuple[Path, float]] = []

    # حساب مدد المقاطع المتطابقة مع الصوت الفعلي
    valid_segs = [s for s in segments if (s.get("display_text") or s.get("text") or "").strip()]
    use_timestamps = all("start_sec" in s and "end_sec" in s for s in valid_segs)

    for active_idx in range(len(grouped_lines)):
        txt_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(txt_layer)

        cur_y = text_start_y
        for seg_i, lines in enumerate(grouped_lines):
            is_act = (seg_i == active_idx) and (len(grouped_lines) > 1)
            for ln in lines:
                if cur_y + lh > zone_bot + 10:
                    break
                if is_act:
                    hl_col = (225, 152, 162, 46) if not is_dua else (218, 174, 62, 44)
                    td.rounded_rectangle(
                        [safe_x0 - 16, cur_y + 4, safe_x1 + 16, cur_y + lh - 8],
                        radius=18,
                        fill=hl_col,
                    )
                ink = (26, 16, 12, 250) if (seg_i == active_idx or len(grouped_lines) == 1) else (98, 84, 76, 200)
                _draw_rtl(td, W // 2, cur_y + int(lh * 0.44), ln, chosen_font, ink, align="center", v_center=True)
                cur_y += lh

        final_img = _blend_ink_into_wool(bg_rgb, txt_layer)
        out_png = base_png_path.with_name(f"{base_png_path.stem}_rom_{active_idx}.png")
        final_img.save(out_png)

        if use_timestamps:
            s_obj = valid_segs[active_idx]
            next_start = float(valid_segs[active_idx + 1]["start_sec"]) if active_idx + 1 < len(valid_segs) else total_dur
            dur = max(0.5, next_start - float(s_obj["start_sec"])) if active_idx == 0 else max(0.5, (total_dur - float(s_obj["start_sec"])) if active_idx == len(valid_segs) - 1 else (next_start - float(s_obj["start_sec"])))
        else:
            dur = total_dur / len(grouped_lines)
        outputs.append((out_png, dur))

    if outputs:
        cur_sum = sum(d for _, d in outputs)
        if abs(cur_sum - total_dur) > 0.05:
            lp, ld = outputs[-1]
            outputs[-1] = (lp, max(0.5, ld + (total_dur - cur_sum)))

    return outputs
