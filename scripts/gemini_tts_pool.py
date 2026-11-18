from __future__ import annotations
import base64
import io
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


def _wait_for_rate_limit():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < 2.5:
        time.sleep(2.5 - elapsed)
    _last_request_time = time.time()


def _log(msg: str) -> None:
    print(f"[gemini-tts] {msg}", flush=True)


class GeminiKeyPool:
    def __init__(self) -> None:
        raw = os.getenv("GEMINI_API_KEYS", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
        self.keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not self.keys:
            raise RuntimeError("❌ لا مفاتيح Gemini")
        self.current_index = 0
        self.cooldowns: dict[str, float] = {}
        self.dead_keys: set[str] = set()
        _log(f"🔑 محمّل {len(self.keys)} مفتاح Gemini")

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
            raise RuntimeError(f"🚫 جميع مفاتيح Gemini ({len(self.keys)}) غير صالحة (401/403)!")
        for _ in range(10):
            key = self._available_key()
            if key:
                return key
            live_cooldowns = {k: v for k, v in self.cooldowns.items() if k not in self.dead_keys}
            if not live_cooldowns:
                raise RuntimeError("🚫 جميع مفاتيح Gemini الصالحة مستنفدة.")
            soonest = min(live_cooldowns.values())
            wait = max(1, int(soonest - time.time()))
            if wait > 900:
                raise RuntimeError("🚫 كل مفاتيح Gemini الصالحة في فترة تهدئة طويلة!")
            _log(f"⏳ انتظار {wait}ث لتوفر مفتاح...")
            time.sleep(min(wait + 1, 70))
        raise RuntimeError("❌ فشل الحصول على مفتاح متاح")

    def _advance(self) -> None:
        self.current_index = (self.current_index + 1) % len(self.keys)

    def advance(self) -> None:
        self._advance()

    def mark_failed(self, cooldown_seconds: int = 65) -> None:
        key = self.keys[self.current_index]
        self.cooldowns[key] = time.time() + cooldown_seconds
        self.current_index = (self.current_index + 1) % len(self.keys)

    def mark_dead(self, reason: str = "401") -> None:
        key = self.keys[self.current_index]
        idx = self.current_index + 1
        self.dead_keys.add(key)
        alive = len(self.keys) - len(self.dead_keys)
        _log(f"🗑️ استبعاد مفتاح معطوب #{idx}/{len(self.keys)} ({reason}) — المفاتيح السليمة المتبقية: {alive}")
        if alive <= 0:
            raise RuntimeError(f"🚫 جميع مفاتيح Gemini ({len(self.keys)}) غير صالحة!")
        self.current_index = (self.current_index + 1) % len(self.keys)


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


def discover_tts_models(prefer_lite: bool = False) -> list[str]:
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
        for _ in range(len(pool.keys)):
            key = pool.get_current()
            r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=20)
            if r.status_code == 401:
                pool.mark_dead("401")
                continue
            if r.status_code == 200:
                tts_models = []
                for m in r.json().get("models", []):
                    name = m.get("name", "").replace("models/", "")
                    methods = m.get("supportedGenerationMethods", [])
                    if "generateContent" in methods and "tts" in name.lower():
                        tts_models.append(name)
                if tts_models:
                    tts_models.sort(key=_tts_rank)
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps({"models": tts_models, "timestamp": time.time()}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    return tts_models
            break
    except Exception:
        pass
    return fallback


def _split_text(text: str, max_chars: int = 360) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!؟])\s+", text)
    chunks, buf = [], ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            sub_parts = re.split(r"(?<=[،؛,:])\s+", part)
            sub_buf = ""
            for sp in sub_parts:
                if len(sub_buf) + len(sp) + 1 <= max_chars:
                    sub_buf = f"{sub_buf} {sp}".strip()
                else:
                    if sub_buf:
                        chunks.append(sub_buf)
                    sub_buf = sp
            if sub_buf:
                chunks.append(sub_buf)
        elif len(buf) + len(part) + 1 <= max_chars:
            buf = f"{buf} {part}".strip()
        else:
            if buf:
                chunks.append(buf)
            buf = part
    if buf:
        chunks.append(buf)
    return chunks


