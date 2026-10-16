/* The stored session: the background worker writes it (auth.ts), content scripts watch whose it is. */

export const SESSION_KEY = 'authSession';

export interface StoredSession {
  access_token: string;
  refresh_token: string;
  /* Epoch seconds. */
  expires_at: number;
  /* The access token's `sub`; absent on a session stored before it was kept. */
  user_id?: string;
}

/* Best-effort JWT payload decode for display and cache identity only (the email
 * in the sign-in strip, the `sub` that names the account) — never used for
 * authorization, the API independently verifies the token via JWKS. */
export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const [, payload] = token.split('.');
    if (payload == null) return null;
    const padded = payload.replace(/-/g, '+').replace(/_/g, '/').padEnd(
      payload.length + ((4 - (payload.length % 4)) % 4), '=',
    );
    return JSON.parse(atob(padded)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/* A chrome.storage.local key changed, in this tab or another. Returns the unsubscribe. */
export function onStoredChange(
  key: string, fn: (change: chrome.storage.StorageChange) => void,
): () => void {
  const listener = (changes: Record<string, chrome.storage.StorageChange>, area: string): void => {
    if (area === 'local' && key in changes) fn(changes[key]);
  };
  try { chrome.storage.onChanged.addListener(listener); } catch { return () => {}; }  // orphaned
  return () => { try { chrome.storage.onChanged.removeListener(listener); } catch { /* orphaned */ } };
}

/* Whose session it is: null signed out, else the token's `sub` ('' if unreadable). */
function userOf(s?: StoredSession): string | null {
  if (s == null) return null;
  const sub = s.user_id ?? decodeJwtPayload(s.access_token)?.sub;
  return typeof sub === 'string' ? sub : '';
}

/* The signed-in account changed (sign-in, sign-out, switch, failed refresh; any tab): drop what
 * was read as the previous one, on the TRANSITION like the SPA (frontend/src/lib/auth.tsx). A
 * token refresh keeps the user, so it never fires; `fn` gets the new user (null: signed out). */
export function onAccountChange(fn: (user: string | null) => void): () => void {
  return onStoredChange(SESSION_KEY, (c) => {
    const user = userOf(c.newValue);
    if (userOf(c.oldValue) !== user) fn(user);
  });
}
