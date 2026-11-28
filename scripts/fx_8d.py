#!/usr/bin/env python3
"""
scripts/fx_8d.py
================
محرك العمق ثلاثي الأبعاد 3D المجاني 100% (يعمل على CPU):
- يستخدم Depth Anything V2 Small (ONNX) لفصل المشهد إلى 3 طبقات عمق + Inpainting.
- للخلفيات والقصص: حركة كاميرا ناعمة بثلاثية أبعاد حقيقية (Parallax + Rack Focus لطيف) بدون أشعة محروقة وبدون تشويش يضخّم حجم الفيديو.
- للدفاتر الصوفية (is_notebook=True): يحفظ حدة النص 100% بدون أشعة أو دوران أو جزيئات لتقليل حجم الفيديو لأدنى حد ممكن.
"""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PIL import Image, ImageOps

_DEPS_LOCK = threading.Lock()
_DEPTH_LOCK = threading.Lock()
_DEPTH_SESS = None
_DEPTH_FAILED = False

# تفعيل الأشعة أو الجزيئات فقط إذا طُلب صراحةً عبر متغير بيئة (مطفأة افتراضياً لصفاء النص وصغر الحجم)
ENABLE_8D_RAYS = os.getenv("ENABLE_8D_RAYS", "0") == "1"
ENABLE_8D_PARTICLES = os.getenv("ENABLE_8D_PARTICLES", "0") == "1"


def _ensure_8d_deps() -> bool:
    global np, cv2, ort
    try:
        import numpy as _np
        import cv2 as _cv2
        np, cv2 = _np, _cv2
    except ImportError:
        with _DEPS_LOCK:
            print("   📦 تثبيت numpy و opencv-python-headless لمحرك العمق...", flush=True)
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "numpy", "opencv-python-headless"],
                check=False, timeout=120,
            )
            import numpy as _np
            import cv2 as _cv2
            np, cv2 = _np, _cv2

    try:
        import onnxruntime as _ort
        ort = _ort
    except ImportError:
        with _DEPS_LOCK:
            print("   📦 تثبيت onnxruntime لمحرك العمق...", flush=True)
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "onnxruntime"],
                check=False, timeout=120,
            )
            try:
                import onnxruntime as _ort
                ort = _ort
            except Exception:
                ort = None
    return True


def _get_depth_session():
    global _DEPTH_SESS, _DEPTH_FAILED
    if _DEPTH_FAILED or ort is None:
        return None
    if _DEPTH_SESS is not None:
        return _DEPTH_SESS

    with _DEPTH_LOCK:
        if _DEPTH_SESS is not None:
            return _DEPTH_SESS
        if _DEPTH_FAILED:
            return None

        cache_dir = Path(tempfile.gettempdir()) / "depth_v2_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        model_path = None

        try:
            from huggingface_hub import hf_hub_download
            for fname in ("onnx/model_quantized.onnx", "onnx/model.onnx"):
                try:
                    model_path = hf_hub_download(
                        repo_id="onnx-community/depth-anything-v2-small",
                        filename=fname,
                        cache_dir=str(cache_dir),
                    )
                    if model_path:
                        break
                except Exception:
                    continue
        except Exception:
            pass

        if not model_path:
            local_onnx = cache_dir / "depth_v2_small_q.onnx"
            if local_onnx.exists() and local_onnx.stat().st_size > 8_000_000:
                model_path = str(local_onnx)
            else:
                import requests
                for url in (
                    "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model_quantized.onnx",
                    "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx",
                ):
                    try:
                        r = requests.get(url, timeout=90, stream=True)
                        if r.status_code == 200:
                            with open(local_onnx, "wb") as f:
                                for ch in r.iter_content(65536):
                                    f.write(ch)
                            if local_onnx.stat().st_size > 8_000_000:
                                model_path = str(local_onnx)
                                break
                    except Exception:
                        continue

        if model_path:
            try:
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = max(2, os.cpu_count() or 2)
                _DEPTH_SESS = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
                print(f"🧊 [3D Depth] Depth Anything V2 جاهز: {Path(model_path).name}", flush=True)
                return _DEPTH_SESS
            except Exception as e:
                print(f"   ⚠️ [3D Depth ONNX]: {str(e)[:80]}", flush=True)

        _DEPTH_FAILED = True
        return None


