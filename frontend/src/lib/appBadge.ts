/* The number on the installed app's icon (ruling R4): the one unread observer's
 * count (lib/unreadCount), cleared when the signed-in identity changes
 * (lib/auth); the service worker sets it from a push's `app_badge` (public/sw.js).
 * Set while unread rows exist, cleared at zero. A browser tab, an engine
 * without the Badging API and a refused permission (iOS draws the badge only
 * once notifications are allowed) are all normal, so every failure is
 * swallowed. */

import { isInstalledApp } from './serviceWorker';

export function syncAppBadge(count: number): void {
  if (typeof navigator.setAppBadge !== 'function' || !isInstalledApp()) return;
  try {
    const done = count > 0 ? navigator.setAppBadge(count) : navigator.clearAppBadge();
    done.catch(() => {});
  } catch {
    /* A synchronous throw (a missing clearAppBadge) is the same no-badge case. */
  }
}
