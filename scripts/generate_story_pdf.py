"""
generate_story_pdf.py — تحويل القصة إلى كتاب PDF عربي منسق
يقرأ الأحداث (Collected)، ويصنع كتاباً بغلاف وعناوين، مع تمييز الآيات.
"""

import os, sys, json, re
from pathlib import Path
from fpdf import FPDF
import arabic_reshaper
from bidi.algorithm import get_display

_REPO_ROOT = Path(__file__).resolve().parent.parent
STORIES_DIR = _REPO_ROOT / "data" / "stories"
STORIES_JSON = _REPO_ROOT / "data" / "library" / "stories.json"
OUTPUT_DIR = _REPO_ROOT / "out"

# مسار الخط العربي (متثبت في الـ Workflow)
FONT_PATH = "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Bold.ttf"
if not os.path.exists(FONT_PATH):
    FONT_PATH = "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf"

def shape_text(text):
    """تجهيز النص العربي للطباعة"""
    if not text:
        return ""
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)

class StoryBook(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font("Amiri", "B", 10)
            self.set_text_color(150, 150, 150)
            self.cell(0, 10, shape_text("ملاك المعرفة — قصص الأنبياء"), align="C")
            self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font("Amiri", "", 10)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, shape_text(f"صفحة {self.page_no()}"), align="C")

def build_pdf(story_id: str):
    # 1. قراءة معلومات القصة
    library = json.loads(STORIES_JSON.read_text(encoding="utf-8"))
    story = next((s for s in library["stories"] if s["id"] == story_id), None)
    if not story:
        print(f"❌ القصة {story_id} غير موجودة.")
        sys.exit(1)

    story_title = story.get("title", story_id)
    collected_dir = STORIES_DIR / story_id / "collected" / "events"
    if not collected_dir.exists():
        print(f"❌ لا يوجد أحداث مجمعة: {collected_dir}")
        sys.exit(1)

    # 2. إعداد ملف الـ PDF
    pdf = StoryBook(format="A5") # مقاس كتاب
    pdf.add_font("Amiri", "", FONT_PATH, uni=True)
    pdf.add_font("Amiri", "B", FONT_PATH, uni=True)
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # --- صفحة الغلاف ---
    pdf.add_page()
    pdf.set_y(60)
    pdf.set_font("Amiri", "B", 30)
    pdf.set_text_color(184, 134, 11) # لون ذهبي
    pdf.cell(0, 20, shape_text("سلسلة قصص الأنبياء"), align="C", new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font("Amiri", "B", 24)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 20, shape_text(story_title), align="C", new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_y(-40)
    pdf.set_font("Amiri", "", 14)
    pdf.cell(0, 10, shape_text("إعداد: ملاك المعرفة"), align="C")

    # --- محتوى الأحداث ---
    for ev_dir in sorted(collected_dir.glob("event_*")):
        meta_file = ev_dir / "meta.json"
        narr_file = ev_dir / "narrative.json"
        ayah_file = ev_dir / "ayahs.json"

        title = ev_dir.name
        if meta_file.exists():
            title = json.loads(meta_file.read_text(encoding="utf-8")).get("title", title)

        # عنوان الحدث (فصل)
        pdf.add_page()
        pdf.set_font("Amiri", "B", 18)
        pdf.set_text_color(184, 134, 11)
        pdf.cell(0, 15, shape_text(title), align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(5)
        pdf.set_draw_color(184, 134, 11) # خط ذهبي تحت العنوان
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(10)

        # السرد
        if narr_file.exists():
            narratives = json.loads(narr_file.read_text(encoding="utf-8"))
            for block in narratives:
                text = block.get("text", "")
                if not text.strip(): continue
                
                # تمييز الآيات (لو النص فيه أقواس مزخرفة)
                if "﴿" in text or block.get("source", "").startswith("التفسير"):
                    pdf.set_font("Amiri", "B", 14)
                    pdf.set_text_color(0, 100, 0) # لون أخضر للآيات والتفسير
                else:
                    pdf.set_font("Amiri", "", 14)
                    pdf.set_text_color(50, 50, 50)

                pdf.multi_cell(0, 8, shape_text(text), align="R")
                pdf.ln(3)

        # الآيات المرفقة (لو مش مدمجة في السرد)
        if ayah_file.exists():
            ayahs = json.loads(ayah_file.read_text(encoding="utf-8"))
            if ayahs:
                pdf.ln(5)
                pdf.set_font("Amiri", "B", 12)
                pdf.set_text_color(150, 100, 0)
                pdf.cell(0, 10, shape_text("الآيات المرتبطة:"), align="R", new_x="LMARGIN", new_y="NEXT")
                
                pdf.set_font("Amiri", "", 13)
                pdf.set_text_color(0, 100, 0)
                for a in ayahs:
                    ayah_str = f"﴿ {a['text']} ﴾ [{a['ref']}]"
                    pdf.multi_cell(0, 8, shape_text(ayah_str), align="R")
                    pdf.ln(2)

    # 3. حفظ الملف
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{story_id}_book.pdf"
    pdf.output(str(out_path))
    print(f"✅ تم إنشاء الكتاب: {out_path}")
    return out_path

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("الاستخدام: python generate_story_pdf.py <story_id>")
        sys.exit(1)
    
    build_pdf(sys.argv[1])
