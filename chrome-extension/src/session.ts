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

/* The signed-in account changed (sign-in, sign-out, switch, failed refresh; any tab): drop what
 * was read as the previous one, on the TRANSITION like the SPA (frontend/src/lib/auth.tsx). A
 * token refresh keeps the user; a session stored before user_id counts as one unknown account. */
export function onAccountChange(fn: () => void): () => void {
  const user = (s?: StoredSession): string | null => (s == null ? null : s.user_id ?? '');
  return onStoredChange(SESSION_KEY, (c) => { if (user(c.oldValue) !== user(c.newValue)) fn(); });
}
