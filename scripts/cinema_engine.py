"""
cinema_engine.py — محرك الأنيميشن السينمائي 2.5D/AI لقناة ملاك المعرفة
- يستخدم التوكنات الموجودة مع تجاوز تلقائي لأي HF_TOKEN معطوب (401) بالتحميل العام وAI Horde المتوازي.
- يطبّق ضوابط بصرية ذات مستويين: يسمح بالبشر العاديين تاريخياً عند حاجة القصة، ويمنع تماماً أي تجسيد في سياقات
  الذات الإلهية والأنبياء والصحابة والملائكة والمبشرين بالجنة والنساء المسلمات.
"""

from __future__ import annotations

import os
import re
import io
import sys
import json
import math
import time
import base64
import hashlib
import random
import shutil
import inspect
import tempfile
import threading
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import requests
from PIL import Image, ImageOps

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import PIXABAY_KEY
from filters_loader import is_safe_tag, is_safe_query, is_sacred_context

try:
    import numpy as np
    import cv2
    HAS_CV2 = True
except Exception:
    np = None
    cv2 = None
    HAS_CV2 = False

try:
    import onnxruntime as ort
    HAS_ORT = True
except Exception:
    ort = None
    HAS_ORT = False

try:
    from huggingface_hub import hf_hub_download, InferenceClient
    HAS_HF_HUB = True
except Exception:
    hf_hub_download = None
    InferenceClient = None
    HAS_HF_HUB = False

try:
    from gradio_client import Client as GradioClient, handle_file as gradio_handle_file
    HAS_GRADIO = True
except Exception:
    GradioClient = None
    gradio_handle_file = None
    HAS_GRADIO = False

CINEMA_AVAILABLE = HAS_CV2 and (np is not None)

_STATE_LOCK = threading.Lock()
_VISION_LOCK = threading.Lock()
_LAST_VISION_TS = 0.0

# ==============================================================================
# الهوية البصرية الموحدة (Master Art Style Bible — Painterly 3D Epic Cocktail)
# ==============================================================================
STYLE_BIBLE_SUFFIX = (
    "masterpiece painterly 3D epic animation frame, arcane fortiche and prince of egypt "
    "inspired visual fusion, rich conceptual oil brushwork texture blended with volumetric "
    "3D cinematic depth, dramatic chiaroscuro rim lighting, deep atmospheric perspective, "
    "filmic color grading, vertical 9:16 portrait composition, ultra detailed, no text, "
    "no arabic letters, no watermark, no signature, no border frame"
)

BASE_NEGATIVE = (
    "photorealistic modern selfie, flat cartoon, chibi, anime meme, text, words, letters, "
    "watermark, logo, signature, border, modern car, airplane, skyscraper, electricity pole, "
    "neon sign, wristwatch, modern clothing, deformed hands, extra limbs, distorted anatomy, "
    "glowing halo around head, angelic wings, idol statue, sculpture, church, cross, crucifix, "
    "bell tower, cathedral, nudity, revealing clothes"
)

STRICT_NO_HUMANS_NEGATIVE = (
    "human, humans, person, people, man, men, woman, women, child, face, faces, portrait, "
    "body, silhouette, crowd, hand, hands, eye, eyes, angel, angels, cherub, wings, "
    "warrior, figure, humanoid, " + BASE_NEGATIVE
)

_FORBIDDEN_STOCK_RE = re.compile(
    r"\b(?:church|cathedral|chapel|basilica|monastery|abbey|bell\s*tower|steeple|gothic|"
    r"crucifix|cross|altar|priest|nun|monk|statue|sculpture|idol|buddha|hindu|shrine)\b",
    re.IGNORECASE,
)

MOOD_PALETTES = {
    "cosmic_awe": {
        "desc": "deep obsidian indigo sky and radiant amber gold volumetric celestial rays, monumental primordial cosmic scale",
        "tint_bgr": (18, 8, -10),
        "contrast": 1.12,
        "sat": 1.15,
        "ray_color": (135, 200, 255),
        "default_particles": "gold_motes",
        "default_sfx": "desert_breeze",
    },
    "ancient_civilization": {
        "desc": "warm sandstone ochre and twilight slate, torchlight shadows, colossal carved desert canyon sandstone, floating golden dust",
        "tint_bgr": (-12, 4, 18),
        "contrast": 1.10,
        "sat": 1.10,
        "ray_color": (120, 195, 255),
        "default_particles": "gold_motes",
        "default_sfx": "wind",
    },
    "storm_trial": {
        "desc": "brooding storm teal and copper lightning glow, wind-swept clouds and turbulent horizon, intense chiaroscuro",
        "tint_bgr": (16, 4, -8),
        "contrast": 1.16,
        "sat": 0.96,
        "ray_color": (210, 215, 230),
        "default_particles": "rain",
        "default_sfx": "rain_thunder",
    },
    "serenity_quran": {
        "desc": "tranquil deep emerald teal dusk and soft warm golden horizon, misty stillness, reverent sacred serenity",
        "tint_bgr": (12, 10, -4),
        "contrast": 1.06,
        "sat": 1.05,
        "ray_color": (140, 215, 245),
        "default_particles": "gold_motes",
        "default_sfx": "none",
    },
    "sacred_heritage": {
        "desc": "warm amber lantern light, ancient middle eastern desert oasis and calm water reflections, atmospheric dusk",
        "tint_bgr": (-6, 5, 14),
        "contrast": 1.08,
        "sat": 1.12,
        "ray_color": (130, 205, 255),
        "default_particles": "embers",
        "default_sfx": "wind",
    },
}

CAMERA_MOVES = ("dolly_in", "dolly_out", "truck_left", "truck_right", "tilt_up")
XFADE_TYPES = ("fade", "dissolve", "smoothleft", "smoothright")

_DEFAULT_IMAGE_SPACES = [
    s.strip() for s in os.getenv(
        "HF_IMAGE_SPACES",
        "black-forest-labs/FLUX.1-schnell,Tongyi-MAI/Z-Image-Turbo,black-forest-labs/FLUX.2-dev"
    ).split(",") if s.strip()
]

_DEFAULT_I2V_SPACES = [
    s.strip() for s in os.getenv(
        "HF_I2V_SPACES",
        "Wan-AI/Wan2.2-I2V-A14B,Lightricks/LTX-Video"
    ).split(",") if s.strip()
]

_RUNTIME_STATE = {
    "hf_token_invalid": False,
    "zerogpu_exhausted": False,
    "hf_inference_disabled": False,
    "bad_spaces": set(),
    "space_clients": {},
    "space_apis": {},
    "depth_session": None,
    "depth_failed": False,
}


def _purge_invalid_hf_token(reason: str = "401"):
    with _STATE_LOCK:
        if not _RUNTIME_STATE["hf_token_invalid"]:
            print(f"   🔓 تم اكتشاف HF_TOKEN غير صالح ({reason}) — التحويل للوصول العام المجاني بدون توكن.", flush=True)
        _RUNTIME_STATE["hf_token_invalid"] = True
        _RUNTIME_STATE["hf_inference_disabled"] = True
        _RUNTIME_STATE["space_clients"].clear()
        _RUNTIME_STATE["space_apis"].clear()
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("HUGGINGFACE_HUB_TOKEN", None)


def _cache_root() -> Path:
    p = Path(tempfile.gettempdir()) / "story_media_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _share_enabled() -> bool:
    return os.getenv("SHARE_EPISODE_MEDIA", "0") == "1"


def _get_gemini_pool_and_models():
    try:
        from gemini_tts_pool import get_pool
        from model_discovery import discover_and_cache
        from utilities import get_breaker
        pool = get_pool()
        breaker = get_breaker()
        raw_models = discover_and_cache(force=False) or []
        models = []
        seen = set()
        for m in raw_models:
            clean_m = m.removeprefix("models/").strip()
            if "tts" in clean_m.lower() or "image" in clean_m.lower():
                continue
            if clean_m and clean_m not in seen:
                seen.add(clean_m)
                models.append(clean_m)
        for fb in ("gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"):
            if fb not in seen:
                seen.add(fb)
                models.append(fb)
        return pool, breaker, models[:8]
    except Exception:
        return None, None, []


def _thinking_cfg(model: str) -> dict | None:
    ml = model.lower()
    if any(x in ml for x in ("gemini-3", "gemini-4")):
        return {"thinkingLevel": "minimal"}
    if "gemini-2.5" in ml:
        return {"thinkingBudget": 0}
    return None


