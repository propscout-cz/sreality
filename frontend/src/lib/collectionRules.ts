/* The collection rules every surface shares, the app's and the Chrome
 * extension's: no imports, no React, no DOM, so both bundles take this file. */

/* The save control's name and its checklist's — one verb on every surface. */
export const COLLECTION_SAVE_LABEL = 'Uložit do kolekce';

/* THE predicate for "mine" vs "shared with me" (migration 610): the API sets
 * owner_email to null exactly when the caller owns the thing. Notes carry the
 * same field, so one definition serves both. */
export function isMine(x: { owner_email: string | null }): boolean {
  return x.owner_email == null;
}

/* The one label: a shared collection names its owner, which also tells apart
 * same-named collections (every account has a "monitoring"). */
export function collectionLabel(c: { name: string; owner_email: string | null }): string {
  return isMine(c) ? c.name : `${c.name} · ${c.owner_email}`;
}

/* The save checklist's order: monitored collections first, then by name. Never
 * the server's order — GET /collections sorts by updated_at, which every
 * add/remove bumps, so the row just clicked would jump to the top. */
export function sortCollections<C extends { monitoring_enabled: boolean; name: string }>(
  collections: readonly C[],
): C[] {
  return [...collections].sort(
    (a, b) => Number(b.monitoring_enabled) - Number(a.monitoring_enabled) || a.name.localeCompare(b.name),
  );
}

/* Who else can read a SHARED note on a property sitting in `ids`: for a
 * collection I own, every recipient; for one shared with me, its owner.
 * De-duplicated and sorted, so the composer label is stable. */
export function shareAudience(
  ids: Iterable<number>,
  byId: ReadonlyMap<number, { owner_email: string | null; shared_with: readonly { email: string | null }[] }>,
): string[] {
  const emails = new Set<string>();
  for (const id of ids) {
    const c = byId.get(id);
    if (c == null) continue;
    if (isMine(c)) {
      for (const s of c.shared_with) if (s.email != null) emails.add(s.email);
    } else if (c.owner_email != null) {
      emails.add(c.owner_email);
    }
  }
  return [...emails].sort();
}
