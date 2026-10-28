/* The user's appearance on the shadow roots the extension mounts. Each root
 * takes the app's tokens.css, whose `:host(…)` selectors read `data-scheme` and
 * `data-theme` off the HOST, so ONE call, themeHost, stamps a host by the app's
 * paint rule (lib/appearance). The pick is the profile's, served by
 * get_auth_state from the session token: asked at start and whenever the stored
 * session changes (a sign-in or sign-out, or the refresh that carries a new
 * pick); `system` follows the OS while the page is open. */

import { DEFAULT_APPEARANCE, followSystemDark, paintAppearance, type Appearance } from '../../frontend/src/lib/appearance';
import { SESSION_KEY, onStoredChange } from './session';

type Pin = 'light' | undefined;
/* Weak, so a host the page or a surface has removed is collected, not kept
 * here; its dead ref goes at the next themeHost or repaint. */
const hosts = new Map<WeakRef<Element>, Pin>();
let current: Appearance = DEFAULT_APPEARANCE;
const systemDark = followSystemDark(() => { if (current.theme === 'system') repaint(); });

const paint = (host: Element, pin: Pin): void =>
  paintAppearance(host, pin ? { ...current, theme: pin } : current, systemDark);

function repaint(): void {
  for (const [ref, pin] of hosts) {
    const host = ref.deref();
    if (host == null) hosts.delete(ref);
    else paint(host, pin);
  }
}

/* `pin` keeps the user's scheme on its light side whatever their theme: for
 * paint laid over a photo, whose white type needs the light tones under it. */
export function themeHost(host: Element, pin?: 'light'): void {
  for (const ref of hosts.keys()) if (ref.deref() == null) hosts.delete(ref);
  hosts.set(new WeakRef(host), pin);
  paint(host, pin);
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
