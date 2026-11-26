#!/usr/bin/env python3
"""
scripts/romantic_cards.py
=========================
مولد الدفتر الصوفي القطني 8K والوسائد المخيطة لمشروع «همسات ملك» (1080×1920):
- يصلح تمرير التوكن في gradio_client (token / hf_token / public) لتشغيل Z-Image-Turbo و FLUX.1-schnell.
- يدعم Gemini Native Image + ZeroGPU + AI Horde النشط.
- يفحص نظافة الوسط عبر Gemini Vision، وإذا احتوى الوسط أثراً خفيفاً ينظفه برفق مع الحفاظ على كامل إبداع 8K حوله.
"""

from __future__ import annotations

import base64
import inspect
import io
import json
import os
import random
import re
import tempfile
import threading
import time
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


def _open_gradio_client(space_id: str, tok: str | None):
    """يفتح Gradio Client بتوافق كامل مع كل إصدارات gradio_client (token أو hf_token أو عام)."""
    from gradio_client import Client as GradioClient
    if tok:
        try:
            sig = inspect.signature(GradioClient.__init__).parameters
            if "token" in sig:
                return GradioClient(space_id, token=tok)
            if "hf_token" in sig:
                return GradioClient(space_id, hf_token=tok)
        except Exception:
            pass
    return GradioClient(space_id)


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


def _get_gemini_keys() -> list[str]:
    raw_keys = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get("GEMINI_API_KEY", "")
    return [k.strip() for k in re.split(r"[\s,;]+", raw_keys.strip()) if len(k.strip()) > 20]


def _extract_gradio_image_path(obj, work_dir: Path) -> str | None:
    if not obj:
        return None
    if isinstance(obj, str):
        if os.path.exists(obj):
            return obj
        if obj.startswith(("http://", "https://")):
            try:
                ir = requests.get(obj, timeout=30)
                if ir.status_code == 200 and len(ir.content) > 8000:
                    p = work_dir / f"gr_{random.randint(1000,9999)}.png"
                    p.write_bytes(ir.content)
                    return str(p)
            except Exception:
                return None
        return None
    if isinstance(obj, dict):
        for key in ("path", "image", "url"):
            if key in obj and obj[key]:
                found = _extract_gradio_image_path(obj[key], work_dir)
                if found:
                    return found
        return None
    if isinstance(obj, (list, tuple)):
        for item in obj:
            found = _extract_gradio_image_path(item, work_dir)
            if found:
                return found
    return None


