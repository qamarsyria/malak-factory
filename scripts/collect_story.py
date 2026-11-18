"""
collect_story.py — المرحلة 1: جمع المصادر الموثقة بالتسلسل الزمني
- يسير على timeline خطوة بخطوة بالترتيب (event_01, event_02, ...).
- يسحب الآيات الحقيقية من المكتبة المحلية أو alquran.cloud.
- يسحب التفاسير الحقيقية (ابن كثير، السعدي، الميسر، والطبري عند الحاجة) عبر spa5k/tafsir_api.
- يبحث في صحيح البخاري وصحيح مسلم (محلياً أو عبر fawazahmed0/hadith-api).
- يتحقق من مطابقة كل آية للحدث الزمني، ويدمج النصوص بعد إزالة التكرار، ويربط كل فقرة بآيتها المؤكدة.
"""

import os
import re
import sys
import json
import time
import gzip
import shutil
from pathlib import Path
import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_tts_pool import get_pool
from model_discovery import discover_and_cache
from utilities import get_breaker

# ═══════════════════════════════════════════════════════════
# المسارات والثوابت
# ═══════════════════════════════════════════════════════════
STORIES_JSON   = _REPO_ROOT / "data" / "library" / "stories.json"
STORIES_OUTPUT = _REPO_ROOT / "data" / "stories"
AYAH_LIB_GZ    = _REPO_ROOT / "data" / "libraries" / "ayah-library.json.gz"
HADITH_LIB_GZ  = _REPO_ROOT / "data" / "libraries" / "hadith-library.json.gz"
SOURCES_JSON   = _REPO_ROOT / "data" / "auto-generated" / "sources.json"

