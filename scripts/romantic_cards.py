"""
scripts/romantic_cards.py
=========================
مولد الدفتر القطني الصوفي الرومانسي 3D لمشروع «همسات ملك» (1080×1920):
- يولّد بالذكاء الاصطناعي (ZeroGPU FLUX/Z-Image -> AI Horde النشط) قالباً قطنياً صوفياً 3D
  بخيوط وأضلاع مبطنة دافئة وورود وقلب مخملي دون أي نص مشوه.
- يفحص خلوّ الوسط عبر Gemini Vision، ويدمج الخط العربي داخل ألياف الصوف مباشرة دون طمس الملمس.
- يحتوي احتياطاً محلياً بصفوف قطنية منتفخة ثلاثية الأبعاد وحلقات جانبية في حال تعذر الشبكة.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


def _check_valid_hf_token() -> str | None:
    tok = (os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACE_HUB_TOKEN", "")).strip()
    if not tok:
        return None
    try:
        r = requests.get("https://huggingface.co/api/whoami-v2", headers={"Authorization": f"Bearer {tok}"}, timeout=10)
        if r.status_code == 200:
            return tok
        print(f"   🔓 [Cards] HF_TOKEN مرفوض ({r.status_code}) — استخدام الوصول العام.", flush=True)
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
    except Exception as e:
        print(f"   ⚠️ [Cards Horde models]: {str(e)[:70]}", flush=True)
    return chosen or ["AlbedoBase XL (SDXL)", "Juggernaut XL"]


def _vision_check_clean_blank(img_path: Path) -> bool:
    """يتحقق أن وسط الدفتر خالٍ من الخربشة أو الكتابة المشوهة."""
    raw_keys = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get("GEMINI_API_KEY", "")
    keys = [k.strip() for k in re.split(r"[\s,;]+", raw_keys.strip()) if len(k.strip()) > 20]
    if not keys:
        return True
    try:
        with Image.open(img_path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((512, 512), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=82)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return False

    prompt = (
        "Inspect this blank plush notebook background. Does the middle writing area look clean and blank "
        "(without unreadable scribbled text/gibberish words)? "
        'Return ONLY JSON: {"blank_clean": true, "has_plush_texture": true}'
    )
    for k in keys[:3]:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={k}",
                json={
                    "contents": [{"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": b64}}, {"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 256, "responseMimeType": "application/json"},
                },
                timeout=20,
            )
            if r.status_code == 200:
                parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                txt = "\n".join(p.get("text", "") for p in parts if not p.get("thought") and p.get("text"))
                s, e = txt.find("{"), txt.rfind("}")
                if s != -1 and e > s:
                    res = json.loads(txt[s : e + 1])
                    return bool(res.get("blank_clean", True))
        except Exception:
            continue
    return True


def _ai_generate_blank_romantic_plush(prompt: str, neg: str, out_path: Path, W: int, H: int) -> bool:
    # 1) Hugging Face ZeroGPU (FLUX.1-schnell ثم Z-Image-Turbo)
    try:
        from gradio_client import Client as GradioClient
        tok = _check_valid_hf_token()
        for space_id in ("black-forest-labs/FLUX.1-schnell", "Tongyi-MAI/Z-Image-Turbo"):
            try:
                kwargs = {"token": tok} if tok else {}
                client = GradioClient(space_id, **kwargs)
                api = client.view_api(return_format="dict") or {}
                named = api.get("named_endpoints", {})
                ep_name = "/infer" if "/infer" in named else ("/generate" if "/generate" in named else next(iter(named.keys()), None))
                if not ep_name:
                    continue
                params = named[ep_name].get("parameters", [])
                call_kw = {}
                for p in params:
                    pn = (p.get("parameter_name") or "").lower()
                    if pn in ("prompt", "positive_prompt"):
                        call_kw[p["parameter_name"]] = prompt
                    elif pn in ("negative_prompt", "neg_prompt"):
                        call_kw[p["parameter_name"]] = neg
                    elif pn == "seed":
                        call_kw[p["parameter_name"]] = 108
                    elif pn in ("randomize_seed", "random_seed"):
                        call_kw[p["parameter_name"]] = False
                    elif pn == "width":
                        call_kw[p["parameter_name"]] = 720
                    elif pn == "height":
                        call_kw[p["parameter_name"]] = 1280
                    elif pn in ("num_inference_steps", "steps"):
                        call_kw[p["parameter_name"]] = 4 if "schnell" in space_id else 8
                    elif pn == "resolution":
                        enums = (p.get("type") or {}).get("enum") or []
                        call_kw[p["parameter_name"]] = next((e for e in enums if "9:16" in str(e) or "720x1280" in str(e)), p.get("parameter_default"))
                    elif "parameter_default" in p and p.get("parameter_has_default", True):
                        call_kw[p["parameter_name"]] = p.get("parameter_default")

                with ThreadPoolExecutor(max_workers=1) as ex:
                    res = ex.submit(lambda: client.predict(**call_kw, api_name=ep_name)).result(timeout=75)

                fpath = res[0] if isinstance(res, (list, tuple)) else res
                if isinstance(fpath, dict):
                    fpath = fpath.get("path") or fpath.get("url")
                if fpath and os.path.exists(str(fpath)):
                    with Image.open(str(fpath)) as im:
                        im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                        im.save(out_path, "JPEG", quality=93)
                    if out_path.exists() and out_path.stat().st_size > 25000:
                        print(f"   🧶 [Cards ZeroGPU] تم توليد الدفتر القطني عبر {space_id}", flush=True)
                        return True
            except Exception as e:
                print(f"   ⚠️ [Cards ZeroGPU {space_id}]: {str(e)[:90]}", flush=True)
                continue
    except Exception as e:
        print(f"   ⚠️ [Cards gradio unavailable]: {str(e)[:80]}", flush=True)

    # 2) AI Horde الديناميكي (مع مهلة 145 ثانية لضمان عدم الانقطاع في الصف)
    try:
        models = _get_active_horde_models()
        print(f"   🧶 [Cards AI Horde] محاولة توليد الدفتر القطني عبر: {models[:3]}", flush=True)
        headers = {
            "apikey": os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000",
            "Client-Agent": "HamsatMalak:3.1:romantic-plush",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": f"{prompt} ### {neg}",
            "params": {
                "sampler_name": "k_euler_a",
                "width": 576,
                "height": 1024,
                "steps": 20,
                "cfg_scale": 6.5,
                "seed": "291026",
                "n": 1,
            },
            "nsfw": False,
            "censor_nsfw": True,
            "r2": True,
            "models": models,
        }
        r = requests.post("https://aihorde.net/api/v2/generate/async", headers=headers, json=payload, timeout=20)
        if r.status_code not in (200, 202):
            print(f"   ⚠️ [Cards Horde submit HTTP {r.status_code}]: {r.text[:90]}", flush=True)
            return False
        job_id = r.json().get("id")
        if not job_id:
            return False

        deadline = time.time() + 145
        while time.time() < deadline:
            time.sleep(5)
            chk = requests.get(f"https://aihorde.net/api/v2/generate/check/{job_id}", headers=headers, timeout=15)
            if chk.status_code != 200:
                continue
            cdata = chk.json()
            if cdata.get("faulted"):
                print("   ⚠️ [Cards Horde] faulted", flush=True)
                return False
            if cdata.get("done"):
                st = requests.get(f"https://aihorde.net/api/v2/generate/status/{job_id}", headers=headers, timeout=20)
                gens = st.json().get("generations") or [] if st.status_code == 200 else []
                if gens and gens[0].get("img"):
                    ir = requests.get(gens[0]["img"], timeout=30)
                    if ir.status_code == 200 and len(ir.content) > 8000:
                        tmp = out_path.with_suffix(".webp")
                        tmp.write_bytes(ir.content)
                        with Image.open(tmp) as im:
                            im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                            im.save(out_path, "JPEG", quality=93)
                        tmp.unlink(missing_ok=True)
                        if out_path.exists() and out_path.stat().st_size > 25000:
                            print("   ✅ [Cards Horde] تم توليد الدفتر القطني الواقعي بنجاح!", flush=True)
                            return True
                return False
        print("   ⏱️ [Cards Horde] تجاوز المهلة 145ث", flush=True)
    except Exception as e:
        print(f"   ⚠️ [Cards Horde error]: {str(e)[:90]}", flush=True)
    return False


def _get_romantic_template(kind: str, W: int, H: int) -> tuple[Image.Image | None, bool]:
    repo_tpl = _REPO_ROOT / "assets" / "templates" / f"{kind}.jpg"
    if repo_tpl.exists() and repo_tpl.stat().st_size > 20000:
        with Image.open(repo_tpl) as im:
            return ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS), True

    cache_dir = Path(tempfile.gettempdir()) / "whispers_plush_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{kind}_{W}x{H}.jpg"

    with _TPL_LOCK:
        if cached.exists() and cached.stat().st_size > 20000:
            with Image.open(cached) as im:
                return im.convert("RGB"), True

        if kind == "romantic_dua_notebook":
            prompt = (
                "vertical 9:16 overhead macro studio photography of a thick ivory-cream fluffy quilted "
                "cotton-wool stitched notebook resting on a warm dark walnut wooden desk, soft horizontal "
                "pillowed plush wool ridges across the page, delicate white jasmine blossoms on the left edge "
                "and a glowing warm brass lantern on the far bottom right corner, completely blank empty "
                "cream wool center area, soft window shadows, ZERO text, ZERO letters, ZERO writing, 8k photo"
            )
            neg = (
                "text, words, letters, writing, handwriting, arabic, calligraphy, numbers, watermark, logo, "
                "couple, people, kissing, cartoon, flat 2d, smooth paper"
            )
        else:
            prompt = (
                "vertical 9:16 overhead macro studio photography of a thick warm cream-white fluffy quilted "
                "cotton-wool stitched notebook resting on a warm walnut wooden desk, soft horizontal pillowed "
                "plush wool ridges, a small crimson velvet heart ornament at the top center margin, a blooming "
                "red tulip flower resting on the far left edge, and a modest loving couple sitting gently at "
                "the far bottom right corner edge, completely blank empty fluffy cream center area, warm "
                "morning light, ZERO text, ZERO letters, ZERO handwriting, 8k macro photography"
            )
            neg = (
                "text, words, letters, writing, handwriting, arabic, calligraphy, numbers, watermark, logo, "
                "nudity, explicit, ugly hands, deformed faces, cartoon, vector, flat 2d, smooth paper"
            )

        if _ai_generate_blank_romantic_plush(prompt, neg, cached, W, H):
            is_clean = _vision_check_clean_blank(cached)
            with Image.open(cached) as im:
                return im.convert("RGB"), is_clean
    return None, False


def _make_quilted_wool_fallback(W: int, H: int, is_dua: bool, row_h: int = 86) -> Image.Image:
    """احتياط محلي بملمس أضلاع قطنية صوفية منتفخة 3D وحلقات تجليد (ليس مستطيلاً مسطحاً)."""
    desk = Image.new("RGB", (W, H), (82, 56, 40))
    grain = Image.effect_noise((W // 8, H), 22).resize((W, H), Image.Resampling.BILINEAR).convert("RGB")
    desk = ImageChops.soft_light(desk, grain).filter(ImageFilter.GaussianBlur(radius=3.5)).convert("RGBA")

    px0, py0, px1, py1 = 56, 165, W - 56, H - 165
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([px0 + 6, py0 + 14, px1 + 6, py1 + 14], radius=50, fill=(18, 10, 6, 130))
    desk.alpha_composite(sh.filter(ImageFilter.GaussianBlur(radius=18)))

    pw, ph = px1 - px0, py1 - py0
    # رسم صفوف قطنية منتفخة 3D أفقية
    rib_canvas = Image.new("RGB", (pw, ph), (244, 234, 228))
    rd = ImageDraw.Draw(rib_canvas)
    top_rgb = (252, 245, 240) if not is_dua else (250, 246, 238)
    crevice_rgb = (218, 200, 190) if not is_dua else (214, 204, 188)

    for y in range(ph):
        phase = (y % row_h) / max(1, row_h)
        puff = math.sin(math.pi * phase) ** 0.58  # قوس انتفاخ ثلاثي الأبعاد لكل سطر قطني
        col = tuple(int(crevice_rgb[c] + (top_rgb[c] - crevice_rgb[c]) * puff) for c in range(3))
        rd.line([(0, y), (pw - 1, y)], fill=col)

    # ألياف صوف ناعمة متعددة المقاييس
    wool_fine = Image.effect_noise((pw, ph), 20).convert("RGB")
    wool_coarse = Image.effect_noise((pw // 3, ph // 3), 18).resize((pw, ph), Image.Resampling.BILINEAR).convert("RGB")
    rib_canvas = ImageChops.soft_light(rib_canvas, wool_fine)
    rib_canvas = ImageChops.soft_light(rib_canvas, wool_coarse)

    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1], radius=48, fill=255)
    page = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    page.paste(rib_canvas.convert("RGBA"), (0, 0), mask)

    # لمعة 3D محيطية + خياطة محيطية دافئة
    bevel = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bevel)
    bd.rounded_rectangle([4, 4, pw - 5, ph - 8], radius=44, outline=(255, 255, 255, 210), width=6)
    bd.rounded_rectangle([4, 10, pw - 5, ph - 4], radius=44, outline=(120, 88, 72, 65), width=6)
    page = Image.alpha_composite(page, bevel.filter(ImageFilter.GaussianBlur(radius=3)))

    desk.alpha_composite(page, dest=(px0, py0))

    # حلقات دفتر قطنية جانبية يساراً (مثل نموذج صدفة)
    sp = ImageDraw.Draw(desk)
    for ry in range(py0 + 85, py1 - 75, 76):
        sp.rounded_rectangle([px0 - 24, ry - 8, px0 + 18, ry + 8], radius=8, fill=(215, 206, 198, 255), outline=(145, 130, 118, 220), width=2)

    return desk.convert("RGB")


def _blend_ink_into_wool(base_rgb: Image.Image, text_layer_rgba: Image.Image) -> Image.Image:
    composed = Image.alpha_composite(base_rgb.convert("RGBA"), text_layer_rgba).convert("RGB")
    wool_grain = Image.blend(Image.new("RGB", base_rgb.size, (255, 255, 255)), base_rgb, 0.22)
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
    font_path = _find_amiri_font()
    is_dua = (scene_category == "prayer_niqab")
    tpl_kind = "romantic_dua_notebook" if is_dua else "romantic_plush_notebook"

    safe_x0, safe_x1 = 140, W - 130
    text_max_w = safe_x1 - safe_x0
    title_y = 250
    zone_top = 340
    zone_bot = 1350
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

    chosen_font = _load_font(font_path, 48)
    grouped_lines: list[list[str]] = []
    lh = 86

    for sz in range(64, 41, -2):
        f = _load_font(font_path, sz)
        grouped = [_wrap_text(txt, f, text_max_w, dd) for txt in seg_texts]
        total_lines = sum(len(g) for g in grouped)
        cand_lh = max(84, int(sz * 1.62))
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

    ai_bg, is_clean_center = _get_romantic_template(tpl_kind, W, H)
    if ai_bg is not None:
        bg = ai_bg.convert("RGBA")
        # إذا كان الوسط نظيفاً نُبقي الملمس الصوفي 3D الحقيقي 100% مكشوفاً! وإلا ننعمه بخفة شديدة فقط
        if not is_clean_center:
            clean_center = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            fill_c = (247, 242, 234, 130) if is_dua else (250, 242, 238, 130)
            ImageDraw.Draw(clean_center).rounded_rectangle([110, 290, W - 110, 1365], radius=44, fill=fill_c)
            bg = Image.alpha_composite(bg, clean_center.filter(ImageFilter.GaussianBlur(radius=16)))
    else:
        bg = _make_quilted_wool_fallback(W, H, is_dua, row_h=lh).convert("RGBA")

    bd = ImageDraw.Draw(bg)
    clean_title = re.sub(r"[a-zA-Z]", "", title or "همسة لروحها").strip()
    clean_title = re.sub(r"م[\u064B-\u065F]*ل[\u064B-\u065F]*ك[\u064B-\u065F]*", "ملك", clean_title)
    title_font = _load_font(font_path, 52)
    _draw_rtl(bd, W // 2, title_y, clean_title, title_font, (132, 36, 48, 245) if not is_dua else (68, 52, 34, 245), align="center", v_center=True)

    sig_font = _load_font(font_path, 38)
    _draw_rtl(bd, 235, 1455, "همسات ملك", sig_font, (132, 62, 72, 235) if not is_dua else (112, 86, 46, 235), align="center", v_center=True)

    bg_rgb = bg.convert("RGB")
    outputs: list[tuple[Path, float]] = []

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
                    hl_col = (232, 150, 165, 52) if not is_dua else (218, 174, 62, 48)
                    td.rounded_rectangle(
                        [safe_x0 - 14, cur_y + 4, safe_x1 + 14, cur_y + lh - 10],
                        radius=18,
                        fill=hl_col,
                    )
                ink = (22, 14, 10, 250) if (seg_i == active_idx or len(grouped_lines) == 1) else (92, 78, 70, 200)
                _draw_rtl(td, W // 2, cur_y + int(lh * 0.44), ln, chosen_font, ink, align="center", v_center=True)
                cur_y += lh

        final_img = _blend_ink_into_wool(bg_rgb, txt_layer)
        out_png = base_png_path.with_name(f"{base_png_path.stem}_rom_{active_idx}.png")
        final_img.save(out_png)

        if use_timestamps:
            s_obj = valid_segs[active_idx]
            next_start = float(valid_segs[active_idx + 1]["start_sec"]) if active_idx + 1 < len(valid_segs) else total_dur
            dur = max(0.5, (total_dur - float(s_obj["start_sec"])) if active_idx == len(valid_segs) - 1 else (next_start - float(s_obj["start_sec"])))
        else:
            dur = total_dur / len(grouped_lines)
        outputs.append((out_png, dur))

    if outputs:
        cur_sum = sum(d for _, d in outputs)
        if abs(cur_sum - total_dur) > 0.05:
            lp, ld = outputs[-1]
            outputs[-1] = (lp, max(0.5, ld + (total_dur - cur_sum)))

    return outputs
