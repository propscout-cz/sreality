/* Listing-page content script for every portal we scrape.
 *
 * Detail pages → a floating panel (closed shadow root) that shows the
 * property's MF reference rent for flats (with the "Výnos MF" gross yield on a
 * sale flat), rendered by the SHAPE of the one result, and the comparables
 * estimation for sale apartments as the deeper tool / fallback.
 *
 * Index/search pages → per-card controls + yield badges and the corner dock
 * (see index_overlay.ts).
 *
 * All network calls go through chrome.runtime.sendMessage to the background
 * worker (host_permissions + the portal's CORS don't apply there). */

import styles from './styles.css?inline';
import tokens from '../../frontend/src/styles/tokens.css?inline';
import { detailRef, portalForHost, type PortalRef } from '../../frontend/src/lib/portalIds';
import {
  bellIconSvg,
  bookmarkIconSvg,
  eyeOffIconSvg,
  funnelIconSvg,
  savedCollections,
  saveTargets,
  stageLook,
} from './glyphs';
import { failureKind, runIndexOverlay, type IndexOverlayHandle } from './index_overlay';
import { followAppearance } from './appearance';
import { buildCard } from './card';
import {
  APP_BASE_URL,
  COLLECTIONS_URL,
  EXTENSION_RELOADED_DETAIL,
  NOT_SIGNED_IN_DETAIL,
  friendlyDetail,
} from './config';
import { keepFocus } from './dom';
import { INGEST_STEP_CS, isAdminGate, outcomeMessage, runIngest, type IngestStep } from './ingest_flow';
import { onAccountChange } from './session';
import { createStore, isLiveDeal, sleep, type Pending } from './store';
import type {
  AgentQuota,
  ApiMessage,
  ApiResult,
  AuthState,
  BillingMe,
  EstimationRun,
  IngestState,
  PortalListing,
  RouteMessage,
  YieldScenarioUpdate,
} from './types';
import type { Collection, Note, PipelineStage, Tag } from '../../frontend/src/lib/types';
// Shared product brand — the ONE definition (frontend/src/lib/brand.ts), the
// same one the SPA uses. Rebranding there updates the panel wordmark here too.
import { APP_NAME } from '../../frontend/src/lib/brand';
// The app's own definitions, one each: the MF shape rule and its yield text,
// the formatters and the per-m² vocabulary, the stage look, the collection rules.
import { mfShape, mfYieldText } from '../../frontend/src/lib/mfReference';
import { fmtArea, fmtCount, fmtCzk, fmtMeasuredPricePerM2, fmtPct } from '../../frontend/src/lib/format';
import { PPM2_UNIT, areaBasisLabel } from '../../frontend/src/lib/measure';
import { stageAccent, stageBadge } from '../../frontend/src/lib/pipelineStage';
import { COLLECTION_SAVE_LABEL, isMine, shareAudience } from '../../frontend/src/lib/collectionRules';

const DEFAULT_RENOVATION_CZK = 0;
/* Back-compat ONLY, never a second live definition of the fond rate: the value
 * to seed when the lookup response carries no `fond_per_m2_czk_default` KEY at
 * all — a bundle built from this tree talking to an API deployed before W7
 * (build-extension.yml uploads a dist/ on every PR, so that pairing is the
 * normal way this branch gets reviewed). Without it an absent key reads as
 * "no fond applies", the yield returns null and the panel's headline figure
 * silently disappears for every subject. The live definition is the server's
 * `api/schemas.DEFAULT_FOND_CZK_PER_M2`, served per subject; delete this once
 * no pre-W7 API can be reached. */
const LEGACY_FOND_FALLBACK_CZK_PER_M2 = 10;
const PATCH_DEBOUNCE_MS = 500;
const POLL_INTERVAL_MS = 2000;
// Agent estimates (Wave 1) run the LLM comparable search and may drain via the
// worker job lane, so they can take well past the old 120 s (60×2 s) window —
// which reported a succeeding run as failed. 180×2 s = 360 s, safely above the
// agent's ~240 s wall clock plus queue latency.
const POLL_MAX_ATTEMPTS = 180;
const HOST_ELEMENT_ID = '__sreality_yield_panel_host__';

/* Minimized = the panel collapses to a tiny bar showing just the two yield
 * figures. The preference persists across listings/pages via chrome.storage.local
 * (the "storage" permission), so once the operator tucks it away it stays small
 * while they browse. Loaded once on boot; openPanel awaits it before first paint
 * so there's no expand→collapse flash. */
const MINIMIZED_KEY = 'panelMinimized';
let minimized = false;
const minimizedReady: Promise<void> = chrome.storage.local
  .get([MINIMIZED_KEY])
  .then((r) => { minimized = r[MINIMIZED_KEY] === true; })
  .catch(() => { /* storage unavailable → default to expanded */ });

type Phase = 'loading' | 'deactivated' | 'active' | 'error' | 'signed_out';

interface PanelState {
  phase: Phase;
  /* The signed-in operator's email (Wave 1 extension login), or null when
   * signed out / not yet loaded. Drives the sign-out control in the header. */
  authEmail: string | null;
  /* Our scraped facts + its property's MF result for the subject listing. */
  listing: PortalListing | null;
  /* byt+prodej? Gates the estimation block; the MF block renders by the
   * result's shape and the app link + facts show regardless. null = an ad we
   * do not hold. */
  isSaleApt: boolean | null;
  /* Optional comparables estimation (full row) for the editable yield block. */
  run: EstimationRun | null;
  /* Per-axis "touched" — null + touched=false means "follow the default". */
  rentTouched: boolean;
  costTouched: boolean;
  priceTouched: boolean;
  renovationTouched: boolean;
  rent: number | null;
  costPerM2: number | null;
  price: number | null;
  /* Flat one-off renovation budget, added to price for the yield denominator. */
  renovation: number | null;
  /* True while an estimation is being created/polled. */
  busy: boolean;
  /* True while the pill shows its "Odebrat z pipeline?" confirm row. Removal is
   * the one destructive pipeline action and it has no undo — the card's "v
   * pipeline od" / "ve fázi od" are deleted and re-adding restamps them — so
   * the ✕ asks first, the same two-step the note rows and the SPA's stage menu
   * use (rule #22: one pipeline affordance, one behaviour, every surface). */
  pipelineConfirmRemove: boolean;
  /* True while the checklist under the action bar is open. */
  collectionsOpen: boolean;
  /* The last add/remove failure, shown inside the checklist beside the row the
   * operator just clicked — not at the foot of the panel. */
  collectionError: string | null;
  /* True while the tag checklist is open. One checklist at a time: opening it
   * closes the collection one, and the other way round. */
  tagsOpen: boolean;
  /* The last attach/detach failure, shown inside the tag checklist. */
  tagError: string | null;
  /* The property's operator notes (null = not loaded). Per-property, not cached. */
  notes: Note[] | null;
  /* True once every retry of the notes read failed: the panel says so and offers
   * a retry instead of drawing "no notes" (MS16). */
  notesFailed: boolean;
  /* True while a note save is in flight (disables the add box). */
  noteBusy: boolean;
  /* id of the note currently open for inline edit, or null. Only one at a time. */
  noteEditingId: number | null;
  /* id of the note showing its "Delete this note?" confirm row, or null. */
  noteConfirmDeleteId: number | null;
  /* True while an edit-save or delete is in flight for noteEditingId /
   * noteConfirmDeleteId (disables that row's buttons). */
  noteRowBusy: boolean;
  /* The caller's agent-estimation allowance (null = not loaded / fetch failed —
   * the counter is simply hidden, never blocking the estimate button). */
  quota: AgentQuota | null;
  /* "Přidat do databáze" on an ad we do not hold: the step on screen while the
   * flow runs (null = idle), and the sentence it ended on when it did not end
   * with the ad held. */
  ingestStep: IngestStep | null;
  ingestMessage: string | null;
  errorMessage: string | null;
}

/* What a render reads: the panel's own state plus, from the one store (store.ts),
 * its property's curation (on `listing`), the lists that name it and the writes
 * in flight — projected per render by `view`, never copied into the state. */
interface PanelView extends PanelState {
  stages: PipelineStage[] | null;
  collections: Collection[] | null;
  /* Every retry of the read failed with no list to show: say so, not "Načítám…". */
  collectionsFailed: boolean;
  tags: Tag[] | null;
  tagsFailed: boolean;
  /* A write of that kind is in flight for the property (disables its control). */
  pipelineBusy: boolean;
  collectionBusy: boolean;
  tagBusy: boolean;
  dismissBusy: boolean;
}

/* An extension reload, update, disable or removal orphans the content script
 * already in an open tab (Chrome re-injects only on page load): sendMessage
 * then throws "Extension context invalidated." synchronously. send() resolves
 * with that as a failure instead of rejecting — every caller's busy flag
 * ("Přihlašuji…") waits for a result — and says what actually fixes it (a
 * detail the search page's notice recognises, to offer a reload instead of a
 * retry). */
function runtimeDetail(message: string | undefined): string {
  if (message != null && /context invalidated/i.test(message)) {
    return EXTENSION_RELOADED_DETAIL;
  }
  return message ?? 'runtime error';
}

function send(message: ApiMessage): Promise<ApiResult<unknown>> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(message, (response: ApiResult<unknown>) => {
        const error = chrome.runtime.lastError;  // always read: Chrome logs an unchecked one
        resolve(error ? { ok: false, status: 0, detail: runtimeDetail(error.message) } : response);
      });
    } catch (err) {
      resolve({
        ok: false,
        status: 0,
        detail: runtimeDetail(err instanceof Error ? err.message : String(err)),
      });
    }
  });
}

/* The one store both surfaces draw from (store.ts). */
const store = createStore({ send, sleep, onAccountChange });
const { call } = store;

// ----------------------------------------------------------------------
// Formatting
// ----------------------------------------------------------------------

/* A note's timestamp as a compact cs-CZ date (full datetime in the title attr). */
function fmtNoteDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('cs-CZ', { day: 'numeric', month: 'numeric', year: 'numeric' });
}

function formatNumber(n: number | null): string {
  if (n == null || !Number.isFinite(n)) return '';
  return String(n);
}

