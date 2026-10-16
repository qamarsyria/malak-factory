#!/usr/bin/env python3
"""
audio_mixer_audar.py — التوليد الصوتي الكامل باستخدام Audar-TTS-V1-Flash
- يتجاوز torchaudio.load باستخدام soundfile
- يعيد أخذ العينات إلى 16kHz المطلوبة من NeuCodec
"""

import os
import re
import sys
from pathlib import Path

MODEL_REPO = "audarai/Audar-TTS-V1-Flash"
GGUF_FILE  = "Audar-TTS-V1-Flash-Q4_K_M.gguf"
OUTPUT_DIR = Path("output")
REF_WAV    = Path("assets/audio/reference/sudais_ref.wav")
REF_TEXT   = "وَجَاءَتْ سَيَّارَةٌ فَأَرْسَلُوا وَارِدَهُمْ"

DEFAULT_TEXT = (
    "الحَمدُ لِلَّهِ رَبِّ العالَمينَ. "
    "هذا اِختِبارٌ لِنِظامِ تَوليدِ الصَّوتِ العَرَبِيِّ الفَصيحِ."
)


def download_gguf():
    from huggingface_hub import hf_hub_download
    print(f"⬇️ تحميل {GGUF_FILE} ...")
    path = hf_hub_download(
        repo_id=MODEL_REPO,
        filename=GGUF_FILE,
        local_dir="./models",
    )
    print(f"✅ تم التحميل: {path}")
    return path


def load_audio_16k(wav_path: Path):
    """تحميل الصوت من ملف WAV وإعادة أخذ العينات إلى 16kHz مع قناة واحدة"""
    import soundfile as sf
    import numpy as np
    import torch

    # تحميل الصوت باستخدام soundfile (يتخطى torchaudio.load)
    data, sr = sf.read(str(wav_path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)  # mono

    # إعادة أخذ العينات إلى 16kHz باستخدام torch
    if sr != 16000:
        import torchaudio.functional as F
        wav = torch.from_numpy(data).unsqueeze(0)  # (1, T)
        wav = F.resample(wav, orig_freq=sr, new_freq=16000)
        data = wav.squeeze(0).numpy()

    # الشكل المطلوب: (1, 1, T)
    tensor = torch.from_numpy(data).float().unsqueeze(0).unsqueeze(0)
    return tensor


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    target_text = os.environ.get("TEST_TEXT", "").strip() or DEFAULT_TEXT
    print(f"📝 النص: {target_text[:80]}...")

    if not REF_WAV.exists():
        print(f"❌ المرجع الصوتي غير موجود: {REF_WAV}")
        sys.exit(1)

    gguf_path = download_gguf()

    print("🔧 تحميل llama-cpp-python ...")
    from llama_cpp import Llama
    llm = Llama(model_path=gguf_path, n_ctx=4096, n_gpu_layers=0, verbose=False)

    print("🔧 تحميل NeuCodec ...")
    from neucodec import NeuCodec
    codec = NeuCodec.from_pretrained("neuphonic/neucodec").eval()

    print("🎤 ترميز المرجع الصوتي ...")
    ref_tensor = load_audio_16k(REF_WAV)  # (1, 1, T)
    ref_codes = codec.encode_code(ref_tensor).squeeze().tolist()
    ref = "".join(f"<|speech_{c}|>" for c in ref_codes)
    print(f"📊 عدد codes المرجع: {len(ref_codes)}")

    prompt = (
        "user: Convert the text to speech:"
        f"<|REF_TEXT_START|>{REF_TEXT}<|REF_TEXT_END|>"
        f"<|REF_SPEECH_START|>{ref}<|REF_SPEECH_END|>"
        f"<|TARGET_TEXT_START|>{target_text}<|TARGET_TEXT_END|>"
        "\nassistant:<|TARGET_CODES_START|>"
    )

    print("🎙️ توليد الصوت ...")
    tce = llm.tokenize(b"<|TARGET_CODES_END|>", add_bos=False, special=True)[0]
    toks = llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)

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

    import torch
    import soundfile as sf
    wav = codec.decode_code(torch.tensor(codes)[None, None, :]).cpu().numpy()[0, 0, :]

    out_path = OUTPUT_DIR / "test_audar.wav"
    sf.write(str(out_path), wav, 24000, subtype="PCM_16")
    print(f"✅ تم إنشاء: {out_path} ({len(wav)/24000:.1f} ثانية)")


if __name__ == "__main__":
    main()
