/* The controls the extension draws ON a search-page card — the Browse card's
 * own cluster, in the same order: the pipeline funnel, the save-to-collection
 * bookmark and the hide eye, then the yield badge (sale apartments only).
 *
 * Every listing we have a property for gets the three controls, whatever its
 * category; they mean exactly what they mean in the app and write through the
 * same routes:
 *   funnel   — out of the pipeline: one click adds at the entry stage. In it:
 *              a click opens the stage menu (move, or remove behind a two-step
 *              confirm). Never a remove toggle (rule #22).
 *   bookmark — opens the checklist of every collection of MINE (rule #18; a
 *              shared one is never a save target); a click on a row adds or
 *              removes the property.
 *   eye      — hides the property / restores it, one click either way
 *              (migration 536). Absent while the property is a LIVE deal.
 *
 * Operator state is PROPERTY-grain and lives in the one store (store.ts): a
 * write repaints every card of that property on the page, and the panel when it
 * shows the same property — both draw from the store, neither syncs the other.
 *
 * A card in the pipeline, a collection or tagged also gets that drawn over its
 * photo (index_photo_mark.ts): the photo toned in the stage colour under the
 * stage badge; a copper rim and a label naming the collection; the tag chips.
 *
 * The cluster lives in a closed shadow root per card, so the portal's CSS
 * can't reach it; the two menus share one page-level shadow host, positioned
 * against the button that opened them (a card's own overflow would clip a
 * menu drawn inside it). The only things written into the portal's DOM are
 * the two host elements (the cluster's, the photo mark's) and two attributes
 * on the card. */

import tokens from '../../frontend/src/styles/tokens.css?inline';
import { themeHost } from './appearance';
import { COLLECTIONS_URL, friendlyDetail } from './config';
import { h, keepFocus } from './dom';
import {
  bellIconSvg,
  bookmarkIconSvg,
  eyeOffIconSvg,
  funnelIconSvg,
  knowsAll,
  savedCollections,
  saveTargets,
  stageLook,
} from './glyphs';
import { COLLECTION_SAVE_LABEL } from '../../frontend/src/lib/collectionRules';
import { stageAccent, stageBadge } from '../../frontend/src/lib/pipelineStage';
import { PHOTO_MARK_HOST_CLASS, createPhotoMark } from './index_photo_mark';
import { isLiveDeal, type Curation, type ListKind, type Pending, type Store } from './store';
import { detailRef, type PortalRef } from '../../frontend/src/lib/portalIds';
import type { PortalListing } from './types';

/* Holds the listing id the card was mounted FOR, not a boolean: SPA routers
 * recycle card DOM nodes between result sets, so a node can still carry the
 * controls of the listing it previously held. Storing the id lets a recycled
 * node be detected and re-mounted (or, while its lookup is failing, cleared)
 * instead of silently showing — and writing to — another listing's property. */
export const PROCESSED_ATTR = 'data-mf-processed';
/* On <html>: "1" while dismissed properties are drawn barely visible. */
export const VEIL_ROOT_ATTR = 'data-mf-veil';
/* On a card whose property the operator dismissed. */
const DISMISSED_ATTR = 'data-mf-dismissed';
const HOST_CLASS = '__mf_card_host';
const MENU_HOST_ID = '__mf_menu_host__';
const PAGE_STYLE_ID = '__mf_card_style__';
const STAGE_MENU_LABEL = 'Fáze v pipeline';

export interface BadgeView {
  kind: 'yield' | 'est' | 'cta';
  text: string;
  title: string;
}

export interface CardLayerDeps {
  /* Curation, the lists and the writes (store.ts). */
  store: Store;
  /* The overlay's lookup results by native id: what each card's listing IS
   * (facts, badge). Its curation fields are the lookup's; the store's are live. */
  cache: Map<string, PortalListing>;
  /* The yield badge for this card, or null when it gets none. */
  badgeFor: (listing: PortalListing) => BadgeView | null;
  openPanel: (ref: PortalRef, href: string, listing: PortalListing) => void;
  /* Whether dismissed properties are drawn barely visible right now. */
  veilOn: () => boolean;
  /* Say why a write failed (the dock's line). */
  flash: (message: string) => void;
  /* Cards were repainted — the dismissed count may have moved. */
  onRefresh: () => void;
}

export interface CardLayer {
  /* Draw the controls for the listing this card links (idempotent per id). */
  mount: (card: HTMLElement, ref: PortalRef, href: string) => void;
  clear: (card: HTMLElement) => void;
  /* Repaint every card and the open menu from the store. */
  refresh: () => void;
  dismissedCount: () => number;
  /* Drop every control: the overlay stopped, or the session changed (the store
   * dropped what the account that left had read). */
  reset: () => void;
}

type MenuKind = 'stages' | 'collections';

interface Menu {
  kind: MenuKind;
  propertyId: number;
  anchor: HTMLButtonElement;
  /* The card host the anchor lives in — what a pointer event on it targets. */
  anchorHost: HTMLElement;
  /* The stage menu is showing its "Odebrat z pipeline?" confirm. */
  confirming: boolean;
  /* The last collection write's failure, shown beside the rows. */
  error: string | null;
}

