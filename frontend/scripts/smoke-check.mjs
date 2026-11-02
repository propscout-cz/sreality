#!/usr/bin/env node
// Production page check and smoke sweep. Signs in as the dedicated admin test account and only
// ever reads: merge mode is opened and closed with no card ticked (Merge stays disabled below
// two), and nothing is dragged, toggled or saved.
//
//   node frontend/scripts/smoke-check.mjs                                  the default sweep
//   node frontend/scripts/smoke-check.mjs --page <path> [--expect <text>]... [--absent <text>]...
//     (--page repeats and replaces the sweep; each text binds to the --page before it)
//
// Exit: 0 passed, 1 a check failed, 2 bad usage or no credentials (no browser started).
// Setup, once: npx playwright install chromium (from frontend/).
// Credentials: the environment, then this worktree's .env, then the main checkout's .env.
// A one-off imports openSession() instead of copying the login; importing starts nothing.
//
// FIXTURES this script depends on, both on the admin test account (never name it here; a
// missing fixture fails its check). Resolve the account from the address in the environment.
//
// 1. Pipeline cards. With none, the board's hydration chain short-circuits and five of its six
//    hops go unexercised. The four properties are active, priced and carry photos.
//
//   insert into property_pipeline (property_id, stage_id, account_id, board_position)
//   select v.pid,
//          (select id from pipeline_stages
//            where account_id = :acct and archived_at is null
//            order by case when v.slot = 0 then 0 else 1 end, position
//            limit 1),
//          :acct, v.slot + 1
//   from (values (5,0),(6,0),(18,2),(28,3)) as v(pid, slot)
//   on conflict (property_id, account_id) do nothing;
//
// 2. A paused watchdog for the editor check. Its filter cannot match (price >= 2 and <= 1), so
//    even a resume writes nothing. :'email' is $SMOKE_TEST_ADMIN_EMAIL.
//
//   insert into notification_subscriptions (id, name, filter_spec, is_active, account_id)
//   select '5a0c3e00-0000-4000-8000-000000002231', 'smoke fixture (paused, never resume)',
//          '{"category_type": "pronajem", "min_price_czk": 2, "max_price_czk": 1}'::jsonb,
//          false, m.account_id
//   from account_members m join auth.users u on u.id = m.user_id
//   where u.email = :'email'
//   on conflict (id) do nothing;

import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, realpathSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '../..');
const SHOT_DIR = path.join(__dirname, '.smoke-shots');

export const FIXTURE_WATCHDOG_ID = '5a0c3e00-0000-4000-8000-000000002231';
const FIXTURE_WATCHDOG_NAME = 'smoke fixture (paused, never resume)';

const USAGE = 'usage: node frontend/scripts/smoke-check.mjs [--page <path> [--expect <text>]... [--absent <text>]...]...';

/* Time-to-first-card on /pipeline. The board's own north-star target is one
 * round trip to interactive cards; this is the guardrail, not the goal. */
const BUDGET_FIRST_CARD_MS = 12000;

/* Per-route budgets. `baseline` is the last measurement. Ceilings sit modestly above it: headroom
 * for instance weather, tight enough that a STRUCTURAL regression (a new waterfall level, a read
 * that starts firing per-card, a duplicated bootstrap) trips them.
 *
 * Ceilings are RATCHETS: a wave that removes requests lowers its number on a measurement, and
 * none is ever raised to make room. History: `git log -p` on this file and the route table in
 * roadmap/hydration-sprint.md § Baselines.
 *
 * Counted: every request with an Authorization header (see visit). Routes are the operator's
 * daily path plus the two that hid defects. */
const ROUTE_BUDGETS = [
  { path: '/browse',        baseline: '22 req / 4.9s', maxRequests: 27, maxMs: 20000, check: checkBrowse },
  { path: '/pipeline',      baseline: '17 req / 0.9s', maxRequests: 22, maxMs: 15000, check: checkPipeline },
  { path: '/collections',   baseline: '5 req / 0.9s',  maxRequests: 10, maxMs: 12000 },
  { path: '/watchdog',      baseline: '6 req / 1.3s',  maxRequests: 12, maxMs: 12000 },
  { path: '/notifications', baseline: '5 req / 1.0s',  maxRequests: 10, maxMs: 12000 },
];
const UNBUDGETED = { maxRequests: Infinity, maxMs: 30000 };
const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

const shortUrl = (u) => u.replace(/^https?:\/\//, '').split('?')[0].slice(0, 80);

/* Page text reaches the log, so a credential or token must not survive the trip. */
function scrub(text) {
  let out = String(text).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, '<token>');
  for (const secret of [process.env.SMOKE_TEST_ADMIN_EMAIL, process.env.SMOKE_TEST_ADMIN_PASSWORD]) {
    if (secret) out = out.replaceAll(secret, '<credential>');
  }
  return out;
}

