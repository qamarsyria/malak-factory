"""محرك Gemini TTS — المصدر الوحيد للصوت (مع اكتشاف تلقائي للموديلات)."""
from __future__ import annotations

import base64
import os
import re
import subprocess
import time
import wave
from pathlib import Path

import requests

def _log(msg: str) -> None:
    print(f"[gemini-tts] {msg}", flush=True)

# ═══════════════════════════════════════════════════════
#  إدارة مفاتيح Gemini
# ═══════════════════════════════════════════════════════
class GeminiKeyPool:
    def __init__(self) -> None:
        raw = os.getenv("GEMINI_API_KEYS", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
        self.keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not self.keys:
            raise RuntimeError("❌ لا مفاتيح Gemini. أضف GEMINI_API_KEYS أو GEMINI_API_KEY")
        self.current_index = 0
        self.cooldowns: dict[str, float] = {}
        _log(f"🔑 محمّل {len(self.keys)} مفتاح Gemini")

    def _available_key(self) -> str | None:
        now = time.time()
        for _ in range(len(self.keys)):
            key = self.keys[self.current_index]
            if now >= self.cooldowns.get(key, 0):
                return key
            self.current_index = (self.current_index + 1) % len(self.keys)
        return None

    def get_current(self) -> str:
        key = self._available_key()
        if not key:
            soonest = min(self.cooldowns.values())
            wait = max(1, int(soonest - time.time()))
            if wait > 300:
                raise RuntimeError("🚫 كل المفاتيح مستنفدة لليوم!")
            _log(f"⏳ كل المفاتيح في تبريد — أنتظر {wait}ث")
            time.sleep(wait)
            key = self._available_key()
            if not key:
                raise RuntimeError("❌ كل مفاتيح Gemini مستنفدة")
        return key

    def _advance(self) -> None:
        self.current_index = (self.current_index + 1) % len(self.keys)

    def mark_failed(self, cooldown_seconds: int = 60) -> None:
        key = self.keys[self.current_index]
        self.cooldowns[key] = time.time() + cooldown_seconds
        _log(f"⚠️  مفتاح {key[:12]}... → تبريد {cooldown_seconds}ث")
        self._advance()

    def mark_daily_exhausted(self) -> None:
        key = self.keys[self.current_index]
        self.cooldowns[key] = time.time() + 86400
        _log(f"🚫 مفتاح {key[:12]}... → مستنفد لليوم")
        self._advance()

_pool: GeminiKeyPool | None = None
def get_pool() -> GeminiKeyPool:
    global _pool
    if _pool is None:
        _pool = GeminiKeyPool()
    return _pool

# ═══════════════════════════════════════════════════════
#  اكتشاف موديلات الصوت المتاحة (تلقائياً)
# ═══════════════════════════════════════════════════════
_MODEL_CACHE: list[str] | None = None

def discover_tts_models() -> list[str]:
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE

    p = get_pool()
    key = p.get_current()
    _log("🔍 نسأل Gemini عن موديلات الصوت المتاحة...")
    
    try:
        r = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=30
        )
        if r.status_code != 200:
            return ["gemini-2.5-flash-preview-tts"]
        
        models = r.json().get("models", [])
        tts_models = [
            m.get("name", "").replace("models/", "")
            for m in models
            if "generateContent" in m.get("supportedGenerationMethods", [])
            and any(x in m.get("name", "").lower() for x in ["tts", "speech", "audio"])
        ]
        
        if not tts_models:
            return ["gemini-2.5-flash-preview-tts"]
        
        def rank(name):
            m = re.search(r'(\d+\.\d+)', name)
            version = float(m.group(1)) if m else 0
            return (-version, name)
        
        tts_models.sort(key=rank)
        _log(f"✅ موديلات TTS المتاحة: {tts_models[:3]}")
        _MODEL_CACHE = tts_models
        return tts_models
    except Exception as e:
        _log(f"⚠️ فشل اكتشاف الموديلات: {e}")
        return ["gemini-2.5-flash-preview-tts"]

