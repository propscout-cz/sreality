-- 638_retire_manual_estimates_tool.sql -- the estimator skills stop naming `get_manual_rental_estimates`.
--
-- WHY. Operator ruling 2026-10-19 (Valuo sprint, ruling 7): the "Ruční odhady" feature (manual rental estimates,
-- Phase U-ME, migrations 046/047) is retired end to end; its tables never held a row. The agent tool goes with the
-- code in the same PR. This file removes what the skills rows say about it; 639 drops the tables.
--
-- APPLY ORDER: BEFORE the PR's code deploys. `api/agent.py` builds a run's tool list as
-- `AGENT_TOOLS[name] for name in skill.allowed_tools`: a listed name with no registry entry is a KeyError, so once
-- the registry entry is gone every run of a skill still listing the tool fails ("KeyError:
-- 'get_manual_rental_estimates'"). Applied first, the still-deployed code simply stops offering the tool. (Edits
-- through the Settings page already refuse a name outside the registry: `api/skills._validate_allowed_tools`.)
--
-- VERSIONING. Skills have one history mechanism: the BEFORE UPDATE trigger `skills_history_trigger` (029, 052)
-- copies the old row into `skills_history` and bumps `version`. The Settings page (PUT /admin/skills/{name} ->
-- `api/skills.update_skill`) is a plain UPDATE through that same trigger, so this UPDATE keeps the prior prompt and
-- tools exactly as an operator edit would. The trigger fires on EVERY update, so the WHERE matches only rows this
-- file changes: a re-run or the CI replay after it touches nothing and bumps no version. One UPDATE per row, both
-- edits together: rental_estimator_full_v1 v8 -> v9, rental_estimator_v1 (archived, still loadable) v1 -> v2.
--
-- THE PROMPT. Only the exact "Consult manual estimates" bullet that migrations 070/076 wrote into
-- rental_estimator_full_v1 is removed, and only where it is present verbatim (live 2026-10-19: v8, length 12426,
-- md5 51d01b8f3297cd31f35e70a696b8c9bc, carries it). A hand-edited variant is left alone and reported by a WARNING.
-- rental_estimator_v1's live prompt never carried the step (047 left it to the operator).
-- A refinement still `proposed` holds a full copy of the old prompt, and Apply (`api/refiner.apply_refinement`)
-- writes it back as-is, so the same bullet goes from its `proposed_prompt` and `original_prompt` too (the pair the
-- review diff compares); applied and dismissed refinements are history and stay as they are.
-- Rollback: the prior version is the newest `skills_history` row for that name.

do $m$
declare
  tool   constant text := 'get_manual_rental_estimates';
  bullet constant text := $b$- **Consult manual estimates.** Call `get_manual_rental_estimates`
  on the subject's sreality_id once before `record_estimate`.
  Manual figures are operator judgement, not comparables — never
  replace your distribution with one, but if your point estimate
  diverges from any manual figure by > 15%, name each manual
  figure and its `source_kind` in a warning.

$b$;
  changed  int;
  residual text;
begin
  update public.skills
     set allowed_tools = allowed_tools - tool,
         system_prompt = replace(system_prompt, bullet, ''),
         updated_by    = 'migration_638_retire_manual_estimates'
   where allowed_tools ? tool
      or strpos(system_prompt, bullet) > 0;
  get diagnostics changed = row_count;
  raise notice '638: % skill row(s) updated', changed;

  if exists (select 1 from public.skills where allowed_tools ? tool) then
    raise exception '638 did not land: a skill still lists %', tool;
  end if;
  if exists (select 1 from public.skills where strpos(system_prompt, bullet) > 0) then
    raise exception '638 did not land: a skill prompt still carries the manual-estimates bullet';
  end if;

  select string_agg(name, ', ' order by name) into residual
    from public.skills where strpos(system_prompt, tool) > 0;
  if residual is not null then
    raise warning '638: prompt(s) of % still mention % (not the verbatim bullet) -- edit them in Settings',
      residual, tool;
  end if;

  update public.skill_refinements
     set proposed_prompt = replace(proposed_prompt, bullet, ''),
         original_prompt = replace(original_prompt, bullet, '')
   where status = 'proposed'
     and (strpos(proposed_prompt, bullet) > 0 or strpos(original_prompt, bullet) > 0);
  get diagnostics changed = row_count;
  raise notice '638: % proposed refinement(s) updated', changed;

  select string_agg(id::text, ', ' order by id) into residual
    from public.skill_refinements where status = 'proposed' and strpos(proposed_prompt, tool) > 0;
  if residual is not null then
    raise warning '638: proposed refinement(s) % still mention % -- dismiss them or edit the skill after applying',
      residual, tool;
  end if;
end
$m$;
