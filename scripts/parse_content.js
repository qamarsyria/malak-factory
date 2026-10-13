/**
 * يحوّل faq-data.js و hadith-db.js إلى JSON نظيف.
 * يستخدم vm لالتقاط المتغيرات حتى بدون module.exports.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadExports(filePath) {
  const content = fs.readFileSync(filePath, "utf-8");
  const sandbox = {
    module: { exports: {} },
    exports: {},
    console: console,
    require: require,
  };
  vm.createContext(sandbox);
  try {
    vm.runInContext(content, sandbox, { timeout: 5000 });
  } catch (e) {
    console.warn(`⚠️ فشل تشغيل ${filePath}: ${e.message}`);
    return null;
  }
  return sandbox.module.exports;
}

function deepCollectObjects(node, seen = new Set()) {
  const results = [];
  if (!node || typeof node !== "object") return results;
  if (seen.has(node)) return results;
  seen.add(node);

  if (Array.isArray(node)) {
    for (const item of node) {
      results.push(...deepCollectObjects(item, seen));
    }
    return results;
  }

  // هل هذا الكائن يشبه مسألة/حديث؟
  const hasQA =
    (node.q || node.question || node.سؤال) &&
    (node.a || node.answer || node.جواب);
  const hasHadith =
    (node.text || node.hadith || node.متن || node.المتن) &&
    (node.source || node.المخرج || node.book);

  if (hasQA || hasHadith) {
    results.push(node);
    return results;
  }

  // ابحث في القيم الداخلية
  for (const v of Object.values(node)) {
    if (v && typeof v === "object") {
      results.push(...deepCollectObjects(v, seen));
    }
  }
  return results;
}

function normalize(item, type) {
  const q =
    item.q || item.question || item.سؤال || item.title || item.عنوان || "";
  const a =
    item.a ||
    item.answer ||
    item.جواب ||
    item.text ||
    item.حديث ||
    item.متن ||
    item.المتن ||
    "";
  const src =
    item.source ||
    item.المخرج ||
    item.book ||
    item.الكتاب ||
    (type === "hadith" ? "من الصحيحين" : "");

  if (!q || !a) return null;
  if (String(a).length < 30) return null;

  return {
    id: item.id || `${type}-${Math.random().toString(36).slice(2, 9)}`,
    question: String(q).replace(/\s+/g, " ").trim(),
    answer: String(a).replace(/\s+/g, " ").trim().slice(0, 800),
    source: String(src).replace(/\s+/g, " ").trim(),
    type: type,
  };
}

function processFile(filePath, type, outputPath) {
  if (!fs.existsSync(filePath)) {
    console.log(`⏭️  ${filePath} غير موجود، تخطّي`);
    return 0;
  }
  const exp = loadExports(filePath);
  if (!exp) return 0;

  const raw = deepCollectObjects(exp);
  const items = raw.map((o) => normalize(o, type)).filter(Boolean);
  fs.writeFileSync(outputPath, JSON.stringify(items, null, 2), "utf-8");
  console.log(`✅ ${type}: ${items.length} عنصراً → ${outputPath}`);
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