interface MenuDom {
  host: HTMLElement;
  shadow: ShadowRoot;
  box: HTMLElement;
}

interface CardHandle {
  sourceId: string;
  paint: () => void;
  destroy: () => void;
}

/* In the portal's DOM, so it only ever names our own attributes. Dimming the
 * card's CHILDREN rather than the card keeps our own controls legible on it —
 * the eye is how the operator takes the dismissal back — and needs no guess at
 * the page's background colour. Hovering the card lifts it enough to see what
 * it is before restoring it. */
const PAGE_CSS = `
  html[${VEIL_ROOT_ATTR}="1"] [${DISMISSED_ATTR}] > :not(.${HOST_CLASS}) {
    opacity: 0.1 !important; filter: grayscale(1) !important;
    transition: opacity 140ms ease !important;
  }
  html[${VEIL_ROOT_ATTR}="1"] [${DISMISSED_ATTR}]:hover > :not(.${HOST_CLASS}) {
    opacity: 0.5 !important;
  }
`;

/* `!important` on the host's own box: inside a shadow tree it is what lets
 * :host beat a portal rule that happens to match our element (".card > div").
 *
 * z-index 900, not the maximum: a card rarely makes a stacking context of its
 * own, so this competes with the whole page. It has to clear what a card
 * stacks inside itself (stretched links, carousel arrows: single and double
 * digits) and stay UNDER what a portal floats over its results — sticky
 * headers, filter drawers, galleries, cookie walls (the common scales start at
 * 1000). At the maximum the controls showed through every one of those. */
const CARD_CSS = `
  ${tokens}
  :host {
    all: initial !important;
    position: absolute !important; top: 6px !important; left: 6px !important;
    z-index: 900 !important; display: block !important;
  }
  [hidden] { display: none !important; }
  .bar {
    display: flex; align-items: center; gap: 3px;
    font-family: system-ui, -apple-system, sans-serif;
    transition: opacity 140ms ease;
  }
  :host([data-veiled]) .bar { opacity: 0.5; }
  :host-context([${DISMISSED_ATTR}]:hover) .bar,
  :host([data-veiled]) .bar:focus-within { opacity: 1; }
  .act {
    box-sizing: border-box; display: inline-flex; align-items: center;
    justify-content: center; gap: 2px; height: 24px; min-width: 24px;
    margin: 0; padding: 0; font: inherit; color: var(--color-ink-3);
    background: color-mix(in srgb, var(--color-paper-3) 92%, transparent);
    border: 1px solid var(--color-rule-strong); border-radius: var(--radius-xs);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
    -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px);
    cursor: pointer;
    transition: color 120ms ease, border-color 120ms ease, background 120ms ease;
  }
  .act:hover { color: var(--color-copper); border-color: var(--color-copper); }
  .act--dismiss:hover { color: var(--color-ink); border-color: var(--color-ink); }
  .act[aria-disabled="true"] { opacity: 0.6; cursor: wait; }
  .act:focus-visible, .badge:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 1px; }
  .act svg { width: 14px; height: 14px; flex-shrink: 0; }
  .act--in { padding: 0 4px; background: color-mix(in srgb, var(--color-paper-3) 96%, transparent); }
  .act--saved { color: var(--color-copper); border-color: var(--color-copper); background: var(--color-copper-soft); }
  .act--on { color: var(--color-ink); border-color: var(--color-ink-3); background: var(--color-inset); }
  .code {
    font-size: 11px; font-weight: 600; line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .badge {
    box-sizing: border-box; display: inline-flex; align-items: center;
    height: 24px; margin: 0; padding: 0 7px;
    font: inherit; font-size: 11px; font-weight: 600; line-height: 1;
    letter-spacing: 0.02em; font-variant-numeric: tabular-nums; white-space: nowrap;
    border: 1px solid var(--color-rule-strong); border-radius: var(--radius-xs);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
    cursor: pointer;
  }
  .badge--yield { background: var(--color-copper); color: var(--color-on-copper); }
  .badge--est { background: var(--color-ink-3); color: var(--color-paper-3); }
  .badge--cta { background: var(--color-paper-3); color: var(--color-copper); }
  .badge--cta:hover { background: var(--color-copper-soft); }
  .badge--yield:hover, .badge--est:hover { filter: brightness(1.08); }
  @media (prefers-reduced-motion: reduce) { .bar, .act { transition: none; } }
`;

/* The panel's slip, floated: a popover surface, the accent "filed" top edge.
 * Rows are the panel checklist's rows. */
