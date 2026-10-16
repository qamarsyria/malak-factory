#!/usr/bin/env python3
"""المرحلة 3: فك ترميز codes إلى WAV"""

import json
from pathlib import Path

TARGET_JSON = Path("output/target_codes.json")
OUTPUT_WAV  = Path("output/test_audar.wav")


def main():
    data = json.loads(TARGET_JSON.read_text(encoding="utf-8"))
    codes = data["codes"]
    print(f"📖 قراءة {len(codes)} code")

    print("🔧 تحميل NeuCodec ...")
    import torch
    import soundfile as sf
    from neucodec import NeuCodec
    codec = NeuCodec.from_pretrained("neuphonic/neucodec").eval()

    print("🔓 فك الترميز ...")
    with torch.no_grad():
        wav = codec.decode_code(torch.tensor(codes)[None, None, :]).cpu().numpy()[0, 0]

    sf.write(str(OUTPUT_WAV), wav, 24000, subtype="PCM_16")
    print(f"✅ تم إنشاء: {OUTPUT_WAV} ({len(wav)/24000:.1f} ثانية)")


if __name__ == "__main__":
    main()
