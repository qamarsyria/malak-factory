import gzip
import json
import re
import sys
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _REPO_ROOT / "data" / "libraries"
OUTPUT_FILE = OUTPUT_DIR / "adhkar-library.json.gz"


def normalize_key(text: str) -> str:
    t = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", text or "")
    t = re.sub(r"[^\u0621-\u064A]", "", t)
    return t[:70]


def clean_str(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def fetch_json(url: str):
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"   ⚠️ تعذر الجلب من {url}: {e}")
    return None


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("⬇️ جاري تحميل مكتبة أذكار الصباح والمساء وقبل النوم (حصن المسلم)...")

    collected: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()

    def add_zekr(slot: str, text: str, repeat, bless: str, reference: str, category_label: str):
        text = clean_str(text)
        if len(text) < 15:
            return
        k = (slot, normalize_key(text))
        if not k[1] or k in seen_keys:
            return
        seen_keys.add(k)
        try:
            rep_num = int(str(repeat).strip()) if str(repeat).strip().isdigit() else 1
        except Exception:
            rep_num = 1
        collected.append({
            "id": f"adhkar-{slot}-{len(collected)+1:04d}",
            "slot": slot,  # sabah | masaa | sleep
            "category": category_label,
            "text": text,
            "repeat": max(1, rep_num),
            "bless": clean_str(bless),
            "reference": clean_str(reference) or "حصن المسلم",
            "type": "adhkar",
        })

    # 1) أذكار الصباح وأذكار المساء المفصولة بدقة (أصبحنا / أمسينا)
    sabah_data = fetch_json("https://raw.githubusercontent.com/ahegazy/muslimKit/master/docs/json/azkar_sabah.json")
    if isinstance(sabah_data, dict):
        for item in sabah_data.get("content", []):
            add_zekr("sabah", item.get("zekr", ""), item.get("repeat", 1), item.get("bless", ""), "حصن المسلم — أذكار الصباح", "أذكار الصباح")

    massa_data = fetch_json("https://raw.githubusercontent.com/ahegazy/muslimKit/master/docs/json/azkar_massa.json")
    if isinstance(massa_data, dict):
        for item in massa_data.get("content", []):
            add_zekr("masaa", item.get("zekr", ""), item.get("repeat", 1), item.get("bless", ""), "حصن المسلم — أذكار المساء", "أذكار المساء")

    # 2) osamayy/azkar-db (يحتوي الصباح، المساء، وأذكار النوم مع الفضل والتخريج)
    azkar_db = fetch_json("https://raw.githubusercontent.com/osamayy/azkar-db/master/azkar.json")
    if isinstance(azkar_db, list):
        for item in azkar_db:
            cat = clean_str(item.get("category", ""))
            zekr = item.get("zekr", "")
            desc = item.get("description", "")
            cnt = item.get("count", 1)
            ref = item.get("reference", "")
            if "الصباح" in cat:
                add_zekr("sabah", zekr, cnt, desc, ref, "أذكار الصباح")
            if "المساء" in cat:
                add_zekr("masaa", zekr, cnt, desc, ref, "أذكار المساء")
            if "النوم" in cat and "استيقاظ" not in cat and "فزع" not in cat and "تقلب" not in cat:
                add_zekr("sleep", zekr, cnt, desc, ref, "أذكار قبل النوم")

    # 3) rn0x/Adhkar-json (كتاب حصن المسلم للشيخ سعيد القحطاني رحمه الله)
    hisn_data = fetch_json("https://raw.githubusercontent.com/rn0x/Adhkar-json/main/adhkar.json")
    if isinstance(hisn_data, list):
        for section in hisn_data:
            cat = clean_str(section.get("category", ""))
            arr = section.get("array", [])
            if cat == "أذكار النوم":
                for sub in arr:
                    add_zekr("sleep", sub.get("text", ""), sub.get("count", 1), "", "حصن المسلم — أذكار النوم", "أذكار قبل النوم")
            elif "الصباح والمساء" in cat:
                for sub in arr:
                    txt = sub.get("text", "")
                    if "أمسينا" in txt and "أصبحنا" not in txt:
                        add_zekr("masaa", txt, sub.get("count", 1), "", "حصن المسلم — أذكار المساء", "أذكار المساء")
                    elif "أصبحنا" in txt and "أمسينا" not in txt:
                        add_zekr("sabah", txt, sub.get("count", 1), "", "حصن المسلم — أذكار الصباح", "أذكار الصباح")
                    else:
                        add_zekr("sabah", txt, sub.get("count", 1), "", "حصن المسلم — أذكار الصباح", "أذكار الصباح")
                        add_zekr("masaa", txt, sub.get("count", 1), "", "حصن المسلم — أذكار المساء", "أذكار المساء")

    counts = {s: sum(1 for x in collected if x["slot"] == s) for s in ("sabah", "masaa", "sleep")}
    print(f"📝 تم تجميع {len(collected)} ذكر صحيح: صباح={counts['sabah']} | مساء={counts['masaa']} | نوم={counts['sleep']}")

    if not collected or counts["sleep"] == 0:
        print("❌ لم يتم جمع أذكار كافية للأقسام الثلاثة.")
        sys.exit(1)

    with gzip.open(OUTPUT_FILE, "wt", encoding="utf-8") as f:
        json.dump(collected, f, ensure_ascii=False)

    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"✅ تم حفظ مكتبة الأذكار بنجاح: {OUTPUT_FILE} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
