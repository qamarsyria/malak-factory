#!/usr/bin/env python3
"""
scripts/islamic_cards.py
=========================
مولد الدفتر الصوفي القطني الإسلامي 8K لمشروع «مَلَّاكُ المَعْرِفَةِ»:
- يولّد دفتراً صوفياً قطنياً بطابع ديني روحاني عبر Gemini Image / Z-Image-Turbo / FLUX.1-schnell / AI Horde.
- يدعم تعدد الصفحات (Pagination) تلقائياً للأحاديث والأدعية والفتاوى الطويلة بدون بتر أي كلمة.
- يضبط موضع الترويسة أسفل دبوس الهلال الذهبي ويعرض الترجمة الإنكليزية (text_en) في شريط سفلي أنيق.
"""

from __future__ import annotations

import base64
import inspect
import io
import json
import os
import random
import re
import subprocess
import sys
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
_TPL_LOCK = threading.Lock()
_GRADIO_LOCK = threading.Lock()
_HORDE_FAILED_RUN = False

HEADER_BY_TYPE = {
    "cta":     "الدَّالُّ عَلَى الخَيْرِ كَفَاعِلِهِ",
    "outro":   "شَارِكِ الخَيْرَ وَانْشُرِ العِلْمَ",
    "hadith":  "مِنْ هَدْيِ النُّبُوَّةِ الشَّرِيفَةِ",
    "fiqh":    "فَائِدَةٌ شَرْعِيَّةٌ مُيَسَّرَةٌ",
    "adhkar":  "ذِكْرٌ وَطُمَأْنِينَةٌ",
    "dua":     "دُعَاءٌ وَمُنَاجَاةٌ",
    "summary": "خُلَاصَةٌ وَعِبْرَةٌ",
}

CHANNEL_BRAND = "مَلَّاكُ المَعْرِفَةِ"


def _prep_txt(text: str) -> str:
    if HAS_RAQM:
        return text
    return bidi_display(arabic_reshaper.reshape(text))


def _load_font(font_path: str, size: int):
    if HAS_RAQM:
        return ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.RAQM)
    return ImageFont.truetype(font_path, size)


def _find_latin_font(fallback_font: str) -> str:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return fallback_font


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


def _wrap_latin(text: str, font, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    lines: list[str] = []
    for para in (text or "").splitlines():
        words = para.split()
        if not words:
            continue
        cur = ""
        for w in words:
            trial = f"{cur} {w}".strip() if cur else w
            bb = draw.textbbox((0, 0), trial, font=font)
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
    cleaned = re.sub(
        r"م[\u064B-\u065F]*ل[\u064B-\u065F]*ا[\u064B-\u065F]*ك[\u064B-\u065F]*"
        r"\s+ال[\u064B-\u065F]*م[\u064B-\u065F]*ع[\u064B-\u065F]*ر[\u064B-\u065F]*ف[\u064B-\u065F]*ة[\u064B-\u065F]*[.!؟،\s]*$",
        "",
        text.strip(),
    ).strip()
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
        if mid > 0:
            return [" ".join(words[:mid]), " ".join(words[mid:])]

    return merged


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


def _ensure_gradio_client_cls():
    with _GRADIO_LOCK:
        try:
            from gradio_client import Client as GradioClient
            return GradioClient
        except ImportError:
            print("   📦 تثبيت gradio_client لتفعيل ZeroGPU...", flush=True)
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "gradio_client"],
                check=False,
                timeout=90,
            )
            from gradio_client import Client as GradioClient
            return GradioClient


