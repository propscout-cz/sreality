/// <reference lib="webworker" />
// @ts-check
/* The SPA's service worker (Phone app map #1759; design and every reason: #1764;
 * push: docs/design/notifications-push/PROGRAM.md § 3.4).
 *
 * VERSION-AGNOSTIC ON PURPOSE. It caches by URL, never by build, so it does not
 * change when the app deploys and never decides which build runs: the network
 * does. lib/buildSkew notices a newer build and offers a reload; lib/lazyChunk
 * recovers from a missing chunk. Nothing here reloads a page or talks to one;
 * the one page it ever opens is the one a tapped notification names.
 * It handles exactly two kinds of same-origin GET:
 *   - navigations: network-first (navigation preload); the last good shell only
 *     after 3 s, on a network error or on a 5xx; the URL is never rewritten (the
 *     Supabase sign-in return reads its tokens from it);
 *   - /assets/*: cache-first; only 200s are stored, newest 200 kept, 14 days max.
 * Everything else (the API and its /images, Supabase, map tiles, the manifest,
 * the icons, buildSkew's /index.html probe, workflow-docs.json) is never
 * answered here and goes to the network and the HTTP cache as if there were no
 * worker.
 *
 * And two push events (the server sends declarative Web Push, which iOS 18.4+
 * shows by itself without waking this worker; iOS 16.4-18.3 hand it here):
 *   - push: ALWAYS shows exactly one notification, then sets the app badge.
 *     Three pushes that show nothing and WebKit removes every subscription of
 *     the origin, so an empty, unreadable or partial payload still shows
 *     "Nové upozornění", opening /notifications;
 *   - notificationclick: opens the notification's same-origin page in the app's
 *     open window (focus, then navigate), else in a new one.
 *
 * KILL SWITCH. To retire the worker, NEVER delete this file: an installed worker
 * whose script 404s or turns into HTML just stays installed. Replace the whole
 * file with
 *
 *   self.addEventListener('install', () => self.skipWaiting());
 *   self.addEventListener('activate', (event) => event.waitUntil((async () => {
 *     for (const name of await caches.keys()) await caches.delete(name);
 *     await self.registration.unregister();
 *   })()));
 *
 * and, in the same PR, make registerServiceWorker() in src/lib/serviceWorker.ts
 * unregister instead of register (or every page load reinstalls the kill
 * switch). Open pages keep running; the next load runs without a worker. Keep
 * that file served for months. Unregistering also ends push below iOS 18.4: no
 * worker is left to show one, and the subscriptions go with the registration. */

const sw = /** @type {ServiceWorkerGlobalScope} */ (/** @type {unknown} */ (self));

// The -v1 suffixes change only when the cache FORMAT changes, not with the logic.
const SHELL_CACHE = 'shell-v1';
const ASSET_CACHE = 'assets-v1';
/* One key for every route: Caddy answers every route with the same index.html. */
const SHELL_KEY = '/index.html';
const NAV_TIMEOUT_MS = 3000;
const ASSET_MAX_ENTRIES = 200;
const ASSET_MAX_AGE_MS = 14 * 24 * 3600 * 1000;
let prunedThisLifetime = false;

sw.addEventListener('install', (event) => event.waitUntil(sw.skipWaiting()));

sw.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    if (sw.registration.navigationPreload) await sw.registration.navigationPreload.enable();
    for (const name of await caches.keys()) {
      if (name !== SHELL_CACHE && name !== ASSET_CACHE) await caches.delete(name);
    }
    await pruneAssets();
    await sw.clients.claim();
  })());
});

sw.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== sw.location.origin) return;
  if (request.mode === 'navigate') return handleNavigation(event);
  if (url.pathname.startsWith('/assets/')) event.respondWith(cacheFirst(event));
});

/** @param {FetchEvent} event */
function handleNavigation(event) {
  const network = (async () => (await event.preloadResponse) || fetch(event.request))();
  /* Registered before respondWith hands the response to the page, so the clone
   * in storeShell happens before the page reads the body. A response that
   * arrives after the timeout still refreshes the cached shell. */
  event.waitUntil(network.then(storeShell, () => {}));
  event.respondWith((async () => {
    /** @type {Promise<null>} */
    const timeout = new Promise((resolve) => setTimeout(resolve, NAV_TIMEOUT_MS, null));
    const first = await Promise.race([network.catch(() => null), timeout]);
    if (first && first.status < 500) return first;
    const cached = await caches.match(SHELL_KEY, { cacheName: SHELL_CACHE });
    /* No shell cached yet: hand over the 5xx, or keep waiting for the network. */
    return cached || first || network;
  })());
}

/** @param {Response} response */
async function storeShell(response) {
  const copy = response.clone();
  const type = copy.headers.get('content-type') || '';
  if (copy.status !== 200 || copy.redirected || !type.startsWith('text/html')) return;
  await (await caches.open(SHELL_CACHE)).put(SHELL_KEY, copy);
  if (!prunedThisLifetime) {
    prunedThisLifetime = true;
    await pruneAssets();
  }
}

