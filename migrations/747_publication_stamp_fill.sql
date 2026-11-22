-- 747_publication_stamp_fill.sql -- every property already published gets its publication stamp, so
-- that properties.settled_at alone can say "published" (operator ruling 2026-11-17: a property the
-- engine has not read is not published, so no clock may publish; toolkit/publication.py).
-- ONE-SHOT: it runs BEFORE the code that reads the stamp alone deploys, never after (see RE-RUN).
--
-- WHY. Since migration 668 a property was published when settled_at was set OR it was 6 h old, the
-- second arm read off the clock by every surface. The clock cannot tell "the engine found no
-- duplicate" from "the engine has not read the ad": on 2026-11-17 eight re-lists of properties held
-- for weeks were told as New match at their 6 h mark while the engine was braked for 10 h. The
-- maintenance pass now writes the stamp in every case (scripts/recompute_property_stats._SETTLE_SQL)
-- and every reader reads the stamp only. This file stamps the 1.15M properties the clock published.
--
-- WHAT.
--   1. settled_at = first_seen_at + 6 h on every unstamped property at least 6 h old: the very value
--      the old rule computed for it, so no surface, old code or new, sees a property move. Batched by
--      id range (5,000 ids), one commit and a 1 s sleep per batch, row locks skipped and swept on a
--      second walk. No trigger fires (properties' one trigger is BEFORE UPDATE OF is_active). STOP
--      RULE: the fill halts, keeping what it committed, when the engine lane's failed_passes
--      (worker_heartbeats) rises while it runs: the engine's reads time out under database strain and
--      each failure halves its rate, so the fill yields to it. Re-run to resume.
--   2. properties_unsettled_idx, partial on settled_at IS NULL: the stamper's candidates (the fresh
--      and those waiting for the engine, a few thousand rows) without a scan of 1.1M. Built after the
--      fill, so the fill's updates can stay heap-only; it is the table's one index naming settled_at.
--      Built as 605/746 build theirs: an INVALID leftover of a cancelled build is moved aside and
--      dropped first (IF NOT EXISTS would keep it), and the build waits for older transactions
--      with no lock_timeout (at the house 5 s that wait cancels the build and leaves it INVALID).
--
-- WHEN. Not 05:00-10:00Z (the bazos and idnes walks overlap), and on a compute size the operator has
-- judged fit for a 1.13M-row fill (.claude/skills/database/references/compute-size.md).
--
-- APPLY ORDER. BEFORE the merge, through apply_migration.yml (--ref the branch), never the MCP (it
-- wraps a file in one transaction; step 1 commits per batch and step 2 is CONCURRENTLY). Inert under
-- the code it precedes: coalesce(settled_at, first_seen_at + 6 h) reads the same moment either way.
-- The code that reads the stamp alone must not deploy before this file has run: without it every
-- older property would read unpublished. Migration 748 (the views) follows the deploy.
-- RE-RUN. Safe until the index is valid: a halted or cancelled run resumes where it stopped. Once
-- properties_unsettled_idx is valid the fill is DONE and a re-run stamps nothing: after the deploy
-- an unstamped property 6 h old is one the stamper HOLDS for the engine's read, and stamping it by
-- the clock is the very rule this change removes. ROLLBACK: forward only (rule 1).
-- Verify (the first BEFORE the deploy only; after it the stamper's held properties count here):
--   select count(*) from properties where settled_at is null and first_seen_at <= now() - interval '7 hours';  -- 0
--   select indisvalid from pg_index where indexrelid = 'public.properties_unsettled_idx'::regclass;         -- t

set lock_timeout = '5s';
set statement_timeout = 0;

do $fill$
declare
  v_max bigint;
  v_lo bigint;
  v_n bigint;
  v_total bigint := 0;
  v_walk int;
  v_failed bigint;
  v_failed_now bigint;
begin
  select coalesce((details #>> '{autodedup,failed_passes}')::bigint, 0) into v_failed
    from public.worker_heartbeats where worker = 'realtime-worker';
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'properties' and column_name = 'settled_at') then
    raise exception '747 refused: properties.settled_at is missing (apply 668 first)';
  end if;
  if exists (select 1 from pg_index i
              where i.indexrelid = to_regclass('public.properties_unsettled_idx') and i.indisvalid) then
    raise notice '747: properties_unsettled_idx is valid, the fill is done; nothing stamped';
    return;
  end if;
  select coalesce(max(id), 0) into v_max from public.properties;
  for v_walk in 1..2 loop
    v_lo := 0;
    while v_lo < v_max loop
      update public.properties p
         set settled_at = p.first_seen_at + interval '6 hours'
       where p.id in (select q.id from public.properties q
                       where q.id > v_lo and q.id <= v_lo + 5000
                         and q.settled_at is null
                         and q.first_seen_at <= now() - interval '6 hours'
                         for no key update skip locked);
      get diagnostics v_n = row_count;
      v_total := v_total + v_n;
      commit;
      if v_n > 0 then
        perform pg_sleep(1);
        select coalesce((details #>> '{autodedup,failed_passes}')::bigint, 0) into v_failed_now
          from public.worker_heartbeats where worker = 'realtime-worker';
        if v_failed_now > coalesce(v_failed, 0) then
          raise exception '747 halted after % properties: the engine lane failed a pass during the fill (% -> %); re-run when the database is quiet',
            v_total, v_failed, v_failed_now;
        end if;
      end if;
      v_lo := v_lo + 5000;
    end loop;
  end loop;
  raise notice '747: stamped % properties', v_total;
  select count(*) into v_n from public.properties
   where settled_at is null and first_seen_at <= now() - interval '7 hours';
  if v_n > 0 then
    raise exception '747 did not land: % properties older than 7 h are unstamped (re-run)', v_n;
  end if;
end
$fill$;

do $invalid$
begin
  if exists (select 1 from pg_index i
              where i.indexrelid = to_regclass('public.properties_unsettled_idx') and not i.indisvalid) then
    alter index public.properties_unsettled_idx rename to properties_unsettled_idx_invalid;
    raise notice '747: an INVALID properties_unsettled_idx from a cancelled build moved aside';
  end if;
end
$invalid$;

set lock_timeout = 0;
set statement_timeout = '1900s';

drop index concurrently if exists public.properties_unsettled_idx_invalid;

create index concurrently if not exists properties_unsettled_idx
  on public.properties (id) where settled_at is null;

set lock_timeout = '5s';
set statement_timeout = '600s';

comment on column public.properties.settled_at is
  'Publication (migrations 668/747): when the maintenance pass published this property -- the engine has read '
  'every ad it will read on it, and the pipeline settled it (first photo stored or none, no pending engine '
  'question, no group with an ad elsewhere) or 6 h had passed since first sight and since the read. Stamped once; a merge passes it to a survivor '
  'not yet published. NULL = not published. Served <=> settled_at IS NOT NULL (toolkit/publication.py).';

do $post$
begin
  if not exists (select 1 from pg_index i
                  where i.indexrelid = to_regclass('public.properties_unsettled_idx') and i.indisvalid) then
    raise exception '747 did not land: properties_unsettled_idx is missing or invalid';
  end if;
end
$post$;

reset statement_timeout;
reset lock_timeout;
