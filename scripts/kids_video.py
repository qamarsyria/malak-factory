#!/usr/bin/env python3
"""
scripts/kids_video.py
=====================
مخرج قصص الأطفال الكرتونية 3D لمشروع «مَلَّاكُ المَعْرِفَةِ» (720×1280 @ 18fps):
- مستقل بالكامل ولا يعتمد على ملفات خارجية مفقودة.
- يولّد مشاهد 3D Pixar Storybook متناسقة لشخصيتي عِمَاد ومَرْيَم عبر:
  (Gemini Image -> ZeroGPU Z-Image-Turbo / FLUX.1-schnell -> AI Horde) مع فحص Gemini Vision.
- يحافظ على كامل حوارات الأطفال ويخصص مشهد دفتر الحكمة لفقرة النهاية (type="moral") بحجم اقتصادي ونقي.
"""

from __future__ import annotations

import base64
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

VIDEO_W = 720
VIDEO_H = 1280
FPS = 18
XFADE_DUR = 0.35
HAS_RAQM = features.check("raqm")

BOY_NAME = os.getenv("KIDS_BOY_NAME", "عِمَاد").strip() or "عِمَاد"
GIRL_NAME = os.getenv("KIDS_GIRL_NAME", "مَرْيَم").strip() or "مَرْيَم"
CHANNEL_BRAND = "مَلَّاكُ المَعْرِفَةِ"

_GRADIO_LOCK = threading.Lock()
_HORDE_LOCK = threading.Lock()
_HORDE_FAILURES = 0

FINAL_ENCODE = [
    "-c:v", "libx264",
    "-preset", "slow",
    "-crf", "27",
    "-maxrate", "220k",
    "-bufsize", "440k",
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


def _ensure_gradio_client_cls():
    with _GRADIO_LOCK:
        try:
            from gradio_client import Client as GradioClient
            return GradioClient
        except ImportError:
            print("   📦 تثبيت gradio_client لتفعيل ZeroGPU...", flush=True)
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "gradio_client"], check=False, timeout=90)
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
                    p = work_dir / f"gr_{random.randint(1000,9999)}.png"
                    p.write_bytes(ir.content)
                    return str(p)
            except Exception:
                return None
        return None
    if isinstance(obj, dict):
        for key in ("path", "image", "url"):
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
        "Rules: Cute modest 3D animated children (boy and/or veiled girl) and friendly animals are ALLOWED. "
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
    raw_keys = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get("GEMINI_API_KEY", "")
    keys = [k.strip() for k in re.split(r"[\s,;]+", raw_keys.strip()) if len(k.strip()) > 20]
    if not keys:
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
                    timeout=50,
                )
                if r.status_code != 200:
                    continue
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
            except Exception:
                continue
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


def _generate_kids_scene_image(scene_en: str, out_jpg: Path, seed: int) -> bool:
    global _HORDE_FAILURES
    neg = (
        "nudity, adult woman, uncovered hair woman, scary, horror, blood, weapon, deformed face, "
        "extra fingers, extra limbs, cross, church, statue, text, words, watermark, 2d flat stick figure, halo, wings"
    )

    if _gemini_kids_image(scene_en, out_jpg) and _vision_check_kids_image(out_jpg, scene_en):
        return True

    try:
        tok = _check_valid_hf_token()
        for space_id in ("Tongyi-MAI/Z-Image-Turbo", "black-forest-labs/FLUX.1-schnell"):
            for try_tok in ([tok, None] if tok else [None]):
                ex = ThreadPoolExecutor(max_workers=1)
                try:
                    client = _open_gradio_client(space_id, try_tok)
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
                    print(f"   ⚠️ [Kids ZeroGPU {space_id}]: {str(e)[:85]}", flush=True)
                    if "quota" not in str(e).lower() and "401" not in str(e):
                        break
                finally:
                    ex.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass

    with _HORDE_LOCK:
        skip_horde = (_HORDE_FAILURES >= 2)
    if skip_horde:
        return False

    try:
        models = _get_kids_horde_models()
        print(f"   🎨 [Kids AI Horde] {models[:2]}", flush=True)
        headers = {
            "apikey": os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000",
            "Client-Agent": "MallakKids:1.2:pixar-clean",
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
            deadline = time.time() + 75
            while time.time() < deadline:
                time.sleep(5)
                chk = requests.get(f"https://aihorde.net/api/v2/generate/check/{job_id}", headers=headers, timeout=15)
                if chk.status_code != 200:
                    continue
                cd = chk.json()
                if cd.get("faulted"):
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
                                return True
                    break
    except Exception:
        pass

    with _HORDE_LOCK:
        _HORDE_FAILURES += 1
    return False


def _make_kids_gradient_fallback(out_jpg: Path):
    img = Image.new("RGB", (VIDEO_W, VIDEO_H))
    d = ImageDraw.Draw(img)
    c1, c2 = (54, 108, 178), (242, 188, 118)
    for y in range(VIDEO_H):
        t = y / VIDEO_H
        d.line([(0, y), (VIDEO_W, y)], fill=tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3)))
    img.save(out_jpg, "JPEG", quality=92)