function parseNumber(raw: string): number | null {
  const trimmed = raw.trim().replace(/\s/g, '').replace(',', '.');
  if (trimmed === '') return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/* The SPA's <PencilIcon>/<TrashIcon> (icons.tsx), hand-reproduced like the
 * marks in glyphs.ts — used on the note edit/delete row buttons. */
const PENCIL_ICON_SVG =
  '<svg viewBox="0 0 11 11" fill="none" stroke="currentColor" stroke-width="1" ' +
  'stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M1.5 9.5 L1.5 7.5 L7 2 L9 4 L3.5 9.5 Z"/></svg>';

const TRASH_ICON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>' +
  '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>' +
  '<line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>';

const COLLECTION_MENU_ID = 'collection-menu';
const TAG_MENU_ID = 'tag-menu';
const TAGS_LABEL = 'Štítky';

// ----------------------------------------------------------------------
// Estimation-editor defaults (mirror the SPA's YieldBlock, but now seeded
// with the subject listing's own price/area from our lookup).
// ----------------------------------------------------------------------

/* THE panel's destructive-confirm row — an inline "are you sure" with the
 * question, one line of consequence, and Confirm / Cancel. Generic on purpose:
 * the panel should grow exactly one of these, not one per action (the SPA made
 * the same move with <PipelineStageMenu>'s confirm). */
function buildConfirmRow(opts: {
  question: string;
  detail?: string;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}): HTMLElement {
  const row = document.createElement('div');
  row.className = 'confirm-row';
  row.setAttribute('role', 'group');
  row.setAttribute('aria-label', opts.question);

  const q = document.createElement('p');
  q.className = 'confirm-question';
  q.textContent = opts.question;
  row.appendChild(q);

  if (opts.detail != null && opts.detail !== '') {
    const d = document.createElement('p');
    d.className = 'confirm-detail';
    d.textContent = opts.detail;
    row.appendChild(d);
  }

  const actions = document.createElement('div');
  actions.className = 'confirm-actions';
  const yes = document.createElement('button');
  yes.type = 'button';
  yes.className = 'confirm-yes';
  yes.textContent = opts.confirmLabel;
  yes.disabled = opts.busy ?? false;
  yes.onclick = opts.onConfirm;
  actions.appendChild(yes);
  const no = document.createElement('button');
  no.type = 'button';
  no.className = 'confirm-no';
  no.textContent = 'Zrušit';
  no.disabled = opts.busy ?? false;
  no.onclick = opts.onCancel;
  actions.appendChild(no);
  row.appendChild(actions);

  return row;
}

/* The subject's area AND the basis of that area — never one without the other.
 * `area_m2` is polymorphic (migration 423): 905 m² of floor and 905 m² of
 * parcel are different denominators, and only the stamp says which one this is.
 *
 * Two sources, in the historical fallback order. `state.run.input_spec` carries
 * an area but NO stamp — the server builds that spec from lat/lng/area/
 * disposition/floor only — so its basis is taken from the lookup row when we
 * have one (the run's target area IS that row's area whenever the URL matched
 * something we scrape) and is honestly null when we don't (an LLM-parsed
 * subject that isn't in our DB has no stamp anywhere). */
interface SubjectArea {
  value: number | null;
  basis: string | null;
}

function subjectArea(state: PanelState): SubjectArea {
  const l = state.listing;
  const listingBasis = l?.found ? l.area_basis ?? null : null;
  const specArea = state.run?.input_spec?.area_m2 ?? null;
  if (specArea != null) return { value: specArea, basis: listingBasis };
  if (l?.area_m2 != null) return { value: l.area_m2, basis: listingBasis };
  return { value: null, basis: null };
}

/* A fond (fond oprav + SVJ) is levied per m² of a DWELLING's floor area. It is
 * never levied on a parcel, so when the denominator is a plot the rate must not
 * be multiplied by it at all: 10 Kč per m² × 905 m² of land manufactures ~9 050
 * Kč/month of cost, which the yield then subtracts from the rent — enough to
 * drive the figure negative.
 *
 * WHERE that decision is made is the whole point. The server makes it once
 * (`api/portal_lookup._fond_default_czk_per_m2`) and states it by serving a
 * present-but-NULL rate; this reads that answer. Re-deriving it here from
 * `area_basis` would be a second definition of "does a fond apply", and the two
 * would disagree: the server tests `area_basis == 'plot'` OR
 * `category_main == 'pozemek'`, and migration 423 added `area_basis` with no
 * backfill — so a land row not yet re-scraped is `area_basis` NULL while it is
 * plainly a parcel. An ABSENT field is an API predating W7: assume a fond
 * applies, exactly as the panel did before. */
function fondApplies(state: PanelState): boolean {
  return state.listing?.fond_per_m2_czk_default !== null;
}

function defaultPrice(state: PanelState): number | null {
  const run = state.run;
  if (run?.input_purchase_price_czk != null) return run.input_purchase_price_czk;
  if (run?.estimate_kind === 'sale' && run.estimated_sale_price_czk != null) {
    return run.estimated_sale_price_czk;
  }
  return state.listing?.price_czk ?? null;
}

/* The starting service charge, SERVED BY THE SERVER per subject
 * (api/schemas.DEFAULT_FOND_CZK_PER_M2, resolved through the lookup). The
 * extension deliberately holds no live copy of the number.
 *
 * A served `null` is a real answer — no fond can apply to this subject — and
 * an empty field is then the honest state. An ABSENT key is a different thing
 * entirely: an API that predates W7, or no lookup row at all. Collapsing the
 * two with `?? null` is what would blank the field, make `fond` null and drop
 * the yield to "—" for every listing, so they are branched apart explicitly. */
function defaultFond(state: PanelState): number | null {
  const served = state.listing?.fond_per_m2_czk_default;
  return served === undefined ? LEGACY_FOND_FALLBACK_CZK_PER_M2 : served;
}

function defaultRent(state: PanelState): number | null {
  /* Prefer the comparables estimate; if it found nothing (thin market), fall
   * back to the MF reference rent so the yield calculator still works. */
  return state.run?.estimated_monthly_rent_czk
    ?? state.listing?.mf_reference_rent_czk
    ?? null;
}

function computeYield(state: PanelState): number | null {
  const { rent, costPerM2, price, renovation } = state;
  const area = subjectArea(state);
  /* Not-applicable is ZERO, not null: a plot genuinely carries no fond, so the
   * yield is still rent × 12 ÷ acquisition and must keep rendering. Null stays
   * reserved for "a fond applies but we can't size it" (no rate, no area). */
  const fond = !fondApplies(state)
    ? 0
    : costPerM2 != null && area.value != null
      ? costPerM2 * area.value
      : null;
  /* Total acquisition cost = listing price + one-off renovation budget. */
  const acquisition = price != null ? price + (renovation ?? 0) : null;
  if (rent == null || fond == null || acquisition == null || acquisition <= 0) {
    return null;
  }
  return ((rent - fond) * 12) / acquisition * 100;
}

/* The two derived quantities the yield is built from, shown as field hints so the
 * operator sees the formula's parts (mirrors the SPA's YieldBlock): the monthly
 * fond from the per-m² rate × area, and the acquisition denominator (price +
 * renovation). Empty fond hint (no area) collapses via `.field-hint:empty`. */
function fondHint(state: PanelState): string {
  const area = subjectArea(state);
  /* Say WHY the field is inert rather than leaving the operator to wonder why
   * their typed rate changes nothing. The DECISION came from the server; the
   * `area_basis` stamp is used only to word it, and is absent for a land row
   * the server recognised by `category_main` alone. */
  if (!fondApplies(state)) {
    return area.basis === 'plot'
      ? 'Neuplatní se — plocha je pozemek.'
      : 'Neuplatní se u tohoto typu nemovitosti.';
  }
  if (state.costPerM2 == null || area.value == null) return '';
  return `= ${fmtCzk(Math.round(state.costPerM2 * area.value))}/měs`;
}

function acquisitionHint(state: PanelState): string {
  const reno = state.renovation ?? 0;
  return reno > 0 && state.price != null
    ? `Akvizice ${fmtCzk(Math.round(state.price + reno))}`
    : 'Jednorázový rozpočet, přičte se k ceně';
}

/* The two yield figures shown in the minimized bar: the MF gross yield (a
 * value's, or a range's two) and the operator's live comparables yield (when an
 * estimation is loaded). Either may be absent — the bar degrades to whatever
 * exists. */
function minimizedYieldCells(state: PanelState): { label: string; value: string }[] {
  const cells: { label: string; value: string }[] = [];
  const mf = mfYieldText(state.listing?.mf_gross_yield_pct, state.listing?.mf_reference_rent);
  if (mf != null) cells.push({ label: 'MF', value: mf });
  if (state.run?.status === 'success') {
    const y = computeYield(state);
    if (y != null) cells.push({ label: 'Odhad', value: fmtPct(y) });
  }
  return cells;
}

/* The window-control glyphs: a minimize line (collapse to bar) + a restore
 * chevron (the bar grows upward into the full card). Inline SVG, currentColor. */
function minimizeIconSvg(): string {
  return (
    '<svg class="ctrl-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round" aria-hidden="true">' +
    '<line x1="4" y1="11.5" x2="12" y2="11.5"/></svg>'
  );
}

function restoreIconSvg(): string {
  return (
    '<svg class="ctrl-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<polyline points="4.5,10 8,6.5 11.5,10"/></svg>'
  );
}

/* Toggle the persisted minimized preference + re-render the current panel. */
function onToggleMinimize(): void {
  minimized = !minimized;
  void chrome.storage.local.set({ [MINIMIZED_KEY]: minimized });
  render(state);
}

function bodyFromState(state: PanelState): YieldScenarioUpdate {
  return {
    rent_czk: state.rentTouched ? state.rent : null,
    /* `estimation_runs.scenario` is ONE persisted value shared with the SPA's
     * RunPanel, which applies whatever it finds there. Writing a rate this
     * panel has just declared inert would hand the same run two yields — ours
     * with fond 0, the SPA's with the rate × a parcel. Never persist one. */
    fond_per_m2_czk: state.costTouched && fondApplies(state) ? state.costPerM2 : null,
    price_czk: state.priceTouched ? state.price : null,
    renovation_czk: state.renovationTouched ? state.renovation : null,
  };
}

/* Load the editable scenario from a full estimation run. */
function seedFromRun(state: PanelState, run: EstimationRun): PanelState {
  const sc = run.scenario;
  const next: PanelState = { ...state, run };
  return {
    ...next,
    rentTouched: sc?.rent_czk != null,
    costTouched: sc?.fond_per_m2_czk != null,
    priceTouched: sc?.price_czk != null,
    renovationTouched: sc?.renovation_czk != null,
    rent: sc?.rent_czk ?? defaultRent(next),
    costPerM2: sc?.fond_per_m2_czk ?? defaultFond(next),
    price: sc?.price_czk ?? defaultPrice(next),
    renovation: sc?.renovation_czk ?? DEFAULT_RENOVATION_CZK,
  };
}

// ----------------------------------------------------------------------
// Panel mount + render
// ----------------------------------------------------------------------

function mountPanel(): {
  shadow: ShadowRoot;
  render: (state: PanelState) => void;
  destroy: () => void;
} {
  const existing = document.getElementById(HOST_ELEMENT_ID);
  if (existing != null) existing.remove();

  const host = document.createElement('div');
  host.id = HOST_ELEMENT_ID;
  document.documentElement.appendChild(host);
  const shadow = host.attachShadow({ mode: 'closed' });
  const styleEl = document.createElement('style');
  /* TEMPORARY (until this panel is replaced): the app's palette ahead of the
   * panel's own, for the stage colours lib/pipelineStage names and the brick
   * styles.css's note deletion reads. The host carries no theme attributes, so
   * it stays Měď light like the rest of it. */
  styleEl.textContent = tokens + styles;
  shadow.appendChild(styleEl);

  const panel = document.createElement('div');
  panel.className = 'panel';
  shadow.appendChild(panel);

  /* The ledger header band: a small copper index-mark + product wordmark, and
   * the close control. The wordmark is the shared product brand (APP_NAME) —
   * mirrors the SPA header — so it reads honestly for every listing, including
   * non-apartments where no yield is shown. */
  function header(showMinimize: boolean): HTMLElement {
    const h = document.createElement('div');
    h.className = 'p-head';
    const mark = document.createElement('div');
    mark.className = 'p-mark';
    const tick = document.createElement('span');
    tick.className = 'p-tick';
    mark.appendChild(tick);
    const word = document.createElement('span');
    word.className = 'p-word';
    word.textContent = APP_NAME;
    mark.appendChild(word);
    h.appendChild(mark);
    const controls = document.createElement('div');
    controls.className = 'p-controls';
    if (showMinimize) {
      const min = document.createElement('button');
      min.className = 'p-min';
      min.type = 'button';
      min.title = 'Minimalizovat';
      min.setAttribute('aria-label', 'Minimalizovat');
      min.innerHTML = minimizeIconSvg();
      min.onclick = () => onToggleMinimize();
      controls.appendChild(min);
    }
    const close = document.createElement('button');
    close.className = 'p-close';
    close.type = 'button';
    close.textContent = '×';
    close.title = 'Skrýt panel';
    close.onclick = () => host.remove();
    controls.appendChild(close);
    h.appendChild(controls);
    return h;
  }

  /* The minimized panel: a tiny one-line bar showing just the two yield figures
   * (the operator's at-a-glance triage signal), with a restore chevron + close.
   * Clicking anywhere on the bar (except ×) expands. Reads as the spine of the
   * same filed card — copper tick + stamped copper figures. */
  function renderMinimizedBar(state: PanelView): HTMLElement {
    const bar = document.createElement('div');
    bar.className = 'min-bar';
    bar.title = 'Rozbalit panel';
    bar.onclick = () => onToggleMinimize();

    const tick = document.createElement('span');
    tick.className = 'min-tick';
    bar.appendChild(tick);

    const content = document.createElement('div');
    content.className = 'min-content';
    if (state.phase === 'loading') {
      content.appendChild(minText('Načítám…'));
    } else if (state.phase === 'deactivated') {
      content.appendChild(minText('Není v databázi'));
    } else {
      const cells = minimizedYieldCells(state);
      if (cells.length > 0) {
        cells.forEach((c, i) => {
          if (i > 0) {
            const sep = document.createElement('span');
            sep.className = 'min-sep';
            sep.textContent = '·';
            content.appendChild(sep);
          }
          const cell = document.createElement('span');
          cell.className = 'min-cell';
          const lab = document.createElement('span');
          lab.className = 'min-label';
          lab.textContent = c.label;
          const val = document.createElement('span');
          val.className = 'min-value';
          val.textContent = c.value;
          cell.append(lab, val);
          content.appendChild(cell);
        });
      } else {
        /* No yields (non-sale-apt / not yet computed) → a compact subject. */
        const l = state.listing;
        const parts: string[] = [];
        const kind = l?.kind_label ?? l?.disposition;
        if (kind) parts.push(kind);
        if (l?.price_czk != null) parts.push(fmtCzk(l.price_czk));
        content.appendChild(minText(parts.join(' · ') || APP_NAME));
      }
    }
    bar.appendChild(content);

    const restore = document.createElement('span');
    restore.className = 'min-restore';
    restore.innerHTML = restoreIconSvg();
    bar.appendChild(restore);

    const close = document.createElement('button');
    close.className = 'min-close';
    close.type = 'button';
    close.textContent = '×';
    close.title = 'Skrýt panel';
    close.onclick = (e) => { e.stopPropagation(); host.remove(); };
    bar.appendChild(close);

    return bar;
  }

  function minText(text: string): HTMLElement {
    const s = document.createElement('span');
    s.className = 'min-fallback';
    s.textContent = text;
    return s;
  }

  /* Every state change rebuilds the panel wholesale (build below). Around it,
   * keep what a rebuild would otherwise take from the operator mid-action:
   *   - focus (by data-key) and the caret/selection inside it — so typing in
   *     the note box or keyboarding down the collection checklist isn't
   *     interrupted by a background load landing;
   *   - both scroll positions — the panel's own and the checklist's — which
   *     would otherwise snap to the top on every click in them. */
  const render = (state: PanelState): void => {
    const panelTop = panel.scrollTop;
    const listTop = panel.querySelector<HTMLElement>('.coll-list')?.scrollTop ?? 0;
    keepFocus(shadow, () => build(view(state)));
    panel.scrollTop = panelTop;
    const list = panel.querySelector<HTMLElement>('.coll-list');
    if (list != null) list.scrollTop = listTop;
  };

  const build = (state: PanelView): void => {
    panel.innerHTML = '';
    /* Minimized collapses every phase to the tiny bar EXCEPT error (a failure
     * should stay fully visible). The minimize control itself only shows once
     * there's a full panel worth collapsing (active phase). */
    const isMin = minimized && state.phase !== 'error';
    panel.classList.toggle('panel--min', isMin);
    panel.classList.toggle('panel--muted', state.phase === 'deactivated' && !isMin);

    if (isMin) {
      panel.appendChild(renderMinimizedBar(state));
      return;
    }

    panel.appendChild(header(state.phase === 'active'));
    const body = document.createElement('div');
    body.className = 'p-body';
    panel.appendChild(body);

    if (state.phase === 'loading') {
      body.appendChild(note('Načítám data…', 'note--loading'));
      return;
    }
    if (state.phase === 'signed_out') {
      renderSignedOut(body, state);
      return;
    }
    if (state.phase === 'error') {
      renderAuthStrip(body, state);
      const line = errorLine(state.errorMessage ?? 'Něco se nepovedlo.');
      /* The lookup carries the pipeline card, collections and dismissal: a
       * failed one is retried whole (MS16). A reload or a missing API config is
       * not fixed by asking again, so those get no retry. */
      if (panelRef != null && failureKind(state.errorMessage ?? '') === 'error') {
        const ref = panelRef;
        line.append(' ', retryLink(() => { void openPanel(ref, panelUrl); }));
      }
      body.appendChild(line);
      return;
    }
    if (state.phase === 'deactivated') {
      renderAuthStrip(body, state);
      renderNotInDb(body, state);
      return;
    }

    /* active — read top-down: sign-out control → WHAT it is (subject) → the
     * property's MF result → act on it (bookmark / open in app) → the deeper
     * estimate. MF renders by the result's shape (any flat that has one); the
     * estimate is gated to apartments for sale; the subject + actions are not. */
    renderAuthStrip(body, state);
    renderSubjectFacts(body, state);
    renderMfBlock(body, state);
    if (state.isSaleApt !== false) {
      renderActionsBar(body, state);
      renderEstimation(body, state);
    } else {
      renderActionsBar(body, state);
      body.appendChild(note('Výnos MF a odhad jsou jen u bytů na prodej.'));
    }
    renderNotes(body, state);  // any listing we have a property for
    if (state.errorMessage != null) body.appendChild(errorLine(state.errorMessage));
    // Focus + scroll are restored by render(), around this build.
  };

  /* An ad we do not hold: say so and offer to add it. While the flow runs, its
   * step stands where the button was; a refusal or failure is one sentence
   * above the button, which a click re-sends (the API is idempotent). */
  function renderNotInDb(body: HTMLElement, state: PanelView): void {
    body.appendChild(note('Tato nemovitost zatím není v naší databázi.'));
    if (state.ingestStep != null) {
      body.appendChild(note(INGEST_STEP_CS[state.ingestStep], 'note--loading'));
      return;
    }
    if (state.ingestMessage != null) body.appendChild(errorLine(state.ingestMessage));
    if (ingestForbidden) return;
    const add = document.createElement('button');
    add.className = 'btn-primary';
    add.type = 'button';
    add.textContent = 'Přidat do databáze';
    add.onclick = () => { void onIngest(); };
    body.appendChild(add);
  }

  function note(text: string, variant = ''): HTMLElement {
    const p = document.createElement('p');
    p.className = 'note' + (variant ? ` ${variant}` : '');
    p.textContent = text;
    return p;
  }

  function errorLine(text: string): HTMLElement {
    const p = document.createElement('p');
    p.className = 'note note--error';
    p.textContent = text;
    return p;
  }

  function retryLink(onRetry: () => void): HTMLElement {
    const a = document.createElement('a');
    a.href = '#';
    a.className = 'coll-link';
    a.textContent = 'Zkusit znovu';
    a.onclick = (e) => { e.preventDefault(); onRetry(); };
    return a;
  }

  /* The extension's own sign-in prompt (Wave 1) — every route now requires a
   * real Supabase session, so a signed-out operator sees this instead of a
   * raw 401. Google OAuth via chrome.identity.launchWebAuthFlow + PKCE, run
   * in the background worker (the only context that can open the auth
   * window and reach GoTrue). */
  function renderSignedOut(body: HTMLElement, state: PanelView): void {
    body.appendChild(note('Pro zobrazení dat se prosím přihlaste.'));
    if (state.errorMessage != null) body.appendChild(errorLine(state.errorMessage));
    const btn = document.createElement('button');
    btn.className = 'btn-primary';
    btn.type = 'button';
    btn.disabled = state.busy;
    btn.textContent = state.busy ? 'Přihlašuji…' : 'Přihlásit se přes Google';
    btn.onclick = () => { void onSignIn(); };
    body.appendChild(btn);
  }

  /* The signed-in operator's identity + a sign-out control — shown atop the
   * panel body whenever a session is loaded (active/deactivated/error), so
   * signing out is always one click away without a separate popup surface. */
  function renderAuthStrip(body: HTMLElement, state: PanelView): void {
    if (state.authEmail == null) return;
    const strip = document.createElement('div');
    strip.className = 'auth-strip';
    const email = document.createElement('span');
    email.className = 'auth-email';
    email.textContent = state.authEmail;
    email.title = state.authEmail;
    strip.appendChild(email);
    const out = document.createElement('a');
    out.className = 'auth-signout';
    out.textContent = 'Odhlásit';
    out.href = '#';
    out.onclick = (e) => { e.preventDefault(); void onSignOut(); };
    strip.appendChild(out);
    body.appendChild(strip);
  }

  /* The two "this listing ↔ our app" affordances in one row: the deal-pipeline
   * bookmark (left) + the "open in our app" deep-link (right). Either may be
   * absent (no property yet / no SPA base configured); the row appears only if
   * something landed in it. */
  function renderActionsBar(body: HTMLElement, state: PanelView): void {
    const row = document.createElement('div');
    row.className = 'actions-bar';
    renderPipelineToggle(row, state);       // the funnel — sole pipeline affordance (rule #22)
    renderCollectionToggle(row, state);     // save to any collection (rule #18)
    renderDismissToggle(row, state);        // hide from discovery (migration 536)
    renderAppLink(row, state);
    if (row.childElementCount > 0) body.appendChild(row);
    renderCollectionMenu(body, state);      // the checklist opens full-width under the row
    renderTagRow(body, state);              // the property's tags + their checklist (rule #18)
    renderTagMenu(body, state);
  }

  function renderAppLink(container: HTMLElement, state: PanelView): void {
    const l = state.listing;
    // `found` is the DB-membership flag the lookup already returns. Gating on
    // sreality_id != null would hide the app link for every post-Gate-2 listing,
    // which HAS an app page — and the link below never uses that id anyway.
    if (!l || !l.found || !APP_BASE_URL) return;
    const a = document.createElement('a');
    a.className = 'app-link';
    // Canonical natural-key URL, never the negative synthetic id (migration 097):
    // the extension already holds the (source, native id) it looked the listing up
    // by, which is exactly the app's /listing/{source}/{native} route.
    a.href =
      `${APP_BASE_URL}/listing/${encodeURIComponent(l.source)}/${encodeURIComponent(l.source_id)}`;
    a.target = '_blank';
    a.rel = 'noopener';
    const txt = document.createElement('span');
    txt.textContent = 'Otevřít v aplikaci';
    const arrow = document.createElement('span');
    arrow.className = 'app-link-arrow';
    arrow.textContent = '→';
    a.append(txt, ' ', arrow);
    container.appendChild(a);
  }

  /* Deal-pipeline control for the listing's property (rule #22). Mirrors the
   * SPA's PipelineToggle: out of pipeline → a copper "Přidat do pipeline" button
   * (add → entry stage); in pipeline → a filled pill with a stage `<select>` (the
   * app's single-choice control — change stage via the SAME audited move PATCH)
   * + a `✕` to remove. Property-grain — shown for ANY listing we have a property
   * for, hidden during the brief window a freshly-scraped row has no property_id. */
  function renderPipelineToggle(container: HTMLElement, state: PanelView): void {
    const l = state.listing;
    if (l == null || !l.found || l.property_id == null) return;
    const inPipe = l.pipeline?.in_pipeline ?? false;

    if (!inPipe) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pipeline-toggle';
      btn.disabled = state.pipelineBusy;
      btn.setAttribute('aria-pressed', 'false');
      btn.title = 'Přidat do pipeline';
      btn.innerHTML = funnelIconSvg(false);
      const label = document.createElement('span');
      label.textContent = 'Přidat do pipeline';
      btn.appendChild(label);
      btn.onclick = () => { void onTogglePipeline(); };
      container.appendChild(btn);
      return;
    }

    const pill = document.createElement('div');
    pill.className = 'pipeline-pill' + (state.pipelineBusy ? ' pipeline-pill--busy' : '');
    /* Tint by the CURRENT stage, like the SPA's pill — the pill was copper for
     * every stage before the badge existed, which made "which stage am I on?"
     * unreadable at a glance. */
    const look = stageLook(l.pipeline);
    const accent = stageAccent(look);
    pill.style.color = accent.fg;
    pill.style.background = accent.soft;
    pill.style.borderColor = accent.fg;

    const icon = document.createElement('span');
    icon.className = 'pipeline-pill-icon';
    icon.innerHTML = funnelIconSvg(true);
    const badge = look && stageBadge(look, state.stages ?? []);
    if (badge != null) {
      const code = document.createElement('span');
      code.className = 'pipeline-stage-code';
      code.textContent = badge;
      icon.appendChild(code);
    }
    pill.appendChild(icon);

    /* The stage selector. Options come from the loaded stage list; until it
     * arrives, a single option (the current stage) keeps the label visible. */
    const select = document.createElement('select');
    select.className = 'pipeline-select';
    select.disabled = state.pipelineBusy;
    select.title = 'Změnit fázi';
    select.setAttribute('aria-label', 'Fáze v pipeline');
    const stages = state.stages
      ?? (l.pipeline?.stage_id != null
        ? [{ id: l.pipeline.stage_id, label: l.pipeline.stage_label ?? 'V pipeline' }]
        : []);
    if (state.stages == null) select.disabled = true;  // not loaded yet
    for (const s of stages) {
      const opt = document.createElement('option');
      opt.value = String(s.id);
      opt.textContent = s.label;
      if (s.id === l.pipeline?.stage_id) opt.selected = true;
      select.appendChild(opt);
    }
    select.onchange = () => { void onMoveStage(Number(select.value)); };
    pill.appendChild(select);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'pipeline-remove';
    remove.disabled = state.pipelineBusy;
    remove.title = 'Odebrat z pipeline';
    remove.setAttribute('aria-label', 'Odebrat z pipeline');
    remove.setAttribute('aria-expanded', String(state.pipelineConfirmRemove));
    remove.textContent = '✕';
    remove.onclick = () => onRemovePipelineRequest();
    pill.appendChild(remove);

    container.appendChild(pill);

    /* The confirm row (see PanelState.pipelineConfirmRemove): removal is the one
     * pipeline action that can't be undone, so it asks — and points at the
     * closed stages, which keep the deal's record instead of deleting it. */
    if (state.pipelineConfirmRemove) {
      const closed = (state.stages ?? []).filter((s) => s.is_terminal);
      container.appendChild(buildConfirmRow({
        question: 'Odebrat z pipeline?',
        detail: 'Karta ztratí „v pipeline od“ i „ve fázi od“.'
          + (closed.length > 0
            ? ` Uzavřený obchod raději přesuňte do fáze „${closed[0].label}“.`
            : ''),
        confirmLabel: state.pipelineBusy ? 'Odebírám…' : 'Odebrat',
        busy: state.pipelineBusy,
        onConfirm: () => { void onTogglePipeline(); },
        onCancel: () => onRemovePipelineCancel(),
      }));
    }
  }

  /* The save-to-collection control for the listing's property (rule #18) — the
   * SPA listing header's CollectionSaveToggle: "Uložit do kolekce" while the
   * property is in none of MY collections, "V kolekci" / "V kolekcích · N" once
   * it is (one shared with me is not "saved"; until the list tells them apart,
   * "Kolekce…"),
   * and a click opens the checklist of EVERY collection of mine below the row. It
   * replaced a one-click "Sledovat" bell that could only reach one hard-picked
   * collection; the monitored collections now lead that checklist, bell-marked,
   * exactly as in the app. A separate affordance from the pipeline funnel
   * (rule #22). Neutral while unsaved: copper is the pipeline's accent, and only
   * the SAVED state borrows the soft copper tint. */
  function renderCollectionToggle(container: HTMLElement, state: PanelView): void {
    const l = state.listing;
    if (l == null || !l.found || l.property_id == null) return;
    const saved = savedCollections(l.collection_ids, state.collections);
    const count = saved?.length ?? 0;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'collection-toggle' + (count > 0 ? ' collection-toggle--saved' : '');
    btn.dataset.key = 'coll-toggle';
    btn.setAttribute('aria-expanded', String(state.collectionsOpen));
    if (state.collectionsOpen) btn.setAttribute('aria-controls', COLLECTION_MENU_ID);
    /* No aria-label: the visible text IS the name (the SPA's label-in-name rule).
     * The verb and WHICH collections live in the title, so a hover answers
     * "in what?" without opening the checklist. */
    btn.title = count === 0
      ? COLLECTION_SAVE_LABEL
      : `${COLLECTION_SAVE_LABEL} — ${(saved ?? []).map((c) => c.name).join(', ')}`;
    btn.innerHTML = bookmarkIconSvg(count > 0);
    const label = document.createElement('span');
    /* Unknown is neither saved nor unsaved; the checklist it opens says why
     * (loading, or a failed read with a retry). */
    label.textContent = saved == null
      ? (state.collectionsFailed ? 'Kolekce' : 'Kolekce…')
      : count === 0
        ? COLLECTION_SAVE_LABEL
        : count === 1 ? 'V kolekci' : `V kolekcích · ${count}`;
    btn.appendChild(label);
    const caret = document.createElement('span');
    caret.className = 'collection-caret';
    caret.setAttribute('aria-hidden', 'true');
    caret.textContent = '▾';
    btn.appendChild(caret);
    btn.onclick = () => onToggleCollectionsMenu();
    btn.onkeydown = (e) => {
      if (e.key !== 'Escape' || !state.collectionsOpen) return;
      e.stopPropagation();  // ours, not the portal's (lightboxes close on Escape too)
      onCloseCollectionsMenu();
    };
    container.appendChild(btn);
  }

  /* The checklist the save control opens — the SPA's CollectionSaveMenu: every
   * collection of MINE as a checkable row (a view-only one shared with me is
   * never a save target), monitored ones first and bell-marked, one
   * click adds or removes the property. It stays open across clicks (a property
   * can sit in several collections) and closes from its trigger or Escape. It
   * opens IN the panel's flow rather than floating over it: the panel re-renders
   * wholesale on every state change and is pinned to the viewport's bottom
   * edge, so a card in the flow is the shape that survives both. */
  function renderCollectionMenu(body: HTMLElement, state: PanelView): void {
    const l = state.listing;
    if (!state.collectionsOpen || l == null || !l.found || l.property_id == null) return;
    const memberIds = new Set(l.collection_ids ?? []);

    const { section: menu, body: menuBody } = buildCard({ title: COLLECTION_SAVE_LABEL });
    menu.id = COLLECTION_MENU_ID;
    menu.setAttribute('role', 'group');
    menu.setAttribute('aria-label', COLLECTION_SAVE_LABEL);
    menu.onkeydown = (e) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      onCloseCollectionsMenu();
    };

    if (state.collections == null) {
      /* A failed read is NOT an empty one — "no collections yet" would tell an
       * operator whose collections exist that they have none. */
      const p = document.createElement('p');
      p.className = 'coll-state';
      if (state.collectionsFailed) {
        p.textContent = 'Kolekce se nepodařilo načíst. ';
        p.appendChild(retryLink(() => { store.load('collections', true); }));
      } else {
        p.textContent = 'Načítám kolekce…';
        p.classList.add('note--loading');
      }
      menuBody.appendChild(p);
    } else if (saveTargets(state.collections).length === 0) {
      const p = document.createElement('p');
      p.className = 'coll-state';
      p.textContent = 'Zatím nemáte žádnou kolekci.';
      menuBody.appendChild(p);
    } else {
      const list = document.createElement('ul');
      list.className = 'coll-list';
      for (const c of saveTargets(state.collections)) {
        const member = memberIds.has(c.id);
        const li = document.createElement('li');
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'coll-row' + (member ? ' coll-row--member' : '');
        /* aria-disabled, not `disabled`: a disabled button can't hold focus, so
         * the row being saved would drop keyboard focus to the page for the
         * length of the write. onToggleCollection ignores clicks while busy. */
        if (state.collectionBusy) row.setAttribute('aria-disabled', 'true');
        row.setAttribute('aria-pressed', String(member));
        const key = `coll-${c.id}` as const;
        row.dataset.key = key;

        const box = document.createElement('span');
        box.className = 'coll-check';
        box.setAttribute('aria-hidden', 'true');
        box.textContent = '✓';
        row.appendChild(box);

        const name = document.createElement('span');
        name.className = 'coll-name';
        name.textContent = c.name;
        name.title = c.name;
        row.appendChild(name);

        if (c.monitoring_enabled) {
          const bell = document.createElement('span');
          bell.className = 'coll-bell';
          bell.title = 'Sledovaná — upozorní na změny';
          bell.innerHTML = bellIconSvg();
          row.appendChild(bell);
        }

        row.onclick = () => { void onToggleCollection(c.id); };
        li.appendChild(row);
        list.appendChild(li);
      }
      menuBody.appendChild(list);
    }

    if (state.collectionError != null) {
      const err = document.createElement('p');
      err.className = 'coll-error';
      err.setAttribute('role', 'alert');
      err.textContent = state.collectionError;
      menuBody.appendChild(err);
    }

    /* Creating, renaming and monitoring settings stay in the app, as they do on
     * its listing page — the checklist only files the property. */
    if (COLLECTIONS_URL != null) {
      const manage = document.createElement('a');
      manage.className = 'coll-link coll-manage';
      manage.href = COLLECTIONS_URL;
      manage.target = '_blank';
      manage.rel = 'noopener';
      manage.textContent = state.collections != null && saveTargets(state.collections).length === 0
        ? 'Založit kolekci v aplikaci →'
        : 'Spravovat kolekce →';
      menuBody.appendChild(manage);
    }

    body.appendChild(menu);
  }

  /* The property's tags (rule #18): the SPA's TagChip per tag — the name in the
   * tag's colour — then a button opening the checklist of every tag. The panel
   * only assigns; creating, renaming and recolouring a tag stay in the app's
   * assign list, as collections are managed in the app. Absent when there is
   * no property yet, or from an API that predates `tag_ids`. */
  function renderTagRow(body: HTMLElement, state: PanelView): void {
    const l = state.listing;
    if (l == null || !l.found || l.property_id == null || l.tag_ids === undefined) return;
    const ids = l.tag_ids ?? [];
    const row = document.createElement('div');
    row.className = 'tag-row';
    for (const t of (state.tags ?? []).filter((tag) => ids.includes(tag.id))) {
      row.appendChild(tagChip(t));
    }

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tag-toggle';
    btn.dataset.key = 'tag-toggle';
    btn.setAttribute('aria-expanded', String(state.tagsOpen));
    if (state.tagsOpen) btn.setAttribute('aria-controls', TAG_MENU_ID);
    const label = document.createElement('span');
    label.textContent = ids.length === 0 ? 'Přidat štítek' : TAGS_LABEL;
    btn.appendChild(label);
    const caret = document.createElement('span');
    caret.className = 'collection-caret';
    caret.setAttribute('aria-hidden', 'true');
    caret.textContent = '▾';
    btn.appendChild(caret);
    btn.onclick = () => onToggleTagsMenu();
    btn.onkeydown = (e) => {
      if (e.key !== 'Escape' || !state.tagsOpen) return;
      e.stopPropagation();
      onCloseTagsMenu();
    };
    row.appendChild(btn);
    body.appendChild(row);
  }

  /* The checklist the tag button opens — the collection checklist's shape, each
   * row the tag's chip; one click attaches or detaches, and it stays open. */
  function renderTagMenu(body: HTMLElement, state: PanelView): void {
    const l = state.listing;
    if (!state.tagsOpen || l == null || !l.found || l.property_id == null) return;
    const ids = new Set(l.tag_ids ?? []);

    const { section: menu, body: menuBody } = buildCard({ title: TAGS_LABEL });
    menu.id = TAG_MENU_ID;
    menu.setAttribute('role', 'group');
    menu.setAttribute('aria-label', TAGS_LABEL);
    menu.onkeydown = (e) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      onCloseTagsMenu();
    };

    if (state.tags == null) {
      const p = document.createElement('p');
      p.className = 'coll-state';
      if (state.tagsFailed) {
        p.textContent = 'Štítky se nepodařilo načíst. ';
        p.appendChild(retryLink(() => { store.load('tags', true); }));
      } else {
        p.textContent = 'Načítám štítky…';
        p.classList.add('note--loading');
      }
      menuBody.appendChild(p);
    } else if (state.tags.length === 0) {
      const p = document.createElement('p');
      p.className = 'coll-state';
      p.textContent = 'Zatím nemáte žádný štítek.';
      menuBody.appendChild(p);
    } else {
      const list = document.createElement('ul');
      list.className = 'coll-list';
      for (const t of state.tags) {
        const member = ids.has(t.id);
        const li = document.createElement('li');
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'coll-row' + (member ? ' coll-row--member' : '');
        if (state.tagBusy) row.setAttribute('aria-disabled', 'true');
        row.setAttribute('aria-pressed', String(member));
        const key = `tag-${t.id}` as const;
        row.dataset.key = key;

        const box = document.createElement('span');
        box.className = 'coll-check';
        box.setAttribute('aria-hidden', 'true');
        box.textContent = '✓';
        row.appendChild(box);
        const chip = tagChip(t);
        chip.title = t.name;
        row.appendChild(chip);

        row.onclick = () => { void onToggleTag(t.id); };
        li.appendChild(row);
        list.appendChild(li);
      }
      menuBody.appendChild(list);
    }

    if (state.tagError != null) {
      const err = document.createElement('p');
      err.className = 'coll-error';
      err.setAttribute('role', 'alert');
      err.textContent = state.tagError;
      menuBody.appendChild(err);
    }

    // The app's listing page holds the assign list that also creates and edits tags.
    if (APP_BASE_URL) {
      const manage = document.createElement('a');
      manage.className = 'coll-link coll-manage';
      manage.href =
        `${APP_BASE_URL}/listing/${encodeURIComponent(l.source)}/${encodeURIComponent(l.source_id)}`;
      manage.target = '_blank';
      manage.rel = 'noopener';
      manage.textContent = 'Nový štítek v aplikaci →';
      menuBody.appendChild(manage);
    }

    body.appendChild(menu);
  }

  /* Dismiss control for the listing's property (migration 536) — the SPA's
   * DismissButton, one click either way (hiding destroys nothing). Absent when
   * there is no property, when the API predates the field, and while the
   * property is a LIVE deal; a deal closed into a terminal stage keeps it. */
  function renderDismissToggle(container: HTMLElement, state: PanelView): void {
    const l = state.listing;
    if (l == null || !l.found || l.property_id == null) return;
    if (l.dismissed == null || isLiveDeal(l.pipeline, state.stages)) return;
    const on = l.dismissed;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'dismiss-toggle' + (on ? ' dismiss-toggle--on' : '');
    btn.disabled = state.dismissBusy;
    btn.setAttribute('aria-pressed', String(on));
    btn.title = on ? 'Skryto — znovu zobrazit' : 'Skrýt nemovitost';
    btn.innerHTML = eyeOffIconSvg(on);
    const label = document.createElement('span');
    label.textContent = on ? 'Skryto' : 'Skrýt';
    btn.appendChild(label);
    btn.onclick = () => { void onToggleDismiss(); };
    container.appendChild(btn);
  }

  /* Operator notes for the listing's property (rule #18) — shown for ANY listing
   * we have a property for: the notes I can read (mine, plus another author's
   * SHARED ones, migration 610; most-recent-first) + an add box. A saved note
   * carries the viewed advert as origin provenance. */
  function renderNotes(body: HTMLElement, state: PanelView): void {
    const l = state.listing;
    if (l == null || !l.found || l.property_id == null) return;
    const audience = noteAudience(state);
    /* The count only once the notes are read: 0 is "none", not "unknown". */
    const card = buildCard({ title: 'Poznámky', count: state.notes?.length ?? null });
    const sec = card.body;

    /* Not loaded and failed are not "no notes" (MS16). */
    if (state.notesFailed) {
      const line = errorLine('Poznámky se nepodařilo načíst. ');
      line.appendChild(retryLink(() => { void loadNotes(); }));
      sec.appendChild(line);
    } else if (state.notes == null) {
      sec.appendChild(note('Načítám poznámky…', 'note--loading'));
    } else if (state.notes.length > 0) {
      const list = document.createElement('div');
      list.className = 'notes-list';
      for (const n of state.notes) {
        list.appendChild(
          !isMine(n)
            ? buildForeignNoteItem(n)
            : n.id === state.noteEditingId
              ? buildNoteEditItem(n, state)
              : buildNoteItem(n, state, audience),
        );
      }
      sec.appendChild(list);
    }

    const ta = document.createElement('textarea');
    ta.className = 'note-input';
    ta.rows = 2;
    ta.maxLength = 4000;  // mirror the server's CreateNoteIn cap + the SPA's textarea
    ta.placeholder = 'Přidat poznámku…';
    ta.value = noteDraft;
    ta.dataset.key = 'note';
    ta.disabled = state.noteBusy;
    ta.addEventListener('input', (e) => {
      noteDraft = (e.target as HTMLTextAreaElement).value;
    });
    sec.appendChild(ta);

    /* Off by default (consent, ruling 1), offered only when someone would read
     * it — and named, so the operator sees exactly who. */
    if (audience != null && audience.length > 0) {
      const share = document.createElement('label');
      share.className = 'note-share';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = noteShareDraft;
      box.disabled = state.noteBusy;
      box.dataset.key = 'note-share';
      box.addEventListener('change', () => { noteShareDraft = box.checked; });
      const who = document.createElement('span');
      who.textContent = `Sdílet s ${audience.join(', ')}`;
      share.append(box, who);
      sec.appendChild(share);
    } else if (audience == null && state.collectionsFailed) {
      // An unknown audience is not "nobody": say so and read again.
      const line = errorLine('Sdílení se nepodařilo načíst. ');
      line.appendChild(retryLink(() => { store.load('collections', true); }));
      sec.appendChild(line);
    }

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-primary note-save';
    btn.textContent = state.noteBusy ? 'Ukládám…' : 'Uložit poznámku';
    btn.disabled = state.noteBusy;
    btn.onclick = () => { void onAddNote(); };
    sec.appendChild(btn);

    body.appendChild(card.section);
  }

  /* Another author's SHARED note (they marked it; a share put it in reach):
   * read only — no edit or delete, a neutral left rule, and the author first on
   * its meta line (the SPA's ForeignNoteRow). */
  function buildForeignNoteItem(n: Note): HTMLElement {
    const item = document.createElement('div');
    item.className = 'note-item note-item--foreign';
    item.setAttribute('role', 'group');
    item.setAttribute('aria-label', `Poznámka od ${n.owner_email} · ${fmtNoteDate(n.created_at)}`);
    const bodyEl = document.createElement('p');
    bodyEl.className = 'note-body';
    bodyEl.textContent = n.body;
    item.appendChild(bodyEl);
    const meta = document.createElement('div');
    meta.className = 'note-meta';
    const date = noteDate(n);
    date.textContent = `od ${n.owner_email} · ${date.textContent}`;
    meta.appendChild(date);
    item.appendChild(meta);
    return item;
  }

  /* The note's date/edited marker; the exact stamps on hover. */
  function noteDate(n: Note): HTMLElement {
    const date = document.createElement('span');
    date.className = 'note-date';
    date.textContent = fmtNoteDate(n.created_at) + (n.updated_at != null ? ' (upraveno)' : '');
    date.title = n.updated_at != null
      ? `Napsáno ${n.created_at} · upraveno ${n.updated_at}`
      : n.created_at;
    return date;
  }

  /* One of MY notes in normal display mode: body + date/edited marker, its
   * shared mark and toggle, an edit/delete icon pair, and (when this note is the
   * one pending delete) a confirm row in place of the meta line — mirrors the
   * SPA NoteRow's two-step confirm. */
  function buildNoteItem(n: Note, state: PanelView, audience: string[] | null): HTMLElement {
    const item = document.createElement('div');
    item.className = 'note-item';

    const top = document.createElement('div');
    top.className = 'note-item-top';
    const bodyEl = document.createElement('p');
    bodyEl.className = 'note-body';
    bodyEl.textContent = n.body;
    top.appendChild(bodyEl);

    const actions = document.createElement('div');
    actions.className = 'note-actions';
    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'note-action-btn';
    editBtn.setAttribute('aria-label', 'Upravit poznámku');
    editBtn.title = 'Upravit poznámku';
    editBtn.innerHTML = PENCIL_ICON_SVG;
    editBtn.onclick = () => onEditNoteStart(n);
    actions.appendChild(editBtn);
    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'note-action-btn note-action-btn--danger';
    delBtn.setAttribute('aria-label', 'Smazat poznámku');
    delBtn.title = 'Smazat poznámku';
    delBtn.innerHTML = TRASH_ICON_SVG;
    delBtn.onclick = () => onDeleteNoteRequest(n.id);
    actions.appendChild(delBtn);
    top.appendChild(actions);
    item.appendChild(top);

    if (state.noteConfirmDeleteId === n.id) {
      const confirm = document.createElement('div');
      confirm.className = 'note-confirm-delete';
      const label = document.createElement('span');
      label.textContent = 'Smazat tuto poznámku?';
      confirm.appendChild(label);
      const yes = document.createElement('button');
      yes.type = 'button';
      yes.className = 'note-confirm-yes';
      yes.textContent = state.noteRowBusy ? 'Mažu…' : 'Smazat';
      yes.disabled = state.noteRowBusy;
      yes.onclick = () => { void onDeleteNoteConfirm(n.id); };
      confirm.appendChild(yes);
      const no = document.createElement('button');
      no.type = 'button';
      no.className = 'note-confirm-no';
      no.textContent = 'Zrušit';
      no.disabled = state.noteRowBusy;
      no.onclick = () => onDeleteNoteCancel();
      confirm.appendChild(no);
      item.appendChild(confirm);
    } else {
      const meta = document.createElement('div');
      meta.className = 'note-meta';
      meta.appendChild(noteDate(n));
      if (n.shared) {
        const mark = document.createElement('span');
        mark.className = 'note-shared';
        mark.textContent = audience != null && audience.length === 0
          ? 'Sdíleno · zatím to nikdo nevidí'
          : 'Sdíleno';
        meta.appendChild(mark);
      }
      /* Making private is always safe; sharing names who it reaches, and waits
       * until that is known. */
      if (n.shared || (audience != null && audience.length > 0)) {
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'note-share-toggle';
        toggle.textContent = n.shared ? 'Nesdílet' : `Sdílet s ${(audience ?? []).join(', ')}`;
        toggle.disabled = state.noteRowBusy;
        toggle.onclick = () => { void onToggleNoteShared(n); };
        meta.appendChild(toggle);
      }
      item.appendChild(meta);
    }
    return item;
  }

  /* A note in inline-edit mode: textarea (seeded from + writing to the module
   * global noteEditDraft, same non-rerendering-per-keystroke pattern as the
   * add-note box) + Save/Cancel. */
  function buildNoteEditItem(n: Note, state: PanelView): HTMLElement {
    const item = document.createElement('div');
    item.className = 'note-item note-item--editing';

    const ta = document.createElement('textarea');
    ta.className = 'note-input';
    ta.rows = 2;
    ta.maxLength = 4000;
    ta.value = noteEditDraft;
    ta.dataset.key = 'note-edit';
    ta.disabled = state.noteRowBusy;
    ta.addEventListener('input', (e) => {
      noteEditDraft = (e.target as HTMLTextAreaElement).value;
    });
    item.appendChild(ta);

    const row = document.createElement('div');
    row.className = 'note-edit-actions';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'note-confirm-no';
    cancel.textContent = 'Zrušit';
    cancel.disabled = state.noteRowBusy;
    cancel.onclick = () => onEditNoteCancel();
    row.appendChild(cancel);
    const save = document.createElement('button');
    save.type = 'button';
    save.className = 'btn-primary note-edit-save';
    save.textContent = state.noteRowBusy ? 'Ukládám…' : 'Uložit';
    save.disabled = state.noteRowBusy;
    save.onclick = () => { void onEditNoteSave(n.id); };
    row.appendChild(save);
    item.appendChild(row);

    return item;
  }

  /* The stamped valuation, one card: big copper yield figure → a hairline-ruled
   * ledger line carrying the MF reference rent (the signature of the panel).
   * Everything comes off the property's ONE result, by its shape: a value
   * (its yield + rent + the per-m² rate it was built from), the town's range
   * (both ends, with the result's note behind an (i)), a note alone, or
   * nothing. The yield figure appears only when the result has one (a sale
   * flat), and names the card; a rental flat shows its rent under the source's
   * name. No reason text is written here. */
  function renderMfBlock(body: HTMLElement, state: PanelView): void {
    const l = state.listing;
    if (l == null || !l.found) return;
    const shape = mfShape(l.mf_reference_rent);
    if (shape.kind === 'none') return;
    const pct = mfYieldText(l.mf_gross_yield_pct, l.mf_reference_rent);  // null for a note: it carries no yield
    const card = buildCard({ title: pct != null ? 'Výnos MF (hrubý)' : 'Cenová mapa MF' });
    card.title.title = pct != null
      ? 'Hrubý výnos dle cenové mapy nájemného MF (nájem ÷ cena)'
      : 'Cenová mapa nájemného MF';
    const mf = card.body;
    if (shape.kind === 'note') {
      mf.appendChild(note(shape.note));
      body.appendChild(card.section);
      return;
    }

    if (pct != null) {
      const figure = document.createElement('p');
      figure.className = 'mf-figure';
      figure.textContent = pct;
      mf.appendChild(figure);
    }

    const ledger = document.createElement('div');
    ledger.className = 'mf-ledger';
    const lab = document.createElement('span');
    lab.className = 'mf-ledger-label';
    lab.textContent = 'MF nájem';
    const val = document.createElement('span');
    val.className = 'mf-ledger-value';
    /* The per-m² rate is the result's own (a value's total, a range's two
     * totals), never a quotient taken here. */
    if (shape.kind === 'value') {
      val.textContent =
        `${fmtCzk(shape.ref.monthly_rent_czk)}/měs · ` +
        fmtMeasuredPricePerM2(shape.ref.total_per_m2, 'rent');
    } else {
      const r = shape.range;
      val.textContent =
        `${fmtCount(r.rent_min_czk)}–${fmtCzk(r.rent_max_czk)}/měs · ` +
        `${fmtCount(Math.round(r.per_m2_min))}–${fmtMeasuredPricePerM2(r.per_m2_max, 'rent')}`;
      if (shape.note != null) {
        const info = document.createElement('span');
        info.className = 'mf-info';
        info.setAttribute('role', 'img');
        info.title = shape.note;
        info.setAttribute('aria-label', shape.note);
        info.textContent = 'i';
        val.appendChild(info);
      }
    }
    ledger.appendChild(lab);
    ledger.appendChild(val);
    mf.appendChild(ledger);
    body.appendChild(card.section);
  }

  /* The catalog subject line: the disposition as the headword, area + price as
   * tabular metadata, the place on a quiet second line. Left-aligned, like an
   * archive entry — grounds the operator before the yield figure. */
  function renderSubjectFacts(body: HTMLElement, state: PanelView): void {
    const l = state.listing;
    if (l == null || !l.found) return;
    const subject = document.createElement('div');
    subject.className = 'subject';

    const line = document.createElement('div');
    line.className = 'subject-line';
    const kind = l.kind_label ?? l.disposition;
    if (kind) {
      const disp = document.createElement('span');
      disp.className = 'subject-disp';
      disp.textContent = kind;
      line.appendChild(disp);
    }
    const meta: string[] = [];
    /* The area carries its basis: `area_m2` is polymorphic, so "905 m²" alone
     * cannot tell the operator whether they are looking at a flat or a field.
     * An unstamped/unknown basis renders bare rather than guessing. */
    if (l.area_m2 != null) {
      const areaBasis = areaBasisLabel(l.area_basis);
      meta.push(fmtArea(l.area_m2) + (areaBasis != null ? ` (${areaBasis})` : ''));
    }
    if (l.price_czk != null) meta.push(fmtCzk(l.price_czk));
    if (meta.length > 0) {
      const m = document.createElement('span');
      m.className = 'subject-meta';
      m.textContent = meta.join(' · ');
      line.appendChild(m);
    }
    if (line.childElementCount > 0) subject.appendChild(line);

    const place = l.display_label;
    if (place) {
      const p = document.createElement('p');
      p.className = 'subject-place';
      p.textContent = place;
      subject.appendChild(p);
    }
    if (subject.childElementCount > 0) body.appendChild(subject);
  }

  function renderEstimation(body: HTMLElement, state: PanelView): void {
    const card = buildCard({ title: 'Odhad výnosu (komparativní)' });
    const sec = card.body;

    /* No estimation yet (or one is running). */
    if (state.run == null) {
      if (state.busy) {
        sec.appendChild(note('Odhad probíhá… (~1–3 min)', 'note--loading'));
      } else {
        const q = state.quota;
        const metered = q != null && q.metered;
        if (metered && q.remaining <= 0) {
          /* Monthly allowance spent — offer an upgrade instead of a run that
           * would 429. No purchase flow yet: the button just opens the web app. */
          sec.appendChild(note(`Měsíční limit vyčerpán (${q.used}/${q.quota}).`));
          const up = document.createElement('button');
          up.className = 'btn-primary';
          up.type = 'button';
          up.textContent = 'Upgradovat';
          up.title = 'Správa plánu ve webové aplikaci.';
          up.onclick = () => onUpgrade();
          sec.appendChild(up);
        } else {
          const btn = document.createElement('button');
          btn.className = 'btn-primary';
          btn.type = 'button';
          btn.textContent = metered
            ? `Spustit odhad (zbývá ${q.remaining})`
            : 'Spustit odhad';
          btn.onclick = () => onCreateRun();
          sec.appendChild(btn);
          if (metered && q.is_trial) {
            sec.appendChild(note(`Zkušební období: ${q.remaining} z ${q.quota} odhadů.`));
          }
        }
      }
      body.appendChild(card.section);
      return;
    }

    /* A failed run loaded/just-completed — show the reason + a retry. */
    if (state.run.status === 'failed') {
      sec.appendChild(errorLine(state.run.error_message ?? 'Odhad selhal.'));
      const btn = document.createElement('button');
      btn.className = 'btn-primary';
      btn.type = 'button';
      btn.textContent = state.busy ? 'Počítám…' : 'Spustit znovu';
      btn.disabled = state.busy;
      btn.onclick = () => onCreateRun();
      sec.appendChild(btn);
      body.appendChild(card.section);
      return;
    }

    /* Success — surface what kind of result it is. A 'rent' run with no rent
     * means the comparables search found nothing nearby (thin market): we fall
     * the calculator back to the MF rent and say so. */
    if (state.run.estimate_kind === 'rent' && state.run.estimated_monthly_rent_czk == null) {
      sec.appendChild(note(
        'Bez srovnatelných nájmů v okolí — počítáno z MF nájmu (uprav dle potřeby).',
      ));
    } else if (state.run.confidence === 'low') {
      sec.appendChild(note('Nízká spolehlivost odhadu (málo srovnatelných).'));
    }

    const fields = document.createElement('div');
    fields.className = 'fields';
    fields.appendChild(buildField({
      key: 'rent', label: 'Měsíční nájem', suffix: 'Kč',
      value: state.rent, onInput: (v) => onEdit('rent', v),
    }));
    fields.appendChild(buildField({
      // A MONTHLY rate. The period-less unit this replaces is the capital one —
      // it named a per-month charge as if it were a purchase price per m².
      key: 'cost', label: 'Fond oprav + SVJ', suffix: PPM2_UNIT.rent,
      value: state.costPerM2, onInput: (v) => onEdit('cost', v),
      hint: fondHint(state),
      /* An editable field that changes nothing is a lie — and a typed rate
       * would still reach `estimation_runs.scenario` for the SPA to apply. */
      disabled: !fondApplies(state),
    }));
    fields.appendChild(buildField({
      key: 'price', label: 'Cena', suffix: 'Kč',
      value: state.price, onInput: (v) => onEdit('price', v),
    }));
    fields.appendChild(buildField({
      key: 'renovation', label: 'Rekonstrukce', suffix: 'Kč',
      value: state.renovation, onInput: (v) => onEdit('renovation', v),
      hint: acquisitionHint(state),
    }));
    sec.appendChild(fields);

    const yieldRow = document.createElement('div');
    yieldRow.className = 'est-yield';
    const yl = document.createElement('span');
    yl.className = 'est-yield-label';
    yl.textContent = 'Výnos z odhadu';
    const yv = document.createElement('span');
    yv.className = 'est-yield-value';
    yv.textContent = fmtPct(computeYield(state));
    yieldRow.appendChild(yl);
    yieldRow.appendChild(yv);
    sec.appendChild(yieldRow);

    const foot = document.createElement('div');
    foot.className = 'est-foot';
    const status = document.createElement('span');
    status.className = 'est-status';
    const hasOverrides =
      state.rentTouched || state.costTouched || state.priceTouched
      || state.renovationTouched;
    status.textContent = hasOverrides ? 'upraveno · uloženo' : 'živý výpočet';
    foot.appendChild(status);
    /* Reset is always present (toggled), so onEdit can reveal it in place
     * without a full re-render — see onEdit / overridesChanged. */
    const reset = document.createElement('a');
    reset.className = 'est-reset';
    reset.textContent = 'Reset';
    reset.style.display = hasOverrides ? '' : 'none';
    reset.onclick = (e) => { e.preventDefault(); onReset(); };
    foot.appendChild(reset);
    sec.appendChild(foot);
    body.appendChild(card.section);
  }

  function buildField(opts: {
    key: 'rent' | 'cost' | 'price' | 'renovation';
    label: string;
    suffix: string;
    value: number | null;
    onInput: (v: number | null) => void;
    hint?: string;
    disabled?: boolean;
  }): HTMLElement {
    const wrap = document.createElement('div');
    wrap.className = 'field';
    const label = document.createElement('label');
    label.className = 'field-label';
    label.textContent = opts.label;
    wrap.appendChild(label);
    const row = document.createElement('div');
    row.className = 'field-row';
    const input = document.createElement('input');
    input.type = 'text';
    input.inputMode = 'decimal';
    input.className = 'field-input';
    input.value = formatNumber(opts.value);
    input.disabled = opts.disabled === true;
    input.dataset.key = opts.key;
    input.addEventListener('input', (e) => {
      opts.onInput(parseNumber((e.target as HTMLInputElement).value));
    });
    row.appendChild(input);
    const suf = document.createElement('span');
    suf.className = 'field-suffix';
    suf.textContent = opts.suffix;
    row.appendChild(suf);
    wrap.appendChild(row);
    if (opts.hint !== undefined) {
      const h = document.createElement('p');
      h.className = 'field-hint';
      h.dataset.hintFor = opts.key;  // onEdit refreshes it in place (no re-render)
      h.textContent = opts.hint;
      wrap.appendChild(h);
    }
    return wrap;
  }

  return { shadow, render, destroy: () => host.remove() };
}