const MENU_CSS = `
  ${tokens}
  :host { all: initial; }
  [hidden] { display: none !important; }
  .menu {
    position: fixed; top: 0; left: 0; z-index: 2147483647;
    box-sizing: border-box; width: 248px; max-width: calc(100vw - 16px);
    max-height: calc(100vh - 16px); overflow-y: auto; overscroll-behavior: contain;
    padding: 8px 6px;
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    font-size: 13px; line-height: 1.3; text-align: left; color: var(--color-ink);
    background: var(--color-paper-3); border: 1px solid var(--color-rule-strong);
    border-radius: var(--radius-md);
    box-shadow: inset 0 2px 0 var(--color-copper), 0 10px 34px -10px rgba(28, 20, 10, 0.35);
  }
  .menu:focus { outline: none; }
  .eyebrow {
    margin: 2px 6px 6px; font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.16em; color: var(--color-ink-3);
  }
  .list {
    display: flex; flex-direction: column; gap: 1px; max-height: 240px;
    margin: 0; padding: 0; overflow-y: auto; list-style: none;
  }
  .row {
    box-sizing: border-box; display: flex; align-items: center; gap: 8px;
    width: 100%; margin: 0; padding: 6px; font: inherit; text-align: left;
    color: var(--color-ink); background: transparent; border: 0;
    border-radius: var(--radius-xs); cursor: pointer;
    transition: background 120ms ease;
  }
  .row:hover:not([aria-disabled="true"]) { background: var(--color-copper-soft); }
  .row[aria-disabled="true"] { opacity: 0.6; cursor: wait; }
  .row:focus-visible, .link:focus-visible, .yes:focus-visible, .no:focus-visible {
    outline: 2px solid var(--color-focus); outline-offset: -2px;
  }
  .name {
    flex: 1 1 auto; min-width: 0; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
  }
  .stage-code {
    box-sizing: border-box; display: inline-flex; align-items: center;
    justify-content: center; min-width: 18px; height: 16px; padding: 0 3px;
    flex-shrink: 0; font-size: 11px; font-weight: 600; line-height: 1;
    font-variant-numeric: tabular-nums; border: 1px solid currentColor;
    border-radius: var(--radius-xs);
  }
  .mark { flex-shrink: 0; font-size: 11px; }
  .check {
    box-sizing: border-box; display: inline-flex; align-items: center;
    justify-content: center; width: 14px; height: 14px; flex-shrink: 0;
    font-size: 11px; line-height: 1; color: transparent;
    background: var(--color-paper-3); border: 1px solid var(--color-ink-4);
    border-radius: var(--radius-xs);
  }
  .row--member .check {
    color: var(--color-on-copper); background: var(--color-copper); border-color: var(--color-copper);
  }
  .bell { display: inline-flex; flex-shrink: 0; color: var(--color-copper); }
  .bell svg { width: 13px; height: 13px; }
  .state { margin: 2px 6px; font-size: 12px; color: var(--color-ink-3); }
  .error { margin: 6px 6px 0; font-size: 11px; line-height: 1.35; color: var(--color-brick); }
  .link {
    margin: 0; padding: 0; font: inherit; font-weight: 600; color: var(--color-copper);
    text-decoration: none; background: transparent; border: 0; cursor: pointer;
  }
  .link:hover { color: var(--color-copper-2); text-decoration: underline; }
  .manage { display: inline-block; margin: 8px 6px 0; font-size: 11px; }
  .sep { height: 0; margin: 6px 0 4px; border: 0; border-top: 1px solid var(--color-rule); }
  .row--remove { color: var(--color-ink-3); }
  .row--remove:hover:not([aria-disabled="true"]) { color: var(--color-brick); background: var(--color-brick-soft); }
  .confirm { padding: 4px 6px 2px; }
  .confirm-q { margin: 0; font-size: 12px; font-weight: 600; }
  .confirm-d { margin: 3px 0 0; font-size: 11px; line-height: 1.35; color: var(--color-ink-3); }
  .confirm-actions { display: flex; gap: 6px; margin-top: 7px; }
  .yes, .no {
    margin: 0; padding: 3px 8px; font: inherit; font-size: 11px;
    background: transparent; border-radius: var(--radius-sm); cursor: pointer;
  }
  .yes { color: var(--color-brick); border: 1px solid var(--color-brick); }
  .yes:hover { color: var(--color-on-brick); background: var(--color-brick); }
  .no { color: var(--color-ink-3); border: 1px solid var(--color-rule); }
  .no:hover { color: var(--color-ink); border-color: var(--color-ink-4); }
`;

/* Events that start inside the cluster end there. A card is often one big
 * link (or a carousel that drags on pointer-down), and SPA routers listen at
 * the document: a click on the funnel must neither navigate nor reach them. */
const SHIELDED_EVENTS = [
  'click', 'auxclick', 'dblclick', 'mousedown', 'mouseup', 'pointerdown',
  'pointerup', 'touchstart', 'touchend', 'keydown', 'keyup', 'keypress',
] as const;

function setBusy(node: HTMLElement, busy: boolean): void {
  /* aria-disabled, never `disabled`: a disabled button drops keyboard focus to
   * the portal page for the length of the write. Handlers ignore busy clicks. */
  if (busy) node.setAttribute('aria-disabled', 'true');
  else node.removeAttribute('aria-disabled');
}

