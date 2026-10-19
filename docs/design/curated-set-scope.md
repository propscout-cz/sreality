# Curated-set scope — "not in a collection", "not in a pipeline", "no tags" (2026-10-19)

Program record and the build brief. Operator rulings are verbatim facts; everything else is the
design they bind. Read with CLAUDE.md rules 16, 18 and 22.

## North star

**A curated set is one kind of filter.** Tags, collections and pipeline stages share one value
shape, one chip row, one URL grammar, one resolver and one control — on the Browse sidebar, the
phone Filtry sheet and the pipeline board. "In it", "not in it" and "in none of it" read, travel
and resolve the same way for all three, and membership is judged **in the database under the
caller's own RLS**, never as property ids on the wire. Every wave deletes more than it adds.

## Operator rulings (2026-10-19)

1. **Full build now**, including the server move (migration 629) — not a later sprint.
2. **One dedicated "not in" option per set**, rendered as a chip beside the element chips:
   collections → "V žádné kolekci"; pipeline → "Mimo pipeline"; tags → "Bez štítků" (reverses the
   10-14 "no untagged pseudo-chip" ruling for tags). The pipeline's existing whole-set include
   ("Jen nemovitosti v pipeline") survives as the chip "V pipeline". No whole-set include chip for
   collections or tags (not asked; the model supports it, the UI does not offer it).
3. "Any collection" spans **own + shared with me**, as the sharing design (10-17) ruled for every
   cohort reader; card marks keep counting own collections only.
