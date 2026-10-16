"""مجمع مفاتيح Gemini TTS — يتناوب بين عدة مفاتيح مع تقسيم النص الطويل."""
from __future__ import annotations

import base64
import os
import re
import subprocess
import time
import wave
from pathlib import Path

import requests

GEMINI_TTS_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash-preview-tts:generateContent"
)

# حد آمن لطول النص في الطلب الواحد (Gemini TTS)
MAX_CHARS_PER_REQUEST = 1800

VALID_VOICES = {
    "Charon", "Puck", "Kore", "Fenrir", "Aoede",
    "Leda", "Orus", "Zephyr", "Enceladus", "Iapetus",
}


def _log(msg: str) -> None:
    print(f"[gemini-tts] {msg}", flush=True)


class GeminiKeyPool:
    """يدير عدة مفاتيح Gemini API مع تبريد تلقائي عند الفشل."""

    def __init__(self) -> None:
        raw = os.getenv("GEMINI_API_KEYS", "").strip()
        if not raw:
            single = os.getenv("GEMINI_API_KEY", "").strip()
            raw = single
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
            # كل المفاتيح في تبريد — ننتظر أقرب واحد
            soonest = min(self.cooldowns.values())
            wait = max(1, int(soonest - time.time()))
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


# ─────────────────────────── تقسيم النص ───────────────────────────

def _split_text(text: str, max_chars: int = MAX_CHARS_PER_REQUEST) -> list[str]:
    """يقسم النص على حدود الجمل دون كسر المعنى."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    # نقسم على علامات الوقف العربية والإنجليزية
    parts = re.split(r"(?<=[.!?؟।\n])\s+", text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if len(buf) + len(part) + 1 <= max_chars:
            buf = f"{buf} {part}".strip()
        else:
            if buf:
                chunks.append(buf)
            # إذا كانت الجملة نفسها أطول من الحد، نقسمها قسراً
            while len(part) > max_chars:
                chunks.append(part[:max_chars])
                part = part[max_chars:]
            buf = part
    if buf:
        chunks.append(buf)
    return chunks


# ─────────────────────────── TTS لطلب واحد ───────────────────────────

def _tts_single(text: str, out_wav: Path, voice: str, key: str) -> bool:
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
    r = requests.post(
        f"{GEMINI_TTS_URL}?key={key}",
        json=body,
        timeout=180,
    )

    if r.status_code == 429:
        return "RATE_LIMIT"

    if r.status_code != 200:
        _log(f"HTTP {r.status_code}: {r.text[:200]}")
        return False

    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        _log(f"لا candidates: {str(data)[:200]}")
        return False

    parts = (cands[0].get("content") or {}).get("parts") or []
    if not parts:
        return False

    inline = parts[0].get("inlineData") or {}
    audio_b64 = inline.get("data")
    if not audio_b64:
        _log("لا inlineData.data في الاستجابة")
        return False

    raw = base64.b64decode(audio_b64)
    # Gemini TTS يعيد PCM 16-bit mono @ 24000 Hz
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(raw)
    return True


# ─────────────────────────── الواجهة العامة ───────────────────────────

def gemini_tts_with_pool(text: str, out_mp3: str, voice: str = "Charon") -> str:
    """
    يولّد MP3 من النص باستخدام Gemini TTS مع مجمع المفاتيح.
    يقسم النص الطويل تلقائياً ويدمج النتائج.
    """
    if voice not in VALID_VOICES:
        _log(f"⚠️  صوت '{voice}' غير معروف، أستخدم Charon")
        voice = "Charon"

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
            raise RuntimeError(f"فشل توليد المقطع {idx + 1}/{len(chunks)}")

        wav_parts.append(wav_path)
        _log(f"✅ مقطع {idx + 1}/{len(chunks)}")

    # دمج كل ملفات WAV في ملف واحد
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
    # اختبار سريع
    sample = "بسم الله الرحمن الرحيم. الحمد لله رب العالمين."
    gemini_tts_with_pool(sample, "out/test_pool.mp3", voice="Charon")