def _open_gradio_client(space_id: str, tok: str | None):
    GradioClient = _ensure_gradio_client_cls()
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
    default_box = (115, 290, 895, 780)
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
        "Inspect this vertical 9:16 Islamic plush wool notebook background.\n"
        "1) halal_ok: false if there are humans, uncovered women, couples, red romantic hearts, crosses, or statues.\n"
        "2) blank_clean: true ONLY if the center cream wool area is clean and free of scribbled gibberish text.\n"
        "3) box: [x0, y0, x1, y1] in 0..1000 normalized coordinates for the largest CLEAN center writing area "
        "(right of spiral rings, strictly BELOW any top golden crescent/flower pin, above bottom lantern/flowers).\n"
        'Return ONLY JSON: {"halal_ok": true, "blank_clean": true, "box": [115, 290, 895, 780]}'
    )
    for model_id in ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"):
        for k in keys[:3]:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={k}",
                    json={
                        "contents": [{"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": b64}}, {"text": prompt}]}],
                        "generationConfig": {"maxOutputTokens": 256, "responseMimeType": "application/json"},
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
                            if 40 <= x0 < x1 <= 960 and 150 <= y0 < y1 <= 860 and (x1 - x0) >= 480 and (y1 - y0) >= 300:
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
    for model_id in ("gemini-3.1-flash-image-preview", "gemini-3.1-flash-image"):
        for k in keys[:2]:
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
                    timeout=45,
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
                            print(f"   ✨ [Islamic Cards Gemini] تم توليد الدفتر 8K عبر {model_id}", flush=True)
                            return True
            except Exception:
                continue
    return False


def _call_z_image_turbo(prompt: str, out_path: Path, W: int, H: int, seed: int, tok: str | None) -> bool:
    for try_tok in ([tok, None] if tok else [None]):
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            client = _open_gradio_client("Tongyi-MAI/Z-Image-Turbo", try_tok)
            fut = ex.submit(
                lambda: client.predict(
                    prompt=prompt,
                    resolution="720x1280 ( 9:16 )",
                    seed=int(seed),
                    steps=8,
                    shift=3.0,
                    random_seed=False,
                    api_name="/generate",
                )
            )
            res = fut.result(timeout=75)
            fpath = _extract_gradio_image_path(res, out_path.parent)
            if fpath and os.path.exists(fpath):
                with Image.open(fpath) as im:
                    im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                    im.save(out_path, "JPEG", quality=93)
                if out_path.exists() and out_path.stat().st_size > 20000:
                    print("   🕌 [Islamic Cards ZeroGPU] تم توليد الدفتر الصوفي 8K عبر Z-Image-Turbo", flush=True)
                    return True
        except Exception as e:
            msg = str(e)
            print(f"   ⚠️ [Islamic Cards Z-Image-Turbo]: {msg[:100]}", flush=True)
            if "quota" not in msg.lower() and "401" not in msg:
                break
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    return False


def _call_flux_schnell(prompt: str, out_path: Path, W: int, H: int, seed: int, tok: str | None) -> bool:
    for try_tok in ([tok, None] if tok else [None]):
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            client = _open_gradio_client("black-forest-labs/FLUX.1-schnell", try_tok)
            fut = ex.submit(
                lambda: client.predict(
                    prompt=prompt,
                    seed=int(seed),
                    randomize_seed=False,
                    width=720,
                    height=1280,
                    num_inference_steps=4,
                    api_name="/infer",
                )
            )
            res = fut.result(timeout=75)
            fpath = _extract_gradio_image_path(res, out_path.parent)
            if fpath and os.path.exists(fpath):
                with Image.open(fpath) as im:
                    im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
                    im.save(out_path, "JPEG", quality=93)
                if out_path.exists() and out_path.stat().st_size > 20000:
                    print("   🕌 [Islamic Cards ZeroGPU] تم توليد الدفتر الصوفي 8K عبر FLUX.1-schnell", flush=True)
                    return True
        except Exception as e:
            print(f"   ⚠️ [Islamic Cards FLUX.1-schnell]: {str(e)[:100]}", flush=True)
            if "quota" not in str(e).lower() and "401" not in str(e):
                break
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    return False