4. "Mimo pipeline" hides **every card, terminal stages included** — "any stage" read literally.
5. Tags join the control; tags keep **AND** ("must carry every selected tag").
6. **Presets:** all three sets are lenses OUTSIDE preset identity (tags move out; 0 of 7 stored
   presets hold a tag), and **a preset click keeps the active lens** (today it silently clears
   pipeline / collections; the comment at filters.ts:1111-1116 says they combine).
   **Second half replaced 2026-10-19 (review):** "everything resets on preset click and what is
   stored in preset gets applied" — the three sets stay outside preset identity, and a preset click
   (and the phone Feed's scope chip) resets them like the broker scope and the dismissed reveal;
   `withPresetLenses` is deleted.
7. **Look:** the checkbox becomes the chip; stage chips are visible without ticking anything;
   a picked stage chip fills copper (in) or brick (out) with its badge kept.
8. **Czech** chip and summary words: "v pipeline / mimo pipeline", "v kolekci / v žádné kolekci",
   "se štítkem / bez štítků".
9. **Triage:** a card saved or funnelled while excluded leaves the list at once, without a
   reload — the dismiss behaviour (lib/useDismissal.ts drops the row in place).
10. The pipeline board's Kolekce row gets the same control ("V žádné kolekci" included).
11. "Show dismissed" stays its own toggle this sprint.
12. The contract migration (drop `property_ids_excluded`, dead `listing_feed_visible()`) pauses
    for the operator's OK + a pg_dump of the function definitions, after Railway confirms the
    rollout (asked 10-19; answer pending — default is to pause).

## Server contract — migration 629 (applied before merge)

Four functions, one clause shape per side, judged through the `*_public` membership views
(`property_tags_public`, `collection_properties_public` — own + shared by RLS —,
`property_pipeline_public`), SECURITY INVOKER so the caller's RLS applies:

| parameter | type | meaning |
| --- | --- | --- |
| `<set>_set` | text | `'in'` = has any element of the set; `'out'` = has none; NULL = off |
| `<set>_in` | bigint[] | carries the elements — tags: EVERY (AND); collections, stages: ANY; NULL or `{}` = off |
| `<set>_out` | bigint[] | carries none of the elements; NULL or `{}` = off |

for `<set>` ∈ `tags`, `collections`, `pipeline`. `browse_list_visible(...)` and
`properties_map_visible(...)` take `hide_dismissed boolean default true` first (537's exclusion
as a parameter, so the SPA reads ONE source for both lenses) then the nine;
`browse_stats_properties` / `browse_map_cells` append the nine after their last parameter.
PostgREST calls a GET function with typed parameters directly, the SQL body inlines and the
keyset index survives (probe 2026-10-19: page 2.3 ms vs 0.9 ms baseline; exact count over 177k
rows with three exclusions 208 ms vs 44 ms; inactive sides never execute). `property_ids_excluded`
stays until the contract migration. Rails: `tests/test_curated_set_scope_migration.py` (offline),
`tests/test_curated_set_scope_live.py` (replayed schema: RLS scoping, `{}`, AND for tags).

**The SPA sends a side only when it is active** (`undefined`, never `null`: postgrest-js
stringifies a null GET argument to the text `'null'`). Element ids are deduplicated by the codec.

## SPA model — `frontend/src/lib/setScope.ts`

```ts
export interface SetScope {
  set: 'in' | 'out' | null;   // whole-set side
  in: number[];               // elements that must be present (rule per set: tags EVERY, others SOME)
  out: number[];              // elements that must be absent
}
export type CuratedSet = 'tags' | 'collections' | 'pipeline';
export const CURATED_SETS: Record<CuratedSet, {
  inRule: 'every' | 'some';
  param: string;                 // 'tags' | 'collections' | 'pipeline'
  words: { in: string; out: string };   // 'v kolekci' / 'v žádné kolekci' …
  notInLabel: string;            // the dedicated chip: 'V žádné kolekci' / 'Mimo pipeline' / 'Bez štítků'
  inAnyLabel: string | null;     // 'V pipeline' for pipeline, null elsewhere (ruling 2)
}>;
```

`ListingFilters.tags`, `.collections`, `.pipeline` are each `SetScope | null` (null = off;
`DEFAULT_FILTERS` null). `tagsExcluded` and `PipelineScope` are deleted. Four fields in three
shapes become three fields in one shape.

**Normal form**, enforced by `normalizeScope()` in both the codec and every transition:
`set === 'out'` forces `in = out = []`; `set === 'in'` clears nothing but an element moved to `in`
clears `set === 'in'` (the element is more specific); an element is in at most one list; lists are
deduplicated; an all-empty scope is `null`. Contradictions are unrepresentable.

Transitions: `cycleElement(scope, id)` off → in → out → off; `cycleSet(scope)` for the dedicated
"not in" chip off ↔ out (and, where offered, the whole-set include chip off ↔ in). Both are one
generic `cycle<T>(inc, exc, x)` that `lib/portals.cyclePortal` also uses.

Predicates: `matchesScope(memberIds, scope, inRule)` (the board's in-memory rule and the phone
roll's `stillHere`), `scopeActive`, `scopeIncludes` (PresetBar's chip lights on the IN side only),
`scopeWords(set, scope)` (summaries, phone rows). `scopeArgs(f)` → the RPC/SRF argument object
with only active sides, shared by `readSource` and `buildBrowseStatsArgs`.

**URL grammar**, one codec for Browse (`filters.ts`) and the board (`pipelineState.ts`):
`<param>=any|<csv>` for the in side, `<param>_excluded=any|<csv>` for the out side. Existing
wire byte-identical: `tags=1,2`, `tags_excluded=3`, `collections=4`, `pipeline=any|5,6`. New:
`collections_excluded`, `pipeline_excluded`, `=any` on all six. Junk on ANY side degrades to
off (today `?pipeline=<junk>` meant "any stage"; uniform now — the only behaviour change on a
malformed URL, documented in filters.test.ts). An old bundle ignores unknown params and shows a
WIDER cohort, never an inverted one.

**Presets:** `PRESET_EXCLUDED_KEYS = ['tags','collections','pipeline','brokerId','showDismissed']`;
`PRESET_EXCLUDED_PARAMS` is deleted (equality computed on `toSearchParams(stripPresetExcluded(x))`);
`loadPreset` applies the preset's stored filters only, so a click resets every lens (ruling 6 as
replaced 2026-10-19). Stored presets with
`tags: [1,2]` / `tagsExcluded: [3]` coerce losslessly into a `SetScope` in `coerceStoredFilters`.

## The control — `components/filter-controls/CuratedSetFilter.tsx`

One component replaces `TagFilterControl` + `AccountTagFilter`, `CollectionsPicker` +
`PickerGroup` and `PipelineScopePicker`. Props: `{ set, elements: {id, label, adorn?, group?}[],
value: SetScope | null, onChange, query }`. Renders one loading / `ReadFailedMark` / empty
frame (the pipeline picker gains the failed-read state it never had), the optional whole-set
include chip, one `PickButton` per element cycling off → in → "− name" (aria "(excluded)"), grouped
(Mine / Shared with me / No longer available — ONE copy of the "vanished id → #id" rule), and
the dedicated "not in" chip last in the row, brick when on. While the set side is `out`, element
chips are disabled. Elements and adornments come from a per-set descriptor (tag colour dot, stage
badge + accent, collection owner label). The sidebar, the phone sheet (`SIDEBAR_WIDGETS`, one
value per registry id) and the board bar render this one control; the board omits the pipeline
set.

