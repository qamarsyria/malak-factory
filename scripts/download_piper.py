"""تحميل موديل Piper الرجالي الأردني (المسار الصحيح)."""
from pathlib import Path
from huggingface_hub import hf_hub_download


PIPER_DIR = Path("/tmp/piper")
REPO_ID = "rhasspy/piper-voices"
REVISION = "v1.0.0"  # ⚠️ مهم: استخدام الإصدار الصحيح


def main():
    PIPER_DIR.mkdir(parents=True, exist_ok=True)

    # ⚠️ المسار الصحيح (مسار كامل مع kareem/medium)
    files = [
        "ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx",
        "ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx.json",
    ]

    downloaded = []
    for f in files:
        print(f"⬇️  {f}")
        path = hf_hub_download(
            repo_id=REPO_ID,
            filename=f,
            revision=REVISION,
            cache_dir=str(PIPER_DIR),
            force_download=False,
        )
        print(f"   ✅ {path}")
        downloaded.append(Path(path))

    # نسخ للوصول السهل (بدون مسارات cache معقدة)
    import shutil
    for src in downloaded:
        if src.name.endswith(".onnx"):
            target = PIPER_DIR / "ar_JO-kareem-medium.onnx"
            if not target.exists():
                shutil.copy(src, target)
                print(f"📋 {target} ({target.stat().st_size / 1024 / 1024:.1f} MB)")
        elif src.name.endswith(".onnx.json"):
            target = PIPER_DIR / "ar_JO-kareem-medium.onnx.json"
            if not target.exists():
                shutil.copy(src, target)
                print(f"📋 {target}")

    print("\n✅ اكتمل")


if __name__ == "__main__":
    main()
