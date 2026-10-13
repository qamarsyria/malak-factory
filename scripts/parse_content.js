/**
 * parse_content.js — النسخة النهائية (v3)
 * تستخدم vm.runInThisContext مع IIFE لإرجاع المتغيرات.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");

function diagnose(filePath) {
    const p = path.resolve(filePath);
    console.log(`\n🔎 فحص: ${p}`);
    if (!fs.existsSync(p)) {
        console.log(`   ❌ الملف غير موجود!`);
        return null;
    }
    const content = fs.readFileSync(p, "utf-8");
    const size = content.length;
    console.log(`   📏 الحجم: ${size} حرف`);
    if (size < 50) {
        console.log(`   ❌ الملف شبه فارغ — أعد رفعه`);
        return null;
    }
    console.log(`   📄 أول 120 حرفاً: ${content.substring(0, 120).replace(/\n/g, "⏎")}`);
    return content;
}

function loadVars(filePath, expectedNames) {
    const content = diagnose(filePath);
    if (!content) return null;

    const returnParts = expectedNames.map(name =>
        `out.${name} = (typeof ${name} !== 'undefined') ? ${name} : null;`
    ).join("\n            ");

    const wrapped = `
        (function() {
            ${content}
            const out = {};
            ${returnParts}
            return out;
        })()
    `;

    try {
        const result = vm.runInThisContext(wrapped, {
            filename: filePath,
            timeout: 5000,
        });
        return result;
    } catch (e) {
        console.warn(`   ❌ فشل التشغيل: ${e.message}`);
        return null;
    }
}

function collectFAQ(arr) {
    if (!Array.isArray(arr)) {
        console.log(`   ⚠️ FAQ_DATA ليس مصفوفة — نوعه: ${typeof arr}`);
        return [];
    }
    console.log(`   ✓ FAQ_DATA: مصفوفة طولها ${arr.length}`);
    const out = [];
    for (let i = 0; i < arr.length; i++) {
        const item = arr[i];
        if (!item || typeof item !== "object") continue;
        const q = (item.q || item.question || "").toString().trim();
        const a = (item.a || item.answer || "").toString().trim();
        if (!q || a.length < 30) continue;

        const evidence = Array.isArray(item.e)
            ? item.e.map(ev => ({
                type: ev.t || "",
                text: (ev.x || "").replace(/[«»﴿﴾]/g, "").trim(),
                ref: (ev.r || "").trim(),
            }))
            : [];

        out.push({
            id: `faq-${String(i + 1).padStart(3, "0")}`,
            question: q.replace(/\s+/g, " "),
            answer: a.replace(/\s+/g, " ").slice(0, 800),
            category: (item.c || "").trim(),
            evidence: evidence,
            source: evidence.length > 0 ? evidence[0].ref : "",
            type: "faq",
        });
    }
    console.log(`   ✓ استُخرج ${out.length} مسألة صالحة`);
    return out;
}

function collectHadith(obj) {
    if (!obj || typeof obj !== "object") {
        console.log(`   ⚠️ HADITH_DB ليس كائناً — نوعه: ${typeof obj}`);
        return [];
    }
    const keys = Object.keys(obj);
    console.log(`   ✓ HADITH_DB: كائن بعدد مفاتيح ${keys.length}`);
    const out = [];
    for (const key of keys) {
        const item = obj[key];
        if (!item || typeof item !== "object") continue;
        const text = (item.text || item.hadith || item.متن || "").toString().trim();
        if (text.length < 20) continue;
        const book = (item.book || "").trim();
        const num = item.num ? ` رقم ${item.num}` : "";
        out.push({
            id: `hadith-${String(book || "x").replace(/\s/g, "")}-${item.num || "0"}`,
            question: text.slice(0, 100) + (text.length > 100 ? "…" : ""),
            answer: text.replace(/\s+/g, " "),
            source: book + num,
            type: "hadith",
        });
    }
    console.log(`   ✓ استُخرج ${out.length} حديثاً صالحاً`);
    return out;
}

console.log("\n═══════════════════════════════════════════");
console.log("  تشخيص parse_content.js");
console.log("═══════════════════════════════════════════");

console.log("\n📘 تحميل FAQ_DATA...");
const faqVars = loadVars(path.join(ROOT, "data/faq-data.js"), ["FAQ_DATA"]);

let faqItems = [];
if (faqVars && faqVars.FAQ_DATA) {
    faqItems = collectFAQ(faqVars.FAQ_DATA);
}
const faqOut = path.join(ROOT, "data/faq-data.json");
fs.writeFileSync(faqOut, JSON.stringify(faqItems, null, 2), "utf-8");
console.log(`✅ faq: ${faqItems.length} عنصراً → ${faqOut}`);

console.log("\n📗 تحميل HADITH_DB...");
const hadVars = loadVars(path.join(ROOT, "data/hadith-db.js"), ["HADITH_DB"]);

let hadItems = [];
if (hadVars && hadVars.HADITH_DB) {
    hadItems = collectHadith(hadVars.HADITH_DB);
}
const hadOut = path.join(ROOT, "data/hadith-db.json");
fs.writeFileSync(hadOut, JSON.stringify(hadItems, null, 2), "utf-8");
console.log(`✅ hadith: ${hadItems.length} عنصراً → ${hadOut}`);

console.log(`\n📊 المجموع: ${faqItems.length + hadItems.length}\n`);