def _vision_inspect_notebook(img_path: Path) -> tuple[bool, tuple[int, int, int, int]]:
    default_box = (130, 260, 920, 625)
    keys = _get_gemini_keys()
    if not keys:
        return True, default_box

    try:
        with Image.open(img_path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((640, 640), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return False, default_box

    prompt = (
        "Inspect this vertical 9:16 plush wool notebook photo.\n"
        "1) blank_clean: true ONLY if the middle cream wool writing area is free of scribbled gibberish text/letters.\n"
        "2) box: [x0, y0, x1, y1] in 0..1000 normalized coordinates for the largest CLEAN cream wool rectangle "
        "in the upper-middle of the page (BELOW the top heart/header, ABOVE any bottom hands/couple/flowers, "
        "and RIGHT of any left tulip/spiral rings).\n"
        'Return ONLY JSON: {"blank_clean": true, "box": [130, 260, 920, 620]}'
    )
    for k in keys[:4]:
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
                    clean = bool(res.get("blank_clean", True))
                    bx = res.get("box")
                    if isinstance(bx, list) and len(bx) == 4:
                        x0, y0, x1, y1 = [int(v) for v in bx]
                        if 40 <= x0 < x1 <= 970 and 120 <= y0 < y1 <= 820 and (x1 - x0) >= 420 and (y1 - y0) >= 200:
                            return clean, (x0, y0, x1, y1)
                    return clean, default_box
        except Exception:
            continue
    return True, default_box


def _gemini_generate_image(prompt: str, out_path: Path, W: int, H: int) -> bool:
    keys = _get_gemini_keys()
    if not keys:
        return False

    models = ("gemini-2.5-flash-image", "gemini-3.1-flash-image-preview")
    logged_err = False
    for model_id in models:
        for k in keys[:4]:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={k}",
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "responseModalities": ["IMAGE", "TEXT"],
                            "imageConfig": {"aspectRatio": "9:16"},
                        },
                    },
                    timeout=55,
                )
                if r.status_code != 200:
                    if not logged_err:
                        print(f"   ℹ️ [Cards Gemini Image {model_id}]: HTTP {r.status_code} ({r.text[:90]})", flush=True)
                        logged_err = True
                    continue
                parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                for p in parts:
                    inline = p.get("inlineData") or p.get("inline_data")
                    if inline and inline.get("data"):
                        raw_bytes = base64.b64decode(inline["data"])
                        with Image.open(io.BytesIO(raw_bytes)) as im:
                            im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                            im.save(out_path, "JPEG", quality=94)
                        if out_path.exists() and out_path.stat().st_size > 30000:
                            print(f"   ✨ [Cards Gemini Image] تم توليد الدفتر القطني 8K عبر {model_id}", flush=True)
                            return True
            except Exception as e:
                if not logged_err:
                    print(f"   ℹ️ [Cards Gemini Image]: {str(e)[:80]}", flush=True)
                    logged_err = True
                continue
    return False


def _ai_generate_blank_romantic_plush(prompt: str, neg: str, out_path: Path, W: int, H: int, seed: int) -> bool:
    # 1) Gemini Native Image
    if _gemini_generate_image(prompt, out_path, W, H):
        return True

    # 2) Hugging Face ZeroGPU (Z-Image-Turbo ثم FLUX.1-schnell) مع إصلاح token/hf_token
    try:
        tok = _check_valid_hf_token()
        tmp_dir = out_path.parent
        for space_id in ("Tongyi-MAI/Z-Image-Turbo", "black-forest-labs/FLUX.1-schnell"):
            try:
                client = _open_gradio_client(space_id, tok)
                api = client.view_api(return_format="dict") or {}
                named = api.get("named_endpoints", {})
                ep_name = "/generate" if "/generate" in named else ("/infer" if "/infer" in named else next(iter(named.keys()), None))
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
                        call_kw[p["parameter_name"]] = int(seed)
                    elif pn in ("randomize_seed", "random_seed"):
                        call_kw[p["parameter_name"]] = False
                    elif pn == "width":
                        call_kw[p["parameter_name"]] = 720
                    elif pn == "height":
                        call_kw[p["parameter_name"]] = 1280
                    elif pn in ("num_inference_steps", "steps"):
                        call_kw[p["parameter_name"]] = 8 if "Z-Image" in space_id else 4
                    elif pn == "resolution":
                        enums = (p.get("type") or {}).get("enum") or []
                        call_kw[p["parameter_name"]] = next(
                            (e for e in enums if "9:16" in str(e) or "720x1280" in str(e) or "576x1024" in str(e)),
                            p.get("parameter_default"),
                        )
                    elif "parameter_default" in p and p.get("parameter_has_default", True):
                        call_kw[p["parameter_name"]] = p.get("parameter_default")

                with ThreadPoolExecutor(max_workers=1) as ex:
                    res = ex.submit(lambda: client.predict(**call_kw, api_name=ep_name)).result(timeout=90)

                fpath = _extract_gradio_image_path(res, tmp_dir)
                if fpath and os.path.exists(fpath):
                    with Image.open(fpath) as im:
                        im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                        im.save(out_path, "JPEG", quality=94)
                    if out_path.exists() and out_path.stat().st_size > 25000:
                        print(f"   🧶 [Cards ZeroGPU] تم توليد الدفتر القطني 8K عبر {space_id}", flush=True)
                        return True
                else:
                    print(f"   ⚠️ [Cards ZeroGPU {space_id}] لم يُرجع مسار صورة: {str(res)[:80]}", flush=True)
            except Exception as e:
                print(f"   ⚠️ [Cards ZeroGPU {space_id}]: {str(e)[:100]}", flush=True)
                continue
    except Exception as e:
        print(f"   ⚠️ [Cards gradio unavailable]: {str(e)[:80]}", flush=True)

    # 3) AI Horde الديناميكي (خطوات 18 لضمان السرعة والنجاح)
    try:
        models = _get_active_horde_models()
        print(f"   🧶 [Cards AI Horde] توليد الدفتر القطني عبر: {models[:3]}", flush=True)
        headers = {
            "apikey": os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000",
            "Client-Agent": "HamsatMalak:3.3:romantic-plush",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": f"{prompt} ### {neg}",
            "params": {
                "sampler_name": "k_euler_a",
                "width": 576,
                "height": 1024,
                "steps": 18,
                "cfg_scale": 6.5,
                "seed": str(seed),
                "n": 1,
            },
            "nsfw": False,
            "censor_nsfw": True,
            "r2": True,
            "models": models,
        }
        r = requests.post("https://aihorde.net/api/v2/generate/async", headers=headers, json=payload, timeout=20)
        if r.status_code not in (200, 202):
            print(f"   ⚠️ [Cards Horde HTTP {r.status_code}]: {r.text[:90]}", flush=True)
            return False
        job_id = r.json().get("id")
        if not job_id:
            return False

        deadline = time.time() + 160
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
                            im.save(out_path, "JPEG", quality=94)
                        tmp.unlink(missing_ok=True)
                        if out_path.exists() and out_path.stat().st_size > 25000:
                            print("   ✅ [Cards Horde] تم توليد الدفتر القطني الواقعي بنجاح!", flush=True)
                            return True
                return False
        print("   ⏱️ [Cards Horde] تجاوز المهلة 160ث", flush=True)
    except Exception as e:
        print(f"   ⚠️ [Cards Horde error]: {str(e)[:90]}", flush=True)
    return False