const steps = [];

function step(name, ok, detail) {
  steps.push({ name, ok });
  console.log(scrub(`${ok ? 'PASS' : 'FAIL'} — ${name}${detail ? ' :: ' + detail : ''}`));
}

/* process.loadEnvFile never overrides a variable already set, so the order is the precedence. */
function loadEnvFiles() {
  let mainCheckout = REPO_ROOT;
  try {
    const args = ['-C', REPO_ROOT, 'rev-parse', '--path-format=absolute', '--git-common-dir'];
    mainCheckout = path.dirname(execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim());
  } catch {
    /* no git here: the environment and this checkout's .env still count */
  }
  for (const dir of [REPO_ROOT, mainCheckout]) {
    const file = path.join(dir, '.env');
    if (existsSync(file)) process.loadEnvFile(file);
  }
}

function credentials() {
  loadEnvFiles();
  const email = process.env.SMOKE_TEST_ADMIN_EMAIL;
  const password = process.env.SMOKE_TEST_ADMIN_PASSWORD;
  if (!email || !password) {
    throw new Error('Missing SMOKE_TEST_ADMIN_EMAIL / SMOKE_TEST_ADMIN_PASSWORD (environment or .env).');
  }
  return { email, password };
}

/** A browser signed in to production: { page, visit(path, options), failedRequests, close() }. */
export async function openSession() {
  const { email, password } = credentials();
  const baseUrl = process.env.SMOKE_CHECK_BASE_URL || 'https://app.propscout.cz';
  mkdirSync(SHOT_DIR, { recursive: true });
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const failedRequests = [];
    page.on('requestfailed', (req) => {
      failedRequests.push(`${req.method()} ${shortUrl(req.url())} — ${req.failure()?.errorText}`);
    });
    await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password').fill(password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('**/browse', { timeout: 15000 });
    const session = { page, baseUrl, failedRequests, shots: 0, close: () => browser.close() };
    session.visit = (pagePath, options) => visit(session, pagePath, options);
    return session;
  } catch (err) {
    await browser.close();
    throw err;
  }
}

/* The ONE navigation after login, and the one failure policy. Returns what it found rather than
 * throwing, so a caller reports every failure of a page at once. A missing entity behind a valid
 * route keeps its title, so only an expected text can catch it. */
