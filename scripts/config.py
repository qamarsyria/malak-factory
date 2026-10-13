/**
 * يحوّل faq-data.js و hadith-db.js إلى JSON نظيف.
 * يستخدم new Function() لالتقاط const declarations.
 */
const fs = require("fs");
const path = require("path");

function loadVars(filePath) {
  const content = fs.readFileSync(filePath, "utf-8");

  // التقط كل الأسماء المعلنة بـ const/let/var في بداية السطر
  const names = [...content.matchAll(/^(?:const|let|var)\s+([A-Za-z_\u0600-\u06FF][\w\u0600-\u06FF]*)\s*=/gm)]
    .map((m) => m[1]);

  if (names.length === 0) {
    console.warn(`⚠️  لا توجد متغيرات معلنة في ${filePath}`);
    return null;
  }

  // غلّف الكود داخل دالة وأرجِع المتغيرات صراحةً
  const wrapped = `
    const window = {};
    const document = {};
    ${content}
    return { ${names.join(", ")} };
  `;

  try {
    const fn = new Function(wrapped);
    return fn();
  } catch (e) {
    console.warn(`⚠️  فشل تشغيل ${filePath}: ${e.message}`);
    return null;
  }
}

function deepCollect(node, seen = new Set()) {
  const out = [];
  if (!node || typeof node !== "object") return out;
  if (seen.has(node)) return out;
  seen.add(node);

  if (Array.isArray(node)) {
    for (const item of node) out.push(...deepCollect(item, seen));
    return out;
  }

  // هل هذا كائن FAQ؟ (q + a)
  const isFaq = (node.q || node.question) && (node.a || node.answer);
  // هل هذا كائن حديث من قاعدة البيانات؟ (text + book + num)
  const isHadith =
    (node.text || node.hadith || node.متن) &&
    (node.book || node.source || node.num);

  if (isFaq || isHadith) {
    out.push(node);
    return out;
  }

  for (const v of Object.values(node)) {
    if (v && typeof v === "object") out.push(...deepCollect(v, seen));
  }
  return out;
}

function normalizeFaq(item, idx) {
  const q = item.q || item.question || "";
  const a = item.a || item.answer || "";
  if (!q || !a) return null;
  const aStr = String(a).replace(/\s+/g, " ").trim();
  if (aStr.length < 30) return null;

  // نحوّل الأدلة (e) إلى نص بسيط
  const evidence = Array.isArray(item.e)
    ? item.e.map((ev) => ({
        type: ev.t || "",
        text: (ev.x || "").replace(/[«»﴿﴾]/g, "").trim(),
        ref: (ev.r || "").trim(),
      }))
    : [];

  return {
    id: `faq-${String(idx + 1).padStart(3, "0")}`,
    question: String(q).replace(/\s+/g, " ").trim(),
    answer: aStr.slice(0, 800),
    category: (item.c || "").trim(),
    evidence: evidence,
    source: evidence.length > 0 ? evidence[0].ref : "",
    type: "faq",
  };
}

function normalizeHadith(item) {
  const text = item.text || item.hadith || item.متن || "";
  const clean = String(text).replace(/\s+/g, " ").trim();
  if (clean.length < 20) return null;

  const book = (item.book || "").trim();
  const num = item.num ? ` رقم ${item.num}` : "";
  const source = book + num;

  return {
    id: `hadith-${String(item.book || "x").replace(/\s/g, "")}-${item.num || "0"}`,
    question: clean.slice(0, 100) + (clean.length > 100 ? "…" : ""),
    answer: clean,
    source: source,
    type: "hadith",
  };
}

function processFile(filePath, type, outputPath) {
  if (!fs.existsSync(filePath)) {
    console.log(`⏭️  ${filePath} غير موجود`);
    return 0;
  }

  const vars = loadVars(filePath);
  if (!vars) {
    fs.writeFileSync(outputPath, "[]", "utf-8");
    return 0;
  }

  const raw = deepCollect(vars);

  let items;
  if (type === "faq") {
    items = raw.map((o, i) => normalizeFaq(o, i)).filter(Boolean);
  } else {
    items = raw.map(normalizeHadith).filter(Boolean);
  }

  fs.writeFileSync(outputPath, JSON.stringify(items, null, 2), "utf-8");
  console.log(`✅ ${type}: ${items.length} عنصراً → ${outputPath}`);

  // اطبع عيّنة للتحقق
  if (items.length > 0) {
    console.log(`   أول عنصر: ${JSON.stringify(items[0], null, 2).slice(0, 400)}`);
  }
  return items.length;
}

const ROOT = path.resolve(__dirname, "..");
const faqN = processFile(
  path.join(ROOT, "data/faq-data.js"),
  "faq",
  path.join(ROOT, "data/faq-data.json")
);
const hadN = processFile(
  path.join(ROOT, "data/hadith-db.js"),
  "hadith",
  path.join(ROOT, "data/hadith-db.json")
);

console.log(`\n📊 المجموع: ${faqN + hadN}`);