def _call_gemini_json(parts: list[dict], timeout: int = 45, max_tokens: int = 4096) -> dict | None:
    pool, breaker, models = _get_gemini_pool_and_models()
    if not pool or not models:
        return None

    for model in models:
        if breaker and breaker.is_open(f"cinema:{model}"):
            continue
        use_thinking = True
        tries = 0
        while tries < 2:
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                gen_cfg = {
                    "temperature": 0.25,
                    "maxOutputTokens": max_tokens,
                    "responseMimeType": "application/json",
                }
                if use_thinking:
                    tc = _thinking_cfg(model)
                    if tc:
                        gen_cfg["thinkingConfig"] = tc

                r = requests.post(
                    url,
                    json={"contents": [{"parts": parts}], "generationConfig": gen_cfg},
                    timeout=(12, timeout),
                )
                if r.status_code == 200:
                    resp = r.json()
                    cand = (resp.get("candidates") or [{}])[0]
                    cparts = cand.get("content", {}).get("parts", [])
                    txt_list = [p.get("text", "") for p in cparts if not p.get("thought") and p.get("text")]
                    raw = "\n".join(txt_list) if txt_list else (cparts[-1].get("text", "") if cparts else "")
                    s, e = raw.find("{"), raw.rfind("}")
                    if s != -1 and e > s:
                        parsed = json.loads(raw[s:e + 1])
                        pool.advance()
                        if breaker:
                            breaker.record_success(f"cinema:{model}")
                        return parsed
                    break
                elif r.status_code == 401 or (r.status_code == 403 and "api key" in r.text.lower()):
                    pool.mark_dead(f"HTTP {r.status_code}")
                    continue
                elif r.status_code == 400 and use_thinking and "thinking" in r.text.lower():
                    use_thinking = False
                    continue
                elif r.status_code == 429:
                    pool.mark_failed(45)
                    tries += 1
                    time.sleep(1.5)
                    continue
                elif r.status_code in (400, 403, 404):
                    if breaker:
                        breaker.record_failure(f"cinema:{model}")
                    break
                else:
                    tries += 1
                    pool.advance()
                    time.sleep(2)
            except Exception:
                tries += 1
                pool.advance()
    return None


# ==============================================================================
# 1) مخرج اللقطات الذكي (AI Shot Director — إيقاع سينمائي 5-6.5 ثوانٍ متزامنة)
# ==============================================================================
def _target_shots_for_duration(c_dur: float, is_quran: bool) -> int:
    if is_quran:
        return 2 if c_dur >= 16.0 else 1
    if c_dur < 7.0:
        return 1
    if c_dur < 13.0:
        return 2
    if c_dur < 20.0:
        return 3
    return max(3, min(4, int(round(c_dur / 6.0))))


def _normalize_shot_durations(shots: list[dict], total_dur: float) -> list[dict]:
    if not shots:
        return shots
    n = len(shots)
    raw_durs = [max(0.8, float(s.get("duration") or (total_dur / n))) for s in shots]
    ratio = total_dur / max(0.1, sum(raw_durs))
    scaled = [max(0.75, round(d * ratio, 3)) for d in raw_durs]
    if n > 1:
        diff = round(total_dur - sum(scaled[:-1]), 3)
        if diff >= 0.65:
            scaled[-1] = diff
        else:
            factor = total_dur / sum(scaled)
            scaled = [round(d * factor, 3) for d in scaled]
            scaled[-1] = round(total_dur - sum(scaled[:-1]), 3)
    else:
        scaled[0] = round(total_dur, 3)

    for s, d in zip(shots, scaled):
        s["duration"] = max(0.5, d)
    return shots


def _fallback_shots_for_chunk(c_text: str, c_dur: float, stype: str, ep_title: str, episode_anchor: str) -> list[dict]:
    is_quran = (stype == "quran")
    sacred = is_quran or is_sacred_context(c_text) or is_sacred_context(ep_title)
    n_shots = _target_shots_for_duration(c_dur, is_quran)

    t = c_text or ""
    if any(w in t for w in ("عرش", "كرسي", "سماوات", "سماء", "نجم", "لوح", "قلم", "خلق")):
        mood = "cosmic_awe"
        base_envs = [
            "vast primordial cosmos with swirling golden nebulae and radiant celestial light shafts above dark cosmic clouds",
            "monumental expanse of ancient heavens glowing with amber starlight and layered translucent clouds",
            "intimate view of shimmering golden dust motes drifting across a deep indigo celestial horizon",
        ]
        fb_q = "starry night milky way"
    elif any(w in t for w in ("بحر", "موج", "طوفان", "مطر", "ريح", "عاصفة", "رعد", "برق")):
        mood = "storm_trial"
        base_envs = [
            "dramatic stormy ocean horizon under towering dark thunderclouds lit by golden-copper lightning",
            "sweeping wind over rugged coastal cliffs with crashing waves and mist in volumetric twilight",
            "close cinematic view of heavy raindrops splashing across dark basalt rocks in storm light",
        ]
        fb_q = "ocean waves clouds sunset"
    elif any(w in t for w in ("مدينة", "قصر", "حجر", "صخر", "قافلة", "قوم", "بناء", "جبل", "صحراء")):
        mood = "ancient_civilization"
        if sacred:
            base_envs = [
                "sweeping wide shot of ancient desert canyon cliffs bathed in golden hour dust",
                "medium cinematic view of ancient desert sandstone formations and glowing warm twilight",
                "close view of wind-blown desert sand drifting across weathered sandstone ridges",
            ]
        else:
            base_envs = [
                "wide establishing view of an ancient middle eastern desert valley with distant caravans under golden dusk",
                "medium tracking shot of ancient stone masons working beside sandstone cliffs at twilight",
                "dramatic low angle silhouette of ancient travelers gazing toward a vast desert horizon at sunset",
            ]
        fb_q = "desert sand dunes sunset"
    else:
        mood = "serenity_quran" if is_quran else "sacred_heritage"
        base_envs = [
            "serene ancient desert oasis with calm reflective water pool mirroring golden sunset clouds",
            "medium view of misty emerald mountain valley with soft golden sunbeams piercing clouds",
            "close cinematic view of warm golden lantern light glowing softly beside an open blank parchment scroll",
        ]
        fb_q = "misty mountain sunrise"

    if is_quran:
        mood = "serenity_quran"

    shot_types = ("wide", "medium", "closeup")
    shots = []
    for i in range(n_shots):
        env = base_envs[i % len(base_envs)]
        st = shot_types[i % len(shot_types)]
        shots.append({
            "sub_idx": i,
            "duration": round(c_dur / n_shots, 3),
            "shot_type": st,
            "camera_move": CAMERA_MOVES[(i + len(t)) % len(CAMERA_MOVES)],
            "mood": mood,
            "particles": "none" if is_quran else MOOD_PALETTES[mood]["default_particles"],
            "sfx_type": "none" if is_quran else MOOD_PALETTES[mood]["default_sfx"],
            "allow_humans": False if sacred else (not is_quran and ("workers" in env or "masons" in env or "travelers" in env)),
            "is_hero_climax": (i == 0 and stype in ("narration", "teaser")),
            "prompt_en": f"{st} cinematic shot, {env}, consistent with {episode_anchor}",
            "fallback_query": fb_q,
        })
    return _normalize_shot_durations(shots, c_dur)


def _enforce_shot_safety(shot: dict, c_text: str, stype: str, ep_title: str) -> dict:
    is_quran = (stype == "quran")
    sacred = is_quran or is_sacred_context(c_text) or is_sacred_context(ep_title)
    if sacred:
        shot["allow_humans"] = False
    else:
        shot["allow_humans"] = bool(shot.get("allow_humans", False))

    prompt = re.sub(r"[^\x20-\x7E]", " ", str(shot.get("prompt_en", ""))).strip()
    if len(prompt) < 15:
        prompt = "epic ancient desert horizon beneath dramatic golden twilight clouds"

    prompt = _FORBIDDEN_STOCK_RE.sub("desert rock", prompt)

    if not shot["allow_humans"]:
        prompt = re.sub(
            r"\b(man|men|woman|women|person|people|human|humans|crowd|worker|workers|angel|angels|prophet|face|hands?|silhouettes?|travelers?|soldiers?)\b",
            "ancient sandstone canyon",
            prompt,
            flags=re.IGNORECASE,
        )
        prompt += ", completely uninhabited pure natural environment, zero people, zero figures, zero faces, zero wings"
    else:
        prompt += ", modest ancient historical middle eastern attire, no wings, no halos"

    shot["prompt_en"] = re.sub(r"\s+", " ", prompt).strip()

    fb_q = str(shot.get("fallback_query", "")).strip()
    if not fb_q or _FORBIDDEN_STOCK_RE.search(fb_q) or any(w in fb_q.lower() for w in ("arch", "courtyard", "building", "palace", "tower", "city", "monument")):
        shot["fallback_query"] = "starry night milky way" if shot.get("mood") == "cosmic_awe" else "desert sunset mountain clouds"

    if shot.get("mood") not in MOOD_PALETTES:
        shot["mood"] = "serenity_quran" if is_quran else "cosmic_awe"
    if shot.get("camera_move") not in CAMERA_MOVES:
        shot["camera_move"] = random.choice(CAMERA_MOVES)
    if shot.get("particles") not in ("gold_motes", "embers", "rain", "snow", "mist", "none"):
        shot["particles"] = "none" if is_quran else MOOD_PALETTES[shot["mood"]]["default_particles"]
    if is_quran:
        shot["sfx_type"] = "none"
    elif shot.get("sfx_type") not in ("wind", "desert_breeze", "ocean", "rain_thunder", "embers", "none"):
        shot["sfx_type"] = MOOD_PALETTES[shot["mood"]]["default_sfx"]
    return shot


