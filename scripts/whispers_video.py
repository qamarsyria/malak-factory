#!/usr/bin/env python3
"""
scripts/whispers_video.py
=========================
المخرج السينمائي 8D لمشروع «همسات ملك» (1080×1920 @ 30fps):
- يولّد لوحات رومانسية أو روحانية عبر (Gemini Native Image -> ZeroGPU Z-Image-Turbo/FLUX -> AI Horde -> Pixabay Film)
  مع فحص Gemini Vision.
- يحوّل كل اللوحات والدفتر الصوفي القطني 8K إلى أنيميشن 8D مجاني على CPU عبر fx_8d.py
  (3 طبقات عمق + ملء الفراغات + كاميرا مدارية 6-DOF + Rack Focus + أشعة نور + جزيئات بوكيه 3D)
  بدون الحاجة لـ Cloudflare أو حصص فيديو خارجية.
"""

from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, features
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import arabic_reshaper
    from bidi.algorithm import get_display as bidi_display
    HAS_RESHAPER = True
except Exception:
    arabic_reshaper = None
    bidi_display = None
    HAS_RESHAPER = False

from romantic_cards import (
    render_romantic_notebook_slides,
    _find_amiri_font,
    _check_valid_hf_token,
    _get_active_horde_models,
    _open_gradio_client,
)
from fx_8d import render_8d_animation_clip

VIDEO_W = 1080
VIDEO_H = 1920
FPS = 30
XFADE_DUR = 0.72

PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()
HAS_RAQM = features.check("raqm")

FINAL_WHISPER_ENCODE = [
    "-c:v", "libx264",
    "-preset", "slow",
    "-crf", "25",
    "-maxrate", "680k",
    "-bufsize", "1360k",
    "-profile:v", "high",
    "-level:v", "4.0",
    "-g", str(FPS * 4),
    "-bf", "3",
    "-aq-mode", "3",
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
]


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


def _composite_halo(canvas: Image.Image, fg_layer: Image.Image, outline_px: int = 3):
    alpha = fg_layer.getchannel("A")
    zero = Image.new("L", canvas.size, 0)
    out_a = alpha.filter(ImageFilter.MaxFilter(outline_px * 2 + 1))
    sh_a = out_a.filter(ImageFilter.GaussianBlur(4))
    canvas.alpha_composite(Image.merge("RGBA", (zero, zero, zero, sh_a)), dest=(0, 4))
    canvas.alpha_composite(Image.merge("RGBA", (zero, zero, zero, out_a)))
    canvas.alpha_composite(fg_layer)


