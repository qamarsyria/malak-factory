#!/usr/bin/env python3
"""
scripts/islamic_cards.py
=========================
مولد البطاقات والدفاتر الإسلامية الروحانية 8K بملمس صوفي حقيقي + خط عربي متقن:
- يدعم Gemini Native Image + ZeroGPU (Z-Image-Turbo / FLUX.1-schnell via _open_gradio_client) + AI Horde النشط.
- يفحص نظافة الوسط وخلوّه من المحظورات via Gemini Vision مع الحفاظ الكامل على ملمس الصوف والزخارف الإسلامية.
- يجمع بين نمط المخطوطة الصوفية الدينية (للحديث والفقه) ونمط الوسائد القطنية المخيطة 3D (للأذكار والـ CTA).
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
from pathlib import Path

import arabic_reshaper
import requests
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, features

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

PILLOW_SEG_TYPES = {"adhkar", "cta", "outro"}


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


def _split_clauses(text: str, max_clauses: int = 4) -> list[str]:
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
# أدوات الاتصال الذكي بمحركات الذكاء الاصطناعي (Gemini Image / ZeroGPU / AI Horde)
# ==============================================================================

def _get_gemini_keys() -> list[str]:
    raw_keys = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get("GEMINI_API_KEY", "")
    return [k.strip() for k in re.split(r"[\s,;]+", raw_keys.strip()) if len(k.strip()) > 20]


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
    return None


def _open_gradio_client(space_id: str, tok: str | None):
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


def _vision_inspect_islamic_card(img_path: Path) -> tuple[bool, tuple[int, int, int, int]]:
    default_box = (110, 225, 890, 825)
    keys = _get_gemini_keys()
    if not keys:
        return True, default_box

    try:
        with Image.open(img_path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((512, 512), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=84)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return False, default_box

    prompt = (
        "Inspect this vertical 9:16 Islamic plush wool notebook/fabric background.\n"
        "1) halal_ok: false if there are humans, uncovered women, couples, red romantic hearts, crosses, or statues.\n"
        "2) blank_clean: true ONLY if the center cream wool area is clean and free of scribbled gibberish text.\n"
        "3) box: [x0, y0, x1, y1] in 0..1000 normalized coordinates for the largest CLEAN center writing area "
        "(below top margin/ornament and above bottom flowers/lantern).\n"
        'Return ONLY JSON: {"halal_ok": true, "blank_clean": true, "box": [110, 225, 890, 825]}'
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
                    if not res.get("halal_ok", True):
                        return False, default_box
                    clean = bool(res.get("blank_clean", True))
                    bx = res.get("box")
                    if isinstance(bx, list) and len(bx) == 4:
                        x0, y0, x1, y1 = [int(v) for v in bx]
                        if 40 <= x0 < x1 <= 960 and 120 <= y0 < y1 <= 890 and (x1 - x0) >= 480 and (y1 - y0) >= 320:
                            return clean, (x0, y0, x1, y1)
                    return clean, default_box
        except Exception:
            continue
    return True, default_box


def _gemini_generate_image(prompt: str, out_path: Path, W: int, H: int) -> bool:
    keys = _get_gemini_keys()
    if not keys:
        return False

    logged = False
    for model_id in ("gemini-2.5-flash-image", "gemini-3.1-flash-image-preview"):
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
                    if not logged:
                        print(f"   ℹ️ [Islamic Cards Gemini {model_id}]: HTTP {r.status_code}", flush=True)
                        logged = True
                    continue
                parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                for p in parts:
                    inline = p.get("inlineData") or p.get("inline_data")
                    if inline and inline.get("data"):
                        raw_bytes = base64.b64decode(inline["data"])
                        with Image.open(io.BytesIO(raw_bytes)) as im:
                            im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                            im.save(out_path, "JPEG", quality=93)
                        if out_path.exists() and out_path.stat().st_size > 20000:
                            print(f"   ✨ [Islamic Cards Gemini] تم توليد القالب الديني 8K عبر {model_id}", flush=True)
                            return True
            except Exception:
                continue
    return False


def _ai_generate_blank_plush(prompt: str, out_path: Path, W: int, H: int, seed: int = 7742) -> bool:
    neg = (
        "text, words, letters, writing, arabic, calligraphy, numbers, watermark, logo, signature, "
        "scribbles, human, woman, man, face, hands, couple, pink, red heart, cross, church, statue, "
        "cartoon, vector, 2d flat, angled perspective"
    )

    # 1) Gemini Native Image
    if _gemini_generate_image(prompt, out_path, W, H):
        return True

    # 2) Hugging Face ZeroGPU (Z-Image-Turbo ثم FLUX.1-schnell via _open_gradio_client)
    try:
        tok = _check_valid_hf_token()
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
                    res = ex.submit(lambda: client.predict(**call_kw, api_name=ep_name)).result(timeout=85)

                fpath = _extract_gradio_image_path(res, out_path.parent)
                if fpath and os.path.exists(fpath):
                    with Image.open(fpath) as im:
                        im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                        im.save(out_path, "JPEG", quality=93)
                    if out_path.exists() and out_path.stat().st_size > 20000:
                        print(f"   🕌 [Islamic Cards ZeroGPU] تم التوليد عبر {space_id}", flush=True)
                        return True
            except Exception as e:
                print(f"   ⚠️ [Islamic Cards ZeroGPU {space_id}]: {str(e)[:90]}", flush=True)
                continue
    except Exception as e:
        print(f"   ⚠️ [Islamic Cards gradio]: {str(e)[:80]}", flush=True)

    # 3) AI Horde النشط
    try:
        models = _get_active_horde_models()
        print(f"   🕌 [Islamic Cards Horde] التوليد عبر: {models[:3]}", flush=True)
        headers = {
            "apikey": os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000",
            "Client-Agent": "MalakAlMarifa:3.3:islamic-plush",
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
        if r.status_code in (200, 202):
            job_id = r.json().get("id")
            if job_id:
                deadline = time.time() + 145
                while time.time() < deadline:
                    time.sleep(5)
                    chk = requests.get(f"https://aihorde.net/api/v2/generate/check/{job_id}", headers=headers, timeout=15)
                    if chk.status_code != 200:
                        continue
                    cdata = chk.json()
                    if cdata.get("faulted"):
                        break
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
                                if out_path.exists() and out_path.stat().st_size > 20000:
                                    print("   ✅ [Islamic Cards Horde] تم توليد القالب بنجاح!", flush=True)
                                    return True
                        break
    except Exception as e:
        print(f"   ⚠️ [Islamic Cards Horde]: {str(e)[:80]}", flush=True)
    return False


def _soft_clean_center_wool(img: Image.Image, norm_box: tuple[int, int, int, int]) -> Image.Image:
    w, h = img.size
    x0 = max(60, int(norm_box[0] * w / 1000) - 10)
    y0 = max(160, int(norm_box[1] * h / 1000) - 10)
    x1 = min(w - 60, int(norm_box[2] * w / 1000) + 10)
    y1 = min(h - 140, int(norm_box[3] * h / 1000) + 10)

    crop = img.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(30))
    wool_noise = Image.effect_noise(crop.size, 10).convert("RGB")
    clean_patch = ImageChops.soft_light(crop, wool_noise)

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([x0 + 16, y0 + 16, x1 - 16, y1 - 16], radius=34, fill=210)
    mask = mask.filter(ImageFilter.GaussianBlur(22))

    patch_full = img.copy()
    patch_full.paste(clean_patch, (x0, y0))
    return Image.composite(patch_full, img, mask)


def _get_plush_template(kind: str, W: int, H: int) -> tuple[Image.Image | None, tuple[int, int, int, int]]:
    default_box = (110, 225, 890, 825)
    repo_tpl = _REPO_ROOT / "assets" / "templates" / f"{kind}.jpg"
    if repo_tpl.exists() and repo_tpl.stat().st_size > 15000:
        _, bx = _vision_inspect_islamic_card(repo_tpl)
        with Image.open(repo_tpl) as im:
            return ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS), bx

    cache_dir = Path(tempfile.gettempdir()) / "islamic_plush_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{kind}_v3_{W}x{H}.jpg"
    box_cache = cache_dir / f"{kind}_v3_{W}x{H}_box.json"

    with _TPL_LOCK:
        if cached.exists() and cached.stat().st_size > 15000 and box_cache.exists():
            try:
                bx = tuple(json.loads(box_cache.read_text()))
                with Image.open(cached) as im:
                    return im.convert("RGB"), bx
            except Exception:
                pass

        prompts = {
            "notebook_plush_blank": (
                "Top-down flat-lay macro photography of a thick cream-ivory quilted sherpa wool-felt "
                "spiritual Islamic notebook lying flat on a warm carved walnut wooden desk, subtle stitched "
                "golden thread border, delicate white jasmine blossoms and a tiny warm glowing brass Islamic "
                "lantern resting ONLY at the far bottom outer corners of the desk, horizontal debossed wool "
                "grooves across the page, soft peaceful warm Noor morning sunlight beams. The ENTIRE middle "
                "and center of the cream wool page is 100% BLANK, EMPTY, CLEAN, UNWRITTEN cream fleece with "
                "ZERO text, ZERO Arabic, ZERO letters, ZERO numbers, ZERO people, 8k photorealistic macro."
            ),
            "fabric_plush_blank": (
                "Top-down flat-lay macro photography of soft fluffy warm ivory-cream and pale sage-gold "
                "velvety plush fleece texture with subtle delicate golden wool sheen and soft spiritual "
                "morning light vignette around the edges, tiny white jasmine petals on the very outer corners, "
                "completely blank empty texture in the center with ZERO text, ZERO letters, ZERO objects, "
                "8k photorealistic tactile craft."
            ),
        }
        prompt = prompts.get(kind, prompts["fabric_plush_blank"])
        if os.getenv("AI_PLUSH_TEMPLATES", "1") == "1" and _ai_generate_blank_plush(prompt, cached, W, H, seed=7742):
            is_clean, bx = _vision_inspect_islamic_card(cached)
            with Image.open(cached) as im:
                rgb = im.convert("RGB")
            if not is_clean:
                rgb = _soft_clean_center_wool(rgb, bx)
                rgb.save(cached, "JPEG", quality=93)
            box_cache.write_text(json.dumps(list(bx)))
            return rgb, bx
    return None, default_box


def _embed_text_into_wool(
    base_rgb: Image.Image,
    items: list[tuple[int, int, str, object, tuple[int, int, int], str]],
) -> Image.Image:
    """يغرس الخط العربي في ألياف الصوف والقماش بنعومة وبدون تغميق الخلفية."""
    w, h = base_rgb.size
    out = base_rgb.convert("RGBA")

    gray = base_rgb.convert("L")
    wool_hi = ImageEnhance.Contrast(gray).enhance(1.75)
    fiber_bleed = wool_hi.point(lambda p: max(0, min(42, int((p - 180) * 0.50))))

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

        soft_mask = mask.filter(ImageFilter.GaussianBlur(0.38))
        effective_alpha = ImageChops.subtract(soft_mask, fiber_bleed)

        rim = Image.new("RGBA", (w, h), (255, 252, 245, 0))
        rim.putalpha(mask.filter(ImageFilter.GaussianBlur(1.0)).point(lambda p: int(p * 0.30)))

        halo = Image.new("RGBA", (w, h), (48, 36, 22, 0))
        halo.putalpha(mask.filter(ImageFilter.GaussianBlur(1.6)).point(lambda p: int(p * 0.16)))

        ink_layer = Image.new("RGBA", (w, h), (*rgb, 255))
        ink_layer.putalpha(effective_alpha)

        out.alpha_composite(rim, dest=(0, 2))
        out.alpha_composite(halo, dest=(0, 1))
        out.alpha_composite(ink_layer)

    return out


def _make_wood_table_bg(W: int, H: int) -> Image.Image:
    base = Image.new("RGB", (W, H), (78, 54, 38))
    grain = Image.effect_noise((W // 8, H), 20).resize((W, H), Image.Resampling.BILINEAR).convert("RGB")
    mixed = ImageChops.soft_light(base, grain)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(H):
        ratio = y / max(1, H)
        alpha = int(95 * abs(ratio - 0.45) * 1.4)
        od.line([(0, y), (W, y)], fill=(14, 9, 5, min(155, alpha)))
    mixed_rgba = mixed.convert("RGBA")
    mixed_rgba.alpha_composite(overlay)
    return mixed_rgba.filter(ImageFilter.GaussianBlur(radius=2.6)).convert("RGB")


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
        gd.rounded_rectangle([x0 - 7, y0 - 7, x1 + 7, y1 + 7], radius=radius + 7, fill=(218, 172, 58, 115))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=13))
        canvas.alpha_composite(glow)

    pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    if fabric_src is not None:
        crop = fabric_src.crop((max(0, x0), max(0, y0), min(fabric_src.width, x0 + pw), min(fabric_src.height, y0 + ph))).resize((pw, ph))
        tint = Image.new("RGB", (pw, ph), fill_rgb)
        base_rgb = Image.blend(crop, tint, 0.56)
    else:
        tex = Image.effect_noise((pw, ph), 12).convert("RGB")
        base_rgb = ImageChops.soft_light(Image.new("RGB", (pw, ph), fill_rgb), tex)

    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1], radius=radius, fill=255)
    pill.paste(base_rgb.convert("RGBA"), (0, 0), mask)

    bevel = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bevel)
    bd.rounded_rectangle([3, 3, pw - 4, ph - 6], radius=max(4, radius - 3), outline=(255, 255, 250, 195), width=4)
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
    """نمط المخطوطة الصوفية الدينية 8K بدون طمس ملمس الصوف الحقيقي."""
    dummy = Image.new("RGB", (10, 10))
    dd = ImageDraw.Draw(dummy)

    ai_tpl, norm_box = _get_plush_template("notebook_plush_blank", W, H)

    if ai_tpl is not None:
        bg = ai_tpl.convert("RGBA")
        bx0 = max(72, int(norm_box[0] * W / 1000))
        by0 = max(210, int(norm_box[1] * H / 1000))
        bx1 = min(W - 72, int(norm_box[2] * W / 1000))
        by1 = min(H - 150, int(norm_box[3] * H / 1000))
        if bx1 - bx0 < 400:
            bx0, bx1 = 88, W - 88
        if by1 - by0 < 360:
            by0, by1 = 235, H - 190
        safe_x0, safe_x1 = bx0, bx1
        hdr_y0, hdr_y1 = max(125, by0 - 82), max(185, by0 - 20)
        zone_top, zone_bot = by0 + 16, by1
    else:
        page_x0, page_y0, page_x1, page_y1 = 44, 126, W - 44, H - 98
        safe_x0, safe_x1 = page_x0 + 44, page_x1 - 44
        hdr_y0, hdr_y1 = page_y0 + 26, page_y0 + 94
        zone_top, zone_bot = hdr_y1 + 32, page_y1 - 86
        bg = _make_wood_table_bg(W, H).convert("RGBA")
        _draw_3d_plush_pill(bg, [page_x0, page_y0, page_x1, page_y1], radius=34, fill_rgb=(245, 239, 226), active=False)

    text_max_w = safe_x1 - safe_x0
    text_max_h = zone_bot - zone_top

    chosen_font = _load_font(font_path, 28)
    clause_lines_map: list[list[str]] = []
    lh = 56

    for sz in range(42, 26, -2):
        f = _load_font(font_path, sz)
        grouped = [_wrap_text(cl, f, text_max_w, dd) for cl in clauses]
        total_lines = sum(len(g) for g in grouped)
        cand_lh = max(58, int(sz * 1.78))
        if total_lines * cand_lh <= text_max_h:
            chosen_font = f
            clause_lines_map = grouped
            lh = cand_lh
            break
    else:
        clause_lines_map = [_wrap_text(cl, chosen_font, text_max_w, dd) for cl in clauses]

    total_lines = max(1, sum(len(g) for g in clause_lines_map))
    total_text_h = total_lines * lh
    text_start_y = zone_top + max(0, (text_max_h - total_text_h) // 2)

    # شارة الترويسة الدينية الذهبية أعلى الصفحة
    hdr_box = [W // 2 - 208, hdr_y0, W // 2 + 208, hdr_y1]
    _draw_3d_plush_pill(bg, hdr_box, radius=28, fill_rgb=(242, 234, 214), fabric_src=bg.convert("RGB"), active=True)

    clean_title = re.sub(r"[a-zA-Z]", "", ep_title or "").strip()
    meta_items: list[tuple[int, int, str, object, tuple[int, int, int], str]] = [
        (W // 2, (hdr_y0 + hdr_y1) // 2, header_label, _load_font(font_path, 26), (72, 48, 18), "center"),
        (W // 2, H - 64, "مَلَاك المَعْرِفَة", _load_font(font_path, 26), (132, 98, 38), "center"),
    ]
    if clean_title:
        # العنوان فوق الدفتر بلون ذهبي على الخشب الداكن
        top_d = ImageDraw.Draw(bg)
        _draw_rtl(top_d, W // 2, 48, clean_title, _load_font(font_path, 24), (244, 218, 152, 255), align="center", v_center=True)

    bg_with_meta = _embed_text_into_wool(bg.convert("RGB"), meta_items)
    total_chars = max(1, sum(len(cl) for cl in clauses))
    outputs: list[tuple[Path, float]] = []

    for active_idx in range(len(clauses)):
        text_items: list[tuple[int, int, str, object, tuple[int, int, int], str]] = []
        cur_y = text_start_y
        for cl_i, lines in enumerate(clause_lines_map):
            is_act = (cl_i == active_idx) and (len(clauses) > 1)
            ink = (20, 14, 8) if (is_act or len(clauses) == 1) else (98, 86, 70)
            for ln in lines:
                if cur_y + lh <= zone_bot + 10:
                    text_items.append(((safe_x0 + safe_x1) // 2, cur_y + int(lh * 0.46), ln, chosen_font, ink, "center"))
                cur_y += lh

        final_img = _embed_text_into_wool(bg_with_meta, text_items).convert("RGB")
        out_png = base_png_path.with_name(f"{base_png_path.stem}_nb_{active_idx}.png")
        final_img.save(out_png)

        s_dur = max(0.35, c_dur * (max(1, len(clauses[active_idx])) / total_chars)) if len(clauses) > 1 else c_dur
        outputs.append((out_png, s_dur))

    return outputs


def _render_plush_pillows_slides(
    clauses: list[str],
    header_label: str,
    ep_title: str,
    font_path: str,
    base_png_path: Path,
    W: int,
    H: int,
    c_dur: float,
) -> list[tuple[Path, float]]:
    """نمط الوسائد القطنية المخيطة 3D (مستوحى من الصورة 1 للأذكار والدعاء والـ CTA)."""
    ai_bg, _ = _get_plush_template("fabric_plush_blank", W, H)
    if ai_bg is not None:
        fabric_rgb = ai_bg
    else:
        base = Image.new("RGB", (W, H), (232, 234, 224))
        fibers = Image.effect_noise((W, H), 15).convert("RGB")
        fabric_rgb = ImageChops.soft_light(base, fibers).filter(ImageFilter.GaussianBlur(0.8)).convert("RGB")

    bg = fabric_rgb.convert("RGBA")

    # لافتة الترويسة العلوية
    clean_title = re.sub(r"[a-zA-Z]", "", ep_title or "").strip()
    if clean_title:
        _draw_rtl(ImageDraw.Draw(bg), W // 2, 52, clean_title, _load_font(font_path, 24), (104, 78, 34, 245), align="center", v_center=True)

    _draw_3d_plush_pill(bg, [W // 2 - 220, 96, W // 2 + 220, 174], radius=34, fill_rgb=(248, 243, 230), fabric_src=fabric_rgb, active=True)
    bg = _embed_text_into_wool(
        bg.convert("RGB"),
        [
            (W // 2, 135, header_label, _load_font(font_path, 28), (68, 46, 16), "center"),
            (W // 2, H - 56, "مَلَاك المَعْرِفَة", _load_font(font_path, 26), (126, 92, 34), "center"),
        ],
    ).convert("RGBA")

    dummy_d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    n_c = max(1, len(clauses))
    zone_top, zone_bot = 212, H - 115
    gap = 26
    box_h = min(285, (zone_bot - zone_top - gap * (n_c - 1)) // n_c)
    stack_h = n_c * box_h + (n_c - 1) * gap
    start_y = zone_top + (zone_bot - zone_top - stack_h) // 2

    total_chars = max(1, sum(len(cl) for cl in clauses))
    outputs: list[tuple[Path, float]] = []

    for act_i in range(n_c):
        frame = bg.copy()
        text_items: list[tuple[int, int, str, object, tuple[int, int, int], str]] = []

        for ci, cl in enumerate(clauses):
            by0 = start_y + ci * (box_h + gap)
            by1 = by0 + box_h
            is_act = (ci == act_i)
            is_ivory = (ci % 2 == 0)
            fill_c = (250, 246, 236) if is_ivory else (232, 238, 224)

            _draw_3d_plush_pill(
                frame,
                [46, by0, W - 46, by1],
                radius=34,
                fill_rgb=fill_c,
                fabric_src=fabric_rgb,
                active=is_act,
            )

            # شارة رقم عربية صغيرة (١، ٢، ٣)
            badge_x, badge_y = 104, (by0 + by1) // 2
            bd = ImageDraw.Draw(frame)
            b_outline = (202, 156, 52, 245) if is_act else (156, 134, 98, 210)
            bd.ellipse([badge_x - 26, badge_y - 26, badge_x + 26, badge_y + 26], fill=(248, 242, 228, 245), outline=b_outline, width=3)
            num_txt = str(ci + 1).translate(_EASTERN_DIGITS)
            text_items.append((badge_x, badge_y, num_txt, _load_font(font_path, 26), (82, 54, 22), "center"))

            max_tw = W - 220
            chosen_f = _load_font(font_path, 26)
            lines = [cl]
            for sz in range(38, 24, -2):
                f = _load_font(font_path, sz)
                lns = _wrap_text(cl, f, max_tw, dummy_d)
                lh = int(sz * 1.66)
                if len(lns) <= 4 and len(lns) * lh <= box_h - 28:
                    chosen_f, lines = f, lns
                    break
            lh = int(chosen_f.size * 1.66)
            cy_t = (by0 + by1) // 2 - ((len(lines) - 1) * lh) // 2
            ink = (22, 16, 10) if is_act or n_c == 1 else (86, 78, 64)
            for ln in lines:
                text_items.append((W // 2 + 24, cy_t, ln, chosen_f, ink, "center"))
                cy_t += lh

        final_img = _embed_text_into_wool(frame.convert("RGB"), text_items).convert("RGB")
        out_png = base_png_path.with_name(f"{base_png_path.stem}_pill_{act_i}.png")
        final_img.save(out_png)

        s_dur = max(0.35, c_dur * (max(1, len(clauses[act_i])) / total_chars)) if n_c > 1 else c_dur
        outputs.append((out_png, s_dur))

    return outputs


def render_islamic_card_slides(
    chunk_text: str,
    seg_type: str,
    ep_title: str,
    font_path: str,
    base_png_path: Path,
    W: int = 720,
    H: int = 1280,
    c_dur: float = 8.0,
) -> list[tuple[Path, float]]:
    clean = re.sub(r"[*#@_\[\]]", "", chunk_text or "")
    clean = re.sub(r"[a-zA-Z]", "", clean).strip()
    clean = clean.replace("ﷺ", "صلى الله عليه وسلم")
    if not clean:
        return []

    clauses = _split_clauses(clean, max_clauses=4 if seg_type in PILLOW_SEG_TYPES else 3)
    if not clauses:
        return []

    header_label = HEADER_BY_TYPE.get(seg_type, HEADER_BY_TYPE["summary"])

    if seg_type in PILLOW_SEG_TYPES:
        outputs = _render_plush_pillows_slides(clauses, header_label, ep_title, font_path, base_png_path, W, H, c_dur)
    else:
        outputs = _render_notebook_slides(clauses, header_label, ep_title, font_path, base_png_path, W, H, c_dur)

    if outputs:
        diff = c_dur - sum(d for _, d in outputs)
        if abs(diff) > 0.02:
            lp, ld = outputs[-1]
            outputs[-1] = (lp, max(0.3, ld + diff))

    return outputs
