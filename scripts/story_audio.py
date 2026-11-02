import os, sys, json, subprocess, tempfile, shutil, re, time, asyncio
from pathlib import Path
import requests
import edge_tts

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import QURAN_API
from utilities import cleanup_dir, notify_telegram
from gemini_tts_pool import gemini_tts_with_pool

try:
    from quranic_phonemizer import Phonemizer
except ImportError as e:
    print(f"❌ فشل استيراد quranic_phonemizer: {e}")
    Phonemizer = None

TARGET_SR = "24000"
SILENCE_DUR = 0.4

_quran_phonemizer = None
def get_quran_phonemizer():
    global _quran_phonemizer
    if _quran_phonemizer is None and Phonemizer is not None:
        try:
            _quran_phonemizer = Phonemizer()
        except Exception as e:
            print(f"   ⚠️ فشل تهيئة Phonemizer: {e}")
    return _quran_phonemizer

def generate_quran_audio_espeak(uthmani_text: str, out_wav: str) -> bool:
    """يولد صوت الآية من الفونيمات التجويدية عبر espeak-ng."""
    try:
        pm = get_quran_phonemizer()
        if not pm:
            return False

        # 1. توليد الفونيمات (باستخدام quranic-phonemizer)
        phonemes = pm.phonemize(uthmani_text)
        if not phonemes:
            print("   ⚠️ لا توجد فونيمات")
            return False

        # 2. تمرير الفونيمات إلى espeak-ng
        espeak_input = f"[[{phonemes}]]"
        subprocess.run([
            "espeak-ng",
            "-v", "ar",
            "-s", "125",
            "-p", "45",
            "-a", "180",
            "-w", out_wav,
            espeak_input,
        ], check=True, capture_output=True, timeout=120)

        return Path(out_wav).exists() and Path(out_wav).stat().st_size > 2000
    except Exception as e:
        print(f"   ⚠️ espeak-ng فشل: {e}")
        return False

# ... (باقي الكود كما هو دون تغيير)
