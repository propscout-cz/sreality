/* Index / search-page overlay. We don't depend on any portal's card markup:
 * we scan every <a href>, keep the ones whose href yields a listing id via the
 * portal registry, and draw on the nearest card-ish ancestor. One batched lookup
 * per pass; a result cache makes it cheap + resilient to SPA re-renders.
 *
 * It runs on EVERY index page of a portal, whatever the category. Per card
 * (index_cards.ts): the pipeline / collection / hide controls for any listing
 * we have a property for, and — on sale apartments only — the yield badge,
 * ALWAYS clickable → opens the listing side sheet (panel.ts: the property's
 * facts, price history, curation and the Výnos estimate). Badge label: "Výnos MF X %"
 * (or the town's "X–Y %" range) when the property's MF result has a yield, else
 * "Odhad X %" when an estimation already exists, else "Odhadnout výnos".
 *
 * Per page (index_dock.ts): the bottom-left dock with the "Skrýt skryté"
 * switch — whether a dismissed property's card is drawn barely visible.
 * And (index_promo.ts) the grid's cards that are not listings — paid ads,
 * project promos — washed whitish.
 *
 * Sale-apartment gating of the badge: by our row's category. An ad we do not
 * hold gets no badge — an estimation is always of an ad we hold, and adding one
 * is the detail panel's "Přidat do databáze" (docs/design/new-property-from-link.md).
 *
 * Controls and badges only ever come from a SUCCESSFUL lookup. When it fails
 * (signed out, network / API error) the page gets one notice in the dock
 * instead — see "the failure notice" below. */

// The shared product brand (frontend/src/lib/brand.ts) — the notice's wordmark
// is the same string as the panel header's.
import { APP_NAME } from '../../frontend/src/lib/brand';
import { fmtCzk, fmtPct } from '../../frontend/src/lib/format';
// The MF result's ONE shape rule and its yield text, shared with the SPA.
import { mfShape, mfYieldText } from '../../frontend/src/lib/mfReference';
import {
  API_NOT_CONFIGURED_DETAIL,
  EXTENSION_RELOADED_DETAIL,
  NOT_SIGNED_IN_DETAIL,
} from './config';
import { h } from './dom';
import {
  PROCESSED_ATTR,
  VEIL_ROOT_ATTR,
  createCardLayer,
  holdsOneListing,
  type BadgeView,
} from './index_cards';
import { mountDock, onVeilChange, readVeil, writeVeil, type DockHandle } from './index_dock';
import { createPromoWash } from './index_promo';
import { sleep, type Store } from './store';
import { detailRef, portalForHost, type PortalRef } from '../../frontend/src/lib/portalIds';
import type { ApiResult, AuthState, PortalListing } from './types';

type OpenPanel = (
  ref: PortalRef, url: string, prefetched?: PortalListing | null,
) => Promise<void>;

const SCAN_DEBOUNCE_MS = 400;
const MAX_LOOKUP_PER_PASS = 50;

