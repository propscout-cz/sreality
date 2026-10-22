-- 627_region_annotations_drop.sql -- the Browse > Stats LLM "annotations" feature goes, its cache and its settings.
--
-- WHY. Operator ruling 2026-10-19 (docs/design/stats-dashboard.md, ruling 1): the Stats tab becomes a dashboard the
-- user composes (migration 626), and the four fixed cards go with everything that only served them. One of those was
-- `summarize_region_dispositions` (migration 104): an LLM sentence per disposition box in the old Kc/m2 card, cached
-- per (filter set, calendar day) in `region_disposition_annotations`, prompted from two app_settings rows. The box it
-- annotated no longer exists, so the sentence has nothing to describe. The code is deleted in the same PR as 626
-- (toolkit/region_annotations.py, POST /tools/summarize_region_dispositions, its schemas, the SPA query); this file
-- removes what the code read and wrote.
--
-- ===========================================================================
-- DESTRUCTIVE. Applied only on the operator's word, AFTER the code is gone (the PR carrying 626 merged and its
-- API / SPA rollout finished): an API still serving the old route would hit a missing table. The cache is a cache
-- (re-derivable by nothing now, and needed by nothing); the two settings are prompt text kept in migration 104 and
-- 426. Forward only (rule 1): there is no restore and none is wanted.
-- ===========================================================================
--
-- WHAT GOES
--   1. public.region_disposition_annotations (and with it its own index, sequence and its FK to llm_calls;
--      nothing references it -- no view, function or FK names it since 104).
--   2. app_settings rows llm_region_annotation_system_prompt / llm_region_annotation_model.
-- WHAT STAYS. `llm_calls` rows with called_for = 'summarize_region_dispositions' (cost history), and that value in
-- llm_calls_called_for_check, which every later migration that re-states the check carries: a CHECK that refuses
-- its own history cannot be re-validated. No CASCADE.
--
-- Idempotent and replay-safe (`if exists`, a DELETE of absent keys is a no-op): CI's schema replay runs it.

set lock_timeout = '5s';
set statement_timeout = '60s';

begin;

drop table if exists public.region_disposition_annotations;

delete from public.app_settings
 where key in ('llm_region_annotation_system_prompt', 'llm_region_annotation_model');

do $post$
begin
  if to_regclass('public.region_disposition_annotations') is not null then
    raise exception '627 did not land: region_disposition_annotations still exists';
  end if;
  if exists (select 1 from public.app_settings
              where key in ('llm_region_annotation_system_prompt', 'llm_region_annotation_model')) then
    raise exception '627 did not land: an llm_region_annotation_* setting survived';
  end if;
end
$post$;

commit;

reset statement_timeout;
reset lock_timeout;
