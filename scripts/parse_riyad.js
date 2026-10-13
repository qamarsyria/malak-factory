/**
 * يحوّل riyad-salihin.js إلى JSON نظيف.
 * يعالج النمطين: أبواب مستقلة + كتب بأبواب متداخلة.
 */
const fs = require("fs");
const path = require("path");

const INPUT = path.resolve(__dirname, "../data/riyad-salihin.js");
const OUTPUT = path.resolve(__dirname, "../data/riyad-salihin.json");

const riyad = require(INPUT);
const out = [];

function pushHadith(h, chapter, book) {
  if (!h || typeof h !== "object") return;
  if (!h.المتن || !h.رقم_الحديث) return;
  const text = String(h.المتن).replace(/\s+/g, " ").trim();
  if (text.length < 20) return;
  out.push({
    id: `riyad-${h.رقم_الحديث}`,
    number: String(h.رقم_الحديث),
    narrator: (h.الراوي || "").trim(),
    text: text,
    source: (h.المخرج || "").trim(),
    chapter: chapter || "",
    book: book || "",
    collection: "riyad-salihin",
  });
}

for (const [, item] of Object.entries(riyad)) {
  if (!item || typeof item !== "object") continue;

  // النمط 2: كتاب بأبواب متداخلة
  if (Array.isArray(item.الأبواب)) {
    for (const باب of item.الأبواب) {
      if (!باب || !Array.isArray(باب.الأحاديث)) continue;
      for (const h of باب.الأحاديث) {
        pushHadith(h, باب.اسم_الباب || "", item.اسم_الكتاب || "");
      }
    }
    continue;
  }

  // النمط 1: باب مستقل
  if (Array.isArray(item.الأحاديث)) {
    for (const h of item.الأحاديث) {
      pushHadith(h, item.اسم_الباب || "", "المقدمات");
    }
  }
}

fs.writeFileSync(OUTPUT, JSON.stringify(out, null, 2), "utf-8");
console.log(`✅ رياض الصالحين: ${out.length} حديثاً → ${OUTPUT}`);