def _soft_clean_center_wool(img: Image.Image, norm_box: tuple[int, int, int, int]) -> Image.Image:
    """
    إذا رسم الـ AI بعض الخربشة الخفيفة في الوسط مع بقاء الحواف والقلب واليدين 8K مذهلة:
    ينعم وينظف مربع الكتابة فقط بريشة ناعمة تحافظ على نسيج الصوف المحيط!
    """
    w, h = img.size
    x0 = max(90, int(norm_box[0] * w / 1000) - 20)
    y0 = max(240, int(norm_box[1] * h / 1000) - 20)
    x1 = min(w - 70, int(norm_box[2] * w / 1000) + 20)
    y1 = min(int(h * 0.72), int(norm_box[3] * h / 1000) + 20)

    # أخذ متوسط لون الصوف الفاتح داخل الإطار
    crop = img.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(36))
    wool_noise = Image.effect_noise(crop.size, 11).convert("RGB")
    clean_patch = ImageChops.soft_light(crop, wool_noise)

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([x0 + 18, y0 + 18, x1 - 18, y1 - 18], radius=42, fill=215)
    mask = mask.filter(ImageFilter.GaussianBlur(26))

    patch_full = img.copy()
    patch_full.paste(clean_patch, (x0, y0))
    return Image.composite(patch_full, img, mask)


