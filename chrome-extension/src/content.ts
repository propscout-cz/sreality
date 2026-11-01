/* The content script on every portal we scrape: the entry and the router.
 *
 * Each tab owns its SUBJECT — what the side panel (sidepanel.ts) shows while this
 * tab is its window's active one: a detail page's ad, or the search card last
 * opened in the panel; none on any other page. A detail page also gets the edge
 * tab (edge_tab.ts), the panel's opener; any other page on a portal host gets the
 * search-page overlay (index_overlay.ts: per-card controls and the corner dock).
 * Both draw from this page's store (store.ts), over the one transport (transport.ts):
 * every network call goes to the background worker, which calls from the
 * extension's origin, not the portal page's; this script never fetches. The
 * side panel of this tab's window holds a port to it while open: the subject goes up
 * it, and each side re-reads a property the other wrote. */

import { detailRef, portalForHost, type PortalRef } from '../../frontend/src/lib/portalIds';
import { followAppearance } from './appearance';
import { PANEL_PORT } from './config';
import { createEdgeTab } from './edge_tab';
import { runIndexOverlay, type IndexOverlayHandle } from './index_overlay';
import { onAccountChange } from './session';
import { createStore, sleep } from './store';
import { send } from './transport';
import type { PanelCall, PanelSubject, PortMessage, RouteMessage } from './types';

let subject: PanelSubject | null = null;
/* The side panel of this tab's window, while it is open. */
let panelPort: chrome.runtime.Port | null = null;

function post(message: PortMessage): void {
  try { panelPort?.postMessage(message); } catch { /* the panel closed, or this script was orphaned */ }
}

/* Chrome opens its side panel only on a user action, so a click calls this at once:
 * the background opens the panel while the gesture is live. Opened or not, the panel
 * of this window connects to a tab that calls. */
function callPanel(open: boolean): void {
  const message: PanelCall = { type: 'panel', open };
  try {
    chrome.runtime.sendMessage(message).catch(() => { /* nobody answers a call */ });
  } catch {
    // Orphaned (chrome.runtime.id is gone): an extension update or reload under this page.
    edge.setOrphaned();
  }
}

function setSubject(next: PanelSubject | null): void {
  subject = next;
  post({ type: 'subject', subject });
}

const store = createStore({
  send, sleep, onAccountChange, written: (propertyId) => post({ type: 'property_written', property_id: propertyId }),
});
const edge = createEdgeTab(store, () => callPanel(true));

/* What a URL means to US — the LISTING it shows, not its query string. SPAs
 * rewrite the URL for things that don't change the subject (a gallery lightbox,
 * tracking params); keying on the raw href would re-open the panel on each and
 * lose a half-typed note or an edited assumption. Index pages collapse to one key:
 * paginating doesn't restart the overlay, whose observer picks up new cards. */
function routeKey(url: string): string {
  const ref = detailRef(url);
  return ref != null ? `detail:${ref.source}:${ref.sourceId}` : 'index';
}

let lastRouteKey: string | null = null;
let indexOverlay: IndexOverlayHandle | null = null;

/* Re-entrant: several portals are SPAs that navigate through the History API, so
 * the background relays each soft navigation (route_changed) and this re-decides. */
function renderForUrl(url: string): void {
  const key = routeKey(url);
  if (key === lastRouteKey) return;
  lastRouteKey = key;
  indexOverlay?.stop();
  indexOverlay = null;

  const ref = detailRef(url);
  if (ref != null) {
    setSubject({ ref, url });
    edge.show(ref).catch((err: unknown) => { console.error('[mf-ext] edge tab failed', err); });
    return;
  }
  setSubject(null);
  edge.remove();
  if (portalForHost(window.location.hostname) == null) return;
  const openCard = (cardRef: PortalRef, href: string): void => {
    setSubject({ ref: cardRef, url: href });
    callPanel(true);
  };
  runIndexOverlay(store, openCard)
    .then((overlay) => {
      // Another route change landed while the overlay was starting: adopt nothing.
      if (lastRouteKey !== key) overlay.stop();
      else indexOverlay = overlay;
    })
    .catch((err: unknown) => { console.error('[mf-ext] index overlay failed', err); });
}

chrome.runtime.onMessage.addListener((message: RouteMessage) => {
  if (message.type === 'route_changed') renderForUrl(message.url);
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== PANEL_PORT) return;
  panelPort = port;
  port.onMessage.addListener((message: PortMessage) => {
    if (message.type === 'property_written') store.reread(message.property_id);
  });
  port.onDisconnect.addListener(() => {
    if (panelPort !== port) return;
    panelPort = null;
    edge.setPanelOpen(false);
  });
  edge.setPanelOpen(true);
  post({ type: 'subject', subject });
});

// The profile's scheme and theme on every themed shadow host (appearance.ts).
followAppearance(store);
renderForUrl(window.location.href);
callPanel(false);
/* The back/forward cache closes the panel's port as the page goes in, and the frozen
 * page hears no onDisconnect: drop it here; back out, call the panel again. */
window.addEventListener('pagehide', (e) => {
  if (!e.persisted) return;
  panelPort = null;
  edge.setPanelOpen(false);
});
window.addEventListener('pageshow', (e) => { if (e.persisted) callPanel(false); });
