"""اكتشاف النماذج النصية المتاحة من Gemini وترتيبها حسب الحداثة والاستقرار."""
import os
import re
import json
import time
import requests
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_CACHE = _REPO_ROOT / "state" / "models_cache.json"
MAX_AGE_HOURS = 12


def _model_score(name: str) -> tuple:
    n = name.lower()

    # استبعاد النماذج غير النصية
    bad_tokens = [
        "tts", "speech", "audio", "image", "imagen", "veo",
        "embedding", "aqa", "vision", "gemma", "learnlm",
        "robotics", "computer-use", "nano"
    ]
    if any(bad in n for bad in bad_tokens):
        return (999, 999, name)

    # استخراج رقم الإصدار (مثلاً 3.8 أو 3.5 أو 4)
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
    ver = float(m.group(1)) if m else 0.0

    # الأفضلية للنماذج الحديثة (>= 3.0)؛ 2.5 قيد الإيقاف، و<=2.0 متوقف/قديم
    if ver >= 3.0:
        tier = 0
    elif ver >= 2.5:
        tier = 1
    else:
        tier = 2

    # داخل الجيل الواحد: Flash العادي ثم Pro ثم Flash-Lite
    if "flash" in n and "lite" not in n:
        kind = 0
    elif "pro" in n:
        kind = 1
    elif "lite" in n:
        kind = 2
    else:
        kind = 3

    # تفضيل الإصدارات المستقرة على preview/exp
    is_preview = 1 if any(x in n for x in ["preview", "exp", "experimental"]) else 0

    return (tier, is_preview, kind, -ver, name)


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
            if "generateContent" not in methods:
                continue
            score = _model_score(name)
            if score[0] < 999:
                models.append(name)

        return sorted(models, key=_model_score)
    except Exception as e:
        print(f"⚠️ فشل اكتشاف النماذج: {e}", flush=True)
        return []


def discover_and_cache(force: bool = False) -> list:
    if not force and MODELS_CACHE.exists():
        try:
            cache = json.loads(MODELS_CACHE.read_text(encoding="utf-8"))
            age_hours = (time.time() - cache.get("timestamp", 0)) / 3600
            if age_hours < MAX_AGE_HOURS and cache.get("models"):
                return cache["models"]
        except Exception:
            pass

    raw = os.environ.get("GEMINI_API_KEYS", "") or os.environ.get("GEMINI_API_KEY", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    fallback = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]

    if not keys:
        print("⚠️ لا توجد مفاتيح Gemini", flush=True)
        return fallback

    all_models, seen = [], set()
    for key in keys[:2]:
        for m in fetch_available_models(key):
            if m not in seen:
                seen.add(m)
                all_models.append(m)
        if all_models:
            break

    if all_models:
        all_models.sort(key=_model_score)
        MODELS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        MODELS_CACHE.write_text(
            json.dumps(
                {"models": all_models, "timestamp": time.time()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"✅ اكتُشفت {len(all_models)} نماذج (الأول: {all_models[0]})", flush=True)
    else:
        all_models = fallback
        print("⚠️ استخدام النماذج الاحتياطية الحديثة", flush=True)

    return all_models


if __name__ == "__main__":
    for m in discover_and_cache(force=True):
        print(m)
