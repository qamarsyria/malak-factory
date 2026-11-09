from __future__ import annotations
import base64, json, os, re, subprocess, time, wave
from pathlib import Path
import requests

MODEL_CACHE_FILE = "out/tts_models_cache.json"
MODEL_CACHE_TTL = 24 * 3600

LANGUAGE_CODE = "ar-XA"

_last_request_time = 0
def _wait_for_rate_limit():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < 3.0: time.sleep(3.0 - elapsed)
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
            if not self.cooldowns:
                raise RuntimeError("🚫 جميع المفاتيح مستنفدة أو محظورة.")
            soonest = min(self.cooldowns.values())
            wait = max(1, int(soonest - time.time()))
            if wait > 3600: raise RuntimeError("🚫 كل مفاتيح Gemini مستنفدة لليوم!")
            time.sleep(wait + 2)
        raise RuntimeError("❌ فشل الحصول على مفتاح")

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

def discover_tts_models(prefer_lite: bool = False) -> list[str]:
    cache_path = Path(MODEL_CACHE_FILE)
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            age = time.time() - cache.get("timestamp", 0)
            if age < MODEL_CACHE_TTL and cache.get("models"):
                return cache["models"]
        except: pass
    
    try:
        key = get_pool().get_current()
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            all_models = r.json().get("models", [])
            tts_models = []
            for m in all_models:
                name = m.get("name", "").replace("models/", "")
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods and "tts" in name.lower():
                    tts_models.append(name)
            if tts_models:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps({"models": tts_models, "timestamp": time.time()}), encoding="utf-8")
                return tts_models
    except: pass
    return ["gemini-2.5-flash-preview-tts"]

def _split_text(text: str, max_chars: int = 150) -> list[str]:
    text = text.strip()
    if not text: return []
    parts = re.split(r'(?<=[.!؟])\s+', text)
    chunks = []
    for part in parts:
        if len(part) <= max_chars:
            if part.strip(): chunks.append(part.strip())
        else:
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
    # 🌟 فلترة صارمة: مسح كل رموز التجويد والحروف غير العربية
    clean_text = re.sub(r'[\u06D6-\u06ED]', ' ', text)
    clean_text = re.sub(r'[^\u0621-\u064A\u064B-\u0652\u0670\s]', '', clean_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    
    if not clean_text: return False

    models = discover_tts_models()
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {
            # 🌟 أمر صارم لجيميني: اقرأ فقط ولا تتحدث بأي لغة أخرى
            "systemInstruction": {
                "parts": [{
                    "text": "أنت قارئ قرآن. اقرأ النص العربي فقط بصوت ترتيل هادئ. لا تضف أي شرح أو ترجمة أو تعليق. لا تتحدث بأي لغة أخرى غير العربية. انطق النص فقط كما هو."
                }]
            },
            "contents": [{"parts": [{"text": clean_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "languageCode": LANGUAGE_CODE,
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}},
                },
            },
        }
        try:
            _wait_for_rate_limit()
            r = requests.post(url, json=body, timeout=45) 
            if r.status_code == 429: return "RATE_LIMIT"
            if r.status_code in (400, 404): continue
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
            
            try:
                probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(out_wav)], capture_output=True, text=True, timeout=10)
                audio_dur = float(probe.stdout.strip() or 0)
                if audio_dur < 0.3:
                    out_wav.unlink(missing_ok=True)
                    continue
            except: pass
            
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
        
        for attempt in range(len(pool.keys) * 3): 
            try: key = pool.get_current()
            except Exception as e: 
                time.sleep(10); continue
            try: result = _tts_single(chunk, wav_path, voice, key)
            except: pool.mark_failed(65); continue

            if result == "RATE_LIMIT": 
                pool.mark_failed(65); continue
            if result is True: 
                success = True; break
            pool.mark_failed(65)

        if not success: 
            raise RuntimeError(f"❌ فشل توليد المقطع {idx + 1}")

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