def _clean_chunk(input_wav: Path, output_wav: Path) -> bool:
    try:
        af = (
            "silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_duration=0:start_threshold=-45dB,"
            "areverse,"
            "afade=t=in:st=0:d=0.015"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_wav), "-af", af, "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(output_wav)],
            check=True, capture_output=True, timeout=60,
        )
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(output_wav)],
            capture_output=True, text=True, timeout=10,
        )
        dur = float(probe.stdout.strip() or 0)
        if dur > 0.15:
            tmp_fade = output_wav.with_name(output_wav.stem + "_f.wav")
            st = max(0.0, dur - 0.025)
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(output_wav), "-af", f"afade=t=out:st={st:.3f}:d=0.025",
                 "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp_fade)],
                check=True, capture_output=True, timeout=60,
            )
            tmp_fade.replace(output_wav)
        return output_wav.exists() and output_wav.stat().st_size > 1000
    except Exception:
        return False


def _save_audio_bytes(raw: bytes, out_wav: Path) -> None:
    if raw[:4] == b"RIFF":
        with wave.open(io.BytesIO(raw), "rb") as w_in:
            sr = w_in.getframerate()
            nch = w_in.getnchannels()
            sw = w_in.getsampwidth()
            frames = w_in.readframes(w_in.getnframes())
        tmp = out_wav.with_suffix(".tmp.wav")
        with wave.open(str(tmp), "wb") as w_out:
            w_out.setnchannels(nch)
            w_out.setsampwidth(sw)
            w_out.setframerate(sr)
            w_out.writeframes(frames)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp), "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
            check=True, capture_output=True, timeout=30,
        )
        tmp.unlink(missing_ok=True)
    else:
        with wave.open(str(out_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(raw)


def _tts_single(text: str, out_wav: Path, voice: str, key: str) -> bool | str:
    clean_text = re.sub(r"[^\u0621-\u064A\u064B-\u065F\u0670\s،؛؟!.:]", "", text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    if not clean_text:
        return False

    for model in discover_tts_models():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {
            "contents": [{"parts": [{"text": clean_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}},
                },
            },
        }
        try:
            _wait_for_rate_limit()
            r = requests.post(url, json=body, timeout=60)
            if r.status_code == 401 or (r.status_code == 403 and "api key" in r.text.lower()):
                return "INVALID_KEY"
            if r.status_code == 429:
                return "RATE_LIMIT"
            if r.status_code in (400, 404):
                _log(f"⚠️ {model}: HTTP {r.status_code} - {r.text[:120]}")
                continue
            if r.status_code != 200:
                _log(f"⚠️ {model}: HTTP {r.status_code}")
                continue

            data = r.json()
            cands = data.get("candidates") or []
            if not cands:
                continue
            parts = (cands[0].get("content") or {}).get("parts") or []
            if not parts:
                continue
            inline = parts[0].get("inlineData") or {}
            audio_b64 = inline.get("data")
            if not audio_b64:
                continue

            raw = base64.b64decode(audio_b64)
            _save_audio_bytes(raw, out_wav)

            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(out_wav)],
                capture_output=True, text=True, timeout=10,
            )
            if float(probe.stdout.strip() or 0) < 0.25:
                out_wav.unlink(missing_ok=True)
                continue
            return True
        except Exception:
            continue
    return False


def gemini_tts_with_pool(text: str, out_mp3: str, voice: str = "Charon") -> str:
    out_mp3_path = Path(out_mp3)
    out_mp3_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = out_mp3_path.parent / f"_tts_{int(time.time())}"
    work_dir.mkdir(exist_ok=True)

    try:
        chunks = _split_text(text)
        pool = get_pool()
        wav_parts: list[Path] = []

        for idx, chunk in enumerate(chunks):
            wav_path = work_dir / f"part_{idx:03d}.wav"
            success = False

            for _ in range(max(len(pool.keys) * 2, 6)):
                key = pool.get_current()
                result = _tts_single(chunk, wav_path, voice, key)
                if result == "INVALID_KEY":
                    pool.mark_dead("401/403")
                    continue
                if result == "RATE_LIMIT":
                    pool.mark_failed(70)
                    continue
                if result is True:
                    pool._advance()
                    success = True
                    break
                pool.mark_failed(45)

            if not success:
                raise RuntimeError(f"❌ فشل توليد المقطع الصوتي {idx + 1}")

            cleaned_path = work_dir / f"part_{idx:03d}_clean.wav"
            wav_parts.append(cleaned_path if _clean_chunk(wav_path, cleaned_path) else wav_path)

        merged_wav = work_dir / "merged.wav"
        with wave.open(str(merged_wav), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(24000)
            for part in wav_parts:
                with wave.open(str(part), "rb") as inp:
                    out.writeframes(inp.readframes(inp.getnframes()))

        subprocess.run(
            ["ffmpeg", "-y", "-i", str(merged_wav), "-codec:a", "libmp3lame", "-b:a", "192k", str(out_mp3_path)],
            check=True, capture_output=True, timeout=180,
        )
        return str(out_mp3_path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
