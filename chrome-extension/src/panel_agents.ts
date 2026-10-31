/* "AI agenti": the accent card. Its Výnos row is the old panel's estimation flow,
 * unchanged — run, poll, result, the four-field assumptions editor with its live
 * yield and reset, the quota / trial / limit states — shown where the old panel
 * showed the estimate (a flat for sale). The other agents are rows marked "Brzy",
 * and the chat box has no backend yet: any hover, focus or click lays one note
 * over it, moving nothing. */

import { fmtCzk, fmtPct } from '../../frontend/src/lib/format';
import { PPM2_UNIT } from '../../frontend/src/lib/measure';
import { buildCard } from './card';
import { APP_BASE_URL, friendlyDetail } from './config';
import { h, setBusy } from './dom';
import { lineIcon, sparkIcon, type LineIcon } from './glyphs';
import { sleep, type Store } from './store';
import type {
  AgentQuota, BillingMe, EstimationRun, PortalListing, YieldScenarioUpdate,
} from './types';

const DEFAULT_RENOVATION_CZK = 0;
const PATCH_DEBOUNCE_MS = 500;
export const POLL_INTERVAL_MS = 2000;
// Agent estimates run the LLM comparable search and may drain via the worker's job
// lane: 180 × 2 s = 360 s, above the agent's ~240 s wall clock plus queue latency.
export const POLL_MAX_ATTEMPTS = 180;

/* The agents to come: one constant, one row each. */
const SOON: readonly { icon: LineIcon; name: string; line: string }[] = [
  { icon: 'flip', name: 'Flip agent', line: 'Rozpočet opravy a zisk z prodeje' },
  { icon: 'reno', name: 'Agent přestavby', line: 'Vestavba, dělení, změna užívání' },
  { icon: 'scout', name: 'Regionální scout', line: 'Podobné příležitosti v okolí' },
];
const SUGGESTIONS = ['Co ověřit na prohlídce?', 'Jak si stojí proti okolí?', 'Vyplatí se flip?'];
const CHAT_SOON_ID = 'chat-soon';

type Axis = 'rent' | 'cost' | 'price' | 'renovation';

interface Estimate {
  listing: PortalListing | null;
  run: EstimationRun | null;
  busy: boolean;
  quota: AgentQuota | null;
  error: string | null;
  rentTouched: boolean;
  costTouched: boolean;
  priceTouched: boolean;
  renovationTouched: boolean;
  rent: number | null;
  costPerM2: number | null;
  price: number | null;
  /* A flat one-off renovation budget, added to the price as the yield's denominator. */
  renovation: number | null;
}

const blank = (listing: PortalListing | null): Estimate => ({
  listing, run: null, busy: false, quota: null, error: null,
  rentTouched: false, costTouched: false, priceTouched: false, renovationTouched: false,
  rent: null, costPerM2: null, price: null, renovation: null,
});

/* The subject's area and its basis, never one without the other. The run's spec
 * carries an area but no stamp, so its basis is the lookup row's. */
function subjectArea(s: Estimate): { value: number | null; basis: string | null } {
  const l = s.listing;
  const basis = l?.found ? l.area_basis ?? null : null;
  const spec = s.run?.input_spec?.area_m2 ?? null;
  if (spec != null) return { value: spec, basis };
  if (l?.area_m2 != null) return { value: l.area_m2, basis };
  return { value: null, basis: null };
}

/* Whether a fond applies is the SERVER's answer (portal_lookup._fond_default_czk_per_m2),
 * stated by serving a null rate; the area basis only words the hint. */
function fondApplies(s: Estimate): boolean {
  return s.listing != null && s.listing.fond_per_m2_czk_default !== null;
}

function defaultPrice(s: Estimate): number | null {
  const run = s.run;
  if (run?.input_purchase_price_czk != null) return run.input_purchase_price_czk;
  if (run?.estimate_kind === 'sale' && run.estimated_sale_price_czk != null) return run.estimated_sale_price_czk;
  return s.listing?.price_czk ?? null;
}

/* The comparables' rent, else the MF reference rent (a thin market). */
function defaultRent(s: Estimate): number | null {
  return s.run?.estimated_monthly_rent_czk ?? s.listing?.mf_reference_rent_czk ?? null;
}