def _make_kids_overlay_png(text: str, speaker: str, title: str, show_title: bool, font_path: str, out_png: Path):
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    if show_title and title:
        t_font = _load_font(font_path, 30)
        bb = _measure(d, title, t_font)
        tw = min(VIDEO_W - 60, (bb[2] - bb[0]) + 56)
        x0, x1 = (VIDEO_W - tw) // 2, (VIDEO_W + tw) // 2
        d.rounded_rectangle([x0, 34, x1, 96], radius=28, fill=(24, 34, 68, 195), outline=(255, 212, 104, 235), width=3)
        tx = (VIDEO_W - (bb[2] - bb[0])) // 2 - bb[0]
        if HAS_RAQM:
            d.text((tx, 46), _prep_txt(title), font=t_font, fill=(255, 232, 148, 255), direction="rtl")
        else:
            d.text((tx, 46), _prep_txt(title), font=t_font, fill=(255, 232, 148, 255))

    speaker_cfg = {
        "boy": (BOY_NAME, (46, 134, 212, 240)),
        "girl": (GIRL_NAME, (226, 98, 146, 240)),
        "narrator": ("الحكواتي", (212, 148, 38, 240)),
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

        d.rounded_rectangle([30, box_y0, VIDEO_W - 30, box_y1], radius=28, fill=(18, 24, 46, 204), outline=(255, 222, 138, 195), width=2)

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


def _make_kids_moral_storybook_card(moral_text: str, font_path: str, out_png: Path):
    prompt = (
        "Top-down flat-lay macro studio photograph of an open thick puffy cream-ivory quilted wool "
        "children's storybook lying flat on a warm bright sunlit oak table, cute 3D felt stars and "
        "tiny pastel flowers pinned only on the outer borders, warm golden sparkles, completely clean "
        "blank empty cream center area with ZERO text, ZERO letters, 8k 3D Pixar storybook craft."
    )
    bg_jpg = out_png.with_suffix(".bg.jpg")
    if not _generate_kids_scene_image(prompt, bg_jpg, seed=888):
        img = Image.new("RGB", (VIDEO_W, VIDEO_H), (248, 241, 226))
        noise = Image.effect_noise((VIDEO_W // 2, VIDEO_H // 2), 14).resize((VIDEO_W, VIDEO_H), Image.Resampling.BILINEAR).convert("RGB")
        img = ImageChops.soft_light(img, noise)
        d0 = ImageDraw.Draw(img)
        d0.rounded_rectangle([38, 120, VIDEO_W - 38, VIDEO_H - 120], radius=40, outline=(218, 168, 68), width=5)
        img.save(bg_jpg, "JPEG", quality=92)

    with Image.open(bg_jpg) as im:
        card = im.convert("RGBA")

    d = ImageDraw.Draw(card)
    d.rounded_rectangle([64, 210, VIDEO_W - 64, VIDEO_H - 220], radius=36, fill=(252, 247, 236, 218), outline=(212, 164, 62, 215), width=3)

    h_font = _load_font(font_path, 34)
    hdr = "حِكْمَةُ القِصَّةِ اليَوْم"
    hbb = _measure(d, hdr, h_font)
    hx = (VIDEO_W - (hbb[2] - hbb[0])) // 2 - hbb[0]
    if HAS_RAQM:
        d.text((hx, 255), _prep_txt(hdr), font=h_font, fill=(182, 88, 28, 255), direction="rtl")
    else:
        d.text((hx, 255), _prep_txt(hdr), font=h_font, fill=(182, 88, 28, 255))

    # ملاءمة تلقائية لارتفاع النص بين y=340 و y=930 لمنع التداخل
    m_font = _load_font(font_path, 30)
    lines = []
    lh = 52
    for sz in range(38, 22, -2):
        f = _load_font(font_path, sz)
        lns = _wrap_text(moral_text, f, VIDEO_W - 180, d)
        cand_lh = max(48, int(sz * 1.70))
        if len(lns) * cand_lh <= 560:
            m_font, lines, lh = f, lns, cand_lh
            break
    if not lines:
        lines = _wrap_text(moral_text, m_font, VIDEO_W - 180, d)

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

    sig_font = _load_font(font_path, 28)
    sig = f"{BOY_NAME} وَ{GIRL_NAME} — {CHANNEL_BRAND}"
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

    work = Path(tempfile.mkdtemp(prefix="kids_vid_"))
    try:
        def _prep_img(i_s):
            idx, s = i_s
            out_img = work / f"ksc_{idx}.jpg"
            if s.get("type") == "moral":
                return out_img
            seed = 9000 + idx * 107 + random.randint(1, 50)
            if not _generate_kids_scene_image(s["scene_prompt_en"], out_img, seed):
                _make_kids_gradient_fallback(out_img)
            return out_img

        with ThreadPoolExecutor(max_workers=2) as ex:
            scene_imgs = list(ex.map(_prep_img, list(enumerate(segs))))

        clip_slots: list[tuple[Path, float]] = []

        for idx, (s, img_p) in enumerate(zip(segs, scene_imgs)):
            sdur = float(s["duration"])
            is_last = (idx == len(segs) - 1)
            render_dur = sdur + (0.0 if is_last else XFADE_DUR)
            frames = max(18, int(round(render_dur * FPS)) + 8)
            is_moral = (s.get("type") == "moral")

            if is_moral and moral:
                moral_png = work / "moral_card.png"
                _make_kids_moral_storybook_card(moral, font_path, moral_png)
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-loop", "1", "-t", f"{render_dur:.3f}", "-i", str(moral_png),
                        "-vf", f"scale={VIDEO_W}:{VIDEO_H},fps={FPS},format=yuv420p,setsar=1",
                        "-t", f"{render_dur:.3f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-an",
                        str(work / f"kclip_{idx}.mp4"),
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
                z_expr = "min(zoom+0.00045,1.06)" if idx % 2 == 0 else "max(1.06-0.00045*on,1.0)"
                fc = (
                    f"[0:v]scale={VIDEO_W*2}:{VIDEO_H*2}:force_original_aspect_ratio=increase,"
                    f"crop={VIDEO_W*2}:{VIDEO_H*2},"
                    f"zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={VIDEO_W}x{VIDEO_H}:fps={FPS},"
                    f"eq=contrast=1.03:saturation=1.08,format=yuv420p[bg];"
                    f"[1:v]fps={FPS},format=rgba[ov];"
                    f"[bg][ov]overlay=0:0:shortest=1,format=yuv420p,setsar=1[v]"
                )
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-loop", "1", "-t", f"{render_dur:.3f}", "-i", str(img_p),
                        "-loop", "1", "-t", f"{render_dur:.3f}", "-i", str(ov_png),
                        "-filter_complex", fc, "-map", "[v]", "-t", f"{render_dur:.3f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-an",
                        str(work / f"kclip_{idx}.mp4"),
                    ],
                    check=True, capture_output=True, timeout=180,
                )
            clip_slots.append((work / f"kclip_{idx}.mp4", sdur))

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