def plan_episode_shots(meta: dict) -> dict:
    ep_title = meta.get("title", "")
    story_id = meta.get("story_id", "")
    segments = meta.get("segments", [])

    plan_key = hashlib.md5(f"{story_id}:{ep_title}:{len(segments)}".encode("utf-8")).hexdigest()[:10]
    shared_plan_file = _cache_root() / f"cinema_plan_{plan_key}.json"

    cached_chunks_plan: dict[str, dict] = {}
    episode_anchor = "golden twilight ancient Middle Eastern desert horizons and vast dramatic cosmic skies"

    if _share_enabled() and shared_plan_file.exists():
        try:
            saved = json.loads(shared_plan_file.read_text(encoding="utf-8"))
            cached_chunks_plan = saved.get("chunks", {})
            episode_anchor = saved.get("episode_anchor", episode_anchor)
        except Exception:
            cached_chunks_plan = {}

    pending_batches = []
    current_batch = []
    for seg_idx, seg in enumerate(segments):
        stype = seg.get("type", "narration")
        for c_idx, chunk in enumerate(seg.get("chunks", [])):
            c_dur = float(chunk.get("duration", 0))
            if c_dur <= 0:
                continue
            ckey = f"{seg_idx}_{c_idx}"
            if ckey in cached_chunks_plan:
                continue
            c_text = (chunk.get("text") or "").strip()
            sacred = (stype == "quran") or is_sacred_context(c_text) or is_sacred_context(ep_title)
            current_batch.append({
                "key": ckey,
                "seg_idx": seg_idx,
                "c_idx": c_idx,
                "type": stype,
                "duration": round(c_dur, 3),
                "n_shots": _target_shots_for_duration(c_dur, stype == "quran"),
                "force_no_humans": sacred,
                "text": c_text[:450],
            })
            if len(current_batch) >= 8:
                pending_batches.append(current_batch)
                current_batch = []
    if current_batch:
        pending_batches.append(current_batch)

    if pending_batches:
        print(f"🎬 AI Shot Director: تخطيط لقطات سينمائية متزامنة لـ {sum(len(b) for b in pending_batches)} مقطع...", flush=True)

    for batch in pending_batches:
        batch_desc = json.dumps(batch, ensure_ascii=False, indent=2)
        prompt = f"""You are a Hollywood Animation Art Director for an Islamic historical & cosmic documentary series ("Malak Al-Marifa").
Episode: "{ep_title}"

Task: Break each audio chunk below into exact sequential visual shots (5-6.5 seconds each, Wide -> Medium -> Close-up/POV) that directly depict what the Arabic narration is saying.

STRICT VISUAL & ISLAMIC POLICY:
1. If "force_no_humans" is true (e.g. Quran, Allah, Throne/Kursi, Creation, Angels, Prophets, Companions, Muslim women), you MUST set "allow_humans": false and describe ONLY cosmic scale, natural phenomena, light rays, ancient desert/mountain vistas, blank parchment scrolls, or POV camera views — NEVER include people, faces, hands, silhouettes, angels, or wings.
2. If "force_no_humans" is false AND the Arabic text clearly mentions ordinary historical humans (ancient builders, crowds, travelers, caravans), you MAY set "allow_humans": true with historically modest Middle Eastern attire (no wings, no glowing halos, no modern clothes). Otherwise keep "allow_humans": false.
3. NEVER use churches, cathedrals, crosses, statues, idols, or gothic towers.
4. Keep "prompt_en" purely visual English (18-30 words per shot), and keep "fallback_query" strictly a 3-word nature/sky/desert query (e.g. "starry night sky", "desert dunes sunset", "ocean waves clouds").

Input Chunks:
{batch_desc}

Return ONLY valid JSON:
{{
  "episode_anchor": "short 10-15 word consistent lighting/setting description",
  "chunks": {{
    "0_0": [
      {{
        "shot_type": "wide|medium|closeup",
        "camera_move": "dolly_in|dolly_out|truck_left|truck_right|tilt_up",
        "mood": "cosmic_awe|ancient_civilization|storm_trial|serenity_quran|sacred_heritage",
        "particles": "gold_motes|embers|rain|snow|mist|none",
        "sfx_type": "wind|desert_breeze|ocean|rain_thunder|embers|none",
        "allow_humans": false,
        "is_hero_climax": true,
        "duration": 5.5,
        "prompt_en": "wide establishing view of...",
        "fallback_query": "starry night sky"
      }}
    ]
  }}
}}"""
        res = _call_gemini_json([{"text": prompt}], timeout=55, max_tokens=5120)
        if res and isinstance(res.get("chunks"), dict):
            if res.get("episode_anchor"):
                episode_anchor = re.sub(r"[^\x20-\x7E]", " ", str(res["episode_anchor"])).strip() or episode_anchor
            for item in batch:
                ck = item["key"]
                planned_list = res["chunks"].get(ck)
                if isinstance(planned_list, list) and planned_list:
                    cleaned_list = []
                    for s_i, sh in enumerate(planned_list[:item["n_shots"] + 1]):
                        if not isinstance(sh, dict):
                            continue
                        sh["sub_idx"] = s_i
                        cleaned_list.append(_enforce_shot_safety(sh, item["text"], item["type"], ep_title))
                    if cleaned_list:
                        cached_chunks_plan[ck] = {
                            "shots": _normalize_shot_durations(cleaned_list, item["duration"]),
                        }
                        continue
                cached_chunks_plan[ck] = {
                    "shots": _fallback_shots_for_chunk(
                        item["text"], item["duration"], item["type"], ep_title, episode_anchor
                    )
                }
        else:
            for item in batch:
                cached_chunks_plan[item["key"]] = {
                    "shots": _fallback_shots_for_chunk(
                        item["text"], item["duration"], item["type"], ep_title, episode_anchor
                    )
                }

    final_chunks: dict[str, list[dict]] = {}
    for seg_idx, seg in enumerate(segments):
        stype = seg.get("type", "narration")
        for c_idx, chunk in enumerate(seg.get("chunks", [])):
            c_dur = float(chunk.get("duration", 0))
            if c_dur <= 0:
                continue
            ck = f"{seg_idx}_{c_idx}"
            base_entry = cached_chunks_plan.get(ck)
            if base_entry and base_entry.get("shots"):
                shots_copy = [dict(s) for s in base_entry["shots"]]
                shots_copy = [_enforce_shot_safety(s, chunk.get("text", ""), stype, ep_title) for s in shots_copy]
                final_chunks[ck] = _normalize_shot_durations(shots_copy, c_dur)
            else:
                final_chunks[ck] = _fallback_shots_for_chunk(
                    chunk.get("text", ""), c_dur, stype, ep_title, episode_anchor
                )

    if _share_enabled():
        try:
            shared_plan_file.write_text(
                json.dumps({"episode_anchor": episode_anchor, "chunks": cached_chunks_plan}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    return {
        "plan_key": plan_key,
        "episode_anchor": episode_anchor,
        "chunks": final_chunks,
    }


# ==============================================================================
# 2) فاحص الأمان البصري (Gemini Vision Guard + كاشف احتياطي محلي)
# ==============================================================================
def _vision_guard_check(img_path: Path, allow_humans: bool) -> tuple[bool, str]:
    global _LAST_VISION_TS
    try:
        with Image.open(img_path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((512, 512), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=82)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        return False, f"unreadable_image: {e}"

    human_rule = (
        "Ordinary historical people/crowds/workers wearing modest ancient garments are ALLOWED, "
        "EXCEPT any prophet/saint glowing halo, winged angel, idol statue, modern clothing, or immodesty."
        if allow_humans
        else "ZERO humans, faces, hands, human silhouettes, crowds, humanoid shadows, or winged figures are allowed ANYWHERE in the image."
    )

    guard_prompt = f"""Inspect this vertical illustration strictly for Islamic documentary quality & safety.
Policy:
1. {human_rule}
2. Reject if there is gibberish text, watermarks, logos, modern vehicles/skyscrapers, Christian crosses/churches/idolatry/statues, horror/gore, or severe visual distortion.

Return ONLY JSON:
{{
  "has_humans": true,
  "has_wings_or_halo": false,
  "has_sacred_or_immodest_violation": false,
  "has_modern_or_text_or_glitch": false,
  "safe": true,
  "reason": "short note"
}}"""

    with _VISION_LOCK:
        gap = 1.0 - (time.time() - _LAST_VISION_TS)
        if gap > 0:
            time.sleep(gap)
        res = _call_gemini_json(
            [
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                {"text": guard_prompt},
            ],
            timeout=25,
            max_tokens=512,
        )
        _LAST_VISION_TS = time.time()

    if res:
        has_humans = bool(res.get("has_humans", False))
        has_wings = bool(res.get("has_wings_or_halo", False))
        has_sacred_bad = bool(res.get("has_sacred_or_immodest_violation", False))
        has_glitch = bool(res.get("has_modern_or_text_or_glitch", False))
        explicit_safe = bool(res.get("safe", True))

        if has_wings or has_sacred_bad or has_glitch or not explicit_safe:
            return False, str(res.get("reason", "vision_guard_rejected"))
        if (not allow_humans) and has_humans:
            return False, "vision_guard_detected_human_in_strict_scene"
        return True, "ok"

    if (not allow_humans) and HAS_CV2:
        try:
            cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            if os.path.exists(cascade_path):
                clf = cv2.CascadeClassifier(cascade_path)
                gray = cv2.cvtColor(cv2.imread(str(img_path)), cv2.COLOR_BGR2GRAY)
                faces = clf.detectMultiScale(gray, 1.15, 5, minSize=(36, 36))
                if len(faces) > 0:
                    return False, "local_haar_detected_face"
        except Exception:
            pass
    return True, "fallback_unchecked"


# ==============================================================================
# 3) مولد الصور متعدد الطبقات (HF ZeroGPU -> AI Horde المتوازي -> Stock آمن)
# ==============================================================================
def _get_hf_token() -> str | None:
    if _RUNTIME_STATE["hf_token_invalid"]:
        return None
    tok = (os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACE_HUB_TOKEN", "")).strip()
    return tok or None


def _get_gradio_client(space_id: str):
    if not HAS_GRADIO:
        return None
    with _STATE_LOCK:
        if space_id in _RUNTIME_STATE["bad_spaces"]:
            return None
        if space_id in _RUNTIME_STATE["space_clients"]:
            return _RUNTIME_STATE["space_clients"][space_id]

    for attempt_anon in (False, True):
        hf_token = None if attempt_anon else _get_hf_token()
        if attempt_anon and not _RUNTIME_STATE["hf_token_invalid"]:
            break
        try:
            sig = inspect.signature(GradioClient.__init__)
            kwargs = {}
            if hf_token:
                if "token" in sig.parameters:
                    kwargs["token"] = hf_token
                elif "hf_token" in sig.parameters:
                    kwargs["hf_token"] = hf_token
            client = GradioClient(space_id, **kwargs)
            try:
                api_info = client.view_api(return_format="dict") or {}
            except Exception:
                api_info = {}
            with _STATE_LOCK:
                _RUNTIME_STATE["space_clients"][space_id] = client
                _RUNTIME_STATE["space_apis"][space_id] = api_info
            return client
        except Exception as e:
            msg = str(e)
            if ("401" in msg or "403" in msg or "invalid" in msg.lower()) and not _RUNTIME_STATE["hf_token_invalid"]:
                _purge_invalid_hf_token("Gradio 401")
                continue
            print(f"   ⚠️ تعذر فتح Space ({space_id}): {msg[:90]}", flush=True)
            with _STATE_LOCK:
                _RUNTIME_STATE["bad_spaces"].add(space_id)
            return None
    return None


def _extract_media_path(result, out_dir: Path, prefix: str) -> Path | None:
    def _walk(obj):
        if isinstance(obj, str) and obj.strip():
            return obj.strip()
        if isinstance(obj, dict):
            for k in ("path", "url", "video", "image"):
                if k in obj:
                    v = _walk(obj[k])
                    if v:
                        return v
        if isinstance(obj, (list, tuple)):
            for item in obj:
                v = _walk(item)
                if v:
                    return v
        return None

    found = _walk(result)
    if not found:
        return None
    if os.path.exists(found):
        return Path(found)
    if found.startswith("http"):
        try:
            r = requests.get(found, timeout=45)
            if r.status_code == 200 and len(r.content) > 5000:
                ext = ".mp4" if ".mp4" in found.lower() else ".png"
                dst = out_dir / f"{prefix}_{int(time.time()*1000)}_{threading.get_ident()}{ext}"
                dst.write_bytes(r.content)
                return dst
        except Exception:
            pass
    return None


def _build_gradio_kwargs(space_id: str, prompt: str, neg_prompt: str, seed: int, w: int, h: int, image_file: Path | None = None):
    with _STATE_LOCK:
        api = _RUNTIME_STATE["space_apis"].get(space_id, {})
    named = api.get("named_endpoints", {})
    if not named:
        return None, None

    ep_name = None
    for candidate in ("/infer", "/generate", "/predict", "/generate_image", "/image_to_video"):
        if candidate in named:
            ep_name = candidate
            break
    if not ep_name:
        ep_name = next(iter(named.keys()))

    params = named[ep_name].get("parameters", [])
    kwargs = {}
    for p in params:
        pname = (p.get("parameter_name") or "").lower()
        pdefault = p.get("parameter_default")
        if image_file is not None and pname in ("image", "input_image", "img", "first_frame"):
            kwargs[p["parameter_name"]] = gradio_handle_file(str(image_file))
        elif pname in ("prompt", "positive_prompt", "caption"):
            kwargs[p["parameter_name"]] = prompt
        elif pname in ("negative_prompt", "neg_prompt"):
            kwargs[p["parameter_name"]] = neg_prompt
        elif pname == "seed":
            kwargs[p["parameter_name"]] = int(seed)
        elif pname in ("randomize_seed", "random_seed"):
            kwargs[p["parameter_name"]] = False
        elif pname == "width":
            kwargs[p["parameter_name"]] = int(w)
        elif pname == "height":
            kwargs[p["parameter_name"]] = int(h)
        elif pname in ("num_inference_steps", "steps"):
            kwargs[p["parameter_name"]] = 4 if "schnell" in space_id.lower() else 8
        elif pname in ("guidance_scale", "guidance"):
            kwargs[p["parameter_name"]] = 0.0 if ("schnell" in space_id.lower() or "z-image" in space_id.lower()) else 3.5
        elif pname == "resolution":
            enum_vals = (p.get("type") or {}).get("enum") or []
            chosen_res = None
            for ev in enum_vals:
                if "9:16" in str(ev) or "720x1280" in str(ev) or "576x1024" in str(ev):
                    chosen_res = ev
                    break
            kwargs[p["parameter_name"]] = chosen_res or pdefault
        elif pname == "aspect_ratio":
            kwargs[p["parameter_name"]] = "9:16"
        elif pname in ("duration", "duration_seconds") and image_file is not None:
            kwargs[p["parameter_name"]] = 4
        elif "parameter_default" in p and p.get("parameter_has_default", True):
            kwargs[p["parameter_name"]] = pdefault

    return ep_name, kwargs


def _call_zerogpu_image(prompt: str, neg_prompt: str, seed: int, out_path: Path) -> bool:
    if not HAS_GRADIO or _RUNTIME_STATE["zerogpu_exhausted"]:
        return False

    for space_id in _DEFAULT_IMAGE_SPACES:
        if space_id in _RUNTIME_STATE["bad_spaces"]:
            continue
        client = _get_gradio_client(space_id)
        if not client:
            continue

        ep_name, kwargs = _build_gradio_kwargs(space_id, prompt, neg_prompt, seed, 720, 1280)
        if not ep_name:
            continue

        def _invoke():
            return client.predict(**kwargs, api_name=ep_name)

        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                res = ex.submit(_invoke).result(timeout=75)
            media = _extract_media_path(res, out_path.parent, out_path.stem)
            if media and _normalize_generated_image(media, out_path, 720, 1280):
                return True
        except FuturesTimeoutError:
            print(f"   ⏱️ ZeroGPU timeout ({space_id})", flush=True)
        except Exception as e:
            msg = str(e).lower()
            if "401" in msg or "403" in msg:
                _purge_invalid_hf_token("ZeroGPU predict 401")
                continue
            if any(k in msg for k in ("zerogpu", "quota", "exceeded", "too many requests", "rate limit")):
                with _STATE_LOCK:
                    if not _RUNTIME_STATE["zerogpu_exhausted"]:
                        print("   ⏸️ انتهت حصة ZeroGPU المؤقتة — التحويل الفوري إلى AI Horde المتوازي.", flush=True)
                    _RUNTIME_STATE["zerogpu_exhausted"] = True
                return False
            print(f"   ⚠️ ZeroGPU ({space_id}): {str(e)[:90]}", flush=True)
    return False


def _call_hf_inference_image(prompt: str, neg_prompt: str, out_path: Path) -> bool:
    hf_token = _get_hf_token()
    if not HAS_HF_HUB or not hf_token or _RUNTIME_STATE["hf_inference_disabled"]:
        return False
    try:
        client = InferenceClient(token=hf_token, timeout=50)
        img = client.text_to_image(
            prompt=prompt,
            negative_prompt=neg_prompt,
            model="black-forest-labs/FLUX.1-schnell",
            width=720,
            height=1280,
            num_inference_steps=4,
        )
        if isinstance(img, Image.Image):
            tmp = out_path.with_suffix(f".hf_{threading.get_ident()}.png")
            img.save(tmp)
            ok = _normalize_generated_image(tmp, out_path, 720, 1280)
            tmp.unlink(missing_ok=True)
            return ok
    except Exception as e:
        msg = str(e).lower()
        if any(k in msg for k in ("401", "402", "403", "credit", "payment", "subscribe", "unauthorized")):
            if "401" in msg or "403" in msg:
                _purge_invalid_hf_token("HF Inference 401")
            else:
                _RUNTIME_STATE["hf_inference_disabled"] = True
    return False


def _call_ai_horde_image(prompt: str, neg_prompt: str, seed: int, out_path: Path) -> bool:
    api_key = os.getenv("AI_HORDE_API_KEY", "").strip() or "0000000000"
    headers = {
        "apikey": api_key,
        "Client-Agent": "MalakAlMarifa:2.1:github-actions",
        "Content-Type": "application/json",
    }
    full_prompt = f"{prompt} ### {neg_prompt}"
    payload = {
        "prompt": full_prompt,
        "params": {
            "sampler_name": "k_euler_a",
            "width": 576,
            "height": 1024,
            "steps": 18,
            "cfg_scale": 7.0,
            "seed": str(seed),
            "n": 1,
        },
        "nsfw": False,
        "censor_nsfw": True,
        "r2": True,
        "models": ["AlbedoBase XL (SDXL)", "DreamShaper XL", "Deliberate 3.0", "stable_diffusion_xl"],
    }
    try:
        r = requests.post("https://aihorde.net/api/v2/generate/async", headers=headers, json=payload, timeout=20)
        if r.status_code not in (200, 202):
            return False
        job_id = r.json().get("id")
        if not job_id:
            return False

        deadline = time.time() + 115
        while time.time() < deadline:
            time.sleep(4)
            chk = requests.get(f"https://aihorde.net/api/v2/generate/check/{job_id}", headers=headers, timeout=15)
            if chk.status_code != 200:
                continue
            cdata = chk.json()
            if cdata.get("faulted"):
                return False
            if cdata.get("wait_time", 0) > 260 and not cdata.get("done"):
                return False
            if cdata.get("done"):
                st = requests.get(f"https://aihorde.net/api/v2/generate/status/{job_id}", headers=headers, timeout=20)
                if st.status_code == 200:
                    gens = st.json().get("generations") or []
                    if gens and gens[0].get("img"):
                        img_resp = requests.get(gens[0]["img"], timeout=30)
                        if img_resp.status_code == 200 and len(img_resp.content) > 5000:
                            tmp = out_path.with_suffix(f".horde_{threading.get_ident()}.webp")
                            tmp.write_bytes(img_resp.content)
                            ok = _normalize_generated_image(tmp, out_path, 720, 1280)
                            tmp.unlink(missing_ok=True)
                            return ok
                return False
    except Exception:
        pass
    return False


def _stylize_stock_painterly(img_bgr):
    if not HAS_CV2:
        return img_bgr
    try:
        smoothed = cv2.bilateralFilter(img_bgr, d=7, sigmaColor=55, sigmaSpace=55)
        stylized = cv2.stylization(smoothed, sigma_s=45, sigma_r=0.35)
        return cv2.addWeighted(smoothed, 0.58, stylized, 0.42, 0)
    except Exception:
        return img_bgr


def _fallback_stock_image(query: str, allow_humans: bool, out_path: Path, used_urls: set) -> bool:
    safe_q = query if (is_safe_query(query, allow_ordinary_humans=allow_humans) and not _FORBIDDEN_STOCK_RE.search(query)) else "starry night milky way"
    candidates: list[str] = []

    if PIXABAY_KEY:
        try:
            r = requests.get(
                "https://pixabay.com/api/",
                params={
                    "key": PIXABAY_KEY,
                    "q": safe_q,
                    "image_type": "all",
                    "safesearch": "true",
                    "per_page": 25,
                    "min_width": 640,
                },
                timeout=15,
            )
            if r.status_code == 200:
                hits = r.json().get("hits", [])
                random.shuffle(hits)
                for h in hits:
                    tags = h.get("tags", "")
                    if _FORBIDDEN_STOCK_RE.search(tags) or not is_safe_tag(tags, allow_ordinary_humans=allow_humans):
                        continue
                    u = h.get("largeImageURL") or h.get("webformatURL")
                    with _STATE_LOCK:
                        if u and u not in used_urls:
                            candidates.append(u)
                            break
        except Exception:
            pass

    if not candidates:
        try:
            r = requests.get(
                "https://api.openverse.org/v1/images/",
                headers={"User-Agent": "MalakAlMarifa/2.1"},
                params={
                    "q": safe_q,
                    "license_type": "commercial",
                    "mature": "false",
                    "filter_dead": "true",
                    "extension": "jpg,png",
                    "page_size": 15,
                },
                timeout=15,
            )
            if r.status_code == 200:
                res = r.json().get("results", [])
                random.shuffle(res)
                for item in res:
                    u = item.get("url")
                    tags_raw = item.get("tags") or []
                    tag_words = [t.get("name", "") if isinstance(t, dict) else str(t) for t in tags_raw]
                    safety_txt = f"{item.get('title', '')} {' '.join(tag_words)}".strip()
                    if not safety_txt or _FORBIDDEN_STOCK_RE.search(safety_txt):
                        continue
                    if is_safe_tag(safety_txt, allow_ordinary_humans=allow_humans):
                        with _STATE_LOCK:
                            if u and u not in used_urls:
                                candidates.append(u)
                                break
        except Exception:
            pass

    for u in candidates:
        try:
            resp = requests.get(u, headers={"User-Agent": "MalakAlMarifa/2.1"}, timeout=25)
            if resp.status_code == 200 and len(resp.content) > 10000:
                tmp = out_path.with_suffix(f".stock_{threading.get_ident()}.raw")
                tmp.write_bytes(resp.content)
                if _normalize_generated_image(tmp, out_path, 720, 1280, stylize=True):
                    tmp.unlink(missing_ok=True)
                    with _STATE_LOCK:
                        used_urls.add(u)
                    return True
                tmp.unlink(missing_ok=True)
        except Exception:
            continue
    return False


def _make_atmospheric_fallback(out_path: Path, mood: str, seed: int):
    rng = random.Random(seed)
    W, H = 720, 1280
    if mood == "cosmic_awe":
        c1, c2 = np.array([38, 20, 12]), np.array([85, 58, 22])
    elif mood == "storm_trial":
        c1, c2 = np.array([32, 28, 18]), np.array([75, 65, 45])
    else:
        c1, c2 = np.array([35, 32, 16]), np.array([58, 72, 90])

    grad = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None, None]
    arr = np.clip(c1 * (1.0 - grad) + c2 * grad, 0, 255).astype(np.uint8)
    arr = np.broadcast_to(arr, (H, W, 3)).copy()
    for _ in range(90):
        x, y = rng.randint(20, W - 20), rng.randint(20, H - 20)
        rad = rng.randint(1, 3)
        cv2.circle(arr, (x, y), rad, (120, 195, 240), -1)
    arr = cv2.GaussianBlur(arr, (5, 5), 0)
    cv2.imwrite(str(out_path), arr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])


def _normalize_generated_image(src_path: Path, dst_path: Path, W: int, H: int, stylize: bool = False) -> bool:
    try:
        with Image.open(src_path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            if min(im.size) < 320:
                return False
            im = ImageOps.fit(im, (W, H), method=Image.Resampling.LANCZOS)
            if stylize and HAS_CV2:
                bgr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
                bgr = _stylize_stock_painterly(bgr)
                cv2.imwrite(str(dst_path), bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 93])
            else:
                im.save(dst_path, "JPEG", quality=93)
        return dst_path.exists() and dst_path.stat().st_size > 15000
    except Exception:
        return False


def generate_and_guard_shot(
    shot: dict,
    episode_anchor: str,
    shot_id: str,
    work_dir: Path,
    used_urls: set,
) -> Path:
    cache_img = _cache_root() / f"keyframe_{shot_id}.jpg"
    if _share_enabled() and cache_img.exists() and cache_img.stat().st_size > 15000:
        return cache_img

    out_img = cache_img if _share_enabled() else (work_dir / f"keyframe_{shot_id}.jpg")
    allow_humans = bool(shot.get("allow_humans", False))
    mood = shot.get("mood", "cosmic_awe")
    mood_desc = MOOD_PALETTES[mood]["desc"]

    base_seed = int(hashlib.md5(shot_id.encode("utf-8")).hexdigest()[:8], 16) % 2147483000

    for attempt in range(3):
        seed = base_seed + attempt * 7919
        tighten = (
            ", completely uninhabited sacred environment, zero people, zero faces, zero silhouettes"
            if (attempt > 0 and not allow_humans)
            else (", clean composition, clear faces/silhouettes only in modest ancient attire" if attempt > 0 else "")
        )
        full_prompt = (
            f"{shot['prompt_en']}, {mood_desc}, {episode_anchor}, {STYLE_BIBLE_SUFFIX}{tighten}"
        )
        neg_prompt = BASE_NEGATIVE if allow_humans else STRICT_NO_HUMANS_NEGATIVE

        generated = (
            _call_zerogpu_image(full_prompt, neg_prompt, seed, out_img)
            or _call_hf_inference_image(full_prompt, neg_prompt, out_img)
            or _call_ai_horde_image(full_prompt, neg_prompt, seed, out_img)
        )
        if not generated:
            generated = _fallback_stock_image(
                shot.get("fallback_query", "starry night milky way"),
                allow_humans,
                out_img,
                used_urls,
            )

        if generated:
            ok, reason = _vision_guard_check(out_img, allow_humans=allow_humans)
            if ok:
                return out_img
            print(f"   🛡️ Vision Guard رفض {shot_id} (محاولة {attempt+1}): {reason}", flush=True)
            out_img.unlink(missing_ok=True)

    _make_atmospheric_fallback(out_img, mood, base_seed)
    return out_img


# ==============================================================================
# 4) ترقية الذروة إلى فيديو AI حقيقي (اختياري بعد اكتمال اللوحات)
# ==============================================================================
def try_upgrade_hero_i2v(
    img_path: Path,
    shot: dict,
    shot_id: str,
    target_dur: float,
    W: int,
    H: int,
    work_dir: Path,
) -> Path | None:
    if os.getenv("ENABLE_HERO_I2V", "1") != "1" or not HAS_GRADIO or _RUNTIME_STATE["zerogpu_exhausted"]:
        return None

    cached_i2v = _cache_root() / f"hero_i2v_{shot_id}_{W}x{H}.mp4"
    if _share_enabled() and cached_i2v.exists() and cached_i2v.stat().st_size > 25000:
        return cached_i2v

    out_mp4 = cached_i2v if _share_enabled() else (work_dir / f"hero_i2v_{shot_id}.mp4")
    motion_prompt = (
        f"gentle cinematic camera push and atmospheric motion, flowing mist and glowing light, "
        f"{shot['prompt_en']}, smooth filmic animation"
    )
    neg = BASE_NEGATIVE if shot.get("allow_humans") else STRICT_NO_HUMANS_NEGATIVE

    for space_id in _DEFAULT_I2V_SPACES:
        if space_id in _RUNTIME_STATE["bad_spaces"]:
            continue
        client = _get_gradio_client(space_id)
        if not client:
            continue
        ep_name, kwargs = _build_gradio_kwargs(space_id, motion_prompt, neg, 42, W, H, image_file=img_path)
        if not ep_name:
            continue

        def _invoke():
            return client.predict(**kwargs, api_name=ep_name)

        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                res = ex.submit(_invoke).result(timeout=140)
            vid = _extract_media_path(res, work_dir, f"raw_i2v_{shot_id}")
            if vid and vid.exists() and vid.stat().st_size > 25000:
                vf = (
                    f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                    f"fps=24,eq=contrast=1.08:saturation=1.10,vignette=PI/6,format=yuv420p,setsar=1"
                )
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(vid),
                        "-t", f"{target_dur:.3f}", "-vf", vf,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(out_mp4),
                    ],
                    check=True, capture_output=True, timeout=90,
                )
                if out_mp4.exists() and out_mp4.stat().st_size > 20000:
                    print(f"   🎞️ تمت ترقية لقطة الذروة {shot_id} إلى فيديو AI حقيقي عبر {space_id}", flush=True)
                    return out_mp4
        except Exception as e:
            msg = str(e).lower()
            if "401" in msg or "403" in msg:
                _purge_invalid_hf_token("I2V 401")
                continue
            if any(k in msg for k in ("zerogpu", "quota", "exceeded", "too many requests")):
                _RUNTIME_STATE["zerogpu_exhausted"] = True
                return None
            with _STATE_LOCK:
                _RUNTIME_STATE["bad_spaces"].add(space_id)
    return None