/* ---- the failure notice --------------------------------------------------
 *
 * A failed lookup leaves nothing to draw, and the page used to stay silent: a
 * signed-out operator got no badge, no CTA and no sign-in prompt — while a
 * detail page in the same state shows the panel's prompt — and read it as a
 * broken extension (2026-09-24). A failure now raises ONE page-level notice
 * offering what can fix it: sign-in when signed out, a page reload when an
 * extension update orphaned this script, a retry for other errors, nothing for
 * a build without an API URL. Page-level, not per-card: without a lookup there
 * is no row to say what a card is, so nothing per card can carry the prompt.
 * It shows while ANY listing card waits on the failed lookup — every card gets
 * controls from it now, so a rental or house search is as stuck as a flat one —
 * and never on a page without listing cards. It sits in the dock, bottom-LEFT,
 * above the "Skrýt skryté" bar: the listing sheet owns the right edge and a
 * badge click can open it while the notice is up (a new card's lookup failing
 * under an open sheet), so the two never share a side. × hides it until the
 * next page load (a soft-nav round trip through a listing keeps it hidden).
 *
 * Backoff: after a failure, observer-driven passes don't ask again for
 * LOOKUP_RETRY_AFTER_MS — a 401 costs no network, but in a real outage every
 * DOM mutation (re-renders, infinite scroll) would re-fetch. The notice's
 * buttons, the tab becoming visible again and an account change (a sign-in or
 * sign-out in the panel or any tab, session.ts) skip the wait; the sign-in
 * button asks get_auth_state first and skips the Google round trip when a
 * session already exists.
 *
 * Rounds: one lookup is out at a time, but an account change or the retry
 * button starts a new round whose lookup goes out even while an older one is
 * pending (a hung request can't swallow the click), as does the tab becoming
 * visible while one is out. Only the current round decides the notice: an
 * older ask's FAILURE is dropped, but its OK answer still fills the cache and
 * badges — rows are rows. (An answer asked before an account change never
 * arrives at all: the store's one session generation drops it.) A lookup unanswered after
 * LOOKUP_TIMEOUT_MS is SHOWN as failed so a hung one can't leave the page
 * silent, and its answer, whenever it lands, still counts: the deadline
 * decides what the notice says, not what the cache holds.
 *
 * Teardown: stop() removes the notice, and everything that awaits re-checks
 * `stopped` — a sign-in or lookup resolving after a route change must not
 * re-mount the notice or re-scan a page this overlay no longer owns. */
const LOOKUP_RETRY_AFTER_MS = 60_000;
/* Prod answers in well under a second; past this a lookup reads as failed. */
const LOOKUP_TIMEOUT_MS = 20_000;
/* A retry that fails again within a frame (offline: "Failed to fetch" is
 * near-instant) would change nothing on screen; the busy state holds this long. */
const RETRY_MIN_BUSY_MS = 400;
/* A live region inserted already filled is typically not announced, so the
 * notice goes in empty and gets its text a frame + this long later. */
const LIVE_REGION_SETTLE_MS = 100;

/* × lasts for the page, not for one overlay run: content.ts restarts the
 * overlay on every soft-nav back from a listing, and a signed-out operator who
 * hid the notice must not get it again after each card they open. A real page
 * load starts a fresh content script, and with it a fresh flag. */
let noticeDismissed = false;

interface Hit {
  ref: PortalRef;
  anchor: HTMLAnchorElement;
  href: string;
}

/* What the notice offers: sign in; reload the page; retry; or nothing. */
type FailureKind = 'signed_out' | 'reload' | 'error' | 'fatal';

interface LookupFailure {
  kind: FailureKind;
  detail: string;
}

interface NoticeView {
  failure: LookupFailure;
  busy: boolean;
  signInError: string | null;
}

interface NoticeHandlers {
  signIn: () => void;
  retry: () => void;
  reload: () => void;
  dismiss: () => void;
}

interface NoticeHandle {
  render: (view: NoticeView) => void;
  connected: () => boolean;
  destroy: () => void;
}

export interface IndexOverlayHandle {
  /* Disconnect the observer, cancel any pending scan, remove everything drawn —
   * called when a route change (SPA soft-nav) moves the tab off an index page,
   * so repeated navigations don't stack duplicate observers scanning the DOM. */
  stop: () => void;
}

export function failureKind(detail: string): FailureKind {
  if (detail === NOT_SIGNED_IN_DETAIL) return 'signed_out';
  if (detail === EXTENSION_RELOADED_DETAIL) return 'reload';
  if (detail === API_NOT_CONFIGURED_DETAIL) return 'fatal';
  return 'error';
}

