#!/usr/bin/env python3
"""المرحلة 2: توليد codes الصوتية للنص المستهدف عبر llama.cpp"""

import os
import re
import json
import sys
from pathlib import Path

MODEL_REPO = "audarai/Audar-TTS-V1-Flash"
GGUF_FILE  = "Audar-TTS-V1-Flash-Q4_K_M.gguf"
REF_JSON   = Path("output/ref_codes.json")
TARGET_JSON = Path("output/target_codes.json")

# حد أقصى لعدد codes المرجع (50 code = 1 ثانية، 300 = 6 ثواني)
MAX_REF_CODES = 300

DEFAULT_TEXT = (
    "الحَمدُ لِلَّهِ رَبِّ العالَمينَ. "
    "هذا اِختِبارٌ لِنِظامِ تَوليدِ الصَّوتِ العَرَبِيِّ الفَصيحِ."
)


def download_gguf():
    from huggingface_hub import hf_hub_download
    print(f"⬇️ تحميل {GGUF_FILE} ...")
    path = hf_hub_download(repo_id=MODEL_REPO, filename=GGUF_FILE, local_dir="./models")
    print(f"✅ تم التحميل: {path}")
    return path


def main():
    ref_data = json.loads(REF_JSON.read_text(encoding="utf-8"))
    ref_text = ref_data["ref_text"]
    ref_codes_all = ref_data["ref_codes"]

    # اقتصاص المرجع إلى MAX_REF_CODES
    if len(ref_codes_all) > MAX_REF_CODES:
        print(f"✂️ اقتصاص المرجع من {len(ref_codes_all)} إلى {MAX_REF_CODES} code")
        ref_codes = ref_codes_all[:MAX_REF_CODES]
    else:
        ref_codes = ref_codes_all

    ref = "".join(f"<|speech_{c}|>" for c in ref_codes)
    print(f"📖 استخدام {len(ref_codes)} code المرجع")

    target_text = os.environ.get("TEST_TEXT", "").strip() or DEFAULT_TEXT
    print(f"📝 النص المستهدف: {target_text[:80]}...")

    gguf_path = download_gguf()

    print("🔧 تحميل llama-cpp-python ...")
    from llama_cpp import Llama
    llm = Llama(
        model_path=gguf_path,
        n_ctx=16384,        # ← زيادة كبيرة
        n_batch=512,        # ← معالجة على دفعات
        n_gpu_layers=0,
        verbose=False,
    )

    prompt = (
        "user: Convert the text to speech:"
        f"<|REF_TEXT_START|>{ref_text}<|REF_TEXT_END|>"
        f"<|REF_SPEECH_START|>{ref}<|REF_SPEECH_END|>"
        f"<|TARGET_TEXT_START|>{target_text}<|TARGET_TEXT_END|>"
        "\nassistant:<|TARGET_CODES_START|>"
    )

    print("🎙️ توليد ...")
    tce = llm.tokenize(b"<|TARGET_CODES_END|>", add_bos=False, special=True)[0]
    toks = llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)
    print(f"📊 طول الـ prompt: {len(toks)} token")

    if len(toks) >= 16000:
        print(f"❌ الـ prompt كبير جداً: {len(toks)} token")
        sys.exit(1)

    ids = []
    for tid in llm.generate(toks, temp=0.7, top_k=20, top_p=0.8, repeat_penalty=1.1):
        if tid == tce or len(ids) >= 4096:
            break
        ids.append(tid)

    print(f"📊 عدد التوكنز المولدة: {len(ids)}")

    text = "".join(
        llm.detokenize([t], special=True).decode("utf-8", "ignore")
        for t in ids
    )
    codes = [int(x) for x in re.findall(r"<\|speech_(\d+)\|>", text)]
    print(f"🎵 عدد الـ codes الصوتية: {len(codes)}")

    if not codes:
        print(f"❌ لم يتم توليد codes. النص الخام: {text[:300]}")
        sys.exit(1)

    TARGET_JSON.write_text(json.dumps({"codes": codes}), encoding="utf-8")
    print(f"✅ حفظ في: {TARGET_JSON}")


if __name__ == "__main__":
    main()
