/* The edge tab on a listing's detail page: the side panel's opener (Chrome opens
 * its side panel only on a user action — this tab, a search card or the toolbar
 * icon). A tab on the right edge in a closed shadow root: the PS mark, the deal's
 * stage badge, this ad's price drop and the MF yield, read through the page's
 * store; hidden while the side panel is open in this window (the drop's price
 * history is read only while the tab shows). Under an extension update it offers
 * the page reload that reconnects it. Depth is its border, bar the one shadow that
 * lifts it off the portal page, as the sheet's folded tab had. */

import tokens from '../../frontend/src/styles/tokens.css?inline';
import { APP_NAME, APP_SHORT } from '../../frontend/src/lib/brand';
import { MF_YIELD_TITLE, mfYieldText } from '../../frontend/src/lib/mfReference';
import type { PortalRef } from '../../frontend/src/lib/portalIds';
import type { PipelineStage } from '../../frontend/src/lib/types';
import { themeHost } from './appearance';
import { EXTENSION_RELOADED_DETAIL } from './config';
import { h, keepFocus } from './dom';
import { codeBadge, moveBadge, stageLook } from './glyphs';
import { tabMarkList } from './panel_logic';
import { adMove, priceSeries } from './panel_price';
import type { Store } from './store';
import type { PortalListing, PriceHistory } from './types';

const HOST_ID = '__propscout_tab__';

const CSS = `
  ${tokens}
  :host { all: initial; }
  .tab {
    position: fixed; z-index: 2147483000; top: 96px; right: 0; min-width: 56px; margin: 0; padding: 10px 4px 12px;
    display: flex; flex-direction: column; align-items: center; gap: 8px; cursor: pointer;
    font: 13px/18px ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    color: var(--color-ink); -webkit-font-smoothing: antialiased; text-align: center;
    background: var(--color-paper-2); border: 1px solid var(--color-rule); border-right: 0;
    border-radius: var(--radius-md) 0 0 var(--radius-md);
    box-shadow: 0 1px 2px rgba(20, 22, 26, 0.06), 0 8px 24px rgba(20, 22, 26, 0.12);
  }
  .tab:hover { background: var(--color-paper-3); }
  .tab:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
  .tab-mark {
    width: 32px; height: 32px; border-radius: var(--radius-sm);
    display: inline-flex; align-items: center; justify-content: center;
    font: 600 14px ui-serif, Georgia, serif; color: var(--color-chrome-accent); background: var(--color-chrome);
  }
  .mono, .code, .move {
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-variant-numeric: tabular-nums;
  }
  .l4 {
    font-size: 11px; line-height: 14px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--color-ink-3);
  }
  .tab-mf { display: flex; flex-direction: column; gap: 1px; font-size: 11px; line-height: 14px; }
  .tab-mf .mono { font-weight: 600; }
  .code { font-size: 11px; line-height: 14px; font-weight: 500; padding: 1px 5px; border-radius: var(--radius-xs); }
  .move {
    height: 20px; padding: 0 4px; border-radius: var(--radius-xs); display: inline-flex; align-items: center;
    white-space: nowrap; font-size: 11px; line-height: 16px; font-weight: 600;
    color: var(--color-sage); background: var(--color-sage-soft);
  }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
`;

/* After the PS mark: the deal's stage badge, this ad's price drop, MF. */
function marks(l: PortalListing | null, move: number | null, stages: readonly PipelineStage[] | null): Node[] {
  if (l == null || !l.found) return [];
  const look = stageLook(l.pipeline);
  const code = look && codeBadge(look, stages);
  if (code != null) code.title = `V pipeline: ${l.pipeline?.stage_label ?? ''}`;
  const mf = mfYieldText(l.mf_gross_yield_pct, l.mf_reference_rent);
  let box: HTMLElement | null = null;
  if (mf != null) {
    box = h('span', 'tab-mf');
    box.title = MF_YIELD_TITLE;
    box.append(h('span', 'l4', 'MF'), h('span', 'mono', mf));
  }
  return tabMarkList<Node>(code, move, box, moveBadge);
}

interface Mount {
  host: HTMLElement;
  shadow: ShadowRoot;
  box: HTMLElement;
}