# ==============================================================================
# 5) محرك العمق Depth Anything V2 Small (ONNX/CPU) + تحريك كاميرا 3D حقيقي
# ==============================================================================
def _download_depth_onnx(depth_cache: Path) -> str | None:
    # 1) عبر huggingface_hub (بتوكن صالح إن وجد، أو token=False بالوضع العام عند 401)
    if HAS_HF_HUB:
        for tok in (_get_hf_token(), False):
            if tok is None and _RUNTIME_STATE["hf_token_invalid"]:
                continue
            for fname in ("onnx/model.onnx", "onnx/model_quantized.onnx"):
                try:
                    return hf_hub_download(
                        repo_id="onnx-community/depth-anything-v2-small",
                        filename=fname,
                        cache_dir=str(depth_cache),
                        token=tok,
                    )
                except Exception as e:
                    msg = str(e)
                    if ("401" in msg or "403" in msg) and tok:
                        _purge_invalid_hf_token("Depth Hub 401")
                        break
                    print(f"   ⚠️ تعذر تحميل Depth ({fname}): {msg[:80]}", flush=True)

    # 2) احتياط مباشر بدون توكن عبر HTTPS
    local_onnx = depth_cache / "depth_v2_small.onnx"
    if local_onnx.exists() and local_onnx.stat().st_size > 10_000_000:
        return str(local_onnx)
    for url in (
        "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx",
        "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model_quantized.onnx",
    ):
        try:
            r = requests.get(url, timeout=90, stream=True)
            if r.status_code == 200:
                with open(local_onnx, "wb") as f:
                    for ch in r.iter_content(65536):
                        f.write(ch)
                if local_onnx.stat().st_size > 10_000_000:
                    return str(local_onnx)
        except Exception:
            continue
    return None


