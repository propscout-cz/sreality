/* The user's appearance on the shadow roots the extension mounts. Each root
 * takes the app's tokens.css, whose `:host(…)` selectors read `data-scheme` and
 * `data-theme` off the HOST, so ONE call, themeHost, stamps a host by the app's
 * paint rule (lib/appearance). The pick is the profile's, served by
 * get_auth_state from the session token: asked at start and whenever the stored
 * session changes (a sign-in or sign-out, or the refresh that carries a new
 * pick); `system` follows the OS while the page is open. */

import { DEFAULT_APPEARANCE, paintAppearance, type Appearance } from '../../frontend/src/lib/appearance';
import { SESSION_KEY, onStoredChange } from './session';

/* Weak, so a host the page or a surface has removed is collected, not kept here. */
const hosts = new Set<WeakRef<Element>>();
let current: Appearance = DEFAULT_APPEARANCE;
let systemQuery: MediaQueryList | null = null;

function systemDark(): boolean {
  if (systemQuery == null) {
    systemQuery = matchMedia('(prefers-color-scheme: dark)');
    systemQuery.addEventListener('change', () => { if (current.theme === 'system') repaint(); });
  }
  return systemQuery.matches;
}

function repaint(): void {
  for (const ref of hosts) {
    const host = ref.deref();
    if (host == null) hosts.delete(ref);
    else paintAppearance(host, current, systemDark);
  }
}

export function themeHost(host: Element): void {
  hosts.add(new WeakRef(host));
  paintAppearance(host, current, systemDark);
}

export function followAppearance(read: () => Promise<Appearance | null>): void {
  const ask = (): void => {
    void read().then((next) => {
      if (next == null || (next.scheme === current.scheme && next.theme === current.theme)) return;
      current = next;
      repaint();
    });
  };
  ask();
  onStoredChange(SESSION_KEY, ask);
}
