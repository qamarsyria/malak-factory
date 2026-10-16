"""جلب نصوص قصص الأنبياء من ketabonline.com (المكتبة الشاملة)."""
import re
import json
import time
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup


BASE_URL = "https://ketabonline.com"

# كتاب قصص الأنبياء لابن كثير
BOOK_ID = "228"

# فصول القصص — نأخذ الفصول المطلوبة
CHAPTERS = {
    "yusuf": "ذكر قصة يوسف عليه السلام",
    "adam": "باب ما ورد في خلق آدم عليه السلام",
    "nuh": "ذكر شئ من أخبار نوح نفسه عليه السلام",
    "ibrahim": "ذكر مناظرة إبراهيم الخليل",
    "musa": "ذكر قصة موسى الكليم عليه الصلاة والتسليم",
}


def fetch_chapter(chapter_title):
    """يجلب فصلاً من الكتاب."""
    url = f"{BASE_URL}/ar/books/{BOOK_ID}"
    print(f"📖 جلب: {chapter_title}")

    r = requests.get(url, timeout=60, headers={
        "User-Agent": "Mozilla/5.0 (compatible; MalakFactory/1.0)"
    })
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")

    soup = BeautifulSoup(r.text, "html.parser")

    # ابحث عن رابط الفصل
    links = soup.find_all("a")
    chapter_link = None
    for link in links:
        if link.text.strip() == chapter_title:
            chapter_link = urljoin(BASE_URL, link.get("href", ""))
            break

    if not chapter_link:
        raise RuntimeError(f"لم أجد الفصل: {chapter_title}")

    # اجلب محتوى الفصل
    r2 = requests.get(chapter_link, timeout=60)
    soup2 = BeautifulSoup(r2.text, "html.parser")

    # استخرج النص (يعتمد على بنية الموقع)
    content_div = soup2.find("div", class_="book-content") or soup2.find("article")
    if content_div:
        text = content_div.get_text(separator="\n", strip=True)
    else:
        text = soup2.get_text(separator="\n", strip=True)

    # نظّف
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def save_story(slug, text):
    """يحفظ النص في ملف."""
    out_dir = Path(f"data/stories/{slug}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "ibn-kathir.txt"
    out_file.write_text(text, encoding="utf-8")
    print(f"💾 {out_file} ({len(text)} حرف)")


def main():
    print("=" * 60)
    print("📚 جلب نصوص قصص الأنبياء من ابن كثير")
    print("=" * 60)

    for slug, chapter in CHAPTERS.items():
        try:
            text = fetch_chapter(chapter)
            save_story(slug, text)
        except Exception as e:
            print(f"❌ فشل {slug}: {e}")
        time.sleep(2)

    print("\n✅ اكتمل")


if __name__ == "__main__":
    main()
