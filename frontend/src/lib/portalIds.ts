/* The portal-id rules: which host is which portal, and how a listing's native
 * id comes out of its detail URL. ONE copy for BOTH browser surfaces, shared the
 * way lib/brand.ts is:
 *   - the SPA's paste-a-link (`parsePortalUrl` → /listing/:source/:nativeId);
 *   - the Chrome extension (content / background / index_overlay / index_cards,
 *     and its vite.config.ts, which builds manifest `content_scripts.matches`
 *     from PORTALS[].hosts).
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
 * Adding a host here means adding it to chrome-extension/manifest.json too;
 * portalIds.test.ts fails when the two lists differ. */

export interface PortalRef {
  source: string;
  sourceId: string;
}

export interface Portal {
  source: string;
  /* Hostnames this portal serves listings on (exact match, case-insensitive). */
  hosts: string[];
  /* Native id from a detail URL or a card's detail href; null = not a detail
   * link (so also: not a detail page). Accepts absolute or relative hrefs. */
  detailId(pathname: string): string | null;
  /* Best-effort "is this a sale apartment?" from the URL alone, for the
   * not-in-our-DB case where we have no row to gate on. true / false / null
   * (unknown). Only portals that encode category in the path implement it. */
  saleApartmentHint?(pathname: string): boolean | null;
}

function firstMatch(re: RegExp, s: string): string | null {
  const m = re.exec(s);
  return m ? m[1] : null;
}

/* sreality / idnes encode category as /…/{prodej|pronajem}/{byt|dum|…}/… */
function pathCategoryHint(path: string): boolean | null {
  const sale = /\/(prodej)\//.test(path);
  const rent = /\/(pronajem|pronájem)\//.test(path);
  const byt = /\/byt[\/-]/.test(path) || /\/byty\//.test(path);
  const otherKind = /\/(dum|domy|pozemek|pozemky|chata|chaty|kancelar|komercni|garaz)\b/.test(path);
  if (rent) return false;
  if (otherKind && !byt) return false;
  if (sale && byt) return true;
  return null;
}

export const PORTALS: Portal[] = [
  {
    source: 'sreality',
    hosts: ['www.sreality.cz', 'sreality.cz'],
    // /detail/{type}/{main}/{slug}/{id} — id is the trailing all-digits segment.
    detailId: (p) => firstMatch(/\/detail\/[^?#]*?\/(\d{5,})(?:[/?#]|$)/, p),
    saleApartmentHint: pathCategoryHint,
  },
  {
    source: 'bazos',
    hosts: ['reality.bazos.cz'],
    // /inzerat/{id}/{slug}.php
    detailId: (p) => firstMatch(/\/inzerat\/(\d+)\//, p),
  },
  {
    // .com is the English site: www-canonical (the apex 302s to www), the same
    // ids as .cz under its own path word. Checked live 2026-10-12.
    source: 'bezrealitky',
    hosts: ['www.bezrealitky.cz', 'bezrealitky.cz', 'www.bezrealitky.com', 'bezrealitky.com'],
    // /nemovitosti-byty-domy/{id}-{slug} (.cz) · /properties-flats-houses/{id}-{slug} (.com)
    detailId: (p) => firstMatch(/\/(?:nemovitosti-byty-domy|properties-flats-houses)\/(\d+)/, p),
  },
  {
    source: 'idnes',
    hosts: ['reality.idnes.cz'],
    // /detail/{sale}/{cat}/{slug}/{24-hex objectid}/
    detailId: (p) => firstMatch(/\/detail\/[^?#]*?\/([0-9a-f]{24})(?:[/?#]|$)/, p),
    saleApartmentHint: pathCategoryHint,
  },
  {
    source: 'maxima',
    hosts: ['nemovitosti.maxima.cz'],
    // /nemovitosti/{id}/  — id is a short letter+digits token (e.g. b50089333)
    detailId: (p) => firstMatch(/\/nemovitosti\/([a-z]?\d{6,})(?:[/?#]|$)/, p),
  },
  {
    source: 'remax',
    hosts: ['www.remax-czech.cz', 'remax-czech.cz'],
    // /reality/detail/{id}/{slug}
    detailId: (p) => firstMatch(/\/reality\/detail\/(\d+)/, p),
  },
  {
    // /nemovitosti/{id}/ — id is the digit segment after /nemovitosti/. (maxima
    // shares this path on a different host; host-routing disambiguates, and maxima
    // ids carry a letter prefix.) Verified against live source_id_native.
    source: 'mmreality',
    hosts: ['www.mmreality.cz', 'mmreality.cz'],
    detailId: (p) => firstMatch(/\/nemovitosti\/(\d{6,})(?:[/?#]|$)/, p),
  },
  {
    // /{sale}/{cat}/{disp}/{town}/{slug}-{id}.html — id is the trailing run of
    // digits before ".html" (any length: live ids run 4–7 digits). The "-…\.html"
    // anchor isolates it from slug decoys (the area in "...-330-m2-{id}.html").
    // Verified against 100% of live source_id_native.
    source: 'ceskereality',
    hosts: ['www.ceskereality.cz', 'ceskereality.cz'],
    detailId: (p) => firstMatch(/-(\d+)\.html\b/, p),
    saleApartmentHint: pathCategoryHint,
  },
  {
    // /detail/{obec}/{slug}-{id}.html — same "-{id}.html" scheme as ceskereality
    // (the "-…\.html" anchor isolates the id from a slug area decoy like
    // "pole-253870-m-…-{id}.html"). The detail URL does NOT encode the category,
    // so no saleApartmentHint (the /listings/lookup row gates instead).
    source: 'realitymix',
    hosts: ['www.realitymix.cz', 'realitymix.cz'],
    detailId: (p) => firstMatch(/-(\d+)\.html\b/, p),
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

/* A pasted portal link → the pair the SPA's /listing/:source/:nativeId route
 * takes, or null when it is not a portal listing's detail URL. Whitespace is
 * trimmed and a missing scheme assumed https; query and fragment are ignored. A
 * relative path pins no portal here (the host fallback is empty). */
export function parsePortalUrl(url: string): { source: string; nativeId: string } | null {
  const text = url.trim();
  if (text === '') return null;
  const ref = detailRef(/^[a-z][a-z\d+.-]*:\/\//i.test(text) ? text : `https://${text}`, '');
  return ref == null ? null : { source: ref.source, nativeId: ref.sourceId };
}
