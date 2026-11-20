import gzip
import json
import sys
import time
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "ayah-library.json.gz"


def fetch_json_with_retry(url: str, retries: int = 3) -> dict:
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=40)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries:
                raise e
            time.sleep(2 * attempt)
    return {}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("⬇️ جاري تحميل القرآن الكريم (الرسم العثماني) + التفسير الميسر...")

    try:
        quran_data = fetch_json_with_retry("https://api.alquran.cloud/v1/quran/quran-uthmani")["data"]["surahs"]
        tafsir_data = fetch_json_with_retry("https://api.alquran.cloud/v1/quran/ar.muyassar")["data"]["surahs"]

        tafsir_map: dict[tuple[int, int], str] = {}
        for surah in tafsir_data:
            s_num = int(surah["number"])
            for ayah in surah["ayahs"]:
                tafsir_map[(s_num, int(ayah["numberInSurah"]))] = (ayah.get("text") or "").strip()

        ayah_library = []
        for surah in quran_data:
            s_num = int(surah["number"])
            s_name = surah["name"]
            for ayah in surah["ayahs"]:
                a_num = int(ayah["numberInSurah"])
                ayah_library.append({
                    "id": f"ayah-{s_num:03d}-{a_num:03d}",
                    "surah_number": s_num,
                    "surah_name": s_name,
                    "ayah_number": a_num,
                    "text": (ayah.get("text") or "").strip(),
                    "tafsir": tafsir_map.get((s_num, a_num), ""),
                    "type": "quran",
                })

        if len(ayah_library) < 6000:
            raise RuntimeError(f"عدد الآيات غير مكتمل: {len(ayah_library)}")

        with gzip.open(OUTPUT_FILE, "wt", encoding="utf-8") as f:
            json.dump(ayah_library, f, ensure_ascii=False)

        size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
        print(f"✅ تم حفظ {len(ayah_library)} آية مع التفسير الميسر في: {OUTPUT_FILE} ({size_mb:.1f} MB)")

    except Exception as e:
        print(f"❌ فشل تحميل مكتبة الآيات: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