export async function runIndexOverlay(
  store: Store, openPanel: OpenPanel,
): Promise<IndexOverlayHandle> {
  const portal = portalForHost(location.hostname);
  if (portal == null) return { stop: () => {} };

  const cache = new Map<string, PortalListing>();

  let stopped = false;
  /* Whether dismissed properties are drawn barely visible (index_dock.ts). */
  let veil = await readVeil();
  let dock: DockHandle | null = null;
  let sawCards = false;  // the dock mounts with the first listing card, then stays
  let failure: LookupFailure | null = null;
  let failedAt: number | null = null;  // backoff anchor (monotonic); null = free to ask
  let cardsWaiting = false;  // the last pass saw possible sale cards with no row yet
  let signingIn = false;
  let retrying = false;
  let signInError: string | null = null;
  let notice: NoticeHandle | null = null;
  let round = 0;  // bumped by an account change, a retry or the tab coming back: see Rounds
  let inFlightRound: number | null = null;  // round of the lookup that is out
  let rescan = false;  // a pass skipped its lookup because this round's was out

  let timer: ReturnType<typeof setTimeout> | null = null;
  const schedule = (): void => {
    if (timer != null) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; void pass(); }, SCAN_DEBOUNCE_MS);
  };

  // performance.now, not Date.now: a wall-clock step back must not stretch it.
  const backingOff = (): boolean =>
    failedAt != null && performance.now() - failedAt < LOOKUP_RETRY_AFTER_MS;

  const handlers: NoticeHandlers = {
    signIn: () => { void signIn(); },
    retry: () => { void retry(); },
    reload: () => { location.reload(); },
    dismiss: () => { noticeDismissed = true; syncNotice(); },
  };

  /* The notice is a pure function of the state above: shown, updated in
   * place, or removed — never a second host. While an ask for the current
   * round is out, a notice that isn't up stays down (the answer decides;
   * raising the previous failure meanwhile would offer a retry that throws
   * that answer away); an error notice that is up reads busy ("Načítám…"),
   * a sign-in notice keeps its button (busy there means a sign-in is running). */
  function syncNotice(): void {
    const current = failure;
    if (stopped || noticeDismissed || current == null || !cardsWaiting) {
      notice?.destroy();
      notice = null;
      return;
    }
    if (notice != null && !notice.connected()) {
      // The portal re-rendered <body>'s children and took our host with them.
      notice.destroy();
      notice = null;
    }
    const looking = inFlightRound === round;
    if (notice == null) {
      if (looking || dock == null) return;
      notice = mountNotice(handlers, dock.slot);
    }
    notice.render({
      failure: current,
      busy: current.kind === 'signed_out' ? signingIn : looking || retrying,
      signInError,
    });
  }

  // What each card's listing IS; its curation went to the store as the lookup answered.
  function keep(rows: PortalListing[]): void {
    for (const l of rows) cache.set(l.source_id, l);
  }

  const promos = createPromoWash();
  const layer = createCardLayer({
    store,
    cache,
    badgeFor,
    openPanel: (ref, href, listing) => { void openPanel(ref, href, listing); },
    veilOn: () => veil,
    flash: (message) => { dock?.flash(message); },
    onRefresh: () => { dock?.setCount(layer.dismissedCount()); },
  });

  function applyVeil(on: boolean): void {
    veil = on;
    document.documentElement.setAttribute(VEIL_ROOT_ATTR, on ? '1' : '0');
    dock?.setVeil(on);
    layer.refresh();
  }

  /* The dock lives for as long as the page shows listing cards; a portal that
   * re-renders <body>'s children takes the host with it, so it is re-made. */
  function ensureDock(): void {
    if (dock != null && !dock.connected()) {
      notice?.destroy();
      notice = null;
      dock.destroy();
      dock = null;
    }
    if (dock != null) return;
    dock = mountDock(NOTICE_CSS, () => {
      applyVeil(!veil);
      writeVeil(veil);
    });
    dock.setVeil(veil);
  }

  /* The extension is gone from this tab (update, disable, removal): stop
   * scanning the page — the notice, if one is due, offers the reload. */
  function orphaned(detail: string): void {
    failure = { kind: 'reload', detail };
    failedAt = performance.now();
    obs.disconnect();
    document.removeEventListener('visibilitychange', onVisibility);
  }

  function settle(res: ApiResult<PortalListing[]>): void {
    if (res.ok) {
      keep(res.data);
      failure = null;
      failedAt = null;
      signInError = null;
      return;
    }
    const kind = failureKind(res.detail);
    if (kind === 'reload') {
      orphaned(res.detail);
      return;
    }
    if (failure?.kind !== kind) signInError = null;
    failure = { kind, detail: res.detail };
    failedAt = performance.now();
  }

  async function lookup(hits: Hit[]): Promise<void> {
    const ids = [...new Set(
      hits.filter((h) => !cache.has(h.ref.sourceId)).map((h) => h.ref.sourceId),
    )].slice(0, MAX_LOOKUP_PER_PASS);
    if (ids.length === 0 || backingOff()) return;
    if (inFlightRound === round) {
      rescan = true;
      return;
    }
    const asked = round;
    inFlightRound = asked;
    syncNotice();  // a notice already up reads busy while this ask is out
    const answer = store.lookup(ids.map((id) => ({ source: portal!.source, source_id: id })));
    let deadline: ReturnType<typeof setTimeout> | undefined;
    const late = new Promise<null>((resolve) => {
      deadline = setTimeout(() => resolve(null), LOOKUP_TIMEOUT_MS);
    });
    const res = await Promise.race([answer, late]);
    clearTimeout(deadline);
    if (inFlightRound === asked) inFlightRound = null;
    if (stopped) return;
    // Superseded: the newer round decides the notice, but its rows still count.
    const current = asked === round;
    if (res == null) {
      if (current) {
        settle({ ok: false, status: 0, detail: `Server neodpověděl do ${LOOKUP_TIMEOUT_MS / 1000} s` });
      }
      void answer.then((eventual) => {
        if (stopped || !eventual.ok) return;
        if (asked === round) settle(eventual);
        else keep(eventual.data);
        schedule();
      });
    } else if (current) {
      settle(res);
    } else if (res.ok) {
      keep(res.data);
      schedule();
    }
    if (current && rescan) {
      rescan = false;
      schedule();
    }
  }

  async function pass(): Promise<void> {
    if (stopped) return;
    let hits = collectHits(portal!.source);
    if (hits.length > 0) {
      sawCards = true;
      ensureDock();
      await lookup(hits);
      if (stopped) return;
      /* The page can change while the lookup is out (a soft-nav to a page
       * without cards, a filter swapping one result set for another): the
       * notice and the cards act on the page as it is now. */
      hits = collectHits(portal!.source);
    }
    if (sawCards) ensureDock();

    cardsWaiting = hits.some((h) => !cache.has(h.ref.sourceId));
    syncNotice();

    // Every listing card on the page: those mounted earlier, and this pass's.
    const cards = new Set(document.querySelectorAll<HTMLElement>(`[${PROCESSED_ATTR}]`));
    for (const hit of hits) {
      const card = cardFor(hit.anchor, hit.ref.sourceId);
      cards.add(card);
      if (cache.has(hit.ref.sourceId)) layer.mount(card, hit.ref, hit.href);
      else unbadgeRecycled(hit, card, layer.clear);
    }
    layer.refresh();  // drops cards the portal removed; recounts the dismissed
    promos.update(cards);
  }

  /* The same background-owned PKCE sign-in the panel's prompt runs (a content
   * script can't reach chrome.identity) — unless a session already exists,
   * made in the panel or another tab. A session arriving is an account change,
   * which asks again (below). */
  async function signIn(): Promise<void> {
    if (signingIn) return;
    signingIn = true;
    signInError = null;
    syncNotice();
    try {
      const auth = await store.call<AuthState>({ type: 'get_auth_state' });
      if (stopped || (auth.ok && auth.data.signedIn)) return;
      if (!auth.ok && failureKind(auth.detail) === 'reload') {
        orphaned(auth.detail);
        return;
      }
      const res = await store.call<undefined>({ type: 'sign_in' });
      if (stopped || res.ok) return;
      if (failureKind(res.detail) === 'reload') orphaned(res.detail);
      else signInError = `Přihlášení selhalo: ${res.detail}`;
    } finally {
      signingIn = false;
      syncNotice();
    }
  }

  /* Asks now, past the backoff and past a lookup that is still out; the
   * button reads "Načítám…" until this lookup settles, for at least
   * RETRY_MIN_BUSY_MS. */
  async function retry(): Promise<void> {
    if (retrying) return;
    retrying = true;
    failedAt = null;
    round++;
    syncNotice();
    try {
      await Promise.all([pass(), sleep(RETRY_MIN_BUSY_MS)]);
    } finally {
      retrying = false;
      syncNotice();
    }
  }

  /* Coming back to this tab: the operator may have signed in (or the outage
   * ended) meanwhile — ask again now rather than after the backoff, past any
   * outage-era request still hanging (its failure must not re-arm the backoff
   * over this fresh ask; its rows, should it answer, still count). */
  const onVisibility = (): void => {
    if (stopped || failure == null || document.visibilityState !== 'visible') return;
    failedAt = null;
    if (inFlightRound === round) round++;
    schedule();
  };

  /* A write here or in the panel, a list read, a lookup: the cards repaint from
   * the store (each card only when what it draws changed). */
  const unsubscribeStore = store.subscribe(() => { if (!stopped) layer.refresh(); });

  /* Every row and control here was read as the account that just left (the
   * store dropped its curation and lists): drop them and ask again (a
   * signed-out answer raises the notice). */
  const unsubscribeAccount = store.onAccountChange(() => {
    if (stopped) return;
    round++;
    cache.clear();
    layer.reset();
    failure = null;
    failedAt = null;
    signInError = null;
    void pass();
  });

  document.documentElement.setAttribute(VEIL_ROOT_ATTR, veil ? '1' : '0');
  // Flipped in another tab, or on another portal: this page follows.
  const unsubscribeVeil = onVeilChange((on) => { if (!stopped) applyVeil(on); });

  const obs = new MutationObserver(schedule);
  obs.observe(document.body, { childList: true, subtree: true });
  document.addEventListener('visibilitychange', onVisibility);
  void pass();

  return {
    stop(): void {
      stopped = true;
      obs.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
      unsubscribeVeil();
      unsubscribeAccount();
      unsubscribeStore();
      if (timer != null) clearTimeout(timer);
      /* The controls go with the overlay: a later run starts from an empty
       * cache, and a card still marked as mounted would be skipped by it. */
      layer.reset();
      promos.reset();
      syncNotice();
      dock?.destroy();
      dock = null;
    },
  };
}