def _make_whisper_overlay_png(
    text: str,
    title: str,
    show_title: bool,
    category: str,
    font_path: str,
    out_png: Path,
):
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    scrim = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)

    scrim_rgb = (18, 20, 36) if category == "prayer_niqab" else (36, 10, 22)

    if show_title:
        for y in range(85, 305):
            a = int(108 * (1.0 - abs(y - 185) / 120.0))
            sd.line([(0, y), (VIDEO_W, y)], fill=(*scrim_rgb, max(0, a)))

    start_y = int(VIDEO_H * 0.54)
    for y in range(start_y, VIDEO_H):
        ratio = (y - start_y) / max(1, VIDEO_H - start_y)
        alpha = int(178 * (ratio ** 1.28))
        sd.line([(0, y), (VIDEO_W, y)], fill=(*scrim_rgb, alpha))
    overlay.alpha_composite(scrim)

    fg_body = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    fg_meta = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(fg_body)
    md = ImageDraw.Draw(fg_meta)

    if show_title and title:
        t_font = _load_font(font_path, 50)
        clean_t = re.sub(r"م[\u064B-\u065F]*ل[\u064B-\u065F]*ك[\u064B-\u065F]*", "ملك", title)
        pt = _prep_txt(clean_t)
        bb = _measure(md, clean_t, t_font)
        tx = (VIDEO_W - (bb[2] - bb[0])) // 2 - bb[0]
        title_col = (255, 228, 166, 248) if category == "prayer_niqab" else (255, 222, 184, 248)
        if HAS_RAQM:
            md.text((tx, 150), pt, font=t_font, fill=title_col, direction="rtl")
        else:
            md.text((tx, 150), pt, font=t_font, fill=title_col)

        line_y = 228
        md.line([(VIDEO_W // 2 - 140, line_y), (VIDEO_W // 2 + 140, line_y)], fill=(246, 206, 148, 165), width=2)
        md.ellipse([VIDEO_W // 2 - 5, line_y - 5, VIDEO_W // 2 + 5, line_y + 5], fill=(255, 224, 170, 220))

    clean_text = (text or "").strip()
    if clean_text:
        chosen_font = _load_font(font_path, 46)
        lines: list[str] = []
        lh = 76
        for sz in range(60, 40, -2):
            f = _load_font(font_path, sz)
            lns = _wrap_text(clean_text, f, VIDEO_W - 140, bd)
            cand_lh = max(78, int(sz * 1.62))
            if len(lns) <= 4 and len(lns) * cand_lh <= 360:
                chosen_font, lines, lh = f, lns, cand_lh
                break
        if not lines:
            lines = _wrap_text(clean_text, chosen_font, VIDEO_W - 140, bd)[:4]

        block_h = len(lines) * lh
        cur_y = 1605 - block_h
        for ln in lines:
            pt = _prep_txt(ln)
            bb = _measure(bd, ln, chosen_font)
            lx = (VIDEO_W - (bb[2] - bb[0])) // 2 - bb[0]
            if HAS_RAQM:
                bd.text((lx, cur_y), pt, font=chosen_font, fill=(255, 248, 240, 255), direction="rtl")
            else:
                bd.text((lx, cur_y), pt, font=chosen_font, fill=(255, 248, 240, 255))
            cur_y += lh

    wm_font = _load_font(font_path, 34)
    wm = "همسات ملك"
    bb = _measure(md, wm, wm_font)
    wx = (VIDEO_W - (bb[2] - bb[0])) // 2 - bb[0]
    wm_col = (242, 208, 138, 232) if category == "prayer_niqab" else (246, 198, 168, 232)
    if HAS_RAQM:
        md.text((wx, 1735), _prep_txt(wm), font=wm_font, fill=wm_col, direction="rtl")
    else:
        md.text((wx, 1735), _prep_txt(wm), font=wm_font, fill=wm_col)

    _composite_halo(overlay, fg_body, outline_px=3)
    _composite_halo(overlay, fg_meta, outline_px=2)
    overlay.save(out_png)


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


def _vision_check_whisper_image(img_path: Path, category: str, scene_en: str) -> bool:
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

    if category == "prayer_niqab":
        policy = (
            "Religious prayer scene: ONLY modest veiled Muslim woman in loose dark niqab/hijab OR "
            "mosque/lantern/moonlight/white roses. REJECT uncovered women, couples kissing, "
            "crosses, statues, cheap 2D clipart, or text."
        )
    else:
        policy = (
            "Romantic modest cinematic scene matching the prompt. Tasteful loving couple in modest "
            "attire, roses, rainy window, starlight or enchanted forest are ALLOWED. REJECT nudity, "
            "explicit kissing, deformed faces/extra limbs, crosses, cheap 2D clipart, or gibberish text."
        )

    prompt = (
        f"Inspect this vertical illustration.\nIntended scene: '{scene_en[:160]}'\nPolicy: {policy}\n"
        'Return ONLY JSON: {"safe": true, "high_craft": true, "reason": "ok"}'
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
                    return bool(res.get("safe", True)) and bool(res.get("high_craft", True))
        except Exception:
            continue
    return True


def _gemini_scene_image(full_prompt: str, out_jpg: Path) -> bool:
    raw_keys = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get("GEMINI_API_KEY", "")
    keys = [k.strip() for k in re.split(r"[\s,;]+", raw_keys.strip()) if len(k.strip()) > 20]
    if not keys:
        return False

    for model_id in ("gemini-2.5-flash-image", "gemini-3.1-flash-image-preview"):
        for k in keys[:3]:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={k}",
                    json={
                        "contents": [{"parts": [{"text": full_prompt}]}],
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
                        raw_bytes = base64.b64decode(inline["data"])
                        with Image.open(io.BytesIO(raw_bytes)) as im:
                            im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (VIDEO_W, VIDEO_H), Image.Resampling.LANCZOS)
                            im.save(out_jpg, "JPEG", quality=93)
                        if out_jpg.exists() and out_jpg.stat().st_size > 25000:
                            print(f"   ✨ [Scene Gemini Image] تم الرسم عبر {model_id}", flush=True)
                            return True
            except Exception:
                continue
    return False


def _generate_romantic_scene_image(scene_en: str, category: str, out_jpg: Path, seed: int) -> bool:
    if category == "prayer_niqab":
        style = (
            "hyper-realistic peaceful twilight cinematic photography, glowing golden mosque lantern, "
            "crescent moonlight and white jasmine blossoms, volumetric soft light beams, modest Islamic "
            "dignity, 8k vertical 9:16 portrait, no text, no watermark"
        )
        neg = (
            "nudity, uncovered woman, kissing, couple, cross, church, statue, cartoon, vector, "
            "clipart, text, watermark, deformed hands, deformed faces"
        )
    else:
        style = (
            "luminous Makoto Shinkai and photorealistic 8k romantic cinema fusion, dreamy warm rose-gold "
            "candlelight hour volumetric glow, dreamy glowing bokeh, soft petals or gentle rain, "
            "tasteful modest tender atmosphere, vertical 9:16 portrait composition, no text, no watermark"
        )
        neg = (
            "nudity, explicit, bikini, lingerie, cheap 2d cartoon, stick figure, clipart, ugly faces, "
            "deformed hands, extra fingers, cross, horror, skull, text, words, watermark"
        )

    full_prompt = f"{scene_en}, {style}"

    if _gemini_scene_image(full_prompt, out_jpg) and _vision_check_whisper_image(out_jpg, category, scene_en):
        return True

    try:
        tok = _check_valid_hf_token()
        # Z-Image-Turbo بالاستدعاء الدقيق ثم FLUX.1-schnell
        for space_id in ("Tongyi-MAI/Z-Image-Turbo", "black-forest-labs/FLUX.1-schnell"):
            for try_tok in ([tok, None] if tok else [None]):
                try:
                    client = _open_gradio_client(space_id, try_tok)
                    if "Z-Image" in space_id:
                        call_fn = lambda: client.predict(
                            prompt=full_prompt,
                            resolution="720x1280 ( 9:16 )",
                            seed=int(seed),
                            steps=8,
                            shift=3.0,
                            random_seed=False,
                            api_name="/generate",
                        )
                    else:
                        call_fn = lambda: client.predict(
                            prompt=full_prompt,
                            seed=int(seed),
                            randomize_seed=False,
                            width=720,
                            height=1280,
                            num_inference_steps=4,
                            api_name="/infer",
                        )

                    with ThreadPoolExecutor(max_workers=1) as ex:
                        res = ex.submit(call_fn).result(timeout=85)

                    fpath = _extract_gradio_path(res, out_jpg.parent)
                    if fpath and os.path.exists(fpath):
                        with Image.open(fpath) as im:
                            im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (VIDEO_W, VIDEO_H), Image.Resampling.LANCZOS)
                            im.save(out_jpg, "JPEG", quality=93)
                        if out_jpg.exists() and out_jpg.stat().st_size > 25000 and _vision_check_whisper_image(out_jpg, category, scene_en):
                            print(f"   ✅ [Scene ZeroGPU] تم الرسم عبر {space_id}", flush=True)
                            return True
                except Exception as e:
                    print(f"   ⚠️ [Scene ZeroGPU {space_id}]: {str(e)[:90]}", flush=True)
                    if "quota" not in str(e).lower() and "401" not in str(e):
                        break
    except Exception:
        pass

    try:
        models = _get_active_horde_models()
        print(f"   🎨 [Scene AI Horde] رسم '{scene_en[:45]}...' عبر {models[:3]}", flush=True)
        headers = {
            "apikey": os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000",
            "Client-Agent": "HamsatMalak:4.0:8d-scene",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": f"{full_prompt} ### {neg}",
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
                        tmp = out_jpg.with_suffix(".webp")
                        tmp.write_bytes(ir.content)
                        with Image.open(tmp) as im:
                            im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (VIDEO_W, VIDEO_H), Image.Resampling.LANCZOS)
                            im.save(out_jpg, "JPEG", quality=93)
                        tmp.unlink(missing_ok=True)
                        if out_jpg.exists() and _vision_check_whisper_image(out_jpg, category, scene_en):
                            print("   ✅ [Scene Horde] تم توليد اللوحة الرومانسية بنجاح!", flush=True)
                            return True
                return False
        print("   ⏱️ [Scene Horde] تجاوز المهلة 145ث", flush=True)
    except Exception as e:
        print(f"   ⚠️ [Scene Horde error]: {str(e)[:85]}", flush=True)
    return False


def _fetch_pixabay_film(category: str, work_dir: Path, used_urls: set[str]) -> Path | None:
    if not PIXABAY_KEY:
        return None
    queries = (
        ["islamic lantern night", "crescent moon clouds", "white roses dew"]
        if category == "prayer_niqab"
        else ["pink roses petals bokeh", "rain window candlelight", "couple sunset beach silhouette", "autumn forest sunlight"]
    )
    random.shuffle(queries)
    for q in queries:
        try:
            r = requests.get(
                "https://pixabay.com/api/videos/",
                params={"key": PIXABAY_KEY, "q": q, "video_type": "film", "per_page": 25, "safesearch": "true"},
                timeout=18,
            )
            if r.status_code != 200:
                continue
            hits = r.json().get("hits", [])
            random.shuffle(hits)
            for h in hits:
                tl = (h.get("tags") or "").lower()
                if any(b in tl for b in ("bikini", "swim", "horror", "skull", "cross", "church")):
                    continue
                u = (h.get("videos") or {}).get("medium", {}).get("url")
                if u and u not in used_urls:
                    out = work_dir / f"px_{h['id']}.mp4"
                    with open(out, "wb") as f:
                        for ch in requests.get(u, timeout=45, stream=True).iter_content(16384):
                            f.write(ch)
                    if out.exists() and out.stat().st_size > 40000:
                        used_urls.add(u)
                        return out
        except Exception:
            continue
    return None


def _make_romantic_gradient_bg(out_jpg: Path, category: str):
    img = Image.new("RGB", (VIDEO_W, VIDEO_H))
    d = ImageDraw.Draw(img)
    c1, c2 = ((18, 26, 52), (68, 44, 72)) if category == "prayer_niqab" else ((48, 16, 38), (138, 56, 82))
    for y in range(VIDEO_H):
        t = y / VIDEO_H
        d.line([(0, y), (VIDEO_W, y)], fill=tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3)))
    img.save(out_jpg, "JPEG", quality=92)


def _extract_segment_durations(segs: list[dict], total_dur: float) -> list[float]:
    if not segs:
        return [total_dur]
    if all("start_sec" in s and "end_sec" in s for s in segs):
        durs = []
        for i, s in enumerate(segs):
            if i < len(segs) - 1:
                d = max(0.8, float(segs[i + 1]["start_sec"]) - float(s["start_sec"]))
            else:
                d = max(0.8, total_dur - float(s["start_sec"]))
            durs.append(d)
    elif all("dry_duration" in s for s in segs):
        raw = [float(s["dry_duration"]) + float(s.get("pause_after_ms", 420)) / 1000.0 for s in segs]
        scale = total_dur / max(0.1, sum(raw))
        durs = [max(0.8, round(x * scale, 3)) for x in raw]
    else:
        weights = [max(10, len(s.get("display_text") or s.get("text") or "")) for s in segs]
        tot_w = sum(weights)
        durs = [max(0.8, round(total_dur * w / tot_w, 3)) for w in weights]

    if len(durs) > 1:
        durs[-1] = max(0.8, round(total_dur - sum(durs[:-1]), 3))
    else:
        durs[0] = round(total_dur, 3)
    return durs


def build_whisper_video(audio_path: str, json_path: str, out_mp4: str) -> bool:
    audio_p = Path(audio_path).resolve()
    ep_p = Path(json_path).resolve()
    out_p = Path(out_mp4).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    ep = json.loads(ep_p.read_text(encoding="utf-8"))
    meta = None
    for cand in [
        audio_p.with_name(f"{audio_p.stem}_meta.json"),
        audio_p.with_name(f"{audio_p.stem.replace('_malak', '')}_meta.json"),
    ]:
        if cand.exists():
            meta = json.loads(cand.read_text(encoding="utf-8"))
            break

    category = ep.get("scene_category") or ep.get("category") or "modest_love"
    if category == "romantic":
        category = "modest_love"
    elif category == "dua":
        category = "prayer_niqab"

    title = (ep.get("title") or "همسة لروحها").strip()
    segs = (meta or {}).get("segments") or ep.get("segments") or []

    ep_segs = ep.get("segments") or []
    for idx, s in enumerate(segs):
        if "scene_prompt_en" not in s and idx < len(ep_segs):
            s["scene_prompt_en"] = ep_segs[idx].get("scene_prompt_en", "")

    dur_out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_p)],
        capture_output=True, text=True,
    ).stdout.strip()
    total_dur = max(6.0, float(dur_out or 20.0))

    font_path = _find_amiri_font()
    seg_durs = _extract_segment_durations(segs, total_dur)
    notebook_mode = os.environ.get("WHISPERS_NOTEBOOK_MODE", "climax").strip().lower()
    mood_8d = "spiritual_noor" if category == "prayer_niqab" else "warm_romance"
    moves_8d = ("orbit_in", "orbit_left", "orbit_out", "orbit_right")

    print(f"⏱️ مدة الصوت: {total_dur:.2f} ث | المحور: {category} | الوضع: {notebook_mode} | المحرك: 8D مجاني", flush=True)

    work = Path(tempfile.mkdtemp(prefix="whisper_8d_"))
    try:
        if notebook_mode == "full_notebook" or len(segs) <= 1:
            card_slides = render_romantic_notebook_slides(
                segs, title, category, work / "nb.png", W=VIDEO_W, H=VIDEO_H, total_dur=total_dur
            )
            _render_8d_slides_with_fade(card_slides, total_dur, work / "vid_only.mp4", mood_8d)
        else:
            scene_segs = segs[:-1]
            scene_durs = seg_durs[:-1]
            climax_dur = seg_durs[-1]
            used_urls: set[str] = set()

            def _prep_scene(i_s):
                idx, s = i_s
                sc_en = s.get("scene_prompt_en") or (
                    "glowing golden mosque lantern in twilight courtyard with white roses"
                    if category == "prayer_niqab"
                    else "warm golden sunset over blooming rose garden and rainy cozy window"
                )
                out_img = work / f"sc_{idx}.jpg"
                seed = 5000 + idx * 911 + random.randint(1, 500)
                if _generate_romantic_scene_image(sc_en, category, out_img, seed):
                    return out_img
                px_vid = _fetch_pixabay_film(category, work, used_urls)
                if px_vid:
                    return px_vid
                _make_romantic_gradient_bg(out_img, category)
                return out_img

            with ThreadPoolExecutor(max_workers=2) as ex:
                media_list = list(ex.map(_prep_scene, list(enumerate(scene_segs))))

            clip_slots: list[tuple[Path, float]] = []
            for idx, (s, sdur, media_p) in enumerate(zip(scene_segs, scene_durs, media_list)):
                render_dur = sdur + XFADE_DUR
                ov_png = work / f"ov_{idx}.png"
                _make_whisper_overlay_png(
                    s.get("display_text") or s.get("text") or "",
                    title=title,
                    show_title=(idx == 0),
                    category=category,
                    font_path=font_path,
                    out_png=ov_png,
                )
                seg_mp4 = work / f"seg_clip_{idx}.mp4"
                is_img = media_p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                if is_img:
                    bg_8d = work / f"bg_8d_{idx}.mp4"
                    render_8d_animation_clip(
                        img_path=media_p,
                        out_mp4=bg_8d,
                        duration=render_dur,
                        W=VIDEO_W,
                        H=VIDEO_H,
                        fps=FPS,
                        mood=mood_8d,
                        particles="gold_motes",
                        move=moves_8d[idx % len(moves_8d)],
                        is_notebook=False,
                        seed=1000 + idx * 77,
                    )
                    fc = (
                        f"[0:v]fps={FPS},format=yuv420p[bg];"
                        f"[1:v]fps={FPS},format=rgba[ov];"
                        f"[bg][ov]overlay=0:0:shortest=1,format=yuv420p,setsar=1[v]"
                    )
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-loglevel", "error",
                            "-i", str(bg_8d),
                            "-loop", "1", "-t", f"{render_dur:.3f}", "-i", str(ov_png),
                            "-filter_complex", fc, "-map", "[v]", "-t", f"{render_dur:.3f}",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an",
                            str(seg_mp4),
                        ],
                        check=True, capture_output=True, timeout=180,
                    )
                else:
                    fc = (
                        f"[0:v]scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
                        f"crop={VIDEO_W}:{VIDEO_H},fps={FPS},"
                        f"eq=contrast=1.04:saturation=1.12,vignette=PI/6,format=yuv420p[bg];"
                        f"[1:v]fps={FPS},format=rgba[ov];"
                        f"[bg][ov]overlay=0:0:shortest=1,format=yuv420p,setsar=1[v]"
                    )
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-loglevel", "error",
                            "-stream_loop", "-1", "-t", f"{render_dur:.3f}", "-i", str(media_p),
                            "-loop", "1", "-t", f"{render_dur:.3f}", "-i", str(ov_png),
                            "-filter_complex", fc, "-map", "[v]", "-t", f"{render_dur:.3f}",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an",
                            str(seg_mp4),
                        ],
                        check=True, capture_output=True, timeout=180,
                    )
                clip_slots.append((seg_mp4, sdur))

            # مشهد الذروة في الدفتر الصوفي بتحريك 8D ناعم يحافظ على حدة الحروف 100%
            card_slides = render_romantic_notebook_slides(
                [segs[-1]], title, category, work / "nb.png", W=VIDEO_W, H=VIDEO_H, total_dur=climax_dur
            )
            climax_png = card_slides[-1][0]
            climax_mp4 = work / "seg_climax_notebook.mp4"
            render_8d_animation_clip(
                img_path=climax_png,
                out_mp4=climax_mp4,
                duration=climax_dur,
                W=VIDEO_W,
                H=VIDEO_H,
                fps=FPS,
                mood=mood_8d,
                particles="gold_motes",
                move="orbit_in",
                is_notebook=True,
                seed=9090,
            )
            clip_slots.append((climax_mp4, climax_dur))

            _concat_clips_with_xfade(clip_slots, total_dur, work / "vid_only.mp4")

        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(work / "vid_only.mp4"),
                "-i", str(audio_p),
                "-map", "0:v", "-map", "1:a",
                "-t", f"{total_dur:.3f}",
                *FINAL_WHISPER_ENCODE,
                "-c:a", "aac", "-b:a", "80k", "-ar", "44100",
                "-shortest",
                str(out_p),
            ],
            check=True, capture_output=True, timeout=360,
        )
        size_mb = out_p.stat().st_size / (1024 * 1024)
        print(f"✅ تم إخراج فيديو همسات ملك 8D: {out_p.name} ({size_mb:.2f} MB)", flush=True)
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _concat_clips_with_xfade(clip_slots: list[tuple[Path, float]], total_dur: float, out_mp4: Path):
    if len(clip_slots) == 1:
        shutil.copy2(clip_slots[0][0], out_mp4)
        return

    inputs = []
    for p, _ in clip_slots:
        inputs.extend(["-i", str(p)])
    filters = []
    last_tag = "[0:v]"
    offset = 0.0
    n_trans = len(clip_slots) - 1
    for k in range(n_trans):
        offset += float(clip_slots[k][1])
        out_tag = "[vout]" if k == n_trans - 1 else f"[vx{k}]"
        tr = "dissolve" if k == n_trans - 1 else "fade"
        filters.append(f"{last_tag}[{k+1}:v]xfade=transition={tr}:duration={XFADE_DUR:.3f}:offset={offset:.3f}{out_tag}")
        last_tag = out_tag

    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[vout]",
            "-t", f"{total_dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an",
            str(out_mp4),
        ],
        check=True, capture_output=True, timeout=240,
    )


