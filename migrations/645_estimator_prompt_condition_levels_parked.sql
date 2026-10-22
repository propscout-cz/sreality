-- 645_estimator_prompt_condition_levels_parked.sql -- the estimator prompt stops steering the agent onto the
-- condition-level filters.
--
-- WHY. Operator ruling 2026-10-20: condition scoring (rule 14) is PARKED -- "keep the scoring engine for now, but make
-- sure it does not impact anything". Its 1-5 levels were last written 2026-06-18 (~4% of properties). The PR that
-- carries this file retires the four `*_condition_level_min/_max` filters from every surface (registry, agent tool
-- schema, cohorts, Browse, Watchdog) and stops sending `target.apartment_condition_level` /
-- `target.building_condition_level` in the agent's first message. This file removes what the skills rows tell the
-- agent about them; the engine's code, tables, columns and data stay.
--
-- APPLY ORDER: BEFORE the PR's code deploys. Old code + new prompt is harmless (the agent simply stops setting two
-- filters it still has). New code + old prompt tells the agent to set filters its tool no longer has
-- (`find_comparables_relaxed` drops an unknown argument silently, `api/agent._FCR_OVERRIDE_FIELDS`) and to read
-- target facts the message no longer carries.
--
-- VERSIONING (the 638 recipe). Skills have one history mechanism: the BEFORE UPDATE trigger `skills_history_trigger`
-- (029, 052) copies the old row into `skills_history` and bumps `version`, exactly as a Settings-page edit does. The
-- trigger fires on EVERY update, so the WHERE matches only rows this file changes: a re-run or the CI replay after it
-- touches nothing and bumps no version. One UPDATE, all three edits together: rental_estimator_full_v1 v9 -> v10 (the
-- only skill whose prompt names a level; rental_estimator_v1 never did).
--
-- THE PROMPT. Three verbatim replacements, each only where present byte for byte (live 2026-10-20: v9, length 12060,
-- md5 4ae09c9577babca3df984c2b6999027b, carries all three; each block's md5 was checked against it):
--   b  the "What an ideal cohort looks like" condition bullet (migration 076);
--   s  the whole "### Three condition filters -- how to combine" section (076), replaced by a short
--      `condition_match`-only section;
--   v  one clause of the "Condition verification step" paragraph a refinement added later (live only; 076's replayed
--      prompt never carried it), which still named the derived scores.
-- Everything else stays byte-identical. A hand-edited variant is left alone and reported by a WARNING. Verify after
-- applying (a read-only dry run of the same replace() on 2026-10-20 gave exactly this): v10, length 10578,
-- md5 d756d0117e996d2217fa9015060a3be5, no `condition_level` / `_level_min` / "derived score" left.
-- A refinement still `proposed` holds a full copy of the old prompt, and Apply (`api/refiner.apply_refinement`) writes
-- it back as-is, so the same edits go to its `proposed_prompt` and `original_prompt` too (live: refinement 3, whose own
-- rewording of the verification step still mentions the derived scores -> WARNING; dismiss it or edit the skill).
-- Applied and dismissed refinements are history and stay as they are.
-- Rollback: the prior version is the newest `skills_history` row for rental_estimator_full_v1.

do $m$
declare
  b_old constant text := $b0$- comparable apartment condition AND comparable building condition
  on BOTH axes — matched via the three condition filters
  (`condition_match`, `apartment_condition_level_min`,
  `building_condition_level_min`; see the dedicated section below)
  and cross-checked against photos for cases the filters can't
  decide;
$b0$;
  b_new constant text := $b1$- comparable apartment condition AND comparable building condition
  on BOTH axes — matched via `condition_match` (see the dedicated
  section below) and cross-checked against photos for cases the
  filter can't decide;
$b1$;
  s_old constant text := $s0$### Three condition filters — how to combine

You have three filters for condition matching. Use them together,
not as substitutes. Full descriptions live in each filter's tool
schema; what follows is strategy.

- `condition_match` — multi-select on sreality's "Stav objektu"
  enum (`novostavba`, `po_rekonstrukci`, `velmi_dobry`, `dobry`,
  `pred_rekonstrukci`, `k_demolici`). Always populated on
  scraped listings.

- `apartment_condition_level_min` — INT 1-5 derived score on the
  unit itself (jádro, kuchyň, podlahy, vnitřní rozvody). 5 =
  výborný (novostavba / po kompletní rekonstrukci); 1 = umakartové
  jádro / kritický stav. NULL until the scoring phase has run on
  the listing's current snapshot.

- `building_condition_level_min` — INT 1-5 derived score on the
  building shell (zateplení, střecha, společné prostory,
  stoupačky). Same scale. The two axes commonly diverge — a
  renovated unit inside an unrenovated panelák is a 4-5 apartment
  with a 2-3 building.

The target's three values are in the first user message under
`target.condition`, `target.apartment_condition_level`, and
`target.building_condition_level`. Use them to set the filters.

How to combine on the first cohort call:

- When the target HAS a derived score on an axis: set the
  matching `*_level_min` to `target_level - 1` (one tier below).
  This gives a cohort within +/-1 tier on that axis. Match BOTH
  axes independently when both are populated — don't collapse
  them.
- Add `condition_match` on top of the level filters when the
  target's sreality enum is at the extremes (`novostavba`,
  `k_demolici`) and you want a hard categorical floor, OR when
  you're falling back from a level filter that can't be used.