function collectHits(source: string): Hit[] {
  const hits: Hit[] = [];
  const anchors = document.querySelectorAll<HTMLAnchorElement>('a[href]');
  for (const anchor of Array.from(anchors)) {
    const ref = detailRef(anchor.href, location.hostname);
    if (ref == null || ref.source !== source) continue;
    // Skip only if this card is already mounted for THIS listing (see PROCESSED_ATTR).
    if (anchor.closest(`[${PROCESSED_ATTR}]`)?.getAttribute(PROCESSED_ATTR) === ref.sourceId) {
      continue;
    }
    hits.push({ ref, anchor, href: anchor.href });
  }
  return hits;
}

/* The card a listing link belongs to: the nearest card-ish ancestor — then
 * outward through the list items / articles around it for as long as they
 * still hold this ONE listing. A photo carousel renders the listing's link once
 * per slide, each inside its own card-ish node (bezrealitky): without the climb
 * every slide got its own controls, riding away with its photo, and the title
 * link a further set on the article around them. Only `li` / `article` are
 * climbed to — a class-name match that far out is a results wrapper as often
 * as a card. */
function cardFor(anchor: HTMLAnchorElement, sourceId: string): HTMLElement {
  const near = anchor.closest<HTMLElement>(
    'li, article, [class*="item"], [class*="card"], [class*="result"], [class*="estate"]',
  ) ?? anchor.parentElement ?? anchor;
  let card = near;
  for (
    let outer = near.parentElement?.closest<HTMLElement>('li, article');
    outer != null && holdsOneListing(outer, sourceId);
    outer = outer.parentElement?.closest<HTMLElement>('li, article')
  ) {
    card = outer;
  }
  return card;
}

