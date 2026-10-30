-- 690: admit called_for 'agent_run' on llm_calls (#2065): every model call of the generic agent loop
-- (api/agents/loop.py), first from the local runner scripts/agent_run.py, later from agent-worker (#2070).
--
-- SHIP THIS BEFORE THE CODE CALLS THE MODEL: `LLMClient._record_call` INSERTs after the provider has billed, and a
-- CHECK violation there loses the completion (527's header).
--
-- The list is restated whole (a CHECK's IN-list cannot be widened in place) and diffed against the CATALOG, not
-- against file N-1: on 2026-10-29 the live constraint held exactly 527's 25 values (pg_get_constraintdef, read before
-- writing this file) and llm_calls held no other value. Nothing is removed. Since #2065 the Python `CalledFor`
-- literal is gone; tests/test_llm_called_for_census.py checks every value the code sends against this list.
--
-- Shape of 531: NOT VALID takes ACCESS EXCLUSIVE for an instant, VALIDATE scans under SHARE UPDATE EXCLUSIVE, which
-- does not block the writers of this hot append table; the accepted set only grows, so no row can fail it. Plain
-- SET, never SET LOCAL: the apply path is statement-autocommit. The DROP + ADD pair is one transaction so writers
-- never see the table with neither constraint.

set lock_timeout = '5s';

begin;

alter table llm_calls drop constraint if exists llm_calls_called_for_check;
alter table llm_calls add constraint llm_calls_called_for_check
  check (called_for in (
    'parse_url',
    'summarize_listing',
    'compare_listing_images',
    'agent_estimation',
    'extract_building_units',
    'read_floor_plan',
    'refine_skill',
    'discover_condition_markers',
    'score_listing_condition',
    'summarize_region_dispositions',
    'enrich_listing_description',
    'classify_listing_images',
    'compare_listings_visually',
    'compare_listing_site_plans',
    'compare_listing_floor_plans',
    'screen_exam_image',
    'suggest_exam_answer',
    'review_exam_image',
    'label_image_bulk',
    'extract_location_claims',
    'location_llm_bakeoff',
    'outreach_draft',
    'autodedup_judge_text',
    'autodedup_judge_vision',
    'autodedup_judge_gold',
    'agent_run'
  )) not valid;

commit;

reset lock_timeout;

alter table llm_calls validate constraint llm_calls_called_for_check;
