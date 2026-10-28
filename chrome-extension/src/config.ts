/* What the build inlines, read once for both bundles — the background worker's and
 * the content script's (each vite pass bundles its own copy, as with session.ts):
 * the base URLs, normalized, and the failure details one side answers and the
 * other tells apart. */

/* Tolerate a URL entered without a scheme (`api.up.railway.app`): fetch needs an
 * absolute one, so default to https; drop a trailing slash so `BASE + path`
 * doesn't double it. */
export function normalizeBaseUrl(raw: string | undefined): string {
  const trimmed = (raw ?? '').trim();
  if (trimmed === '') return '';
  const withScheme = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  return withScheme.replace(/\/$/, '');
}

// PURE: a bundle that reads none of a constant drops it (the content script fetches nothing).
export const API_BASE_URL = /* @__PURE__ */ normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL);
export const SUPABASE_URL = /* @__PURE__ */ normalizeBaseUrl(import.meta.env.VITE_SUPABASE_URL);
/* The app, for the "Otevřít v aplikaci" deep-links. Empty → every app link hidden. */
export const APP_BASE_URL = /* @__PURE__ */ normalizeBaseUrl(import.meta.env.VITE_APP_BASE_URL);
/* The app's collections page — the "Spravovat kolekce" link of both save
 * checklists (the panel's and the search-page cards'). Null → link hidden. */
export const COLLECTIONS_URL = APP_BASE_URL ? `${APP_BASE_URL}/collections` : null;

/* Every extension-touched route runs on verify_jwt, so a request with no session
 * is a clean, expected "please sign in" — `status: 401` + this detail lets a
 * surface prompt for sign-in instead of showing a raw error. */
export const NOT_SIGNED_IN_DETAIL = 'not_signed_in';
/* A build without VITE_API_BASE_URL, which neither a retry nor a reload can fix. */
export const API_NOT_CONFIGURED_DETAIL = 'API base URL not configured';
/* The content script was orphaned — Chrome throws the same "Extension context
 * invalidated" for a reload, an update, a disable and a removal, so the copy
 * names none of them. Only a page reload helps. */
export const EXTENSION_RELOADED_DETAIL = 'Spojení s rozšířením se přerušilo — obnovte stránku';

/* A failure detail in the operator's words. */
export function friendlyDetail(detail: string): string {
  return detail === NOT_SIGNED_IN_DETAIL ? 'nejste přihlášeni' : detail;
}
