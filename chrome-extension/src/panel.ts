/* The listing panel: what the side sheet shows for one portal ad and the flows
 * behind it — the session check, the lookup, the price history, sign-in/out and
 * the add-to-database flow. The sheet's frame is sheet.ts; the header, the three
 * cards and the other states are their own modules, drawn from the one store
 * (curation, lists, writes) and this controller's state.
 *
 * Guards: every panel open is a new epoch, and anything resuming after an `await`
 * applies only while its epoch is current — so listing A's lookup or history never
 * paints into listing B's sheet; the cards guard their own reads by property. */

import type { PortalRef } from '../../frontend/src/lib/portalIds';
import { APP_BASE_URL, NOT_SIGNED_IN_DETAIL, friendlyDetail } from './config';
import { failureKind } from './index_overlay';
import { isAdminGate, outcomeMessage, runIngest, type IngestStep } from './ingest_flow';
import { POLL_INTERVAL_MS, POLL_MAX_ATTEMPTS, createAgents } from './panel_agents';
import { header, tabMarks } from './panel_header';
import { createMine } from './panel_mine';
import { adMove, priceCard, priceSeries, type HistoryView } from './panel_price';
import { errorCard, notFoundCard, signedOutCard, skeleton } from './panel_states';
import { mountSheet, type Sheet } from './sheet';
import { sleep, type Store } from './store';
import type { AuthState, IngestState, PortalListing, PriceHistory } from './types';
import type { PriceSeries } from '../../frontend/src/lib/priceHistory';

type Phase = 'loading' | 'signed_out' | 'not_found' | 'active' | 'error';

interface PanelState {
  phase: Phase;
  email: string | null;
  /* The lookup row; its curation is read from the store at render. */
  listing: PortalListing | null;
  /* Null for an ad not yet attached to a property (nothing to chart yet). */
  history: HistoryView | null;
  error: string | null;
  signingIn: boolean;
  ingestStep: IngestStep | null;
  ingestMessage: string | null;
  accountOpen: boolean;
}

const initial = (): PanelState => ({
  phase: 'loading', email: null, listing: null, history: null, error: null,
  signingIn: false, ingestStep: null, ingestMessage: null, accountOpen: false,
});