def _ai_generate_blank_plush(prompt: str, out_path: Path, W: int, H: int, seed: int = 7742) -> bool:
    global _HORDE_FAILED_RUN
    neg = (
        "text, words, letters, writing, arabic, calligraphy, numbers, watermark, logo, signature, "
        "scribbles, human, woman, man, face, hands, couple, pink, red heart, cross, church, statue, "
        "cartoon, vector, 2d flat, angled perspective"
    )

    if _gemini_generate_image(prompt, out_path, W, H):
        return True

    tok = _check_valid_hf_token()
    if _call_z_image_turbo(prompt, out_path, W, H, seed, tok):
        return True
    if _call_flux_schnell(prompt, out_path, W, H, seed, tok):
        return True

    if _HORDE_FAILED_RUN:
        return False

    try:
        models = _get_active_horde_models()
        print(f"   🕌 [Islamic Cards Horde] التوليد عبر: {models[:3]}", flush=True)
        headers = {
            "apikey": os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000",
            "Client-Agent": "MalakAlMarifa:3.6:islamic-plush",
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
            print(f"   ⚠️ [Islamic Cards Horde HTTP {r.status_code}]", flush=True)
            _HORDE_FAILED_RUN = True
            return False
        job_id = r.json().get("id")
        if not job_id:
            _HORDE_FAILED_RUN = True
            return False

        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(5)
            chk = requests.get(f"https://aihorde.net/api/v2/generate/check/{job_id}", headers=headers, timeout=15)
            if chk.status_code != 200:
                continue
            cdata = chk.json()
            if cdata.get("faulted"):
                _HORDE_FAILED_RUN = True
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
                        if out_path.exists() and out_path.stat().st_size > 20000:
                            print("   ✅ [Islamic Cards Horde] تم توليد الدفتر 8K بنجاح!", flush=True)
                            return True
                _HORDE_FAILED_RUN = True
                return False
        _HORDE_FAILED_RUN = True
    except Exception as e:
        print(f"   ⚠️ [Islamic Cards Horde]: {str(e)[:80]}", flush=True)
        _HORDE_FAILED_RUN = True
    return False


def _soft_clean_center_wool(img: Image.Image, norm_box: tuple[int, int, int, int]) -> Image.Image:
    w, h = img.size
    x0 = max(60, int(norm_box[0] * w / 1000) - 10)
    y0 = max(180, int(norm_box[1] * h / 1000) - 10)
    x1 = min(w - 60, int(norm_box[2] * w / 1000) + 10)
    y1 = min(h - 160, int(norm_box[3] * h / 1000) + 10)

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
    default_box = (115, 290, 895, 780)
    repo_tpl = _REPO_ROOT / "assets" / "templates" / f"{kind}.jpg"
    if repo_tpl.exists() and repo_tpl.stat().st_size > 15000:
        _, bx = _vision_inspect_islamic_card(repo_tpl)
        with Image.open(repo_tpl) as im:
            return ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS), bx

    cache_dir = Path(tempfile.gettempdir()) / "islamic_plush_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{kind}_v5_{W}x{H}.jpg"
    box_cache = cache_dir / f"{kind}_v5_{W}x{H}_box.json"

    with _TPL_LOCK:
        if cached.exists() and cached.stat().st_size > 15000 and box_cache.exists():
            try:
                bx = tuple(json.loads(box_cache.read_text()))
                with Image.open(cached) as im:
                    return im.convert("RGB"), bx
            except Exception:
                pass

        prompt = (
            "Top-down flat-lay macro studio photograph of a thick puffy cream-ivory quilted sherpa wool-felt "
            "spiral-bound Islamic notebook lying flat on a warm carved walnut wooden desk, silver spiral binding "
            "rings along the far left edge, soft fuzzy wool edges, horizontal debossed quilted wool ridges across "
            "the page, a tiny cute 3D golden felt crescent and pearl flower pinned at the top center margin, "
            "delicate white jasmine blossoms and a small warm glowing brass Islamic lantern resting ONLY at the "
            "far bottom corners of the page, soft peaceful golden Noor sunlight beams. "
            "The ENTIRE middle of the cream wool page is 100% BLANK, EMPTY, CLEAN, UNWRITTEN cream fleece "
            "with ZERO text, ZERO Arabic, ZERO English, ZERO letters, ZERO numbers, ZERO people, 8k macro photo."
        )
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


def warmup_islamic_notebook_template(W: int = 720, H: int = 1280):
    try:
        print("🧶 تجهيز قالب الدفتر الصوفي الإسلامي 8K مسبقاً...", flush=True)
        _get_plush_template("notebook_plush_blank", W, H)
    except Exception as e:
        print(f"   ⚠️ تعذر التجهيز المسبق للدفتر: {str(e)[:80]}", flush=True)


