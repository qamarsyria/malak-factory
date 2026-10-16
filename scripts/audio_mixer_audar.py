#!/usr/bin/env python3
"""
audio_mixer_audar.py
توليد صوت عربي فصيح باستخدام Audar-TTS-V1 عبر llama.cpp (GGUF)
- يعمل على CPU (GitHub Actions) بدون GPU
- يدعم استنساخ الصوت من مرجع قصير (5-15 ثانية)
- ينتج ملف WAV واحد لكل حلقة
"""

import os
import re
import sys
import json
import subprocess
import tempfile
from pathlib import Path

# ---------- الإعدادات ----------
MODEL_REPO = "audarai/Audar-TTS-V1-Flash"   # Flash أخف، يمكن تغييره إلى Turbo
GGUF_FILE = "Audar-TTS-V1-Flash-Q8_0.gguf"  # Q8_0 جودة عالية وحجم معقول
REFERENCE_WAV = "assets/audio/reference/sudais_ref.wav"  # 5-15 ثانية
OUTPUT_WAV = "output/episode_audar.wav"
SAMPLE_RATE = 24000

# ---------- أدوات مساعدة ----------
def download_gguf():
    """تحميل ملف GGUF من HuggingFace"""
    from huggingface_hub import hf_hub_download
    print(f"⬇️ تحميل {GGUF_FILE} من {MODEL_REPO} ...")
    path = hf_hub_download(
        repo_id=MODEL_REPO,
        filename=GGUF_FILE,
        local_dir="./models",
        local_dir_use_symlinks=False
    )
    print(f"✅ تم التحميل: {path}")
    return path

def clean_reference(wav_path: str) -> str:
    """تنظيف المرجع الصوتي: إزالة الصدى والضجيج المنخفض"""
    import soundfile as sf
    import numpy as np
    from scipy.signal import butter, sosfilt

    data, sr = sf.read(wav_path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    # High-pass filter عند 80Hz
    sos = butter(4, 80, btype="highpass", fs=sr, output="sos")
    data = sosfilt(sos, data).astype("float32")
    # Normalize
    data = data / (np.abs(data).max() + 1e-9) * 0.95
    # حفظ نسخة نظيفة
    clean_path = wav_path.replace(".wav", "_clean.wav")
    sf.write(clean_path, data, sr, subtype="PCM_16")
    print(f"🧹 تم تنظيف المرجع: {clean_path}")
    return clean_path

def normalize_arabic(text: str) -> str:
    """تطبيع النص العربي"""
    # إزالة التشكيل (Audar يتعامل مع النص الخام)
    text = re.sub(r"[\u064B-\u0652\u0670\u0640]", "", text)
    # إزالة الحروف اللاتينية
    text = re.sub(r"[A-Za-z]", "", text)
    # إزالة الرموز الغريبة
    text = re.sub(r"[^\u0600-\u06FF\s\.\,\!\?\:\؛]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def split_sentences(text: str) -> list:
    """تقسيم النص إلى جمل طبيعية"""
    parts = re.split(r"(?<=[\.\!\?\:\؛])\s+", text)
    merged, buf = [], ""
    for p in parts:
        buf = (buf + " " + p).strip() if buf else p
        if len(buf.split()) >= 4:
            merged.append(buf)
            buf = ""
    if buf:
        merged.append(buf)
    return merged

# ---------- محرك Audar (llama.cpp) ----------
def build_prompt(ref_wav: str, ref_text: str, target_text: str) -> str:
    """بناء الـ prompt المطلوب لـ Audar"""
    from neucodec import NeuCodec
    import torch

    codec = NeuCodec.from_pretrained("neuphonic/neucodec").eval()
    ref_codes = codec.encode_code(ref_wav).squeeze().tolist()
    ref = "".join(f"<|speech_{c}|>" for c in ref_codes)

    prompt = (
        "user: Convert the text to speech:"
        f"<|REF_TEXT_START|>{ref_text}<|REF_TEXT_END|>"
        f"<|REF_SPEECH_START|>{ref}<|REF_SPEECH_END|>"
        f"<|TARGET_TEXT_START|>{target_text}<|TARGET_TEXT_END|>"
        "\nassistant:<|TARGET_CODES_START|>"
    )
    return prompt

def generate_audio(llm, codec, prompt: str):
    """توليد الصوت من الـ prompt"""
    import torch
    import soundfile as sf

    tce = llm.tokenize(b"<|TARGET_CODES_END|>", add_bos=False, special=True)[0]
    toks = llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)

    ids = []
    for tid in llm.generate(
        toks, temp=0.65, top_k=40, top_p=0.9, repeat_penalty=1.1
    ):
        if tid == tce or len(ids) >= 2048:
            break
        ids.append(tid)

    text = "".join(
        llm.detokenize([t], special=True).decode("utf-8", "ignore")
        for t in ids
    )
    codes = [int(x) for x in re.findall(r"<\|speech_(\d+)\|>", text)]
    wav = codec.decode_code(torch.tensor(codes)[None, None, :]).cpu().numpy()[0, 0, :]
    return wav

# ---------- البرنامج الرئيسي ----------
def main():
    os.makedirs("output", exist_ok=True)

    # 1. تحميل النموذج
    gguf_path = download_gguf()

    # 2. تحضير المرجع
    ref_clean = clean_reference(REFERENCE_WAV)
    ref_text = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"  # نص الآية المرجعية

    # 3. تحميل النص المستهدف
    #    (يمكن استبداله بقراءة من ملف JSON الحلقة)
    with open("data/stories/yusuf/episode-01.json", "r", encoding="utf-8") as f:
        episode = json.load(f)

    all_text = " ".join(
        seg.get("text", "") for seg in episode.get("segments", [])
        if seg.get("type") == "narration"
    )
    all_text = normalize_arabic(all_text)
    sentences = split_sentences(all_text)

    print(f"📝 عدد الجمل: {len(sentences)}")

    # 4. تحميل المحرك
    from llama_cpp import Llama
    from neucodec import NeuCodec
    import soundfile as sf
    import numpy as np

    llm = Llama(
        model_path=gguf_path,
        n_ctx=4096,
        n_gpu_layers=0,   # CPU فقط
        verbose=False,
    )
    codec = NeuCodec.from_pretrained("neuphonic/neucodec").eval()

    # 5. توليد كل جملة
    all_audio = []
    for i, sent in enumerate(sentences):
        print(f"🎙️ جملة {i+1}/{len(sentences)}: {sent[:50]}...")
        prompt = build_prompt(ref_clean, ref_text, sent)
        wav = generate_audio(llm, codec, prompt)
        all_audio.append(wav)

    # 6. دمج المقاطع
    final = np.concatenate(all_audio)
    sf.write(OUTPUT_WAV, final, SAMPLE_RATE, subtype="PCM_16")
    print(f"✅ تم إنشاء: {OUTPUT_WAV} ({len(final)/SAMPLE_RATE:.1f} ثانية)")

if __name__ == "__main__":
    main()
