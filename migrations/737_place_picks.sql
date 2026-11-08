-- 737: place picks -- what a place box's pick named, recorded so own place search can be gated on questions its
-- author did not write (spec #2441 Part 1, ruling Q10; ticket #2445). Telemetry, not a location, and it ends.
--
-- WHO WRITES: only `/maps/resolve` (api/main.py), one INSERT as a background task after the answer is sent, through
--   location_data/place_picks.py, for a verified caller without the `automation` marker and a pick that resolved to a
--   level. Append-only.
-- WHO READS: location_data/place_picks.picks_between (the DB-lane test and the gate's eval, #2452). Own place search
--   never reads it: the picks are its holdout. No browser role reads or writes it: RLS on with no policy, everything
--   revoked from public/anon/authenticated, as 700 does.
-- DATA KEPT: when, the text the list answered (NULL when any character of it is a digit, the API's `isdigit`; else
--   stored exactly as answered), the 1-based rank, the level and our RÚIAN code (NULL = refused). No account, nothing
--   from Mapy.cz. `level` is the bound level, the row type's level when refused, `momc` for a městská část (resolve
--   still answers its části).
-- The digit CHECK pins the ICU collation: under libc (CI's default) [[:digit:]] matches ASCII only, under ICU (prod's)
--   every decimal digit, the full-width "２" too.
-- RETENTION: pg_cron job place-picks-retention, daily 03:47, deletes rows over 12 months old. Once the switch-over
--   deploys, one DELETE empties the table (#2453); Stage B drops it (#2455).
-- APPLY: before the PR that writes it merges. Additive: one table and one cron job; every statement is idempotent.

set statement_timeout = '60s';

create table if not exists public.place_picks (
  created_at timestamptz not null default now(),
  text       text,
  rank       smallint    not null,
  level      ruian_level not null,
  code       bigint,
  constraint place_picks_rank check (rank between 1 and 10),
  constraint place_picks_text check (
    text is null or (char_length(text) between 1 and 200 and (text collate "und-x-icu") !~ '[[:digit:]]'))
);

alter table public.place_picks enable row level security;
revoke all on public.place_picks from public, anon, authenticated;

comment on table public.place_picks is
  'Place-box picks (mig 737, ruling Q10): written only by POST /maps/resolve via location_data/place_picks.py, '
  'append-only, own place search''s holdout. NULL text = the text held a digit; NULL code = refused. Rows over '
  '12 months are deleted daily (pg_cron place-picks-retention).';

-- Guarded like 674: the CI replay has no pg_cron.
do $cron$
begin
  perform cron.schedule(
    'place-picks-retention',
    '47 3 * * *',
    $$set statement_timeout='60s'; delete from public.place_picks where created_at < now() - interval '12 months';$$
  );
exception when others then
  raise notice 'pg_cron unavailable; place-picks-retention not scheduled (%).', sqlerrm;
end
$cron$;

do $post$
begin
  if not (select relrowsecurity from pg_class where oid = 'public.place_picks'::regclass) then
    raise exception '737: place_picks has row level security off';
  end if;
  if has_table_privilege('anon', 'public.place_picks', 'select, insert, update, delete')
     or has_table_privilege('authenticated', 'public.place_picks', 'select, insert, update, delete') then
    raise exception '737: a browser role reaches place_picks';
  end if;
  if to_regclass('cron.job') is null then
    raise notice '737: no pg_cron here; the schedule is not asserted';
  elsif not exists (select 1 from cron.job
                     where jobname = 'place-picks-retention' and schedule = '47 3 * * *' and active
                       and command like '%statement_timeout%place_picks%12 months%') then
    raise exception '737: place-picks-retention is not scheduled daily under a statement_timeout';
  end if;
end
$post$;