def _get_romantic_template(kind: str, W: int, H: int) -> tuple[Image.Image | None, tuple[int, int, int, int]]:
    default_box = (130, 255, 920, 625)
    repo_tpl = _REPO_ROOT / "assets" / "templates" / f"{kind}.jpg"
    if repo_tpl.exists() and repo_tpl.stat().st_size > 20000:
        _, box = _vision_inspect_notebook(repo_tpl)
        with Image.open(repo_tpl) as im:
            return ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS), box

    cache_dir = Path(tempfile.gettempdir()) / "whispers_plush_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{kind}_v4_{W}x{H}.jpg"
    box_cache = cache_dir / f"{kind}_v4_{W}x{H}_box.json"

    with _TPL_LOCK:
        if cached.exists() and cached.stat().st_size > 25000 and box_cache.exists():
            try:
                bx = tuple(json.loads(box_cache.read_text()))
                with Image.open(cached) as im:
                    return im.convert("RGB"), bx
            except Exception:
                pass

        if kind == "romantic_dua_notebook":
            prompt = (
                "Top-down flat-lay macro studio photograph of a thick puffy cream-white sherpa fleece "
                "spiral-bound notebook lying flat on a warm blurred wooden desk, metal spiral rings visible "
                "along the far left edge, soft fuzzy wool fibers and horizontal debossed quilted wool ridges "
                "across the entire page, a tiny cute 3D white pearl and golden crescent felt ornament pinned "
                "at the very top center, delicate white jasmine blossoms at the bottom left corner and a tiny "
                "warm glowing brass lantern at the bottom right corner. The ENTIRE upper-middle and center of the "
                "cream wool page is 100% BLANK, EMPTY, CLEAN, UNWRITTEN cream fleece with NO text, NO Arabic, "
                "NO English, NO letters, NO handwriting, NO scribbles anywhere. Warm dreamy daylight, 8k macro craft photo."
            )
            neg = (
                "text, words, letters, arabic, english, handwriting, calligraphy, numbers, watermark, gibberish, "
                "scribbles, lines of ink, couple, people, cartoon, flat 2d vector, angled perspective"
            )
        else:
            prompt = (
                "Top-down flat-lay macro studio photograph of a thick puffy ivory-cream sherpa wool fleece "
                "spiral-bound notebook lying flat on a warm blurred oak wooden desk, spiral binding coils along "
                "the far left edge, ultra-realistic fuzzy wool fibers and soft horizontal debossed quilted wool "
                "ridges across the page, a cute 3D puffy red velvet felt heart stitched at the very top center, "
                "a single small red tulip resting at the far left margin, two cute 3D puffy stitched white felt "
                "arms with black blanket-stitch outline holding hands around a small daisy at the bottom left, "
                "and a sweet modest loving couple sitting gently at the bottom center-right corner. "
                "The ENTIRE upper-middle and center of the cream wool page (from 20% to 65% height) is 100% BLANK, "
                "EMPTY, CLEAN, UNWRITTEN cream quilted wool with NO text, NO Arabic, NO English, NO letters, "
                "NO handwriting, NO scribbles anywhere. Warm cozy sunlight, 8k photorealistic tactile wool craft."
            )
            neg = (
                "text, words, letters, arabic, english, handwriting, calligraphy, numbers, watermark, gibberish, "
                "scribbles, ink lines, nudity, explicit, deformed hands, ugly faces, cartoon, flat 2d, angled camera"
            )

        seed = 291026 + random.randint(1, 500)
        if _ai_generate_blank_romantic_plush(prompt, neg, cached, W, H, seed):
            is_clean, box = _vision_inspect_notebook(cached)
            with Image.open(cached) as im:
                rgb = im.convert("RGB")
            if not is_clean:
                rgb = _soft_clean_center_wool(rgb, box)
                rgb.save(cached, "JPEG", quality=94)
            box_cache.write_text(json.dumps(list(box)))
            return rgb, box
    return None, default_box


