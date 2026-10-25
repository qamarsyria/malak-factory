"""اكتشاف النماذج المتاحة من Gemini كل 24 ساعة + حفظها في cache."""
import os, json, time, requests
from pathlib import Path

MODELS_CACHE = Path("state/models_cache.json")
MAX_AGE_HOURS = 24


def fetch_available_models(api_key: str) -> list:
    """جلب النماذج التي تدعم generateContent + JSON."""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return []
        models = []
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" not in methods:
                continue
            if "embedding" in name or "aqa" in name:
                continue
            models.append(name)
        # ترتيب: flash أولاً (أسرع)، ثم pro، ثم الأحدث
        def score(n):
            s = 0
            if "flash" in n: s += 10
            if "pro" in n: s += 5
            if "2.5" in n: s += 3
            if "2.0" in n: s += 2
            if "1.5" in n: s += 1
            if "latest" in n: s += 4
            return -s
        return sorted(models, key=score)
    except Exception as e:
        print(f"⚠️ فشل اكتشاف النماذج: {e}", flush=True)
        return []


def discover_and_cache(force=False) -> list:
    """يكتشف النماذج ويحفظها في cache (صالح 24 ساعة)."""
    if not force and MODELS_CACHE.exists():
        try:
            cache = json.loads(MODELS_CACHE.read_text(encoding="utf-8"))
            age_hours = (time.time() - cache.get("timestamp", 0)) / 3600
            if age_hours < MAX_AGE_HOURS and cache.get("models"):
                print(f"✅ استخدام cache النماذج (عمرها {age_hours:.1f}h)", flush=True)
                return cache["models"]
        except Exception:
            pass

    keys = os.environ.get("GEMINI_API_KEYS", "").split(",")
    keys = [k.strip() for k in keys if k.strip()]
    if not keys:
        print("⚠️ لا توجد مفاتيح Gemini", flush=True)
        return []

    all_models = []
    seen = set()
    for key in keys[:2]:  # مفتاحان يكفيان للاكتشاف
        for m in fetch_available_models(key):
            if m not in seen:
                seen.add(m)
                all_models.append(m)
        if all_models:
            break

    if all_models:
        MODELS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        MODELS_CACHE.write_text(
            json.dumps({"models": all_models, "timestamp": time.time()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"✅ اكتُشفت {len(all_models)} نماذج: {all_models[:5]}...", flush=True)
    else:
        all_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

    return all_models


if __name__ == "__main__":
    models = discover_and_cache(force=True)
    print("\n".join(models))
