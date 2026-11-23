/* The portal-id rules: which host is which portal, and how a listing's native
 * id comes out of its detail URL. ONE copy for BOTH browser surfaces, shared the
 * way lib/brand.ts is:
 *   - the SPA's paste-a-link (`parsePortalUrl` → /listing/:source/:nativeId);
 *   - the Chrome extension (content / index_overlay / index_cards, and its
 *     vite.config.ts, which builds manifest `content_scripts.matches` from
 *     PORTALS[].pageHosts).
 * Zero imports, no React, no DOM at module load: safe in either build. It lives
 * under frontend/ because the SPA's Docker build context is frontend/ only (a
 * repo-root file would break the Railway image); the extension always builds
 * from the whole repo and reaches in by relative import.
 *
 * `source` is `listings.source` and the id is exactly `listings.source_id_native`,
 * so /listings/lookup and the SPA's advert route resolve the pair directly. Sale-
 * apartment gating and the MF rent/yield come from the lookup, not from here.
 *
 * The id extractor doubles as a "is this a detail URL?" test (null for index /
 * search pages) AND as the extension's index-overlay matcher: on a search page it
 * scans every <a href> and keeps the ones that yield an id — robust to the
 * portals' card markup changing under us.
 *
 * The id rules MIRROR the Python canon, scraper/portal_identity.py (the walk's
 * identity); one probe fixture, tests/fixtures/portal_url_probes.json, runs
 * against both, so the two can only drift with a red test.
 *
 * Adding a page host here means adding it to chrome-extension/manifest.json and
 * to chrome-extension/release/allowed.ts too (a new site on the install warning);
 * portalIds.test.ts fails when the manifest and the page hosts differ. */

export interface PortalRef {
  source: string;
  sourceId: string;
}

export interface Portal {
  source: string;
  /* Every hostname that names this portal (exact match, case-insensitive): a pasted
   * link on any of them resolves. Mirrors scraper/portal_identity.py. */
  hosts: string[];
  /* The hosts that answer with pages; the rest of `hosts` only redirect to one of
   * these, deep ad paths included (checked 2026-11-01). The extension's content
   * script runs only here, so the install warning names only these. */
  pageHosts: string[];
  /* Native id from a detail URL or a card's detail href; null = not a detail
   * link (so also: not a detail page). Accepts absolute or relative hrefs. */
  detailId(pathname: string): string | null;
}

function firstMatch(re: RegExp, s: string): string | null {
  const m = re.exec(s);
  return m ? m[1] : null;
}