- When the target is NULL on an axis (not yet scored): DO NOT
  set that `*_level_min` filter. The filter excludes every
  unscored comparable, which would silently drop most of your
  pool. Fall back to `condition_match` with the target's sreality
  enum (or a small neighbouring set — `velmi_dobry` + `dobry`
  for a mid-tier target), and cross-check qualitatively via
  `summarize_listing` / `compare_listing_images` on a few cohort
  entries.
- When the cohort comes back thin after applying level filters,
  relax by ONE tier (drop `target_level - 1` to
  `target_level - 2`) before dropping the filter entirely.
  Recording the relaxation in `warnings` is fine.

$s0$;
  s_new constant text := $s1$### Condition matching — how to filter

`condition_match` is your condition filter: a multi-select on the
listing's condition enum (`novostavba`, `po_rekonstrukci`,
`velmi_dobry`, `dobry`, `pred_rekonstrukci`, `k_demolici`, ...; the
full list is in its tool schema). The target's value is in the
first user message under `target.condition`.

- On the first cohort call set it to the target's value, or a small
  neighbouring set (`velmi_dobry` + `dobry` for a mid-tier target).
  When `target.condition` is null, leave it unset.
- One value covers the whole property: it does not split the unit
  (jádro, kuchyň, podlahy, rozvody) from the building shell
  (zateplení, střecha, společné prostory), and a renovated unit
  inside an unrenovated panelák is common. Cross-check both axes
  qualitatively via `summarize_listing` / `compare_listing_images`
  on a few cohort entries.
- When the cohort comes back thin, widen the set by one neighbouring
  value before dropping the filter entirely. Recording the
  relaxation in `warnings` is fine.

$s1$;
  v_old constant text := $v0$When condition filters alone
cannot distinguish quality (especially when derived scores are NULL),
$v0$;
  v_new constant text := $v1$When `condition_match` alone
cannot distinguish quality,
$v1$;
  changed  int;
  residual text;
begin
  update public.skills
     set system_prompt = replace(replace(replace(system_prompt, b_old, b_new), s_old, s_new), v_old, v_new),
         updated_by    = 'migration_645_condition_levels_parked'
   where strpos(system_prompt, b_old) > 0
      or strpos(system_prompt, s_old) > 0
      or strpos(system_prompt, v_old) > 0;
  get diagnostics changed = row_count;
  raise notice '645: % skill row(s) updated', changed;

  if exists (select 1 from public.skills
              where strpos(system_prompt, b_old) > 0
                 or strpos(system_prompt, s_old) > 0
                 or strpos(system_prompt, v_old) > 0) then
    raise exception '645 did not land: a skill prompt still carries a replaced condition-level block';
  end if;

  select string_agg(name, ', ' order by name) into residual
    from public.skills
   where strpos(system_prompt, 'condition_level') > 0
      or strpos(lower(system_prompt), 'derived score') > 0;
  if residual is not null then
    raise warning '645: prompt(s) of % still mention the condition levels (not the verbatim blocks) -- edit them in Settings',
      residual;
  end if;

  update public.skill_refinements
     set proposed_prompt = replace(replace(replace(proposed_prompt, b_old, b_new), s_old, s_new), v_old, v_new),
         original_prompt = replace(replace(replace(original_prompt, b_old, b_new), s_old, s_new), v_old, v_new)
   where status = 'proposed'
     and (strpos(proposed_prompt, b_old) > 0 or strpos(proposed_prompt, s_old) > 0
          or strpos(proposed_prompt, v_old) > 0 or strpos(original_prompt, b_old) > 0
          or strpos(original_prompt, s_old) > 0 or strpos(original_prompt, v_old) > 0);
  get diagnostics changed = row_count;
  raise notice '645: % proposed refinement(s) updated', changed;

  select string_agg(id::text, ', ' order by id) into residual
    from public.skill_refinements
   where status = 'proposed'
     and (strpos(proposed_prompt, 'condition_level') > 0
          or strpos(lower(proposed_prompt), 'derived score') > 0);
  if residual is not null then
    raise warning '645: proposed refinement(s) % still mention the condition levels -- dismiss them or edit the skill after applying',
      residual;
  end if;
end
$m$;
