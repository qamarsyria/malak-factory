"""تجربة معزولة: استنساخ تجويد السديس على صوت صناعي.
لا تلمس المشروع الأساسي. ترفع النتائج على تلغرام للمقارنة.
"""
import os
import subprocess
import tempfile
from pathlib import Path
import requests

# ═══════════════════════════════════════════════════════════
# 🌟 إعدادات التجربة
# ═══════════════════════════════════════════════════════════
SUDAIS_BASE = "https://everyayah.com/data/Abdurrahmaan_As-Sudais_192kbps"
TEST_AYAHS = [
    (1, 1),   # الفاتحة: 1
    (112, 1), # الإخلاص: 1
    (2, 255), # آية الكرسي
]

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

WORK = Path(tempfile.mkdtemp(prefix="test_tajweed_"))

def download_sudais(surah, ayah, out_path):
    """يحمّل تلاوة السديس للآية (للمقارنة)."""
    filename = f"{surah:03d}{ayah:03d}.mp3"
    url = f"{SUDAIS_BASE}/{filename}"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            Path(out_path).write_bytes(r.content)
            return True
    except Exception as e:
        print(f"⚠️ فشل تحميل السديس {surah}:{ayah}: {e}")
    return False

def generate_with_f5(text, ref_wav, out_wav):
    """يولّد الآية بـ F5-TTS مع استنساخ تجويد المرجع."""
    try:
        from f5_tts.api import F5TTS
        from huggingface_hub import hf_hub_download
        import soundfile as sf

        print(f"⏳ تحميل F5-TTS Arabic...")
        ckpt = hf_hub_download("NightPrince/MSA-F5-TTS-Arabic", "model.pt")
        vocab = hf_hub_download("NightPrince/MSA-F5-TTS-Arabic", "vocab.txt")

        tts = F5TTS(
            model="F5TTS_v1_Base",
            ckpt_file=ckpt,
            vocab_file=vocab,
            device="cpu",
        )

        print(f"🎙️ توليد F5-TTS (قد يستغرق دقائق)...")
        wav, sr, _ = tts.infer(
            ref_file=str(ref_wav),
            ref_text="",  # F5-TTS يكتشف النص تلقائياً
            gen_text=text,
            nfe_step=24,
        )
        sf.write(str(out_wav), wav, sr)
        del tts
        return True
    except Exception as e:
        print(f"❌ فشل F5-TTS: {e}")
        return False

def send_to_telegram(audio_path, caption):
    """يرفع ملف صوتي إلى تلغرام."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("⚠️ لا مفاتيح تلغرام")
        return
    try:
        with open(audio_path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendAudio",
                data={"chat_id": TELEGRAM_CHAT, "caption": caption},
                files={"audio": f},
                timeout=60,
            )
        if r.status_code == 200:
            print(f"✅ رُفع: {Path(audio_path).name}")
        else:
            print(f"⚠️ فشل الرفع: {r.text[:100]}")
    except Exception as e:
        print(f"⚠️ خطأ رفع: {e}")

def main():
    # 1. تحميل مرجع صوت السديس (نستخدم أول آية كمرجع)
    print("\n" + "="*50)
    print("🌟 تجربة استنساخ التجويد")
    print("="*50)

    ref_surah, ref_ayah = TEST_AYAHS[0]
    ref_path = WORK / "sudais_ref.mp3"
    if not download_sudais(ref_surah, ref_ayah, str(ref_path)):
        print("❌ فشل تحميل المرجع")
        return

    # تحويل MP3 إلى WAV (مطلوب لـ F5-TTS)
    ref_wav = WORK / "sudais_ref.wav"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(ref_path),
        "-ar", "24000", "-ac", "1", str(ref_wav)
    ], check=True, capture_output=True)

    # ارفع المرجع للمقارنة
    send_to_telegram(str(ref_wav), f"📖 المرجع: السديس {ref_surah}:{ref_ayah}")

    # 2. لكل آية: ولّد بـ F5-TTS وارفع للمقارنة
    for surah, ayah in TEST_AYAHS:
        print(f"\n--- الآية {surah}:{ayah} ---")

        # حمّل السديس للمقارنة (فقط عرض، ليس للاستخدام)
        original = WORK / f"original_{surah}_{ayah}.mp3"
        if download_sudais(surah, ayah, str(original)):
            send_to_telegram(str(original), f"🎧 السديس الأصلي: {surah}:{ayah}")

        # ولّد بـ F5-TTS
        gen_wav = WORK / f"f5_{surah}_{ayah}.wav"
        if generate_with_f5(
            f"سورة رقم {surah} آية {ayah}",  # مؤقتاً: بدل النص الفعلي
            str(ref_wav),
            str(gen_wav),
        ):
            # تحويل لـ MP3 للرفع
            gen_mp3 = WORK / f"f5_{surah}_{ayah}.mp3"
            subprocess.run([
                "ffmpeg", "-y", "-i", str(gen_wav),
                "-codec:a", "libmp3lame", "-b:a", "192k", str(gen_mp3)
            ], check=True, capture_output=True)
            send_to_telegram(str(gen_mp3), f"🤖 F5-TTS: {surah}:{ayah}")

    print("\n" + "="*50)
    print("✅ انتهت التجربة، تحقق من تلغرام للمقارنة")
    print("="*50)

if __name__ == "__main__":
    main()
