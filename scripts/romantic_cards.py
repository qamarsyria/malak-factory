#!/usr/bin/env python3
"""
scripts/romantic_cards.py
=========================
مولد الدفتر الصوفي القطني 8K والوسائد المخيطة لمشروع «همسات ملك» (1080×1920):
- يولّد لوحة ماكرو فوتوغرافية حقيقية 8K بالذكاء الاصطناعي عبر:
  1) Gemini Native Image (gemini-3.1-flash-image-preview / gemini-2.5-flash-image)
  2) Hugging Face ZeroGPU (Z-Image-Turbo مع إصلاح استخراج Gallery / FLUX.1-schnell)
  3) AI Horde النشط (Juggernaut XL / RealVisXL)
- يفحص نظافة الوسط ويستخرج إحداثيات منطقة الكتابة الآمنة عبر Gemini Vision
  بحيث لا يتصادم النص العربي أبداً مع القلب اللباد أو اليدين أو الكوبل أو الزهور.
- يغرس الخط العربي داخل ألياف الصوف الحقيقية بدون تغميق اللوحة أو طمس الوبر.
"""

from __future__ import annotations

import base64
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


def _get_gemini_keys() -> list[str]:
    raw_keys = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get("GEMINI_API_KEY", "")
    return [k.strip() for k in re.split(r"[\s,;]+", raw_keys.strip()) if len(k.strip()) > 20]


def _extract_gradio_image_path(obj, work_dir: Path) -> str | None:
    """يستخرج مسار الصورة من مخرجات Gradio مهما كان شكلها (list / Gallery / dict / URL)."""
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
    """
    يفحص عبر Gemini Vision أن القاعدة خالية من النصوص المشوهة ويحدد المساحة الفارغة الآمنة للكتابة
    بنسبة 0..1000 حتى لا يركب الخط على القلب أو اليدين أو الزهور.
    """
    default_box = (130, 265, 920, 625)  # إحداثيات 0..1000 افتراضية آمنة
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
        "1) blank_clean: true ONLY if the center cream wool area is completely clean and free of scribbled gibberish text/letters.\n"
        "2) box: [x0, y0, x1, y1] in 0..1000 normalized coordinates for the largest CLEAN EMPTY cream wool rectangle "
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
                        if 40 <= x0 < x1 <= 970 and 120 <= y0 < y1 <= 820 and (x1 - x0) >= 450 and (y1 - y0) >= 220:
                            return clean, (x0, y0, x1, y1)
                    return clean, default_box
        except Exception:
            continue
    return True, default_box


def _gemini_generate_image(prompt: str, out_path: Path, W: int, H: int) -> bool:
    """توليد القاعدة الصوفية الواقعية 8K مباشرة عبر Gemini Image (Nano Banana)."""
    keys = _get_gemini_keys()
    if not keys:
        return False

    models = ("gemini-3.1-flash-image-preview", "gemini-2.5-flash-image")
    for model_id in models:
        for k in keys[:5]:
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
                    timeout=60,
                )
                if r.status_code != 200:
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
            except Exception:
                continue
    return False