## Query layer

`readSource` always calls the SRF (`browse_list_visible` / `properties_map_visible`) with
`scopeArgs(f)` + `hide_dismissed: !f.showDismissed`; `HIDING_DISMISSED` and the bare-relation
branch die. `buildBrowseStatsArgs` adds the nine params and stops sending `property_ids_excluded`.
`resolveTagPrefilter`, `resolveCollectionPrefilter`, `resolvePipelinePrefilter`,
`pipelineIdsForScope`, `BrowsePrefilters.propertyIdsExcluded` and `applyPrefilters`' `.not()`
branch are deleted. The member maps stay for glyphs and the board.

**Freshness (ruling 9):** one module-level settle (as built: `lib/curationSettle.ts`, see Delivered) runs after
every curation write (pipeline add/move/remove, collection add/remove, tag attach/detach,
dismiss): it walks the Browse query cache (filters sit at `browseKeys` slot 1), re-evaluates
`matchesScope` for that property under each query's scopes with the patched member maps, drops
the row in place from cards/table pages when it no longer matches, decrements the exact count,
and invalidates map/stats keys. This replaces the 52 `pipelineScoped` / `collectionScoped` /
`cohortScoped` prop sites and closes the tag-write and phone-Feed staleness gaps.

## Registry and codegen

`FilterType.PIPELINE_SCOPE` → `FilterType.SET_SCOPE`, `UiControl.PIPELINE_SCOPE` →
`UiControl.SET_SCOPE` (net zero members); `tags`, `collections`, `pipeline` are `SET_SCOPE`,
`CATEGORY_CURATION`, `agendas={BROWSE}`, `pg_column=None`; the `tags_excluded` entry is deleted
(4 → 3). `scripts/generate_filter_registry.py` regenerates `filterRegistry.generated.ts`.
One registry assertion: every `CATEGORY_CURATION` filter is BROWSE-only. `HAND_CODED_BROWSE_FILTERS`,
`REGISTRY_KEY_MAP`, `filterLayout` band keys, `CUSTOM_REACH` and the FilterSheet FILLED fixture
follow (the `tags` custom block rejoins the Curation form block as a one-value widget).

## Deletion ledger (runtime code)

Removed: `lib/tagScope.ts`, `lib/collectionScope.ts`, `PipelineScopePicker.tsx`,
`CollectionsPicker` + `PickerGroup`, `TagFilterControl` frame, `PipelineScope` + `parsePipelineScope`,
`tagsExcluded`, `PRESET_EXCLUDED_PARAMS`, `HIDING_DISMISSED` + the bare-relation branch, the three
client resolvers + `pipelineIdsForScope` + the denylist seam, the board's second URL codec,
52 cohort-scoped prop sites, 2 of 3 "vanished id" copies, the phone summary's `pipeline_scope`
case, the registry's `tags_excluded` entry.
Added: `lib/setScope.ts`, `CuratedSetFilter.tsx`, `lib/curationSettle.ts`, two URL params,
migration 629 + its two tests.

## Waves

- **W1** migration 629 + offline pin test (done) + live test; applied via `apply_migration.yml`
  before merge; proof = one Stats call carrying `pipeline_set`.
- **W2** SPA value shape + codec + registry + query layer (UX-invisible, cohort unchanged).
- **W3** the control on sidebar / phone / board, words, PresetBar chip, preset lens carry,
  the curation settle.
- **W4** docs in the same PR (architecture rules 18/22 + the 598 paragraph + dismissal doctrine;
  CLAUDE.md rule 22 line-neutral; database skill's SRF paragraph; interface-design skill line 279;
  GLOSSARY; roadmap/operator-workflow-track.md closes "Not in collection").
- **W5** merge in the window (`autodedup.settings rt_bootstrap:rt` false), Railway commit status,
  read-only production walk (old bookmarks, phone sheet, board), then the contract migration on
  the operator's word.

## Delivered (2026-10-19, branch `feature/curated-set-scope`)

