#!/usr/bin/env python3
"""
audio_mixer_audar.py — اختبار أولي لتوليد صوت عربي فصيح
باستخدام Audar-TTS-V1-Flash عبر llama-cpp-python
"""

import os
import re
import sys
from pathlib import Path

# ---------- الإعدادات ----------
MODEL_REPO = "audarai/Audar-TTS-V1-Flash"
GGUF_FILE  = "Audar-TTS-V1-Flash-Q8_0.gguf"
OUTPUT_DIR = Path("output")
REF_WAV    = Path("assets/audio/reference/sudais_ref.wav")

# نص تجريبي قصير — يختبر الفصحى والتشكيل والأرقام
DEFAULT_TEXT = (
    "الحَمدُ لِلَّهِ رَبِّ العالَمينَ. "
    "هذا اِختِبارٌ لِنِظامِ تَوليدِ الصَّوتِ العَرَبِيِّ الفَصيحِ. "
    "قَرَأَ الوَلَدُ ثَلاثَ كُتُبٍ في يَومٍ واحِدٍ."
)


def download_gguf() -> str:
    from huggingface_hub import hf_hub_download
    print(f"⬇️ تحميل {GGUF_FILE} من {MODEL_REPO} ...")
    path = hf_hub_download(
        repo_id=MODEL_REPO,
        filename=GGUF_FILE,
        local_dir="./models",
    )
    print(f"✅ تم التحميل: {path}")
    return path


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 1. تحديد النص المستهدف
    text = os.environ.get("TEST_TEXT", "").strip() or DEFAULT_TEXT
    print(f"📝 النص: {text[:80]}...")

    # 2. التحقق من المرجع الصوتي
    if not REF_WAV.exists():
        print(f"❌ المرجع الصوتي غير موجود: {REF_WAV}")
        sys.exit(1)
    print(f"🎤 المرجع: {REF_WAV}")

    # 3. تحميل النموذج
    gguf_path = download_gguf()

    # 4. تحميل المحرك
    print("🔧 تحميل llama-cpp-python ...")
    from llama_cpp import Llama
    llm = Llama(
        model_path=gguf_path,
        n_ctx=4096,
        n_gpu_layers=0,
        verbose=False,
    )

    # 5. اختبار بسيط: توليد نص عام للتأكد أن النموذج يعمل
    print("🧪 اختبار استجابة النموذج ...")
    response = llm(
        "user: Convert the text to speech:\n"
        f"<|TEXT_START|>{text}<|TEXT_END|>\n"
        "assistant:",
        max_tokens=64,
        temperature=0.65,
    )
    print("📤 الرد الخام:", response["choices"][0]["text"][:200])

    # ملاحظة: التوليد الكامل للصوت يحتاج neucodec —
    # سنضيفه في الخطوة التالية بعد التأكد أن llama-cpp يعمل.

    print("✅ اكتمل الاختبار الأولي.")
    # ملف علامة للتحقق من الـ artifact
    (OUTPUT_DIR / "test_marker.txt").write_text(
        f"Text tested: {text}\nModel: {GGUF_FILE}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