def _get_depth_session():
    if _RUNTIME_STATE["depth_failed"] or not HAS_ORT:
        return None
    if _RUNTIME_STATE["depth_session"] is not None:
        return _RUNTIME_STATE["depth_session"]

    depth_cache = Path(tempfile.gettempdir()) / "depth_v2_cache"
    depth_cache.mkdir(parents=True, exist_ok=True)
    model_path = _download_depth_onnx(depth_cache)
    if model_path:
        try:
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = max(2, os.cpu_count() or 2)
            sess = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
            _RUNTIME_STATE["depth_session"] = sess
            print(f"🧊 Depth Anything V2 Small ONNX جاهز: {Path(model_path).name}", flush=True)
            return sess
        except Exception as e:
            print(f"   ⚠️ فشل تشغيل جلسة Depth ONNX: {str(e)[:80]}", flush=True)

    _RUNTIME_STATE["depth_failed"] = True
    return None


def compute_depth_map(img_path: Path, shot_id: str, W: int, H: int):
    cache_npy = _cache_root() / f"depth_{shot_id}_{W}x{H}.npy"
    if _share_enabled() and cache_npy.exists():
        try:
            arr = np.load(str(cache_npy))
            if arr.shape == (H, W):
                return arr.astype(np.float32)
        except Exception:
            pass

    bgr = cv2.imread(str(img_path))
    if bgr is None:
        return np.linspace(0.2, 0.9, H, dtype=np.float32)[:, None] * np.ones((1, W), dtype=np.float32)
    bgr = cv2.resize(bgr, (W, H), interpolation=cv2.INTER_LANCZOS4)

    sess = _get_depth_session()
    depth_01 = None
    if sess is not None:
        try:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            inp_img = cv2.resize(rgb, (518, 518), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            inp_norm = (inp_img - mean) / std
            tensor = np.transpose(inp_norm, (2, 0, 1))[None, ...].astype(np.float32)

            in_name = sess.get_inputs()[0].name
            out_name = sess.get_outputs()[0].name
            pred = sess.run([out_name], {in_name: tensor})[0]
            pred2d = np.squeeze(pred).astype(np.float32)

            d_min, d_max = float(pred2d.min()), float(pred2d.max())
            if d_max - d_min > 1e-5:
                norm2d = (pred2d - d_min) / (d_max - d_min)
                up = cv2.resize(norm2d, (W, H), interpolation=cv2.INTER_CUBIC)
                up = cv2.bilateralFilter(up.astype(np.float32), d=9, sigmaColor=0.18, sigmaSpace=14)
                up = cv2.GaussianBlur(up, (21, 21), 8.0)
                depth_01 = np.clip(up, 0.0, 1.0).astype(np.float32)
        except Exception as e:
            print(f"   ⚠️ خطأ استنتاج العمق: {str(e)[:70]}", flush=True)

    if depth_01 is None:
        depth_01 = np.linspace(0.08, 0.95, H, dtype=np.float32)[:, None] * np.ones((1, W), dtype=np.float32)

    if _share_enabled():
        try:
            np.save(str(cache_npy), depth_01)
        except Exception:
            pass
    return depth_01


def _init_particles(kind: str, W: int, H: int, seed: int):
    rng = np.random.default_rng(seed)
    if kind == "none":
        return None
    if kind == "rain":
        count = 110
        return {
            "kind": "rain",
            "x": rng.uniform(0, W, count),
            "y": rng.uniform(0, H, count),
            "vy": rng.uniform(14.0, 24.0, count),
            "vx": rng.uniform(-2.5, -1.2, count),
            "len": rng.uniform(16, 32, count),
            "alpha": rng.uniform(0.22, 0.45, count),
        }
    if kind == "embers":
        count = 45
        return {
            "kind": "embers",
            "x": rng.uniform(0, W, count),
            "y": rng.uniform(H * 0.25, H, count),
            "vy": rng.uniform(-2.4, -0.9, count),
            "vx": rng.uniform(-0.8, 0.8, count),
            "rad": rng.integers(2, 5, count),
            "phase": rng.uniform(0, 6.28, count),
        }
    if kind == "snow":
        count = 75
        return {
            "kind": "snow",
            "x": rng.uniform(0, W, count),
            "y": rng.uniform(0, H, count),
            "vy": rng.uniform(1.4, 3.2, count),
            "vx": rng.uniform(-0.6, 0.6, count),
            "rad": rng.integers(2, 5, count),
            "phase": rng.uniform(0, 6.28, count),
        }
    count = 36
    return {
        "kind": "gold_motes",
        "x": rng.uniform(0, W, count),
        "y": rng.uniform(0, H, count),
        "vy": rng.uniform(-0.8, -0.2, count),
        "vx": rng.uniform(-0.4, 0.5, count),
        "rad": rng.integers(2, 6, count),
        "phase": rng.uniform(0, 6.28, count),
    }


def _step_and_draw_particles(frame_bgr: np.ndarray, pstate: dict | None, frame_idx: int, W: int, H: int):
    if not pstate:
        return frame_bgr
    kind = pstate["kind"]
    overlay = frame_bgr.copy()

    if kind == "rain":
        pstate["x"] = (pstate["x"] + pstate["vx"]) % W
        pstate["y"] = (pstate["y"] + pstate["vy"]) % H
        for i in range(len(pstate["x"])):
            x0, y0 = int(pstate["x"][i]), int(pstate["y"][i])
            x1, y1 = int(x0 + pstate["vx"][i] * 1.4), int(y0 + pstate["len"][i])
            cv2.line(overlay, (x0, y0), (x1, y1), (225, 232, 240), 1, cv2.LINE_AA)
        return cv2.addWeighted(frame_bgr, 0.78, overlay, 0.22, 0)

    pstate["x"] = (pstate["x"] + pstate["vx"] + 0.3 * np.sin(frame_idx * 0.06 + pstate["phase"])) % W
    pstate["y"] = (pstate["y"] + pstate["vy"]) % H
    color = (95, 175, 255) if kind == "embers" else ((245, 248, 255) if kind == "snow" else (140, 215, 255))
    blend = 0.40 if kind == "embers" else 0.28
    for i in range(len(pstate["x"])):
        cx, cy = int(pstate["x"][i]), int(pstate["y"][i])
        r = int(pstate["rad"][i])
        cv2.circle(overlay, (cx, cy), r, color, -1, cv2.LINE_AA)
    return cv2.addWeighted(frame_bgr, 1.0 - blend, overlay, blend, 0)


def render_parallax_clip(
    img_path: Path,
    depth_map: np.ndarray,
    shot: dict,
    shot_id: str,
    render_dur: float,
    W: int,
    H: int,
    out_mp4: Path,
    fps: int = 24,
):
    bgr = cv2.imread(str(img_path))
    if bgr is None:
        _make_atmospheric_fallback(img_path, shot.get("mood", "cosmic_awe"), 123)
        bgr = cv2.imread(str(img_path))
    bgr = cv2.resize(bgr, (W, H), interpolation=cv2.INTER_LANCZOS4)

    mood_cfg = MOOD_PALETTES.get(shot.get("mood", "cosmic_awe"), MOOD_PALETTES["cosmic_awe"])
    bgr_f = bgr.astype(np.float32)
    tint = np.array(mood_cfg["tint_bgr"], dtype=np.float32)
    bgr_f = (bgr_f - 128.0) * mood_cfg["contrast"] + 128.0 + tint
    bgr_f = np.clip(bgr_f, 0, 255)

    far_mask = (1.0 - depth_map)[..., None] ** 1.8 * 0.14
    haze_color = np.array(mood_cfg["ray_color"], dtype=np.float32) * 0.42
    bgr_f = bgr_f * (1.0 - far_mask) + haze_color * far_mask

    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    nx = (xx - W * 0.5) / (W * 0.5)
    ny = (yy - H * 0.5) / (H * 0.5)
    vig = np.clip(1.0 - 0.24 * (nx * nx + ny * ny), 0.62, 1.0)[..., None]
    base_img = np.clip(bgr_f * vig, 0, 255).astype(np.uint8)

    depth_w = (0.18 + 0.82 * depth_map).astype(np.float32)
    cx, cy = W * 0.5, H * 0.5
    base_x = xx.astype(np.float32)
    base_y = yy.astype(np.float32)

    move = shot.get("camera_move", "dolly_in")
    particles = _init_particles(
        shot.get("particles", "gold_motes"),
        W, H,
        int(hashlib.md5(shot_id.encode("utf-8")).hexdigest()[:8], 16) % 99999,
    )

    n_frames = max(12, int(round(render_dur * fps)))
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps),
        "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-an", str(out_mp4),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    base_overscan = 1.075
    try:
        for f in range(n_frames):
            t = f / max(1, n_frames - 1)
            e = 0.28 * t + 0.72 * (0.5 - 0.5 * math.cos(math.pi * t))

            dz, dx_frac, dy_frac = 0.0, 0.0, 0.0
            if move == "dolly_in":
                dz = 0.058 * e
                dx_frac = 0.006 * math.sin(math.pi * t)
            elif move == "dolly_out":
                dz = 0.058 * (1.0 - e)
                dx_frac = -0.006 * math.sin(math.pi * t)
            elif move == "truck_left":
                dz = 0.022 * e
                dx_frac = 0.024 * (e - 0.5)
            elif move == "truck_right":
                dz = 0.022 * e
                dx_frac = -0.024 * (e - 0.5)
            elif move == "tilt_up":
                dz = 0.025 * e
                dy_frac = 0.022 * (0.5 - e)

            scale = (1.0 / base_overscan) * (1.0 - dz * depth_w)
            map_x = cx + (base_x - cx) * scale + (dx_frac * W) * depth_w
            map_y = cy + (base_y - cy) * scale + (dy_frac * H) * depth_w

            warped = cv2.remap(
                base_img, map_x, map_y,
                interpolation=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT_101,
            )
            if particles is not None:
                warped = _step_and_draw_particles(warped, particles, f, W, H)

            proc.stdin.write(warped.tobytes())
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        stderr_bytes = proc.stderr.read() if proc.stderr else b""
        ret = proc.wait(timeout=180)
        if ret != 0:
            raise RuntimeError(f"Parallax ffmpeg error: {stderr_bytes.decode('utf-8', 'ignore')[:300]}")


