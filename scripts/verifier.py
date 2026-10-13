"""تحقق من النص عبر 3 طبقات: القرآن + hadith-db المحلية + dorar.net."""
import re
import json
from pathlib import Path
import requests
from config import HADITH_JSON, QURAN_API, DORAR_API


# ====== طبقة 1: القرآن ======
def check_quran_references(text):
    """
    يبحث عن أنماط ﴿...﴾ ويستخرج السورة:الآية إن وُجدت.
    يرجع (عدد, صحيح).
    """
    # ابحث عن ﴿...﴾ مع مرجع بعدها
    pattern = re.compile(r"﴿(.+?)﴾")
    matches = pattern.findall(text)
    if not matches:
        return {"total": 0, "verified": 0, "issues": []}

    issues = []
    verified = 0
    for verse in matches:
        verse = verse.strip()
        if len(verse) < 5:
            continue
        try:
            r = requests.get(
                f"{QURAN_API}/search/{verse[:80]}/all/ar",
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                count = (data.get("data") or {}).get("count", 0)
                if count > 0:
                    verified += 1
                else:
                    issues.append(f"آية غير موجودة: {verse[:50]}")
        except Exception as e:
            issues.append(f"خطأ اتصال: {e}")

    return {"total": len(matches), "verified": verified, "issues": issues}


# ====== طبقة 2: hadith-db المحلية ======
def _load_local_hadiths():
    p = Path(HADITH_JSON)
    if not p.exists():
        return {}
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
        return {x["id"]: x for x in items}
    except Exception:
        return {}


def check_against_local_db(text):
    """
    يتحقق أن كل حديث مقتبس موجود في hadith-db.
    يرجع dict بالنتيجة.
    """
    local = _load_local_hadiths()
    if not local:
        return {"total": 0, "verified": 0, "issues": ["hadith-db فارغ"]}

    # استخرج الأحاديث المقتبسة (بين «...»)
    hadiths = re.findall(r"«(.+?)»", text)
    if not hadiths:
        return {"total": 0, "verified": 0, "issues": []}

    verified = 0
    issues = []
    for h in hadiths:
        h = h.strip()
        if len(h) < 15:
            continue
        # ابحث عن تطابق جزئي في قاعدة البيانات
        found = False
        for item in local.values():
            stored = item.get("answer", "")
            # تطابق 20 كلمة متتالية أو 60% تشابه
            words_h = set(h.split())
            words_s = set(stored.split())
            if not words_h:
                continue
            overlap = len(words_h & words_s) / len(words_h)
            if overlap >= 0.6:
                found = True
                break
        if found:
            verified += 1
        else:
            issues.append(f"حديث غير موجود في القاعدة: {h[:60]}")

    return {"total": len(hadiths), "verified": verified, "issues": issues}


# ====== طبقة 3: dorar.net ======
def check_with_dorar(text):
    """
    يستعلم dorar.net عن درجة الحديث.
    يرجع dict بالنتيجة.
    """
    hadiths = re.findall(r"«(.+?)»", text)
    if not hadiths:
        return {"checked": 0, "grades": [], "issues": []}

    grades = []
    issues = []
    for h in hadiths[:2]:  # حد أقصى 2 استعلام لكل نص
        h = h.strip()
        if len(h) < 15:
            continue
        try:
            r = requests.get(
                DORAR_API,
                params={"skey": h[:100]},
                timeout=20,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            # dorar يُرجع HTML في m.value
            value = str(data.get("m", {}).get("value", ""))
            # ابحث عن درجة
            for grade in ["صحيح", "حسن", "ضعيف", "موضوع", "منكر"]:
                if grade in value:
                    grades.append({"hadith": h[:40], "grade": grade})
                    if grade in ("ضعيف", "موضوع", "منكر"):
                        issues.append(f"حديث {grade}: {h[:60]}")
                    break
        except Exception as e:
            issues.append(f"dorar خطأ: {e}")

    return {"checked": len(hadiths), "grades": grades, "issues": issues}


# ====== الواجهة الرئيسية ======
def verify(script):
    """
    script: dict فيه question, answer.
    يرجع dict:
      {ok: bool, warnings: [...], details: {...}}
    """
    full_text = f"{script['question']} {script['answer']}"
    warnings = []

    # 1
    quran_res = check_quran_references(full_text)
    if quran_res["total"] > 0 and quran_res["verified"] < quran_res["total"]:
        warnings.append(f"آيات غير مُتحقق منها: {quran_res['verified']}/{quran_res['total']}")

    # 2
    local_res = check_against_local_db(full_text)
    if local_res["total"] > 0 and local_res["verified"] < local_res["total"]:
        warnings.append(f"أحاديث غير موجودة محلياً: {local_res['verified']}/{local_res['total']}")

    # 3
    dorar_res = check_with_dorar(full_text)
    if dorar_res["issues"]:
        warnings.extend(dorar_res["issues"])

    # القرار: النص يُقبل إلا إذا وجدنا حديثاً موضوعاً أو ضعيفاً جداً
    critical = any(
        "موضوع" in w or "منكر" in w for w in warnings
    )

    return {
        "ok": not critical,
        "warnings": warnings,
        "details": {
            "quran": quran_res,
            "local": local_res,
            "dorar": dorar_res,
        },
    }