// ----------------------------------------------------------------------
// App-level state machine
// ----------------------------------------------------------------------

let state: PanelState;
let render: (s: PanelState) => void;
let panelShadow: ShadowRoot | null = null;
/* The listing URL the panel currently represents — what "Přidat do databáze"
 * sends. On a detail page it's location.href; opened from an index card it's
 * that card's detail href (NOT the search page). */
let panelUrl = '';
/* The same listing's portal ref, kept alongside panelUrl so an account change
 * can re-run openPanel() without the caller re-supplying it. */
let panelRef: PortalRef | null = null;
let patchTimer: ReturnType<typeof setTimeout> | null = null;

/* Monotonic id of the panel currently on screen. Every openPanel() replaces the
 * module-global panel state wholesale, so anything resuming after an `await` has
 * to prove the panel it started for is still the one being shown — otherwise one
 * listing's figures paint into another listing's panel. That was survivable when
 * only an index-badge click could re-open mid-flight; since route_changed re-opens
 * the panel on every SPA soft-navigation (see the entry section), listing-to-
 * listing browsing is now the COMMON path through these awaits.
 *
 * Two layers, deliberately: the property-id guards below (setStateFor,
 * loadNotes, onAddNote) answer "is the panel still showing this property?" and
 * survive a re-open of the SAME listing; this epoch answers the stricter "is this
 * still the same panel instance?", which is what openPanel's own awaits and the
 * multi-minute estimation poll need. Curation needs neither: the store keys it
 * by property, and a render reads the property it shows. */