def _ai_generate_blank_romantic_plush(prompt: str, neg: str, out_path: Path, W: int, H: int, seed: int) -> bool:
    # 1) Gemini Native Image (الأعلى واقعية ومطابقة لصورة صدفة)
    if _gemini_generate_image(prompt, out_path, W, H):
        return True

    # 2) Hugging Face ZeroGPU (Z-Image-Turbo مع إصلاح Gallery ثم FLUX.1-schnell)
    try:
        from gradio_client import Client as GradioClient
        tok = _check_valid_hf_token()
        tmp_dir = out_path.parent
        for space_id in ("Tongyi-MAI/Z-Image-Turbo", "black-forest-labs/FLUX.1-schnell"):
            try:
                kwargs = {"token": tok} if tok else {}
                client = GradioClient(space_id, **kwargs)
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
                    res = ex.submit(lambda: client.predict(**call_kw, api_name=ep_name)).result(timeout=85)

                fpath = _extract_gradio_image_path(res, tmp_dir)
                if fpath and os.path.exists(fpath):
                    with Image.open(fpath) as im:
                        im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                        im.save(out_path, "JPEG", quality=94)
                    if out_path.exists() and out_path.stat().st_size > 25000:
                        print(f"   🧶 [Cards ZeroGPU] تم توليد الدفتر القطني 8K عبر {space_id}", flush=True)
                        return True
            except Exception as e:
                print(f"   ⚠️ [Cards ZeroGPU {space_id}]: {str(e)[:90]}", flush=True)
                continue
    except Exception as e:
        print(f"   ⚠️ [Cards gradio unavailable]: {str(e)[:80]}", flush=True)

    # 3) AI Horde الديناميكي
    try:
        models = _get_active_horde_models()
        print(f"   🧶 [Cards AI Horde] توليد الدفتر القطني عبر: {models[:3]}", flush=True)
        headers = {
            "apikey": os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000",
            "Client-Agent": "HamsatMalak:3.2:romantic-plush",
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
    except Exception as e:
        print(f"   ⚠️ [Cards Horde error]: {str(e)[:90]}", flush=True)
    return False


def _get_romantic_template(kind: str, W: int, H: int) -> tuple[Image.Image | None, tuple[int, int, int, int]]:
    default_box = (130, 255, 920, 625)
    repo_tpl = _REPO_ROOT / "assets" / "templates" / f"{kind}.jpg"
    if repo_tpl.exists() and repo_tpl.stat().st_size > 20000:
        _, box = _vision_inspect_notebook(repo_tpl)
        with Image.open(repo_tpl) as im:
            return ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS), box

    cache_dir = Path(tempfile.gettempdir()) / "whispers_plush_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{kind}_v3_{W}x{H}.jpg"
    box_cache = cache_dir / f"{kind}_v3_{W}x{H}_box.json"

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

        for attempt in range(2):
            seed = 291026 + attempt * 733 + random.randint(1, 200)
            if _ai_generate_blank_romantic_plush(prompt, neg, cached, W, H, seed):
                is_clean, box = _vision_inspect_notebook(cached)
                if is_clean or attempt == 1:
                    box_cache.write_text(json.dumps(list(box)))
                    with Image.open(cached) as im:
                        return im.convert("RGB"), box
                cached.unlink(missing_ok=True)
    return None, default_box


def _embed_text_into_real_wool(
    base_rgb: Image.Image,
    items: list[tuple[int, int, str, object, tuple[int, int, int], str]],
) -> Image.Image:
    """
    يغرس الخط العربي داخل ألياف الصوف الحقيقية المولّدة بالذكاء الاصطناعي:
    - لا يغمّق خلفية الدفتر إطلاقاً!
    - يسرّب وبر الصوف المضيء داخل الحرف + ظل انغماس دقيق أسفل الحرف.
    """
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

        # إضاءة انغماس دقيقة أسفل الحرف (1.5px) توحي بضغط الحبر في الوبر
        rim = Image.new("RGBA", (w, h), (255, 252, 245, 0))
        rim.putalpha(mask.filter(ImageFilter.GaussianBlur(1.1)).point(lambda p: int(p * 0.34)))

        # ظل ناعم غائر داخل ألياف الصوف
        halo = Image.new("RGBA", (w, h), (58, 38, 28, 0))
        halo.putalpha(mask.filter(ImageFilter.GaussianBlur(1.7)).point(lambda p: int(p * 0.18)))

        ink_layer = Image.new("RGBA", (w, h), (*rgb, 255))
        ink_layer.putalpha(effective_alpha)

        out.alpha_composite(rim, dest=(0, 2))
        out.alpha_composite(halo, dest=(0, 1))
        out.alpha_composite(ink_layer)

    return out


def _make_clean_emergency_fallback(W: int, H: int, is_dua: bool) -> Image.Image:
    """احتياط نظيف وهادئ جداً عند انقطاع كل محركات الـ AI (بدون أي رسومات كرتونية)."""
    bg = Image.new("RGB", (W, H), (246, 236, 228) if not is_dua else (244, 240, 234))
    d = ImageDraw.Draw(bg)
    c_top = (252, 244, 238) if not is_dua else (248, 244, 236)
    c_bot = (232, 212, 206) if not is_dua else (224, 216, 204)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(c_top[i] + (c_bot[i] - c_top[i]) * t) for i in range(3)))
    noise = Image.effect_noise((W // 2, H // 2), 12).resize((W, H), Image.Resampling.BILINEAR).convert("RGB")
    bg = ImageChops.soft_light(bg, noise)
    d2 = ImageDraw.Draw(bg)
    d2.rounded_rectangle([54, 160, W - 54, H - 170], radius=48, outline=(212, 186, 174), width=4)
    return bg


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
        ai_bg = _make_clean_emergency_fallback(W, H, is_dua)
        norm_box = (120, 240, 880, 680)

    # تحويل إحداثيات المساحة الفارغة الآمنة من 0..1000 إلى بكسل حقيقي
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

    # تباعد سطور واسع 1.80x حتى لا تتداخل الحركات والتشكيل أبداً
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

    # كتابة العنوان والتاريخ وتوقيع همسات ملك في مواضع آمنة بعيدة عن الوسط
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