# ═══════════════════════════════════════════════════════
#  تقسيم النص
# ═══════════════════════════════════════════════════════
MAX_CHARS_PER_REQUEST = 1800

def _split_text(text: str, max_chars: int = MAX_CHARS_PER_REQUEST) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    parts = re.split(r"(?<=[.!?؟।\n])\s+", text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if len(buf) + len(part) + 1 <= max_chars:
            buf = f"{buf} {part}".strip()
        else:
            if buf:
                chunks.append(buf)
            while len(part) > max_chars:
                chunks.append(part[:max_chars])
                part = part[max_chars:]
            buf = part
    if buf:
        chunks.append(buf)
    return chunks

# ═══════════════════════════════════════════════════════
#  TTS لطلب واحد
# ═══════════════════════════════════════════════════════
def _tts_single(text: str, out_wav: Path, voice: str, key: str) -> bool | str:
    models = discover_tts_models()
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice}
                    }
                },
            },
        }
        try:
            r = requests.post(url, json=body, timeout=180)
            
            if r.status_code == 429:
                return "RATE_LIMIT"
            
            if r.status_code == 400:
                # خطأ 400 يعني أن البيانات المرسلة خاطئة (مثل اسم صوت غير مدعوم)، لا نحرق المفتاح!
                _log(f"   ❌ خطأ 400 (بيانات خاطئة) مع {model}: {r.text[:100]}")
                return False
                
            if r.status_code == 404:
                _log(f"   ⚠️ الموديل {model} غير متاح، جرب الذي بعده...")
                continue
                
            if r.status_code != 200:
                _log(f"   ⚠️ HTTP {r.status_code} من {model}")
                continue

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
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(raw)
            return True
            
        except Exception as e:
            _log(f"   ⚠️ استثناء مع {model}: {str(e)[:100]}")
            continue
            
    return False

# ═══════════════════════════════════════════════════════
#  الواجهة العامة
# ═══════════════════════════════════════════════════════
def gemini_tts_with_pool(text: str, out_mp3: str, voice: str = "Charon") -> str:
    out_mp3_path = Path(out_mp3)
    out_mp3_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = out_mp3_path.parent / f"_tts_{int(time.time())}"
    work_dir.mkdir(exist_ok=True)

    chunks = _split_text(text)
    _log(f"📝 {len(text)} حرف → {len(chunks)} مقطع")

    pool = get_pool()
    wav_parts: list[Path] = []

    for idx, chunk in enumerate(chunks):
        wav_path = work_dir / f"part_{idx:03d}.wav"
        success = False
        for attempt in range(len(pool.keys) * 2):
            key = pool.get_current()
            try:
                result = _tts_single(chunk, wav_path, voice, key)
            except Exception as e:
                _log(f"استثناء: {str(e)[:120]}")
                pool.mark_failed(30)
                continue

            if result == "RATE_LIMIT":
                pool.mark_daily_exhausted()
                continue
            if result is True:
                success = True
                break
            pool.mark_failed(60)

        if not success:
            raise RuntimeError(f"❌ فشل توليد المقطع {idx + 1}/{len(chunks)} بجميع الموديلات")

        wav_parts.append(wav_path)
        _log(f"✅ مقطع {idx + 1}/{len(chunks)}")

    # دمج الملفات
    merged_wav = work_dir / "merged.wav"
    with wave.open(str(merged_wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        for part in wav_parts:
            with wave.open(str(part), "rb") as inp:
                out.writeframes(inp.readframes(inp.getnframes()))

    # تحويل إلى MP3
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(merged_wav),
         "-codec:a", "libmp3lame", "-q:a", "4", str(out_mp3_path)],
        check=True, capture_output=True, timeout=180,
    )

    # تنظيف
    for f in work_dir.glob("*"):
        f.unlink(missing_ok=True)
    work_dir.rmdir()

    _log(f"🎉 تم: {out_mp3_path}")
    return str(out_mp3_path)

if __name__ == "__main__":
    sample = "بسم الله الرحمن الرحيم. الحمد لله رب العالمين."
    gemini_tts_with_pool(sample, "out/test_pool.mp3", voice="Charon")