let renderEpoch = 0;

/* The property the panel shows, if it is one we hold. */
function shownProperty(): number | null {
  const l = (state as PanelState | undefined)?.listing;
  return l?.found ? l.property_id : null;
}

function view(s: PanelState): PanelView {
  const pid = shownProperty();
  const curation = pid != null ? store.curation(pid) : null;
  const collections = store.list('collections');
  const tags = store.list('tags');
  return {
    ...s,
    listing: s.listing != null && curation != null ? { ...s.listing, ...curation } : s.listing,
    stages: store.list('stages').data,
    collections: collections.data,
    collectionsFailed: collections.status === 'failed',
    tags: tags.data,
    tagsFailed: tags.status === 'failed',
    pipelineBusy: pid != null && store.busy('pipeline', pid),
    collectionBusy: pid != null && store.busy('collections', pid),
    tagBusy: pid != null && store.busy('tags', pid),
    dismissBusy: pid != null && store.busy('dismissed', pid),
  };
}

/* A list, or the shown property's curation, changed — here, on a card, anywhere. */
store.subscribe((changed) => {
  if (state == null) return;
  const pid = shownProperty();
  if (changed == null || (pid != null && changed.includes(pid))) render(state);
});

function setState(updater: (prev: PanelState) => PanelState): void {
  state = updater(state);
  render(state);
}

