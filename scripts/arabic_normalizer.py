# scripts/arabic_normalizer.py
import re
from mishkal.tashkeel import TashkeelClass

_tashkeel = TashkeelClass()

FORCED_TASHKEEL = {
    "الله": "اللَّه", "لله": "لِلَّه", "بالله": "بِاللَّه", "والله": "وَاللَّه",
    "الرحمن": "الرَّحْمَن", "الرحيم": "الرَّحِيم", "إبراهيم": "إِبْرَاهِيم",
    "إسماعيل": "إِسْمَاعِيل", "إسحاق": "إِسْحَاق", "يعقوب": "يَعْقُوب",
    "يوسف": "يُوسُف", "موسى": "مُوسَى", "عيسى": "عِيسَى", "نوح": "نُوح",
    "آدم": "آدَم", "مريم": "مَرْيَم", "فرعون": "فِرْعَوْن",
}

def normalize_narration(text: str) -> str:
    """تشكيل آلي + إصلاح الكلمات الحرجة للسرد فقط (ليس للآيات)."""
    if not text.strip():
        return text
    # 1. إزالة أي تشكيل قديم (لتجنب التداخل)
    text = re.sub(r'[\u064B-\u0652\u0670\u0640]', '', text)
    # 2. تنظيف المسافات والترقيم
    text = re.sub(r'\s+', ' ', text).strip()
    # 3. تشكيل آلي
    try:
        text = _tashkeel.tashkeel(text)
    except Exception as e:
        print(f"⚠️ فشل التشكيل الآلي: {e}")
    # 4. فرض التشكيل الصحيح للكلمات الحرجة
    for wrong, right in FORCED_TASHKEEL.items():
        text = re.sub(rf'(?<![ء-ي\u064B-\u0652]){re.escape(wrong)}(?![ء-ي\u064B-\u0652])', right, text)
    return text