# ==============================================================================
# 6) تحضير ورندرة خلفيات المقاطع بالتوازي + xfade + مشاركة الكاش للنسخ الثلاث
# ==============================================================================
def _get_vid_dur(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def prepare_and_render_episode_chunks(
    meta: dict,
    plan: dict,
    W: int,
    H: int,
    work_dir: Path,
) -> dict[tuple[int, int], Path]:
    segments = meta.get("segments", [])
    used_urls = set()
    plan_key = plan["plan_key"]
    ep_anchor = plan["episode_anchor"]

    # المرحلة أ: تجهيز قائمة اللقطات وتوليدها بالتوازي (4 مسارات لتسريع AI Horde)
    jobs: list[tuple[int, int, int, dict, str, bool]] = []
    for seg_idx, seg in enumerate(segments):
        stype = seg.get("type", "narration")
        is_quran = (stype == "quran")
        for c_idx, chunk in enumerate(seg.get("chunks", [])):
            c_dur = float(chunk.get("duration", 0))
            if c_dur <= 0:
                continue
            shots = plan["chunks"].get(f"{seg_idx}_{c_idx}", [])
            for s_idx, sh in enumerate(shots):
                shot_id = f"{plan_key}_{seg_idx}_{c_idx}_{s_idx}"
                jobs.append((seg_idx, c_idx, s_idx, sh, shot_id, is_quran))

    print(f"🎨 توليد وفحص {len(jobs)} لوحة أنيميشن بالتوازي...", flush=True)
    shot_assets: dict[tuple[int, int, int], dict] = {}
    hero_candidates: list[tuple[int, int, int, float]] = []

    def _run_shot_job(job):
        seg_idx, c_idx, s_idx, sh, shot_id, is_quran = job
        img_path = generate_and_guard_shot(sh, ep_anchor, shot_id, work_dir, used_urls)
        return seg_idx, c_idx, s_idx, sh, shot_id, is_quran, img_path

    with ThreadPoolExecutor(max_workers=4) as pool_ex:
        for seg_idx, c_idx, s_idx, sh, shot_id, is_quran, img_path in pool_ex.map(_run_shot_job, jobs):
            shot_assets[(seg_idx, c_idx, s_idx)] = {
                "shot": sh,
                "shot_id": shot_id,
                "img_path": img_path,
                "is_quran": is_quran,
                "i2v_path": None,
            }
            if sh.get("is_hero_climax") and (not is_quran):
                hero_candidates.append((seg_idx, c_idx, s_idx, float(sh["duration"])))

    # المرحلة ب: ترقية حتى لقطتي ذروة لفيديو AI حقيقي إذا توفرت حصة ZeroGPU
    max_hero = max(0, int(os.getenv("MAX_HERO_I2V_SHOTS", "2")))
    upgraded = 0
    for seg_idx, c_idx, s_idx, sdur in hero_candidates:
        if upgraded >= max_hero:
            break
        asset = shot_assets[(seg_idx, c_idx, s_idx)]
        vid_path = try_upgrade_hero_i2v(
            asset["img_path"], asset["shot"], asset["shot_id"], sdur + 0.5, W, H, work_dir
        )
        if vid_path:
            asset["i2v_path"] = vid_path
            upgraded += 1

    # المرحلة ج: رندر مقاطع الخلفية النهائية لكل chunk
    chunk_bg_map: dict[tuple[int, int], Path] = {}

    for seg_idx, seg in enumerate(segments):
        stype = seg.get("type", "narration")
        is_quran = (stype == "quran")
        for c_idx, chunk in enumerate(seg.get("chunks", [])):
            c_dur = float(chunk.get("duration", 0))
            if c_dur <= 0:
                continue
            shots = plan["chunks"].get(f"{seg_idx}_{c_idx}", [])
            if not shots:
                continue

            target_dur = max(c_dur, 24.0) if (is_quran and _share_enabled()) else c_dur
            cache_chunk_mp4 = _cache_root() / f"chunk_bg_{plan_key}_{seg_idx}_{c_idx}_{W}x{H}.mp4"

            if _share_enabled() and cache_chunk_mp4.exists():
                existing_dur = _get_vid_dur(cache_chunk_mp4)
                if existing_dur >= c_dur - 0.05:
                    if abs(existing_dur - c_dur) <= 0.06:
                        chunk_bg_map[(seg_idx, c_idx)] = cache_chunk_mp4
                    else:
                        trimmed = work_dir / f"trim_bg_{seg_idx}_{c_idx}.mp4"
                        subprocess.run(
                            [
                                "ffmpeg", "-y", "-loglevel", "error",
                                "-i", str(cache_chunk_mp4), "-t", f"{c_dur:.3f}",
                                "-c:v", "copy", str(trimmed),
                            ],
                            check=True, capture_output=True, timeout=60,
                        )
                        chunk_bg_map[(seg_idx, c_idx)] = trimmed
                    continue

            shots_scaled = _normalize_shot_durations([dict(s) for s in shots], target_dur)
            n = len(shots_scaled)
            min_slot = min(s["duration"] for s in shots_scaled)
            fade_dur = min(0.40, min_slot * 0.35) if n > 1 else 0.0

            sub_clips: list[Path] = []
            for s_idx, sh in enumerate(shots_scaled):
                asset = shot_assets[(seg_idx, c_idx, s_idx)]
                slot_dur = float(sh["duration"])
                render_dur = slot_dur + (fade_dur if s_idx < n - 1 else 0.0)
                sub_mp4 = work_dir / f"sub_{seg_idx}_{c_idx}_{s_idx}.mp4"

                if asset["i2v_path"] and _get_vid_dur(asset["i2v_path"]) >= render_dur - 0.05:
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-loglevel", "error",
                            "-i", str(asset["i2v_path"]), "-t", f"{render_dur:.3f}",
                            "-c:v", "copy", str(sub_mp4),
                        ],
                        check=True, capture_output=True, timeout=60,
                    )
                else:
                    depth_map = compute_depth_map(asset["img_path"], asset["shot_id"], W, H)
                    render_parallax_clip(
                        asset["img_path"], depth_map, sh, asset["shot_id"], render_dur, W, H, sub_mp4, fps=24
                    )
                sub_clips.append(sub_mp4)

            final_chunk_bg = cache_chunk_mp4 if _share_enabled() else (work_dir / f"chunk_bg_{seg_idx}_{c_idx}.mp4")

            if len(sub_clips) == 1:
                shutil.copy2(sub_clips[0], final_chunk_bg)
            else:
                inputs = []
                for sc in sub_clips:
                    inputs.extend(["-i", str(sc)])
                filters = []
                last_tag = "[0:v]"
                offset = 0.0
                for k in range(len(sub_clips) - 1):
                    offset += float(shots_scaled[k]["duration"])
                    trans = XFADE_TYPES[(seg_idx + c_idx + k) % len(XFADE_TYPES)]
                    out_tag = "[vout]" if k == len(sub_clips) - 2 else f"[vx{k}]"
                    filters.append(
                        f"{last_tag}[{k+1}:v]xfade=transition={trans}:duration={fade_dur:.3f}:offset={offset:.3f}{out_tag}"
                    )
                    last_tag = out_tag

                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error",
                        *inputs,
                        "-filter_complex", ";".join(filters),
                        "-map", "[vout]",
                        "-t", f"{target_dur:.3f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-an", str(final_chunk_bg),
                    ],
                    check=True, capture_output=True, timeout=180,
                )

            if target_dur > c_dur + 0.05:
                trimmed = work_dir / f"trim_bg_{seg_idx}_{c_idx}.mp4"
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-i", str(final_chunk_bg), "-t", f"{c_dur:.3f}",
                        "-c:v", "copy", str(trimmed),
                    ],
                    check=True, capture_output=True, timeout=60,
                )
                chunk_bg_map[(seg_idx, c_idx)] = trimmed
            else:
                chunk_bg_map[(seg_idx, c_idx)] = final_chunk_bg

    return chunk_bg_map