/* setState, but a no-op once the panel it was captured for has been replaced. */
function setStateIf(epoch: number, updater: (prev: PanelState) => PanelState): void {
  if (epoch === renderEpoch) setState(updater);
}

/* setState, but only while the panel still shows `propertyId`: a write can
 * outlive its panel. */
function setStateFor(propertyId: number, patch: (prev: PanelState) => Partial<PanelState>): void {
  setState((prev) => (prev.listing?.property_id === propertyId ? { ...prev, ...patch(prev) } : prev));
}

function schedulePatch(): void {
  if (patchTimer != null) clearTimeout(patchTimer);
  patchTimer = setTimeout(async () => {
    patchTimer = null;
    if (state.run == null) return;
    const res = await call<EstimationRun>({
      type: 'patch_scenario', run_id: state.run.id, body: bodyFromState(state),
    });
    if (!res.ok) {
      setState((prev) => ({ ...prev, errorMessage: `Uložení selhalo: ${friendlyDetail(res.detail)}` }));
      return;
    }
    /* Save succeeded — nothing visible depends on the refreshed run, so update
     * state silently. A full re-render here would destroy the <input> the
     * operator is typing in and steal focus (the bug we're avoiding). */
    state.run = res.data;
    if (state.errorMessage != null) {
      state.errorMessage = null;
      render(state);
    }
  }, PATCH_DEBOUNCE_MS);
}

