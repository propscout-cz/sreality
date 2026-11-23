/* The side panel end to end, from the BUILT dist/: sidepanel.html + sidepanel.js at ~400 px with
 * chrome.* stubbed (runtime messages answered by a mock background, tabs.connect ports by mock
 * content scripts, windows, storage), and dist/content.js on routed fake portal pages (the edge
 * tab on a detail page, the card badges on a search page). Prints one PASS/FAIL line per check,
 * writes screenshots, exits 1 on any FAIL.
 *
 *   npm run build && npm run e2e [-- <dist-dir> <out-dir>]
 *
 * CHROME_PATH runs another Chromium binary instead of Playwright's headless shell. */

import { chromium } from 'playwright-core';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const [DIST = path.join(ROOT, 'dist'), OUT = path.join(ROOT, 'e2e-shots')] = process.argv.slice(2);
if (!existsSync(path.join(DIST, 'sidepanel.js'))) {
  console.error(`No built extension in ${DIST}: run npm run build first.`);
  process.exit(1);
}
mkdirSync(OUT, { recursive: true });
const contentJs = readFileSync(path.join(DIST, 'content.js'), 'utf8');

const PANEL_URL = 'https://ext.test/sidepanel.html';
const DETAIL = 'https://www.sreality.cz/detail/prodej/byt/3+kk/praha-holesovice/1234567';
const SEARCH = 'https://www.sreality.cz/hledani/prodej/byty';
const DAY = 86_400_000;
/* Today, not a fixed date: the price chart runs its axis to Date.now(). */
const NOW = Date.now();
const iso = (daysAgo) => new Date(NOW - daysAgo * DAY).toISOString();

// ---- fixtures: what the mock background answers, per message type ----------------------------

const STAGES = [
  { id: 1, key: 'zajem', label: 'Zájem', position: 1, color: 'copper', is_terminal: false, is_entry: true, code: '1' },
  { id: 2, key: 'prohlidka', label: 'Prohlídka', position: 2, color: 'ochre', is_terminal: false, is_entry: false, code: '2' },
  { id: 3, key: 'nabidka', label: 'Nabídka', position: 3, color: 'teal', is_terminal: false, is_entry: false, code: '3' },
  { id: 4, key: 'koupeno', label: 'Koupeno', position: 4, color: 'sage', is_terminal: true, is_entry: false, code: '4' },
  { id: 5, key: 'zamitnuto', label: 'Zamítnuto', position: 5, color: 'brick', is_terminal: true, is_entry: false, code: '5' },
];
const COLLECTIONS = [
  { id: 11, name: 'Holešovice 3+kk', monitoring_enabled: true, owner_email: null, shared_with: [{ account_id: 'x', email: 'kolega@example.cz' }] },
  { id: 12, name: 'Investice 2026', monitoring_enabled: false, owner_email: null, shared_with: [] },
  { id: 13, name: 'Sdílená od Petra', monitoring_enabled: false, owner_email: 'petr@example.cz', shared_with: [] },
];
const TAGS = [{ id: 21, name: 'balkon', color: 'sage' }, { id: 22, name: 'investice', color: 'plum' }];
const MF_VALUE = { status: 'ok', monthly_rent_czk: 29200, total_per_m2: 374, rent_per_m2: 340, fees_per_m2: 34, area_m2: 78, granularity: 'obec', note: null };
const IN_PIPE = { in_pipeline: true, stage_id: 2, stage_key: 'prohlidka', stage_label: 'Prohlídka', stage_code: '2', stage_color: 'ochre', entered_stage_at: iso(4) };
const OUT_PIPE = { in_pipeline: false, stage_id: null, stage_key: null, stage_label: null, stage_code: null, stage_color: null, entered_stage_at: null };