# ==============================================================================
# 7) مولد المؤثرات الصوتية الطبيعية الحلال (-26dB، مع كتم 100% عند القرآن)
# ==============================================================================
def _gen_natural_noise_texture(kind: str, n_samples: int, sr: int = 24000, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n_samples).astype(np.float32)
    freqs = np.fft.rfftfreq(n_samples, d=1.0 / sr)
    spec = np.fft.rfft(white)

    if kind == "rain_thunder":
        filt = np.exp(-((freqs - 1200.0) / 1400.0) ** 2) + 0.35 * np.exp(-(freqs / 160.0) ** 2)
        env = 0.78 + 0.22 * np.sin(2 * np.pi * np.linspace(0, n_samples / sr, n_samples) * 0.17)
    elif kind == "ocean":
        filt = 1.0 / np.maximum(1.0, (freqs / 140.0) ** 1.1)
        t = np.linspace(0, n_samples / sr, n_samples, dtype=np.float32)
        env = 0.45 + 0.55 * (0.5 + 0.5 * np.sin(2 * np.pi * t * 0.14)) ** 1.6
    elif kind == "embers":
        filt = 1.0 / np.maximum(1.0, (freqs / 280.0) ** 0.9)
        crackles = (rng.random(n_samples) > 0.9985).astype(np.float32) * rng.uniform(0.3, 0.9, n_samples)
        base = np.fft.irfft(spec * filt, n=n_samples).astype(np.float32)
        base = base / max(1e-5, float(np.max(np.abs(base))))
        return np.clip(base * 0.55 + crackles * 0.45, -1.0, 1.0).astype(np.float32)
    else:
        filt = np.exp(-((freqs - 260.0) / 240.0) ** 2) + 0.25 * np.exp(-(freqs / 90.0) ** 2)
        t = np.linspace(0, n_samples / sr, n_samples, dtype=np.float32)
        env = 0.62 + 0.38 * np.sin(2 * np.pi * t * 0.11 + 0.7)

    shaped = np.fft.irfft(spec * filt, n=n_samples).astype(np.float32)
    shaped *= env.astype(np.float32)
    peak = float(np.max(np.abs(shaped)))
    if peak > 1e-6:
        shaped /= peak
    return shaped