function computeYield(s: Estimate): number | null {
  const area = subjectArea(s);
  /* Not applicable is ZERO, not null: a plot carries no fond and still yields. */
  const fond = !fondApplies(s) ? 0 : s.costPerM2 != null && area.value != null ? s.costPerM2 * area.value : null;
  const acquisition = s.price != null ? s.price + (s.renovation ?? 0) : null;
  if (s.rent == null || fond == null || acquisition == null || acquisition <= 0) return null;
  return ((s.rent - fond) * 12) / acquisition * 100;
}

function fondHint(s: Estimate): string {
  const area = subjectArea(s);
  if (!fondApplies(s)) {
    return area.basis === 'plot' ? 'Neuplatní se — plocha je pozemek.' : 'Neuplatní se u tohoto typu nemovitosti.';
  }
  if (s.costPerM2 == null || area.value == null) return '';
  return `= ${fmtCzk(Math.round(s.costPerM2 * area.value))}/měs`;
}

function acquisitionHint(s: Estimate): string {
  const reno = s.renovation ?? 0;
  return reno > 0 && s.price != null ? `Akvizice ${fmtCzk(Math.round(s.price + reno))}` : 'Jednorázový rozpočet, přičte se k ceně';
}

/* `estimation_runs.scenario` is ONE value shared with the app's RunPanel: a rate
 * declared inert here is never persisted. */
function scenarioBody(s: Estimate): YieldScenarioUpdate {
  return {
    rent_czk: s.rentTouched ? s.rent : null,
    fond_per_m2_czk: s.costTouched && fondApplies(s) ? s.costPerM2 : null,
    price_czk: s.priceTouched ? s.price : null,
    renovation_czk: s.renovationTouched ? s.renovation : null,
  };
}

function seed(s: Estimate, run: EstimationRun): void {
  const sc = run.scenario;
  s.run = run;
  s.rentTouched = sc?.rent_czk != null;
  s.costTouched = sc?.fond_per_m2_czk != null;
  s.priceTouched = sc?.price_czk != null;
  s.renovationTouched = sc?.renovation_czk != null;
  s.rent = sc?.rent_czk ?? defaultRent(s);
  s.costPerM2 = sc?.fond_per_m2_czk ?? s.listing?.fond_per_m2_czk_default ?? null;
  s.price = sc?.price_czk ?? defaultPrice(s);
  s.renovation = sc?.renovation_czk ?? DEFAULT_RENOVATION_CZK;
}

/* The missing half of the yield's pair: a subject for sale needs a rent, one let
 * needs a sale price; land is never estimated as a rent. */
function estimateKindFor(l: PortalListing): 'rent' | 'sale' {
  if (l.category_type === 'pronajem' || l.category_main === 'pozemek') return 'sale';
  return 'rent';
}

const formatNumber = (n: number | null): string => (n == null || !Number.isFinite(n) ? '' : String(n));

