-- 671_plot_column_echo_heal.sql -- clear the stored plots that are not plots: a flat's (every one), and a
-- commercial unit's that merely repeats its own floor figure while its own advert text does not say it is the parcel.
--
-- ===================================================================================================
-- DESTRUCTIVE (a stored value becomes NULL). APPLY ONLY AFTER the plot rule (`scraper.area.stated_plot`,
-- the PR this one is stacked on) is MERGED AND DEPLOYED, with the off-database backup taken (step 3 below)
-- AND the operator's explicit OK. Merged TOGETHER with the rule in one PR (operator's call, 2026-10-25) and
-- applied from `main` once the rule is deployed; the migration_drift cell reads this file's backup table
-- ABSENT only between that merge and the apply.
-- ===================================================================================================
--
-- THE RULING. General ruling 3 (2026-09-30) + the operator's answers of 2026-10-01, rebuilt on current main on
-- the operator's go-ahead of 2026-10-25 (the originals, PR #1668 + #1669 with migration 583, never merged and are
-- superseded). On 14 of 14 live pages opened (idnes, realitymix, mmreality; ceskereality answered 403 and was read from its stored
-- pages) the portal ITSELF prints one figure in the plot box and the floor-area box. Not our key maps: the
-- missing rule was "a plot equal to the floor area on a commercial unit is not a plot" and "a flat never carries
-- a plot at all". That rule is `scraper.area.stated_plot`, called BEFORE the content hash at the contract
-- boundary (`ScrapedListing.__post_init__`; sreality's `parse_listing`), so a live row clears at its next detail
-- fetch with one genuine snapshot. This file clears what is already STORED, which no ingest path can:
-- `scripts/reparse.py` never blanks (`_merged`: a re-derive that yields None keeps the stored value), and an
-- inactive or unchanged row is never refetched.
--
-- THE RAILS. The predicate below is exactly the parser rule over the stored cells. Its literals are pinned to the
-- Python constants by tests/test_migration_671_rails.py (the category sets, the labelled bases, the text-evidence
-- pattern `scraper.area.PLOT_TEXT_EVIDENCE_SRC` with its separator class and dotted-thousands test -- carried here
-- VERBATIM, one text Python's `re` and Postgres's regex both read -- and the predicate replayed against `stated_plot`
-- over a grid of cells and descriptions) and the file is EXECUTED over seeded hits and misses by
-- tests/test_migration_671_live.py (CI migrations job):
--   P1  byt with any estate_area (`PLOT_FREE_CATEGORIES`): a flat never carries a plot. On ceskereality the
--       flat "plots" were the doubled floor figure, the placeholder 1, the whole building's parcel or a
--       near-floor figure (fixture b1: plot 64 beside užitná 59) -- none the unit's own land.
--   P2  komercni whose estate_area equals the usable measure, OR equals the headline `area_m2` when
--       `area_basis` is a LABELLED interior measure ('usable' / 'floor' / 'total' = `PLOT_ECHO_BASES`).
--       NOT compared, by the ruling: a title / prose fallback (basis 'unknown' -- "Prodej areálu 3 400 m²" IS
--       the site), a zastavěná plocha figure for equality (a building may cover its whole parcel), and a HOUSE
--       (the operator kept the dum rule as it is; a garage's parcel really is its footprint). Equality is the
--       columns' own: numeric(9,1) against numeric(9,1) / numeric(7,1) -- what the Python rule compares too
--       (0.1 m², half up).
--       EXCEPT when the advert's own `description` says the figure is a parcel (operator, 2026-10-25: equality
--       alone is not evidence): (a) it states a parcel ("pozemek" / "parcela" in any case form, "plocha
--       pozemku" ...) EQUAL to the figure (m² or ha, at column scale) -- kept; else (c) a parcel it states that
--       DIFFERS -- the box echoes the floor, a hit; else (b) a built-up area ("zastavěná plocha" ...) SMALLER
--       than the figure -- kept; else (d) no statement -- a hit. The title is not read: no stored column holds it.
-- NOT HERE: dum, pozemek (area_m2 IS the plot), ostatni. UNDER-HEAL, NAMED (not a false hit): a komerční echo
-- that predates the basis stamp with `usable_area` NULL (the realitymix "Plocha" shape: usable NULL, basis
-- NULL) is missed; the usable arm still catches a pre-stamp row whose `usable_area` carries the figure.
--
-- CENSUS (2026-10-25, read-only, this file's own predicate WITH the text evidence; 2026-09-30 counted ACTIVE
-- rows only):
--   P1 byt       active 3,218   inactive 6,751
--   P2 komercni  active 2,226   inactive 1,961
--   = 14,156 listings on 13,020 properties (5,444 active).
--   The text evidence keeps 248 komerční plots the equality alone cleared (the first census, 14,404 listings on
--   13,244 properties, had P2 active 2,356 / inactive 2,079): active 92 by (a) + 38 by (b), inactive 89 + 29.
--   Still cleared among the 2,356 active equality hits: 149 by (c), 2,077 by (d) -- 104 of those at >= 3,000 m²,
--   where the hand-read found about half to be areál sites whose text names no parcel (the named residual).
--   (b) is the weak arm: of the 38 active rows it keeps, a read found about half parcels and about half a
--   multi-storey building's floor area beside its smaller footprint -- the operator rules on it (scraper track W22).
--   Active rows with area_basis NULL among the hits: 1,549 (ceskereality byt 654, ceskereality komercni 497,
--   idnes komercni 341, realitymix komercni 57) -- rows whose last detail write predates the basis stamp.
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
--                          and area_basis in ('usable','floor','total')))
--                 and not coalesce((
--                   select bool_or(e.parcel and e.m2 = estate_area)
--                          or (not bool_or(e.parcel) and bool_or(not e.parcel and e.m2 < estate_area))
--                     from (select m[1] !~ '^[Zz]' as parcel,
--                                  round(replace(case when n.t ~ '^[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]+)?$'
--                                                     then replace(n.t, '.', '') else n.t end,
--                                                ',', '.')::numeric
--                                        * case when m[3] ~ '^[hH]' then 10000 else 1 end, 1) as m2
--                             from regexp_matches(description,
--                    '(?<![A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž])([Pp]ozem|POZEM|[Pp]arcel|PARCEL|[Zz]asta'
--                    'v[ěe]n|ZASTAV[ĚE]N)[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž]*(?:(?!(?:[0-9]{1,3}(?:['
--                    '\u0020\u00a0\u202f\u2009\u200b\u200c\u200d\u2060][0-9]{3})+(?:[.,][0-9]+)?|[0-9]{1,3'
--                    '}(?:\.[0-9]{3})+(?:,[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)[\u0020\u0009\u000a\u000d\u00a0'
--                    '\u202f\u2009]*(?:[mM](?:²|[\u0020\u0009\u000a\u000d\u00a0\u202f\u2009]?2)|[hH][aA]|['
--                    'hH]ektar[a-záčďéěíňóřšťúůýž]*)(?![0-9A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž])|[Pp]ozem'
--                    '|POZEM|[Pp]arcel|PARCEL|[Zz]astav[ěe]n|ZASTAV[ĚE]N|[.][\u0020\u0009\u000a\u000d'
--                    '\u00a0\u202f\u2009]*[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]|[\u000a\u000d][\u0020\u0009\u000a\u000d'
--                    '\u00a0\u202f\u2009]*[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ])[^•|;!?]){0,40}([0-9]{1,3}(?:[\u0020\u00a0'
--                    '\u202f\u2009\u200b\u200c\u200d\u2060][0-9]{3})+(?:[.,][0-9]+)?|[0-9]{1,3}(?:\.[0-9]{'
--                    '3})+(?:,[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)[\u0020\u0009\u000a\u000d\u00a0\u202f\u2009]*'
--                    '([mM](?:²|[\u0020\u0009\u000a\u000d\u00a0\u202f\u2009]?2)|[hH][aA]|[hH]ektar[a-záčďé'
--                    'ěíňóřšťúůýž]*)(?![0-9A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž])', 'g') m,
--                                  lateral (select regexp_replace(m[2], '[\u0020\u00a0\u202f\u2009\u200b\u200c\u200d\u2060]', '', 'g')
--                                             as t) n) e),
--                   false)))
--   ) r group by rail, source, category_main, is_active, area_basis
--   order by rail, source, category_main, is_active, area_basis;
--
-- STOP CONDITIONS -- do not apply; hand-read the rows first (and report them) when:
--   1. any row's category_main is not 'byt' or 'komercni' (impossible by construction: the predicate moved).
--   2. any P2 row carries area_basis = 'plot': a dwelling stamped as land is a row re-categorised after a
--      land-era derive -- OUR residue, not the advertiser's. 0 on 2026-10-25.
--   3. the ACTIVE komerční rows with area_basis NULL (895 on 2026-10-25) moved by more than 10 % since: they hit
--      by the usable arm alone, and an active unstamped row was never re-derived. The 2026-10-25 hand-read read
--      them with the rest (it sorted every active hit, whatever its basis). (A flat hits whatever its basis.)
--   4. the ACTIVE P2 total is over 3,000 or the ACTIVE P1 total is over 4,000, or all rails over 18,000: the
--      predicate or the data moved since the census above.
--   5. more than 10 P1 rows on sreality, or more than 1 P2 row on sreality -- read the one there is first.
--
-- APPLY ORDER. Before any of it, this PR (rule + heal) merged past its gate (scraper track W22): the stratified
-- hand-read of the active komerční hits (done 2026-10-25: right on ~93 %; the REVERSE case -- an areál, farm,
-- camp or petrol station whose TRUE parcel is typed into both boxes -- ~170 active rows, 93 confirmed by the
-- advert's own text) answered by the operator's ruling of 2026-10-25 (the text evidence above, which keeps those
-- the text confirms), the (b) arm KEPT at merge (2026-10-25: a kept figure loses nothing), and the autodedup programme told. This file clears
-- the same class on the stored rows, inactive ones included (the backup keeps every value).
--   1. The plot rule DEPLOYED (Railway's commit status for the realtime-worker + the next detail drain on main).
--      Before that, a live detail refetch RE-WRITES an echo (`estate_area` is a `structured` cell, so the
--      parse's value lands on every write); a row that did gets caught by a re-run after deploy.
--   2. The COUNT QUERY, compared with the census; the stop conditions.
--   3. The off-database copy (to R2, the database skill's destructive gate):
--        gh workflow run backup_before_drop.yml -f label=plot-echo-heal -f targets="listings:id,estate_area"
--   4. The operator's OK (destructive).
--   5. NO autodedup re-seed and no pause (the programme's answer, 2026-10-25): this file writes NO snapshot, so the
--      lane's `changed` feed never sees a healed row, but the live settings already discount the echo
--      (`d43_plot_column_echo`) and E960 re-reads members' current facts before any merge; a re-claim of the
--      affected in-scope ads through the normal queue is offered on the operator's OK.
--   6. gh workflow run apply_migration.yml --ref main -f file=671_plot_column_echo_heal.sql
--      -f dry_run=false -f confirm=APPLY
--   7. Re-run the COUNT QUERY --
--      expected: no rows (re-run this file if a row a concurrent drain held was skipped).
--   8. Re-bless the field census from a terminal with SUPABASE_DB_URL (`python -m scraper.field_census --bless`)
--      and commit the regenerated `data/field_capture/` files in a small follow-up PR: ceskereality `estate_area` is
--      blessed at fill 0.6145 and this file takes it to ~0.52 (30,275 -> 25,734 of 49,383 active on
--      2026-10-25), a 9.3 pp drop 0.7 pp short of `parsing_fill_drop_warn_pp` -- the parsing cell must measure
--      the new intended fill, not drift into Watch.
--   9. Re-run the COUNT QUERY once more after the next detail drain; a re-run of this file is safe.
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
                       and l.area_basis in ('usable','floor','total')))
              and not coalesce((
                select bool_or(e.parcel and e.m2 = l.estate_area)
                       or (not bool_or(e.parcel) and bool_or(not e.parcel and e.m2 < l.estate_area))
                  from (select m[1] !~ '^[Zz]' as parcel,
                               round(replace(case when n.t ~ '^[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]+)?$'
                                                  then replace(n.t, '.', '') else n.t end,
                                             ',', '.')::numeric
                                     * case when m[3] ~ '^[hH]' then 10000 else 1 end, 1) as m2
                          from regexp_matches(l.description,
                 '(?<![A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž])([Pp]ozem|POZEM|[Pp]arcel|PARCEL|[Zz]asta'
                 'v[ěe]n|ZASTAV[ĚE]N)[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž]*(?:(?!(?:[0-9]{1,3}(?:['
                 '\u0020\u00a0\u202f\u2009\u200b\u200c\u200d\u2060][0-9]{3})+(?:[.,][0-9]+)?|[0-9]{1,3'
                 '}(?:\.[0-9]{3})+(?:,[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)[\u0020\u0009\u000a\u000d\u00a0'
                 '\u202f\u2009]*(?:[mM](?:²|[\u0020\u0009\u000a\u000d\u00a0\u202f\u2009]?2)|[hH][aA]|['
                 'hH]ektar[a-záčďéěíňóřšťúůýž]*)(?![0-9A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž])|[Pp]ozem'
                 '|POZEM|[Pp]arcel|PARCEL|[Zz]astav[ěe]n|ZASTAV[ĚE]N|[.][\u0020\u0009\u000a\u000d'
                 '\u00a0\u202f\u2009]*[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]|[\u000a\u000d][\u0020\u0009\u000a\u000d'
                 '\u00a0\u202f\u2009]*[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ])[^•|;!?]){0,40}([0-9]{1,3}(?:[\u0020\u00a0'
                 '\u202f\u2009\u200b\u200c\u200d\u2060][0-9]{3})+(?:[.,][0-9]+)?|[0-9]{1,3}(?:\.[0-9]{'
                 '3})+(?:,[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)[\u0020\u0009\u000a\u000d\u00a0\u202f\u2009]*'
                 '([mM](?:²|[\u0020\u0009\u000a\u000d\u00a0\u202f\u2009]?2)|[hH][aA]|[hH]ektar[a-záčďé'
                 'ěíňóřšťúůýž]*)(?![0-9A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž])', 'g') m,
                               lateral (select regexp_replace(m[2], '[\u0020\u00a0\u202f\u2009\u200b\u200c\u200d\u2060]', '', 'g')
                                          as t) n) e),
                false)))
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
                       and l.area_basis in ('usable','floor','total')))
              and not coalesce((
                select bool_or(e.parcel and e.m2 = l.estate_area)
                       or (not bool_or(e.parcel) and bool_or(not e.parcel and e.m2 < l.estate_area))
                  from (select m[1] !~ '^[Zz]' as parcel,
                               round(replace(case when n.t ~ '^[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]+)?$'
                                                  then replace(n.t, '.', '') else n.t end,
                                             ',', '.')::numeric
                                     * case when m[3] ~ '^[hH]' then 10000 else 1 end, 1) as m2
                          from regexp_matches(l.description,
                 '(?<![A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž])([Pp]ozem|POZEM|[Pp]arcel|PARCEL|[Zz]asta'
                 'v[ěe]n|ZASTAV[ĚE]N)[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž]*(?:(?!(?:[0-9]{1,3}(?:['
                 '\u0020\u00a0\u202f\u2009\u200b\u200c\u200d\u2060][0-9]{3})+(?:[.,][0-9]+)?|[0-9]{1,3'
                 '}(?:\.[0-9]{3})+(?:,[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)[\u0020\u0009\u000a\u000d\u00a0'
                 '\u202f\u2009]*(?:[mM](?:²|[\u0020\u0009\u000a\u000d\u00a0\u202f\u2009]?2)|[hH][aA]|['
                 'hH]ektar[a-záčďéěíňóřšťúůýž]*)(?![0-9A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž])|[Pp]ozem'
                 '|POZEM|[Pp]arcel|PARCEL|[Zz]astav[ěe]n|ZASTAV[ĚE]N|[.][\u0020\u0009\u000a\u000d'
                 '\u00a0\u202f\u2009]*[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]|[\u000a\u000d][\u0020\u0009\u000a\u000d'
                 '\u00a0\u202f\u2009]*[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ])[^•|;!?]){0,40}([0-9]{1,3}(?:[\u0020\u00a0'
                 '\u202f\u2009\u200b\u200c\u200d\u2060][0-9]{3})+(?:[.,][0-9]+)?|[0-9]{1,3}(?:\.[0-9]{'
                 '3})+(?:,[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)[\u0020\u0009\u000a\u000d\u00a0\u202f\u2009]*'
                 '([mM](?:²|[\u0020\u0009\u000a\u000d\u00a0\u202f\u2009]?2)|[hH][aA]|[hH]ektar[a-záčďé'
                 'ěíňóřšťúůýž]*)(?![0-9A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽa-záčďéěíňóřšťúůýž])', 'g') m,
                               lateral (select regexp_replace(m[2], '[\u0020\u00a0\u202f\u2009\u200b\u200c\u200d\u2060]', '', 'g')
                                          as t) n) e),
                false)));
  if left_over > 0 then
    raise notice '671: % row(s) a concurrent write held were skipped -- re-run the file', left_over;
  end if;
end
$post$;

commit;