/* A card still waiting on its lookup whose node was recycled from another
 * listing (see PROCESSED_ATTR) drops that listing's controls and click targets
 * now, not on the next successful lookup — a failing or backed-off lookup would
 * leave them up, writing to the wrong property, for a whole backoff window. A
 * card that still links the mounted listing wasn't recycled, just holds two
 * links; it is left alone. */
function unbadgeRecycled(hit: Hit, card: HTMLElement, clear: (card: HTMLElement) => void): void {
  const prevId = card.getAttribute(PROCESSED_ATTR);
  if (prevId == null || prevId === hit.ref.sourceId) return;
  const stillLinked = Array.from(card.querySelectorAll<HTMLAnchorElement>('a[href]'))
    .some((a) => detailRef(a.href, location.hostname)?.sourceId === prevId);
  if (!stillLinked) clear(card);
}

/* The yield badge — sale apartments we hold only, by our row's category. A
 * value's or a range's yield on the badge; the result's note (the range's (i),
 * or a reason) rides in its title — the badge falls through to the estimate or
 * the CTA when there is no MF yield to show. */
function badgeFor(listing: PortalListing): BadgeView | null {
  if (!listing.found) return null;
  if (listing.category_main !== 'byt' || listing.category_type !== 'prodej') return null;

  let title = 'Klikni pro odhad výnosu';
  const shape = mfShape(listing.mf_reference_rent);
  if (shape.kind === 'value') {
    title = `MF nájem ${fmtCzk(shape.ref.monthly_rent_czk)}/měs · klikni pro odhad`;
  } else if (shape.kind !== 'none' && shape.note != null) {
    title = shape.note;
  }
  const mfYield = mfYieldText(listing.mf_gross_yield_pct, listing.mf_reference_rent);
  if (mfYield != null) return { kind: 'yield', text: `Výnos MF ${mfYield}`, title };
  if (listing.latest_estimation?.gross_yield_pct != null) {
    return { kind: 'est', text: `Odhad ${fmtPct(listing.latest_estimation.gross_yield_pct)}`, title };
  }
  return { kind: 'cta', text: 'Odhadnout výnos', title };
}