export const PORTALS: Portal[] = [
  {
    source: 'sreality',
    hosts: ['www.sreality.cz', 'sreality.cz'],
    pageHosts: ['www.sreality.cz'],
    // /detail/{type}/{main}/{slug}/{id} — id is the LAST segment, all digits; 18 at most,
    // so it always fits the queue's bigint sreality_id.
    detailId: (p) => firstMatch(/\/detail\/[^?#]*\/([0-9]{5,18})\/?$/, p),
  },
  {
    source: 'bazos',
    hosts: ['reality.bazos.cz'],
    pageHosts: ['reality.bazos.cz'],
    // /inzerat/{id}/{slug}.php
    detailId: (p) => firstMatch(/\/inzerat\/(\d+)\//, p),
  },
  {
    // .com is the English site: www-canonical (the apex 302s to www), the same
    // ids as .cz under its own path word. Checked live 2026-10-12.
    source: 'bezrealitky',
    hosts: ['www.bezrealitky.cz', 'bezrealitky.cz', 'www.bezrealitky.com', 'bezrealitky.com'],
    pageHosts: ['www.bezrealitky.cz', 'www.bezrealitky.com'],
    // /nemovitosti-byty-domy/{id}-{slug} (.cz) · /properties-flats-houses/{id}-{slug} (.com)
    detailId: (p) => firstMatch(/\/(?:nemovitosti-byty-domy|properties-flats-houses)\/(\d+)/, p),
  },
  {
    source: 'idnes',
    hosts: ['reality.idnes.cz'],
    pageHosts: ['reality.idnes.cz'],
    // /detail/{sale}/{cat}/{slug}/{hex objectid}/ — 24 hex today, 16+ in the canon
    // (scraper/portal_identity.py); the last segment only, so a hex-looking slug word is no id.
    detailId: (p) => firstMatch(/\/detail\/[^?#]*?\/([0-9a-f]{16,})\/?$/, p),
  },
  {
    source: 'maxima',
    hosts: ['nemovitosti.maxima.cz'],
    pageHosts: ['nemovitosti.maxima.cz'],
    // /nemovitosti/{id}/  — id is a letter + digits token (e.g. b50089333)
    detailId: (p) => firstMatch(/\/nemovitosti\/([a-z][0-9]+)\/?$/, p),
  },
  {
    source: 'remax',
    hosts: ['www.remax-czech.cz', 'remax-czech.cz'],
    pageHosts: ['www.remax-czech.cz'],
    // /reality/detail/{id}/{slug}
    detailId: (p) => firstMatch(/\/reality\/detail\/(\d+)/, p),
  },
  {
    // /nemovitosti/{id}/ — id is the digit segment after /nemovitosti/. (maxima
    // shares this path on a different host; host-routing disambiguates, and maxima
    // ids carry a letter prefix.) Verified against live source_id_native.
    source: 'mmreality',
    hosts: ['www.mmreality.cz', 'mmreality.cz'],
    pageHosts: ['www.mmreality.cz'],
    detailId: (p) => firstMatch(/\/nemovitosti\/([0-9]+)\/?/, p),
  },
  {
    // /{sale}/{cat}/{disp}/{town}/{slug}-{id}.html — id is the trailing run of
    // 4+ digits before ".html" (live ids run 4–7 digits). The "-…\.html" end
    // anchor isolates it from slug decoys (the area in "...-330-m2-{id}.html").
    // Verified against 100% of live source_id_native.
    source: 'ceskereality',
    hosts: ['www.ceskereality.cz', 'ceskereality.cz'],
    pageHosts: ['www.ceskereality.cz'],
    detailId: (p) => firstMatch(/-([0-9]{4,})\.html$/, p),
  },
  {
    // /detail/{obec}/{slug}-{id}.html — same "-{id}.html" scheme as ceskereality
    // (the "-…\.html" anchor isolates the id from a slug area decoy like
    // "pole-253870-m-…-{id}.html").
    source: 'realitymix',
    hosts: ['www.realitymix.cz', 'realitymix.cz'],
    pageHosts: ['realitymix.cz'],
    detailId: (p) => firstMatch(/-([0-9]{4,})\.html$/, p),
  },
];

const BY_HOST: Map<string, Portal> = (() => {
  const m = new Map<string, Portal>();
  for (const portal of PORTALS) {
    for (const h of portal.hosts) m.set(h.toLowerCase(), portal);
  }
  return m;
})();

export function portalForHost(host: string): Portal | null {
  return BY_HOST.get(host.toLowerCase()) ?? null;
}

function parse(url: string): URL | null {
  try { return new URL(url, 'https://x'); } catch { return null; }
}

export function portalForUrl(url: string): Portal | null {
  const u = parse(url);
  if (u == null) return null;
  // Relative hrefs resolve against the dummy origin; fall back to current host.
  const host = u.hostname === 'x' ? location.hostname : u.hostname;
  return portalForHost(host);
}

/* (source, native id) for a detail URL/href, or null if it isn't one. For
 * relative card hrefs, `host` pins the portal (cards always link same-host). */
export function detailRef(url: string, host: string = location.hostname): PortalRef | null {
  const u = parse(url);
  if (u == null) return null;
  const portal = portalForHost(u.hostname === 'x' ? host : u.hostname);
  if (portal == null) return null;
  const id = portal.detailId(u.pathname);
  return id == null ? null : { source: portal.source, sourceId: id };
}

export function isDetailPage(url: string): boolean {
  return detailRef(url) != null;
}

/* A path the fetch would rewrite (a `.` / `..` segment or a backslash, percent-encoded
 * or not) names one ad in the id rule and fetches another. `new URL` has already
 * rewritten it, so this reads the text as pasted — the canon's `_rewritten_path`. */
function rewrittenPath(absolute: string): boolean {
  const beforeQuery = absolute.split(/[?#]/, 1)[0];
  let decoded = beforeQuery;
  try { decoded = decodeURIComponent(beforeQuery); } catch { /* Python's unquote keeps it raw too */ }
  return decoded.includes('\\') || decoded.split('/').slice(3).some((seg) => seg === '.' || seg === '..');
}

/* A pasted portal link → the pair the SPA's /listing/:source/:nativeId route
 * takes, or null when it is not a portal listing's detail URL. Whitespace is
 * trimmed and a missing scheme assumed https; query and fragment are ignored. A
 * relative path pins no portal here (the host fallback is empty). */
export function parsePortalUrl(url: string): { source: string; nativeId: string } | null {
  const text = url.trim();
  if (text === '') return null;
  const absolute = /^[a-z][a-z\d+.-]*:\/\//i.test(text) ? text : `https://${text}`;
  if (rewrittenPath(absolute)) return null;
  const ref = detailRef(absolute, '');
  return ref == null ? null : { source: ref.source, nativeId: ref.sourceId };
}