/* Edits update state + the derived display IN PLACE — never a full re-render,
 * which would rebuild the inputs and drop focus mid-keystroke. */
function onEdit(
  axis: 'rent' | 'cost' | 'price' | 'renovation', value: number | null,
): void {
  switch (axis) {
    case 'rent': state.rent = value; state.rentTouched = true; break;
    case 'cost': state.costPerM2 = value; state.costTouched = true; break;
    case 'price': state.price = value; state.priceTouched = true; break;
    case 'renovation':
      state.renovation = value; state.renovationTouched = true; break;
  }
  const yv = panelShadow?.querySelector<HTMLElement>('.est-yield-value');
  if (yv != null) yv.textContent = fmtPct(computeYield(state));
  /* Refresh the derived field hints (fond/měs + acquisition denominator) in
   * place too, so the formula's parts track the inputs without a re-render. */
  const fh = panelShadow?.querySelector<HTMLElement>('[data-hint-for="cost"]');
  if (fh != null) fh.textContent = fondHint(state);
  const ah = panelShadow?.querySelector<HTMLElement>('[data-hint-for="renovation"]');
  if (ah != null) ah.textContent = acquisitionHint(state);
  const status = panelShadow?.querySelector<HTMLElement>('.est-status');
  if (status != null) status.textContent = 'upraveno · uloženo';
  const reset = panelShadow?.querySelector<HTMLElement>('.est-reset');
  if (reset != null) reset.style.display = '';
  schedulePatch();
}

/* WHICH estimate this subject needs — and, through the server's
 * `category_type`-follows-the-kind rule, which comparable cohort gets searched.
 *
 * The panel's yield wants the MISSING half of the pair: a subject being sold
 * needs a rent, a subject already let needs a sale price. Land is never
 * estimated as a rent — a per-m² of parcel is a capital figure, and the server's
 * measure guard refuses to call an area-scaled capital per-m² a monthly rent. */
function estimateKindFor(l: PortalListing): 'rent' | 'sale' {
  if (l.category_type === 'pronajem') return 'sale';
  if (l.category_main === 'pozemek') return 'sale';
  return 'rent';
}

function onReset(): void {
  setState((prev) => ({
    ...prev,
    rent: defaultRent(prev),
    costPerM2: defaultFond(prev),
    price: defaultPrice(prev),
    renovation: DEFAULT_RENOVATION_CZK,
    rentTouched: false, costTouched: false, priceTouched: false,
    renovationTouched: false,
  }));
  schedulePatch();
}

/* The poll runs up to POLL_MAX_ATTEMPTS × POLL_INTERVAL_MS (~6 min), far longer
 * than an operator stays on one listing — so every apply here is epoch-guarded.
 * The run itself still completes server-side and is picked up by the next open
 * of THAT listing (openPanel loads latest_estimation); only the display is
 * dropped, never the work. */
async function onCreateRun(): Promise<void> {
  const epoch = renderEpoch;
  const subject = state.listing;
  if (subject?.listing_id == null) return;
  setState((prev) => ({ ...prev, busy: true, errorMessage: null }));
  const res = await call<EstimationRun>({
    type: 'create_estimation',
    listing_id: subject.listing_id,
    estimate_kind: estimateKindFor(subject),
    category_main: subject.category_main,
  });
  if (!res.ok) {
    // 429 = the monthly allowance is spent (the atomic quota gate). Refresh the
    // quota so the button flips to the upgrade prompt, and say so plainly.
    if (res.status === 429) {
      void loadQuota();
      setStateIf(epoch, (prev) => ({
        ...prev, busy: false, errorMessage: 'Měsíční limit odhadů vyčerpán.',
      }));
      return;
    }
    setStateIf(epoch, (prev) => ({
      ...prev, busy: false, errorMessage: `Odhad se nepodařilo spustit: ${friendlyDetail(res.detail)}`,
    }));
    return;
  }
  let row = res.data;
  for (let i = 0; i < POLL_MAX_ATTEMPTS; i++) {
    if (row.status === 'success' || row.status === 'failed') break;
    await sleep(POLL_INTERVAL_MS);
    if (epoch !== renderEpoch) return;  // navigated away — stop burning polls
    const next = await call<EstimationRun>({ type: 'get_estimation', run_id: row.id });
    if (!next.ok) break;
    row = next.data;
  }
  // A metered agent run consumed a slot (success OR failed-but-created) — refresh
  // the counter so the next panel view is accurate.
  void loadQuota();
  if (row.status !== 'success') {
    setStateIf(epoch, (prev) => ({
      ...prev, busy: false,
      errorMessage: row.error_message ?? 'Odhad se nedokončil včas.',
    }));
    return;
  }
  setStateIf(epoch, (prev) => seedFromRun({ ...prev, busy: false }, row));
}

