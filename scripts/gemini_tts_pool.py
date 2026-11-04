from __future__ import annotations
import base64, json, os, re, subprocess, time, wave
from pathlib import Path
import requests

MODEL_CACHE_FILE = "out/tts_models_cache.json"
MODEL_CACHE_TTL = 24 * 3600

_last_request_time = 0
def _wait_for_rate_limit():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < 5.0: time.sleep(5.0 - elapsed)
    _last_request_time = time.time()

def _log(msg: str) -> None: print(f"[gemini-tts] {msg}", flush=True)

class GeminiKeyPool:
    def __init__(self) -> None:
        raw = os.getenv("GEMINI_API_KEYS", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
        self.keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not self.keys: raise RuntimeError("❌ لا مفاتيح Gemini")
        self.current_index = 0
        self.cooldowns: dict[str, float] = {}
        _log(f"🔑 محمّل {len(self.keys)} مفتاح Gemini")

    def _available_key(self) -> str | None:
        now = time.time()
        for _ in range(len(self.keys)):
            key = self.keys[self.current_index]
            if now >= self.cooldowns.get(key, 0): return key
            self.current_index = (self.current_index + 1) % len(self.keys)
        return None

    def get_current(self) -> str:
        for _ in range(10): 
            key = self._available_key()
            if key: return key
            soonest = min(self.cooldowns.values())
            wait = max(1, int(soonest - time.time()))
            if wait > 3600: raise RuntimeError("🚫 كل مفاتيح Gemini مستنفدة لليوم!")
            print(f"   ⏳ انتظار {wait} ثانية لتجنب Rate Limit...", flush=True)
            time.sleep(wait + 2)
        raise RuntimeError("❌ فشل الحصول على مفتاح Gemini متاح")

    def _advance(self) -> None: self.current_index = (self.current_index + 1) % len(self.keys)
    def mark_failed(self, cooldown_seconds: int = 65) -> None:
        key = self.keys[self.current_index]
        self.cooldowns[key] = time.time() + cooldown_seconds
        self.current_index = (self.current_index + 1) % len(self.keys)

_pool: GeminiKeyPool | None = None
def get_pool() -> GeminiKeyPool:
    global _pool
    if _pool is None: _pool = GeminiKeyPool()
    return _pool

_MODEL_CACHE: list[str] | None = None

def discover_tts_models(prefer_lite: bool = False) -> list[str]:
    global _MODEL_CACHE
    if _MODEL_CACHE is not None: return _MODEL_CACHE
    try:
        if Path(MODEL_CACHE_FILE).exists():
            data = json.loads(Path(MODEL_CACHE_FILE).read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) < MODEL_CACHE_TTL:
                _MODEL_CACHE = data["models"]
                return _MODEL_CACHE
    except: pass

    p = get_pool()
    key = p.get_current()
    try:
        _wait_for_rate_limit()
        r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=30)
        if r.status_code != 200: return ["gemini-2.5-flash-preview-tts"]
        models = r.json().get("models", [])
        tts_models = [m.get("name", "").replace("models/", "") for m in models if "generateContent" in m.get("supportedGenerationMethods", []) and any(x in m.get("name", "").lower() for x in ["tts", "speech", "audio"])]
        if not tts_models: return ["gemini-2.5-flash-preview-tts"]

        def rank(name):
            m = re.search(r'(\d+\.\d+)', name)
            version = float(m.group(1)) if m else 0
            is_lite = 0 if "lite" in name.lower() else 1
            is_preview = 1 if "preview" in name.lower() else 0
            return (is_lite, -version, is_preview, name) if prefer_lite else (-version, is_lite, is_preview, name)

        tts_models.sort(key=rank)
        _MODEL_CACHE = tts_models
        Path(MODEL_CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(MODEL_CACHE_FILE).write_text(json.dumps({"timestamp": time.time(), "models": tts_models}), encoding="utf-8")
        return tts_models
    except: return ["gemini-2.5-flash-preview-tts"]

# 🌟 تقسيم النص إلى جمل قصيرة جداً لمنع الهلوسة
def _split_text(text: str, max_chars: int = 150) -> list[str]:
    text = text.strip()
    if not text: return []
    # تقسيم عند علامات الترقيم
    parts = re.split(r'(?<=[.!؟])\s+', text)
    chunks = []
    for part in parts:
        if len(part) <= max_chars:
            if part.strip(): chunks.append(part.strip())
        else:
            # إذا كانت الجملة طويلة، نقسمها عند الفواصل
            sub_parts = re.split(r'(?<=[،,])\s+', part)
            buf = ""
            for sp in sub_parts:
                if len(buf) + len(sp) < max_chars: buf += " " + sp
                else:
                    if buf.strip(): chunks.append(buf.strip())
                    buf = sp
            if buf.strip(): chunks.append(buf.strip())
    return chunks

def _clean_chunk(input_wav: Path, output_wav: Path) -> bool:
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(input_wav)], capture_output=True, text=True, timeout=10)
        duration = float(probe.stdout.strip() or 0)
        if duration <= 0.4:
            import shutil; shutil.copy(str(input_wav), str(output_wav)); return True
        cut_duration = duration - 0.12
        fade_start = max(0, cut_duration - 0.15)
        subprocess.run(["ffmpeg", "-y", "-i", str(input_wav), "-t", str(cut_duration), "-af", f"afade=t=out:st={fade_start}:d=0.15", "-ar", "24000", "-ac", "1", str(output_wav)], check=True, capture_output=True, timeout=60)
        return output_wav.exists() and output_wav.stat().st_size > 1000
    except: return False