async function visit(session, pagePath, { expect = [], absent = [], onLoaded } = {}) {
  const { page, baseUrl } = session;
  const url = new URL(pagePath, baseUrl);
  const budget = ROUTE_BUDGETS.find((r) => r.path === url.pathname) ?? UNBUDGETED;
  const failures = [];

  const onConsole = (msg) => {
    if (msg.type() === 'error') failures.push(`console error: ${msg.text()}`);
  };
  const onPageError = (err) => failures.push(`page error: ${err}`);

  /* An app request is one carrying an Authorization header: database, auth and API calls, on
   * whatever host they move to, and never images or tiles. */
  const requests = [];
  const reads = [];
  const onResponse = (res) => {
    const req = res.request();
    reads.push(req.headerValue('authorization').then((auth) => {
      if (auth) requests.push({ method: req.method(), url: req.url(), status: res.status() });
    }, () => {}));
  };

  page.on('console', onConsole);
  page.on('pageerror', onPageError);
  page.on('response', onResponse);
  const started = Date.now();
  try {
    const doc = await page.goto(url.href, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (doc && doc.status() >= 500) failures.push(`HTTP ${doc.status()} on the document`);
    onLoaded?.(started);
    await page.waitForLoadState('networkidle', { timeout: budget.maxMs + 15000 });
  } catch (err) {
    failures.push(`did not settle: ${err.message.split('\n')[0]}`);
  }
  const elapsedMs = Date.now() - started;

  const finalPath = new URL(page.url()).pathname;
  if (finalPath !== url.pathname) {
    failures.push(`redirected: asked for ${url.pathname}, landed on ${finalPath}`);
  }
  if ((await page.title()) === 'Not found') {
    failures.push(`${url.pathname} is not a route: copy the pattern from the route table (frontend/src/lib/routes.ts), never guess`);
  }

  /* Expected first: it waits for the paint, and only a painted page can prove a text absent. */
  const visibleText = (text) => page.getByText(text).filter({ visible: true });
  for (const text of expect) {
    await visibleText(text).first().waitFor({ state: 'visible', timeout: 5000 }).catch(() => {
      failures.push(`expected text missing: "${text}"`);
    });
  }
  for (const text of absent) {
    if ((await visibleText(text).count()) > 0) failures.push(`absent text present: "${text}"`);
  }

  session.shots += 1;
  const slug = url.pathname.replace(/[^\w-]+/g, '-').replace(/^-|-$/g, '') || 'root';
  const screenshot = path.join(SHOT_DIR, `${String(session.shots).padStart(2, '0')}-${slug}.png`);
  await page.screenshot({ path: screenshot }).catch((err) => {
    failures.push(`no screenshot: ${err.message.split('\n')[0]}`);
  });

  page.off('console', onConsole);
  page.off('pageerror', onPageError);
  page.off('response', onResponse);
  await Promise.allSettled(reads);

  for (const r of requests.filter((x) => x.status >= 500)) {
    failures.push(`${r.status} on ${shortUrl(r.url)}`);
  }
  if (requests.length > budget.maxRequests) {
    failures.push(`over budget: ${requests.length} app requests, ceiling ${budget.maxRequests} (baseline ${budget.baseline})`);
  }
  if (elapsedMs > budget.maxMs) failures.push(`over budget: settled in ${elapsedMs}ms, ceiling ${budget.maxMs}ms`);

  return { failures, requests, elapsedMs, screenshot };
}

function report(pagePath, { failures, requests, elapsedMs, screenshot }) {
  step(pagePath, failures.length === 0, `${requests.length} app requests, ${elapsedMs}ms, ${path.basename(screenshot)}`);
  for (const failure of failures) step(`${pagePath} — ${failure}`, false);
}

/** The Watchdog editor against the paused fixture: reads the Name input, never toggles or saves. */
export async function checkWatchdogFixture(session, id = FIXTURE_WATCHDOG_ID) {
  const result = await session.visit(`/watchdog/${id}/edit`);
  const name = await session.page.getByRole('textbox', { name: 'Name' }).inputValue({ timeout: 5000 }).catch(() => null);
  if (name !== FIXTURE_WATCHDOG_NAME) {
    result.failures.push(`Name input holds ${JSON.stringify(name)}, not the fixture's name`);
  }
  for (const r of result.requests.filter((x) => WRITE_METHODS.has(x.method))) {
    result.failures.push(`a write was sent: ${r.method} ${shortUrl(r.url)}`);
  }
  return result;
}

async function checkBrowse({ page }) {
  const mergeModeBtn = page.getByRole('button', { name: 'Sloučit…', exact: true });
  await mergeModeBtn.waitFor({ state: 'visible', timeout: 15000 });
  step('Browse renders + "Sloučit…" (merge mode) button visible', true);

  /* W7a moved card photos into the hydration layer, so the grid can render perfectly with every
   * photo silently gone. The carousel draws "n / total" only when it holds MORE THAN ONE image,
   * so the counter proves photos arrived. Waited for: the cards paint before it does. */
  const photoCounter = page.locator('span', { hasText: /^\d+ \/ \d+$/ }).first();
  let carouselOk = false;
  let counterText = 'none';
  try {
    await photoCounter.waitFor({ state: 'visible', timeout: 20000 });
    counterText = (await photoCounter.innerText()).trim();
    /* Parsed, not a digit class: /[2-9]\d*$/ failed a good 16-photo carousel live. */
    const total = Number(/^\d+ \/ (\d+)$/.exec(counterText)?.[1] ?? 0);
    carouselOk = total > 1;
  } catch { /* carouselOk stays false: reported below, not thrown */ }
  step('Browse cards hydrate a multi-photo carousel', carouselOk, `photo counter: ${counterText}`);

  /* The counter is drawn from the photo ROWS, so it reads "1 / 7" with all seven <img>s 403ing
   * (W7b: a wrong R2 signature fails exactly there). naturalWidth is > 0 only once the browser
   * has decoded real bytes. */
  let decoded = 0;
  let sampled = 0;
  try {
    await page.locator('img[src*="/images/"]').first().waitFor({ state: 'visible', timeout: 20000 });
    const shots = await page.locator('img[src*="/images/"]').all();
    for (const img of shots.slice(0, 5)) {
      sampled += 1;
      if (await img.evaluate((el) => el.complete && el.naturalWidth > 0)) decoded += 1;
    }
  } catch { /* decoded stays 0: reported below, not thrown */ }
  step('listing photos load from R2 (presigned redirect resolves)', sampled > 0 && decoded === sampled, `${decoded}/${sampled} sampled <img> decoded`);

  await mergeModeBtn.click();
  const mergeBtn = page.getByRole('button', { name: /^Merge($| \d)/ });
  await mergeBtn.waitFor({ state: 'visible', timeout: 5000 });
  const mergeDisabled = await mergeBtn.isDisabled();
  step('mutating button disabled pre-selection (no cards clicked)', mergeDisabled, `merge disabled=${mergeDisabled}`);
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  step('exited merge mode, zero cards ever touched', true);
}

/* The smoke account owns seeded pipeline cards precisely so the board's hydration chain (rows ->
 * properties -> covers -> brokers) runs. Read-only: loaded and inspected, never dragged. */
async function checkPipeline({ page }, { firstCardMs }) {
  step('pipeline board paints a card', firstCardMs !== null, `first card visible in ${firstCardMs}ms`);
  step(`time-to-first-card under ${BUDGET_FIRST_CARD_MS}ms`, firstCardMs !== null && firstCardMs <= BUDGET_FIRST_CARD_MS, `${firstCardMs}ms`);
  const priced = await page.locator('a[href^="/property/"]').filter({ hasText: /\d/ }).count();
  step('at least one card shows a price', priced >= 1, `${priced} priced cards`);
  // one <ul aria-label={stage.label}> per stage column (pages/Pipeline.tsx)
  const columns = await page.locator('main ul[aria-label]').count();
  step('stage columns rendered', columns >= 2, `${columns} columns`);
}

async function sweep(session) {
  for (const route of ROUTE_BUDGETS) {
    /* Started once the new document is in, or a card still on the old page would answer. */
    let firstCard = null;
    const onLoaded = route.path !== '/pipeline' ? undefined : (started) => {
      firstCard = session.page.locator('a[href^="/property/"]').first()
        .waitFor({ state: 'visible', timeout: 30000 })
        .then(() => Date.now() - started, () => null);
    };
    report(route.path, await session.visit(route.path, { onLoaded }));
    await route.check?.(session, { firstCardMs: await firstCard });
  }
  report('Watchdog editor (fixture)', await checkWatchdogFixture(session));
}

function parsePages(args) {
  const text = { type: 'string', multiple: true };
  const { tokens } = parseArgs({ args, options: { page: text, expect: text, absent: text }, tokens: true });
  const pages = [];
  for (const token of tokens) {
    if (token.kind !== 'option') continue;
    if (!token.value?.trim()) throw new Error(`--${token.name} needs a value`);
    if (token.name === 'page') {
      const offSite = new URL(token.value, 'https://check.invalid').origin !== 'https://check.invalid';
      if (!token.value.startsWith('/') || offSite) throw new Error(`--page takes an in-app path: ${token.value}`);
      pages.push({ path: token.value, expect: [], absent: [] });
    } else if (pages.length === 0) {
      throw new Error(`--${token.name} must follow a --page`);
    } else {
      pages.at(-1)[token.name].push(token.value);
    }
  }
  const unproven = pages.find((p) => p.absent.length > 0 && p.expect.length === 0);
  if (unproven) throw new Error(`${unproven.path}: --absent needs an --expect on the same page, or a blank page would pass`);
  return pages;
}

async function main(args) {
  let pages;
  try {
    pages = parsePages(args);
    credentials();
  } catch (err) {
    console.error(`${err.message}\n${USAGE}`);
    return 2;
  }
  rmSync(SHOT_DIR, { recursive: true, force: true });

  let session;
  try {
    session = await openSession();
    step('login redirects to /browse', true);
    if (pages.length === 0) {
      await sweep(session);
    } else {
      for (const { path: pagePath, expect, absent } of pages) {
        report(pagePath, await session.visit(pagePath, { expect, absent }));
      }
    }
  } catch (err) {
    step('UNEXPECTED FAILURE', false, String(err));
  } finally {
    await session?.close();
  }

  if (session?.failedRequests.length) {
    console.log('\n--- failed requests (informational: navigation aborts in-flight map and count requests) ---');
    console.log(scrub(session.failedRequests.join('\n')));
  }
  const allPassed = steps.every((s) => s.ok);
  console.log(`\n=== ${allPassed ? 'ALL STEPS PASSED' : 'SOME STEPS FAILED'} (screenshots: ${SHOT_DIR}) ===`);
  return allPassed ? 0 : 1;
}

/* By path, not import.meta.main or .filename: on a Node without them the guard reads undefined,
 * and the script would exit 0 having checked nothing. */
function isEntryPoint() {
  try {
    return realpathSync(process.argv[1]) === fileURLToPath(import.meta.url);
  } catch {
    return false;
  }
}

if (isEntryPoint()) {
  main(process.argv.slice(2)).then(process.exit, (err) => {
    console.error(scrub(err));
    process.exit(1);
  });
}