/* "Přidat do databáze": the API enqueues the ad's page for the worker's drain
 * and the flow polls its one state on the estimate's budget. Once the ad is held
 * with a property the lookup is re-read (by the identity the server resolved)
 * and painted through showListing, so the controls appear as for any ad we hold.
 * Epoch-guarded like the estimate: leaving the page stops the poll, never the
 * server-side work. The first admin-gate answer hides the button until the
 * account changes: a non-admin is told once, not offered a click that always fails. */
let ingestForbidden = false;

async function onIngest(): Promise<void> {
  const epoch = renderEpoch;
  const outcome = await runIngest(panelUrl, {
    call: (message) => call<IngestState>(message),
    sleep,
    alive: () => epoch === renderEpoch,
    onStep: (step) => setStateIf(epoch, (prev) => ({
      ...prev, ingestStep: step, ingestMessage: null,
    })),
    intervalMs: POLL_INTERVAL_MS,
    maxPolls: POLL_MAX_ATTEMPTS,
  });
  if (outcome.kind === 'cancelled') return;
  if (outcome.kind !== 'held') {
    if (isAdminGate(outcome)) ingestForbidden = true;
    setStateIf(epoch, (prev) => ({
      ...prev, ingestStep: null, ingestMessage: outcomeMessage(outcome, friendlyDetail),
    }));
    return;
  }
  const res = await store.lookup([{ source: outcome.source, source_id: outcome.nativeId }]);
  if (epoch !== renderEpoch) return;
  if (!res.ok) {
    setStateIf(epoch, (prev) => ({
      ...prev, ingestStep: null,
      ingestMessage: `Načtení inzerátu selhalo: ${friendlyDetail(res.detail)}`,
    }));
    return;
  }
  await showListing(epoch, res.data[0] ?? null);
}

/* "Upgradovat" — no purchase flow yet (Wave 1); open the web app where plan
 * management will live. No-op if the app URL isn't configured. */
function onUpgrade(): void {
  if (APP_BASE_URL) window.open(APP_BASE_URL, '_blank', 'noopener');
}

/* The panel's writes go through the store's one write path (store.ts:
 * optimistic, reconciled, put back once on failure — through the same
 * bearer-gated routes the SPA writes through). The panel keeps only its own
 * state: it clears its error line(s) once the store took the write (null: one
 * of its kind is in flight), and says a failure where it can be seen. */
async function panelWrite(
  write: (propertyId: number) => Pending, clear: Partial<PanelState>,
  failed: (prev: PanelState, detail: string, on: boolean) => Partial<PanelState>,
  settled: Partial<PanelState> = {},
): Promise<void> {
  const propertyId = state.listing?.property_id;
  if (propertyId == null) return;
  const pending = write(propertyId);
  if (pending == null) return;
  setStateFor(propertyId, () => clear);
  const res = await pending;
  setStateFor(propertyId, (prev) => ({
    ...settled, ...(res.ok ? {} : failed(prev, friendlyDetail(res.detail), res.on)),
  }));
}

/* Ask before removing (PanelState.pipelineConfirmRemove) — the ✕ opens the
 * confirm row, it does not write. Adding stays one click: it is reversible. */
function onRemovePipelineRequest(): void {
  setState((prev) => ({ ...prev, pipelineConfirmRemove: true, errorMessage: null }));
}

function onRemovePipelineCancel(): void {
  setState((prev) => ({ ...prev, pipelineConfirmRemove: false }));
}

/* Bookmark / un-bookmark the listing's property into the deal pipeline. */
function onTogglePipeline(): Promise<void> {
  return panelWrite(
    (id) => (store.curation(id)?.pipeline?.in_pipeline ? store.removeFromPipeline(id) : store.addToPipeline(id)),
    { errorMessage: null },
    (_, detail) => ({ errorMessage: `Uložení do pipeline selhalo: ${detail}` }),
    { pipelineConfirmRemove: false },
  );
}

/* Change the deal stage: the SAME audited PATCH the SPA's PipelineToggle and
 * the kanban use (stamps `entered_stage_at`, logs a `moved` event). Moving the
 * card answers the "remove?" question a different way — close the confirm
 * rather than leave it hanging over a card that just moved. */
function onMoveStage(stageId: number): Promise<void> {
  return panelWrite(
    (id) => store.moveStage(id, stageId), { pipelineConfirmRemove: false, errorMessage: null },
    (_, detail) => ({ errorMessage: `Změna fáze selhala: ${detail}` }),
  );
}

/* Hide / restore the listing's property (the SAME /dismissals routes as the SPA). */
function onToggleDismiss(): Promise<void> {
  return panelWrite(
    (id) => store.toggleDismiss(id), { errorMessage: null },
    (_, detail) => ({ errorMessage: `Skrytí se nepodařilo uložit: ${detail}` }),
  );
}

/* Add the listing's property to one collection of mine, or take it out (rule
 * #18), through the SAME /collections/{id}/properties routes the SPA uses. A
 * failure shows in the checklist when it is open, else on the panel's own error
 * line — it may have been closed mid-write, and an error inside a closed
 * checklist is a revert nobody is told about. */
function onToggleCollection(collectionId: number): Promise<void> {
  return panelWrite((id) => store.toggleCollection(id, collectionId), { collectionError: null },
    (prev, detail, add) => {
      const message = `${add ? 'Uložení do kolekce' : 'Odebrání z kolekce'} se nepodařilo: ${detail}`;
      return prev.collectionsOpen ? { collectionError: message } : { errorMessage: message };
    });
}

/* Attach one tag to the listing's property, or detach it (rule #18), through
 * the SAME /properties/{id}/tags routes the SPA's TagAssignList uses. */
function onToggleTag(tagId: number): Promise<void> {
  return panelWrite((id) => store.toggleTag(id, tagId), { tagError: null },
    (prev, detail, attach) => {
      const message = `${attach ? 'Přidání štítku' : 'Odebrání štítku'} se nepodařilo: ${detail}`;
      return prev.tagsOpen ? { tagError: message } : { errorMessage: message };
    });
}

/* The SPA's TagChip: the tag's name in its colour on its soft wash. The tag
 * palette IS the stage palette (TagColor), so its accent resolves alike. */
function tagChip(tag: Tag): HTMLElement {
  const chip = document.createElement('span');
  chip.className = 'tag-chip';
  const accent = stageAccent(tag);
  chip.style.color = accent.fg;
  chip.style.borderColor = accent.fg;
  chip.style.background = accent.soft;
  chip.textContent = tag.name;
  return chip;
}

function onToggleTagsMenu(): void {
  const opening = !state.tagsOpen;
  setState((prev) => ({
    ...prev, tagsOpen: opening, tagError: null,
    ...(opening ? { collectionsOpen: false, collectionError: null } : {}),
  }));
  if (opening) store.load('tags', true);
}

function onCloseTagsMenu(): void {
  setState((prev) => ({ ...prev, tagsOpen: false, tagError: null }));
  panelShadow?.querySelector<HTMLElement>('.tag-toggle')?.focus();
}

/* Open / close the save-to-collection checklist. Opening also re-reads the
 * collection list in the background: the list is cached for the life of the
 * portal tab, so a collection created in the app since then would otherwise
 * be missing until a reload. The cached rows show meanwhile. */
function onToggleCollectionsMenu(): void {
  const opening = !state.collectionsOpen;
  setState((prev) => ({
    ...prev, collectionsOpen: opening, collectionError: null,
    ...(opening ? { tagsOpen: false, tagError: null } : {}),
  }));
  if (opening) store.load('collections', true);
}

/* Escape: close, and hand focus back to the trigger — the row that had it was
 * just removed with the checklist. */
function onCloseCollectionsMenu(): void {
  setState((prev) => ({ ...prev, collectionsOpen: false, collectionError: null }));
  panelShadow?.querySelector<HTMLElement>('.collection-toggle')?.focus();
}

/* The panel belongs to the account that read it: once the store dropped what
 * that account had read, an account change puts the panel on the prompt or
 * re-reads it. */
store.onAccountChange((user) => {
  ingestForbidden = false;
  if (panelRef == null || document.getElementById(HOST_ELEMENT_ID) == null) return;
  if (user != null) return void openPanel(panelRef, panelUrl);
  renderEpoch++;
  setState((prev) => ({
    ...prev, phase: 'signed_out', authEmail: null, listing: null, errorMessage: null,
    collectionsOpen: false, tagsOpen: false,
  }));
});

/* Fetch the caller's agent-estimation allowance for the "(zbývá X)" counter +
 * upgrade prompt. Best-effort: a failure leaves state.quota null (counter hidden,
 * button still works). Fetched per panel open so a run that just consumed a slot
 * is reflected on the next open. */
async function loadQuota(): Promise<void> {
  const res = await call<BillingMe>({ type: 'get_billing' });
  if (res.ok) {
    setState((prev) => ({ ...prev, quota: res.data.agent_estimations }));
  }
}

/* The note add-box draft. A module global (not panel state) so editing it never
 * triggers a re-render — the textarea reads it on render, writes it on input;
 * reset per panel open so one listing's draft never bleeds onto another. */
let noteDraft = '';

/* Same pattern as noteDraft, for whichever note is currently open for inline
 * edit (state.noteEditingId names WHICH note; this module global holds ITS
 * draft text so typing doesn't re-render). Reset per panel open + whenever
 * edit mode opens/closes so it never bleeds onto a different note or listing. */
let noteEditDraft = '';

/* The composer's "Sdílet s …" checkbox — noteDraft's twin: off by default,
 * reset with the draft. */
let noteShareDraft = false;

/* Who a SHARED note on the panel's property would reach — the app's
 * shareAudience over the same facts (the lookup's collection_ids × the
 * collection list). Null = not known yet (the list loading or failed), which
 * must never read as "nobody": no checkbox, no share toggle, no "nobody" claim. */
function noteAudience(s: PanelView): string[] | null {
  const l = s.listing;
  if (l == null) return null;
  if ((l.collection_ids ?? []).length === 0) return [];
  if (s.collections == null) return null;
  return shareAudience(l.collection_ids ?? [], new Map(s.collections.map((c) => [c.id, c])));
}

/* Notes are PER-PROPERTY (not page-wide like the store's lists), so they're fetched
 * fresh per panel open and held in panel state — never cached. The dedup
 * is the per-panel `state.notes == null` guard, NOT a page-wide in-flight flag: a
 * page-wide flag would early-return (and strand) a DIFFERENT property opened while
 * the first's fetch is still in flight (a list survives that because it is one
 * page-wide value; notes are one per property). A duplicate
 * fetch from a rapid same-property re-open is harmless (idempotent, identity-
 * guarded apply). Retried on a transient blip; identity-guarded so a mid-flight
 * panel re-open can't apply one property's notes onto another. When every try
 * fails the panel says so (`notesFailed`) and its retry calls this again. */
async function loadNotes(): Promise<void> {
  const l = state.listing;
  if (l == null || l.property_id == null || state.notes != null) return;
  const propertyId = l.property_id;
  const mine = (prev: PanelState): boolean => prev.listing?.property_id === propertyId;
  if (state.notesFailed) setState((prev) => ({ ...prev, notesFailed: false }));
  const res = await store.retry(() => call<Note[]>({ type: 'list_notes', property_id: propertyId }));
  if (res == null) return;
  setState((prev) => (!mine(prev) ? prev : res.ok ? { ...prev, notes: res.data } : { ...prev, notesFailed: true }));
}

/* Save the draft as a note on the listing's property. `origin_listing_ref_id` is
 * the viewed advert's surrogate id (display provenance, rule #18). The new note is
 * prepended (the list is most-recent-first); the draft clears. Identity-guarded
 * like the other writes; reuses the SAME POST the SPA's CurationBlock uses. */
async function onAddNote(): Promise<void> {
  const l = state.listing;
  if (l == null || l.property_id == null) return;
  const body = noteDraft.trim();
  if (body === '') return;
  const propertyId = l.property_id;
  // Shared only while the checkbox is on screen, i.e. someone would read it.
  const shared = noteShareDraft && (noteAudience(view(state))?.length ?? 0) > 0;
  setState((prev) => ({ ...prev, noteBusy: true, errorMessage: null }));
  const res = await call<Note>({
    type: 'add_note', property_id: propertyId, body, shared,
    origin_listing_ref_id: l.listing_id ?? null,
  });
  if (!res.ok) {
    setState((prev) => ({
      ...prev, noteBusy: false,
      errorMessage: `Uložení poznámky selhalo: ${friendlyDetail(res.detail)}`,
    }));
    return;
  }
  // Clear the draft only if the panel still shows this property — identity-guarded
  // like the prepend, so a mid-request re-open can't wipe another listing's draft.
  if (state.listing?.property_id === propertyId) {
    noteDraft = '';
    noteShareDraft = false;
  }
  setState((prev) =>
    prev.listing?.property_id === propertyId && prev.notes != null
      ? { ...prev, noteBusy: false, notes: [res.data, ...prev.notes] }
      : { ...prev, noteBusy: false });
  /* A list never read is not a list of one: read the whole one, new note included. */
  if (state.notes == null && state.listing?.property_id === propertyId) void loadNotes();
}