/** @param {FetchEvent} event */
async function cacheFirst(event) {
  const cache = await caches.open(ASSET_CACHE);
  const hit = await cache.match(event.request, { ignoreVary: true });
  if (hit) return hit;
  const response = await fetch(event.request);
  /* A 404 (a chunk from a build this container no longer has) goes back to
   * lazyChunk untouched and is never stored. */
  if (response.status === 200 && response.type === 'basic') {
    event.waitUntil(cache.put(event.request, response.clone()));
  }
  return response;
}

/* Keep the newest ASSET_MAX_ENTRIES by their Date header, none older than
 * ASSET_MAX_AGE_MS. Always safe: a pruned file is fetched again, or 404s into
 * lazyChunk's reload. */
async function pruneAssets() {
  const cache = await caches.open(ASSET_CACHE);
  const now = Date.now();
  const entries = [];
  for (const request of await cache.keys()) {
    const response = await cache.match(request, { ignoreVary: true });
    entries.push({ request, at: Date.parse(response?.headers.get('date') ?? '') || 0 });
  }
  entries.sort((a, b) => b.at - a.at);
  await Promise.all(entries
    .filter((entry, i) => i >= ASSET_MAX_ENTRIES || now - entry.at > ASSET_MAX_AGE_MS)
    .map((entry) => cache.delete(entry.request, { ignoreVary: true })));
}

/* ---- Push ---------------------------------------------------------------- */

const PUSH_FALLBACK_TITLE = 'Nové upozornění';
const PUSH_FALLBACK_PATH = '/notifications';

sw.addEventListener('push', (event) => {
  event.waitUntil(showPush(event.data));
});

sw.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(openPage(samePage(event.notification.data?.navigate)));
});

/** @param {PushMessageData | null} data */
async function showPush(data) {
  const push = readPush(data);
  /* The notification first and on its own: a badge failure never costs it. */
  await sw.registration.showNotification(push.title, {
    body: push.body,
    lang: push.lang,
    data: { navigate: push.navigate },
  });
  await setBadge(push.badge);
}

/* The declarative shape, {"web_push": 8030, "notification": {title, body,
 * navigate, lang, app_badge}, "app_badge": N}, read field by field: whatever is
 * missing or malformed falls back, and nothing throws. */
/** @param {PushMessageData | null} data */
function readPush(data) {
  /** @type {Record<string, unknown>} */
  let payload = {};
  try {
    const parsed = data ? data.json() : null;
    if (parsed && typeof parsed === 'object') payload = parsed;
  } catch {
    /* Not JSON: every field falls back. */
  }
  const n = payload.notification && typeof payload.notification === 'object'
    ? /** @type {Record<string, unknown>} */ (payload.notification)
    : {};
  const text = (/** @type {unknown} */ v) => (typeof v === 'string' && v.trim() ? v : undefined);
  const badge = [payload.app_badge, n.app_badge].find(
    (v) => typeof v === 'number' && Number.isInteger(v) && v >= 0,
  );
  return {
    title: text(n.title) ?? PUSH_FALLBACK_TITLE,
    body: text(n.body),
    lang: text(n.lang) ?? 'cs',
    navigate: samePage(n.navigate),
    badge: /** @type {number | undefined} */ (badge),
  };
}

/* An absolute URL on this origin: a relative path is resolved against it, and a
 * missing, unparsable or foreign one becomes the feed. */
/** @param {unknown} navigate */
function samePage(navigate) {
  const origin = sw.location.origin;
  if (typeof navigate === 'string' && navigate) {
    try {
      const url = new URL(navigate, origin);
      if (url.origin === origin) return url.href;
    } catch {
      /* falls through to the feed */
    }
  }
  return new URL(PUSH_FALLBACK_PATH, origin).href;
}

/* The icon's number from the push itself (the page's unread observer writes it
 * too, lib/unreadCount): 0 clears it. No Badging API, or a refusal, is normal. */
/** @param {number | undefined} count */
async function setBadge(count) {
  const nav = sw.navigator;
  if (count === undefined || typeof nav.setAppBadge !== 'function') return;
  try {
    await (count > 0 ? nav.setAppBadge(count) : nav.clearAppBadge());
  } catch {
    /* ignore */
  }
}

/* The app's open window if there is one, focused and sent to the page; a new
 * one otherwise. A client the browser just launched can refuse focus (or a
 * navigate, if this worker does not control it yet): it then opens instead. */
/** @param {string} url */
async function openPage(url) {
  const windows = await sw.clients.matchAll({ type: 'window', includeUncontrolled: true });
  const open = windows.find((client) => new URL(client.url).origin === sw.location.origin);
  if (open) {
    try {
      const focused = await open.focus();
      await focused.navigate(url);
      return;
    } catch {
      /* opened below */
    }
  }
  await sw.clients.openWindow(url);
}
