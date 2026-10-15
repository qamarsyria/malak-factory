"""اختبار مصادر تحميل موسيقى ومؤثرات برمجياً."""
import subprocess
import tempfile
from pathlib import Path
import requests


# قائمة المصادر للاختبار — كل مصدر له رابط مباشر معروف
SOURCES = {
    "Internet Archive (music)": [
        "https://archive.org/download/testmp3testfile/mpthreetest.mp3",
        "https://archive.org/download/FreeMusicArchive/Mariachi_Sonora_Vaquero.mp3",
    ],
    "Wikimedia Commons (audio)": [
        "https://upload.wikimedia.org/wikipedia/commons/1/1f/Bach_-_Toccata_and_Fugue_in_D_minor.ogg",
        "https://upload.wikimedia.org/wikipedia/commons/c/c8/Example.ogg",
    ],
    "Chosic (music)": [
        "https://www.chosic.com/wp-content/uploads/2021/04/And-So-It-Begins-Inspired-By-Daniel-Lev.mp3",
        "https://www.chosic.com/wp-content/uploads/2021/07/The-Epic-Hero-Epic-Cinematic-Keys-of-Moon-Music.mp3",
    ],
    "Bensound (music)": [
        "https://www.bensound.com/bensound-music/bensound-ukulele.mp3",
        "https://www.bensound.com/bensound-music/bensound-tenderness.mp3",
    ],
    "Freesound (sfx)": [
        "https://cdn.freesound.org/previews/316/316847_5123451-lq.mp3",
        "https://cdn.freesound.org/previews/415/415209_5123451-lq.mp3",
    ],
    "Mixkit (music)": [
        "https://assets.mixkit.co/music/preview/mixkit-tech-house-vibes-130.mp3",
        "https://assets.mixkit.co/music/preview/mixkit-serene-view-443.mp3",
    ],
    "Pixabay CDN (direct)": [
        "https://cdn.pixabay.com/download/audio/2022/03/15/audio_c8c8a73467.mp3",
        "https://cdn.pixabay.com/download/audio/2022/01/18/audio_d0a13f69d2.mp3",
    ],
}


def test_url(url, timeout=15):
    """يختبر تحميل رابط واحد."""
    try:
        r = requests.get(
            url,
            timeout=timeout,
            stream=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"

        # اقرأ أول 50KB للتأكد من صحة الملف
        content = b""
        for chunk in r.iter_content(8192):
            content += chunk
            if len(content) > 50000:
                break

        if len(content) < 1024:
            return False, f"ملف صغير ({len(content)} بايت)"

        return True, f"{len(content)/1024:.1f} KB"

    except Exception as e:
        return False, str(e)[:80]


def main():
    print("=" * 70)
    print("🧪 اختبار مصادر التحميل من GitHub Actions")
    print("=" * 70)

    results = {}

    for source_name, urls in SOURCES.items():
        print(f"\n📡 {source_name}")
        print("-" * 60)

        success_count = 0
        for url in urls:
            short_url = url[:70] + ("..." if len(url) > 70 else "")
            print(f"   🔗 {short_url}")
            ok, info = test_url(url)
            status = "✅" if ok else "❌"
            print(f"      {status} {info}")
            if ok:
                success_count += 1

        results[source_name] = {
            "tested": len(urls),
            "success": success_count,
        }

    # الخلاصة
    print("\n" + "=" * 70)
    print("📊 الخلاصة")
    print("=" * 70)
    print(f"{'المصدر':<35} {'نجح':<10} {'فشل':<10}")
    print("-" * 70)

    working_sources = []
    for name, r in results.items():
        failed = r["tested"] - r["success"]
        mark = "✅" if r["success"] > 0 else "❌"
        print(f"{mark} {name:<33} {r['success']:<10} {failed:<10}")
        if r["success"] > 0:
            working_sources.append(name)

    print("\n" + "=" * 70)
    if working_sources:
        print(f"✅ المصادر التي تعمل: {', '.join(working_sources)}")
        print("\n→ سنستخدم هذه المصادر في fetch_music.py")
    else:
        print("❌ لا يوجد مصدر يعمل — نحتاج بحثاً إضافياً")
    print("=" * 70)


if __name__ == "__main__":
    main()