def _embed_text_into_wool(
    base_rgb: Image.Image,
    items: list[tuple[int, int, str, object, tuple[int, int, int], str]],
) -> Image.Image:
    w, h = base_rgb.size
    out = base_rgb.convert("RGBA")

    gray = base_rgb.convert("L")
    wool_hi = ImageEnhance.Contrast(gray).enhance(1.70)
    fiber_bleed = wool_hi.point(lambda p: max(0, min(32, int((p - 185) * 0.42))))

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

        soft_mask = mask.filter(ImageFilter.GaussianBlur(0.32))
        effective_alpha = ImageChops.subtract(soft_mask, fiber_bleed)

        rim = Image.new("RGBA", (w, h), (255, 252, 245, 0))
        rim.putalpha(mask.filter(ImageFilter.GaussianBlur(0.9)).point(lambda p: int(p * 0.28)))

        halo = Image.new("RGBA", (w, h), (48, 36, 22, 0))
        halo.putalpha(mask.filter(ImageFilter.GaussianBlur(1.4)).point(lambda p: int(p * 0.16)))

        ink_layer = Image.new("RGBA", (w, h), (*rgb, 255))
        ink_layer.putalpha(effective_alpha)

        out.alpha_composite(rim, dest=(0, 2))
        out.alpha_composite(halo, dest=(0, 1))
        out.alpha_composite(ink_layer)

    return out


def _draw_crisp_overlay_label(
    canvas: Image.Image,
    x: int,
    y: int,
    text: str,
    font,
    fill_rgb: tuple[int, int, int],
    is_rtl: bool = True,
):
    if not text:
        return
    fg = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fg)
    pt = _prep_txt(text) if is_rtl else text
    if is_rtl and HAS_RAQM:
        bb = fd.textbbox((0, 0), pt, font=font, direction="rtl")
        dx = x - (bb[2] - bb[0]) // 2 - bb[0]
        dy = y - (bb[1] + bb[3]) // 2
        fd.text((dx, dy), pt, font=font, fill=(*fill_rgb, 255), direction="rtl")
    else:
        bb = fd.textbbox((0, 0), pt, font=font)
        dx = x - (bb[2] - bb[0]) // 2 - bb[0]
        dy = y - (bb[1] + bb[3]) // 2
        fd.text((dx, dy), pt, font=font, fill=(*fill_rgb, 255))

    alpha = fg.getchannel("A")
    zero = Image.new("L", canvas.size, 0)
    outline_a = alpha.filter(ImageFilter.MaxFilter(3))
    sh_a = outline_a.filter(ImageFilter.GaussianBlur(1.5))
    canvas.alpha_composite(Image.merge("RGBA", (zero, zero, zero, sh_a)), dest=(0, 2))
    canvas.alpha_composite(Image.merge("RGBA", (zero, zero, zero, outline_a)))
    canvas.alpha_composite(fg)


def _draw_en_subtitle_band(
    canvas: Image.Image,
    text_en: str,
    latin_font_path: str,
    W: int,
    H: int,
):
    if not (text_en or "").strip():
        return
    draw = ImageDraw.Draw(canvas)
    max_w = W - 80

    chosen_f = ImageFont.truetype(latin_font_path, 20)
    en_lines: list[str] = []
    for sz in (22, 20, 18, 17):
        f = ImageFont.truetype(latin_font_path, sz)
        wrapped = _wrap_latin(text_en.strip(), f, max_w, draw)
        if len(wrapped) <= 3 or sz == 17:
            chosen_f, en_lines = f, wrapped[:4]
            break

    if not en_lines:
        return

    lh = 28
    pad_y = 14
    box_h = len(en_lines) * lh + pad_y * 2
    y1 = H - 54
    y0 = y1 - box_h
    x0, x1 = 28, W - 28

    band = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    bd.rounded_rectangle(
        [x0, y0, x1, y1],
        radius=18,
        fill=(18, 12, 8, 212),
        outline=(212, 175, 55, 130),
        width=2,
    )
    canvas.alpha_composite(band)

    td = ImageDraw.Draw(canvas)
    cur_y = y0 + pad_y
    for ln in en_lines:
        bb = td.textbbox((0, 0), ln, font=chosen_f)
        tw = bb[2] - bb[0]
        dx = (W - tw) // 2 - bb[0]
        td.text((dx, cur_y), ln, font=chosen_f, fill=(250, 245, 232, 255))
        cur_y += lh


