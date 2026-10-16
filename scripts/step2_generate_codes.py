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

MAX_REF_CODES = 600      # مرجع أطول لدقة أعلى
MAX_NEW_TOKENS = 1000    # كافٍ لجملة كاملة
LOOP_WINDOW = 15         # نافذة كشف التكرار

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


def has_loop(ids, window=LOOP_WINDOW):
    """كشف حلقة التكرار: آخر window token متطابقة"""
    if len(ids) < window:
        return False
    return len(set(ids[-window:])) == 1


def main():
    ref_data = json.loads(REF_JSON.read_text(encoding="utf-8"))
    ref_text = ref_data["ref_text"]
    ref_codes_all = ref_data["ref_codes"]

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
        n_ctx=16384,
        n_batch=512,
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

    ids = []
    stopped_by = "max_tokens"
    for tid in llm.generate(
        toks,
        temp=0.5,              # ← أقل لتقليل العشوائية
        top_k=20,
        top_p=0.8,
        repeat_penalty=1.3,    # ← عقوبة أعلى لمنع التكرار
    ):
        if tid == tce:
            stopped_by = "stop_token"
            break
        if len(ids) >= MAX_NEW_TOKENS:
            stopped_by = "max_tokens"
            break
        ids.append(tid)
        if has_loop(ids):
            stopped_by = f"loop_detected({LOOP_WINDOW} نفس الـ token)"
            print(f"🔁 توقف بسبب: {stopped_by}")
            # احذف التكرار من النهاية
            ids = ids[:-LOOP_WINDOW]
            break

    print(f"🛑 توقف التوليد بسبب: {stopped_by}")
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
