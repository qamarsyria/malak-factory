#!/usr/bin/env node
/**
 * extract_sources.js — يستخرج كل مواضيع المنصة إلى data/auto-generated/sources.json
 *
 * المصادر:
 *   - 10 صفحات HTML (prophets, sahaba, quranic-stories, ghazawat, seerah,
 *     shuhada, fadail, fiqh, zuhd, sihr)
 *   - 10 ملفات women-data/*.js
 *
 * الاستخدام:
 *   PLATFORM_ROOT=../qamar-malak node scripts/extract_sources.js
 *   PLATFORM_ROOT=/path/to/qamar-malak node scripts/extract_sources.js
 *   (الافتراضي: ../qamar-malak — أي شقيق لـ malak-factory)
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const PLATFORM_ROOT = process.env.PLATFORM_ROOT || path.resolve(ROOT, '../qamar-malak');
const OUT_DIR = path.join(ROOT, 'data', 'auto-generated');
const OUT_FILE = path.join(OUT_DIR, 'sources.json');

console.log('═══════════════════════════════════════════════════');
console.log('  استخراج مصادر المنصة → sources.json');
console.log('═══════════════════════════════════════════════════');
console.log('PLATFORM_ROOT:', PLATFORM_ROOT);
console.log('OUT_FILE:', OUT_FILE);
console.log('');

if (!fs.existsSync(PLATFORM_ROOT)) {
    console.error(`❌ PLATFORM_ROOT غير موجود: ${PLATFORM_ROOT}`);
    console.error('   ضع مسار المنصة في متغير PLATFORM_ROOT أو انسخها بجانب malak-factory.');
    process.exit(1);
}


// ═══════════════════════════════════════════════════════════════
// Sandbox: stubs للـ DOM (كي لا تنهار السكربتات عند التحميل)
// ═══════════════════════════════════════════════════════════════
function makeSandbox() {
    const noop = () => {};
    const fakeElement = {
        style: {},
        textContent: '',
        innerHTML: '',
        addEventListener: noop,
        appendChild: noop,
        querySelector: () => null,
        querySelectorAll: () => [],
        classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
        getAttribute: () => null,
        setAttribute: noop,
        dataset: {},
    };

    const sandbox = {
        window: {},
        document: {
            readyState: 'complete',
            addEventListener: noop,
            getElementById: () => fakeElement,
            querySelector: () => null,
            querySelectorAll: () => [],
            createElement: () => fakeElement,
            body: fakeElement,
            head: fakeElement,
        },
        localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
        sessionStorage: { getItem: () => null, setItem: noop },
        navigator: { serviceWorker: { register: () => Promise.resolve() } },
        console: console,
        setTimeout: setTimeout,
        clearTimeout: clearTimeout,
        setInterval: setInterval,
        clearInterval: clearInterval,
        fetch: () => Promise.reject(new Error('no fetch in extraction')),
        URLSearchParams: URLSearchParams,
        IntersectionObserver: class { observe() {} disconnect() {} unobserve() {} },
        location: { search: '', href: '', hostname: '' },
        alert: noop,
        confirm: () => false,
        prompt: () => null,
    };

    // window كـ reference لـ sandbox نفسه (كي window.localStorage يعمل)
    sandbox.window = sandbox;
    return sandbox;
}


// ═══════════════════════════════════════════════════════════════
// استخراج متغيرات من HTML (كل <script> بدون src)
// ═══════════════════════════════════════════════════════════════
function extractFromHtml(htmlPath, varNames) {
    const content = fs.readFileSync(htmlPath, 'utf-8');

    const scripts = [];
    const regex = /<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
    let m;
    while ((m = regex.exec(content)) !== null) {
        scripts.push(m[1]);
    }

    if (scripts.length === 0) {
        throw new Error(`لم يُعثر على <script> داخلي في ${htmlPath}`);
    }

    const combined = scripts.join('\n;\n');

    const returnParts = varNames.map(n =>
        `try { out[${JSON.stringify(n)}] = (typeof ${n} !== 'undefined') ? ${n} : null; } catch(e) { out[${JSON.stringify(n)}] = null; }`
    ).join('\n');

    const wrapped = `(function() {
${combined}
;
const out = {};
${returnParts}
return out;
})();`;

    const sandbox = makeSandbox();
    try {
        return vm.runInNewContext(wrapped, sandbox, { timeout: 15000 });
    } catch (e) {
        throw new Error(`فشل تشغيل ${path.basename(htmlPath)}: ${e.message}`);
    }
}


// ═══════════════════════════════════════════════════════════════
// استخراج من ملف JS (women-data/*)
// ═══════════════════════════════════════════════════════════════
function extractFromJs(jsPath, varNames) {
    const content = fs.readFileSync(jsPath, 'utf-8');
    const sandbox = makeSandbox();
    try {
        vm.runInNewContext(content, sandbox, { timeout: 15000 });
    } catch (e) {
        throw new Error(`فشل تشغيل ${path.basename(jsPath)}: ${e.message}`);
    }
    const out = {};
    varNames.forEach(n => {
        out[n] = sandbox[n] || (sandbox.window && sandbox.window[n]) || null;
    });
    return out;
}


// ═══════════════════════════════════════════════════════════════
// تطبيع الأقسام إلى {h, p, text, ayah}
// ═══════════════════════════════════════════════════════════════
function normalizeSections(sections) {
    if (!Array.isArray(sections)) return [];
    return sections.map(s => {
        const out = {};
        if (s.h) out.h = String(s.h);
        if (s.p) out.p = String(s.p);
        if (s.text) out.text = String(s.text);
        if (s.ayah === true) out.ayah = true;
        return out;
    }).filter(s => s.p || s.text);
}


// ═══════════════════════════════════════════════════════════════
// ترجمة أسماء الأنبياء إلى slug إنجليزي (يدوي للدقة)
// ═══════════════════════════════════════════════════════════════
const PROPHET_SLUGS = {
    'آدم': 'adam', 'إدريس': 'idris', 'نوح': 'nuh', 'هود': 'hud',
    'صالح': 'salih', 'إبراهيم': 'ibrahim', 'لوط': 'lut', 'إسماعيل': 'ismail',
    'إسحاق': 'ishaq', 'يعقوب': 'yaqub', 'يوسف': 'yusuf', 'أيوب': 'ayyub',
    'شعيب': 'shuaib', 'موسى': 'musa', 'هارون': 'harun', 'ذو الكفل': 'dhul-kifl',
    'داود': 'dawud', 'سليمان': 'sulayman', 'إلياس': 'ilyas', 'اليسع': 'alyasa',
    'يونس': 'yunus', 'زكريا': 'zakariya', 'يحيى': 'yahya', 'عيسى': 'isa',
    'محمد': 'muhammad',
};

function slugifyProphet(name) {
    for (const key of Object.keys(PROPHET_SLUGS)) {
        if (name.includes(key)) return PROPHET_SLUGS[key];
    }
    return 'prophet-' + name.replace(/\s+/g, '-').slice(0, 20);
}


// ═══════════════════════════════════════════════════════════════
// معالجة prophets / sahaba / ghazawat / quranic-stories
// (بنية موحدة: name, icon, category, title/role/year/surah, brief, sections, refs)
// ═══════════════════════════════════════════════════════════════
function normalizeGeneric(items, sourceType, slugPrefix) {
    return items.map((item, idx) => {
        const fallbackId = `${slugPrefix}-${String(idx + 1).padStart(3, '0')}`;
        const name = item.name || item.title || '';
        const slug = sourceType === 'prophets' ? slugifyProphet(name) : fallbackId;
        return {
            source_id: `${sourceType}/${slug}`,
            source_type: sourceType,
            index: idx + 1,
            name: String(name),
            title: String(item.title || ''),
            subtitle: String(item.role || item.year || item.surah || ''),
            category: String(item.category || ''),
            icon: String(item.icon || ''),
            brief: String(item.brief || ''),
            sections: normalizeSections(item.sections),
            refs: String(item.refs || ''),
        };
    });
}


// ═══════════════════════════════════════════════════════════════
// shuhada — 3 مصفوفات (BADR + UHUD + FATH)
// ═══════════════════════════════════════════════════════════════
function normalizeShuhada(badr, uhud, fath) {
    const all = [
        ...(badr || []).map(x => ({ ...x, group: 'badr' })),
        ...(uhud || []).map(x => ({ ...x, group: 'uhud' })),
        ...(fath || []).map(x => ({ ...x, group: 'fath' })),
    ];
    return all.map((item, idx) => ({
        source_id: `shuhada/sh-${String(idx + 1).padStart(3, '0')}`,
        source_type: 'shuhada',
        index: idx + 1,
        name: String(item.name || ''),
        title: String(item.role || ''),
        subtitle: String(item.role || ''),
        category: item.group === 'badr' ? 'شهداء بدر'
                : item.group === 'uhud' ? 'شهداء أحد'
                : 'شهداء الفتوحات',
        icon: String(item.icon || ''),
        brief: String(item.brief || ''),
        sections: normalizeSections(item.sections),
        refs: String(item.refs || ''),
    }));
}


// ═══════════════════════════════════════════════════════════════
// fadail / zuhd / sihr — quotes → sections
// ═══════════════════════════════════════════════════════════════
function normalizeQuotes(items, sourceType, slugPrefix) {
    return items.map((item, idx) => {
        const sections = [];

        if (item.intro) {
            sections.push({ h: 'مقدمة', p: String(item.intro) });
        }

        (item.quotes || []).forEach((q, qi) => {
            const label = q.type === 'ayah' ? 'آية'
                        : q.type === 'hadith' ? 'حديث'
                        : q.type === 'athar' ? 'أثر' : 'دليل';
            const text = q.text ? String(q.text) : '';
            const ref = q.ref ? String(q.ref) : '';
            if (text) {
                sections.push({
                    h: `${label} ${qi + 1}`,
                    p: text + (ref ? ` [${ref}]` : ''),
                });
            }
        });

        return {
            source_id: `${sourceType}/${slugPrefix}-${String(idx + 1).padStart(3, '0')}`,
            source_type: sourceType,
            index: idx + 1,
            name: String(item.title || ''),
            title: String(item.title || ''),
            subtitle: '',
            category: '',
            icon: String(item.icon || ''),
            brief: String(item.intro || ''),
            sections: sections,
            refs: '',
        };
    });
}


// ═══════════════════════════════════════════════════════════════
// fiqh — topics → sections
// ═══════════════════════════════════════════════════════════════
function normalizeFiqh(items) {
    return items.map((item, idx) => {
        const sections = [];

        if (item.intro) {
            sections.push({ h: 'مقدمة', p: String(item.intro) });
        }

        (item.topics || []).forEach(topic => {
            const itemsList = Array.isArray(topic.items) ? topic.items : [];
            let paragraph = itemsList.map(x => `• ${x}`).join('\n');

            if (topic.quote) {
                const q = topic.quote;
                const qtext = q.text ? String(q.text) : '';
                const qref = q.ref ? String(q.ref) : '';
                if (qtext) paragraph += `\n\n${qtext}${qref ? ` [${qref}]` : ''}`;
            }

            if (paragraph) {
                sections.push({
                    h: String(topic.title || ''),
                    p: paragraph,
                });
            }
        });

        return {
            source_id: `fiqh/fiqh-${String(idx + 1).padStart(3, '0')}`,
            source_type: 'fiqh',
            index: idx + 1,
            name: String(item.title || ''),
            title: String(item.title || ''),
            subtitle: '',
            category: '',
            icon: String(item.icon || ''),
            brief: String(item.intro || ''),
            sections: sections,
            refs: '',
        };
    });
}


// ═══════════════════════════════════════════════════════════════
// women — 10 ملفات
// ═══════════════════════════════════════════════════════════════
function normalizeWomenSection(sectionData, sectionId, sectionTitle) {
    if (!sectionData || !Array.isArray(sectionData.items)) {
        return { items: [], count: 0 };
    }
    const items = sectionData.items.map((item, idx) => {
        const blocks = item.blocks || [];

        const sections = blocks.map(b => {
            let paragraph = '';
            if (b.p) paragraph += String(b.p);
            if (Array.isArray(b.list)) {
                const listText = b.list.map(x => `• ${x}`).join('\n');
                paragraph += (paragraph ? '\n\n' : '') + listText;
            }
            return { h: String(b.h || ''), p: paragraph };
        }).filter(s => s.p);

        return {
            source_id: `women/${sectionId}/${item.id || String(idx + 1).padStart(3, '0')}`,
            source_type: 'women',
            section_id: sectionId,
            section_title: sectionTitle,
            index: idx + 1,
            name: String(item.title || ''),
            title: String(item.title || ''),
            subtitle: '',
            category: sectionTitle,
            icon: String(item.icon || ''),
            brief: String(item.summary || ''),
            sections: sections,
            quran_evidence: (item.quran || []).map(q => ({
                text: String(q.text || ''),
                ref: String(q.ref || ''),
            })),
            hadith_evidence: (item.hadith || []).map(h => ({
                text: String(h.text || ''),
                ref: String(h.ref || ''),
                grade: String(h.grade || ''),
            })),
            refs: '',
        };
    });
    return { items, count: items.length };
}


// ═══════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════
function main() {
    fs.mkdirSync(OUT_DIR, { recursive: true });

    const result = {
        meta: {
            extracted_at: new Date().toISOString(),
            platform_root: PLATFORM_ROOT,
            total_topics: 0,
            sources_breakdown: {},
        },
        prophets: [],
        sahaba: [],
        quranic_stories: [],
        ghazawat: [],
        seerah: [],
        shuhada: [],
        fadail: [],
        fiqh: [],
        zuhd: [],
        sihr: [],
        women: { sections: {}, total: 0 },
    };

    // ─── 1. prophets ───
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'prophets.html'), ['PROPHETS']);
        if (vars.PROPHETS) {
            result.prophets = normalizeGeneric(vars.PROPHETS, 'prophets', 'prophet');
            console.log(`✅ prophets: ${result.prophets.length}`);
        } else {
            console.log('⚠️ PROPHETS غير موجود في prophets.html');
        }
    } catch (e) {
        console.error('❌ prophets:', e.message);
    }

    // ─── 2. sahaba ───
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'sahaba.html'), ['SAHABA']);
        if (vars.SAHABA) {
            result.sahaba = normalizeGeneric(vars.SAHABA, 'sahaba', 'sahabi');
            console.log(`✅ sahaba: ${result.sahaba.length}`);
        }
    } catch (e) {
        console.error('❌ sahaba:', e.message);
    }

    // ─── 3. quranic-stories ───
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'quranic-stories.html'), ['STORIES']);
        if (vars.STORIES) {
            result.quranic_stories = normalizeGeneric(vars.STORIES, 'quranic-stories', 'qstory');
            console.log(`✅ quranic-stories: ${result.quranic_stories.length}`);
        }
    } catch (e) {
        console.error('❌ quranic-stories:', e.message);
    }

    // ─── 4. ghazawat ───
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'ghazawat.html'), ['BATTLES']);
        if (vars.BATTLES) {
            result.ghazawat = normalizeGeneric(vars.BATTLES, 'ghazawat', 'ghazwa');
            console.log(`✅ ghazawat: ${result.ghazawat.length}`);
        }
    } catch (e) {
        console.error('❌ ghazawat:', e.message);
    }

    // ─── 5. seerah (sections بدون h) ───
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'seerah.html'), ['TIMELINE']);
        if (vars.TIMELINE) {
            result.seerah = vars.TIMELINE.map((item, idx) => ({
                source_id: `seerah/seerah-${String(idx + 1).padStart(3, '0')}`,
                source_type: 'seerah',
                index: idx + 1,
                name: String(item.title || ''),
                title: String(item.title || ''),
                subtitle: String(item.year || ''),
                category: '',
                icon: '',
                brief: String(item.brief || ''),
                sections: normalizeSections(item.sections),
                refs: String(item.refs || ''),
            }));
            console.log(`✅ seerah: ${result.seerah.length}`);
        }
    } catch (e) {
        console.error('❌ seerah:', e.message);
    }

    // ─── 6. shuhada (3 مصفوفات) ───
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'shuhada.html'), ['BADR', 'UHUD', 'FATH']);
        result.shuhada = normalizeShuhada(vars.BADR, vars.UHUD, vars.FATH);
        console.log(`✅ shuhada: ${result.shuhada.length}`);
    } catch (e) {
        console.error('❌ shuhada:', e.message);
    }

    // ─── 7. fadail ───
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'fadail.html'), ['FADAIL_DATA']);
        if (vars.FADAIL_DATA) {
            result.fadail = normalizeQuotes(vars.FADAIL_DATA, 'fadail', 'fadl');
            console.log(`✅ fadail: ${result.fadail.length}`);
        }
    } catch (e) {
        console.error('❌ fadail:', e.message);
    }

    // ─── 8. fiqh ───
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'fiqh.html'), ['FIQH_DATA']);
        if (vars.FIQH_DATA) {
            result.fiqh = normalizeFiqh(vars.FIQH_DATA);
            console.log(`✅ fiqh: ${result.fiqh.length}`);
        }
    } catch (e) {
        console.error('❌ fiqh:', e.message);
    }

    // ─── 9. zuhd ───
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'zuhd.html'), ['ZUHD_DATA']);
        if (vars.ZUHD_DATA) {
            result.zuhd = normalizeQuotes(vars.ZUHD_DATA, 'zuhd', 'zuhd');
            console.log(`✅ zuhd: ${result.zuhd.length}`);
        }
    } catch (e) {
        console.error('❌ zuhd:', e.message);
    }

    // ─── 10. sihr ───
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'sihr.html'), ['SIHR_DATA']);
        if (vars.SIHR_DATA) {
            result.sihr = normalizeQuotes(vars.SIHR_DATA, 'sihr', 'sihr');
            console.log(`✅ sihr: ${result.sihr.length}`);
        }
    } catch (e) {
        console.error('❌ sihr:', e.message);
    }

    // ─── 11. women (10 ملفات) ───
    const WOMEN_FILES = [
        ['aqeedah',      'WOMEN_SECTION_AQEEDAH',     'العقيدة والمكانة'],
        ['ibadat',       'WOMEN_SECTION_IBADAT',      'العبادات'],
        ['clothing',     'WOMEN_SECTION_CLOTHING',    'اللباس والزينة'],
        ['marriage',     'WOMEN_SECTION_MARRIAGE',    'الزواج والحياة الزوجية'],
        ['motherhood',   'WOMEN_SECTION_MOTHERHOOD',  'الأمومة والتربية'],
        ['character',    'WOMEN_SECTION_CHARACTER',   'الأخلاق والسلوك'],
        ['society',      'WOMEN_SECTION_SOCIETY',     'المرأة في المجتمع'],
        ['special',      'WOMEN_SECTION_SPECIAL',     'أحكام خاصة بالمرأة'],
        ['models',       'WOMEN_SECTION_MODELS',      'النماذج النسائية'],
        ['prohibitions', 'WOMEN_SECTION_PROHIBITIONS','المحظورات والمناسبات'],
    ];

    let womenTotal = 0;
    WOMEN_FILES.forEach(([slug, varName, title]) => {
        try {
            const jsPath = path.join(PLATFORM_ROOT, 'women-data', `women-${slug}.js`);
            const vars = extractFromJs(jsPath, [varName]);
            if (vars[varName]) {
                const normalized = normalizeWomenSection(vars[varName], slug, title);
                result.women.sections[slug] = {
                    title: vars[varName].title || title,
                    desc: vars[varName].desc || '',
                    items: normalized.items,
                };
                womenTotal += normalized.count;
                console.log(`✅ women/${slug}: ${normalized.count}`);
            } else {
                console.log(`⚠️ women/${slug}: ${varName} غير موجود`);
            }
        } catch (e) {
            console.error(`❌ women/${slug}:`, e.message);
        }
    });
    result.women.total = womenTotal;
    console.log(`✅ women (مجموع): ${womenTotal}`);

    // ─── الإجمالي ───
    const breakdown = {
        prophets: result.prophets.length,
        sahaba: result.sahaba.length,
        quranic_stories: result.quranic_stories.length,
        ghazawat: result.ghazawat.length,
        seerah: result.seerah.length,
        shuhada: result.shuhada.length,
        fadail: result.fadail.length,
        fiqh: result.fiqh.length,
        zuhd: result.zuhd.length,
        sihr: result.sihr.length,
        women: result.women.total,
    };
    const total = Object.values(breakdown).reduce((a, b) => a + b, 0);

    result.meta.total_topics = total;
    result.meta.sources_breakdown = breakdown;

    fs.writeFileSync(OUT_FILE, JSON.stringify(result, null, 2), 'utf-8');

    console.log('');
    console.log('═══════════════════════════════════════════════════');
    console.log(`✅ تم الاستخراج: ${total} موضوعاً`);
    console.log(`📄 ${OUT_FILE}`);
    console.log('═══════════════════════════════════════════════════');
    console.log('الملخص:');
    Object.entries(breakdown).forEach(([k, v]) => {
        console.log(`  ${k.padEnd(20)} ${v}`);
    });
    console.log('═══════════════════════════════════════════════════');

    if (total === 0) {
        console.error('⚠️ لم يُستخرج أي موضوع — تحقق من PLATFORM_ROOT وهيكل الملفات');
        process.exit(1);
    }
}

try {
    main();
} catch (e) {
    console.error('❌ فشل عام:', e.message);
    console.error(e.stack);
    process.exit(1);
}
