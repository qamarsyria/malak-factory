"""اكتشاف النماذج النصية المتاحة من Gemini وحفظها في cache."""
import os
import json
import time
import requests
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_CACHE = _REPO_ROOT / "state/models_cache.json"
MAX_AGE_HOURS = 24


def fetch_available_models(api_key: str) -> list:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return []
            
        models = []
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            methods = m.get("supportedGenerationMethods", [])
            
            # 🌟 1. يجب أن يدعم توليد النصوص
            if "generateContent" not in methods:
                continue
                
            # 🌟 2. استبعاد نماذج الصوت والصور والترجمة
            if any(bad in name.lower() for bad in ["tts", "speech", "audio", "image", "embedding", "aqa", "vision"]):
                continue
                
            models.append(name)

        # 🌟 3. ترتيب النماذج: نفضل النماذج السريعة والمستقرة
        def score(n):
            s = 0
            if "flash" in n: s += 10
            if "pro" in n: s += 5
            if "1.5" in n: s += 8  # الأكثر استقراراً
            if "2.0" in n: s += 6
            if "2.5" in n: s += 4  # قد يكون غير متاح للجميع
            if "latest" in n: s += 2
            return -s

        return sorted(models, key=score)
    except Exception as e:
        print(f"⚠️ فشل اكتشاف النماذج: {e}", flush=True)
        return []


def discover_and_cache(force=False) -> list:
    if not force and MODELS_CACHE.exists():
        try:
            cache = json.loads(MODELS_CACHE.read_text(encoding="utf-8"))
            age_hours = (time.time() - cache.get("timestamp", 0)) / 3600
            if age_hours < MAX_AGE_HOURS and cache.get("models"):
                return cache["models"]
        except Exception:
            pass

    keys = os.environ.get("GEMINI_API_KEYS", "").split(",")
    keys = [k.strip() for k in keys if k.strip()]
    if not keys:
        print("⚠️ لا توجد مفاتيح Gemini", flush=True)
        return ["gemini-1.5-flash", "gemini-2.0-flash"] # احتياطي

    all_models, seen = [], set()
    for key in keys[:2]:
        for m in fetch_available_models(key):
            if m not in seen:
                seen.add(m)
                all_models.append(m)
        if all_models:
            break

    if all_models:
        MODELS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        MODELS_CACHE.write_text(
            json.dumps({"models": all_models, "timestamp": time.time()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"✅ اكتُشفت {len(all_models)} نماذج", flush=True)
    else:
        # احتياطي في حال فشل الاتصال
        all_models = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]
        print(f"⚠️ استخدام النماذج الاحتياطية", flush=True)

    return all_models


if __name__ == "__main__":
    models = discover_and_cache(force=True)
    print("\n".join(models))