def _render_8d_slides_with_fade(card_slides: list[tuple[Path, float]], total_dur: float, out_mp4: Path, mood_8d: str):
    if len(card_slides) == 1:
        render_8d_animation_clip(
            img_path=card_slides[0][0],
            out_mp4=out_mp4,
            duration=total_dur,
            W=VIDEO_W,
            H=VIDEO_H,
            fps=FPS,
            mood=mood_8d,
            particles="gold_motes",
            move="orbit_in",
            is_notebook=True,
            seed=707,
        )
        return

    min_d = min(d for _, d in card_slides)
    fade_d = min(0.45, min_d * 0.35)
    inputs = []
    filters = []
    tmp_dir = card_slides[0][0].parent
    for i, (p, d) in enumerate(card_slides):
        in_d = d + (fade_d if i < len(card_slides) - 1 else 0.0)
        sl_mp4 = tmp_dir / f"nb_8d_{i}.mp4"
        render_8d_animation_clip(
            img_path=p,
            out_mp4=sl_mp4,
            duration=in_d,
            W=VIDEO_W,
            H=VIDEO_H,
            fps=FPS,
            mood=mood_8d,
            particles="gold_motes",
            move="orbit_in" if i % 2 == 0 else "orbit_out",
            is_notebook=True,
            seed=700 + i * 33,
        )
        inputs.extend(["-i", str(sl_mp4)])
        filters.append(f"[{i}:v]fps={FPS},format=yuv420p,setsar=1[c{i}]")

    last_tag = "[c0]"
    offset = 0.0
    for k in range(len(card_slides) - 1):
        offset += float(card_slides[k][1])
        out_tag = "[vout]" if k == len(card_slides) - 2 else f"[cx{k}]"
        filters.append(f"{last_tag}[c{k+1}]xfade=transition=dissolve:duration={fade_d:.3f}:offset={offset:.3f}{out_tag}")
        last_tag = out_tag

    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[vout]",
            "-t", f"{total_dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an",
            str(out_mp4),
        ],
        check=True, capture_output=True, timeout=240,
    )


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python scripts/whispers_video.py <audio.mp3> <episode.json> <out.mp4>")
        sys.exit(1)
    build_whisper_video(sys.argv[1], sys.argv[2], sys.argv[3])
