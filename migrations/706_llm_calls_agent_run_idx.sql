-- 706: the index on llm_calls.agent_run_id that 704 declared (#2070), on its own because it cannot share a call with
-- 704's transaction. CONCURRENTLY at top level (605's shape), so writers of that hot append table never wait on a scan.
-- The DROP clears an invalid leftover from an interrupted build, which IF NOT EXISTS would otherwise accept as done.
-- Apply through apply_migration.yml (`psql -f`): an MCP apply wraps its statements in a transaction and refuses this.

set statement_timeout = '1800s';

drop index concurrently if exists llm_calls_agent_run_idx;

create index concurrently if not exists llm_calls_agent_run_idx on llm_calls (agent_run_id)
  where agent_run_id is not null;

reset statement_timeout;