/* A lookup row: ad 1234567 (property 501, in the pipeline at Prohlídka) unless overridden. */
function row(over) {
  return {
    source: 'sreality', source_id: '1234567', found: true, sreality_id: 1234567, listing_id: 900,
    property_id: 501, category_main: 'byt', category_type: 'prodej', area_m2: 78, area_basis: 'usable',
    price_czk: 8_990_000, price_per_m2: 115256, price_per_m2_basis: 'sale_capital_czk_m2',
    disposition: '3+kk', subtype: null, kind_label: '3+kk', display_label: 'Komunardů, Praha 7 – Holešovice',
    is_active: true, last_seen_at: iso(0),
    mf_reference_rent_czk: 29200, mf_gross_yield_pct: 3.9, mf_reference_rent: MF_VALUE,
    canonical_listing_id: 900, property_is_active: true, tom_days: 94, floor: 4, total_floors: 6,
    condition: 'velmi_dobry', ownership: 'osobni', property_subtype: null, plot_area_m2: null,
    fond_per_m2_czk_default: 10, latest_estimation: null,
    pipeline: IN_PIPE, collection_ids: [11, 12], tag_ids: [21], dismissed: false,
    ...over,
  };
}
const ROW_B = row({
  source_id: '7654321', listing_id: 950, property_id: 502, canonical_listing_id: 950, disposition: '2+kk', area_m2: 54,
  display_label: 'Seifertova, Praha 3 – Žižkov', pipeline: OUT_PIPE, collection_ids: [], tag_ids: [],
});
const SOURCES = [
  { id: 900, property_id: 501, source: 'sreality', source_url: null, source_id_native: '1234567', ad_reference: null, is_active: true, price_czk: 8_990_000, first_seen_at: iso(94), last_seen_at: iso(0) },
  { id: 901, property_id: 501, source: 'idnes', source_url: null, source_id_native: 'abc', ad_reference: null, is_active: true, price_czk: 9_150_000, first_seen_at: iso(61), last_seen_at: iso(0) },
  { id: 902, property_id: 501, source: 'bezrealitky', source_url: null, source_id_native: 'b1', ad_reference: null, is_active: false, price_czk: 9_290_000, first_seen_at: iso(88), last_seen_at: iso(25) },
];
const SNAPS = [
  { id: 1, listing_id: 900, scraped_at: iso(94), price_czk: 9_590_000 },
  { id: 2, listing_id: 900, scraped_at: iso(55), price_czk: 9_390_000 },
  { id: 3, listing_id: 900, scraped_at: iso(14), price_czk: 8_990_000 },
  { id: 4, listing_id: 901, scraped_at: iso(61), price_czk: 9_150_000 },
  { id: 5, listing_id: 902, scraped_at: iso(88), price_czk: 9_290_000 },
];
const NOTES = [
  { id: 1, property_id: 501, body: 'Majitel je ochoten jednat o ceně. Prohlídka ve čtvrtek v 17:00, vzít PENB.', shared: true, created_at: iso(4), updated_at: null, owner_email: null },
];
const SIGNED_OUT = { signedIn: false, email: null, appearance: { scheme: 'med', theme: 'light' } };
const BASE = {
  get_auth_state: { signedIn: true, email: 'operator@example.cz', appearance: { scheme: 'med', theme: 'light', at: iso(0) } },
  lookup_listings: [row({}), ROW_B],
  get_price_history: { sources: SOURCES, snapshots: SNAPS },
  list_pipeline_stages: STAGES, list_collections: COLLECTIONS, list_tags: TAGS, list_notes: NOTES,
  get_billing: { plan: 'pro', status: 'active', agent_estimations: { quota: 10, used: 3, remaining: 7, is_trial: false, metered: true } },
  add_pipeline_card: { property_id: 502, stage_id: 1, stage_key: 'zajem', stage_label: 'Zájem', stage_code: '1', stage_color: 'copper', entered_stage_at: iso(0), added: true },
};
const RUN = {
  id: 77, status: 'success', estimate_kind: 'rent', input_purchase_price_czk: null, estimated_monthly_rent_czk: 31000,
  estimated_sale_price_czk: null, gross_yield_pct: 4.1, confidence: 'medium', input_spec: { area_m2: 78 }, scenario: null, error_message: null,
};
const RUNNING = { ...RUN, id: 88, status: 'running', estimated_monthly_rent_czk: null, gross_yield_pct: null, confidence: null };
/* The ad with a finished yield run on it: the panel shows the editable scenario. */
const WITH_RUN = { lookup_listings: [row({ latest_estimation: { estimation_id: 77, estimate_kind: 'rent', gross_yield_pct: 4.1 } }), ROW_B], get_estimation: RUN, patch_scenario: RUN };
const SUBJECT = { ref: { source: 'sreality', sourceId: '1234567' }, url: DETAIL };
const SUBJECT_B = { ref: { source: 'sreality', sourceId: '7654321' }, url: 'https://www.sreality.cz/detail/prodej/byt/2+kk/praha-zizkov/7654321' };

