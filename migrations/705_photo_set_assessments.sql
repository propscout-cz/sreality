-- 705: the cache of the agents' structured photo assessment (#2066, ADR-0007), and 'assess_property_visuals' on
-- llm_calls.
--
-- ONE ROW PER (photo set, prompt version): a market fact every agent of every account shares, so a property is judged
-- once. The row names no account, user, run, property or ad, and holds no llm_calls id (that row can name a run, a run
-- an account): only what the pictures got and what the model said. A photo set is an ad's own public photos (never an
-- operator attachment), hashed by toolkit/photo_assessment.py; the version is the hash of skills/photo-facets.md.
--
-- SERVICE-ROLE ONLY, like every other market mirror nothing in the browser reads: RLS on, no policy, every grant revoked
-- from the browser's roles. The one reader and writer is the tool's toolkit module on the service connection; the
-- agent reads it through that tool's result, and Browse, the map, stats, the watchdog and the estimator read nothing
-- here (tests/test_photo_assessment_census.py). No browser grant keeps "Browse shows no facet" (ADR-0007) a fact of the
-- schema, not a habit of the SPA.
--
-- NOT A CONDITION SCORE: rule 14's parked levels and listing_condition_scores are not touched and not read.
--
-- SHIP THIS BEFORE AN AGENT LISTS THE TOOL: `LLMClient._record_call` INSERTs after the provider has billed, and a CHECK
-- violation there loses the completion (527's header). The list is restated whole and was diffed against the CATALOG on
-- 2026-11-01 (pg_get_constraintdef: exactly 690's 26 values). Nothing is removed. Independent of 704, which does not
-- restate this CHECK: the two apply in either order.
--
-- Shape of 690: NOT VALID, then VALIDATE under SHARE UPDATE EXCLUSIVE, so the writers of this hot append table never
-- wait on the scan. Plain SET, never SET LOCAL: the apply path is statement-autocommit.

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
    'agent_run',
    'assess_property_visuals'
  )) not valid;

commit;

reset lock_timeout;

alter table llm_calls validate constraint llm_calls_called_for_check;

create table if not exists public.photo_set_assessments (
  photo_set_hash text          not null,
  prompt_version text          not null,
  facets         jsonb         not null,
  model          text          not null,
  photos_used    integer       not null,
  plans_used     integer       not null,
  cost_usd       numeric(10,6) not null,
  created_at     timestamptz   not null default now(),
  primary key (photo_set_hash, prompt_version)
);

comment on table public.photo_set_assessments is
  'One judgement per photo set and prompt version, shared by every agent (ADR-0007). Service role only; Browse reads nothing here.';

alter table public.photo_set_assessments enable row level security;
revoke all on public.photo_set_assessments from public, anon, authenticated;
