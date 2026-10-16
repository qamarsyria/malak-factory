#!/usr/bin/env python3
"""المرحلة 1: ترميز المرجع الصوتي وحفظ codes"""

import os
import json
from pathlib import Path

REF_WAV  = Path("assets/audio/reference/sudais_ref.wav")
REF_TEXT = "وَجَاءَتْ سَيَّارَةٌ فَأَرْسَلُوا وَارِدَهُمْ"
OUTPUT   = Path("output/ref_codes.json")


def load_audio_16k(wav_path: Path):
    import soundfile as sf
    import numpy as np
    import torch
    import torchaudio.functional as F

    data, sr = sf.read(str(wav_path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        wav = torch.from_numpy(data).unsqueeze(0)
        wav = F.resample(wav, orig_freq=sr, new_freq=16000)
        data = wav.squeeze(0).numpy()
    return torch.from_numpy(data).float().unsqueeze(0).unsqueeze(0)


def main():
    OUTPUT.parent.mkdir(exist_ok=True)

    print("🔧 تحميل NeuCodec ...")
    from neucodec import NeuCodec
    codec = NeuCodec.from_pretrained("neuphonic/neucodec").eval()

    print("🎤 ترميز المرجع ...")
    ref_tensor = load_audio_16k(REF_WAV)
    with __import__("torch").no_grad():
        ref_codes = codec.encode_code(ref_tensor).squeeze().tolist()

    result = {
        "ref_text": REF_TEXT,
        "ref_codes": ref_codes,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"✅ حفظ {len(ref_codes)} code في: {OUTPUT}")

    # تحرير الذاكرة
    del codec, ref_tensor
    print("🧹 تحرير الذاكرة")


if __name__ == "__main__":
    main()