def compute_depth_map_8d(img_bgr, W: int, H: int, cache_key: str | None = None):
    _ensure_8d_deps()
    cache_dir = Path(tempfile.gettempdir()) / "depth_v2_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = (cache_dir / f"d8_{cache_key}_{W}x{H}.npy") if cache_key else None

    if cache_file and cache_file.exists():
        try:
            arr = np.load(str(cache_file))
            if arr.shape == (H, W):
                return arr.astype(np.float32)
        except Exception:
            pass

    sess = _get_depth_session()
    depth_01 = None
    if sess is not None:
        try:
            rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            inp_img = cv2.resize(rgb, (518, 518), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            tensor = np.transpose((inp_img - mean) / std, (2, 0, 1))[None, ...].astype(np.float32)
            in_name = sess.get_inputs()[0].name
            out_name = sess.get_outputs()[0].name
            pred2d = np.squeeze(sess.run([out_name], {in_name: tensor})[0]).astype(np.float32)

            d_min, d_max = float(pred2d.min()), float(pred2d.max())
            if d_max - d_min > 1e-5:
                norm2d = (pred2d - d_min) / (d_max - d_min)
                up = cv2.resize(norm2d, (W, H), interpolation=cv2.INTER_CUBIC)
                up = cv2.bilateralFilter(up.astype(np.float32), d=9, sigmaColor=0.16, sigmaSpace=14)
                up = cv2.GaussianBlur(up, (19, 19), 7.0)
                depth_01 = np.clip(up, 0.0, 1.0).astype(np.float32)
        except Exception:
            pass

    if depth_01 is None:
        yy = np.linspace(0.05, 0.95, H, dtype=np.float32)[:, None]
        xx = np.abs(np.linspace(-1.0, 1.0, W, dtype=np.float32))[None, :]
        depth_01 = np.clip(yy * 0.85 + (1.0 - xx) * 0.15, 0.0, 1.0).astype(np.float32)

    if cache_file:
        try:
            np.save(str(cache_file), depth_01)
        except Exception:
            pass
    return depth_01


_MOODS_8D = {
    "warm_romance": {
        "tint_bgr": (-3, 1, 8),
        "contrast": 1.04,
        "saturation": 1.08,
        "ray_color": (165, 200, 255),
        "ray_strength": 0.05,
        "p_back": (175, 205, 255),
        "p_front": (195, 185, 255),
    },
    "spiritual_noor": {
        "tint_bgr": (3, 4, 6),
        "contrast": 1.04,
        "saturation": 1.06,
        "ray_color": (140, 215, 250),
        "ray_strength": 0.05,
        "p_back": (150, 220, 255),
        "p_front": (185, 235, 255),
    },
    "cosmic_awe": {
        "tint_bgr": (8, 3, -4),
        "contrast": 1.07,
        "saturation": 1.08,
        "ray_color": (135, 200, 255),
        "ray_strength": 0.04,
        "p_back": (145, 210, 255),
        "p_front": (180, 230, 255),
    },
    "storm_trial": {
        "tint_bgr": (10, 2, -4),
        "contrast": 1.09,
        "saturation": 1.04,
        "ray_color": (210, 215, 230),
        "ray_strength": 0.03,
        "p_back": (220, 228, 240),
        "p_front": (235, 240, 248),
    },
    "ancient_civilization": {
        "tint_bgr": (-6, 2, 10),
        "contrast": 1.05,
        "saturation": 1.08,
        "ray_color": (120, 195, 255),
        "ray_strength": 0.04,
        "p_back": (140, 205, 255),
        "p_front": (170, 220, 255),
    },
    "serenity_quran": {
        "tint_bgr": (6, 4, -2),
        "contrast": 1.04,
        "saturation": 1.06,
        "ray_color": (140, 215, 245),
        "ray_strength": 0.04,
        "p_back": (160, 225, 255),
        "p_front": (190, 235, 255),
    },
    "sacred_heritage": {
        "tint_bgr": (-3, 3, 6),
        "contrast": 1.05,
        "saturation": 1.09,
        "ray_color": (130, 205, 255),
        "ray_strength": 0.04,
        "p_back": (145, 215, 255),
        "p_front": (175, 225, 255),
    },
}


def _smoothstep(edge0: float, edge1: float, x):
    t = np.clip((x - edge0) / max(1e-5, edge1 - edge0), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def _init_3d_particles(kind: str, W: int, H: int, seed: int):
    if not ENABLE_8D_PARTICLES or kind in ("none", "mist"):
        return None
    rng = np.random.default_rng(seed)

    if kind == "rain":
        n_back, n_front = 40, 20
        return {
            "kind": "rain",
            "bx": rng.uniform(0, W, n_back), "by": rng.uniform(0, H, n_back),
            "bvy": rng.uniform(14.0, 20.0, n_back), "bvx": rng.uniform(-1.5, -0.8, n_back),
            "blen": rng.uniform(10, 18, n_back),
            "fx": rng.uniform(0, W, n_front), "fy": rng.uniform(0, H, n_front),
            "fvy": rng.uniform(20.0, 28.0, n_front), "fvx": rng.uniform(-2.0, -1.0, n_front),
            "flen": rng.uniform(18, 28, n_front),
        }

    n_back = 10
    n_front = 4
    vy_sign = 1.0 if kind == "snow" else -1.0
    return {
        "kind": kind,
        "bx": rng.uniform(0, W, n_back), "by": rng.uniform(0, H, n_back),
        "bvy": vy_sign * rng.uniform(0.18, 0.45, n_back),
        "bvx": rng.uniform(-0.15, 0.15, n_back),
        "brad": rng.integers(1, 3, n_back),
        "bph": rng.uniform(0, 6.28, n_back),
        "fx": rng.uniform(0, W, n_front), "fy": rng.uniform(0, H, n_front),
        "fvy": vy_sign * rng.uniform(0.35, 0.80, n_front),
        "fvx": rng.uniform(-0.25, 0.25, n_front),
        "frad": rng.integers(4, 9, n_front),
        "fph": rng.uniform(0, 6.28, n_front),
    }


def _draw_particle_layer(frame_bgr, pstate: dict, layer: str, f_idx: int, W: int, H: int, col_bgr: tuple[int, int, int]):
    kind = pstate["kind"]
    overlay = frame_bgr.copy()

    if kind == "rain":
        px_k, py_k, vx_k, vy_k, ln_k = ("bx", "by", "bvx", "bvy", "blen") if layer == "back" else ("fx", "fy", "fvx", "fvy", "flen")
        pstate[px_k] = (pstate[px_k] + pstate[vx_k]) % W
        pstate[py_k] = (pstate[py_k] + pstate[vy_k]) % H
        thick = 1 if layer == "back" else 2
        blend = 0.12 if layer == "back" else 0.16
        for i in range(len(pstate[px_k])):
            x0, y0 = int(pstate[px_k][i]), int(pstate[py_k][i])
            x1, y1 = int(x0 + pstate[vx_k][i] * 1.4), int(y0 + pstate[ln_k][i])
            cv2.line(overlay, (x0, y0), (x1, y1), col_bgr, thick, cv2.LINE_AA)
        return cv2.addWeighted(frame_bgr, 1.0 - blend, overlay, blend, 0)

    px_k, py_k, vx_k, vy_k, r_k, ph_k = (
        ("bx", "by", "bvx", "bvy", "brad", "bph")
        if layer == "back"
        else ("fx", "fy", "fvx", "fvy", "frad", "fph")
    )
    pstate[px_k] = (pstate[px_k] + pstate[vx_k] + 0.20 * np.sin(f_idx * 0.04 + pstate[ph_k])) % W
    pstate[py_k] = (pstate[py_k] + pstate[vy_k]) % H

    if layer == "back":
        for i in range(len(pstate[px_k])):
            cv2.circle(overlay, (int(pstate[px_k][i]), int(pstate[py_k][i])), int(pstate[r_k][i]), col_bgr, -1, cv2.LINE_AA)
        return cv2.addWeighted(frame_bgr, 0.88, overlay, 0.12, 0)

    bokeh_layer = np.zeros_like(frame_bgr)
    for i in range(len(pstate[px_k])):
        cv2.circle(bokeh_layer, (int(pstate[px_k][i]), int(pstate[py_k][i])), int(pstate[r_k][i]), col_bgr, -1, cv2.LINE_AA)
    bokeh_layer = cv2.GaussianBlur(bokeh_layer, (15, 15), 5.0)
    return cv2.addWeighted(frame_bgr, 1.0, bokeh_layer, 0.16, 0)


def render_8d_animation_clip(
    img_path: Path,
    out_mp4: Path,
    duration: float,
    W: int = 720,
    H: int = 1280,
    fps: int = 24,
    mood: str = "warm_romance",
    particles: str = "gold_motes",
    move: str = "orbit_in",
    is_notebook: bool = False,
    seed: int = 42,
    depth_map_override=None,
):
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    # وضع الدفتر الصوفي: صورة ثابتة حادة 100% بدون أي أشعة أو اهتزاز، مما يبقي النص واضحاً والحجم صغيراً جداً
    if is_notebook:
        clean_png = out_mp4.with_suffix(".clean.png")
        with Image.open(img_path) as im:
            im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (W, H), Image.Resampling.LANCZOS)
            im.save(clean_png, "PNG")
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-loop", "1", "-t", f"{duration:.3f}", "-i", str(clean_png),
                    "-vf", f"fps={fps},format=yuv420p,setsar=1",
                    "-t", f"{duration:.3f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-an", str(out_mp4),
                ],
                check=True, capture_output=True, timeout=120,
            )
        finally:
            clean_png.unlink(missing_ok=True)
        return

    _ensure_8d_deps()

    pw, ph = (W, H) if W <= 720 else (720, int(round(720 * H / max(1, W))))
    pw -= pw % 2
    ph -= ph % 2

    with Image.open(img_path) as im:
        im = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (pw, ph), Image.Resampling.LANCZOS)
        bgr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)

    mood_cfg = _MOODS_8D.get(mood, _MOODS_8D["warm_romance"])

    bgr_f = bgr.astype(np.float32)
    tint = np.array(mood_cfg["tint_bgr"], dtype=np.float32)
    bgr_f = (bgr_f - 128.0) * mood_cfg["contrast"] + 128.0 + tint
    bgr = np.clip(bgr_f, 0, 255).astype(np.uint8)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * mood_cfg["saturation"], 0, 255)
    bgr = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    cache_key = hashlib.md5(f"{img_path.stem}_{img_path.stat().st_size}".encode("utf-8")).hexdigest()[:10]
    if depth_map_override is not None and depth_map_override.shape == (ph, pw):
        depth_01 = depth_map_override.astype(np.float32)
    else:
        depth_01 = compute_depth_map_8d(bgr, pw, ph, cache_key=cache_key)

    hole_fg = (depth_01 > 0.62).astype(np.uint8) * 255
    hole_fg = cv2.dilate(hole_fg, np.ones((15, 15), np.uint8), iterations=1)
    hole_mid = (depth_01 > 0.38).astype(np.uint8) * 255
    hole_mid = cv2.dilate(hole_mid, np.ones((19, 19), np.uint8), iterations=1)

    half_w, half_h = max(64, pw // 2), max(64, ph // 2)
    bgr_half = cv2.resize(bgr, (half_w, half_h), interpolation=cv2.INTER_AREA)
    bg_inpaint = cv2.inpaint(bgr_half, cv2.resize(hole_mid, (half_w, half_h), interpolation=cv2.INTER_NEAREST), 5, cv2.INPAINT_TELEA)
    mid_inpaint = cv2.inpaint(bgr_half, cv2.resize(hole_fg, (half_w, half_h), interpolation=cv2.INTER_NEAREST), 4, cv2.INPAINT_TELEA)

    bg_layer = cv2.resize(bg_inpaint, (pw, ph), interpolation=cv2.INTER_CUBIC)
    mid_layer = cv2.resize(mid_inpaint, (pw, ph), interpolation=cv2.INTER_CUBIC)
    fg_layer = bgr

    m_mid_keep = (hole_mid[..., None] == 0).astype(np.float32)
    bg_layer = np.clip(bgr * m_mid_keep + bg_layer * (1.0 - m_mid_keep), 0, 255).astype(np.uint8)
    m_fg_keep = (hole_fg[..., None] == 0).astype(np.float32)
    mid_layer = np.clip(bgr * m_fg_keep + mid_layer * (1.0 - m_fg_keep), 0, 255).astype(np.uint8)

    alpha_mid = cv2.GaussianBlur(_smoothstep(0.30, 0.50, depth_01), (15, 15), 5.0)[..., None]
    alpha_fg = cv2.GaussianBlur(_smoothstep(0.58, 0.76, depth_01), (11, 11), 3.5)[..., None]

    bg_blur = cv2.GaussianBlur(bg_layer, (11, 11), 3.5)
    mid_blur = cv2.GaussianBlur(mid_layer, (9, 9), 3.0)
    fg_blur = cv2.GaussianBlur(fg_layer, (7, 7), 2.2)

    yy, xx = np.mgrid[0:ph, 0:pw].astype(np.float32)
    nx = (xx - pw * 0.5) / (pw * 0.5)
    ny = (yy - ph * 0.5) / (ph * 0.5)
    vig = np.clip(1.0 - 0.14 * (nx * nx + ny * ny), 0.76, 1.0)[..., None].astype(np.float32)

    if ENABLE_8D_RAYS:
        angle_grid = np.arctan2(yy + ph * 0.14, xx - pw * 0.62)
        ray_falloff = np.clip(1.0 - (yy / (ph * 0.86)), 0.0, 1.0) ** 1.65
        # الإصلاح: ray_col مُطبّع في 0..255 لذا نضربه بـ ray_strength فقط (بدون * 255.0!)
        ray_col = np.array(mood_cfg["ray_color"], dtype=np.float32)
        ray_strength = float(mood_cfg["ray_strength"])
    else:
        angle_grid = ray_falloff = ray_col = None
        ray_strength = 0.0

    pstate = _init_3d_particles(particles, pw, ph, seed)

    n_frames = max(12, int(round(duration * fps)))
    vf_args = ["-vf", f"scale={W}:{H}:flags=lanczos,setsar=1"] if (pw != W or ph != H) else ["-vf", "setsar=1"]
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24", "-s", f"{pw}x{ph}", "-r", str(fps),
        "-i", "pipe:0",
        *vf_args,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", str(out_mp4),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    overscan = 1.07
    cx, cy = pw * 0.5, ph * 0.5
    dx_base = xx - cx
    dy_base = yy - cy

    try:
        for f in range(n_frames):
            t = f / max(1, n_frames - 1)
            ease = 0.25 * t + 0.75 * (0.5 - 0.5 * math.cos(math.pi * t))

            # حركة كاميرا 3D خطية ناعمة (بدون تدوير roll ولا اهتزاز يضخم الضغط)
            if move in ("dolly_out", "orbit_out"):
                z_base = 1.0 + 0.045 * (1.0 - ease)
                cam_x = -0.015 * math.sin(math.pi * t) * pw
                cam_y = 0.008 * (ease - 0.5) * ph
            elif move in ("truck_left", "orbit_left"):
                z_base = 1.0 + 0.025 * ease
                cam_x = 0.020 * (ease - 0.5) * pw
                cam_y = 0.0
            elif move in ("truck_right", "orbit_right"):
                z_base = 1.0 + 0.025 * ease
                cam_x = -0.020 * (ease - 0.5) * pw
                cam_y = 0.0
            else:
                z_base = 1.0 + 0.045 * ease
                cam_x = 0.015 * math.sin(math.pi * t) * pw
                cam_y = 0.010 * (0.5 - ease) * ph

            focus_z = 0.72 - 0.30 * (0.5 - 0.5 * math.cos(math.pi * t))
            bg_dof = min(0.45, abs(focus_z - 0.18) * 0.75)
            mid_dof = min(0.32, abs(focus_z - 0.50) * 0.65)
            fg_dof = min(0.35, abs(focus_z - 0.84) * 0.70)

            bg_src = cv2.addWeighted(bg_layer, 1.0 - bg_dof, bg_blur, bg_dof, 0) if bg_dof > 0.03 else bg_layer
            mid_src = cv2.addWeighted(mid_layer, 1.0 - mid_dof, mid_blur, mid_dof, 0) if mid_dof > 0.03 else mid_layer
            fg_src = cv2.addWeighted(fg_layer, 1.0 - fg_dof, fg_blur, fg_dof, 0) if fg_dof > 0.03 else fg_layer

            def _warp_layer(src, factor: float):
                sc = 1.0 / (overscan * (1.0 + (z_base - 1.0) * factor))
                mx = (cx + dx_base * sc + cam_x * factor).astype(np.float32)
                my = (cy + dy_base * sc + cam_y * factor).astype(np.float32)
                return cv2.remap(src, mx, my, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)

            w_bg = _warp_layer(bg_src, 0.30).astype(np.float32)
            w_mid = _warp_layer(mid_src, 1.00).astype(np.float32)
            w_fg = _warp_layer(fg_src, 2.05).astype(np.float32)

            a_mid_w = _warp_layer(alpha_mid, 1.00)
            if a_mid_w.ndim == 2:
                a_mid_w = a_mid_w[..., None]
            a_fg_w = _warp_layer(alpha_fg, 2.05)
            if a_fg_w.ndim == 2:
                a_fg_w = a_fg_w[..., None]

            comp = w_bg * (1.0 - a_mid_w) + w_mid * a_mid_w

            if pstate is not None:
                comp_u8 = _draw_particle_layer(np.clip(comp, 0, 255).astype(np.uint8), pstate, "back", f, pw, ph, mood_cfg["p_back"])
                comp = comp_u8.astype(np.float32)

            comp = (comp * (1.0 - a_fg_w) + w_fg * a_fg_w) * vig

            if ENABLE_8D_RAYS and ray_col is not None and ray_strength > 0:
                ray_wave = 0.5 + 0.5 * np.sin(angle_grid * 13.0 + 0.6 + f * 0.04)
                noor_mask = ((ray_wave ** 2.2) * ray_falloff)[..., None] * (1.0 - 0.65 * a_fg_w)
                pulse = 0.85 + 0.15 * math.sin(f * 0.07)
                comp = comp + noor_mask * ray_col * (ray_strength * pulse)

            frame_out = np.clip(comp, 0, 255).astype(np.uint8)

            if pstate is not None:
                frame_out = _draw_particle_layer(frame_out, pstate, "front", f, pw, ph, mood_cfg["p_front"])

            proc.stdin.write(frame_out.tobytes())
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        stderr_bytes = proc.stderr.read() if proc.stderr else b""
        ret = proc.wait(timeout=180)
        if ret != 0:
            raise RuntimeError(f"3D Depth ffmpeg error: {stderr_bytes.decode('utf-8', 'ignore')[:300]}")
