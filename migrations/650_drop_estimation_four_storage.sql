-- 650_drop_estimation_four_storage.sql -- the retired LLM URL parser's storage goes (W4 of
-- docs/design/new-property-from-link.md; ruling 5, 2026-10-20).
--
-- WHY. W2 (#1924, merged 2026-10-21) deleted the parser (scraper/source_dispatcher, scraper/source_parsers,
-- scraper/url_parser), both /estimations/preview routes, POST /buildings/from_url and every reader and writer of
-- these columns; an estimation is always of an ad we hold, so nothing produces a parse kind, a confidence, a page
-- body or "subject attributes" any more. Live before the drop (2026-10-21): parsed_url_cache 12 rows, all expired,
-- last write 2026-07-23; source_html on 10 estimation runs (~1 MB), subject_attributes on 0 of 106 runs,
-- building_runs 0 rows ever; the two llm_parse_* settings read by nothing; the 'idnes_reality' portals row a relic
-- of migration 100's on-demand parser registry (listings.source is 'idnes'; 0 listings carry 'idnes_reality', and
-- no foreign key points at portals). The only dependants (pg_depend, 2026-10-21): parsed_url_activity reads
-- parsed_url_cache, and portal_health_mv reads parsed_url_activity; no view reads any of the nine columns and no
-- function body names any of these objects.
--
-- ===========================================================================
-- DESTRUCTIVE. Applied only with the operator's OK (given 2026-10-20, ruling 5) and AFTER a backup:
--   gh workflow run backup_before_drop.yml -f label=estimation-four-storage \
--     -f targets="estimation_runs:id,source_kind,parse_confidence,parse_confidence_per_field,source_html,subject_attributes building_runs:id,source_kind,parse_confidence,parse_confidence_per_field,source_html app_settings portals"
-- (app_settings and portals whole: both small, and the two deleted settings' current values exist nowhere else,
-- see WHAT GOES 4).
-- and only AFTER W2's rollout (done 2026-10-21: no deployed code reads these). The parser's TABLE parsed_url_cache
-- and its view parsed_url_activity are NOT dropped here: the legacy matview portal_health_mv still reads the view and
-- refresh_health_matviews() (pg_cron, every 10 min) refreshes that matview unguarded, so they go with migration 643
-- (#1980, the Health layer drop), whose header already sequences them. Never CASCADE: anything else that still
-- depends on the nine columns must fail the apply, not vanish with it.
-- Forward only (rule 1): no restore wanted.
-- ===========================================================================
--
-- WHAT GOES
--   1. (deferred to 643/#1980) table public.parsed_url_cache (020) + view public.parsed_url_activity (318).
--   2. estimation_runs: source_kind (+ its CHECK + index), parse_confidence (+ CHECK), parse_confidence_per_field,
--      source_html (020), subject_attributes (138).
--   3. building_runs: source_kind, parse_confidence, parse_confidence_per_field, source_html (035).
--   4. app_settings rows llm_parse_model + llm_parse_system_prompt (020). Their earlier values stay in
--      app_settings_history; the current ones live only in the backup: 020's history trigger fires on UPDATE, so a
--      delete archives nothing.
--   5. the portals row 'idnes_reality' (100/163); portals.kind stays (scraper/field_census.py reads it).
-- Untouched on purpose: llm_calls.called_for keeps 'parse_url' as a historical value; estimation_runs.input_url stays
-- (the 13 historic unbound runs render through it).
--
-- LOCKS. Column drops take a brief ACCESS EXCLUSIVE lock on estimation_runs (106 rows) and building_runs (0 rows);
-- the 5 s lock_timeout makes a drop give up instead of queueing. Every statement is idempotent (`if exists`), so a
-- timeout is re-run and the CI replay runs it too.

set lock_timeout = '5s';
set statement_timeout = '60s';

begin;

alter table public.estimation_runs
  drop column if exists source_kind,
  drop column if exists parse_confidence,
  drop column if exists parse_confidence_per_field,
  drop column if exists source_html,
  drop column if exists subject_attributes;

alter table public.building_runs
  drop column if exists source_kind,
  drop column if exists parse_confidence,
  drop column if exists parse_confidence_per_field,
  drop column if exists source_html;

delete from public.app_settings where key in ('llm_parse_model', 'llm_parse_system_prompt');

delete from public.portals where source = 'idnes_reality';

do $post$
begin
  if exists (select 1 from information_schema.columns
              where table_schema = 'public'
                and column_name in ('source_kind', 'parse_confidence', 'parse_confidence_per_field', 'source_html',
                                    'subject_attributes')
                and table_name in ('estimation_runs', 'building_runs')) then
    raise exception '650 did not land: a parse column is still on estimation_runs or building_runs';
  end if;
  if exists (select 1 from public.app_settings where key in ('llm_parse_model', 'llm_parse_system_prompt'))
     or exists (select 1 from public.portals where source = 'idnes_reality') then
    raise exception '650 did not land: an llm_parse_* setting or the idnes_reality portals row remains';
  end if;
end
$post$;

commit;

reset statement_timeout;
reset lock_timeout;