/* Open a note for inline edit — seeds the edit draft with its current body and
 * closes any delete-confirm row (a note can't be mid-edit and mid-confirm). */
function onEditNoteStart(note: Note): void {
  noteEditDraft = note.body;
  setState((prev) => ({ ...prev, noteEditingId: note.id, noteConfirmDeleteId: null }));
}

function onEditNoteCancel(): void {
  setState((prev) => ({ ...prev, noteEditingId: null }));
}

/* Save the edit draft as the note's new body via PATCH. Identity-guarded like
 * the other note writes; leaves edit mode on success, keeps it open (with the
 * server error surfaced) on failure so the operator doesn't lose the draft. */
async function onEditNoteSave(noteId: number): Promise<void> {
  const l = state.listing;
  if (l == null || l.property_id == null) return;
  const body = noteEditDraft.trim();
  if (body === '') return;
  const propertyId = l.property_id;
  setState((prev) => ({ ...prev, noteRowBusy: true, errorMessage: null }));
  const res = await call<Note>({
    type: 'update_note', property_id: propertyId, note_id: noteId, patch: { body },
  });
  if (!res.ok) {
    setState((prev) => ({
      ...prev, noteRowBusy: false,
      errorMessage: `Úprava poznámky selhala: ${friendlyDetail(res.detail)}`,
    }));
    return;
  }
  setState((prev) =>
    prev.listing?.property_id === propertyId
      ? {
          ...prev, noteRowBusy: false, noteEditingId: null,
          notes: (prev.notes ?? []).map((n) => (n.id === noteId ? res.data : n)),
        }
      : { ...prev, noteRowBusy: false });
}

/* Mark one of my notes shared, or private again (PATCH {shared}; not an edit,
 * so `updated_at` stays). Identity-guarded like the other note writes. */
async function onToggleNoteShared(note: Note): Promise<void> {
  const l = state.listing;
  if (l == null || l.property_id == null || state.noteRowBusy) return;
  const propertyId = l.property_id;
  setState((prev) => ({ ...prev, noteRowBusy: true, errorMessage: null }));
  const res = await call<Note>({
    type: 'update_note', property_id: propertyId, note_id: note.id, patch: { shared: !note.shared },
  });
  if (!res.ok) {
    setState((prev) => ({
      ...prev, noteRowBusy: false,
      errorMessage: `Sdílení poznámky se nepodařilo změnit: ${friendlyDetail(res.detail)}`,
    }));
    return;
  }
  setState((prev) =>
    prev.listing?.property_id === propertyId
      ? {
          ...prev, noteRowBusy: false,
          notes: (prev.notes ?? []).map((n) => (n.id === note.id ? res.data : n)),
        }
      : { ...prev, noteRowBusy: false });
}

/* Show the "Delete this note?" confirm row — closes any open edit (mirrors
 * onEditNoteStart's mutual exclusion the other way). */
function onDeleteNoteRequest(noteId: number): void {
  setState((prev) => ({ ...prev, noteConfirmDeleteId: noteId, noteEditingId: null }));
}

function onDeleteNoteCancel(): void {
  setState((prev) => ({ ...prev, noteConfirmDeleteId: null }));
}

async function onDeleteNoteConfirm(noteId: number): Promise<void> {
  const l = state.listing;
  if (l == null || l.property_id == null) return;
  const propertyId = l.property_id;
  setState((prev) => ({ ...prev, noteRowBusy: true, errorMessage: null }));
  const res = await call<{ deleted: true }>({ type: 'delete_note', property_id: propertyId, note_id: noteId });
  if (!res.ok) {
    setState((prev) => ({
      ...prev, noteRowBusy: false,
      errorMessage: `Smazání poznámky selhalo: ${friendlyDetail(res.detail)}`,
    }));
    return;
  }
  setState((prev) =>
    prev.listing?.property_id === propertyId
      ? {
          ...prev, noteRowBusy: false, noteConfirmDeleteId: null,
          notes: (prev.notes ?? []).filter((n) => n.id !== noteId),
        }
      : { ...prev, noteRowBusy: false });
}

/* Trigger the extension's own PKCE sign-in (Wave 1) — the background worker
 * owns chrome.identity (a content script can't call it directly), so this
 * just relays a message; the session landing is an account change, which
 * re-opens the panel (onAccountChange). */
async function onSignIn(): Promise<void> {
  setState((prev) => ({ ...prev, busy: true, errorMessage: null }));
  const res = await call<undefined>({ type: 'sign_in' });
  if (!res.ok) {
    setState((prev) => ({
      ...prev, busy: false, phase: 'signed_out',
      errorMessage: `Přihlášení selhalo: ${res.detail}`,
    }));
  }
}

/* The session going is an account change, which puts the panel on the prompt. */
async function onSignOut(): Promise<void> {
  const res = await call<undefined>({ type: 'sign_out' });
  if (!res.ok) setState((prev) => ({ ...prev, errorMessage: friendlyDetail(res.detail) }));
}

/* Mounts/refreshes the floating panel for one listing. Used by the detail-page
 * entry AND by index-card badges (which pass the card's ref + href + the
 * already-fetched listing so no second lookup is needed). */
export async function openPanel(
  ref: PortalRef, url: string, prefetched?: PortalListing | null,
): Promise<void> {
  const epoch = ++renderEpoch;
  panelUrl = url;
  panelRef = ref;
  const panel = mountPanel();
  render = panel.render;
  panelShadow = panel.shadow;
  noteDraft = '';  // a fresh panel starts with an empty note draft
  noteShareDraft = false;
  noteEditDraft = '';
  state = {
    phase: 'loading', authEmail: null, listing: null, isSaleApt: null, run: null,
    rentTouched: false, costTouched: false, priceTouched: false,
    renovationTouched: false,
    rent: null, costPerM2: null, price: null, renovation: null, busy: false,
    pipelineConfirmRemove: false, collectionsOpen: false, collectionError: null,
    tagsOpen: false, tagError: null,
    notes: null, notesFailed: false, noteBusy: false, quota: null, errorMessage: null,
    noteEditingId: null, noteConfirmDeleteId: null, noteRowBusy: false,
    ingestStep: null, ingestMessage: null,
  };
  await minimizedReady;  // persisted minimized pref before first paint → no flash
  if (epoch !== renderEpoch) return;
  render(state);

  /* Every route needs a real session now (Wave 1) — check first so a
   * signed-out operator sees one clean prompt instead of a lookup 401. */
  const auth = await call<AuthState>({ type: 'get_auth_state' });
  if (!auth.ok) {
    /* The background is unreachable (an extension reload orphaned this tab):
     * not a sign-in problem, so say what happened instead of prompting for
     * one — the same state the search page's notice reports. */
    setStateIf(epoch, (prev) => ({ ...prev, phase: 'error', errorMessage: auth.detail }));
    return;
  }
  if (!auth.data.signedIn) {
    setStateIf(epoch, (prev) => ({ ...prev, phase: 'signed_out', authEmail: null }));
    return;
  }
  setStateIf(epoch, (prev) => ({ ...prev, authEmail: auth.data.email }));

  let listing: PortalListing | null;
  if (prefetched !== undefined) {
    listing = prefetched;
  } else {
    const res = await store.lookup([{ source: ref.source, source_id: ref.sourceId }]);
    if (!res.ok) {
      if (res.detail === NOT_SIGNED_IN_DETAIL) {
        setStateIf(epoch, (prev) => ({ ...prev, phase: 'signed_out', authEmail: null }));
        return;
      }
      setStateIf(epoch, (prev) => ({ ...prev, phase: 'error', errorMessage: res.detail }));
      return;
    }
    listing = res.data[0] ?? null;
  }
  /* The lookup for a listing the operator has already navigated away from must
   * not land — this is the apply that would otherwise show A's yield under B. */
  if (epoch !== renderEpoch) return;
  await showListing(epoch, listing);
}

/* Paint one lookup row and start what a held ad needs. Shared by openPanel and
 * the add-to-database flow (onIngest), so an ad the panel just added gets
 * exactly the controls of any ad we hold. Its curation is the store's: every
 * lookup (the card's that handed a row over too) seeded it as it answered. */
async function showListing(epoch: number, listing: PortalListing | null): Promise<void> {
  /* The panel is for an ad we hold (app link, facts, MF, estimate); an ad we
   * lack gets only the offer to add it — an estimation is always of a held ad. */
  const held = listing?.found ? listing : null;
  const saleApt = held != null
    ? held.category_main === 'byt' && held.category_type === 'prodej'
    : null;
  setStateIf(epoch, (prev) => ({
    ...prev, phase: held != null ? 'active' : 'deactivated',
    listing, isSaleApt: saleApt, ingestStep: null, ingestMessage: null,
  }));
  if (held == null) return;

  /* Agent estimates are metered — fetch the remaining allowance so the estimate
   * button can show "(zbývá X)" and flip to an upgrade prompt at zero. Only the
   * estimate block (sale apartments) uses it; non-blocking, and a failure just
   * leaves the counter hidden. */
  if (saleApt) void loadQuota();

  /* For a property we have, load the stage list so the in-pipeline control can
   * offer stage changes, and the collection list so the save control can name
   * the property's collections (both non-blocking; cached across panel opens).
   * The collection list is RE-READ on every open (the cached one paints first,
   * an unchanged read redraws nothing): it decides what is saved and who a
   * shared note reaches, and a share or collection made in the app since the
   * tab loaded must not leave either wrong — the SPA re-reads after 30 s. */
  if (held.property_id != null) {
    store.load('stages');
    store.load('collections', true);
    store.load('tags');
    void loadNotes();  // the property's existing notes (per-property, lazy)
  }

  /* Lazily load an existing estimation so the editable yield block appears
   * without blocking the MF headline (only the estimation section uses it). */
  const est = saleApt ? held.latest_estimation : null;
  if (est != null) {
    const full = await call<EstimationRun>({
      type: 'get_estimation', run_id: est.estimation_id,
    });
    if (full.ok && full.data.status === 'success') {
      setStateIf(epoch, (prev) => seedFromRun(prev, full.data));
    }
  }
}

// ----------------------------------------------------------------------
// Entry: detail pages get the panel; other pages on a known portal host
// get the index-card overlay (a no-op if there are no listing cards).
//
// Several portals are SPAs (sreality's Next.js app confirmed live) that
// navigate via the History API — no new document, so the manifest-injected
// content script only ever runs once per real page load. `renderForUrl` is
// re-entrant so the background worker's route_changed relay (webNavigation,
// see background.ts) can re-run it on every soft-navigation, not just on the
// document this script was first injected into.
// ----------------------------------------------------------------------

/* What the current URL means to US — the LISTING it shows, not its query string.
 * SPAs rewrite the URL for things that don't change the subject (photo-gallery
 * lightbox, tracking params, scroll bookkeeping); keying on the raw href would
 * tear the panel down and re-fetch on every one of those, discarding the
 * operator's half-typed note draft and calculator edits. Index pages collapse to
 * one key per host: paginating doesn't restart the overlay, whose MutationObserver
 * already picks up the new cards. */
function routeKey(url: string): string {
  const ref = detailRef(url);
  return ref != null ? `detail:${ref.source}:${ref.sourceId}` : 'index';
}

let lastRouteKey: string | null = null;
let indexOverlay: IndexOverlayHandle | null = null;

function renderForUrl(url: string): void {
  const key = routeKey(url);
  if (key === lastRouteKey) return;
  lastRouteKey = key;
  /* Leaving a page invalidates whatever the old one still had in flight, even
   * when the new page opens no panel of its own (detail → index). */
  renderEpoch++;

  if (indexOverlay != null) {
    indexOverlay.stop();
    indexOverlay = null;
  }

  const ref = detailRef(url);
  if (ref != null) {
    openPanel(ref, url).catch((err: unknown) => {
      console.error('[mf-ext] detail boot failed', err);
    });
    return;
  }

  document.getElementById(HOST_ELEMENT_ID)?.remove();
  if (portalForHost(window.location.hostname) != null) {
    runIndexOverlay(store, openPanel)
      .then((overlay) => {
        /* Another route change landed while the overlay was starting — adopt
         * nothing, or its observer outlives the page it was scanning. Keyed on
         * the route, not renderEpoch, which an index-badge click also bumps. */
        if (lastRouteKey !== key) overlay.stop();
        else indexOverlay = overlay;
      })
      .catch((err: unknown) => {
        console.error('[mf-ext] index overlay failed', err);
      });
  }
}

chrome.runtime.onMessage.addListener((message: RouteMessage) => {
  if (message.type === 'route_changed') renderForUrl(message.url);
});

// The profile's scheme and theme on every themed shadow host (appearance.ts).
followAppearance(async () => {
  const auth = await call<AuthState>({ type: 'get_auth_state' });
  return auth.ok ? auth.data.appearance : null;
});
renderForUrl(window.location.href);