def _embed_text_into_real_wool(
    base_rgb: Image.Image,
    items: list[tuple[int, int, str, object, tuple[int, int, int], str]],
) -> Image.Image:
    w, h = base_rgb.size
    out = base_rgb.convert("RGBA")

    gray = base_rgb.convert("L")
    wool_hi = ImageEnhance.Contrast(gray).enhance(1.85)
    fiber_bleed = wool_hi.point(lambda p: max(0, min(48, int((p - 178) * 0.55))))

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

        soft_mask = mask.filter(ImageFilter.GaussianBlur(0.4))
        effective_alpha = ImageChops.subtract(soft_mask, fiber_bleed)

        rim = Image.new("RGBA", (w, h), (255, 252, 245, 0))
        rim.putalpha(mask.filter(ImageFilter.GaussianBlur(1.1)).point(lambda p: int(p * 0.34)))

        halo = Image.new("RGBA", (w, h), (58, 38, 28, 0))
        halo.putalpha(mask.filter(ImageFilter.GaussianBlur(1.7)).point(lambda p: int(p * 0.18)))

        ink_layer = Image.new("RGBA", (w, h), (*rgb, 255))
        ink_layer.putalpha(effective_alpha)

        out.alpha_composite(rim, dest=(0, 2))
        out.alpha_composite(halo, dest=(0, 1))
        out.alpha_composite(ink_layer)

    return out