def _make_rich_wool_notebook_fallback(W: int, H: int, groove_ys: list[int], lh: int) -> Image.Image:
    desk = Image.new("RGB", (W, H), (62, 42, 28))
    grain = Image.effect_noise((W // 10, H // 2), 18).resize((W, H), Image.Resampling.BILINEAR).convert("RGB")
    desk = ImageChops.soft_light(desk, grain).filter(ImageFilter.GaussianBlur(3.4)).convert("RGBA")

    px0, py0, px1, py1 = 36, 98, W - 36, H - 175
    pw, ph = px1 - px0, py1 - py0

    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([px0 + 6, py0 + 12, px1 + 8, py1 + 14], radius=38, fill=(16, 8, 4, 155))
    desk.alpha_composite(sh.filter(ImageFilter.GaussianBlur(16)))

    page = Image.new("RGB", (pw, ph), (245, 238, 224))
    n1 = Image.effect_noise((pw // 4, ph // 4), 12).resize((pw, ph), Image.Resampling.BICUBIC).convert("RGB")
    n2 = Image.effect_noise((pw, ph), 9).convert("RGB")
    page = ImageChops.soft_light(ImageChops.soft_light(page, n1), n2).convert("RGBA")

    gs = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    gh = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    gsd, ghd = ImageDraw.Draw(gs), ImageDraw.Draw(gh)
    all_g = sorted(set(groove_ys))
    if all_g:
        step = max(54, lh)
        g = all_g[0] - step
        while g > py0 + 40:
            all_g.append(g)
            g -= step
        g = max(all_g) + step
        while g < py1 - 40:
            all_g.append(g)
            g += step
    for gy_abs in sorted(set(all_g)):
        gy = gy_abs - py0
        if 30 < gy < ph - 26:
            gsd.line([(24, gy), (pw - 24, gy)], fill=(154, 130, 104, 65), width=4)
            ghd.line([(24, gy + 5), (pw - 24, gy + 5)], fill=(255, 253, 248, 95), width=4)
    page.alpha_composite(gs.filter(ImageFilter.GaussianBlur(2.2)))
    page.alpha_composite(gh.filter(ImageFilter.GaussianBlur(2.8)))

    rng = random.Random(303)
    pmask = Image.new("L", (pw, ph), 0)
    pmd = ImageDraw.Draw(pmask)
    pmd.rounded_rectangle([12, 12, pw - 13, ph - 13], radius=36, fill=255)
    for x in range(44, pw - 44, 4):
        for ye, sg in ((12, -1), (ph - 13, 1)):
            pmd.line([(x, ye), (x + rng.randint(-3, 3), ye + sg * rng.randint(3, 8))], fill=rng.randint(165, 242), width=2)
    for y in range(44, ph - 44, 4):
        for xe, sg in ((12, -1), (pw - 13, 1)):
            pmd.line([(xe, y), (xe + sg * rng.randint(3, 8), y + rng.randint(-3, 3))], fill=rng.randint(165, 242), width=2)
    pmask = pmask.filter(ImageFilter.GaussianBlur(0.6))

    out_page = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    out_page.paste(page, (0, 0), pmask)
    desk.alpha_composite(out_page, dest=(px0, py0))

    sp = ImageDraw.Draw(desk)
    for ry in range(py0 + 68, py1 - 56, 56):
        sp.ellipse([px0 + 14, ry - 6, px0 + 28, ry + 6], fill=(82, 64, 52, 180))
        sp.rounded_rectangle([px0 - 16, ry - 5, px0 + 22, ry + 5], radius=5, fill=(198, 192, 186, 255), outline=(126, 118, 110, 220), width=1)
    return desk.convert("RGB")


def _render_notebook_slides(
    clauses: list[str],
    header_label: str,
    ep_title: str,
    font_path: str,
    base_png_path: Path,
    W: int,
    H: int,
    c_dur: float,
    text_en: str = "",
    latin_font_path: str | None = None,
) -> list[tuple[Path, float]]:
    dummy = Image.new("RGB", (10, 10))
    dd = ImageDraw.Draw(dummy)
    lfont_path = latin_font_path or _find_latin_font(font_path)

    ai_tpl, norm_box = _get_plush_template("notebook_plush_blank", W, H)

    en_reserve = 205 if (text_en or "").strip() else 145

    if ai_tpl is not None:
        bx0 = max(72, int(norm_box[0] * W / 1000))
        by0 = max(210, int(norm_box[1] * H / 1000))
        bx1 = min(W - 64, int(norm_box[2] * W / 1000))
        by1 = min(H - en_reserve, int(norm_box[3] * H / 1000))
        if bx1 - bx0 < 400:
            bx0, bx1 = 92, W - 76
        if by1 - by0 < 340:
            by0, by1 = 230, H - en_reserve
        safe_x0, safe_x1 = bx0, bx1
        hdr_y = max(int(H * 0.315), by0 + 22)
        zone_top, zone_bot = hdr_y + 52, by1
    else:
        safe_x0, safe_x1 = 94, W - 74
        hdr_y = 162
        zone_top, zone_bot = 224, H - max(205, en_reserve + 15)

    text_max_w = safe_x1 - safe_x0
    text_max_h = max(240, zone_bot - zone_top)
    center_x = (safe_x0 + safe_x1) // 2

    chosen_font = _load_font(font_path, 30)
    clause_lines_map: list[list[str]] = []
    lh = 54

    for sz in range(38, 28, -2):
        f = _load_font(font_path, sz)
        grouped = [_wrap_text(cl, f, text_max_w, dd) for cl in clauses]
        total_lines = sum(len(g) for g in grouped)
        cand_lh = max(52, int(sz * 1.70))
        if total_lines * cand_lh <= text_max_h:
            chosen_font, clause_lines_map, lh = f, grouped, cand_lh
            break
    else:
        chosen_font = _load_font(font_path, 30)
        clause_lines_map = [_wrap_text(cl, chosen_font, text_max_w, dd) for cl in clauses]
        lh = max(52, int(30 * 1.70))

    max_lines_per_page = max(3, text_max_h // lh)

    flat_items: list[tuple[int, str]] = []
    for cl_i, lines in enumerate(clause_lines_map):
        for ln in lines:
            flat_items.append((cl_i, ln))

    total_lines_all = max(1, len(flat_items))
    groove_ys = [zone_top + i * lh + int(lh * 0.88) for i in range(min(total_lines_all, max_lines_per_page))]

    if ai_tpl is not None:
        bg = ai_tpl.convert("RGBA")
    else:
        bg = _make_rich_wool_notebook_fallback(W, H, groove_ys, lh).convert("RGBA")

    dec = ImageDraw.Draw(bg)
    dec.line([(center_x - 135, hdr_y + 26), (center_x + 135, hdr_y + 26)], fill=(184, 144, 62, 175), width=2)
    dec.ellipse([center_x - 4, hdr_y + 22, center_x + 4, hdr_y + 30], fill=(184, 144, 62, 220))

    meta_items: list[tuple[int, int, str, object, tuple[int, int, int], str]] = [
        (center_x, hdr_y, header_label, _load_font(font_path, 28), (118, 76, 24), "center"),
    ]
    bg_with_meta = _embed_text_into_wool(bg.convert("RGB"), meta_items)

    clean_title = re.sub(r"[a-zA-Z]", "", ep_title or "").strip()
    if clean_title:
        t_bar = Image.new("RGBA", bg_with_meta.size, (0, 0, 0, 0))
        ImageDraw.Draw(t_bar).rounded_rectangle(
            [W // 2 - 240, 20, W // 2 + 240, 70],
            radius=16,
            fill=(18, 12, 8, 150),
            outline=(212, 175, 55, 85),
            width=1,
        )
        bg_with_meta.alpha_composite(t_bar)
        _draw_crisp_overlay_label(bg_with_meta, W // 2, 45, clean_title, _load_font(font_path, 25), (255, 226, 145), is_rtl=True)

    _draw_crisp_overlay_label(bg_with_meta, W // 2, H - 26, CHANNEL_BRAND, _load_font(font_path, 25), (255, 222, 130), is_rtl=True)

    outputs: list[tuple[Path, float]] = []

    if total_lines_all <= max_lines_per_page:
        total_text_h = total_lines_all * lh
        text_start_y = zone_top + max(0, (text_max_h - total_text_h) // 2)
        total_chars = max(1, sum(len(cl) for cl in clauses))

        for active_idx in range(len(clauses)):
            text_items: list[tuple[int, int, str, object, tuple[int, int, int], str]] = []
            cur_y = text_start_y
            for cl_i, lines in enumerate(clause_lines_map):
                is_act = (cl_i == active_idx) and (len(clauses) > 1)
                ink = (18, 12, 6) if (is_act or len(clauses) == 1) else (98, 86, 70)
                for ln in lines:
                    text_items.append((center_x, cur_y + int(lh * 0.46), ln, chosen_font, ink, "center"))
                    cur_y += lh

            final_img = _embed_text_into_wool(bg_with_meta.convert("RGB"), text_items)
            _draw_en_subtitle_band(final_img, text_en, lfont_path, W, H)
            out_png = base_png_path.with_name(f"{base_png_path.stem}_nb_{active_idx}.png")
            final_img.convert("RGB").save(out_png)

            s_dur = max(0.35, c_dur * (max(1, len(clauses[active_idx])) / total_chars)) if len(clauses) > 1 else c_dur
            outputs.append((out_png, s_dur))
    else:
        pages = [flat_items[i : i + max_lines_per_page] for i in range(0, len(flat_items), max_lines_per_page)]
        total_chars_pages = max(1, sum(len(ln) for _, ln in flat_items))

        for p_idx, page_lines in enumerate(pages):
            page_h = len(page_lines) * lh
            text_start_y = zone_top + max(0, (text_max_h - page_h) // 2)
            text_items = []
            cur_y = text_start_y
            for _, ln in page_lines:
                text_items.append((center_x, cur_y + int(lh * 0.46), ln, chosen_font, (18, 12, 6), "center"))
                cur_y += lh

            final_img = _embed_text_into_wool(bg_with_meta.convert("RGB"), text_items)
            _draw_en_subtitle_band(final_img, text_en, lfont_path, W, H)
            out_png = base_png_path.with_name(f"{base_png_path.stem}_nb_{p_idx}.png")
            final_img.convert("RGB").save(out_png)

            p_chars = max(1, sum(len(ln) for _, ln in page_lines))
            s_dur = max(0.45, c_dur * (p_chars / total_chars_pages))
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
    text_en: str = "",
    latin_font_path: str | None = None,
) -> list[tuple[Path, float]]:
    clean = re.sub(r"[*#@_\[\]]", "", chunk_text or "")
    clean = re.sub(r"[a-zA-Z]", "", clean).strip()
    clean = clean.replace("ﷺ", "صلى الله عليه وسلم")
    if not clean:
        return []

    clauses = _split_clauses(clean, max_clauses=3)
    if not clauses:
        return []

    header_label = HEADER_BY_TYPE.get(seg_type, HEADER_BY_TYPE["summary"])
    outputs = _render_notebook_slides(
        clauses=clauses,
        header_label=header_label,
        ep_title=ep_title,
        font_path=font_path,
        base_png_path=base_png_path,
        W=W,
        H=H,
        c_dur=c_dur,
        text_en=text_en,
        latin_font_path=latin_font_path,
    )

    if outputs:
        diff = c_dur - sum(d for _, d in outputs)
        if abs(diff) > 0.02:
            lp, ld = outputs[-1]
            outputs[-1] = (lp, max(0.3, ld + diff))

    return outputs
