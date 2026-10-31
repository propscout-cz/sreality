/* The content script on every portal we scrape: the entry and the router.
 *
 * A listing's detail page gets the side sheet (panel.ts, sheet.ts); any other
 * page on a portal host gets the search-page overlay (index_overlay.ts: per-card
 * controls and the corner dock). Both draw from ONE store (store.ts), created
 * here over the one transport: every network call goes to the background worker
 * through chrome.runtime.sendMessage (it calls from the extension's origin, not
 * the portal page's); this script never fetches. */

import { detailRef, portalForHost } from '../../frontend/src/lib/portalIds';
import { followAppearance } from './appearance';
import { EXTENSION_RELOADED_DETAIL } from './config';
import { runIndexOverlay, type IndexOverlayHandle } from './index_overlay';
import { createPanel } from './panel';
import { onAccountChange } from './session';
import { createStore, sleep } from './store';
import type { ApiMessage, ApiResult, AuthState, RouteMessage } from './types';

/* An extension reload, update, disable or removal orphans the content script in an
 * open tab (Chrome re-injects only on page load): sendMessage then throws "Extension
 * context invalidated." synchronously. send() resolves with a failure instead of
 * rejecting — every busy flag waits for a result — and says what fixes it (a detail
 * the search page's notice recognises, to offer a reload instead of a retry). */
function runtimeDetail(message: string | undefined): string {
  if (message != null && /context invalidated/i.test(message)) return EXTENSION_RELOADED_DETAIL;
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
      resolve({ ok: false, status: 0, detail: runtimeDetail(err instanceof Error ? err.message : String(err)) });
    }
  });
}

const store = createStore({ send, sleep, onAccountChange });
const panel = createPanel(store);

/* What a URL means to US — the LISTING it shows, not its query string. SPAs
 * rewrite the URL for things that don't change the subject (a gallery lightbox,
 * tracking params); keying on the raw href would re-open the sheet on each and
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
    panel.open(ref, url).catch((err: unknown) => { console.error('[mf-ext] detail boot failed', err); });
    return;
  }
  panel.close();
  if (portalForHost(window.location.hostname) == null) return;
  runIndexOverlay(store, panel.open)
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

// The profile's scheme and theme on every themed shadow host (appearance.ts).
followAppearance(async () => {
  const auth = await store.call<AuthState>({ type: 'get_auth_state' });
  return auth.ok ? auth.data.appearance : null;
});
renderForUrl(window.location.href);