- `f2164e99` migration 629 + `248b0e68` its live rail (W1).
- `858a5b5b` layer A — value, codec, registry, query layer (W2).
- `91812539` layer B — `CuratedSetFilter` on sidebar / phone sheet / board, preset lens carry (W3).
- `1ed93180` layer C — `lib/curationSettle.ts`, the 52 cohort-scoped prop sites deleted (W3).
- Layer D — docs (W4): architecture rules 18 / 22 / dismissal doctrine / board fail-open,
  CLAUDE.md rule 22, database + interface-design skills, GLOSSARY, operator-workflow track.

**Deviations from this brief** (each reported by the layer that made it):

- The descriptor has a sixth field, `elementNoun: [one, few, many]`, so `scopeWords` prints counts
  ("v kolekci (2 kolekce)", "bez 1 fáze") from data rather than a per-set branch, and
  `elementNounWithout` for the exclusion side; `param` was dropped (the URL param IS the set's
  name, as the RPC arguments are), and the control's frame words (`what`, `empty`) live in the
  descriptor too.
- `cycleSet(scope, side)` is a two-state toggle (off ↔ out, off ↔ in), not `cycle<T>`; only
  `cycleElement` and `cyclePortal` go through the generic `cycle<T>`.
- Normal form for an element on both sides, or `=any` on both sides: **out wins** (the later step of
  the chip cycle). `cycleElement` while the set side is `out` lifts the exclusion.
- `FilterType.INT_LIST` lost its last user with the retype and was deleted (with FilterForm's
  `int_list` branch) — pure dead-path subtraction, outside the ledger.
- Grouped elements (Mine / Shared with me / No longer available): the whole-set chips get a trailing
  row of their own, so the not-in chip is still the control's last chip. Vanished `#id` chips
  CLEAR on click (tags used to cycle them). A whole-set include a link carries for a set that
  does not offer the chip (`?tags=any`, `?collections=any`) is drawn as a pressed "Se štítkem" /
  "V kolekci" chip so it can be switched off. The operator ruled **keep** (2026-10-19): the codec
  still accepts that spelling, so the chip stays.
- On the board the control's `query` joins the member-map read and the elements read
  (`joinReads`): a failure of either is the retry; loading only while both are.
- The settle is three functions in `lib/curationSettle.ts`, each taking the lens that moved
  (without it the newcomer / not-loaded re-reads would fire on every cohort hiding dismissals):
  `settlePatches(qc, propertyId, lens)` (inside `useOptimisticWrite`, rolled back on failure;
  pipeline + dismiss settle at the click), `settleRereads(qc, propertyId, lens, failed?)` (after
  the write lands) and `settleMemberWrite(qc, set, propertyId, elementId, member)`;
  `useOptimisticWrite`'s revalidate gained a third argument, `qc`. Collection and tag writes stay
  non-optimistic: `settleMemberWrite` patches the landed delta, so their drop follows the ack
  (~one round trip), not the click. **Awaiting the operator (2026-10-19):** accept this, or make
  those two writes optimistic (their own rollback paths). A row the member map judged OUT before the write while the list shows
  it means the map is stale: nothing drops, that cohort re-reads. A dropped row's page keeps
  the length it was read with (`fetched`), so infinite scroll pages on.
- Ruling 6's carry shipped for the three curated sets only (`withPresetLenses`, Browse and the
  phone Feed's scope chips); the 2026-10-19 review ruling reverted it: a preset click resets every
  lens and applies what the preset stores.
- Restore (un-dismiss) does not go through the settle: it re-reads every Browse surface, since
  the phone Feed's hidden roll is not a `ListingFilters` cohort. Only the browse-count is
  decremented in place; `no-price-count` is re-read.

**Review fixes (2026-10-19, branch `fix/curated-scope-review`):** the preset carry reverted
(ruling 6 above); the code's "unit" became "element" (`SetElement`, `cycleElement`,
`elementNoun`, the `elements` prop), since GLOSSARY's Unit is a walk's; GLOSSARY gained Curated set
and Lens.

**Still open:** the contract migration (ruling 12, operator's word after the rollout);
whole-collection / tag DELETEs and merges revalidate broadly (a pipeline add that lifts a
dismissal this device knows of re-reads every Browse read); the live rail
(`tests/test_curated_set_scope_live.py`) first runs in CI's migrations lane and does not call
`properties_map_visible` / `browse_map_cells`; W5 (merge window, Railway status, read-only
production walk).