def _tts_single(text: str, out_wav: Path, voice: str, key: str) -> bool | str:
    models = discover_tts_models()
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }
        # 🌟 لا يوجد systemInstruction هنا. قراءة حرفية.
        try:
            _wait_for_rate_limit()
            r = requests.post(url, json=body, timeout=180)
            if r.status_code == 429: return "RATE_LIMIT"
            if r.status_code in (400, 404): return False
            if r.status_code != 200: continue

            data = r.json()
            cands = data.get("candidates") or []
            if not cands: continue
            parts = (cands[0].get("content") or {}).get("parts") or []
            if not parts: continue
            inline = parts[0].get("inlineData") or {}
            audio_b64 = inline.get("data")
            if not audio_b64: continue

            raw = base64.b64decode(audio_b64)
            with wave.open(str(out_wav), "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
                wf.writeframes(raw)
            return True
        except: continue
    return False

def gemini_tts_with_pool(text: str, out_mp3: str, voice: str = "Charon") -> str:
    out_mp3_path = Path(out_mp3)
    out_mp3_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = out_mp3_path.parent / f"_tts_{int(time.time())}"
    work_dir.mkdir(exist_ok=True)

    chunks = _split_text(text)
    pool = get_pool()
    wav_parts: list[Path] = []

    for idx, chunk in enumerate(chunks):
        wav_path = work_dir / f"part_{idx:03d}.wav"
        success = False
        for attempt in range(len(pool.keys) * 5): 
            try: key = pool.get_current()
            except: time.sleep(60); continue
            try: result = _tts_single(chunk, wav_path, voice, key)
            except: pool.mark_failed(65); continue

            if result == "RATE_LIMIT": pool.mark_failed(65); continue
            if result is True: success = True; break
            pool.mark_failed(65)

        if not success: raise RuntimeError(f"❌ فشل توليد المقطع {idx + 1}")
        cleaned_path = work_dir / f"part_{idx:03d}_clean.wav"
        if _clean_chunk(wav_path, cleaned_path): wav_parts.append(cleaned_path)
        else: wav_parts.append(wav_path)

    merged_wav = work_dir / "merged.wav"
    with wave.open(str(merged_wav), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(24000)
        for part in wav_parts:
            with wave.open(str(part), "rb") as inp: out.writeframes(inp.readframes(inp.getnframes()))

    subprocess.run(["ffmpeg", "-y", "-i", str(merged_wav), "-codec:a", "libmp3lame", "-b:a", "192k", str(out_mp3_path)], check=True, capture_output=True, timeout=180)
    import shutil; shutil.rmtree(work_dir, ignore_errors=True)
    return str(out_mp3_path)