function parseNumber(raw: string): number | null {
  const trimmed = raw.trim().replace(/\s/g, '').replace(',', '.');
  if (trimmed === '') return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/* The old panel's eligibility: the estimate is for a flat for sale. */
export const isSaleFlat = (l: PortalListing | null): boolean =>
  l?.found === true && l.category_main === 'byt' && l.category_type === 'prodej';

export interface AgentsDeps {
  store: Store;
  repaint: () => void;
  /* Updates in place (an edit must not rebuild the input being typed in). */
  root: ParentNode;
}

export function createAgents(deps: AgentsDeps) {
  const { store, repaint } = deps;
  let s: Estimate = blank(null);
  let patchTimer: ReturnType<typeof setTimeout> | null = null;
  /* Every panel open is a new generation: a poll or a read for the last one stops applying. */
  let gen = 0;

  async function loadQuota(at: number): Promise<void> {
    const res = await store.call<BillingMe>({ type: 'get_billing' });
    if (at !== gen || !res.ok) return;
    s.quota = res.data.agent_estimations;
    repaint();
  }

  /* A new panel open: the latest estimate of this ad, and the allowance. */
  function reset(listing: PortalListing | null): void {
    gen++;
    if (patchTimer != null) clearTimeout(patchTimer);
    patchTimer = null;
    s = blank(listing);
    if (!isSaleFlat(listing) || listing == null) return;
    const at = gen;
    void loadQuota(at);
    const latest = listing.latest_estimation;
    if (latest == null) return;
    void store.call<EstimationRun>({ type: 'get_estimation', run_id: latest.estimation_id }).then((res) => {
      if (at !== gen || !res.ok || res.data.status !== 'success') return;
      seed(s, res.data);
      repaint();
    });
  }

  function schedulePatch(): void {
    if (patchTimer != null) clearTimeout(patchTimer);
    const at = gen;
    patchTimer = setTimeout(async () => {
      patchTimer = null;
      if (s.run == null) return;
      const res = await store.call<EstimationRun>({ type: 'patch_scenario', run_id: s.run.id, body: scenarioBody(s) });
      if (at !== gen) return;
      if (!res.ok) {
        s.error = `Uložení selhalo: ${friendlyDetail(res.detail)}`;
        repaint();
        return;
      }
      // Nothing visible depends on the saved run: keep it without a repaint (focus stays in the field).
      s.run = res.data;
      if (s.error != null) {
        s.error = null;
        repaint();
      }
    }, PATCH_DEBOUNCE_MS);
  }

  /* An edit updates the yield and the hints IN PLACE — a repaint would rebuild the input. */
  function onEdit(axis: Axis, value: number | null): void {
    if (axis === 'rent') { s.rent = value; s.rentTouched = true; }
    if (axis === 'cost') { s.costPerM2 = value; s.costTouched = true; }
    if (axis === 'price') { s.price = value; s.priceTouched = true; }
    if (axis === 'renovation') { s.renovation = value; s.renovationTouched = true; }
    const { root } = deps;
    root.querySelectorAll<HTMLElement>('[data-yield]').forEach((n) => { n.textContent = fmtPct(computeYield(s)); });
    const fh = root.querySelector<HTMLElement>('[data-hint-for="cost"]');
    if (fh != null) fh.textContent = fondHint(s);
    const ah = root.querySelector<HTMLElement>('[data-hint-for="renovation"]');
    if (ah != null) ah.textContent = acquisitionHint(s);
    const status = root.querySelector<HTMLElement>('[data-status]');
    if (status != null) status.textContent = 'upraveno · uloženo';
    const reset = root.querySelector<HTMLElement>('[data-reset]');
    if (reset != null) reset.hidden = false;
    schedulePatch();
  }

  function onReset(): void {
    s.rent = defaultRent(s);
    s.costPerM2 = s.listing?.fond_per_m2_czk_default ?? null;
    s.price = defaultPrice(s);
    s.renovation = DEFAULT_RENOVATION_CZK;
    s.rentTouched = s.costTouched = s.priceTouched = s.renovationTouched = false;
    repaint();
    schedulePatch();
  }

  /* The poll runs up to ~6 min, longer than an operator stays on one listing: every
   * apply is generation-guarded. The run still completes server-side and the next
   * open of THAT listing loads it; only the display is dropped. */
  async function createRun(): Promise<void> {
    const subject = s.listing;
    if (subject?.listing_id == null || s.busy) return;
    const at = gen;
    s.busy = true;
    s.error = null;
    repaint();
    const res = await store.call<EstimationRun>({
      type: 'create_estimation', listing_id: subject.listing_id,
      estimate_kind: estimateKindFor(subject), category_main: subject.category_main,
    });
    if (at !== gen) return;
    if (!res.ok) {
      s.busy = false;
      // 429: the monthly allowance is spent (the atomic quota gate).
      if (res.status === 429) {
        void loadQuota(at);
        s.error = 'Měsíční limit odhadů vyčerpán.';
      } else {
        s.error = `Odhad se nepodařilo spustit: ${friendlyDetail(res.detail)}`;
      }
      repaint();
      return;
    }
    let row = res.data;
    for (let i = 0; i < POLL_MAX_ATTEMPTS; i++) {
      if (row.status === 'success' || row.status === 'failed') break;
      await sleep(POLL_INTERVAL_MS);
      if (at !== gen) return;  // navigated away: stop burning polls
      const next = await store.call<EstimationRun>({ type: 'get_estimation', run_id: row.id });
      if (!next.ok) break;
      row = next.data;
    }
    if (at !== gen) return;
    // A metered run consumed a slot (success, or failed once created).
    void loadQuota(at);
    s.busy = false;
    if (row.status !== 'success') s.error = row.error_message ?? 'Odhad se nedokončil včas.';
    else seed(s, row);
    repaint();
  }

  function field(key: Axis, label: string, suffix: string, value: number | null, hint?: string, disabled = false): HTMLElement {
    const wrap = h('div', 'field');
    const id = `field-${key}`;
    const lab = h('label', 'l4', label);
    lab.htmlFor = id;
    const row = h('div', 'field-row');
    const input = h('input', '');
    input.id = id;
    input.type = 'text';
    input.inputMode = 'decimal';
    input.value = formatNumber(value);
    input.disabled = disabled;
    input.dataset.key = key;
    input.addEventListener('input', () => onEdit(key, parseNumber(input.value)));
    row.append(input, h('span', 'field-suffix', suffix));
    wrap.append(lab, row);
    if (hint !== undefined) {
      const p = h('p', 'field-hint', hint);
      p.dataset.hintFor = key;
      wrap.appendChild(p);
    }
    return wrap;
  }

  function editor(run: EstimationRun): HTMLElement {
    const box = h('div', 'editor');
    if (run.estimate_kind === 'rent' && run.estimated_monthly_rent_czk == null) {
      box.appendChild(h('p', 'meta', 'Bez srovnatelných nájmů v okolí — počítáno z MF nájmu (uprav dle potřeby).'));
    } else if (run.confidence === 'low') {
      box.appendChild(h('p', 'meta', 'Nízká spolehlivost odhadu (málo srovnatelných).'));
    }
    box.append(
      field('rent', 'Měsíční nájem', 'Kč', s.rent),
      // A MONTHLY rate per m²: the rent-basis unit from the app's one vocabulary.
      field('cost', 'Fond oprav + SVJ', PPM2_UNIT.rent, s.costPerM2, fondHint(s), !fondApplies(s)),
      field('price', 'Cena', 'Kč', s.price),
      field('renovation', 'Rekonstrukce', 'Kč', s.renovation, acquisitionHint(s)),
    );
    const yieldRow = h('div', 'est-yield');
    const value = h('span', 'mono', fmtPct(computeYield(s)));
    value.dataset.yield = '';
    yieldRow.append(h('span', 'l4', 'Výnos z odhadu'), value);
    const touched = s.rentTouched || s.costTouched || s.priceTouched || s.renovationTouched;
    const foot = h('div', 'line');
    const status = h('span', 'meta', touched ? 'upraveno · uloženo' : 'živý výpočet');
    status.dataset.status = '';
    const reset = h('button', 'link push', 'Reset');
    reset.dataset.reset = '';
    reset.dataset.key = 'reset';
    reset.hidden = !touched;
    reset.onclick = () => onReset();
    foot.append(status, reset);
    box.append(yieldRow, foot);
    return box;
  }

  function row(glyph: string, name: string, desc: string, end: Node | null, tag: 'button' | 'div' = 'button'): HTMLElement {
    const r = h(tag, 'agrow');
    const g = h('span', 'agglyph');
    g.setAttribute('aria-hidden', 'true');
    g.innerHTML = glyph;
    const text = h('span', 'agtext');
    text.append(h('span', 'agname', name), h('span', 'agdesc', desc));
    const tail = h('span', 'agend');
    if (end != null) tail.appendChild(end);
    r.append(g, text, tail);
    return r;
  }

  /* The Výnos row and what hangs under it, by the flow's state. */
  function vynos(): HTMLElement[] {
    const out: HTMLElement[] = [];
    const name = 'Odhad výnosu (komparativní)';
    const glyph = lineIcon('yield');
    const arrow = (): HTMLElement => { const a = h('span', ''); a.innerHTML = lineIcon('arrow'); return a; };
    const q = s.quota;
    const metered = q != null && q.metered;
    const run = s.run;
    if (s.busy) {
      const r = row(glyph, name, 'Odhad probíhá… (~1–3 min)', h('span', 'spin'));
      r.dataset.key = 'vynos';
      r.setAttribute('aria-busy', 'true');
      setBusy(r, true);
      out.push(r);
    } else if (run?.status === 'success') {
      const value = h('span', 'mono', fmtPct(computeYield(s)));
      value.dataset.yield = '';
      const r = row(glyph, name, 'Výnos z odhadu', value, 'div');
      r.dataset.key = 'vynos';
      r.tabIndex = -1;  // no control, but the run button's focus lands on its result, not on the page
      out.push(r, editor(run));
    } else if (run == null && metered && q.remaining <= 0) {
      // The monthly allowance is spent: an upgrade instead of a run that would 429.
      const r = row(glyph, name, `Měsíční limit vyčerpán (${q.used}/${q.quota}).`, null);
      setBusy(r, true);
      out.push(r);
      const up = h('button', 'cta', 'Upgradovat');
      up.dataset.key = 'upgrade';
      up.title = 'Správa plánu ve webové aplikaci.';
      up.onclick = () => { if (APP_BASE_URL) window.open(APP_BASE_URL, '_blank', 'noopener'); };
      out.push(up);
    } else {
      const r = row(glyph, name, metered ? `Spustit odhad (zbývá ${q.remaining})` : 'Spustit odhad', arrow());
      r.dataset.key = 'vynos';
      r.onclick = () => { void createRun(); };
      out.push(r);
      if (metered && q.is_trial) out.push(h('p', 'meta', `Zkušební období: ${q.remaining} z ${q.quota} odhadů.`));
    }
    if (s.error != null) {
      const err = h('p', 'err', s.error);
      err.setAttribute('role', 'alert');
      out.push(err);
    }
    return out;
  }

  function chat(): HTMLElement {
    const box = h('div', 'chat');
    const title = h('h3', 'chat-title');
    const av = (): HTMLElement => { const a = h('span', 'av'); a.setAttribute('aria-hidden', 'true'); a.innerHTML = sparkIcon(); return a; };
    title.append(av(), document.createTextNode('Zeptejte se agenta'));
    const opening = h('div', 'opening');
    opening.append(av(), h('p', '', 'Ptejte se na tuto nemovitost: na cenu, na okolí, nebo co ověřit na prohlídce.'));
    const suggs = h('div', 'suggs');
    SUGGESTIONS.forEach((text, i) => {
      const b = h('button', 'sugg', text);
      b.dataset.key = `sugg-${i}`;
      b.setAttribute('aria-disabled', 'true');
      b.setAttribute('aria-describedby', CHAT_SOON_ID);
      suggs.appendChild(b);
    });
    const composer = h('div', 'composer');
    const label = h('label', 'sr', 'Zpráva agentovi');
    label.htmlFor = 'chat-ask';
    const ask = h('input', 'ask');
    ask.id = 'chat-ask';
    ask.dataset.key = 'chat-ask';
    ask.type = 'text';
    ask.readOnly = true;
    ask.placeholder = 'Zeptejte se na tuto nemovitost…';
    ask.setAttribute('aria-disabled', 'true');
    ask.setAttribute('aria-describedby', CHAT_SOON_ID);
    const send = h('button', 'send');
    send.innerHTML = lineIcon('send');
    send.dataset.key = 'chat-send';
    send.setAttribute('aria-label', 'Odeslat');
    send.setAttribute('aria-disabled', 'true');
    send.setAttribute('aria-describedby', CHAT_SOON_ID);
    composer.append(label, ask, send);
    const soon = h('p', 'chat-soon', 'Chat s agentem připravujeme');
    soon.id = CHAT_SOON_ID;
    box.append(title, opening, suggs, composer, soon);
    return box;
  }

  function card(view: PortalListing): HTMLElement {
    s.listing = { ...view };
    const c = buildCard({ title: 'AI agenti', variant: 'agcard' });
    const spark = h('span', 'spark');
    spark.setAttribute('aria-hidden', 'true');
    spark.innerHTML = sparkIcon();
    c.title.before(spark);
    if (isSaleFlat(view)) c.body.append(...vynos());
    else c.body.appendChild(h('p', 'meta', 'Výnos MF a odhad jsou jen u bytů na prodej.'));
    for (const a of SOON) {
      const r = row(lineIcon(a.icon), a.name, a.line, h('span', 'soon', 'Brzy'), 'div');
      r.classList.add('agrow--soon');
      c.body.appendChild(r);
    }
    c.body.appendChild(chat());
    return c.section;
  }

  return { reset, card };
}
