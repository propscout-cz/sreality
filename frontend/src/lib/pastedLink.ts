/* Paste-a-link (#1767, the Phone app's Odkaz tab): what the operator pasted →
 * the portal listing it names, or why it names none.
 *
 * A share sheet rarely hands over a bare URL: Messages, WhatsApp and the
 * portals' own "Sdílet" wrap it in words ("Koukni na tohle https://… 2+kk").
 * So the FIRST URL is pulled out of the text — `https?://…` or a bare `www.…` —
 * and only then handed to the shared portal-id rules (`lib/portalIds.ts`, the
 * extension's own copy), which say which portal and which native id — the
 * phone's instant check and quick path. The server stays authoritative: the
 * "Nová nemovitost" flow sends the pasted text itself (POST /listings/ingest).
 *
 * The last five links resolved are kept per device (localStorage): a roll under
 * the field, so a link opened at a viewing is one tap away on the way home. */

import { parsePortalUrl, portalForUrl } from './portalIds';

/* A URL in running text: a scheme or a bare `www.`, up to the next whitespace
 * or a character no URL carries unescaped. */
const URL_IN_TEXT = /(?:\bhttps?:\/\/|\bwww\.)[^\s<>"'«»“”‘’]+/i;

/* Prose punctuation a sentence leaves stuck to the end of a link. */
const TRAILING = /[.,;:!?…]+$/;

/* Strip what the sentence added: trailing punctuation, and a closing bracket
 * the URL never opened ("(viz https://…/123)"). */
function trimProse(url: string): string {
  let out = url;
  for (;;) {
    const before = out;
    out = out.replace(TRAILING, '');
    for (const [open, close] of [['(', ')'], ['[', ']']] as const) {
      if (out.endsWith(close) && out.split(open).length < out.split(close).length) {
        out = out.slice(0, -1);
      }
    }
    if (out === before) return out;
  }
}

/* The first URL in the pasted text, or null. A text with no scheme and no
 * `www.` that is one dotted token ("reality.bazos.cz/inzerat/…") counts as a
 * URL too: some apps copy a link without its scheme. */
export function firstUrl(text: string): string | null {
  const m = URL_IN_TEXT.exec(text);
  if (m) return trimProse(m[0]);
  const bare = text.trim();
  if (bare && !/\s/.test(bare) && /^[a-z0-9-]+(\.[a-z0-9-]+)+(\/|$)/i.test(bare)) return trimProse(bare);
  return null;
}

export type PastedLink =
  | { kind: 'listing'; url: string; source: string; nativeId: string }
  /* A portal we read, but not one of its listing pages (a search, the home page). */
  | { kind: 'portal-page'; url: string; source: string }
  /* A URL on no portal we read: the server decides (POST /listings/ingest). */
  | { kind: 'other'; url: string }
  | { kind: 'no-url' };

const withScheme = (url: string): string => (/^[a-z][a-z\d+.-]*:\/\//i.test(url) ? url : `https://${url}`);

export function readPastedLink(text: string): PastedLink {
  const url = firstUrl(text);
  if (url == null) return { kind: 'no-url' };
  const hit = parsePortalUrl(url);
  if (hit) return { kind: 'listing', url: withScheme(url), ...hit };
  const portal = portalForUrl(withScheme(url));
  return portal ? { kind: 'portal-page', url, source: portal.source } : { kind: 'other', url: withScheme(url) };
}

/* -------------------------------------------------------------------------- */
/* The per-device roll of the last links                                      */
/* -------------------------------------------------------------------------- */

export const RECENT_LINKS_KEY = 'sreality.phone.recentLinks';
export const RECENT_LINKS_MAX = 5;

export interface RecentLink {
  url: string;
  source: string;
  nativeId: string;
  /* ISO time it was opened. */
  at: string;
}

const isRecent = (v: unknown): v is RecentLink =>
  typeof v === 'object' &&
  v != null &&
  typeof (v as RecentLink).url === 'string' &&
  typeof (v as RecentLink).source === 'string' &&
  typeof (v as RecentLink).nativeId === 'string' &&
  typeof (v as RecentLink).at === 'string';

export function readRecentLinks(): RecentLink[] {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_LINKS_KEY) ?? '[]');
    return Array.isArray(raw) ? raw.filter(isRecent).slice(0, RECENT_LINKS_MAX) : [];
  } catch {
    /* Storage can throw (private-mode lockdown) or hold junk: no roll. */
    return [];
  }
}

/* The link goes to the top; the same listing pasted again moves up, not twice. */
export function rememberLink(link: Omit<RecentLink, 'at'>, now: Date = new Date()): RecentLink[] {
  const next = [
    { ...link, at: now.toISOString() },
    ...readRecentLinks().filter((r) => !(r.source === link.source && r.nativeId === link.nativeId)),
  ].slice(0, RECENT_LINKS_MAX);
  try {
    localStorage.setItem(RECENT_LINKS_KEY, JSON.stringify(next));
  } catch {
    /* ignore: the roll is a convenience */
  }
  return next;
}
