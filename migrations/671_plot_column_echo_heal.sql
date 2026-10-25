-- 671_plot_column_echo_heal.sql -- clear the stored plots that are not plots: a flat's (every one), and a
-- commercial unit's that merely repeats its own floor figure.
--
-- ===================================================================================================
-- DESTRUCTIVE (a stored value becomes NULL). APPLY ONLY AFTER the plot rule (`scraper.area.stated_plot`,
-- same PR) is MERGED AND DEPLOYED, with the off-database backup taken (step 3 below) AND the operator's
-- explicit OK. Never applied by the PR that adds it.
-- ===================================================================================================
--
-- THE RULING. General ruling 3 (2026-09-30) + the operator's answers of 2026-10-01, rebuilt on current main by
-- the operator's ruling 5a (2026-10-25; the originals were PR #1668 + #1669, migration 583, never merged). On 14
-- of 14 live pages opened (idnes, realitymix, mmreality; ceskereality answered 403 and was read from its stored
-- pages) the portal ITSELF prints one figure in the plot box and the floor-area box. Not our key maps: the
-- missing rule was "a plot equal to the floor area on a commercial unit is not a plot" and "a flat never carries
-- a plot at all". That rule is `scraper.area.stated_plot`, called BEFORE the content hash at the contract
-- boundary (`ScrapedListing.__post_init__`; sreality's `parse_listing`), so a live row clears at its next detail
-- fetch with one genuine snapshot. This file clears what is already STORED, which no ingest path can:
-- `scripts/reparse.py` never blanks (`_merged`: a re-derive that yields None keeps the stored value), and an
-- inactive or unchanged row is never refetched.
--
-- THE RAILS. The predicate below is exactly the parser rule over the stored cells. Its literals are pinned to the
-- Python constants by tests/test_migration_671_rails.py (the category sets, the labelled bases, and the
-- predicate replayed against `stated_plot` over a grid) and the file is EXECUTED over seeded hits and misses by
-- tests/test_migration_671_live.py (CI migrations job):
--   P1  byt with any estate_area (`PLOT_FREE_CATEGORIES`): a flat never carries a plot. On ceskereality the
--       flat "plots" were the doubled floor figure, the placeholder 1, the whole building's parcel or a
--       near-floor figure (fixture b1: plot 64 beside užitná 59) -- none the unit's own land.
--   P2  komercni whose estate_area equals the usable measure, OR equals the headline `area_m2` when
--       `area_basis` is a LABELLED interior measure ('usable' / 'floor' / 'total' = `PLOT_ECHO_BASES`).
--       NOT compared, by the ruling: a title / prose fallback (basis 'unknown' -- "Prodej areálu 3 400 m²" IS
--       the site), zastavěná plocha (a building may cover its whole parcel), and a HOUSE (the operator kept the
--       dum rule as it is; a garage's parcel really is its footprint). Equality is the columns' own:
--       numeric(9,1) against numeric(9,1) / numeric(7,1) -- what the Python rule compares too (0.1 m², half up).
-- NOT HERE: dum, pozemek (area_m2 IS the plot), ostatni. UNDER-HEAL, NAMED (not a false hit): a komerční echo
-- that predates the basis stamp with `usable_area` NULL (the realitymix "Plocha" shape: usable NULL, basis
-- NULL) is missed; the usable arm still catches a pre-stamp row whose `usable_area` carries the figure.
--
-- CENSUS (2026-10-25, read-only, this file's own predicate; 2026-09-30 counted ACTIVE rows only):
--   P1 byt       active 3,218   inactive 6,751
--   P2 komercni  active 2,356   inactive 2,079
--   = 14,404 listings on 13,244 properties.
--   Active rows with area_basis NULL among the hits: 1,579 (ceskereality byt 654, ceskereality komercni 499,
--   idnes komercni 367, realitymix komercni 59) -- rows whose last detail write predates the basis stamp.
--   sreality: 7 P1 rows, 1 P2 row (it publishes no plot on a flat or a commercial unit as a rule).
--
-- THE COUNT QUERY (read-only). Run it before the apply, record it with the apply, and again after:
--
--   select rail, source, category_main, is_active, area_basis, count(*) as n from (
--     select case when category_main = 'byt' then 'P1' else 'P2' end as rail,
--            source, category_main, is_active, area_basis
--       from public.listings
--      where estate_area is not null
--        and (category_main = 'byt'
--             or (category_main = 'komercni'
--                 and (estate_area = usable_area
--                      or (estate_area = area_m2
--                          and area_basis in ('usable','floor','total')))))
--   ) r group by rail, source, category_main, is_active, area_basis
--   order by rail, source, category_main, is_active, area_basis;
--
-- STOP CONDITIONS -- do not apply; hand-read the rows first (and report them) when:
--   1. any row's category_main is not 'byt' or 'komercni' (impossible by construction: the predicate moved).
--   2. any P2 row carries area_basis = 'plot': a dwelling stamped as land is a row re-categorised after a
--      land-era derive -- OUR residue, not the advertiser's. 0 on 2026-10-25.
--   3. the ACTIVE komerční rows with area_basis NULL (925 on 2026-10-25) have not been sampled: they hit by the
--      usable arm alone, and an active unstamped row was never re-derived. (A flat hits whatever its basis.)
--   4. the ACTIVE P2 total is over 3,000 or the ACTIVE P1 total is over 4,000, or all rails over 18,000: the
--      predicate or the data moved since the census above.
--   5. more than 10 P1 rows on sreality, or more than 1 P2 row on sreality -- read the one there is first.
--
-- APPLY ORDER.
--   1. The plot rule merged AND deployed (Railway's commit status for the realtime-worker + the next detail
--      drain on main). Before that, a live detail refetch RE-WRITES an echo (`estate_area` is a `structured`
--      cell, so the parse's value lands on every write); a row that did gets caught by a re-run after deploy.
--      Between the merge and the apply, verify_pipeline's `migration_drift` reads this file's backup table
--      ABSENT -- expected, and gone at the apply.
--   2. The COUNT QUERY, compared with the census; the stop conditions; a stratified hand-read of P2 hits by
--      portal x komerční subtype (garáž, sklad, výroba/areál, ubytování, činžovní dům, zemědělský) -- the
--      #1630 -> #1637 lesson. Known false-positive classes: the reverse case (idnes 19082282, a campsite whose
--      land is typed into both boxes: the TRUE plot goes) and a garage whose parcel really is its floor area.
--   3. The off-database copy (to R2, the database skill's destructive gate):
--        gh workflow run backup_before_drop.yml -f label=plot-echo-heal -f targets="listings:id,estate_area"
--   4. The operator's OK (destructive).
--   5. The autodedup programme told, and its lane paused if it wants a fresh seed (interval 0): this file writes
--      NO snapshot, so the lane's `changed` feed (listing_snapshots) never sees a healed row, and the engine's
--      plot features (`plot_area_rel_diff` / `plot_area_exact`) read the echo until it re-reads the rows.
--   6. gh workflow run apply_migration.yml --ref main -f file=671_plot_column_echo_heal.sql -f dry_run=false
--      -f confirm=APPLY
--   7. `rt_seed fresh=true` (or what the programme decides), then resume the lane; re-run the COUNT QUERY --
--      expected: no rows (re-run this file if a row a concurrent drain held was skipped).
--
-- HOW. One transaction; one statement does the work: a `hit` CTE (FOR UPDATE SKIP LOCKED -- a row a drain is
-- writing right now is left for a re-run instead of waited on) -> every hit copied into
-- backup_671.listing_plots (id, property, source, category, liveness, rail, the plot, usable_area, area_m2,
-- area_basis) -> a compare-and-set UPDATE to NULL -> every touched property enqueued into dirty_properties in the
-- SAME statement (rule 20), stamped with the statement clock so a maintenance pass whose cutoff falls inside this
-- transaction cannot clear the mark before it recomputed (the ids made distinct BEFORE the stamp, because
-- ON CONFLICT may touch a row only once). `browse_list` `plot_area_m2` (= estate_area off land, migration 534)
-- follows on that lane's pass. The seam's rules (R9, as 554 / 573 / 642): no listing_snapshots row (the sanctioned
-- rule-2 exception for correcting our own reading of what is already stored -- each healed LIVE row appends one
-- genuine snapshot at its next detail fetch; its stored snapshot keeps the old value, the accepted asymmetry),
-- never last_seen_at (rule 4), and area_basis untouched (it belongs to area_m2, correct on every hit). R11 ("never
-- NULL a stated fact") is honoured by the ruling itself: the figure is ruled not to be a plot, it stays stored as
-- the floor area, and the page's own statement stays in `raw_json` / `portal_raw_pages`.
-- IDEMPOTENT: a second run selects nothing; the backup keeps the FIRST value it saw per listing (on conflict do
-- nothing), and its DDL is `if not exists`, so apply_migration.yml's whole-file retry after a lock timeout is safe.
-- Replay-safe on CI's empty schema.
--
-- RESTORE (only where the cell is still NULL, so a newer value wins; enqueues as the heal did):
--   with restored as (
--     update public.listings l set estate_area = b.estate_area
--       from backup_671.listing_plots b
--      where b.listing_id = l.id and l.estate_area is null
--     returning l.property_id)
--   insert into dirty_properties (property_id, marked_at)
--   select d.property_id, clock_timestamp()
--     from (select distinct property_id from restored where property_id is not null) d
--   on conflict (property_id) do update set marked_at = excluded.marked_at;
-- The backup goes after a month live, as a FORWARD migration (`drop table if exists backup_671.listing_plots;
-- drop schema if exists backup_671;`) so the migration_drift probe retires the object it saw this file declare.

set lock_timeout = '5s';
set statement_timeout = '600s';

begin;

set local lock_timeout = '5s';
set local statement_timeout = '600s';

-- ---------------------------------------------------------------------------
-- 1. The backup. Copied, never granted to a browser role.
-- ---------------------------------------------------------------------------
create schema if not exists backup_671;
revoke all on schema backup_671 from anon, authenticated;

create table if not exists backup_671.listing_plots (
  listing_id    bigint      primary key,
  property_id   bigint,
  source        text        not null,
  category_main text        not null,
  is_active     boolean     not null,
  rail          text        not null check (rail in ('P1', 'P2')),
  estate_area   numeric     not null,
  usable_area   numeric,
  area_m2       numeric,
  area_basis    text,
  backed_up_at  timestamptz not null default now()
);
alter table backup_671.listing_plots enable row level security;
revoke all on backup_671.listing_plots from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. estate_area: a flat's plot (P1) and a komerční plot that is the floor figure (P2).
-- ---------------------------------------------------------------------------
with hit as (
  select l.id, l.property_id, l.source, l.category_main, l.is_active,
         l.estate_area, l.usable_area, l.area_m2, l.area_basis,
         case when l.category_main = 'byt' then 'P1' else 'P2' end as rail
    from public.listings l
   where l.estate_area is not null
     and (l.category_main = 'byt'
          or (l.category_main = 'komercni'
              and (l.estate_area = l.usable_area
                   or (l.estate_area = l.area_m2
                       and l.area_basis in ('usable','floor','total')))))
     for update of l skip locked
), saved as (
  insert into backup_671.listing_plots
    (listing_id, property_id, source, category_main, is_active, rail,
     estate_area, usable_area, area_m2, area_basis)
  select id, property_id, source, category_main, is_active, rail,
         estate_area, usable_area, area_m2, area_basis
    from hit
  on conflict (listing_id) do nothing
), cleared as (
  update public.listings l
     set estate_area = null
    from hit
   where l.id = hit.id
     and l.estate_area is not distinct from hit.estate_area
  returning l.property_id
)
insert into dirty_properties (property_id, marked_at)
select d.property_id, clock_timestamp()
  from (select distinct property_id from cleared where property_id is not null) d
on conflict (property_id) do update set marked_at = excluded.marked_at;

-- ---------------------------------------------------------------------------
-- 3. Post-condition: what is left (a row a concurrent drain held) is reported, never hidden.
-- ---------------------------------------------------------------------------
do $post$
declare
  left_over bigint;
begin
  select count(*) into left_over
    from public.listings l
   where l.estate_area is not null
     and (l.category_main = 'byt'
          or (l.category_main = 'komercni'
              and (l.estate_area = l.usable_area
                   or (l.estate_area = l.area_m2
                       and l.area_basis in ('usable','floor','total')))));
  if left_over > 0 then
    raise notice '671: % row(s) a concurrent write held were skipped -- re-run the file', left_over;
  end if;
end
$post$;

commit;
