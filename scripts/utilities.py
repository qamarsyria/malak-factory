"""أدوات مشتركة: retry, circuit breaker, إشعارات, تنظيف."""
import os
import time
import shutil
import subprocess
from pathlib import Path
from functools import wraps

import requests


def retry(max_attempts=3, backoff=2.0, exceptions=(Exception,), on_fail=None):
    """Retry مع exponential backoff."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last = e
                    wait = backoff ** attempt
                    print(f"⚠️ محاولة {attempt}/{max_attempts} فشلت: {e}. انتظار {wait:.1f}s", flush=True)
                    time.sleep(wait)
            if on_fail:
                on_fail(last)
            raise last
        return wrapper
    return decorator


def notify_telegram(message: str, level: str = "info"):
    """إشعار فوري عبر تلغرام."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(f"ℹ️ إشعار (بلا تلغرام): {message}", flush=True)
        return
    emoji = {"info": "ℹ️", "warn": "⚠️", "error": "🚨", "success": "✅"}.get(level, "ℹ️")
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": f"{emoji} {message}"},
            timeout=10,
        )
    except Exception as e:
        print(f"⚠️ فشل الإشعار: {e}", flush=True)


class CircuitBreaker:
    """عزل النماذج/المفاتيح الفاشلة مؤقتاً."""
    def __init__(self, cooldown=3600, threshold=3):
        self.cooldown = cooldown
        self.threshold = threshold
        self.failures = {}  # key -> (count, last_ts)

    def is_open(self, key) -> bool:
        if key not in self.failures:
            return False
        count, ts = self.failures[key]
        if count < self.threshold:
            return False
        if time.time() - ts > self.cooldown:
            del self.failures[key]
            return False
        return True

    def record_failure(self, key):
        count = self.failures.get(key, (0, 0))[0] + 1
        self.failures[key] = (count, time.time())

    def record_success(self, key):
        self.failures.pop(key, None)


_CB = CircuitBreaker()


def get_breaker():
    return _CB


def cleanup_dir(path):
    """حذف آمن لمجلد مؤقت."""
    try:
        if path and Path(path).exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def safe_ffmpeg(cmd, timeout=900):
    """تشغيل ffmpeg مع timeout ومعالجة أخطاء."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg فشل: {(r.stderr or '')[:500]}")
        return r
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg تجاوز {timeout}s")


def safe_copy(src, dst):
    """نسخ مع إنشاء الأب إن لم يوجد."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(src), str(dst))
