# Changing the Supabase compute size

The operator switches the production Postgres between sizes to pay for capacity only while load needs it
(ruling 2026-11-05: "Large or Medium where there is no significant load … back to 2XL where the load is
there … even 2–3 times a month … without coding or breaking things"). The switch is the operator's click;
an agent reads, advises and watches.

## Sizes

| size | CPU | RAM / shared_buffers | Supabase's max DB | ~USD / month | here |
|---|---|---|---|---|---|
| Medium | 2 cores, **shared** | 4 GB / 1 GB | 100 GB | 60 | 10-02 → 10-09: CPU 100 % all week, Browse rebuild 300–600 s, 1,800 s timeouts |
| Large | 2 cores | 8 GB / 2 GB | 200 GB | 110 | 10-10 → 10-16: Browse rebuild 85–118 s; 11-04 (more data): 479–877 s, engine stalled (fixed #2404) |
| XL | 4 cores | 16 GB / 4 GB | 500 GB | 210 | not tried |
| 2XL | 8 cores | 32 GB / 8 GB | 1 TB | 410 | 10-17 → 11-04: Browse rebuild 41–68 s |

The DB is 149 GB (11-04). What decides the size is the hot set, not the total: the tables every lane and
check reads (`autodedup.pairs` 7.3 GB, `images` 6.3, `location_claims` 4.7, `image_clip_tags` 3.3,
`listings` 2.5 heap + index, `properties` 1.9 …) are ~29 GB. Below 2XL they no longer fit in memory, so every
full read goes to disk. Medium is not a target: half of Large's memory, shared cores, and a DB 1.5× its
recommended size.

## When to use which size

- **2XL** while the reading says `LOAD`, and before any planned heavy work: an engine seed, widening, re-cut
  or W8 retire (`autodedup.yml`), a backfill (CLIP, RÚIAN load, reparse, location re-resolve), a migration
  that rewrites or re-indexes a large table, a new portal's first bulk ingest.
- **Large** while the reading says `QUIET`, but only after one supervised Large trial has held (below). Until
  then Large is unproven on the current data.
- **Up again at once** when a reading on the smaller size says `STRAINED` an hour after the switch.

## The reading

`scripts/coordinator/compute_reading.sql`: one row, read only, safe to paste into the SQL editor.

- `LOAD`: engine backlog > 2,000 unread, or the detail queue holds > 20,000 pending, or its oldest row is
  > 6 h old. The thresholds are first guesses: 2,000 unread is ~3 h of engine reading on 2XL.
- `STRAINED`: the Browse rebuild averaged > 300 s over the last hour (2XL ~65 s; the rebuild runs every
  900 s), or a lane that is on has not passed for 15 min.
- `QUIET`: neither.

## Switching

1. Run the reading. Downsize only on `QUIET`. Confirm no `autodedup.yml` run is in progress
   (`gh run list --workflow autodedup.yml --limit 3`): a dispatch holds its lease for up to 5 h.
2. Brake the engine. Note `engine_brake_s`, set `app_settings.realtime_autodedup_interval_seconds` to 0, and
   wait until the reading's `engine_in_flight_s` is null (up to ~18 min). Why: a pass cut by the restart
   rolls back and halves its pass rate. If the outage outlasts the lease release's 180 s of retries, the
   lease waits out its 40 min TTL.
3. Resize: Supabase dashboard → Project Settings → Compute and Disk → choose the size → confirm. ~2 min of
   downtime; the project reads `RESIZING`, then `ACTIVE_HEALTHY`.
4. Check that it took: the reading's `size` and `pg_started` have changed.
5. Lift the brake to the value noted in step 2.
6. Read again at +30 min and +2 h. The first 30–60 min after any restart are slower, even on 2XL, because
   memory starts empty: Browse rebuild 169 s at +25 min, 877 s on Large. Judge on the +2 h reading.

## What a smaller size still costs

Fixed so that nothing loops or stalls: the engine's row census (#2404), location refetch (#2405), and the Health
checks' budgets and scans (#2406). Still open: the text-extraction claim re-hashes every read ad's
text each pass (~9 GB of TOAST); on Large about a third of its passes time out and deliver nothing until the next pass.
The publishing check's tag-cohort read (~1.2 M buffers; `image_clip_embeddings` not autovacuumed since 10-15) may
still run past its 45 s on Large.
Health cells can read "Not measured" during warm-up: that is a budget running out, not a fault.
