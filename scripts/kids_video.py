#!/usr/bin/env python3
"""
scripts/kids_video.py
=====================
مخرج قصص الأطفال الكرتونية 3D لمشروع «مَلَاكُ المَعْرِفَةِ» (720×1280 @ 18fps):
- الشخصيات: زَيْد + نُور + مَيَّار (اللهجة العراقية الموصلية) + الحكواتي.
- يعتمد على التوليد التدرجي (Gemini -> HF Inference -> ZeroGPU FLUX/Z-Image -> AI Horde -> Pixabay -> إعادة تأطير ذكية)
  بحيث لا يظهر أي مشهد فاضي بدون صور أبداً!
- يحرّك المشاهد عبر AI Image-to-Video (عند توفر الحصة) + العمق ثلاثي الأبعاد fx_8d.py المجاني.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, features

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import arabic_reshaper
    from bidi.algorithm import get_display as bidi_display
    HAS_RESHAPER = True
except Exception:
    arabic_reshaper = None
    bidi_display = None
    HAS_RESHAPER = False

try:
    from huggingface_hub import InferenceClient
    HAS_HF_HUB = True
except Exception:
    InferenceClient = None
    HAS_HF_HUB = False

try:
    from fx_8d import render_8d_animation_clip
    HAS_FX8D = True
except Exception:
    render_8d_animation_clip = None
    HAS_FX8D = False

VIDEO_W = 720
VIDEO_H = 1280
FPS = 18
XFADE_DUR = 0.35
HAS_RAQM = features.check("raqm")

BOY_NAME = os.getenv("KIDS_BOY_NAME", "زَيْد").strip() or "زَيْد"
GIRL_NAME = os.getenv("KIDS_GIRL_NAME", "نُور").strip() or "نُور"
MAYAR_NAME = os.getenv("KIDS_MAYAR_NAME", "مَيَّار").strip() or "مَيَّار"
CHANNEL_BRAND = "مَلَاكُ المَعْرِفَةِ"
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "").strip()

_STATE_LOCK = threading.Lock()
_GRADIO_LOCK = threading.Lock()
_RUNTIME = {
    "gemini_img_disabled": False,
    "hf_inf_disabled": False,
    "zerogpu_exhausted": False,
    "horde_failures": 0,
    "horde_disabled": False,
    "bad_spaces": set(),
    "space_clients": {},
}

FINAL_ENCODE = [
    "-c:v", "libx264",
    "-preset", "slow",
    "-crf", "27",
    "-maxrate", "230k",
    "-bufsize", "460k",
    "-profile:v", "high",
    "-level:v", "4.0",
    "-g", str(FPS * 10),
    "-bf", "4",
    "-refs", "4",
    "-aq-mode", "3",
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
]


def _find_amiri_font() -> str:
    candidates = [
        "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/hosny-amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    try:
        r = subprocess.run(["fc-list", ":lang=ar", "--format=%{file}\n"], capture_output=True, text=True)
        files = [f.strip() for f in r.stdout.splitlines() if f.strip()]
        for f in files:
            if "Amiri" in f and "Bold" in f:
                return f
        if files:
            return files[0]
    except Exception:
        pass
    raise RuntimeError("❌ لم يُعثر على أي خط عربي في النظام.")


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


def _ensure_gradio_client():
    with _GRADIO_LOCK:
        try:
            from gradio_client import Client as GradioClient, handle_file
            return GradioClient, handle_file
        except ImportError:
            print("   📦 تثبيت gradio_client...", flush=True)
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "gradio_client"], check=False, timeout=90)
            from gradio_client import Client as GradioClient, handle_file
            return GradioClient, handle_file


def _open_gradio_client(space_id: str, tok: str | None):
    with _STATE_LOCK:
        if space_id in _RUNTIME["bad_spaces"]:
            return None
        cache_k = f"{space_id}:{bool(tok)}"
        if cache_k in _RUNTIME["space_clients"]:
            return _RUNTIME["space_clients"][cache_k]

    GradioClient, _ = _ensure_gradio_client()
    try:
        sig = inspect.signature(GradioClient.__init__).parameters
        if tok and "token" in sig:
            cl = GradioClient(space_id, token=tok)
        elif tok and "hf_token" in sig:
            cl = GradioClient(space_id, hf_token=tok)
        else:
            cl = GradioClient(space_id)
        with _STATE_LOCK:
            _RUNTIME["space_clients"][cache_k] = cl
        return cl
    except Exception as e:
        print(f"   ⚠️ تعذر فتح {space_id}: {str(e)[:80]}", flush=True)
        with _STATE_LOCK:
            _RUNTIME["bad_spaces"].add(space_id)
        return None


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


def _extract_gradio_path(obj, work_dir: Path) -> str | None:
    if not obj:
        return None
    if isinstance(obj, str):
        if os.path.exists(obj):
            return obj
        if obj.startswith(("http://", "https://")):
            try:
                ir = requests.get(obj, timeout=30)
                if ir.status_code == 200 and len(ir.content) > 8000:
                    ext = ".mp4" if ".mp4" in obj.lower() else ".png"
                    p = work_dir / f"gr_{random.randint(1000,9999)}{ext}"
                    p.write_bytes(ir.content)
                    return str(p)
            except Exception:
                return None
        return None
    if isinstance(obj, dict):
        for key in ("path", "video", "image", "url"):
            if key in obj and obj[key]:
                found = _extract_gradio_path(obj[key], work_dir)
                if found:
                    return found
        return None
    if isinstance(obj, (list, tuple)):
        for item in obj:
            found = _extract_gradio_path(item, work_dir)
            if found:
                return found
    return None


def _vision_check_kids_image(img_path: Path, scene_en: str) -> bool:
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
        f"Inspect this vertical 3D Pixar kids storybook illustration.\nIntended scene: '{scene_en[:160]}'\n"
        "Rules: Cute modest 3D animated children (boy and/or veiled girls) and friendly animals are ALLOWED. "
        "REJECT prophets/religious saints/angels, uncovered adult women, scary horror/violence, deformed faces/extra limbs, crosses, statues, or gibberish text.\n"
        'Return ONLY JSON: {"safe": true, "good_3d": true}'
    )
    for model_id in ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"):
        for k in keys[:3]:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={k}",
                    json={
                        "contents": [{"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": b64}}, {"text": prompt}]}],
                        "generationConfig": {"maxOutputTokens": 200, "responseMimeType": "application/json"},
                    },
                    timeout=20,
                )
                if r.status_code == 200:
                    parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                    txt = "\n".join(p.get("text", "") for p in parts if not p.get("thought") and p.get("text"))
                    s, e = txt.find("{"), txt.rfind("}")
                    if s != -1 and e > s:
                        res = json.loads(txt[s : e + 1])
                        return bool(res.get("safe", True)) and bool(res.get("good_3d", True))
            except Exception:
                continue
    return True


def _gemini_kids_image(prompt: str, out_jpg: Path) -> bool:
    if _RUNTIME["gemini_img_disabled"]:
        return False
    raw_keys = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get("GEMINI_API_KEY", "")
    keys = [k.strip() for k in re.split(r"[\s,;]+", raw_keys.strip()) if len(k.strip()) > 20]
    if not keys:
        _RUNTIME["gemini_img_disabled"] = True
        return False
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
                if r.status_code == 200:
                    parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                    for p in parts:
                        inline = p.get("inlineData") or p.get("inline_data")
                        if inline and inline.get("data"):
                            with Image.open(io.BytesIO(base64.b64decode(inline["data"]))) as im:
                                im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (VIDEO_W, VIDEO_H), Image.Resampling.LANCZOS)
                                im.save(out_jpg, "JPEG", quality=93)
                            if out_jpg.exists() and out_jpg.stat().st_size > 20000:
                                print(f"   ✨ [Kids Gemini Image] {model_id}", flush=True)
                                return True
                elif r.status_code in (403, 429):
                    _RUNTIME["gemini_img_disabled"] = True
                    return False
            except Exception:
                continue
    _RUNTIME["gemini_img_disabled"] = True
    return False


def _hf_inference_kids_image(prompt: str, neg: str, out_jpg: Path, tok: str | None) -> bool:
    if not HAS_HF_HUB or not tok or _RUNTIME["hf_inf_disabled"]:
        return False
    for model in ("black-forest-labs/FLUX.1-schnell", "stabilityai/stable-diffusion-xl-base-1.0"):
        try:
            client = InferenceClient(token=tok, timeout=50)
            img = client.text_to_image(
                prompt=prompt,
                negative_prompt=neg,
                model=model,
                width=VIDEO_W,
                height=VIDEO_H,
                num_inference_steps=4 if "schnell" in model else 20,
            )
            if isinstance(img, Image.Image):
                img = ImageOps.fit(img.convert("RGB"), (VIDEO_W, VIDEO_H), Image.Resampling.LANCZOS)
                img.save(out_jpg, "JPEG", quality=93)
                if out_jpg.exists() and out_jpg.stat().st_size > 20000:
                    print(f"   🎨 [Kids HF Inference] {model}", flush=True)
                    return True
        except Exception as e:
            msg = str(e).lower()
            if any(x in msg for x in ("401", "402", "403", "credit", "payment", "subscribe")):
                _RUNTIME["hf_inf_disabled"] = True
                return False
    return False


def _get_kids_horde_models() -> list[str]:
    preferred = ["DreamShaper XL", "AlbedoBase XL (SDXL)", "Juggernaut XL", "RealVisXL"]
    chosen = []
    try:
        r = requests.get("https://aihorde.net/api/v2/status/models?type=image", timeout=15)
        if r.status_code == 200:
            active = {m.get("name"): int(m.get("count", 0)) for m in r.json() if isinstance(m, dict)}
            for p in preferred:
                for name, cnt in active.items():
                    if cnt >= 1 and p.lower() in str(name).lower() and name not in chosen:
                        chosen.append(name)
    except Exception:
        pass
    return chosen or ["DreamShaper XL", "AlbedoBase XL (SDXL)"]


def _generate_kids_scene_image(scene_en: str, out_jpg: Path, seed: int, tok: str | None) -> bool:
    neg = (
        "nudity, adult woman, uncovered hair woman, scary, horror, blood, weapon, deformed face, "
        "extra fingers, extra limbs, cross, church, statue, text, words, watermark, 2d flat stick figure, halo, wings"
    )

    if _gemini_kids_image(scene_en, out_jpg) and _vision_check_kids_image(out_jpg, scene_en):
        return True

    if _hf_inference_kids_image(scene_en, neg, out_jpg, tok) and _vision_check_kids_image(out_jpg, scene_en):
        return True

    # ترتيب FLUX.1-schnell أولاً لأنه يستهلك حصة ZeroGPU أقل بـ 4 مرات من Z-Image-Turbo
    if not _RUNTIME["zerogpu_exhausted"]:
        for space_id in ("black-forest-labs/FLUX.1-schnell", "Tongyi-MAI/Z-Image-Turbo"):
            if _RUNTIME["zerogpu_exhausted"]:
                break
            for try_tok in ([tok, None] if tok else [None]):
                ex = ThreadPoolExecutor(max_workers=1)
                try:
                    client = _open_gradio_client(space_id, try_tok)
                    if not client:
                        break
                    if "Z-Image" in space_id:
                        fn = lambda: client.predict(
                            prompt=scene_en,
                            resolution="720x1280 ( 9:16 )",
                            seed=int(seed),
                            steps=8,
                            shift=3.0,
                            random_seed=False,
                            api_name="/generate",
                        )
                    else:
                        fn = lambda: client.predict(
                            prompt=scene_en,
                            seed=int(seed),
                            randomize_seed=False,
                            width=720,
                            height=1280,
                            num_inference_steps=4,
                            api_name="/infer",
                        )
                    res = ex.submit(fn).result(timeout=70)
                    fpath = _extract_gradio_path(res, out_jpg.parent)
                    if fpath and os.path.exists(fpath):
                        with Image.open(fpath) as im:
                            im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (VIDEO_W, VIDEO_H), Image.Resampling.LANCZOS)
                            im.save(out_jpg, "JPEG", quality=93)
                        if out_jpg.exists() and out_jpg.stat().st_size > 20000 and _vision_check_kids_image(out_jpg, scene_en):
                            print(f"   🎨 [Kids ZeroGPU] {space_id}", flush=True)
                            return True
                except Exception as e:
                    msg = str(e).lower()
                    print(f"   ⚠️ [Kids ZeroGPU {space_id}]: {str(e)[:85]}", flush=True)
                    if any(q in msg for q in ("quota", "runs limit", "exceeded", "rate limit")):
                        if try_tok is None:
                            with _STATE_LOCK:
                                _RUNTIME["zerogpu_exhausted"] = True
                            break
                    elif "401" not in msg:
                        break
                finally:
                    ex.shutdown(wait=False, cancel_futures=True)

    with _STATE_LOCK:
        skip_horde = _RUNTIME["horde_disabled"]
    if skip_horde:
        return False

    try:
        models = _get_kids_horde_models()
        print(f"   🎨 [Kids AI Horde] {models[:2]}", flush=True)
        headers = {
            "apikey": os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000",
            "Client-Agent": "MalakKids:2.0:pixar-clean",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": f"{scene_en} ### {neg}",
            "params": {"sampler_name": "k_euler_a", "width": 576, "height": 1024, "steps": 18, "cfg_scale": 6.5, "seed": str(seed), "n": 1},
            "nsfw": False, "censor_nsfw": True, "r2": True, "models": models,
        }
        r = requests.post("https://aihorde.net/api/v2/generate/async", headers=headers, json=payload, timeout=20)
        if r.status_code in (200, 202) and r.json().get("id"):
            job_id = r.json()["id"]
            deadline = time.time() + 135
            while time.time() < deadline:
                time.sleep(5)
                chk = requests.get(f"https://aihorde.net/api/v2/generate/check/{job_id}", headers=headers, timeout=15)
                if chk.status_code != 200:
                    continue
                cd = chk.json()
                if cd.get("faulted"):
                    break
                if cd.get("wait_time", 0) > 180 and not cd.get("done"):
                    break
                if cd.get("done"):
                    st = requests.get(f"https://aihorde.net/api/v2/generate/status/{job_id}", headers=headers, timeout=20)
                    gens = st.json().get("generations") or [] if st.status_code == 200 else []
                    if gens and gens[0].get("img"):
                        ir = requests.get(gens[0]["img"], timeout=30)
                        if ir.status_code == 200 and len(ir.content) > 8000:
                            tmp = out_jpg.with_suffix(".webp")
                            tmp.write_bytes(ir.content)
                            with Image.open(tmp) as im:
                                im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (VIDEO_W, VIDEO_H), Image.Resampling.LANCZOS)
                                im.save(out_jpg, "JPEG", quality=93)
                            tmp.unlink(missing_ok=True)
                            if out_jpg.exists() and _vision_check_kids_image(out_jpg, scene_en):
                                print("   ✅ [Kids AI Horde] تم توليد الصورة!", flush=True)
                                return True
                    break
    except Exception:
        pass

    with _STATE_LOCK:
        _RUNTIME["horde_failures"] += 1
        if _RUNTIME["horde_failures"] >= 2:
            _RUNTIME["horde_disabled"] = True
    return False


def _try_pixabay_kids_media(query: str, out_base: Path, used_urls: set) -> Path | None:
    if not PIXABAY_KEY:
        return None
    q = query or "green nature meadow flowers"
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": PIXABAY_KEY, "q": q, "safesearch": "true", "per_page": 25, "min_width": 640},
            timeout=15,
        )
        if r.status_code == 200:
            hits = r.json().get("hits", [])
            random.shuffle(hits)
            for h in hits[:6]:
                tags = (h.get("tags") or "").lower()
                if any(b in tags for b in ("woman", "girl", "bikini", "church", "cross", "alcohol", "violence")):
                    continue
                vids = h.get("videos", {})
                u = vids.get("medium", {}).get("url") or vids.get("small", {}).get("url")
                if not u or u in used_urls:
                    continue
                resp = requests.get(u, timeout=45, stream=True)
                if resp.status_code == 200 and "video" in resp.headers.get("Content-Type", "").lower():
                    vpath = out_base.with_suffix(".mp4")
                    with open(vpath, "wb") as f:
                        for ch in resp.iter_content(16384):
                            f.write(ch)
                    if vpath.stat().st_size > 40000:
                        used_urls.add(u)
                        print(f"   🎞️ [Kids Pixabay Video] {q}", flush=True)
                        return vpath
                    vpath.unlink(missing_ok=True)
    except Exception:
        pass

    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={"key": PIXABAY_KEY, "q": q, "image_type": "illustration", "safesearch": "true", "per_page": 20},
            timeout=15,
        )
        if r.status_code == 200:
            hits = r.json().get("hits", [])
            random.shuffle(hits)
            for h in hits[:5]:
                tags = (h.get("tags") or "").lower()
                if any(b in tags for b in ("woman", "girl", "bikini", "church", "cross")):
                    continue
                u = h.get("largeImageURL") or h.get("webformatURL")
                if not u or u in used_urls:
                    continue
                resp = requests.get(u, timeout=25)
                if resp.status_code == 200:
                    jpath = out_base.with_suffix(".jpg")
                    with Image.open(io.BytesIO(resp.content)) as im:
                        im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (VIDEO_W, VIDEO_H), Image.Resampling.LANCZOS)
                        im.save(jpath, "JPEG", quality=92)
                    used_urls.add(u)
                    return jpath
    except Exception:
        pass
    return None


def _try_kids_i2v(img_path: Path, scene_en: str, target_dur: float, work_dir: Path, tok: str | None) -> Path | None:
    if os.getenv("ENABLE_KIDS_I2V", "1") != "1" or _RUNTIME["zerogpu_exhausted"] or not tok:
        return None
    _, handle_file = _ensure_gradio_client()
    out_mp4 = work_dir / "kids_hero_i2v.mp4"
    for space_id in ("Lightricks/LTX-Video", "Wan-AI/Wan2.2-I2V-A14B"):
        if _RUNTIME["zerogpu_exhausted"]:
            break
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            client = _open_gradio_client(space_id, tok)
            if not client:
                continue
            api = client.view_api(return_format="dict") or {}
            named = api.get("named_endpoints", {})
            if not named:
                continue
            ep_name = next((c for c in ("/image_to_video", "/generate", "/infer", "/predict") if c in named), next(iter(named.keys())))
            kwargs = {}
            for p in named[ep_name].get("parameters", []):
                pn = (p.get("parameter_name") or "").lower()
                if pn in ("image", "input_image", "img", "first_frame"):
                    kwargs[p["parameter_name"]] = handle_file(str(img_path))
                elif pn in ("prompt", "positive_prompt"):
                    kwargs[p["parameter_name"]] = f"gentle 3D Pixar animation, cute characters blinking smiling subtly, {scene_en[:140]}"
                elif "parameter_default" in p and p.get("parameter_has_default", True):
                    kwargs[p["parameter_name"]] = p["parameter_default"]

            res = ex.submit(lambda: client.predict(**kwargs, api_name=ep_name)).result(timeout=110)
            vid = _extract_gradio_path(res, work_dir)
            if vid and vid.endswith(".mp4") and Path(vid).stat().st_size > 20000:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-stream_loop", "-1", "-i", vid, "-t", f"{target_dur:.3f}",
                        "-vf", f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,crop={VIDEO_W}:{VIDEO_H},fps={FPS},format=yuv420p,setsar=1",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-an", str(out_mp4),
                    ],
                    check=True, timeout=90,
                )
                if out_mp4.exists() and out_mp4.stat().st_size > 20000:
                    print(f"   🎬 [Kids AI Video] تمت ترقية المشهد إلى فيديو كرتوني متحرك عبر {space_id}", flush=True)
                    return out_mp4
        except Exception as e:
            if "quota" in str(e).lower() or "runs limit" in str(e).lower():
                with _STATE_LOCK:
                    _RUNTIME["zerogpu_exhausted"] = True
                break
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    return None


def _make_kids_storybook_scenery_fallback(out_jpg: Path, seed: int = 1):
    """رسم بيئة قصصية ملونة عند تعذر كل المصادر (لا يُترك المشهد فارغاً أبداً)."""
    img = Image.new("RGB", (VIDEO_W, VIDEO_H))
    d = ImageDraw.Draw(img)
    for y in range(VIDEO_H):
        t = y / VIDEO_H
        r = int(42 + 120 * t)
        g = int(112 + 88 * t)
        b = int(208 - 30 * t)
        d.line([(0, y), (VIDEO_W, y)], fill=(r, g, b))
    # شمس دافئة ونجوم وسحب وهضاب خضراء
    d.ellipse([510, 120, 640, 250], fill=(255, 224, 118))
    rng = random.Random(seed)
    for _ in range(5):
        cx, cy = rng.randint(60, 560), rng.randint(150, 420)
        d.ellipse([cx, cy, cx + 130, cy + 56], fill=(246, 250, 255))
        d.ellipse([cx + 32, cy - 22, cx + 115, cy + 44], fill=(252, 254, 255))
    d.pieslice([-140, 780, 540, 1450], 180, 360, fill=(84, 168, 92))
    d.pieslice([200, 820, 920, 1480], 180, 360, fill=(64, 146, 78))
    d.rectangle([0, 1080, VIDEO_W, VIDEO_H], fill=(56, 134, 70))
    img.save(out_jpg, "JPEG", quality=92)


def _make_kids_overlay_png(text: str, speaker: str, title: str, show_title: bool, font_path: str, out_png: Path):
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    if show_title and title:
        t_font = _load_font(font_path, 30)
        bb = _measure(d, title, t_font)
        tw = min(VIDEO_W - 60, (bb[2] - bb[0]) + 56)
        x0, x1 = (VIDEO_W - tw) // 2, (VIDEO_W + tw) // 2
        d.rounded_rectangle([x0, 34, x1, 96], radius=28, fill=(24, 34, 68, 200), outline=(255, 212, 104, 235), width=3)
        tx = (VIDEO_W - (bb[2] - bb[0])) // 2 - bb[0]
        if HAS_RAQM:
            d.text((tx, 46), _prep_txt(title), font=t_font, fill=(255, 232, 148, 255), direction="rtl")
        else:
            d.text((tx, 46), _prep_txt(title), font=t_font, fill=(255, 232, 148, 255))

    speaker_cfg = {
        "boy": (BOY_NAME, (46, 134, 212, 245)),
        "girl": (GIRL_NAME, (226, 98, 146, 245)),
        "mayar": (MAYAR_NAME, (28, 168, 158, 245)),
        "narrator": ("الحكواتي", (212, 148, 38, 245)),
    }
    sp_label, sp_col = speaker_cfg.get(speaker, speaker_cfg["narrator"])

    clean_text = (text or "").strip()
    if clean_text:
        chosen_f = _load_font(font_path, 26)
        lines: list[str] = []
        lh = 44
        for sz in range(36, 20, -2):
            f = _load_font(font_path, sz)
            lns = _wrap_text(clean_text, f, VIDEO_W - 104, d)
            cand_lh = max(40, int(sz * 1.54))
            if len(lns) <= 5 and len(lns) * cand_lh <= 260:
                chosen_f, lines, lh = f, lns, cand_lh
                break
        if not lines:
            lines = _wrap_text(clean_text, chosen_f, VIDEO_W - 104, d)[:6]

        box_h = len(lines) * lh + 38
        box_y1 = VIDEO_H - 110
        box_y0 = box_y1 - box_h

        d.rounded_rectangle([30, box_y0, VIDEO_W - 30, box_y1], radius=28, fill=(18, 24, 46, 208), outline=(255, 222, 138, 200), width=2)

        sp_font = _load_font(font_path, 24)
        sbb = _measure(d, sp_label, sp_font)
        sw = (sbb[2] - sbb[0]) + 46
        sx0, sx1 = (VIDEO_W - sw) // 2, (VIDEO_W + sw) // 2
        d.rounded_rectangle([sx0, box_y0 - 24, sx1, box_y0 + 18], radius=20, fill=sp_col, outline=(255, 255, 255, 220), width=2)
        stx = (VIDEO_W - (sbb[2] - sbb[0])) // 2 - sbb[0]
        if HAS_RAQM:
            d.text((stx, box_y0 - 20), _prep_txt(sp_label), font=sp_font, fill=(255, 255, 255, 255), direction="rtl")
        else:
            d.text((stx, box_y0 - 20), _prep_txt(sp_label), font=sp_font, fill=(255, 255, 255, 255))

        cy = box_y0 + 26
        for ln in lines:
            bb = _measure(d, ln, chosen_f)
            lx = (VIDEO_W - (bb[2] - bb[0])) // 2 - bb[0]
            if HAS_RAQM:
                d.text((lx, cy), _prep_txt(ln), font=chosen_f, fill=(255, 250, 242, 255), direction="rtl")
            else:
                d.text((lx, cy), _prep_txt(ln), font=chosen_f, fill=(255, 250, 242, 255))
            cy += lh

    wm_font = _load_font(font_path, 24)
    wm = f"{CHANNEL_BRAND} — قصص الأطفال"
    wbb = _measure(d, wm, wm_font)
    wx = (VIDEO_W - (wbb[2] - wbb[0])) // 2 - wbb[0]
    if HAS_RAQM:
        d.text((wx, VIDEO_H - 68), _prep_txt(wm), font=wm_font, fill=(255, 222, 132, 235), direction="rtl")
    else:
        d.text((wx, VIDEO_H - 68), _prep_txt(wm), font=wm_font, fill=(255, 222, 132, 235))

    overlay.save(out_png)


def _make_kids_moral_storybook_card(moral_text: str, font_path: str, out_png: Path, bg_reuse: Path | None = None):
    if bg_reuse and bg_reuse.exists() and bg_reuse.suffix.lower() in (".jpg", ".png"):
        with Image.open(bg_reuse) as im:
            card = ImageOps.fit(im.convert("RGB"), (VIDEO_W, VIDEO_H), Image.Resampling.LANCZOS).convert("RGBA")
    else:
        img = Image.new("RGB", (VIDEO_W, VIDEO_H), (248, 241, 226))
        noise = Image.effect_noise((VIDEO_W // 2, VIDEO_H // 2), 14).resize((VIDEO_W, VIDEO_H), Image.Resampling.BILINEAR).convert("RGB")
        img = ImageChops.soft_light(img, noise)
        d0 = ImageDraw.Draw(img)
        d0.rounded_rectangle([38, 120, VIDEO_W - 38, VIDEO_H - 120], radius=40, outline=(218, 168, 68), width=5)
        card = img.convert("RGBA")

    d = ImageDraw.Draw(card)
    d.rounded_rectangle([54, 210, VIDEO_W - 54, VIDEO_H - 220], radius=36, fill=(252, 247, 236, 228), outline=(212, 164, 62, 225), width=4)

    h_font = _load_font(font_path, 34)
    hdr = "حِكْمَةُ القِصَّةِ اليَوْم"
    hbb = _measure(d, hdr, h_font)
    hx = (VIDEO_W - (hbb[2] - hbb[0])) // 2 - hbb[0]
    if HAS_RAQM:
        d.text((hx, 255), _prep_txt(hdr), font=h_font, fill=(182, 88, 28, 255), direction="rtl")
    else:
        d.text((hx, 255), _prep_txt(hdr), font=h_font, fill=(182, 88, 28, 255))

    m_font = _load_font(font_path, 30)
    lines = []
    lh = 52
    for sz in range(38, 22, -2):
        f = _load_font(font_path, sz)
        lns = _wrap_text(moral_text, f, VIDEO_W - 168, d)
        cand_lh = max(48, int(sz * 1.70))
        if len(lns) * cand_lh <= 560:
            m_font, lines, lh = f, lns, cand_lh
            break
    if not lines:
        lines = _wrap_text(moral_text, m_font, VIDEO_W - 168, d)

    total_h = len(lines) * lh
    cy = 340 + max(0, (580 - total_h) // 2)
    for ln in lines:
        bb = _measure(d, ln, m_font)
        lx = (VIDEO_W - (bb[2] - bb[0])) // 2 - bb[0]
        if HAS_RAQM:
            d.text((lx, cy), _prep_txt(ln), font=m_font, fill=(32, 24, 18, 255), direction="rtl")
        else:
            d.text((lx, cy), _prep_txt(ln), font=m_font, fill=(32, 24, 18, 255))
        cy += lh

    sig_font = _load_font(font_path, 26)
    sig = f"{BOY_NAME} وَ{GIRL_NAME} وَ{MAYAR_NAME} — {CHANNEL_BRAND}"
    sbb = _measure(d, sig, sig_font)
    sx = (VIDEO_W - (sbb[2] - sbb[0])) // 2 - sbb[0]
    if HAS_RAQM:
        d.text((sx, VIDEO_H - 300), _prep_txt(sig), font=sig_font, fill=(154, 92, 28, 255), direction="rtl")
    else:
        d.text((sx, VIDEO_H - 300), _prep_txt(sig), font=sig_font, fill=(154, 92, 28, 255))

    card.convert("RGB").save(out_png)


def _get_audio_dur(path: Path, fallback: float) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        v = float(r.stdout.strip() or 0)
        return v if v > 0 else fallback
    except Exception:
        return fallback


def build_kids_video(ep_json_path: str, out_mp4: str) -> bool:
    ep_p = Path(ep_json_path).resolve()
    ep = json.loads(ep_p.read_text(encoding="utf-8"))
    audio_p = Path(ep["audio_path"]).resolve()
    out_p = Path(out_mp4).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    title = ep.get("title", "قصة للأطفال")
    moral = ep.get("moral_card", "")
    segs = ep.get("segments", [])
    total_dur = _get_audio_dur(audio_p, float(ep.get("total_duration") or 30.0))
    font_path = _find_amiri_font()
    hf_tok = _check_valid_hf_token()
    used_pixabay_urls: set = set()

    work = Path(tempfile.mkdtemp(prefix="kids_vid_"))
    try:
        scene_media: list[Path | None] = [None] * len(segs)
        generated_imgs: list[tuple[int, str, Path]] = []

        # توليد متسلسل ذكي لحماية حصة ZeroGPU ومنع فشل الطوابير المتزامنة
        for idx, s in enumerate(segs):
            if s.get("type") == "moral":
                continue
            out_img = work / f"ksc_{idx}.jpg"
            seed = 9000 + idx * 107 + random.randint(1, 50)
            who = s.get("who_visible", "all")

            if _generate_kids_scene_image(s["scene_prompt_en"], out_img, seed, hf_tok):
                scene_media[idx] = out_img
                generated_imgs.append((idx, who, out_img))
            else:
                pb_res = _try_pixabay_kids_media(s.get("fallback_query", ""), work / f"pb_{idx}", used_pixabay_urls)
                if pb_res:
                    scene_media[idx] = pb_res

        # ضمان عدم بقاء أي مشهد فارغاً: إعادة استخدام أقرب مشهد شخصيات مولّد بتأطير مختلف قبل أي رسم احتياطي
        for idx, s in enumerate(segs):
            if s.get("type") == "moral" or scene_media[idx] is not None:
                continue
            who = s.get("who_visible", "all")
            matched = next((p for _, w, p in generated_imgs if w == who), None)
            if not matched and generated_imgs:
                matched = generated_imgs[idx % len(generated_imgs)][2]
            if matched:
                reframed = work / f"reframe_{idx}.jpg"
                with Image.open(matched) as im:
                    w0, h0 = im.size
                    crop_box = (int(w0 * 0.06), int(h0 * 0.04), int(w0 * 0.94), int(h0 * 0.92)) if idx % 2 == 0 else (0, 0, w0, int(h0 * 0.94))
                    im = ImageOps.fit(im.crop(crop_box), (VIDEO_W, VIDEO_H), Image.Resampling.LANCZOS)
                    im.save(reframed, "JPEG", quality=92)
                scene_media[idx] = reframed
                print(f"   🔄 المشهد {idx}: إعادة تأطير متحركة من صورة القصة بدلاً من فراغ", flush=True)
            else:
                fb_jpg = work / f"kfb_{idx}.jpg"
                _make_kids_storybook_scenery_fallback(fb_jpg, seed=idx + 17)
                scene_media[idx] = fb_jpg

        # محاولة ترقية أول مشهد رئيسي إلى فيديو AI متحرك فعلاً عند توفر الرصيد
        hero_video: Path | None = None
        if len(segs) > 0 and scene_media[0] and scene_media[0].suffix.lower() in (".jpg", ".png"):
            hero_video = _try_kids_i2v(
                scene_media[0],
                segs[0].get("scene_prompt_en", ""),
                float(segs[0]["duration"]) + XFADE_DUR,
                work,
                hf_tok,
            )

        clip_slots: list[tuple[Path, float]] = []
        moves = ("orbit_in", "orbit_out", "orbit_left", "orbit_right")

        for idx, s in enumerate(segs):
            sdur = float(s["duration"])
            is_last = (idx == len(segs) - 1)
            render_dur = sdur + (0.0 if is_last else XFADE_DUR)
            is_moral = (s.get("type") == "moral")
            clip_mp4 = work / f"kclip_{idx}.mp4"

            if is_moral and moral:
                moral_png = work / "moral_card.png"
                reuse_bg = generated_imgs[0][2] if generated_imgs else None
                _make_kids_moral_storybook_card(moral, font_path, moral_png, bg_reuse=reuse_bg)
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-loop", "1", "-t", f"{render_dur:.3f}", "-i", str(moral_png),
                        "-vf", f"scale={VIDEO_W}:{VIDEO_H},fps={FPS},format=yuv420p,setsar=1",
                        "-t", f"{render_dur:.3f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-an",
                        str(clip_mp4),
                    ],
                    check=True, capture_output=True, timeout=180,
                )
            else:
                ov_png = work / f"kov_{idx}.png"
                _make_kids_overlay_png(
                    s["text"],
                    speaker=s.get("speaker", "narrator"),
                    title=title,
                    show_title=(idx == 0),
                    font_path=font_path,
                    out_png=ov_png,
                )

                media_p = scene_media[idx]
                bg_mp4 = work / f"kbg_{idx}.mp4"

                if idx == 0 and hero_video is not None:
                    bg_mp4 = hero_video
                elif media_p and media_p.suffix.lower() == ".mp4":
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-loglevel", "error",
                            "-stream_loop", "-1", "-i", str(media_p), "-t", f"{render_dur:.3f}",
                            "-vf", f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,crop={VIDEO_W}:{VIDEO_H},fps={FPS},format=yuv420p,setsar=1",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-an", str(bg_mp4),
                        ],
                        check=True, capture_output=True, timeout=120,
                    )
                elif HAS_FX8D and render_8d_animation_clip is not None and media_p is not None:
                    render_8d_animation_clip(
                        img_path=media_p,
                        out_mp4=bg_mp4,
                        duration=render_dur,
                        W=VIDEO_W,
                        H=VIDEO_H,
                        fps=FPS,
                        mood="warm_romance",
                        particles="none",
                        move=moves[idx % len(moves)],
                        is_notebook=False,
                        seed=500 + idx * 19,
                    )
                else:
                    frames = max(18, int(round(render_dur * FPS)) + 8)
                    z_expr = "min(zoom+0.0005,1.07)" if idx % 2 == 0 else "max(1.07-0.0005*on,1.0)"
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-loglevel", "error",
                            "-loop", "1", "-t", f"{render_dur:.3f}", "-i", str(media_p),
                            "-vf", f"scale={VIDEO_W*2}:{VIDEO_H*2}:force_original_aspect_ratio=increase,crop={VIDEO_W*2}:{VIDEO_H*2},zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={VIDEO_W}x{VIDEO_H}:fps={FPS},format=yuv420p,setsar=1",
                            "-t", f"{render_dur:.3f}",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-an", str(bg_mp4),
                        ],
                        check=True, capture_output=True, timeout=150,
                    )

                fc = (
                    f"[0:v]fps={FPS}[bg];"
                    f"[1:v]fps={FPS},format=rgba[ov];"
                    f"[bg][ov]overlay=0:0:shortest=1,format=yuv420p,setsar=1[v]"
                )
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-i", str(bg_mp4),
                        "-loop", "1", "-t", f"{render_dur:.3f}", "-i", str(ov_png),
                        "-filter_complex", fc, "-map", "[v]", "-t", f"{render_dur:.3f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-an",
                        str(clip_mp4),
                    ],
                    check=True, capture_output=True, timeout=180,
                )
            clip_slots.append((clip_mp4, sdur))

        vid_only = work / "kids_vid_only.mp4"
        if len(clip_slots) == 1:
            shutil.copy2(clip_slots[0][0], vid_only)
        else:
            inputs, filters, last_tag, offset = [], [], "[0:v]", 0.0
            for p, _ in clip_slots:
                inputs.extend(["-i", str(p)])
            for k in range(len(clip_slots) - 1):
                offset += float(clip_slots[k][1])
                out_tag = "[vout]" if k == len(clip_slots) - 2 else f"[vx{k}]"
                filters.append(f"{last_tag}[{k+1}:v]xfade=transition=dissolve:duration={XFADE_DUR:.3f}:offset={offset:.3f}{out_tag}")
                last_tag = out_tag
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    *inputs, "-filter_complex", ";".join(filters), "-map", "[vout]",
                    "-t", f"{total_dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-an",
                    str(vid_only),
                ],
                check=True, capture_output=True, timeout=240,
            )

        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(vid_only), "-i", str(audio_p),
                "-map", "0:v", "-map", "1:a", "-t", f"{total_dur:.3f}",
                *FINAL_ENCODE, "-c:a", "aac", "-ac", "1", "-b:a", "48k", "-ar", "24000", "-shortest",
                str(out_p),
            ],
            check=True, capture_output=True, timeout=240,
        )
        mb = out_p.stat().st_size / (1024 * 1024)
        print(f"✅ اكتمل فيديو قصص الأطفال 3D: {out_p.name} ({mb:.2f} MB)", flush=True)
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    ep_file = sys.argv[1] if len(sys.argv) > 1 else str(_REPO_ROOT / "out" / "kids" / "latest_kids_ep.txt")
    if Path(ep_file).name == "latest_kids_ep.txt":
        ep_file = Path(ep_file).read_text(encoding="utf-8").strip()
    out_file = sys.argv[2] if len(sys.argv) > 2 else str(Path(ep_file).with_suffix(".mp4"))
    build_kids_video(ep_file, out_file)