/* The notice's stylesheet, handed to the dock: it mounts in the dock's closed
 * shadow root (which positions it, keeps the portal's CSS out and carries the
 * app's tokens): a popover surface, the 2px accent "filed" top edge, an accent
 * button. Font sizes in px, so a portal's root font size cannot shrink them. */
const NOTICE_CSS = `
  .__mf_notice {
    box-sizing: border-box; width: max-content;
    max-width: min(21rem, calc(100vw - 2.5rem));
    padding: 0.6rem 0.85rem 0.8rem;
    font-size: 13px; line-height: 1.45; text-align: left; color: var(--color-ink);
    background: var(--color-paper-3); border: 1px solid var(--color-rule-strong);
    border-radius: var(--radius-md);
    box-shadow: inset 0 2px 0 var(--color-copper), 0 10px 34px -10px rgba(28, 20, 10, 0.30);
    transition: opacity 140ms ease;
  }
  /* Transparent, not hidden: the empty live region must already be in the
   * accessibility tree when its first text arrives. */
  .__mf_notice.is-pending { opacity: 0; pointer-events: none; }
  @media (prefers-reduced-motion: reduce) { .__mf_notice { transition: none; } }
  .n-head {
    display: flex; align-items: center; justify-content: space-between;
    gap: 1rem; margin-bottom: 0.35rem;
  }
  .n-mark { display: inline-flex; align-items: center; gap: 0.45rem; }
  .n-tick { width: 0.5rem; height: 0.5rem; background: var(--color-copper); flex-shrink: 0; }
  .n-word {
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.2em; color: var(--color-copper);
  }
  .n-close {
    margin: -0.2rem -0.3rem -0.2rem 0; padding: 0 0.2rem;
    font: inherit; font-size: 18px; line-height: 1; color: var(--color-ink-3);
    background: transparent; border: 0; cursor: pointer;
  }
  .n-close:hover { color: var(--color-ink); }
  .n-text { margin: 0; }
  .n-detail, .n-error {
    margin: 0.15rem 0 0; font-size: 12px; overflow-wrap: anywhere;
  }
  .n-detail { color: var(--color-ink-3); }
  .n-error { color: var(--color-brick); }
  .n-primary {
    display: block; width: 100%; margin-top: 0.6rem; padding: 0.5rem 0.9rem;
    font: inherit; font-size: 12px; font-weight: 600;
    color: var(--color-on-copper); background: var(--color-copper); border: 0;
    border-radius: var(--radius-sm); cursor: pointer; transition: background 120ms ease;
  }
  .n-primary:hover { background: var(--color-copper-2); }
  .n-primary[aria-disabled="true"] { background: var(--color-ink-3); cursor: wait; }
  .n-close:focus-visible, .n-primary:focus-visible {
    outline: 2px solid var(--color-focus); outline-offset: 2px;
  }
`;

