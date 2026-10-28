/* The marks every extension surface draws — the detail panel and the
 * search-page card controls — as inline SVG copies of the SPA's icon
 * components (React, so not importable), and the save rules the extension adds
 * on top of the app's shared ones (lib/collectionRules). */

import { isMine, sortCollections } from '../../frontend/src/lib/collectionRules';
import type { StageLook } from '../../frontend/src/lib/pipelineStage';
import type { Collection } from '../../frontend/src/lib/types';
import type { PipelineMembership } from './types';

/* The SPA's <FunnelIcon> (icons.tsx) as inline SVG — the shared "pipeline"
 * glyph on every surface (a funnel with three arrows; filled body =
 * in-pipeline). */
export function funnelIconSvg(filled: boolean): string {
  const f = filled ? 'currentColor' : 'none';
  return (
    '<svg class="pipeline-icon" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true">' +
    '<line x1="7.5" y1="2" x2="7.5" y2="5.5"/><polyline points="6.2,4 7.5,5.7 8.8,4"/>' +
    '<line x1="12" y1="2" x2="12" y2="5.5"/><polyline points="10.7,4 12,5.7 13.3,4"/>' +
    '<line x1="16.5" y1="2" x2="16.5" y2="5.5"/><polyline points="15.2,4 16.5,5.7 17.8,4"/>' +
    `<path d="M4 8 H20 L13.5 15 V21 H10.5 V15 Z" fill="${f}"/></svg>`
  );
}

/* The SPA's <CollectionMark> (a bookmark; filled = in at least one collection)
 * — the collection glyph on every surface, and deliberately not the funnel:
 * collections are many-to-many groupings, the pipeline is the one deal state
 * (rule #22), and the two must never look alike. */
export function bookmarkIconSvg(filled: boolean): string {
  const f = filled ? 'currentColor' : 'none';
  return (
    `<svg class="collection-icon" viewBox="0 0 16 16" fill="${f}" ` +
    'stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M4 2.5 H12 V13.5 L8 10.75 L4 13.5 Z" stroke-linecap="round"/></svg>'
  );
}

/* The bell that marks a MONITORED collection in the checklist (the SPA menu's
 * BellGlyph): membership there is what turns into change alerts. */
export function bellIconSvg(): string {
  return (
    '<svg class="coll-bell-icon" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M6 9 a6 6 0 0 1 12 0 c0 5 1.5 6.5 2.5 7.5 H3.5 C4.5 15.5 6 14 6 9 Z"/>' +
    '<path d="M10 20 a2 2 0 0 0 4 0"/></svg>'
  );
}

/* The SPA's <EyeOffIcon> (icons.tsx). `filled` = dismissed. */
export function eyeOffIconSvg(filled: boolean): string {
  const fill = filled ? ' fill="currentColor" fill-opacity="0.25"' : '';
  return (
    '<svg class="collection-icon" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M2.5 12 C5 7.5 8.3 5.5 12 5.5 S19 7.5 21.5 12 C19 16.5 15.7 18.5 12 18.5 ' +
    `S5 16.5 2.5 12 Z"${fill}/>` +
    '<circle cx="12" cy="12" r="3"/><line x1="4" y1="20" x2="20" y2="4"/></svg>'
  );
}

/* A membership's stage as the app's stage helpers take it (lib/pipelineStage
 * stageAccent / stageBadge); null out of the pipeline. */
export function stageLook(p: PipelineMembership | null | undefined): StageLook | null {
  return p?.in_pipeline && p.stage_id != null ? { id: p.stage_id, color: p.stage_color, code: p.stage_code } : null;
}

/* Whether a held list knows every collection a lookup names. RLS means a fresh
 * read holds every id a lookup can return, so an unknown id means the held
 * list is stale (made or shared since it was read) — re-read it. */
export function knowsAll(
  collectionIds: readonly number[] | null | undefined,
  collections: Collection[],
): boolean {
  const held = new Set(collections.map((c) => c.id));
  return (collectionIds ?? []).every((id) => held.has(id));
}

/* The save checklist's rows: MINE only, in its order. A view-only collection is
 * never a save target, so it is not offered (no disabled variant). */
export function saveTargets(collections: Collection[]): Collection[] {
  return sortCollections(collections.filter(isMine));
}

/* What "saved" means on every mark: the property's collections that are MINE,
 * checklist order — so a mark always agrees with the checklist, and a property
 * that sits only in someone else's collection never reads as saved. Null while
 * the collection list is unknown and the property sits in any collection:
 * without the list mine cannot be told from shared. */
export function savedCollections(
  collectionIds: readonly number[] | null | undefined,
  collections: Collection[] | null,
): Collection[] | null {
  const ids = new Set(collectionIds ?? []);
  if (ids.size === 0) return [];
  if (collections == null) return null;
  return saveTargets(collections).filter((c) => ids.has(c.id));
}