// ---- chrome.* stubs (serialized into the page by addInitScript) ------------------------------

/* The side panel page. `tabs`: tab id → the subject its content script answers on connect
 * (absent = no content script: the port disconnects), after `delay` ms. All three tabs sit in
 * window 1; tab 12 has no script. Assign window.chrome: it is writable but NOT configurable. */
function panelInit({ replies, tabs, active, delay }) {
  window.__log = [];
  window.__sent = [];
  window.__posted = [];
  window.__ports = {};
  window.__listeners = { activated: [], storage: [] };
  window.__closed = false;
  window.close = () => { window.__closed = true; };
  const event = () => { const fns = []; return { fns, addListener: (f) => fns.push(f), removeListener: (f) => fns.splice(fns.indexOf(f), 1) }; };
  const answer = (msg) => {
    const r = replies[msg.type];
    if (msg.type === 'lookup_listings') return msg.items.map((it) => r.find((x) => x.source_id === it.source_id) ?? { ...r[0], source_id: it.source_id, found: false });
    return r;
  };
  window.chrome = {
    runtime: {
      lastError: undefined,
      sendMessage(msg, cb) {
        window.__log.push(msg.type);
        window.__sent.push(msg);
        setTimeout(() => cb(msg.type in replies ? { ok: true, data: answer(msg) } : { ok: false, status: 0, detail: `unmocked ${msg.type}` }), 20);
      },
      onMessage: event(),
    },
    tabs: {
      async query() { return [10, 11, 12].map((id) => ({ id, windowId: 1, active: id === active })); },
      connect(tabId, { name }) {
        const port = { name, onMessage: event(), onDisconnect: event(), postMessage: (m) => window.__posted.push({ tabId, ...m }), disconnect() {} };
        window.__ports[tabId] = port;
        setTimeout(() => {
          if (!(tabId in tabs)) { for (const f of port.onDisconnect.fns) f(); return; }
          for (const f of port.onMessage.fns) f({ type: 'subject', subject: tabs[tabId] });
        }, delay);
        return port;
      },
      onActivated: { addListener: (f) => window.__listeners.activated.push(f) },
      onAttached: event(),
      onDetached: event(),
      onUpdated: event(),
    },
    windows: { async getCurrent() { return { id: 1 }; } },
    storage: {
      local: { get: async () => ({}), set: async () => {}, remove: async () => {} },
      onChanged: { addListener: (f) => window.__listeners.storage.push(f), removeListener() {} },
    },
  };
}

/* A portal page under content.js. Shadow roots are forced open (the extension's are closed,
 * and selectors pierce only open ones). `window.__newPort()` makes the side panel's port. */
function pageInit({ replies }) {
  Element.prototype.attachShadow = ((orig) => function (init) { return orig.call(this, { ...init, mode: 'open' }); })(Element.prototype.attachShadow);
  window.__log = [];
  window.__calls = [];
  window.__connect = [];
  window.__newPort = () => {
    const port = { name: 'side_panel', sent: [], postMessage(m) { this.sent.push(m); }, onMessage: { fns: [], addListener(f) { this.fns.push(f); } }, onDisconnect: { fns: [], addListener(f) { this.fns.push(f); } } };
    window.__lastPort = port;
    return port;
  };
  const answer = (msg) => {
    const r = replies[msg.type];
    if (msg.type === 'lookup_listings') return msg.items.map((it) => r.find((x) => x.source_id === it.source_id));
    return r;
  };
  window.chrome = {
    runtime: {
      lastError: undefined,
      sendMessage(msg, cb) {
        if (window.__orphaned) throw new Error('Extension context invalidated.');
        window.__log.push(msg.type);
        if (msg.type === 'panel') { window.__calls.push(msg); return Promise.resolve(); }
        setTimeout(() => cb(msg.type in replies ? { ok: true, data: answer(msg) } : { ok: false, status: 0, detail: `unmocked ${msg.type}` }), 20);
      },
      onMessage: { addListener() {} },
      onConnect: { addListener: (f) => window.__connect.push(f) },
    },
    storage: {
      local: { get: async () => ({}), set: async () => {}, remove: async () => {} },
      onChanged: { addListener() {}, removeListener() {} },
    },
  };
}