function injectPageStyle(): void {
  if (document.getElementById(PAGE_STYLE_ID) != null) return;
  const style = document.createElement('style');
  style.id = PAGE_STYLE_ID;
  style.textContent = PAGE_CSS;
  (document.head ?? document.documentElement).appendChild(style);
}

/* True when every listing link inside the node is this listing's. The card is
 * found by a markup-agnostic guess (index_overlay's cardFor); should that guess
 * land on a wrapper holding several results, dimming it would blank them all —
 * so such a node is never dimmed, and never climbed to. */
export function holdsOneListing(card: HTMLElement, sourceId: string): boolean {
  return Array.from(card.querySelectorAll<HTMLAnchorElement>('a[href]')).every((a) => {
    const ref = detailRef(a.href, location.hostname);
    return ref == null || ref.sourceId === sourceId;
  });
}

export function createCardLayer(deps: CardLayerDeps): CardLayer {
  const { store, cache } = deps;
  injectPageStyle();

  const handles = new Map<HTMLElement, CardHandle>();
  /* Collection ids a held list did not know and was re-read for — once each, so
   * an id a fresh read still lacks cannot loop the re-read. */
  const rereadFor = new Set<number>();
  /* Lists this run has read. Each overlay run reads a list afresh the first time a
   * card needs it — a rename made in the app shows, a read that failed is asked
   * once more — and after that only one never read (paint runs on every pass). */
  const read = new Set<ListKind>();
  function need(kind: ListKind): void {
    if (read.has(kind)) store.ensure(kind);
    else if (store.load(kind, true)) read.add(kind);
  }

  let menu: Menu | null = null;
  let menuDom: MenuDom | null = null;
  /* What the open menu was last built from. refresh() runs on every overlay
   * pass — any DOM mutation of the portal page — and a menu rebuilt under the
   * pointer swallows the click being made, so it is rebuilt only on a change. */
  let menuPainted = '';

  // ---- writes ---------------------------------------------------------------
  // The store's one write path (optimistic, put back on failure); a failure is
  // said where it can be seen.

  async function flashFailure(pending: Pending, what: string): Promise<void> {
    const res = await pending;
    if (res?.ok === false) deps.flash(`${what}: ${friendlyDetail(res.detail)}`);
  }

  async function toggleCollection(propertyId: number, collectionId: number): Promise<void> {
    if (store.busy('collections', propertyId)) return;
    if (menu?.propertyId === propertyId) menu.error = null;
    const res = await store.toggleCollection(propertyId, collectionId);
    if (res == null || res.ok) return;
    const message = res.on
      ? `Uložení do kolekce se nepodařilo: ${friendlyDetail(res.detail)}`
      : `Odebrání z kolekce se nepodařilo: ${friendlyDetail(res.detail)}`;
    /* Beside the rows while the checklist is still open for this property,
     * else on the dock. */
    if (menu?.kind === 'collections' && menu.propertyId === propertyId) {
      menu.error = message;
      renderMenu();
    } else {
      deps.flash(message);
    }
  }

  // ---- the card cluster -----------------------------------------------------

  function mount(card: HTMLElement, ref: PortalRef, href: string): void {
    const sourceId = ref.sourceId;
    if (card.getAttribute(PROCESSED_ATTR) === sourceId) return;
    clear(card);  // a recycled node drops the previous listing's controls first
    card.setAttribute(PROCESSED_ATTR, sourceId);

    const first = cache.get(sourceId);
    if (first == null) return;
    const hasProperty = first.found && first.property_id != null;
    if (!hasProperty && deps.badgeFor(first) == null) return;

    if (getComputedStyle(card).position === 'static') card.style.position = 'relative';

    const host = document.createElement('div');
    host.className = HOST_CLASS;
    themeHost(host);
    const shadow = host.attachShadow({ mode: 'closed' });
    const style = document.createElement('style');
    style.textContent = CARD_CSS;
    const bar = h('div', 'bar');
    const funnel = h('button', 'act');
    const save = h('button', 'act');
    const hide = h('button', 'act act--dismiss');
    const badge = h('button', 'badge');
    bar.append(funnel, save, hide, badge);
    shadow.append(style, bar);
    const mark = createPhotoMark(card, PROCESSED_ATTR);

    const current = (): { curation: Curation | null; propertyId: number } | null => {
      const listing = cache.get(sourceId);
      return listing?.found && listing.property_id != null
        ? { curation: store.curation(listing.property_id), propertyId: listing.property_id }
        : null;
    };

    funnel.onclick = () => {
      const now = current();
      if (now == null || store.busy('pipeline', now.propertyId)) return;
      if (now.curation?.pipeline?.in_pipeline) toggleMenu('stages', now.propertyId, funnel, host);
      else {
        closeMenu(false);
        void flashFailure(store.addToPipeline(now.propertyId), 'Uložení do pipeline selhalo');
      }
    };
    save.onclick = () => {
      const now = current();
      if (now != null) toggleMenu('collections', now.propertyId, save, host);
    };
    hide.onclick = () => {
      const now = current();
      if (now == null) return;
      closeMenu(false);
      void flashFailure(store.toggleDismiss(now.propertyId), 'Skrytí se nepodařilo uložit');
    };
    badge.onclick = () => {
      const listing = cache.get(sourceId);
      if (listing == null) return;
      closeMenu(false);
      deps.openPanel(ref, href, listing);
    };

    for (const type of SHIELDED_EVENTS) {
      host.addEventListener(type, (e) => {
        e.stopPropagation();
        if (type === 'click' || type === 'auxclick') e.preventDefault();
        if (type === 'keydown' && (e as KeyboardEvent).key === 'Escape' && menu?.anchorHost === host) {
          closeMenu(true);
        }
      }, { passive: type === 'touchstart' || type === 'touchend' });
    }

    let painted = '';
    function paint(): void {
      const l = cache.get(sourceId);
      if (l == null) return;
      const pid = l.found ? l.property_id : null;
      const cur = pid != null ? store.curation(pid) : null;
      const stages = store.list('stages').data;
      const collections = store.list('collections').data;
      const collectionsFailed = store.list('collections').status === 'failed';
      const p = cur?.pipeline;
      const inPipe = p?.in_pipeline ?? false;
      const look = stageLook(p);
      const code = look && stageBadge(look, stages ?? []);
      const memberIds = cur?.collection_ids ?? [];
      /* Saved = in one of MINE; until the list lands mine is unknown, and no
       * mark beats a wrong one (a shared-only property is not saved). */
      const savedNames = (savedCollections(memberIds, collections) ?? []).map((c) => c.name);
      const saved = savedNames.length;
      const tagIds = cur?.tag_ids ?? [];
      const tagged = (store.list('tags').data ?? [])
        .filter((t) => tagIds.includes(t.id))
        .map((t) => ({ name: t.name, color: t.color }));
      const dismissed = cur?.dismissed === true;
      const canHide = cur?.dismissed != null && !isLiveDeal(p, stages);
      const veiled = dismissed && deps.veilOn();
      const view = deps.badgeFor(l);
      /* The lists tell mine from shared and name the label; they name the tag
       * chips. A held list that lacks one of the card's collections is stale
       * (made or shared in the app since it was read): re-read it once — an id
       * is spent only on a read that went out, not on one already under way.
       * Before the unchanged-paint return, so a stale-but-unchanged card asks. */
      if (memberIds.length > 0) {
        need('collections');
        if (collections != null && !knowsAll(memberIds, collections)) {
          const fresh = memberIds.filter((id) => !rereadFor.has(id));
          if (fresh.length > 0 && store.load('collections', true)) for (const id of fresh) rereadFor.add(id);
        }
      }
      if (tagIds.length > 0) need('tags');
      const busyPipeline = pid != null && store.busy('pipeline', pid);
      const busyDismiss = pid != null && store.busy('dismissed', pid);
      const state = JSON.stringify([
        pid, inPipe, p?.stage_label, p?.stage_color, code, saved, savedNames, tagged, dismissed,
        memberIds.length > 0 && collectionsFailed,
        canHide,
        veiled, view, busyPipeline, busyDismiss,
        menu?.anchor === funnel, menu?.anchor === save,
      ]);
      if (state === painted) return;
      painted = state;

      funnel.hidden = pid == null;
      funnel.className = 'act' + (inPipe ? ' act--in' : '');
      const accent = stageAccent(look);
      funnel.style.color = inPipe ? accent.fg : '';
      funnel.style.borderColor = inPipe ? accent.fg : '';
      const funnelLabel = !inPipe
        ? 'Přidat do pipeline'
        : p?.stage_label
          ? `V pipeline (${p.stage_label}) — změnit fázi`
          : 'V pipeline — změnit fázi';
      funnel.title = funnelLabel;
      funnel.setAttribute('aria-label', funnelLabel);
      funnel.setAttribute('aria-pressed', String(inPipe));
      if (inPipe) {
        funnel.setAttribute('aria-haspopup', 'menu');
        funnel.setAttribute('aria-expanded', String(menu?.anchor === funnel));
      } else {
        funnel.removeAttribute('aria-haspopup');
        funnel.removeAttribute('aria-expanded');
      }
      setBusy(funnel, busyPipeline);
      funnel.innerHTML = funnelIconSvg(inPipe);
      if (code != null) funnel.appendChild(h('span', 'code', code));

      save.hidden = pid == null;
      save.className = 'act' + (saved > 0 ? ' act--saved' : '');
      // A failed read is unknown, never "not saved" (the panel's 'Kolekce').
      const saveLabel = saved > 0
        ? `${COLLECTION_SAVE_LABEL} — v ${saved} ${saved === 1 ? 'kolekci' : 'kolekcích'}`
        : memberIds.length > 0 && collectionsFailed
          ? 'Kolekce se nepodařilo načíst'
          : COLLECTION_SAVE_LABEL;
      save.title = saveLabel;
      save.setAttribute('aria-label', saveLabel);
      save.setAttribute('aria-expanded', String(menu?.anchor === save));
      save.innerHTML = bookmarkIconSvg(saved > 0);

      hide.hidden = !canHide;
      hide.className = 'act act--dismiss' + (dismissed ? ' act--on' : '');
      const hideLabel = dismissed ? 'Skryto — znovu zobrazit' : 'Skrýt nemovitost';
      hide.title = hideLabel;
      hide.setAttribute('aria-label', hideLabel);
      hide.setAttribute('aria-pressed', String(dismissed));
      setBusy(hide, busyDismiss);
      hide.innerHTML = eyeOffIconSvg(dismissed);

      badge.hidden = view == null;
      if (view != null) {
        badge.className = `badge badge--${view.kind}`;
        badge.textContent = view.text;
        badge.title = view.title;
      }

      host.toggleAttribute('data-veiled', veiled);
      // A card guess that holds several results would mark another one's photo.
      const marked = inPipe || saved > 0 || tagged.length > 0;
      const single = (dismissed || marked) && holdsOneListing(card, sourceId);
      if (dismissed && single) card.setAttribute(DISMISSED_ATTR, '');
      else card.removeAttribute(DISMISSED_ATTR);
      mark.show(marked && !veiled && single
        ? {
          stage: inPipe
            ? { color: p?.stage_color ?? null, badge: code, label: p?.stage_label ?? null }
            : null,
          collections: saved > 0 ? savedNames : null,
          tags: tagged,
        }
        : null);
    }

    handles.set(card, {
      sourceId,
      paint,
      destroy(): void {
        if (menu?.anchorHost === host) closeMenu(false);
        mark.destroy();
        host.remove();
      },
    });
    card.appendChild(host);
    paint();
    // The stage list names each card's badge and says which deals are live.
    if (hasProperty) need('stages');
  }

  function clear(card: HTMLElement): void {
    const handle = handles.get(card);
    handles.delete(card);
    handle?.destroy();
    // A host left by an overlay run that has since stopped has no handle here.
    card.querySelector(`:scope > .${HOST_CLASS}`)?.remove();
    card.querySelector(`:scope > .${PHOTO_MARK_HOST_CLASS}`)?.remove();
    card.removeAttribute(PROCESSED_ATTR);
    card.removeAttribute(DISMISSED_ATTR);
  }

  function refresh(): void {
    for (const [card, handle] of [...handles]) {
      // The portal dropped the node; should it ever put it back, it re-mounts.
      if (!card.isConnected) clear(card);
      else handle.paint();
    }
    renderMenu();
    deps.onRefresh();
  }

  function dismissedCount(): number {
    const ids = new Set<string>();
    for (const [card, handle] of handles) {
      const pid = cache.get(handle.sourceId)?.property_id;
      if (card.isConnected && pid != null && store.curation(pid)?.dismissed === true) {
        ids.add(handle.sourceId);
      }
    }
    return ids.size;
  }

  function reset(): void {
    closeMenu(false);
    for (const card of [...handles.keys()]) clear(card);
    rereadFor.clear();
    read.clear();
  }

  // ---- the menus ------------------------------------------------------------

  function toggleMenu(
    kind: MenuKind, propertyId: number, anchor: HTMLButtonElement, anchorHost: HTMLElement,
  ): void {
    const reopen = menu?.anchor !== anchor;
    closeMenu(false);
    if (!reopen) return;

    document.getElementById(MENU_HOST_ID)?.remove();
    const host = document.createElement('div');
    host.id = MENU_HOST_ID;
    themeHost(host);
    const shadow = host.attachShadow({ mode: 'closed' });
    const style = document.createElement('style');
    style.textContent = MENU_CSS;
    const box = h('div', 'menu');
    box.tabIndex = -1;
    box.addEventListener('keydown', onMenuKey);
    shadow.append(style, box);
    document.body.appendChild(host);

    menu = { kind, propertyId, anchor, anchorHost, confirming: false, error: null };
    menuDom = { host, shadow, box };
    menuPainted = '';
    document.addEventListener('pointerdown', onOutsidePointer, true);
    window.addEventListener('scroll', placeMenu, { capture: true, passive: true });
    window.addEventListener('resize', placeMenu);

    if (kind === 'stages') store.load('stages');
    else store.load('collections', true);
    refresh();
    focusInMenu(kind === 'stages' ? '[aria-checked="true"]' : '.row');
  }

  /* `refocus` hands focus back to the control that opened the menu — Escape and
   * a made choice do; a click elsewhere has already put focus where it belongs. */
  function closeMenu(refocus: boolean): void {
    if (menu == null) return;
    const { anchor } = menu;
    menu = null;
    document.removeEventListener('pointerdown', onOutsidePointer, true);
    window.removeEventListener('scroll', placeMenu, true);
    window.removeEventListener('resize', placeMenu);
    menuDom?.host.remove();
    menuDom = null;
    if (refocus && anchor.isConnected) anchor.focus();
    refresh();  // the anchor's aria-expanded
  }

  /* Both shadow roots are closed, so a pointer event from inside either one
   * arrives here targeting its host. */
  function onOutsidePointer(e: Event): void {
    if (menu == null || menuDom == null) return;
    // The anchor's own click toggles the menu; closing it here would reopen it.
    if (e.target === menuDom.host || e.target === menu.anchorHost) return;
    closeMenu(false);
  }

  function onMenuKey(e: KeyboardEvent): void {
    e.stopPropagation();  // ours, not the portal's (lightboxes close on Escape too)
    if (menuDom == null) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      closeMenu(true);
      return;
    }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const items = Array.from(
      menuDom.box.querySelectorAll<HTMLElement>('.row, .yes, .no'),
    );
    if (items.length === 0) return;
    e.preventDefault();
    const at = items.indexOf(menuDom.shadow.activeElement as HTMLElement);
    const down = e.key === 'ArrowDown';
    const next = at < 0
      ? (down ? 0 : items.length - 1)
      : (at + (down ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  }

  function focusInMenu(selector: string): void {
    if (menuDom == null) return;
    const target = menuDom.box.querySelector<HTMLElement>(selector)
      ?? menuDom.box.querySelector<HTMLElement>('.row');
    (target ?? menuDom.box).focus({ preventScroll: true });
  }

  /* Under the anchor, flipped above it when the viewport runs out; a menu
   * whose anchor is gone (the portal re-rendered the card) closes. */
  function placeMenu(): void {
    if (menu == null || menuDom == null) return;
    if (!menu.anchor.isConnected) {
      closeMenu(false);
      return;
    }
    const { box } = menuDom;
    const r = menu.anchor.getBoundingClientRect();
    const width = box.offsetWidth;
    const height = box.offsetHeight;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    const below = r.bottom + 4;
    const above = r.top - 4 - height;
    const top = below + height > window.innerHeight - 8 && above >= 8 ? above : below;
    box.style.left = `${Math.round(left)}px`;
    box.style.top = `${Math.round(top)}px`;
  }

  function renderMenu(): void {
    const open = menu;
    if (open == null || menuDom == null) return;
    const cur = store.curation(open.propertyId);
    if (
      !open.anchor.isConnected || cur == null
      || (open.kind === 'stages' && !cur.pipeline?.in_pipeline)
    ) {
      closeMenu(false);
      return;
    }
    const { box, shadow } = menuDom;
    const view = JSON.stringify([
      open.kind, open.propertyId, open.confirming, open.error,
      cur.pipeline, cur.collection_ids,
      store.busy('pipeline', open.propertyId), store.busy('collections', open.propertyId),
      store.list('stages'), store.list('collections'),
    ]);
    if (view === menuPainted) {
      placeMenu();
      return;
    }
    menuPainted = view;
    const listTop = box.querySelector<HTMLElement>('.list')?.scrollTop ?? 0;
    keepFocus(shadow, () => {
      box.replaceChildren();
      if (open.kind === 'stages') buildStageMenu(box, cur, open);
      else buildCollectionMenu(box, cur, open);
    }, () => box);
    const list = box.querySelector<HTMLElement>('.list');
    if (list != null) list.scrollTop = listTop;
    placeMenu();
  }

  function stateLine(text: string, retry?: () => void): HTMLElement {
    const p = h('p', 'state', text);
    if (retry != null) {
      const again = h('button', 'link', 'Zkusit znovu');
      again.dataset.key = 'retry';
      again.onclick = retry;
      p.append(' ', again);
    }
    return p;
  }

  /* The app's <PipelineStageMenu>: every stage as a row (the current one
   * checked), then removal behind its two-step confirm. */
  function buildStageMenu(box: HTMLElement, cur: Curation, open: Menu): void {
    const propertyId = open.propertyId;
    const pending = store.busy('pipeline', propertyId);
    const { data: stages, status } = store.list('stages');
    box.setAttribute('role', 'menu');
    box.setAttribute('aria-label', STAGE_MENU_LABEL);
    box.appendChild(h('p', 'eyebrow', STAGE_MENU_LABEL));

    if (stages == null) {
      box.appendChild(status === 'failed'
        ? stateLine('Fáze se nepodařilo načíst.', () => { store.load('stages'); })
        : stateLine('Načítám fáze…'));
    } else {
      const list = h('div', 'list');
      for (const stage of stages) {
        const isCurrent = stage.id === cur.pipeline?.stage_id;
        const accent = stageAccent(stage);
        const row = h('button', 'row');
        row.setAttribute('role', 'menuitemradio');
        row.setAttribute('aria-checked', String(isCurrent));
        row.dataset.key = `stage-${stage.id}`;
        setBusy(row, pending);
        const code = h('span', 'stage-code', stageBadge(stage, stages) ?? '·');
        code.setAttribute('aria-hidden', 'true');
        code.style.color = accent.fg;
        row.append(code, h('span', 'name', stage.label));
        if (isCurrent) {
          const mark = h('span', 'mark', '✓');
          mark.setAttribute('aria-hidden', 'true');
          mark.style.color = accent.fg;
          row.appendChild(mark);
        }
        row.onclick = () => {
          if (store.busy('pipeline', propertyId)) return;
          // Close first: the write repaints, and focus must already be back on the funnel.
          closeMenu(true);
          if (!isCurrent) void flashFailure(store.moveStage(propertyId, stage.id), 'Změna fáze selhala');
        };
        list.appendChild(row);
      }
      box.appendChild(list);
    }

    box.appendChild(h('div', 'sep'));
    if (!open.confirming) {
      const remove = h('button', 'row row--remove', 'Odebrat z pipeline');
      remove.setAttribute('role', 'menuitem');
      remove.dataset.key = 'remove';
      setBusy(remove, pending);
      remove.onclick = () => {
        if (store.busy('pipeline', propertyId)) return;
        open.confirming = true;
        renderMenu();
        focusInMenu('.yes');
      };
      box.appendChild(remove);
      return;
    }

    /* Removal is the one pipeline action with no undo — the card's dates are
     * deleted and re-adding restamps them — so it asks, in the app's words,
     * and points at the closed stages, which keep the deal's record. */
    const confirm = h('div', 'confirm');
    confirm.appendChild(h('p', 'confirm-q', 'Odebrat z pipeline?'));
    confirm.appendChild(h(
      'p', 'confirm-d',
      'Karta zmizí z nástěnky a ztratí „v pipeline od“ i „ve fázi od“ — po '
        + 'opětovném přidání se počítají znovu.'
        + ((stages ?? []).some((s) => s.is_terminal)
          ? ' Uzavřený obchod raději přesuňte do některé z uzavřených fází.'
          : ''),
    ));
    const actions = h('div', 'confirm-actions');
    const yes = h('button', 'yes', 'Odebrat');
    yes.dataset.key = 'confirm-yes';
    yes.onclick = () => {
      closeMenu(true);
      void flashFailure(store.removeFromPipeline(propertyId), 'Odebrání z pipeline selhalo');
    };
    const no = h('button', 'no', 'Zrušit');
    no.dataset.key = 'confirm-no';
    no.onclick = () => {
      open.confirming = false;
      renderMenu();
      focusInMenu('.row--remove');
    };
    actions.append(yes, no);
    confirm.appendChild(actions);
    box.appendChild(confirm);
  }

  /* The app's <CollectionSaveMenu>: every collection of MINE as a checkable
   * row, monitored ones first and bell-marked. It stays open across clicks — a
   * property can sit in several collections. */
  function buildCollectionMenu(box: HTMLElement, cur: Curation, open: Menu): void {
    const propertyId = open.propertyId;
    const memberIds = new Set(cur.collection_ids ?? []);
    const pending = store.busy('collections', propertyId);
    const { data: collections, status } = store.list('collections');
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', COLLECTION_SAVE_LABEL);
    box.appendChild(h('p', 'eyebrow', COLLECTION_SAVE_LABEL));

    if (collections == null) {
      /* A failed read is NOT an empty one — "no collections yet" would tell an
       * operator whose collections exist that they have none. */
      box.appendChild(status === 'failed'
        ? stateLine('Kolekce se nepodařilo načíst.', () => { store.load('collections', true); })
        : stateLine('Načítám kolekce…'));
    } else if (saveTargets(collections).length === 0) {
      box.appendChild(stateLine('Zatím nemáte žádnou kolekci.'));
    } else {
      const list = h('div', 'list');
      for (const c of saveTargets(collections)) {
        const member = memberIds.has(c.id);
        const row = h('button', 'row' + (member ? ' row--member' : ''));
        row.setAttribute('aria-pressed', String(member));
        row.dataset.key = `coll-${c.id}`;
        setBusy(row, pending);
        const check = h('span', 'check', '✓');
        check.setAttribute('aria-hidden', 'true');
        const name = h('span', 'name', c.name);
        name.title = c.name;
        row.append(check, name);
        if (c.monitoring_enabled) {
          const bell = h('span', 'bell');
          bell.title = 'Sledovaná — upozorní na změny';
          bell.innerHTML = bellIconSvg();
          row.appendChild(bell);
        }
        row.onclick = () => { void toggleCollection(propertyId, c.id); };
        list.appendChild(row);
      }
      box.appendChild(list);
    }

    if (open.error != null) {
      const err = h('p', 'error', open.error);
      err.setAttribute('role', 'alert');
      box.appendChild(err);
    }

    /* Creating, renaming and monitoring settings stay in the app — the
     * checklist only files the property. */
    if (COLLECTIONS_URL != null) {
      const manage = h('a', 'link manage', collections != null && saveTargets(collections).length === 0
        ? 'Založit kolekci v aplikaci →'
        : 'Spravovat kolekce →');
      manage.href = COLLECTIONS_URL;
      manage.target = '_blank';
      manage.rel = 'noopener';
      box.appendChild(manage);
    }
  }

  return { mount, clear, refresh, dismissedCount, reset };
}