def _draw_stitched_pillows_fallback(
    seg_texts: list[str],
    valid_segs: list[dict],
    title: str,
    font_path: str,
    base_png_path: Path,
    W: int,
    H: int,
    total_dur: float,
) -> list[tuple[Path, float]]:
    """احتياط وسائد قطنية مخيطة 3D (نمط الصورة 1) إذا تعذّر الاتصال بكل محركات الـ AI في وضع الدعاء."""
    fur = Image.new("RGB", (W, H), (236, 202, 210))
    fd = ImageDraw.Draw(fur)
    for y in range(H):
        t = y / H
        fd.line([(0, y), (W, y)], fill=(int(242 - 18 * t), int(208 - 22 * t), int(215 - 16 * t)))
    noise = Image.effect_noise((W // 2, H // 2), 18).resize((W, H), Image.Resampling.BILINEAR).convert("RGB")
    fur = ImageChops.soft_light(fur, noise).convert("RGBA")

    def _pillow(cv, box, fill_c, stitch_c, rad=40, gold=False):
        x0, y0, x1, y1 = box
        pw, ph = x1 - x0, y1 - y0
        pad = 24
        lw, lh = pw + pad * 2, ph + pad * 2
        sh_m = Image.new("L", (lw, lh), 0)
        ImageDraw.Draw(sh_m).rounded_rectangle([pad, pad + 7, pad + pw, pad + ph + 7], radius=rad, fill=130)
        sh = Image.new("RGBA", (lw, lh), (72, 34, 46, 0))
        sh.putalpha(sh_m.filter(ImageFilter.GaussianBlur(13)))
        pm = Image.new("L", (lw, lh), 0)
        ImageDraw.Draw(pm).rounded_rectangle([pad, pad, pad + pw, pad + ph], radius=rad, fill=255)
        body = Image.new("RGBA", (lw, lh), (*fill_c, 255))
        bd = ImageDraw.Draw(body)
        for dy in range(ph):
            t = dy / max(1, ph)
            if t < 0.25:
                bd.line([(pad, pad + dy), (pad + pw, pad + dy)], fill=(255, 252, 248, int(68 * (1 - t / 0.25))))
            elif t > 0.72:
                bd.line([(pad, pad + dy), (pad + pw, pad + dy)], fill=(116, 62, 74, int(55 * ((t - 0.72) / 0.28))))
        body.putalpha(pm)
        sd = ImageDraw.Draw(body)
        for sx in range(pad + 14 + rad // 2, pad + pw - 14 - rad // 2, 18):
            sd.line([(sx, pad + 14), (sx + 9, pad + 14)], fill=(*stitch_c, 180), width=3)
            sd.line([(sx, pad + ph - 14), (sx + 9, pad + ph - 14)], fill=(*stitch_c, 180), width=3)
        if gold:
            sd.rounded_rectangle([pad + 2, pad + 2, pad + pw - 2, pad + ph - 2], radius=rad, outline=(212, 162, 84, 235), width=5)
        out = cv.convert("RGBA")
        out.alpha_composite(sh, dest=(x0 - pad, y0 - pad))
        out.alpha_composite(body, dest=(x0 - pad, y0 - pad))
        return out

    fur = _pillow(fur, (150, 130, W - 150, 310), (250, 242, 234), (218, 178, 184), rad=60)
    clean_title = re.sub(r"[a-zA-Z]", "", title or "دعاء لروحها").strip()
    clean_title = re.sub(r"م[\u064B-\u065F]*ل[\u064B-\u065F]*ك[\u064B-\u065F]*", "ملك", clean_title)
    t_font = _load_font(font_path, 50)
    fur = _embed_text_into_real_wool(fur.convert("RGB"), [(W // 2, 220, clean_title, t_font, (46, 30, 24), "center")]).convert("RGBA")

    dummy_d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    shown = seg_texts[:5]
    n_b = max(1, len(shown))
    box_h = min(240, (1540 - 360 - 26 * (n_b - 1)) // n_b)
    start_y = 360 + (1540 - 360 - (n_b * box_h + (n_b - 1) * 26)) // 2
    outputs: list[tuple[Path, float]] = []
    use_ts = all("start_sec" in s and "end_sec" in s for s in valid_segs)

    for act_i in range(len(seg_texts)):
        frame = fur.copy()
        items: list[tuple[int, int, str, object, tuple[int, int, int], str]] = []
        for bi, txt in enumerate(shown):
            by0 = start_y + bi * (box_h + 26)
            by1 = by0 + box_h
            is_c = (bi % 2 == 0)
            frame = _pillow(
                frame,
                (76, by0, W - 76, by1),
                (250, 243, 235) if is_c else (228, 176, 186),
                (208, 176, 168) if is_c else (188, 126, 138),
                rad=40,
                gold=(bi == min(act_i, n_b - 1)),
            )
            chosen_f = _load_font(font_path, 34)
            lines = [txt]
            for sz in range(46, 28, -2):
                f = _load_font(font_path, sz)
                lns = _wrap_text(txt, f, W - 260, dummy_d)
                if len(lns) <= 4 and len(lns) * int(sz * 1.62) <= box_h - 24:
                    chosen_f, lines = f, lns
                    break
            lh = int(chosen_f.size * 1.62)
            cy_t = (by0 + by1) // 2 - ((len(lines) - 1) * lh) // 2
            for ln in lines:
                items.append((W // 2, cy_t, ln, chosen_f, (38, 22, 18), "center"))
                cy_t += lh

        items.append((W // 2, 1645, "همسات ملك", _load_font(font_path, 34), (118, 48, 62), "center"))
        final_img = _embed_text_into_real_wool(frame.convert("RGB"), items).convert("RGB")
        out_png = base_png_path.with_name(f"{base_png_path.stem}_pillow_{act_i}.png")
        final_img.save(out_png)
        dur = total_dur / len(seg_texts)
        if use_ts and act_i < len(valid_segs):
            s_obj = valid_segs[act_i]
            nxt = float(valid_segs[act_i + 1]["start_sec"]) if act_i + 1 < len(valid_segs) else total_dur
            dur = max(0.5, (total_dur - float(s_obj["start_sec"])) if act_i == len(valid_segs) - 1 else (nxt - float(s_obj["start_sec"])))
        outputs.append((out_png, dur))
    return outputs


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

    valid_segs = [s for s in segments if (s.get("display_text") or s.get("text") or "").strip()]
    seg_texts = [re.sub(r"\s+", " ", (s.get("display_text") or s.get("text") or "").strip()) for s in valid_segs]
    if not seg_texts:
        return []

    ai_bg, norm_box = _get_romantic_template(tpl_kind, W, H)
    if ai_bg is None:
        if is_dua:
            return _draw_stitched_pillows_fallback(seg_texts, valid_segs, title, font_path, base_png_path, W, H, total_dur)
        # احتياط دافئ مبطن
        ai_bg = Image.new("RGB", (W, H), (246, 236, 228))
        noise = Image.effect_noise((W // 2, H // 2), 14).resize((W, H), Image.Resampling.BILINEAR).convert("RGB")
        ai_bg = ImageChops.soft_light(ai_bg, noise)
        norm_box = (120, 240, 880, 680)

    bx0 = max(110, int(norm_box[0] * W / 1000))
    by0 = max(310, int(norm_box[1] * H / 1000))
    bx1 = min(W - 90, int(norm_box[2] * W / 1000))
    by1 = min(int(H * 0.68), int(norm_box[3] * H / 1000))
    if bx1 - bx0 < 520:
        bx0, bx1 = 135, W - 110
    if by1 - by0 < 380:
        by0, by1 = 380, 1140

    text_max_w = bx1 - bx0
    text_max_h = by1 - by0
    center_x = (bx0 + bx1) // 2

    dummy_d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    chosen_font = _load_font(font_path, 40)
    grouped_lines: list[list[str]] = []
    lh = 76

    for sz in range(58, 33, -2):
        f = _load_font(font_path, sz)
        grouped = [_wrap_text(txt, f, text_max_w, dummy_d) for txt in seg_texts]
        total_lines = sum(len(g) for g in grouped)
        cand_lh = max(72, int(sz * 1.80))
        if total_lines * cand_lh <= text_max_h:
            chosen_font, grouped_lines, lh = f, grouped, cand_lh
            break
    else:
        grouped_lines = [_wrap_text(txt, chosen_font, text_max_w, dummy_d) for txt in seg_texts]
        lh = max(68, int(chosen_font.size * 1.72))

    total_lines = max(1, sum(len(g) for g in grouped_lines))
    total_text_h = total_lines * lh
    text_start_y = by0 + max(0, (text_max_h - total_text_h) // 2)

    now = datetime.now()
    date_str = now.strftime("%Y/%m/%d")
    day_str = _AR_DAYS.get(now.weekday(), "يوم سعيد")

    clean_title = re.sub(r"[a-zA-Z]", "", title or "همسة لروحها").strip()
    clean_title = re.sub(r"م[\u064B-\u065F]*ل[\u064B-\u065F]*ك[\u064B-\u065F]*", "ملك", clean_title)

    title_font = _load_font(font_path, 46)
    date_font = _load_font(font_path, 38)
    sig_font = _load_font(font_path, 34)

    title_y = max(230, by0 - 58)
    static_items = [
        (center_x + 35, title_y, clean_title, title_font, (64, 24, 28) if not is_dua else (58, 42, 26), "center"),
        (W - 115, 1180, date_str, date_font, (38, 24, 22), "right"),
        (W - 120, 1242, day_str, date_font, (52, 32, 28), "right"),
        (W // 2, 1650, "همسات ملك", sig_font, (124, 34, 48) if not is_dua else (106, 82, 42), "center"),
    ]
    base_with_meta = _embed_text_into_real_wool(ai_bg, static_items).convert("RGB")

    outputs: list[tuple[Path, float]] = []
    use_ts = all("start_sec" in s and "end_sec" in s for s in valid_segs)

    for act_idx in range(len(grouped_lines)):
        text_items: list[tuple[int, int, str, object, tuple[int, int, int], str]] = []
        cur_y = text_start_y
        for seg_i, lines in enumerate(grouped_lines):
            ink = (24, 16, 14) if (seg_i == act_idx or act_idx == len(grouped_lines) - 1) else (92, 78, 70)
            for ln in lines:
                if cur_y + lh <= by1 + 24:
                    text_items.append((center_x, cur_y + int(lh * 0.48), ln, chosen_font, ink, "center"))
                cur_y += lh

        final_img = _embed_text_into_real_wool(base_with_meta, text_items).convert("RGB")
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
