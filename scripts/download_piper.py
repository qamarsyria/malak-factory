"""تحميل موديل Piper الرجالي الأردني."""
from pathlib import Path
from huggingface_hub import hf_hub_download


PIPER_DIR = Path("/tmp/piper")
REPO_ID = "rhasspy/piper-voices"


def main():
    PIPER_DIR.mkdir(parents=True, exist_ok=True)

    files = [
        "ar/ar_JO-kareem-medium/ar_JO-kareem-medium.onnx",
        "ar/ar_JO-kareem-medium/ar_JO-kareem-medium.onnx.json",
    ]

    for f in files:
        print(f"⬇️  {f}")
        path = hf_hub_download(
            repo_id=REPO_ID,
            filename=f,
            cache_dir=str(PIPER_DIR),
            force_download=False,
        )
        print(f"   ✅ {path}")

    # نسخ للوصول السهل
    model_src = PIPER_DIR / "models--rhasspy--piper-voices/snapshots" 
    # نبحث عن الملف في الـ cache
    for root, dirs, files_list in Path(PIPER_DIR).walk():
        for fname in files_list:
            if fname.endswith(".onnx") and "kareem" in fname:
                target = PIPER_DIR / "ar_JO-kareem.onnx"
                if not target.exists():
                    import shutil
                    shutil.copy(root / fname, target)
                    print(f"📋 {target}")
            if fname.endswith(".onnx.json") and "kareem" in fname:
                target = PIPER_DIR / "ar_JO-kareem.onnx.json"
                if not target.exists():
                    import shutil
                    shutil.copy(root / fname, target)
                    print(f"📋 {target}")

    print("\n✅ اكتمل")


if __name__ == "__main__":
    main()