// ---- the run ----------------------------------------------------------------------------------

const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
const checks = [];
const pageErrors = [];
const scenarios = [];
const pages = [];
let current = '';
const check = (name, ok, detail = '') => checks.push({ name, ok: Boolean(ok), detail });
const scenario = (name, run) => scenarios.push({ name, run });

async function newPage(width, height) {
  const p = await browser.newPage({ viewport: { width, height } });
  p.setDefaultTimeout(5_000);
  p.on('pageerror', (e) => pageErrors.push(`${current}: ${e.message}`));
  pages.push(p);
  return p;
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png' };
function serveDist(route) {
  const file = path.join(DIST, new URL(route.request().url()).pathname);
  if (!existsSync(file)) return route.fulfill({ status: 404 });
  return route.fulfill({ contentType: TYPES[path.extname(file)] ?? 'application/octet-stream', body: readFileSync(file) });
}

/* The side panel on window 1, tab `active` active; `settle` ms for it to paint. */
async function panel({ replies = {}, tabs = { 10: SUBJECT, 11: SUBJECT_B }, active = 10, appearance, height = 900, delay = 60, settle = 900 } = {}) {
  const p = await newPage(400, height);
  await p.route('https://ext.test/**', serveDist);
  const all = { ...BASE, ...replies };
  if (appearance) all.get_auth_state = { ...all.get_auth_state, appearance: { ...appearance, at: iso(0) } };
  await p.addInitScript(panelInit, { replies: all, tabs, active, delay });
  await p.goto(PANEL_URL);
  await p.waitForTimeout(settle);
  return p;
}

/* A portal page with dist/content.js injected, as the manifest would. */
async function portalPage(url, body, replies) {
  const p = await newPage(1280, 800);
  await p.route('https://www.sreality.cz/**', (r) => r.fulfill({ contentType: 'text/html', body }));
  await p.addInitScript(pageInit, { replies });
  await p.goto(url);
  await p.addScriptTag({ content: contentJs });
  return p;
}

let shots = 0;
const shot = (p, name) => p.screenshot({ path: path.join(OUT, `${String(++shots).padStart(2, '0')}-${name}.png`) });
const text = (p, sel) => p.locator(sel).first().textContent().catch(() => null);
const title = (p) => text(p, 'h1.title').then((t) => t ?? '');
const count = (p, type) => p.evaluate((t) => window.__log.filter((x) => x === t).length, type);
const activate = (p, tabId, windowId = 1) => p.evaluate(([id, w]) => { for (const f of window.__listeners.activated) f({ tabId: id, windowId: w }); }, [tabId, windowId]);
/* A message from tab `tabId`'s content script up its port. */
const fromTab = (p, tabId, message) => p.evaluate(([id, m]) => { for (const f of window.__ports[id].onMessage.fns) f(m); }, [tabId, message]);
/* The side panel connecting to a portal page: what the page sends up the port. */
const connectPanel = (p) => p.evaluate(() => { const port = window.__newPort(); for (const f of window.__connect) f(port); return port.sent.slice(); });
const disconnectPanel = (p) => p.evaluate(() => { for (const f of window.__lastPort.onDisconnect.fns) f(); });
const edgeTab = '#__propscout_tab__ >> button.tab';

// ---- the side panel ---------------------------------------------------------------------------

scenario('the active tab', async () => {
  const p = await panel({ height: 1500 });
  await shot(p, 'active-med-light');
  check("the panel shows the active tab's ad", /3\+kk, 78/.test(await title(p)));
  check('the document root wears the theme (Měď: no data-scheme, light)', await p.evaluate(() => !document.documentElement.hasAttribute('data-scheme') && document.documentElement.dataset.theme === 'light'));
  await activate(p, 11);
  await p.waitForTimeout(700);
  check("switching tabs shows that tab's subject", /2\+kk, 54/.test(await title(p)));
  await activate(p, 12, 2);
  await p.waitForTimeout(200);
  check("another window's tab switch changes nothing", /2\+kk, 54/.test(await title(p)));
  await p.click('[data-key="pipeline-add"]');
  await p.waitForTimeout(400);
  const posted = await p.evaluate(() => window.__posted.filter((m) => m.type === 'property_written'));
  check("a landed write is told to the window's tabs (property id only)", posted.length === 2 && posted.every((m) => m.property_id === 502), JSON.stringify(posted));
  const before = await count(p, 'lookup_listings');
  await fromTab(p, 11, { type: 'property_written', property_id: 502 });
  await p.waitForTimeout(300);
  const after = await count(p, 'lookup_listings');
  check('a write in a tab makes the panel re-read that property', after === before + 1, `${before} -> ${after}`);
  await fromTab(p, 11, { type: 'subject', subject: { ...SUBJECT_B, url: 'https://www.sreality.cz/detail/x/7654321?foto=3' } });
  await p.waitForTimeout(300);
  check('a URL rewrite of the same ad does not re-open the panel', (await count(p, 'lookup_listings')) === after);
  await p.click('[data-key="close"]');
  check('the close › closes the page (window.close)', await p.evaluate(() => window.__closed));
});

scenario('Kobalt dark', async () => {
  const p = await panel({ appearance: { scheme: 'kobalt', theme: 'dark' }, height: 1500 });
  await shot(p, 'active-kobalt-dark');
  check('Kobalt dark on the document root', await p.evaluate(() => document.documentElement.dataset.scheme === 'kobalt' && document.documentElement.dataset.theme === 'dark'));
});

scenario('signed out', async () => {
  const p = await panel({ replies: { get_auth_state: SIGNED_OUT } });
  await shot(p, 'signed-out');
  check('signed out: the sign-in card', /Přihlásit se přes Google/.test(await text(p, '.body') ?? ''));
});

scenario('no content script in the active tab', async () => {
  const p = await panel({ tabs: {} });
  await shot(p, 'no-subject');
  check('a tab without our content script: the quiet state', /Vyberte inzerát na podporovaném portálu\. Pokud ho máte otevřený, obnovte stránku\./.test(await text(p, '.body') ?? ''));
  check('…signed in: under the account menu', (await p.locator('[data-key="account"]').count()) === 1);
});

scenario('a search page in the active tab', async () => {
  const p = await panel({ tabs: { 10: null } });
  check('a search page with no card opened: the quiet state too', /Vyberte inzerát/.test(await text(p, '.body') ?? ''));
});

scenario('no ad and signed out', async () => {
  const p = await panel({ tabs: {}, replies: { get_auth_state: SIGNED_OUT } });
  await shot(p, 'no-subject-signed-out');
  const body = await text(p, '.body') ?? '';
  check('no ad and signed out (the toolbar on any tab): the sign-in card', /Přihlásit se přes Google/.test(body) && !/Vyberte/.test(body));
});

scenario('state kept across a tab switch', async () => {
  const p = await panel({ height: 1500, replies: WITH_RUN });
  await p.click('[data-key="note-add"]');
  await p.fill('[data-key="note"]', 'Zavolat makléři kvůli sklepu');
  await activate(p, 11);
  await p.waitForTimeout(500);
  check('another property: its own (empty) notes, no draft carried over', (await p.locator('[data-key="note"]').count()) === 0);
  await activate(p, 10);
  await p.waitForTimeout(700);
  check('back on the property: the half-typed note is still there', (await p.inputValue('[data-key="note"]').catch(() => null)) === 'Zavolat makléři kvůli sklepu');
  await shot(p, 'note-draft-kept');
  await p.fill('[data-key="rent"]', '35000');
  await activate(p, 11);
  await p.waitForTimeout(150);
  const patches = await p.evaluate(() => window.__sent.filter((m) => m.type === 'patch_scenario'));
  check('an edit in its debounce is saved at once on the switch, to its own run', patches.length === 1 && patches[0].run_id === 77 && patches[0].body.rent_czk === 35000, JSON.stringify(patches));
});

scenario('a run in flight across a tab switch', async () => {
  const p = await panel({ height: 1500, replies: { create_estimation: RUNNING, get_estimation: RUNNING } });
  await p.click('button[data-key="vynos"]');
  await p.waitForTimeout(200);
  await activate(p, 11);
  await p.waitForTimeout(500);
  await activate(p, 10);
  await p.waitForTimeout(500);
  const busy = await p.locator('[data-key="vynos"][aria-busy="true"]').count();
  const creates = await count(p, 'create_estimation');
  check('back on the ad, its run in flight shows running (no start button, one run)', busy === 1 && creates === 1, `busy=${busy} creates=${creates}`);
});

scenario('a content script that answers late', async () => {
  // The active tab's subject arrives only after the check: the skeleton, never the quiet state.
  const p = await panel({ tabs: { 10: SUBJECT }, delay: 5_000, settle: 300 });
  check('while the active tab is connecting: the skeleton, not the empty state', (await p.locator('.sk-head').count()) === 1 && !/Vyberte inzerát/.test(await text(p, '.body') ?? ''));
});

scenario('menus, focus and the in-place editor', async () => {
  // They run on the page's own document now, not a shadow root.
  const p = await panel({ height: 1500, replies: WITH_RUN });
  const focused = () => p.evaluate(() => document.activeElement?.dataset?.key ?? document.activeElement?.tagName);
  await p.click('[data-key="close-trigger"]');
  await p.waitForTimeout(150);
  check('the Uzavřít menu opens with focus on its first item', /Koupeno/.test(await p.evaluate(() => document.activeElement?.textContent) ?? ''));
  await p.keyboard.press('Escape');
  await p.waitForTimeout(150);
  check('Escape closes it and focus returns to its trigger', (await p.locator('.pipe .menu').count()) === 0 && (await focused()) === 'close-trigger', String(await focused()));
  await p.click('[data-key="account"]');
  await p.waitForTimeout(150);
  check('the account menu opens on Odhlásit and names the account', (await focused()) === 'sign-out' && (await text(p, '.menu-who')) === 'operator@example.cz');
  await shot(p, 'account-menu');
  await p.mouse.click(200, 700);
  await p.waitForTimeout(150);
  check('a pointer down outside closes the menu', (await p.locator('.top .menu').count()) === 0);
  const before = await text(p, '.est-yield .mono');
  await p.fill('[data-key="rent"]', '35000');
  await p.waitForTimeout(100);
  const after = await text(p, '.est-yield .mono');
  check('editing the rent updates the yield in place, keeping the field', before !== after && after?.replace(/\s/g, ' ') === '4,6 %' && (await focused()) === 'rent', `${before} -> ${after}, focus ${await focused()}`);
  await p.waitForTimeout(700);
  check('the edit is saved to the run (PATCH after the debounce)', await p.evaluate(() => window.__log.includes('patch_scenario')));
});

// ---- content.js on portal pages ---------------------------------------------------------------

scenario('the edge tab on a detail page', async () => {
  // The price fell 9 390 000 → 8 220 000 (12,5 %) at the last snapshot.
  const drop = {
    ...BASE,
    lookup_listings: [row({ price_czk: 8_220_000 })],
    get_price_history: { sources: [{ ...SOURCES[0], price_czk: 8_220_000 }, SOURCES[1], SOURCES[2]], snapshots: [SNAPS[0], SNAPS[1], { ...SNAPS[2], price_czk: 8_220_000 }, SNAPS[3], SNAPS[4]] },
  };
  const p = await portalPage(DETAIL, `<!doctype html><html><head><meta charset="utf-8"><title>sreality</title></head><body style="margin:0;font:14px Arial;background:#fff">
    <header style="height:64px;background:#cc0000;color:#fff;display:flex;align-items:center;padding:0 24px;font-weight:bold">sreality.cz</header>
    <main style="padding:24px;max-width:1100px"><h1 style="font-size:24px">Prodej bytu 3+kk 78 m²</h1>
    <div style="width:900px;height:520px;background:linear-gradient(#9cc3e6,#d9d2c0 60%,#7a6a55)"></div>
    <p>Komunardů, Praha 7 – Holešovice</p></main></body></html>`, drop);
  await p.waitForTimeout(900);
  await shot(p, 'edge-tab-on-page');
  const marks = {
    stage: await text(p, '#__propscout_tab__ >> .code'),
    move: await text(p, '#__propscout_tab__ >> .move--down'),
    mf: await text(p, '#__propscout_tab__ >> .tab-mf'),
  };
  check('the edge tab carries the stage, the drop and MF', marks.stage === '2' && /12,5/.test(marks.move ?? '') && /MF.*3,9/.test(marks.mf ?? ''), JSON.stringify(marks));
  check('on load the tab calls the panel of its window (open: false)', JSON.stringify(await p.evaluate(() => window.__calls)) === '[{"type":"panel","open":false}]');
  await p.locator(edgeTab).click();
  check('a click on the tab asks the background to open the panel', (await p.evaluate(() => window.__calls.at(-1)?.open)) === true);
  const sent = await connectPanel(p);
  check("the panel's port gets this page's subject", sent.length === 1 && sent[0].subject?.ref?.sourceId === '1234567', JSON.stringify(sent));
  check('while the panel is open the edge tab hides', (await p.locator(edgeTab).count()) === 0);
  await disconnectPanel(p);
  check('the panel closing brings the edge tab back', (await p.locator(edgeTab).count()) === 1);
  // Into the back/forward cache the port goes with no onDisconnect.
  await connectPanel(p);
  await p.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
  check('pagehide into bfcache shows the edge tab (no onDisconnect in a frozen page)', (await p.locator(edgeTab).count()) === 1);
  // An extension update orphans the script: its next call throws.
  await p.evaluate(() => { window.__orphaned = true; });
  await p.locator(edgeTab).click();
  const orphan = await p.locator(edgeTab).evaluate((t) => ({ title: t.title, text: t.textContent }));
  check('an orphaned script: the edge tab offers the page reload', /obnovte stránku/.test(orphan.title) && /Obnovit/.test(orphan.text), JSON.stringify(orphan));
  await shot(p, 'edge-tab-reload');
});

scenario('the panel already open as a detail page loads', async () => {
  const p = await portalPage(DETAIL, '<!doctype html><html><body><h1>Byt</h1></body></html>', BASE);
  await connectPanel(p);
  await p.waitForTimeout(500);
  const asked = await count(p, 'get_price_history');
  await disconnectPanel(p);
  await p.waitForTimeout(500);
  const after = await count(p, 'get_price_history');
  check('no price history while the panel is open; read once the edge tab shows', asked === 0 && after === 1, `${asked} -> ${after}`);
});

scenario('a search page', async () => {
  const cards = ['1234567', '7654321'].map((id) => `<li style="width:300px;margin:10px;list-style:none"><a href="/detail/prodej/byt/3+kk/praha/${id}"><img width="300" height="200" style="display:block;background:#aab"></a><a href="/detail/prodej/byt/3+kk/praha/${id}">Byt ${id}</a></li>`);
  const p = await portalPage(SEARCH, `<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;font:14px Arial"><ul style="display:flex;padding:12px">${cards.join('')}</ul></body></html>`, BASE);
  await p.waitForTimeout(1500);
  check('a search page has no edge tab', (await p.locator('#__propscout_tab__').count()) === 0);
  const sent = await connectPanel(p);
  check('a search page with no card opened says no subject', sent.length === 1 && sent[0].subject === null, JSON.stringify(sent));
  await p.locator('li').nth(1).locator('.badge').click();
  const after = await p.evaluate(() => ({ sent: window.__lastPort.sent, call: window.__calls.at(-1) }));
  check("a card badge makes its ad the tab's subject and opens the panel", after.sent.at(-1)?.subject?.ref?.sourceId === '7654321' && after.call?.open === true, JSON.stringify(after));
});

for (const { name, run } of scenarios) {
  current = name;
  try {
    await run();
  } catch (err) {
    check(`${name}: ran to the end`, false, String(err?.message ?? err).split('\n')[0]);
  } finally {
    await Promise.all(pages.splice(0).map((p) => p.close()));
  }
}
await browser.close();
check('no page threw an uncaught error', pageErrors.length === 0, pageErrors.join(' | '));

const lines = checks.map((c) => `${c.ok ? 'PASS' : 'FAIL'} ${c.name}${c.detail ? ` — ${c.detail}` : ''}`);
const failed = checks.filter((c) => !c.ok).length;
lines.push('', `${checks.length - failed}/${checks.length} passed; screenshots in ${path.relative(process.cwd(), OUT) || '.'}`);
writeFileSync(path.join(OUT, 'checks.txt'), `${lines.join('\n')}\n`);
console.log(lines.join('\n'));
if (failed > 0) process.exitCode = 1;
