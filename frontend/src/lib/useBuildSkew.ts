/* Offer a reload when the server starts serving a newer build.
 *
 * Mounted once, in Shell. See lib/buildSkew.ts for how "newer build" is
 * determined and why the check is focus-driven rather than polled.
 *
 * The nudge is an OFFER, never an imposition: a sticky toast with a Reload
 * button. Reloading someone's tab out from under them costs unsaved filter
 * state, a half-typed note, a scroll position — and the failure this prevents
 * (a chunk 404) is already handled invisibly by lazyChunk. Shown at most once
 * per tab: a second identical toast after the user chose to ignore the first is
 * nagging, not information.
 *
 * Under the service worker (public/sw.js) a document can START stale: when the
 * network is down or slower than 3 s the worker boots the page from its cached
 * shell, which may name an older build. So when a worker controls the page, one
 * extra probe runs 10 s after mount, off the critical path; the 5-minute
 * throttle in buildSkew then covers the next focus. Still an offer: the toast,
 * never a reload (#1771). */
import { useEffect } from 'react';

import { isBuildStale } from '@/lib/buildSkew';
import { pushToast } from '@/lib/toast';

const MOUNT_PROBE_DELAY_MS = 10_000;

export function useBuildSkew(): void {
  useEffect(() => {
    let shown = false;
    let cancelled = false;

    async function check() {
      if (shown || cancelled || document.visibilityState !== 'visible') return;
      if (!(await isBuildStale())) return;
      if (shown || cancelled) return;
      shown = true;
      pushToast('info', 'A newer version of the app is available.', 0, {
        label: 'Reload',
        onClick: () => window.location.reload(),
      });
    }

    /* No immediate probe on mount: without a worker this tab just fetched the
     * very index.html the probe would read, so it is current by construction
     * (with one, the deferred probe below covers a start from the cached
     * shell). The interesting moment is coming BACK to a tab that has been
     * sitting — `visibilitychange` catches a tab switch or an un-minimize (and
     * an installed iPhone app coming back to the foreground), `focus` catches
     * switching back from another window without ever leaving the tab (the
     * operator's actual pattern: app in one window, editor in another). */
    document.addEventListener('visibilitychange', check);
    window.addEventListener('focus', check);
    /* `navigator.serviceWorker` is undefined outside a secure context. */
    const mountProbe = navigator.serviceWorker?.controller
      ? window.setTimeout(check, MOUNT_PROBE_DELAY_MS)
      : undefined;
    return () => {
      cancelled = true;
      window.clearTimeout(mountProbe);
      document.removeEventListener('visibilitychange', check);
      window.removeEventListener('focus', check);
    };
  }, []);
}