export function createPanel(store: Store) {
  let sheet: Sheet | null = null;
  let state = initial();
  let epoch = 0;
  let ref: PortalRef | null = null;
  /* The ad the panel represents — what "Přidat do databáze" sends (a search
   * card's href when opened from one, not the search page). */
  let url = '';
  /* The first admin-gate answer hides the add button until the account changes. */
  let ingestForbidden = false;
  /* Derived once per history answer: the tracks and this ad's last move. */
  let series: PriceSeries[] | null = null;

  const repaint = (): void => { if (sheet != null) paint(sheet); };
  /* Only after the operator's own action in the sheet: focus is already inside it. */
  const focus = (key: string): void => {
    sheet?.shadow.querySelector<HTMLElement>(`[data-key="${key}"]`)?.focus();
  };
  const mine = createMine({ store, repaint, focus });
  const agents = createAgents({ store, repaint, shadow: () => sheet?.shadow ?? null });

  const set = (patch: Partial<PanelState>): void => {
    state = { ...state, ...patch };
    repaint();
  };
  const setIf = (at: number, patch: Partial<PanelState>): void => { if (at === epoch) set(patch); };

  /* The property the sheet shows, if one we hold. */
  const shown = (): number | null => (state.listing?.found ? state.listing.property_id : null);

  /* The lookup row with its live curation from the store. */
  function view(): PortalListing | null {
    const l = state.listing;
    const pid = shown();
    const curation = pid != null ? store.curation(pid) : null;
    return l != null && curation != null ? { ...l, ...curation } : l;
  }

  store.subscribe((changed) => {
    const pid = shown();
    if (changed == null || (pid != null && changed.includes(pid))) repaint();
  });

  function body(): Node[] {
    switch (state.phase) {
      case 'loading':
        return skeleton();
      case 'signed_out':
        return [signedOutCard(state.signingIn, state.error, () => { void signIn(); })];
      case 'error': {
        const message = state.error ?? 'Něco se nepovedlo.';
        const again = ref != null && failureKind(message) === 'error' ? () => { void open(ref as PortalRef, url); } : null;
        return [errorCard(message, again)];
      }
      case 'not_found':
        return [notFoundCard(state.ingestStep, state.ingestMessage, ingestForbidden, () => { void ingest(); })];
      case 'active': {
        const l = view() as PortalListing;
        const nodes: Node[] = [header(l, adMove(series, l.listing_id))];
        if (state.history != null) {
          nodes.push(priceCard(state.history, series, l.canonical_listing_id, () => { void loadHistory(epoch); }));
        }
        const own = mine.card(l);
        if (own != null) nodes.push(own);
        nodes.push(agents.card(l));
        return nodes;
      }
    }
  }

  function paint(s: Sheet): void {
    const l = view();
    s.paint({
      appHref: l?.found && APP_BASE_URL
        // The app's natural-key route: lands on the property page with this ad open.
        ? `${APP_BASE_URL}/listing/${encodeURIComponent(l.source)}/${encodeURIComponent(l.source_id)}`
        : null,
      email: state.email,
      accountOpen: state.accountOpen,
      onAccount: (open) => set({ accountOpen: open }),
      onOutside: () => {
        if (state.accountOpen) set({ accountOpen: false });
        mine.closeMenus();
      },
      onSignOut: () => { void signOut(); },
      body,
      tab: () => tabMarks(state.phase === 'active' ? l : null, l ? adMove(series, l.listing_id) : null,
        store.list('stages').data),
    });
  }

  async function loadHistory(at: number): Promise<void> {
    const pid = shown();
    if (pid == null) return;
    series = null;
    setIf(at, { history: { status: 'loading' } });
    const res = await store.retry(() => store.call<PriceHistory>({ type: 'get_price_history', property_id: pid }));
    if (res == null || at !== epoch || state.listing == null) return;
    series = res.ok ? priceSeries(state.listing, res.data, Date.now()) : null;
    set({ history: res.ok ? { status: 'ok', data: res.data } : { status: 'failed' } });
  }

  /* Paint one lookup row and start what a held ad needs; shared with the add flow. */
  function show(at: number, listing: PortalListing | null): void {
    if (at !== epoch) return;
    const held = listing?.found ? listing : null;
    series = null;
    mine.reset(held);
    agents.reset(held);
    set({
      phase: held != null ? 'active' : 'not_found', listing,
      history: held?.property_id != null ? { status: 'loading' } : null,
      ingestStep: null, ingestMessage: null,
    });
    if (held?.property_id != null) void loadHistory(at);
  }

  /* Opens (or re-opens) the sheet for one ad: the detail page's own, or a search
   * card's (whose lookup row is handed over, and whose click unfolds the sheet). */
  async function open(next: PortalRef, href: string, prefetched?: PortalListing | null): Promise<void> {
    const at = ++epoch;
    ref = next;
    url = href;
    state = { ...initial(), email: state.email };
    series = null;
    if (sheet == null || !sheet.connected()) {
      // A portal that dropped the host leaves the old sheet's page listener behind: take it off.
      sheet?.remove();
      sheet = null;
      const mounted = await mountSheet(repaint);
      if (at !== epoch) {
        mounted.remove();
        return;
      }
      sheet = mounted;
    }
    if (prefetched !== undefined && sheet.folded()) sheet.setFolded(false);
    repaint();

    const auth = await store.call<AuthState>({ type: 'get_auth_state' });
    if (!auth.ok) {
      // The background is unreachable (an extension reload orphaned this tab): say so.
      setIf(at, { phase: 'error', error: auth.detail });
      return;
    }
    if (!auth.data.signedIn) {
      setIf(at, { phase: 'signed_out', email: null });
      return;
    }
    setIf(at, { email: auth.data.email });

    let listing: PortalListing | null;
    if (prefetched !== undefined) {
      listing = prefetched;
    } else {
      const res = await store.lookup([{ source: next.source, source_id: next.sourceId }]);
      if (!res.ok) {
        if (res.detail === NOT_SIGNED_IN_DETAIL) setIf(at, { phase: 'signed_out', email: null });
        else setIf(at, { phase: 'error', error: res.detail });
        return;
      }
      listing = res.data[0] ?? null;
    }
    show(at, listing);
  }

  /* The page left for one with no panel. */
  function close(): void {
    epoch++;
    sheet?.remove();
    sheet = null;
  }

  async function ingest(): Promise<void> {
    const at = epoch;
    const outcome = await runIngest(url, {
      call: (message) => store.call<IngestState>(message),
      sleep,
      alive: () => at === epoch,
      onStep: (step) => setIf(at, { ingestStep: step, ingestMessage: null }),
      intervalMs: POLL_INTERVAL_MS,
      maxPolls: POLL_MAX_ATTEMPTS,
    });
    if (outcome.kind === 'cancelled') return;
    if (outcome.kind !== 'held') {
      if (isAdminGate(outcome)) ingestForbidden = true;
      setIf(at, { ingestStep: null, ingestMessage: outcomeMessage(outcome, friendlyDetail) });
      return;
    }
    const res = await store.lookup([{ source: outcome.source, source_id: outcome.nativeId }]);
    if (at !== epoch) return;
    if (!res.ok) {
      set({ ingestStep: null, ingestMessage: `Načtení inzerátu selhalo: ${friendlyDetail(res.detail)}` });
      return;
    }
    show(at, res.data[0] ?? null);
  }

  /* The background owns chrome.identity; the session landing is an account change. */
  async function signIn(): Promise<void> {
    set({ signingIn: true, error: null });
    const res = await store.call<undefined>({ type: 'sign_in' });
    if (!res.ok) set({ signingIn: false, phase: 'signed_out', error: `Přihlášení selhalo: ${res.detail}` });
  }

  async function signOut(): Promise<void> {
    const res = await store.call<undefined>({ type: 'sign_out' });
    if (!res.ok) set({ phase: 'error', error: friendlyDetail(res.detail) });
  }

  /* The sheet belongs to the account that read it: after the store dropped what that
   * account read, it re-reads for the new one or prompts. */
  store.onAccountChange((user) => {
    ingestForbidden = false;
    if (ref == null || sheet == null || !sheet.connected()) return;
    if (user != null) {
      void open(ref, url);
      return;
    }
    epoch++;
    series = null;
    set({ ...initial(), phase: 'signed_out' });
  });

  return { open, close };
}
