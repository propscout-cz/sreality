-- 714: the private bucket for files an agent produces (#2073, ADR-0006), and what agent_artifacts needs to record them.
--
-- THE BUCKET. public = false and NO policy on storage.objects: no browser role, not even the owner's, reads an object at
--   Storage. The API reads with the project's secret key after agent_artifacts' RLS has shown the row to the caller
--   (GET /runs/{id}/files/{artifact}), so one gate holds, and CI exercises it. Every object goes up as bare bytes, so the
--   one allowed type also keeps an HTML or SVG type out of the bucket whoever writes. The size is hosted_code.MAX_FILE_BYTES
--   (tests/api/test_agent_files.py holds them equal).
-- THE ROW. storage_key NULL = the answer named this file and its bytes could not be kept (the run still succeeded: the
--   answer was paid for). `kind` holds the producer's media type, a fact and never what is served. One row per name per
--   run: only the attempt that finishes writes rows, in the same transaction as its verdict.
-- BYTES WITH NO ROW (an attempt that lost its lease; an account's deletion, which cascades the rows) go in agent_files.sweep,
--   which reads storage.objects on the service connection. storage_key's index keeps that NOT EXISTS cheap.
-- APPLY AROUND THE MERGE, AND BEFORE #2075 SWITCHES THE EXECUTOR ON. The merged code reads no new column and no worker runs
--   yet, so neither order breaks anything today. On a live executor without this file every upload is refused, and marking
--   the file as not stored fails its insert, so the run retries and pays again.
-- Additive and idempotent: a re-apply sets the bucket back to these limits and leaves its objects. No secret is involved.
-- Verify:
--   select public, file_size_limit, allowed_mime_types from storage.buckets where id = 'agent-files';  -- f, 20971520, {application/octet-stream}
--   select count(*) from pg_policies where schemaname = 'storage';  -- 0

set lock_timeout = '5s';

begin;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('agent-files', 'agent-files', false, 20971520, array['application/octet-stream'])
on conflict (id) do update
  set public = false, file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;

alter table agent_artifacts alter column storage_key drop not null;
create unique index if not exists agent_artifacts_run_name_uq on agent_artifacts (run_id, name);
create index if not exists agent_artifacts_storage_key_idx on agent_artifacts (storage_key) where storage_key is not null;
comment on column agent_artifacts.kind is 'The media type the producer named: a fact, never what a browser is served (agent_files.file_response).';
comment on column agent_artifacts.storage_key is 'The object in bucket agent-files; NULL when its bytes could not be kept.';

commit;

reset lock_timeout;
