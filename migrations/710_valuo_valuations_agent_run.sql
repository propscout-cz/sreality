-- 710_valuo_valuations_agent_run.sql -- Which agent run paid for a Valuo valuation (#2067).
--
-- WHY. `valuo_valuation` lets a run buy a valuation through api/valuo.py's write-ahead row. The row now names its run,
-- so a call is recorded on the run as well as in the ledger, and an interrupted run that starts over still counts what
-- its first start paid for (3 per run). NULL = the website's button, every row before this one included.
--
-- No foreign key, as llm_calls.agent_run_id (704): the row is a record of money, and a run deleted (an account's
-- deletion cascades its runs) must not take it or block on it. No index: the two reads (a run's calls, an account's
-- month) run under the 3-per-run and monthly caps over a table of one row per paid call; add one when that hurts.
--
-- The setting. `agent_valuo_monthly_cap` lands holding JSON null, so no run can call Valuo until the operator
-- writes a number. Absent, null or anything but a number all refuse (api/valuo.monthly_cap).
--
-- Additive. APPLY BEFORE MERGING (apply_migration.yml): api/valuo.py's one INSERT names the column, so the website's
-- POST /properties/{id}/valuo fails without it. Idempotent: a re-apply is a no-op and never overwrites the operator's
-- number. A nullable column with no default is catalog-only, with no scan or rewrite; it takes ACCESS EXCLUSIVE on this
-- small table for that instant, and lock_timeout keeps it from queueing behind a long reader.
-- Verify:
--   select data_type from information_schema.columns
--    where table_schema = 'public' and table_name = 'valuo_valuations' and column_name = 'agent_run_id';  -- bigint
--   select value from app_settings where key = 'agent_valuo_monthly_cap';                                 -- null, or the number

set lock_timeout = '5s';

alter table public.valuo_valuations add column if not exists agent_run_id bigint;
comment on column public.valuo_valuations.agent_run_id is
  'The agent run that made this call (agent_runs.id, no foreign key: the row outlives the run). '
  'NULL = the website. Written only by api/valuo.py, in the write-ahead INSERT.';

insert into public.app_settings (key, value, description) values
  ('agent_valuo_monthly_cap', 'null',
   'Valuo valuations an agent run may take one account up to per calendar month, Europe/Prague, counting every row of '
   'that account and credential environment in valuo_valuations, the website''s included (the website itself is not '
   'bound). A JSON number. Absent or anything else = no agent may call Valuo.')
on conflict (key) do nothing;