CACHE_DIR      = Path("/tmp/malak_ref_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TAFSIR_CDN = "https://cdn.jsdelivr.net/gh/spa5k/tafsir_api@main/tafsir"
HADITH_CDN = "https://cdn.jsdelivr.net/gh/fawazahmed0/hadith-api@1/editions"
QURAN_API  = "https://api.alquran.cloud/v1"

# ═══════════════════════════════════════════════════════════
# تطبيع النص العربي وخريطة السور (114 سورة)
# ═══════════════════════════════════════════════════════════
def norm_ar(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\u064B-\u065F\u0670\u06D6-\u06ED]", "", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    return re.sub(r"\s+", " ", text).strip()


_SURAH_LIST = [
    (1, "الفاتحة"), (2, "البقرة"), (3, "آل عمران"), (4, "النساء"), (5, "المائدة"),
    (6, "الأنعام"), (7, "الأعراف"), (8, "الأنفال"), (9, "التوبة"), (10, "يونس"),
    (11, "هود"), (12, "يوسف"), (13, "الرعد"), (14, "إبراهيم"), (15, "الحجر"),
    (16, "النحل"), (17, "الإسراء"), (18, "الكهف"), (19, "مريم"), (20, "طه"),
    (21, "الأنبياء"), (22, "الحج"), (23, "المؤمنون"), (24, "النور"), (25, "الفرقان"),
    (26, "الشعراء"), (27, "النمل"), (28, "القصص"), (29, "العنكبوت"), (30, "الروم"),
    (31, "لقمان"), (32, "السجدة"), (33, "الأحزاب"), (34, "سبأ"), (35, "فاطر"),
    (36, "يس"), (37, "الصافات"), (38, "ص"), (39, "الزمر"), (40, "غافر"),
    (41, "فصلت"), (42, "الشورى"), (43, "الزخرف"), (44, "الدخان"), (45, "الجاثية"),
    (46, "الأحقاف"), (47, "محمد"), (48, "الفتح"), (49, "الحجرات"), (50, "ق"),
    (51, "الذاريات"), (52, "الطور"), (53, "النجم"), (54, "القمر"), (55, "الرحمن"),
    (56, "الواقعة"), (57, "الحديد"), (58, "المجادلة"), (59, "الحشر"), (60, "الممتحنة"),
    (61, "الصف"), (62, "الجمعة"), (63, "المنافقون"), (64, "التغابن"), (65, "الطلاق"),
    (66, "التحريم"), (67, "الملك"), (68, "القلم"), (69, "الحاقة"), (70, "المعارج"),
    (71, "نوح"), (72, "الجن"), (73, "المزمل"), (74, "المدثر"), (75, "القيامة"),
    (76, "الإنسان"), (77, "المرسلات"), (78, "النبأ"), (79, "النازعات"), (80, "عبس"),
    (81, "التكوير"), (82, "الانفطار"), (83, "المطففين"), (84, "الانشقاق"), (85, "البروج"),
    (86, "الطارق"), (87, "الأعلى"), (88, "الغاشية"), (89, "الفجر"), (90, "البلد"),
    (91, "الشمس"), (92, "الليل"), (93, "الضحى"), (94, "الشرح"), (95, "التين"),
    (96, "العلق"), (97, "القدر"), (98, "البينة"), (99, "الزلزلة"), (100, "العاديات"),
    (101, "القارعة"), (102, "التكاثر"), (103, "العصر"), (104, "الهمزة"), (105, "الفيل"),
    (106, "قريش"), (107, "الماعون"), (108, "الكوثر"), (109, "الكافرون"), (110, "النصر"),
    (111, "المسد"), (112, "الإخلاص"), (113, "الفلق"), (114, "الناس"),
]

SURAH_NUM_TO_NAME = {n: f"سورة {name}" for n, name in _SURAH_LIST}
SURAH_NAME_TO_NUM = {}
for n, name in _SURAH_LIST:
    key = norm_ar(name)
    SURAH_NAME_TO_NUM[key] = n
    if key.startswith("ال"):
        SURAH_NAME_TO_NUM[key[2:]] = n
SURAH_NAME_TO_NUM["ال عمران"] = 3


def resolve_story_surahs(sources: list) -> list[int]:
    surahs = []
    for src in sources or []:
        s_norm = norm_ar(src).replace("سوره ", "")
        if "ايه الكرسي" in s_norm:
            if 2 not in surahs:
                surahs.append(2)
            continue
        if s_norm in SURAH_NAME_TO_NUM:
            num = SURAH_NAME_TO_NUM[s_norm]
            if num not in surahs:
                surahs.append(num)
    return surahs


# ═══════════════════════════════════════════════════════════
# طبقة البيانات المرجعية (القرآن + التفاسير + البخاري ومسلم + المنصة)
# ═══════════════════════════════════════════════════════════
class ReferenceStore:
    def __init__(self):
        self.quran_by_surah: dict[int, dict[int, str]] = {}
        self.muyassar_local: dict[tuple[int, int], str] = {}
        self.tafsir_cache: dict[tuple[str, int], dict[int, str]] = {}
        self.hadiths: list[dict] = []
        self.hadiths_loaded = False
        self._load_local_quran()

    def _load_local_quran(self):
        if AYAH_LIB_GZ.exists():
            try:
                with gzip.open(AYAH_LIB_GZ, "rt", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    s = int(item.get("surah_number", 0))
                    a = int(item.get("ayah_number", 0))
                    if s and a:
                        self.quran_by_surah.setdefault(s, {})[a] = item.get("text", "")
                        if item.get("tafsir"):
                            self.muyassar_local[(s, a)] = item["tafsir"]
                print(f"📖 تم تحميل القرآن محلياً ({len(self.quran_by_surah)} سورة)")
            except Exception as e:
                print(f"⚠️ تعذر قراءة ayah-library.json.gz: {e}")

    def get_surah_ayahs(self, surah_num: int) -> dict[int, str]:
        if surah_num in self.quran_by_surah:
            return self.quran_by_surah[surah_num]

        cache_file = CACHE_DIR / f"quran_surah_{surah_num}.json"
        if cache_file.exists():
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            self.quran_by_surah[surah_num] = {int(k): v for k, v in raw.items()}
            return self.quran_by_surah[surah_num]

        try:
            r = requests.get(f"{QURAN_API}/surah/{surah_num}/quran-uthmani", timeout=20)
            if r.status_code == 200:
                ayahs = r.json().get("data", {}).get("ayahs", [])
                mapping = {int(a["numberInSurah"]): a["text"] for a in ayahs}
                self.quran_by_surah[surah_num] = mapping
                cache_file.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
                return mapping
        except Exception as e:
            print(f"   ⚠️ فشل جلب سورة {surah_num}: {e}")
        return {}

    def get_ayah_text(self, surah: int, ayah: int) -> str:
        return self.get_surah_ayahs(surah).get(ayah, "")

    @staticmethod
    def _clean_html(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _get_surah_tafsir(self, edition: str, surah: int) -> dict[int, str]:
        key = (edition, surah)
        if key in self.tafsir_cache:
            return self.tafsir_cache[key]

        cache_file = CACHE_DIR / f"tafsir_{edition}_{surah}.json"
        if cache_file.exists():
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            self.tafsir_cache[key] = {int(k): v for k, v in raw.items()}
            return self.tafsir_cache[key]

        # محاولة تحميل السورة كاملة بطلب واحد سريع
        try:
            r = requests.get(f"{TAFSIR_CDN}/{edition}/{surah}.json", timeout=20)
            if r.status_code == 200:
                data = r.json()
                mapping = {}
                for item in data.get("ayahs", []):
                    a_num = int(item.get("ayah", 0))
                    txt = self._clean_html(item.get("text", ""))
                    if a_num and txt:
                        mapping[a_num] = txt
                if mapping:
                    self.tafsir_cache[key] = mapping
                    cache_file.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
                    return mapping
        except Exception:
            pass

        self.tafsir_cache[key] = {}
        return {}

    def get_ayah_tafsirs(self, surah: int, ayah: int, include_tabari: bool = False) -> dict[str, str]:
        result = {}

        # الميسر (محلي أولاً ثم CDN)
        if (surah, ayah) in self.muyassar_local:
            result["التفسير الميسر"] = self.muyassar_local[(surah, ayah)]
        else:
            t_muy = self._get_surah_tafsir("ar-tafsir-muyassar", surah).get(ayah)
            if t_muy:
                result["التفسير الميسر"] = t_muy

        # تفسير السعدي (مع اسمَي السلسلة المحتملين في spa5k)
        t_sadi = self._get_surah_tafsir("ar-tafseer-al-saddi", surah).get(ayah)
        if not t_sadi:
            t_sadi = self._get_surah_tafsir("ar-tafsir-as-saadi", surah).get(ayah)
        if t_sadi:
            result["تفسير السعدي"] = t_sadi[:1200]

        # تفسير ابن كثير
        t_ik = self._get_surah_tafsir("ar-tafsir-ibn-kathir", surah).get(ayah)
        if t_ik:
            result["تفسير ابن كثير"] = t_ik[:1500]

        # تفسير الطبري (في القصص غير المقيدة بـ strict_mode)
        if include_tabari:
            t_tab = self._get_surah_tafsir("ar-tafsir-al-tabari", surah).get(ayah)
            if t_tab:
                result["تفسير الطبري"] = t_tab[:1200]

        return result

    def _ensure_hadiths(self):
        if self.hadiths_loaded:
            return
        self.hadiths_loaded = True

        # 1. المكتبة المحلية
        if HADITH_LIB_GZ.exists():
            try:
                with gzip.open(HADITH_LIB_GZ, "rt", encoding="utf-8") as f:
                    data = json.load(f)
                for h in data:
                    txt = (h.get("text") or "").strip()
                    if len(txt) < 25:
                        continue
                    self.hadiths.append({
                        "book": h.get("book", "الصحيح"),
                        "ref": h.get("reference") or h.get("id", ""),
                        "text": txt,
                        "norm": norm_ar(txt),
                    })
                print(f"📚 تم تحميل {len(self.hadiths)} حديث من المكتبة المحلية")
                return
            except Exception as e:
                print(f"⚠️ تعذر قراءة hadith-library.json.gz: {e}")

        # 2. الاحتياطي: صحيح البخاري ومسلم من CDN
        for edition, label in [("ara-bukhari", "صحيح البخاري"), ("ara-muslim", "صحيح مسلم")]:
            cache_file = CACHE_DIR / f"{edition}.json"
            data = None
            if cache_file.exists():
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception:
                    data = None
            if data is None:
                try:
                    print(f"   ⬇️ تحميل {label} المرجعي...")
                    r = requests.get(f"{HADITH_CDN}/{edition}.min.json", timeout=45)
                    if r.status_code == 200:
                        data = r.json()
                        cache_file.write_text(r.text, encoding="utf-8")
                except Exception as e:
                    print(f"   ⚠️ فشل تحميل {label}: {e}")
            if data:
                for item in data.get("hadiths", []):
                    txt = (item.get("text") or "").strip()
                    if len(txt) < 25:
                        continue
                    h_num = item.get("hadithnumber", "")
                    self.hadiths.append({
                        "book": label,
                        "ref": f"{label} ({h_num})",
                        "text": txt,
                        "norm": norm_ar(txt),
                    })
        print(f"📚 إجمالي الأحاديث المتاحة للبحث: {len(self.hadiths)}")

    def search_hadiths(self, phrases: list[str], limit: int = 5) -> list[dict]:
        self._ensure_hadiths()
        if not self.hadiths or not phrases:
            return []

        token_groups = []
        for p in phrases:
            tokens = [t for t in norm_ar(p).split() if len(t) >= 3]
            if tokens:
                token_groups.append(tokens)
        if not token_groups:
            return []

        scored = []
        for h in self.hadiths:
            norm_txt = h["norm"]
            best_score = 0
            for tokens in token_groups:
                hits = sum(1 for t in tokens if t in norm_txt)
                if hits == len(tokens):
                    best_score = max(best_score, hits * 3)
                elif hits >= max(2, len(tokens) - 1):
                    best_score = max(best_score, hits)
            if best_score > 0:
                # تفضيل الأحاديث متوسطة الطول الواضحة على الأحاديث المبتورة أو الطويلة جداً
                scored.append((best_score, -abs(len(h["text"]) - 350), h))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        out, seen_texts = [], set()
        for _, _, h in scored:
            sig = h["norm"][:80]
            if sig in seen_texts:
                continue
            seen_texts.add(sig)
            out.append({"book": h["book"], "ref": h["ref"], "text": h["text"][:1000]})
            if len(out) >= limit:
                break
        return out

    def get_platform_topic(self, story_id: str, story_title: str) -> str:
        if not SOURCES_JSON.exists():
            return ""
        try:
            data = json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
            title_norm = norm_ar(story_title).replace("عليه السلام", "").strip()
            for bucket in ("prophets", "quranic_stories", "seerah", "sahaba"):
                for item in data.get(bucket, []):
                    sid = item.get("source_id", "")
                    name_norm = norm_ar(item.get("name", ""))
                    if story_id in sid or (title_norm and title_norm in name_norm):
                        parts = [f"{s.get('h', '')}: {s.get('p') or s.get('text', '')}" for s in item.get("sections", [])]
                        return "\n".join(parts)[:3000]
        except Exception:
            pass
        return ""


# ═══════════════════════════════════════════════════════════
# استدعاء LLM الآمن
# ═══════════════════════════════════════════════════════════
def _advance_pool(pool):
    if hasattr(pool, "_advance"):
        pool._advance()
    elif hasattr(pool, "advance"):
        pool.advance()


def call_llm(prompt: str, timeout: int = 180, max_tokens: int = 4096) -> dict | None:
    pool = get_pool()
    breaker = get_breaker()
    models = discover_and_cache(force=False)[:5]
    if not models:
        return None

    for model in models:
        if breaker.is_open(f"model:{model}"):
            continue
        for attempt in range(2):
            try:
                key = pool.get_current()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.15,
                        "maxOutputTokens": max_tokens,
                        "responseMimeType": "application/json",
                    },
                }
                r = requests.post(url, json=body, timeout=timeout)

                if r.status_code == 200:
                    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    if not parts:
                        break
                    text = parts[0].get("text", "")
                    s, e = text.find("{"), text.rfind("}")
                    if s == -1 or e <= s:
                        break
                    parsed = json.loads(text[s:e + 1])
                    _advance_pool(pool)
                    breaker.record_success(f"model:{model}")
                    return parsed

                elif r.status_code == 429:
                    print(f"   ⏸️ {model}: Rate Limit (429)", flush=True)
                    pool.mark_failed(75)
                    time.sleep(2)
                elif r.status_code in (400, 404):
                    print(f"   ⚠️ {model}: HTTP {r.status_code}", flush=True)
                    breaker.record_failure(f"model:{model}")
                    break
                elif r.status_code >= 500:
                    time.sleep(3)
            except requests.exceptions.Timeout:
                print(f"   ⏱️ {model}: Timeout", flush=True)
                time.sleep(3)
            except Exception as e:
                print(f"   ⚠️ {model}: {str(e)[:80]}", flush=True)
                break
    return None


def parse_ayah_refs(raw_refs: list) -> list[tuple[int, int]]:
    out, seen = [], set()
    for ref in raw_refs or []:
        ref = str(ref).strip()
        m_range = re.match(r"^(\d{1,3}):(\d{1,3})-(\d{1,3})$", ref)
        if m_range:
            s, a1, a2 = int(m_range.group(1)), int(m_range.group(2)), int(m_range.group(3))
            if 1 <= s <= 114 and 1 <= a1 <= a2 <= 286 and (a2 - a1) <= 10:
                for a in range(a1, a2 + 1):
                    if (s, a) not in seen:
                        seen.add((s, a))
                        out.append((s, a))
            continue
        m_single = re.match(r"^(\d{1,3}):(\d{1,3})$", ref)
        if m_single:
            s, a = int(m_single.group(1)), int(m_single.group(2))
            if 1 <= s <= 114 and 1 <= a <= 286 and (s, a) not in seen:
                seen.add((s, a))
                out.append((s, a))
    return out


# ═══════════════════════════════════════════════════════════
# معالجة خطوة واحدة من الـ timeline
# ═══════════════════════════════════════════════════════════
def process_timeline_step(
    store: ReferenceStore,
    story: dict,
    step_idx: int,
    total_steps: int,
    step_text: str,
    covered_summary: list[str],
    used_ayah_refs: set[str],
    platform_notes: str,
) -> dict:
    guide = story.get("story_guide", {})
    strict_mode = bool(guide.get("strict_mode", False))
    rules = "\n".join(f"- {r}" for r in guide.get("special_rules", []))
    story_surahs = resolve_story_surahs(story.get("sources", []))

    # تجهيز قائمة الآيات المتاحة من السور المرجعية إن كانت قصيرة، لتقليل التخمين
    surah_hints = []
    for s_num in story_surahs[:6]:
        ayahs_map = store.get_surah_ayahs(s_num)
        if ayahs_map and len(ayahs_map) <= 30:
            lines = [f"{s_num}:{a}: {txt}" for a, txt in sorted(ayahs_map.items())]
            surah_hints.append(f"[{SURAH_NUM_TO_NAME[s_num]}]\n" + "\n".join(lines))
        elif ayahs_map:
            surah_hints.append(f"[{SURAH_NUM_TO_NAME[s_num]} (رقم {s_num} — {len(ayahs_map)} آية)]")
    surah_hints_str = "\n\n".join(surah_hints) or "(ابحث في القرآن والسنة الصحيحة)"

    covered_str = "\n".join(f"- {c}" for c in covered_summary) or "(هذه هي الخطوة الأولى)"
    used_refs_str = ", ".join(sorted(used_ayah_refs)) or "(لا يوجد)"

    # ── المرحلة أ: تحديد الآيات المرشحة وكلمات البحث في الحديث لهذه الخطوة فقط ──
    retrieval_prompt = f"""أنت باحث شرعي متخصص في القرآن والسنة والتاريخ الإسلامي.
القصة: "{story.get('title')}"
الخطوة الزمنية الحالية ({step_idx} من {total_steps}): "{step_text}"

ما تم تغطيته سابقاً (يُمنع تكراره أو جلب آياته):
{covered_str}
الآيات المستهلكة سابقاً: {used_refs_str}

السور المرجعية المحددة لهذه القصة:
{surah_hints_str}

المطلوب لهذه الخطوة الزمنية فقط ("{step_text}"):
1. اختر من 1 إلى 5 آيات قرآنية (بصيغة "سورة:آية") تثبت أو تصف هذا الحدث تحديداً دون غيره.
   ⚠️ لا تقترح أي آية من حدث لاحق أو سابق، ولا آية لا تتعلق مباشرة بهذه الخطوة.
2. اقترح 2 إلى 4 عبارات بحث عربية قصيرة (كلمتين إلى 3 كلمات لكل عبارة) للبحث في صحيح البخاري وصحيح مسلم عن أحاديث تتعلق بهذه الخطوة.

أعد JSON فقط:
{{
  "candidate_ayahs": ["112:1", "59:22"],
  "hadith_queries": ["كان الله ولم يكن", "أول ما خلق"]
}}"""

    plan = call_llm(retrieval_prompt, timeout=90, max_tokens=1024) or {}
    candidates = parse_ayah_refs(plan.get("candidate_ayahs", []))

    # لو آية الكرسي في المصادر والخطوة تتعلق بالعرش/الكرسي نضيفها تلقائياً
    if any("الكرسي" in str(x) for x in story.get("sources", [])) and "كرسي" in step_text:
        if (2, 255) not in candidates:
            candidates.insert(0, (2, 255))

    # ── المرحلة ب: سحب النصوص الحقيقية (القرآن + التفاسير + البخاري ومسلم) ──
    retrieved_verses = []
    for s, a in candidates[:6]:
        ref_str = f"{s}:{a}"
        ayah_txt = store.get_ayah_text(s, a)
        if not ayah_txt:
            continue
        tafsirs = store.get_ayah_tafsirs(s, a, include_tabari=(not strict_mode))
        retrieved_verses.append({
            "ref": ref_str,
            "surah": s,
            "ayah": a,
            "surah_name": SURAH_NUM_TO_NAME.get(s, f"سورة {s}"),
            "text": ayah_txt,
            "tafsirs": tafsirs,
        })

    hadith_hits = store.search_hadiths(plan.get("hadith_queries", []), limit=5)

    # تجهيز حزمة المراجع للدمج والتحقق
    verses_context_parts = []
    for rv in retrieved_verses:
        t_lines = "\n".join(f"   • {k}: {v}" for k, v in rv["tafsirs"].items())
        verses_context_parts.append(
            f"### مرجع [{rv['ref']}] ({rv['surah_name']}):\n"
            f"النص القرآني: {rv['text']}\n"
            f"{t_lines}"
        )
    verses_context = "\n\n".join(verses_context_parts) or "(لم تُسحب آيات مرشحة)"

    hadiths_context = "\n\n".join(
        f"### {h['ref']}:\n{h['text']}" for h in hadith_hits
    ) or "(لم يُعثر على حديث مطابق في البحث المباشر)"

    extra_platform = ""
    if (not strict_mode) and platform_notes:
        extra_platform = f"\n\n=== معلومات مساعدة من منصة المشروع (تُراجع بميزان الصحة) ===\n{platform_notes[:1800]}"

    strict_block = (
        "🔴 وضع الصرامة القصوى (عهد بداية الخلق):\n"
        "- يُمنع منعاً باتاً التمريض (يُروى، قيل، يُذكر) أو الإسرائيليات أو تشبيه الصفات.\n"
        "- اعتمد حصراً على ما ورد في الآيات وتفسيرها الصحيح (ابن كثير/السعدي/الميسر) والأحاديث الصحيحة.\n"
    ) if strict_mode else (
        "🟡 القواعد المنهجية:\n"
        "- اعتمد على المراجع المرفقة (القرآن + التفاسير + الصحيحين).\n"
        "- يُمنع الإسرائيليات والأحاديث الضعيفة.\n"
        "- أي معلومة تفسيرية غير قطعية تُنسب بصيغة التمريض مع (والله أعلم).\n"
    )

    # ── المرحلة ج: التحقق من الآيات + الدمج + إزالة التكرار ──
    synthesis_prompt = f"""أنت مؤرخ إسلامي ومفسّر ومدقّق للمصادر.

القصة: "{story.get('title')}"
الخطوة الزمنية الحالية ({step_idx}/{total_steps}): "{step_text}"
الأسلوب المطلوب: {guide.get('narrative_style', 'فصحى وقورة')}

قواعد خاصة بالقصة:
{rules}
{strict_block}

ما تم سرده في الأحداث السابقة (يُمنع تكراره هنا بأي شكل):
{covered_str}

=== المصادر الحقيقية المسحوبة لهذا الحدث ===
{verses_context}

=== الأحاديث الصحيحة المسحوبة ===
{hadiths_context}
{extra_platform}

المهام المطلوبة بدقة:
1. **فلترة الآيات**: انظر إلى النصوص القرآنية وتفاسيرها أعلاه؛ احتفظ في `verified_refs` فقط بالآيات التي تثبت أو تتعلق مباشرة بهذه الخطوة ("{step_text}")، واحذف أي آية بعيدة عن هذا الحدث.
   ⚠️ إذا لم يكن هناك آية مرفقة مناسبة للخطوة، أبق `verified_refs` فارغة ولا تخترع أرقاماً من خارج القائمة.
2. **الدمج وإزالة التكرار**: ادمج المعاني من التفاسير والأحاديث للخطوة الحالية في 2 إلى 4 فقرات سرد مترابطة زمنياً:
   - بدون تكرار للمعلومات بين الفقرات أو مع الأحداث السابقة.
   - بدون أي معلومة من خارج المراجع الموثقة.
   - لا تكتب نص الآية القرآنية حرفياً داخل نص الفقرة (اشرح معناها وسياقها)، وضع في حقل `anchor_ayah` رقم الآية التي تؤكد هذه الفقرة (من `verified_refs` إن وُجدت، وإلا `""`).
3. اكتب `event_title` (عنواناً قصيراً واضحاً للحدث) و`event_summary` (سطر واحد يلخص ما تم سرده).

أعد JSON فقط:
{{
  "event_title": "عنوان الحدث",
  "verified_refs": ["112:1"],
  "blocks": [
    {{
      "text": "فقرة السرد القصصي الموثقة والمدمجة الخالية من التكرار...",
      "anchor_ayah": "112:1",
      "sources_used": ["تفسير ابن كثير", "تفسير السعدي"]
    }}
  ],
  "event_summary": "ملخص مختصر لما غُطي في هذا الحدث"
}}"""

    synthesized = call_llm(synthesis_prompt, timeout=180, max_tokens=4096)
    if not synthesized or not synthesized.get("blocks"):
        return {}

    verified_set = set(synthesized.get("verified_refs", []))
    valid_ayahs = []
    valid_ref_strings = set()
    for rv in retrieved_verses:
        if rv["ref"] in verified_set:
            valid_ayahs.append({
                "ref": rv["ref"],
                "surah": rv["surah"],
                "ayah": rv["ayah"],
                "surah_name": rv["surah_name"],
                "text": rv["text"],
            })
            valid_ref_strings.add(rv["ref"])

    clean_blocks = []
    for b in synthesized.get("blocks", []):
        txt = (b.get("text") or "").strip()
        if len(txt) < 25:
            continue
        anchor = (b.get("anchor_ayah") or "").strip()
        if anchor not in valid_ref_strings:
            anchor = valid_ayahs[0]["ref"] if valid_ayahs else ""
        used_src = b.get("sources_used") or ["التفاسير المعتمدة"]
        clean_blocks.append({
            "source": " + ".join(used_src),
            "text": txt,
            "anchor_ayah": anchor,
        })

    if not clean_blocks:
        return {}

    return {
        "title": (synthesized.get("event_title") or step_text).strip(),
        "timeline_step": step_text,
        "ayahs": valid_ayahs,
        "narrative": clean_blocks,
        "summary": (synthesized.get("event_summary") or step_text).strip(),
    }


# ═══════════════════════════════════════════════════════════
# الدالة الرئيسية
# ═══════════════════════════════════════════════════════════
def main():
    if len(sys.argv) < 2:
        print("الاستخدام: python collect_story.py <story_id>")
        sys.exit(1)

    story_id = sys.argv[1]
    print("=" * 65)
    print(f"📚 جمع المصادر الموثقة بالتسلسل الزمني: {story_id}")
    print("=" * 65)

    if not STORIES_JSON.exists():
        print(f"❌ ملف المكتبة غير موجود: {STORIES_JSON}")
        sys.exit(1)

    library = json.loads(STORIES_JSON.read_text(encoding="utf-8"))
    story = next((s for s in library["stories"] if s["id"] == story_id), None)
    if not story:
        print(f"❌ القصة '{story_id}' غير موجودة في stories.json")
        sys.exit(1)

    timeline = story.get("story_guide", {}).get("timeline", [])
    if not timeline:
        print(f"❌ القصة '{story_id}' بلا timeline!")
        sys.exit(1)

    print(f"📖 {story.get('title')} | العهد: {story.get('era')} | الخطوات: {len(timeline)}")

    store = ReferenceStore()
    platform_notes = store.get_platform_topic(story_id, story.get("title", ""))

    # بناء الحدث في مجلد مؤقت ثم نقله نظيفاً لتفادي مخلفات قديمة
    story_dir = STORIES_OUTPUT / story_id / "collected"
    tmp_events_dir = story_dir / "_events_tmp"
    if tmp_events_dir.exists():
        shutil.rmtree(tmp_events_dir)
    tmp_events_dir.mkdir(parents=True, exist_ok=True)

    covered_summary: list[str] = []
    used_ayah_refs: set[str] = set()
    total_ayahs = 0
    total_blocks = 0

    for idx, step_text in enumerate(timeline, 1):
        ev_id = f"event_{idx:02d}"
        print(f"\n🧭 [{idx}/{len(timeline)}] {step_text}")

        ev_data = None
        for attempt in range(2):
            ev_data = process_timeline_step(
                store=store,
                story=story,
                step_idx=idx,
                total_steps=len(timeline),
                step_text=step_text,
                covered_summary=covered_summary,
                used_ayah_refs=used_ayah_refs,
                platform_notes=platform_notes,
            )
            if ev_data:
                break
            print(f"   🔁 إعادة محاولة الخطوة {idx}...")
            time.sleep(3)

        if not ev_data:
            print(f"❌ فشل جمع الخطوة الزمنية {idx}: '{step_text}'")
            shutil.rmtree(tmp_events_dir, ignore_errors=True)
            sys.exit(1)

        ev_path = tmp_events_dir / ev_id
        ev_path.mkdir(parents=True, exist_ok=True)
        (ev_path / "ayahs.json").write_text(
            json.dumps(ev_data["ayahs"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (ev_path / "narrative.json").write_text(
            json.dumps(ev_data["narrative"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (ev_path / "meta.json").write_text(
            json.dumps(
                {
                    "event_id": ev_id,
                    "chron_index": idx,
                    "title": ev_data["title"],
                    "timeline_step": step_text,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        covered_summary.append(f"{ev_id}: {ev_data['summary']}")
        for a in ev_data["ayahs"]:
            used_ayah_refs.add(a["ref"])
        total_ayahs += len(ev_data["ayahs"])
        total_blocks += len(ev_data["narrative"])

        print(f"   ✅ {ev_id}: {len(ev_data['narrative'])} فقرة مدمجة | {len(ev_data['ayahs'])} آية محققة")
        time.sleep(1.5)

    final_events_dir = story_dir / "events"
    if final_events_dir.exists():
        shutil.rmtree(final_events_dir)
    tmp_events_dir.rename(final_events_dir)

    print("\n" + "=" * 65)
    print(f"🎉 اكتمل الجمع الموثق: {len(timeline)} حدث بالترتيب | {total_blocks} فقرة | {total_ayahs} آية")
    print("=" * 65)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ خطأ فادح: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
