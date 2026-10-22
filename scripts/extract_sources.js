#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const PLATFORM_ROOT = process.env.PLATFORM_ROOT || path.resolve(ROOT, '../qamar-malak');
const OUT_DIR = path.join(ROOT, 'data', 'auto-generated');
const OUT_FILE = path.join(OUT_DIR, 'sources.json');

console.log('PLATFORM_ROOT:', PLATFORM_ROOT);
console.log('OUT_FILE:', OUT_FILE);

if (!fs.existsSync(PLATFORM_ROOT)) {
    console.error(`❌ PLATFORM_ROOT غير موجود: ${PLATFORM_ROOT}`);
    process.exit(1);
}

function makeSandbox() {
    const noop = () => {};
    const fakeElement = {
        style: {}, textContent: '', innerHTML: '',
        addEventListener: noop, appendChild: noop,
        querySelector: () => null, querySelectorAll: () => [],
        classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
        getAttribute: () => null, setAttribute: noop, dataset: {},
    };
    const sandbox = {
        window: {}, globalThis: {},
        document: {
            readyState: 'complete', addEventListener: noop,
            getElementById: () => fakeElement,
            querySelector: () => null, querySelectorAll: () => [],
            createElement: () => fakeElement,
            body: fakeElement, head: fakeElement,
        },
        localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
        sessionStorage: { getItem: () => null, setItem: noop },
        navigator: { serviceWorker: { register: () => Promise.resolve() } },
        console, setTimeout, clearTimeout, setInterval, clearInterval,
        fetch: () => Promise.reject(new Error('no fetch')),
        URLSearchParams,
        IntersectionObserver: class { observe() {} disconnect() {} unobserve() {} },
        location: { search: '', href: '', hostname: '' },
        alert: noop, confirm: () => false, prompt: () => null,
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    return sandbox;
}

function extractFromHtml(htmlPath, varNames) {
    const content = fs.readFileSync(htmlPath, 'utf-8');
    const scripts = [];
    const regex = /<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
    let m;
    while ((m = regex.exec(content)) !== null) scripts.push(m[1]);

    if (scripts.length === 0) {
        throw new Error(`لم يُعثر على <script> في ${htmlPath}`);
    }

    let combined = scripts.join('\n;\n');

    // ═══ الحل الجذري: تحويل const/let/var NAME = إلى globalThis.NAME = ═══
    for (const name of varNames) {
        const re = new RegExp(`\\b(?:const|let|var)\\s+${name}\\s*=`, 'g');
        combined = combined.replace(re, `globalThis.${name} =`);
    }

    const sandbox = makeSandbox();
    try {
        vm.runInNewContext(combined, sandbox, { timeout: 15000 });
    } catch (e) {
        throw new Error(`فشل تشغيل ${path.basename(htmlPath)}: ${e.message}`);
    }

    const out = {};
    varNames.forEach(n => {
        out[n] = sandbox[n] || (sandbox.window && sandbox.window[n]) || null;
    });
    return out;
}

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

function normalizeQuotes(items, sourceType, slugPrefix) {
    return items.map((item, idx) => {
        const sections = [];
        if (item.intro) sections.push({ h: 'مقدمة', p: String(item.intro) });
        (item.quotes || []).forEach((q, qi) => {
            const label = q.type === 'ayah' ? 'آية'
                        : q.type === 'hadith' ? 'حديث'
                        : q.type === 'athar' ? 'أثر' : 'دليل';
            const text = q.text ? String(q.text) : '';
            const ref = q.ref ? String(q.ref) : '';
            if (text) sections.push({ h: `${label} ${qi + 1}`, p: text + (ref ? ` [${ref}]` : '') });
        });
        return {
            source_id: `${sourceType}/${slugPrefix}-${String(idx + 1).padStart(3, '0')}`,
            source_type: sourceType,
            index: idx + 1,
            name: String(item.title || ''),
            title: String(item.title || ''),
            subtitle: '', category: '',
            icon: String(item.icon || ''),
            brief: String(item.intro || ''),
            sections: sections, refs: '',
        };
    });
}

function normalizeFiqh(items) {
    return items.map((item, idx) => {
        const sections = [];
        if (item.intro) sections.push({ h: 'مقدمة', p: String(item.intro) });
        (item.topics || []).forEach(topic => {
            const itemsList = Array.isArray(topic.items) ? topic.items : [];
            let paragraph = itemsList.map(x => `• ${x}`).join('\n');
            if (topic.quote) {
                const q = topic.quote;
                if (q.text) paragraph += `\n\n${q.text}${q.ref ? ` [${q.ref}]` : ''}`;
            }
            if (paragraph) sections.push({ h: String(topic.title || ''), p: paragraph });
        });
        return {
            source_id: `fiqh/fiqh-${String(idx + 1).padStart(3, '0')}`,
            source_type: 'fiqh',
            index: idx + 1,
            name: String(item.title || ''),
            title: String(item.title || ''),
            subtitle: '', category: '',
            icon: String(item.icon || ''),
            brief: String(item.intro || ''),
            sections: sections, refs: '',
        };
    });
}

function normalizeWomenSection(sectionData, sectionId, sectionTitle) {
    if (!sectionData || !Array.isArray(sectionData.items)) return { items: [], count: 0 };
    const items = sectionData.items.map((item, idx) => {
        const sections = (item.blocks || []).map(b => {
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
            section_id: sectionId, section_title: sectionTitle,
            index: idx + 1,
            name: String(item.title || ''),
            title: String(item.title || ''),
            subtitle: '', category: sectionTitle,
            icon: String(item.icon || ''),
            brief: String(item.summary || ''),
            sections: sections,
            quran_evidence: (item.quran || []).map(q => ({ text: String(q.text || ''), ref: String(q.ref || '') })),
            hadith_evidence: (item.hadith || []).map(h => ({ text: String(h.text || ''), ref: String(h.ref || ''), grade: String(h.grade || '') })),
            refs: '',
        };
    });
    return { items, count: items.length };
}

function main() {
    fs.mkdirSync(OUT_DIR, { recursive: true });
    const result = {
        meta: { extracted_at: new Date().toISOString(), platform_root: PLATFORM_ROOT, total_topics: 0, sources_breakdown: {} },
        prophets: [], sahaba: [], quranic_stories: [], ghazawat: [], seerah: [],
        shuhada: [], fadail: [], fiqh: [], zuhd: [], sihr: [],
        women: { sections: {}, total: 0 },
    };

    const HTML_SOURCES = [
        { file: 'prophets.html',         varName: 'PROPHETS',    key: 'prophets',         normalizer: 'generic', prefix: 'prophet',   sourceType: 'prophets' },
        { file: 'sahaba.html',           varName: 'SAHABA',      key: 'sahaba',           normalizer: 'generic', prefix: 'sahabi',    sourceType: 'sahaba' },
        { file: 'quranic-stories.html',  varName: 'STORIES',     key: 'quranic_stories',  normalizer: 'generic', prefix: 'qstory',    sourceType: 'quranic-stories' },
        { file: 'ghazawat.html',         varName: 'BATTLES',     key: 'ghazawat',         normalizer: 'generic', prefix: 'ghazwa',    sourceType: 'ghazawat' },
        { file: 'seerah.html',           varName: 'TIMELINE',    key: 'seerah',           normalizer: 'seerah',  prefix: 'seerah',    sourceType: 'seerah' },
        { file: 'fadail.html',           varName: 'FADAIL_DATA', key: 'fadail',           normalizer: 'quotes',  prefix: 'fadl',      sourceType: 'fadail' },
        { file: 'fiqh.html',             varName: 'FIQH_DATA',   key: 'fiqh',             normalizer: 'fiqh',    prefix: 'fiqh',      sourceType: 'fiqh' },
        { file: 'zuhd.html',             varName: 'ZUHD_DATA',   key: 'zuhd',             normalizer: 'quotes',  prefix: 'zuhd',      sourceType: 'zuhd' },
        { file: 'sihr.html',             varName: 'SIHR_DATA',   key: 'sihr',             normalizer: 'quotes',  prefix: 'sihr',      sourceType: 'sihr' },
    ];

    // ═══ 1-9: ملفات HTML ═══
    for (const src of HTML_SOURCES) {
        try {
            const vars = extractFromHtml(path.join(PLATFORM_ROOT, src.file), [src.varName]);
            if (!vars[src.varName]) {
                console.log(`⚠️ ${src.key}: ${src.varName} غير موجود`);
                continue;
            }
            if (src.normalizer === 'generic') {
                result[src.key] = normalizeGeneric(vars[src.varName], src.sourceType, src.prefix);
            } else if (src.normalizer === 'seerah') {
                result[src.key] = vars[src.varName].map((item, idx) => ({
                    source_id: `seerah/seerah-${String(idx + 1).padStart(3, '0')}`,
                    source_type: 'seerah', index: idx + 1,
                    name: String(item.title || ''), title: String(item.title || ''),
                    subtitle: String(item.year || ''), category: '',
                    icon: '', brief: String(item.brief || ''),
                    sections: normalizeSections(item.sections),
                    refs: String(item.refs || ''),
                }));
            } else if (src.normalizer === 'quotes') {
                result[src.key] = normalizeQuotes(vars[src.varName], src.sourceType, src.prefix);
            } else if (src.normalizer === 'fiqh') {
                result[src.key] = normalizeFiqh(vars[src.varName]);
            }
            console.log(`✅ ${src.key}: ${result[src.key].length}`);
        } catch (e) {
            console.error(`❌ ${src.key}: ${e.message}`);
        }
    }

    // ═══ shuhada: 3 مصفوفات ═══
    try {
        const vars = extractFromHtml(path.join(PLATFORM_ROOT, 'shuhada.html'), ['BADR', 'UHUD', 'FATH']);
        result.shuhada = normalizeShuhada(vars.BADR, vars.UHUD, vars.FATH);
        console.log(`✅ shuhada: ${result.shuhada.length}`);
    } catch (e) {
        console.error(`❌ shuhada: ${e.message}`);
    }

    // ═══ women: 10 ملفات ═══
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
            }
        } catch (e) {
            console.error(`❌ women/${slug}: ${e.message}`);
        }
    });
    result.women.total = womenTotal;

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
    console.log(`✅ إجمالي المواضيع: ${total}`);
    Object.entries(breakdown).forEach(([k, v]) => console.log(`  ${k}: ${v}`));
}

try { main(); } catch (e) { console.error('❌', e.message); console.error(e.stack); process.exit(1); }
