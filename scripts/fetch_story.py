"""جلب سورة كاملة + التفسير الميسّر من api.alquran.cloud."""
import json
import sys
from pathlib import Path
import requests

API = "https://api.alquran.cloud/v1"


def fetch_surah(surah_number):
    """يجلب السورة كاملة + التفسير الميسّر."""
    print(f"📖 جلب سورة رقم {surah_number}...")

    # 1. النص العربي
    r = requests.get(f"{API}/surah/{surah_number}", timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} للسورة")
    surah_data = r.json()["data"]

    # 2. التفسير الميسّر
    print("📚 جلب التفسير الميسّر...")
    r2 = requests.get(f"{API}/surah/{surah_number}/ar.muyassar", timeout=30)
    if r2.status_code != 200:
        raise RuntimeError(f"HTTP {r2.status_code} للتفسير")
    tafsir_data = r2.json()["data"]

    # 3. دمج الآية + التفسير
    tafsir_map = {a["numberInSurah"]: a["text"] for a in tafsir_data["ayahs"]}
    ayahs = []
    for a in surah_data["ayahs"]:
        num = a["numberInSurah"]
        ayahs.append({
            "number": num,
            "global_number": a["number"],
            "text": a["text"],
            "tafsir": tafsir_map.get(num, ""),
        })

    result = {
        "surah": surah_number,
        "name": surah_data["name"].replace("سُورَةُ ", ""),
        "english_name": surah_data["englishName"],
        "revelation_type": surah_data["revelationType"],
        "total_ayahs": surah_data["numberOfAyahs"],
        "ayahs": ayahs,
    }

    print(f"✅ {len(ayahs)} آية جاهزة")
    return result


def save_story(surah_number, slug):
    data = fetch_surah(surah_number)
    out_dir = Path(f"data/stories/{slug}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "source.json"
    out_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"💾 {out_file}")


if __name__ == "__main__":
    surah_num = 12  # يوسف
    slug = "yusuf"
    if len(sys.argv) > 1:
        surah_num = int(sys.argv[1])
    if len(sys.argv) > 2:
        slug = sys.argv[2]
    save_story(surah_num, slug)