/* Built once, then updated in place: the live region keeps its node (so screen
 * readers announce changes, not a remount) and a focused button keeps focus —
 * busy is aria-disabled, because `disabled` would drop focus to <body>, behind
 * every focusable element of the portal page. The first text waits
 * LIVE_REGION_SETTLE_MS after mount (see there). */
function mountNotice(on: NoticeHandlers, slot: HTMLElement): NoticeHandle {
  const box = h('div', '__mf_notice is-pending');
  const head = h('div', 'n-head');
  const mark = h('span', 'n-mark');
  mark.append(h('span', 'n-tick'), h('span', 'n-word', APP_NAME));
  const close = h('button', 'n-close', '×');
  close.title = 'Skrýt';
  close.setAttribute('aria-label', 'Skrýt');
  close.onclick = on.dismiss;
  head.append(mark, close);

  const body = h('div', 'n-body');
  body.setAttribute('role', 'status');
  body.setAttribute('aria-live', 'polite');
  const text = h('p', 'n-text');
  const detail = h('p', 'n-detail');
  const error = h('p', 'n-error');
  body.append(text, detail, error);

  let live = false;  // the first text is in (the box is transparent until then)
  let kind: FailureKind = 'error';
  let busy = false;
  const action = h('button', 'n-primary', '');
  action.onclick = () => {
    if (!live) return;
    if (kind === 'reload') { on.reload(); return; }
    if (busy) return;
    if (kind === 'signed_out') on.signIn();
    else if (kind === 'error') on.retry();
  };

  box.append(head, body, action);
  slot.replaceChildren(box);

  let latest: NoticeView | null = null;
  let settleTimer: ReturnType<typeof setTimeout> | null = null;
  const frame = requestAnimationFrame(() => {
    settleTimer = setTimeout(() => {
      live = true;
      box.classList.remove('is-pending');
      if (latest != null) paint(latest);
    }, LIVE_REGION_SETTLE_MS);
  });

  function paint(view: NoticeView): void {
    kind = view.failure.kind;
    busy = view.busy;
    const signedOut = kind === 'signed_out';
    setText(text, signedOut
      ? 'Pro výnosy a ukládání na kartách se prosím přihlaste.'
      : 'Data ke kartám se nepodařilo načíst.');
    /* The detail line sits in the live region: "Načítám…" and then the error
     * again is what tells a screen reader (and the eye) a retry ran and failed. */
    const detailLine = signedOut ? '' : busy ? 'Načítám…' : view.failure.detail;
    setText(detail, detailLine);
    detail.hidden = detailLine === '';
    const failLine = signedOut ? view.signInError : null;
    setText(error, failLine ?? '');
    error.hidden = failLine == null;
    if (busy) action.setAttribute('aria-disabled', 'true');
    else action.removeAttribute('aria-disabled');
    action.hidden = kind === 'fatal';
    setText(action, {
      signed_out: busy ? 'Přihlašuji…' : 'Přihlásit se přes Google',
      reload: 'Obnovit stránku',
      error: busy ? 'Načítám…' : 'Zkusit znovu',
      fatal: '',
    }[kind]);
  }

  return {
    render(view: NoticeView): void {
      latest = view;
      if (live) paint(view);
    },
    connected: () => box.isConnected,
    destroy(): void {
      cancelAnimationFrame(frame);
      if (settleTimer != null) clearTimeout(settleTimer);
      box.remove();
    },
  };
}

/* Only on change — rewriting a live region's text re-announces it, and passes
 * re-render the notice on every DOM mutation while it is up. */
function setText(node: HTMLElement, value: string): void {
  if (node.textContent !== value) node.textContent = value;
}
