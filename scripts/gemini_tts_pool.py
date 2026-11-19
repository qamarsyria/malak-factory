#!/usr/bin/env python3
"""
scripts/gemini_tts_pool.py
==========================
محرّك توليد الصوت النقي قبل RVC:
- يستخدم Gemini 3.8 TTS بنبرة نقية بدون حشو style.
- يطبع بوضوح أي محرك اشتغل فعلاً، ويحفظ موجة 48kHz صافية بدون تقطيع حاد.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import time
import wave
from pathlib import Path
import requests

MODEL_CACHE_FILE = "out/tts_models_cache.json"
MODEL_CACHE_TTL = 12 * 3600
_last_request_time = 0.0


def _wait_for_rate_limit(min_gap: float = 2.0):
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)
    _last_request_time = time.time()


def _log(msg: str) -> None:
    print(f"[gemini-tts] {msg}", flush=True)


class GeminiKeyPool:
    def __init__(self) -> None:
        raw = (
            os.getenv("GEMINI_API_KEYS", "").strip()
            or os.getenv("GEMINI_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip()
        )
        keys = [k.strip() for k in re.split(r"[\s,;]+", raw) if len(k.strip()) > 20]
        self.keys = list(dict.fromkeys(keys))
        if not self.keys:
            raise RuntimeError("❌ لا مفاتيح GEMINI_API_KEYS صالحة.")
        self.current_index = 0
        self.cooldowns: dict[str, float] = {}
        self.dead_keys: set[str] = set()
        _log(f"🔑 محمّل {len(self.keys)} مفتاح Gemini")

    def alive_count(self) -> int:
        return len(self.keys) - len(self.dead_keys)

    def _available_key(self) -> str | None:
        now = time.time()
        for _ in range(len(self.keys)):
            key = self.keys[self.current_index]
            if key not in self.dead_keys and now >= self.cooldowns.get(key, 0):
                return key
            self.current_index = (self.current_index + 1) % len(self.keys)
        return None

    def get_current(self) -> str:
        if len(self.dead_keys) >= len(self.keys):
            raise RuntimeError("🚫 جميع مفاتيح Gemini غير صالحة!")
        for _ in range(6):
            key = self._available_key()
            if key:
                return key
            live_cooldowns = {k: v for k, v in self.cooldowns.items() if k not in self.dead_keys}
            if not live_cooldowns:
                raise RuntimeError("🚫 جميع مفاتيح Gemini مستنفدة.")
            soonest = min(live_cooldowns.values())
            wait = max(1, int(soonest - time.time()))
            if wait > 90:
                raise RuntimeError("🚫 كل مفاتيح Gemini في تهدئة طويلة.")
            _log(f"⏳ انتظار {wait}ث لتوفر مفتاح...")
            time.sleep(min(wait + 1, 25))
        raise RuntimeError("❌ تعذر الحصول على مفتاح متاح")

    def advance(self) -> None:
        self.current_index = (self.current_index + 1) % len(self.keys)

    def mark_failed(self, cooldown_seconds: int = 20) -> None:
        key = self.keys[self.current_index]
        self.cooldowns[key] = time.time() + cooldown_seconds
        self.advance()

    def mark_dead(self, reason: str = "401") -> None:
        key = self.keys[self.current_index]
        self.dead_keys.add(key)
        _log(f"🗑️ استبعاد مفتاح معطوب ({reason}) — السليمة: {self.alive_count()}")
        if self.alive_count() <= 0:
            raise RuntimeError("🚫 جميع مفاتيح Gemini معطوبة!")
        self.advance()


_pool: GeminiKeyPool | None = None


def get_pool() -> GeminiKeyPool:
    global _pool
    if _pool is None:
        _pool = GeminiKeyPool()
    return _pool


def _tts_rank(name: str) -> tuple:
    n = name.lower()
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
    ver = float(m.group(1)) if m else 0.0
    if "flash" in n and "lite" not in n:
        kind = 0
    elif "lite" in n:
        kind = 1
    elif "pro" in n:
        kind = 2
    else:
        kind = 3
    is_preview = 1 if "preview" in n else 0
    return (-ver, is_preview, kind, n)


def discover_tts_models() -> list[str]:
    cache_path = Path(MODEL_CACHE_FILE)
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            if (time.time() - cache.get("timestamp", 0)) < MODEL_CACHE_TTL and cache.get("models"):
                return cache["models"]
        except Exception:
            pass

    fallback = [
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
        "gemini-2.5-flash-preview-tts",
    ]
    try:
        pool = get_pool()
        for _ in range(min(5, len(pool.keys))):
            key = pool.get_current()
            r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=20)
            if r.status_code in (401, 403):
                pool.mark_dead(str(r.status_code))
                continue
            if r.status_code == 200:
                tts_models = []
                for m in r.json().get("models", []):
                    name = m.get("name", "").replace("models/", "")
                    methods = m.get("supportedGenerationMethods", [])
                    if "generateContent" in methods and "tts" in name.lower() and "pro" not in name.lower():
                        tts_models.append(name)
                if tts_models:
                    tts_models.sort(key=_tts_rank)
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps({"models": tts_models, "timestamp": time.time()}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    _log(f"🎙️ نماذج TTS المكتشفة: {tts_models}")
                    return tts_models
            break
    except Exception:
        pass
    return fallback


def _sanitize_tts_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ، ", text)
    clean = re.sub(r"[^\u0621-\u064A\u064B-\u065F\u0670\s،؛؟!.:…]", " ", text)
    return re.sub(r"\s+", " ", clean).strip()


def _save_audio_bytes(raw: bytes, out_wav: Path) -> None:
    if raw[:4] == b"RIFF":
        tmp = out_wav.with_suffix(".raw_in.wav")
        tmp.write_bytes(raw)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
            check=True, capture_output=True, timeout=30,
        )
        tmp.unlink(missing_ok=True)
    else:
        tmp = out_wav.with_suffix(".raw_pcm.wav")
        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(raw)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
            check=True, capture_output=True, timeout=30,
        )
        tmp.unlink(missing_ok=True)


def _trim_silence_only(input_wav: Path, output_wav: Path) -> bool:
    try:
        af = (
            "silenceremove=start_periods=1:start_duration=0:start_threshold=-52dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_duration=0:start_threshold=-52dB,"
            "areverse"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_wav), "-af", af,
             "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(output_wav)],
            check=True, capture_output=True, timeout=60,
        )
        return output_wav.exists() and output_wav.stat().st_size > 1000
    except Exception:
        return False


def _tts_single(text: str, out_wav: Path, voice: str, key: str, style: str = "") -> bool | str:
    cleaned = _sanitize_tts_text(text)
    if not cleaned:
        return False

    for model in discover_tts_models():
        part: dict = {"text": cleaned}
        if style and ("3.8" in model or "4." in model):
            part["speechMetadata"] = {"style": style}

        voice_variants = [
            {"prebuiltVoiceConfig": {"voiceName": voice}},
            {"voice": voice},
        ]

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        for vc in voice_variants:
            body = {
                "contents": [{"role": "user", "parts": [part]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": vc},
                },
            }
            try:
                _wait_for_rate_limit()
                r = requests.post(url, json=body, timeout=60)
                if r.status_code == 401 or (r.status_code == 403 and "api key" in r.text.lower()):
                    return "INVALID_KEY"
                if r.status_code == 429:
                    if "limit: 0" in r.text.lower() or '"limit":0' in r.text.lower():
                        _log(f"⚠️ {model} غير مجاني على هذا المفتاح")
                        break
                    return "RATE_LIMIT"
                if r.status_code == 400 and "speechMetadata" in part:
                    part = {"text": cleaned}
                    continue
                if r.status_code == 400:
                    _log(f"⚠️ {model} (400): {r.text[:140]}")
                    continue
                if r.status_code in (404, 503):
                    _log(f"⚠️ {model}: HTTP {r.status_code}")
                    break
                if r.status_code != 200:
                    _log(f"⚠️ {model}: HTTP {r.status_code}")
                    break

                data = r.json()
                cands = data.get("candidates") or []
                parts = (cands[0].get("content") or {}).get("parts") or [] if cands else []
                audio_b64 = None
                for p in parts:
                    inline = p.get("inlineData") or {}
                    if inline.get("data"):
                        audio_b64 = inline["data"]
                        break
                if not audio_b64:
                    break

                _save_audio_bytes(base64.b64decode(audio_b64), out_wav)
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(out_wav)],
                    capture_output=True, text=True, timeout=10,
                )
                if float(probe.stdout.strip() or 0) < 0.25:
                    out_wav.unlink(missing_ok=True)
                    break
                _log(f"✅ [Gemini TTS] model={model} | voice={voice}")
                return True
            except Exception as ex:
                _log(f"⚠️ {model} استثناء: {str(ex)[:80]}")
                break
    return False


def _edge_fallback(text: str, out_wav: Path, voice_gender: str = "male") -> bool:
    try:
        import edge_tts
        clean = _sanitize_tts_text(text)
        edge_voice = "ar-SA-ZariyahNeural" if voice_gender == "female" else "ar-SA-HamedNeural"
        _log("🚨🚨🚨 [WARNING - FALLBACK] تعذر Gemini TTS، تم التشغيل بـ Edge TTS (يسبب روبوتية مع RVC!)")
        tmp_mp3 = out_wav.with_suffix(".edge.mp3")

        async def _run():
            comm = edge_tts.Communicate(clean, edge_voice, rate="-6%")
            await comm.save(str(tmp_mp3))

        asyncio.run(_run())
        if tmp_mp3.exists() and tmp_mp3.stat().st_size > 1000:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(tmp_mp3), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
                check=True, capture_output=True, timeout=30,
            )
            tmp_mp3.unlink(missing_ok=True)
            return out_wav.exists()
    except Exception as e:
        _log(f"⚠️ فشل احتياط Edge TTS: {e}")
    return False


def gemini_tts_with_pool(
    text: str,
    out_path: str,
    voice: str = "Orus",
    style: str = "",
    gender_hint: str = "male",
) -> str:
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    work_dir = out_p.parent / f"_tts_{int(time.time() * 1000)}"
    work_dir.mkdir(exist_ok=True)

    try:
        raw_wav = work_dir / "raw.wav"
        clean_wav = work_dir / "clean.wav"
        success = False

        try:
            pool = get_pool()
            for _ in range(max(len(pool.keys), 4)):
                key = pool.get_current()
                res = _tts_single(text, raw_wav, voice, key, style=style)
                if res == "INVALID_KEY":
                    pool.mark_dead("401/403")
                    continue
                if res == "RATE_LIMIT":
                    pool.mark_failed(25)
                    continue
                if res is True:
                    pool.advance()
                    success = True
                    break
                pool.mark_failed(20)
        except Exception as e:
            _log(f"⚠️ تعذر Gemini TTS ({str(e)[:80]})...")

        if not success:
            if not _edge_fallback(text, raw_wav, voice_gender=gender_hint):
                raise RuntimeError("❌ فشل Gemini TTS واحتياط Edge TTS معاً.")

        target_wav = clean_wav if _trim_silence_only(raw_wav, clean_wav) else raw_wav

        if out_p.suffix.lower() == ".wav":
            shutil.copy2(target_wav, out_p)
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(target_wav), "-codec:a", "libmp3lame", "-b:a", "192k", str(out_p)],
                check=True, capture_output=True, timeout=60,
            )
        return str(out_p)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
