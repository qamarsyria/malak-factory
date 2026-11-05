"""محرك SILMA TTS — بديل عربي مفتوح المصدر لـ Gemini.
يعمل محلياً في GitHub Actions بدون الحاجة إلى VPN أو مفاتيح API.
"""
import os, re, subprocess, tempfile, shutil
from pathlib import Path

# مسار الملف الصوتي المرجعي (اختياري: ضع ملف wav لصوت السديس أو أي قارئ)
REF_AUDIO = os.getenv("SILMA_REF_AUDIO", "") 
REF_TEXT = os.getenv("SILMA_REF_TEXT", "ويدقق النظر في القرآن الكريم وسائر الكتب السماوية ويتبع مسالك الرسل العظام عليهم الصلاة والسلام.")

def _clean_for_tts(text: str) -> str:
    """تنظيف النص مع الحفاظ على التشكيل (مهم جداً للنطق الصحيح)."""
    text = re.sub(r'[ۖۗۘۙۚۛ۞]', '', text)
    text = text.replace("۝", " ")
    text = re.sub(r'[a-zA-Z0-9*#@_\[\]\(\)]', '', text)
    return re.sub(r"\s+", " ", text).strip()

def silma_tts_synth(text: str, out_wav: str) -> bool:
    """يولّد ملف WAV باستخدام SILMA TTS. يعيد True عند النجاح."""
    try:
        from silma_tts.api import SilmaTTS
        tts = SilmaTTS()
        
        clean_text = _clean_for_tts(text)
        if not clean_text:
            return False

        # إذا لم يوجد مرجع صوتي، نستخدم الصوت الافتراضي للنموذج (إن وجد)
        ref_file = REF_AUDIO if REF_AUDIO and Path(REF_AUDIO).exists() else None
        
        wav, sr, _ = tts.infer(
            ref_file=ref_file,
            ref_text=REF_TEXT if ref_file else None,
            gen_text=clean_text,
            file_wave=out_wav,
            seed=None,
            speed=1.0
        )
        return Path(out_wav).exists() and Path(out_wav).stat().st_size > 2000
    except Exception as e:
        print(f"   ⚠️ SILMA TTS فشل: {str(e)[:150]}", flush=True)
        return False

def silma_tts_with_pool(text: str, out_mp3: str, voice: str = None, prefer_lite: bool = False) -> str:
    """
    توقيع متوافق مع gemini_tts_with_pool.
    يولّد WAV ثم يحوّله إلى MP3 عبر ffmpeg.
    """
    out_wav = out_mp3.replace(".mp3", "_raw.wav")
    
    if not silma_tts_synth(text, out_wav):
        raise RuntimeError(f"❌ SILMA TTS فشل للنص: {text[:60]}...")
    
    # تحويل إلى MP3
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", out_wav,
            "-ar", "24000", "-ac", "1",
            "-codec:a", "libmp3lame", "-b:a", "192k",
            out_mp3
        ], check=True, capture_output=True, timeout=60)
        return str(out_mp3)
    finally:
        # حذف WAV المؤقت
        if Path(out_wav).exists():
            Path(out_wav).unlink()
