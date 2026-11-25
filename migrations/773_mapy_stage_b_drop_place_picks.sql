-- 773_mapy_stage_b_drop_place_picks.sql -- Mapy exit, Stage B (#2455; spec #2441 rev 2, Part 6): the picks record goes.
--
-- WHY. place_picks (737) recorded what a place box's pick named, so own place search could be gated on questions its
-- author did not write. The gate passed (#2465), the switch-over (#2453) stopped every write and one DELETE emptied it
-- on 2026-11-23 (22 rows -> 0); the soak (#2466) saw no row since. The privacy page promised every record goes "once
-- our own place search replaces Mapy.cz". Its one reader (location_data/place_picks.py), the gate's eval and both
-- DB-lane tests leave in this PR; nothing in the API, the worker or the job runner ever read it.
--
-- WHAT. pg_cron job place-picks-retention (47 3 * * *, the 12-month delete), first; then the table. No CASCADE: a plain
-- DROP refuses while a view, a matview or a foreign key still reads it. $pre$ refuses a table that holds a row (the
-- waiver below covers an empty table only) and a function body that names it.
--
-- DESTRUCTIVE, backup waived (spec #2441 Part 6 / #2455: "Drop the emptied table without backup (approved waiver:
-- deletion is the promise)"). Applied by apply_migration.yml while its PR is open, after the operator's OK on the PR
-- and a row count taken in the same minute, then the PR merges. Every statement is idempotent: a re-run is a no-op.
-- APPLY AS postgres, the job's owner: pg_cron shows a role only its own jobs, so under another role the guard skips
-- the unschedule and the post-check passes anyway (765).
-- VERIFY:
--   select to_regclass('public.place_picks');                                      -- null
--   select count(*) from cron.job where jobname = 'place-picks-retention';        -- 0
-- ROLLBACK: forward only (rule 1). 737's own statements rebuild the empty table and its job (each is idempotent).

set lock_timeout = '5s';
set statement_timeout = '60s';

do $pre$
declare
  found text;
begin
  if to_regclass('public.place_picks') is not null then
    if exists (select 1 from public.place_picks) then
      raise exception '773 refused: place_picks holds rows; the no-backup waiver covers an empty table only';
    end if;
  end if;
  select string_agg(p.oid::regprocedure::text, ', ') into found
    from pg_proc p
   where p.pronamespace = 'public'::regnamespace and p.prosrc ~ '\mplace_picks\M';
  if found is not null then raise exception '773 refused: % still name place_picks', found; end if;
end
$pre$;

do $cron$
begin
  if to_regclass('cron.job') is null then
    raise notice '773: no pg_cron here (the CI replay); nothing to unschedule';
  elsif exists (select 1 from cron.job where jobname = 'place-picks-retention') then
    perform cron.unschedule('place-picks-retention');
  end if;
end
$cron$;

drop table if exists public.place_picks;

do $post$
begin
  if to_regclass('public.place_picks') is not null then
    raise exception '773 did not land: place_picks is still there';
  end if;
  if to_regclass('cron.job') is null then
    raise notice '773: no pg_cron here; the unschedule is not asserted';
  elsif exists (select 1 from cron.job where jobname = 'place-picks-retention') then
    raise exception '773 did not land: place-picks-retention is still scheduled';
  end if;
end
$post$;

reset statement_timeout;
reset lock_timeout;
