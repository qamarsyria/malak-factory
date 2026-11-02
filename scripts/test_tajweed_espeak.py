# scripts/test_tajweed_espeak.py
import subprocess
from pathlib import Path
from quranic_phonemizer import Phonemizer

def test():
    print("🔧 تهيئة Phonemizer...")
    phonemizer = Phonemizer() 

    # آية اختبار (البقرة: 2)
    ayah_text = "ذَٰلِكَ الْكِتَابُ لَا رَيْبَ ۛ فِيهِ ۛ هُدًى لِّلْمُتَّقِينَ"
    print(f"📖 النص: {ayah_text}")

    print("🔊 توليد الفونيمات...")
    phonemes = phonemizer.phonemize(ayah_text)
    print(f"🔤 الفونيمات: {phonemes}")

    Path("out").mkdir(exist_ok=True)
    out_wav = "out/test_ayah.wav"

    print("🎙️ توليد الصوت عبر espeak-ng...")
    # تغليف الفونيمات بـ [[ ]] لكي يقرأها espeak-ng كفونيمات وليس كنص عادي
    espeak_input = f"[[{phonemes}]]"
    
    try:
        subprocess.run([
            "espeak-ng",
            "-v", "ar",
            "-s", "120",
            "-p", "40",
            "-w", out_wav,
            espeak_input
        ], check=True, capture_output=True, timeout=60)
        print(f"✅ تم التوليد بنجاح: {out_wav}")
    except FileNotFoundError:
        print("❌ خطأ: espeak-ng غير مثبت. قم بتشغيل: sudo apt-get install espeak-ng")
    except Exception as e:
        print(f"❌ خطأ: {e}")

if __name__ == "__main__":
    test()