def build_halal_ambient_mix(meta: dict, plan: dict, voice_audio_path: str, work_dir: Path) -> str:
    if np is None:
        return voice_audio_path
    try:
        sr = 24000
        total_dur = _get_vid_dur(Path(voice_audio_path))
        if total_dur <= 1.0:
            return voice_audio_path
        n_total = int(math.ceil(total_dur * sr))
        ambient = np.zeros(n_total, dtype=np.float32)

        cursor = 0.0
        fade_len = int(0.42 * sr)

        for seg_idx, seg in enumerate(meta.get("segments", [])):
            stype = seg.get("type", "")
            is_quran = (stype == "quran")
            for c_idx, chunk in enumerate(seg.get("chunks", [])):
                c_dur = float(chunk.get("duration", 0))
                if c_dur <= 0:
                    continue
                i0 = max(0, min(n_total, int(round(cursor * sr))))
                i1 = max(i0, min(n_total, int(round((cursor + c_dur) * sr))))
                cursor += c_dur

                if is_quran or (i1 - i0) < int(0.3 * sr):
                    continue

                shots = plan["chunks"].get(f"{seg_idx}_{c_idx}", [])
                sfx_kind = (shots[0].get("sfx_type") if shots else "desert_breeze") or "desert_breeze"
                if sfx_kind == "none":
                    continue

                tex = _gen_natural_noise_texture(sfx_kind, i1 - i0, sr=sr, seed=seg_idx * 101 + c_idx)
                win = np.ones(i1 - i0, dtype=np.float32)
                fl = min(fade_len, (i1 - i0) // 3)
                if fl > 0:
                    win[:fl] = np.linspace(0.0, 1.0, fl, dtype=np.float32)
                    win[-fl:] = np.linspace(1.0, 0.0, fl, dtype=np.float32)
                ambient[i0:i1] += tex * win * 0.048

        sfx_wav = work_dir / "halal_ambient.wav"
        pcm = np.clip(ambient * 32767.0, -32768, 32767).astype(np.int16)
        import wave
        with wave.open(str(sfx_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())

        mixed_wav = work_dir / "voice_plus_ambient.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(voice_audio_path),
                "-i", str(sfx_wav),
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]",
                "-map", "[aout]",
                "-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le",
                str(mixed_wav),
            ],
            check=True, capture_output=True, timeout=120,
        )
        if mixed_wav.exists() and mixed_wav.stat().st_size > 10000:
            return str(mixed_wav)
    except Exception as e:
        print(f"   ⚠️ تعذر خلط المؤثر الطبيعي (سيبقى الصوت الصافي): {str(e)[:80]}", flush=True)
    return voice_audio_path