export function createEdgeTab(store: Store, onOpen: () => void) {
  let ref: PortalRef | null = null;
  /* This ad's lookup row (its curation is the store's at paint) and its last price move. */
  let listing: PortalListing | null = null;
  let move: number | null = null;
  let panelOpen = false;
  /* The extension was updated or reloaded under this page: this script can reach nothing. */
  let orphaned = false;
  /* Every ad shown is a new epoch: an answer for the last one never paints. */
  let epoch = 0;
  /* The epoch whose price history was asked for. */
  let historyAt = -1;
  let mount: Mount | null = null;

  /* Made on the first paint, and again after a portal that rebuilds <html>'s children took it. */
  function mounted(): Mount {
    if (mount?.host.isConnected) return mount;
    const host = h('div', '');
    host.id = HOST_ID;
    themeHost(host);
    document.documentElement.appendChild(host);
    const shadow = host.attachShadow({ mode: 'closed' });
    const style = h('style', '');
    style.textContent = CSS;
    const box = h('div', '');
    shadow.append(style, box);
    mount = { host, shadow, box };
    return mount;
  }

  function paint(): void {
    if (ref == null) return;
    const { shadow, box } = mounted();
    const pid = listing?.found ? listing.property_id : null;
    const curation = pid != null ? store.curation(pid) : null;
    const view = listing != null && curation != null ? { ...listing, ...curation } : listing;
    keepFocus(shadow, () => {
      if (panelOpen && !orphaned) {
        box.replaceChildren();
        return;
      }
      const tab = h('button', 'tab');
      tab.dataset.key = 'open';
      // Named by its content (no aria-label), so the marks it shows are announced too.
      const mark = h('span', 'tab-mark', APP_SHORT);
      mark.setAttribute('aria-hidden', 'true');
      if (orphaned) {
        tab.title = EXTENSION_RELOADED_DETAIL;
        tab.append(h('span', 'sr', `${APP_NAME} — ${EXTENSION_RELOADED_DETAIL}`), mark, h('span', 'l4', 'Obnovit'));
        tab.onclick = () => location.reload();
      } else {
        tab.title = 'Otevřít panel';
        const shown = marks(view, move, store.list('stages').data);
        tab.append(h('span', 'sr', `${APP_NAME} — otevřít panel`), mark, ...shown);
        tab.onclick = () => onOpen();
      }
      box.replaceChildren(tab);
    });
  }

  /* This ad's last price move, read once per ad and only while the tab shows. */
  async function loadMove(): Promise<void> {
    const row = listing;
    const at = epoch;
    if (panelOpen || historyAt === at || !row?.found || row.property_id == null) return;
    historyAt = at;
    const history = await store.call<PriceHistory>({ type: 'get_price_history', property_id: row.property_id });
    if (at !== epoch || !history.ok) return;
    move = adMove(priceSeries(row, history.data, Date.now()), row.listing_id);
    paint();
  }

  /* This page's ad: its marks are read afresh. */
  async function show(next: PortalRef): Promise<void> {
    const at = ++epoch;
    ref = next;
    listing = null;
    move = null;
    paint();
    const res = await store.lookup([{ source: next.source, source_id: next.sourceId }]);
    if (at !== epoch || !res.ok) return;
    const row = res.data[0] ?? null;
    listing = row;
    if (row?.pipeline?.in_pipeline) store.ensure('stages');
    paint();
    await loadMove();
  }

  store.subscribe((changed) => {
    const pid = listing?.found ? listing.property_id : null;
    if (changed == null || (pid != null && changed.includes(pid))) paint();
  });
  // The marks belong to the account that read them.
  store.onAccountChange(() => { if (ref != null) void show(ref); });

  return {
    show,
    /* The page left for one that is no listing. */
    remove(): void {
      epoch++;
      ref = null;
      listing = null;
      mount?.host.remove();
      mount = null;
    },
    /* The side panel opened or closed in this window. */
    setPanelOpen(open: boolean): void {
      panelOpen = open;
      paint();
      void loadMove();
    },
    setOrphaned(): void {
      orphaned = true;
      paint();
    },
  };
}
