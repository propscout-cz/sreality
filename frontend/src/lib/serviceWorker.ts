/* Registers public/sw.js, the version-agnostic service worker (its header says
 * what it caches; the design is #1764). Called once from main.tsx, outside
 * React, for every viewport: one SPA, one behaviour, and a worker bug shows on
 * the desktop first.
 *
 * Production builds only: under the dev server a worker caching /assets/*
 * would only confuse. Every failure is swallowed, because no worker is exactly
 * how the app ran before it had one.
 *
 * KEEPING THE WORKER ITSELF FRESH. The browser looks for a new sw.js on a
 * navigation, but an installed iPhone app that resumes from the background is
 * not a navigation, and can go weeks without a cold start. So a visible-again
 * page asks for an update itself, at most once an hour: that is how a fix, or
 * the kill switch, reaches it. The worker never decides which BUILD runs, so a
 * worker a few days old is harmless; new builds are buildSkew's business. The
 * same hourly check re-sends this device's push subscription (lib/push
 * `resyncPush`): iOS never tells the page that a subscription changed.
 *
 * STORAGE. An installed web app gets persistent storage only if it asks
 * (WebKit grants it by heuristic to home-screen apps; #1763). Asked once per
 * launch, and only in the installed app: in a desktop Firefox tab the same call
 * opens a permission prompt. */

import { resyncPush } from './push';

const UPDATE_CHECK_INTERVAL_MS = 60 * 60_000;

export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return;
  const start = () => {
    navigator.serviceWorker
      .register('/sw.js', { scope: '/' })
      .then((registration) => {
        let lastCheck = Date.now();
        document.addEventListener('visibilitychange', () => {
          if (document.visibilityState !== 'visible') return;
          if (Date.now() - lastCheck < UPDATE_CHECK_INTERVAL_MS) return;
          lastCheck = Date.now();
          registration.update().catch(() => {});
          void resyncPush();
        });
      })
      .catch(() => {});
    if (isInstalledApp()) requestPersistentStorage();
  };
  /* After `load`, so registering never competes with the first paint. */
  if (document.readyState === 'complete') start();
  else window.addEventListener('load', start, { once: true });
}

/* `display-mode` alone is not trusted on iOS (a standalone app has been seen
 * matching `fullscreen`); `navigator.standalone` is Safari's own flag. Also the
 * app-icon badge's gate (lib/unreadCount): only an installed app has an icon. */
export function isInstalledApp(): boolean {
  const standalone = (navigator as Navigator & { standalone?: boolean }).standalone === true;
  const displayMode = ['standalone', 'fullscreen'].some(
    (mode) => window.matchMedia?.(`(display-mode: ${mode})`).matches,
  );
  return standalone || displayMode;
}

function requestPersistentStorage(): void {
  const storage = navigator.storage;
  if (!storage?.persist || !storage.persisted) return;
  storage
    .persisted()
    .then((persisted) => (persisted ? undefined : storage.persist()))
    .catch(() => {});
}
