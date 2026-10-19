/* Source-portal display labels, sourced from the canonical filter
 * registry's `portals` enum so the Browse card badge, the filter
 * chips, and any future surface stay in lockstep with the backend
 * `listings.source` vocabulary. Falls back to the raw source code
 * (capitalised) for a portal not yet in the registry enum. */
import { filterById } from './filterRegistry.generated';
import type { ListingFilters } from './filters';
import { cycle } from './setScope';

const PORTAL_LABELS: Record<string, string> = Object.fromEntries(
  (filterById('portals')?.enum_values ?? []).map((o) => [o.value, o.label_cs]),
);

export function portalLabel(source: string | null | undefined): string | null {
  if (!source) return null;
  return PORTAL_LABELS[source] ?? source.charAt(0).toUpperCase() + source.slice(1);
}

/* Short, hand-tuned portal names for compact chrome — e.g. the Health matrix's
 * row heads and the portal tags on listing cards and ad rows. Diacritic
 * spellings (Bazoš) the registry's machine labels don't carry. Falls back to
 * the registry label, then the raw source code. */
export const PORTAL_SHORT_LABEL: Record<string, string> = {
  sreality: 'Sreality',
  bazos: 'Bazoš',
  idnes: 'iDNES',
};

export function portalShort(source: string): string {
  return PORTAL_SHORT_LABEL[source] ?? portalLabel(source) ?? source;
}

/* The portal filter's three values (MS19 + migration 597): the portals a property
 * must have an ad on, the portals it must have none on, and whether only active
 * ads count against the latter. */
export type PortalChoice = Pick<
  ListingFilters, 'portals' | 'portalsExcluded' | 'portalsExcludedActiveOnly'
>;

export const NO_PORTALS: PortalChoice = {
  portals: [], portalsExcluded: [], portalsExcludedActiveOnly: false,
};

/* THE chip transition, for every portal control (Browse, the Watchdog editor, the
 * phone Feed): one tap moves a portal off → on it → not on it → off, and "active
 * ads only" lives only while something is excluded. Builds the three fields
 * explicitly, so a caller may pass a whole ListingFilters. lib/setScope `cycle`
 * is the one transition. */
export function cyclePortal(c: PortalChoice, p: string): PortalChoice {
  const { inc, exc } = cycle(c.portals, c.portalsExcluded, p);
  return {
    portals: inc,
    portalsExcluded: exc,
    portalsExcludedActiveOnly: exc.length > 0 && c.portalsExcludedActiveOnly,
  };
}

/* Both sides of the portal filter in words (MS19 + migration 597) — "on iDNES
 * −Sreality" — for every summary that names a cohort, so an exclusion that hides
 * most of the market is never invisible. Null when the filter is off. */
export function portalScopeLabel(
  included: ReadonlyArray<string>,
  excluded: ReadonlyArray<string>,
  excludedActiveOnly: boolean,
): string | null {
  const parts = excluded.map((p) => `−${portalShort(p)}`);
  if (included.length) parts.unshift(`on ${included.map(portalShort).join(', ')}`);
  if (!parts.length) return null;
  return parts.join(' ') + (excluded.length && excludedActiveOnly ? ' (active ads)' : '');
}

/* A listing's URL on its portal is `source_url` on the row — a fact the portal's
 * own parser stored at ingest (sreality included). Nothing here builds one:
 * sreality's sub-category slugs are its own closed vocabulary (37 "Rodinný dům"
 * is `rodinny`, not `rodinny-dum`), auction types are `drazby`/`podily`, and a
 * merged property's siblings have different triples — reconstruction was wrong
 * per code AND structurally impossible per row. Read `source_url`; when it is
 * null, link in-app. docs/design/portal-listing-url.md. */
