# Health dashboard redesign — the plan

*Written 2026-10-18 for the operator. Status: approved 2026-10-18; the operator's six rulings head § 11, and the sprint runs autonomously, one draft PR per wave. The investigation and design behind it are summarised in § 2; the evidence for every claim (file and line, or the SQL that produced a number) is in the workflow journals named there.*

## 0. In one paragraph

The Health page shows two different judges' opinions, a third one computed in the browser, and several numbers that are wrong by construction. Its alarms never reach anyone: no system alert has ever left the app, and inside the app they cannot even be acknowledged. The plan replaces all of that with one registry of fifteen checks, measured where the data is every five to ten minutes instead of on GitHub's throttled schedule, judged with one vocabulary (OK, Watch, Problem, Not measured), told once per incident on a channel you choose, and shown on one page that puts what is wrong first and names the portal and the reason. It removes more than it adds at every level: 4 judges → 1, 5 vocabularies → 1, 6 monitoring workflows → 1, 8 precomputed summary tables → 0, about 6,300 lines net, and roughly 15 hours of database time a week.

## 1. North star

**A red on the Health page is a real, current, explained problem: measured once, judged once, shown once, told once.**

Every decision in every wave is tested against these corollaries:

1. One registry, one status vocabulary (OK / Watch / Problem / Not measured) and one class (alarm or gauge) for every health fact. Off, idle and building are *states* that read OK, never statuses.
2. Each check runs where its data lives, at its data's own cadence, inside one time budget. A measurement that did not happen is Not measured: never OK, and it never opens, closes or ages an incident.
3. Every threshold is spelled once, beside its measured basis. Limits that depend on behaviour (how often a portal's walks finish, how many ads arrive on a Tuesday) are computed from that behaviour, against a reference the incident itself cannot erode. Every alarm is replayed over 14–28 days of history before it ships and may fire only on real incidents.
4. The page orders by what is wrong, names the portal, job or lane and the reason, and links to the page that owns the fix. The browser holds no threshold.
5. One ladder decides what to tell: a check opens one incident per failing cell, tells it once (grouped per check), reminds until you acknowledge it on Health, and closes once, on a measured OK. A standing, accepted condition is a gauge with a trend: a gauge sends one notice when it first reads Problem and one reminder a day while it stays non-OK; acknowledging silences its reminders like an alarm's; the page's Open-incidents tile counts alarm incidents only.
6. Measuring and telling run on different runtimes, and every watcher is watched by another.
7. Every addition names the deletion that pays for it.

## 2. What the investigation found

**Method.** A 109-agent read of the code, the database and three months of stored history: eleven subsystem readers, a completeness critic, 203 defect claims deduplicated to 32, each tested by three independent reviewers (code, live data, doctrine); all 32 held. Then a design panel: three independent designs, three judges, one synthesis, three adversarial reviews, one repair. Every agent's full return value is kept under `~/.claude/projects/-home-hejtm-dev-sreality/a6940720-4b31-43b0-90de-760b7f7629af/subagents/workflows/` (`wf_b13c77be-f86` is the investigation, `wf_54ca18ad-4de` the design panel).

**Jargon used below.** A *matview* (materialized view) is a summary table the database recomputes on a schedule. *pg_cron* is the database's own scheduler. *The worker* is the always-on Railway service that drains the detail queue, downloads photos and runs dedup. *The bell* is the in-app notifications feed. *RLS* (row-level security) is the set of database rules that decide which rows a signed-in user may read or change. A *cell* is one verdict inside a check: one portal, one job, one lane.

### 2.1 Four judges, five vocabularies, thresholds in six places

- `scraper_health_checks_mv`: 16 checks × 9 portals in SQL, refreshed every 10 minutes, thresholds written as literals in the SQL, words *pass/warn/fail*, values as text. It can never ring the bell.
- `scripts/verify_pipeline.py`: 28 checks run by GitHub crons, words *ok/warn/fail*, thresholds in code plus an `app_settings` row. The only judge that alerts.
- The browser: `derivedArtifacts.ts` decides the freshness of 17 artifacts with its own words (*ok/stale/never/paused/unknown*); `Health.tsx` holds a 25-minute banner, a 36-hour banner, index-percentage colours and a "Queue" column it computes itself (it invents backlogs of 1,829 ads when the real queue holds one row).
- `ops_incidents`: a second incident engine keyed on error text. Its keys match GitHub log boilerplate, so 645 failures sit folded into three incidents that never close, and 37 of 53 alerted incidents were pull-request CI.

### 2.2 Alarms reach nobody, and cannot be acknowledged

- `system_health_channels` is `[]`, the recipients are empty, and the API has no transport key, so the delivery loop never even starts. `channel_sends` has zero rows, ever.
- The RLS rule for updating notifications has no arm for system rows, so an admin cannot mark a system alert read: 861 of 861 are unseen and the badge reads 99+ forever.
- The only path out of the app is GitHub's email on a red run. It covers 7 keys and was red on half of its runs; the OpenAI outage of 2026-10-02 was flagged in 1.5 hours and seen after 8 days. The 2026-08-04 "bell only" ruling predates this evidence.

### 2.3 "Unknown" does not exist, so silence reads as recovery

- The status column allows only ok/warn/fail. A timed-out, not-run or "scored nothing" check is stored as *warn*; the ladder treats any non-fail as green after a 6-hour cooldown that is shorter than the lane's real gap. In 30 days: 34 of 88 "Recovered" alerts were false and 70 onsets followed an unmeasured row.
- Three standing conditions (location coverage counting 13,192 inactive legacy ads, the per-m² unit-price masquerade, and an 80,000-row description re-read shown as a "lag") produced 232 of the 860 alerts and zero incidents. A check that is red on every run trains the reader to ignore every check.

### 2.4 Cadence: GitHub delivers a fifth of what is declared

- The "hourly" acute lane ran 4.9 times a day (median gap 4.9 h, max 9.6 h); the 6-hourly lane 3.6 times a day. A 5-minute worker-liveness limit sampled every 5 hours catches an outage of length D with probability of about D / 4.9 h.
- No check has an expected cadence, so a check whose producer was deleted (`outbound_url_parity`, 2026-09-13) is still served as a current OK. The dead-man switch fires only after 24 hours with no row from any lane; it has never fired.

### 2.5 Measurements that are wrong by construction

- The worker writes no `scrape_runs` row and does about 94 % of detail work, so every counter built on `scrape_runs` is blind: sreality's "error rate 25 %" is 5 errors of 20 GitHub-run items (all one poison ad, HTTP 409, re-armed forever); `delisting_spike` cannot see the worker's flips; the portal cards understate delistings about tenfold; the recent-scrapes chart is near-flat noise.
- "Flipped today" is keyed on `last_seen_at` and is structurally zero under the dwell. Snapshot-density shares sum to 258 %. Sreality's liveness assumes a 60-minute cadence against a measured 261-minute median gap, so it is red a third of the time. remax and maxima report the number of ads they saw as the portal total, so they read 100 % coverage by construction.
- The effective delisting dwell is 24 hours plus a walk gap, not the 12 hours ruled on 2026-10-09: `presence_candidates` skips any ad checked in the last 24 hours. The `delisting_latency` warn line (36 h, set in June) sits at the policy's own floor, so 7 of 9 portals are permanently amber.

### 2.6 Whole domains are unmeasured

- **Dedup:** no check at all. The heartbeat's "dirty" figure is a neighbourhood size, not a backlog; the real backlog definition (`RT_SCOPE_BACKLOG_SQL`) said 64,432 of 155,801 on 10-17. The only dedup status page still says "SHADOW · merges nothing" while the engine has made 18,680 production merges.
- **Publishing:** none of the six latency targets accepted on 2026-07-02 is computed, although every stage leaves a timestamp (discovered → fetched → property → location resolved → Browse rebuilt). Measured live: fetch p95 8.7 min, attach 2 min, resolve 9.5 min; 2 of 2,600 new ads not yet served.
- **Flow stops:** on 2026-10-10 the whole fleet delisted zero ads and nothing alarmed; a portal whose walks stop simply drops out of `walk_coverage`.
- **Platform:** the API has no liveness signal; `db_saturation` counts only failed cron runs and stayed green through a month of saturation in which the health refresh itself timed out daily; worker lanes are judged on one field (`in_flight_s`).

### 2.7 Cost and shape

- The health refresh is the most expensive pg_cron job: about 18 hours of database time a week, one all-or-nothing transaction, four timeouts a week before the 2XL upgrade. Nothing outside the Health page reads any of the six health matviews.
- `Health.tsx` is 2,384 lines with no test, polls 17 requests a minute (2.4 MB of scrape runs each poll), blanks entirely if one RPC fails, links to no other page, sits 18th of 22 in the Admin menu, and is unreachable from the phone shell. The explanation each check produces is never stored; the per-portal offenders are fetched and never rendered.

### 2.8 Incident replay

Of 12 real incidents since July, the alert system caught 2 (the worker freeze and the OpenAI outage); the other 10 produced no related signal, and the median time until a human found one was about six days. Section 8 shows where and how fast each surfaces under the new design.

### 2.9 Defects found on the way (filed as issues, not fixed here)

Poison sreality ad 615387212 re-armed forever by the walk's re-arm; the bazos rate-ledger lease overflowing `smallint` (every GitHub bazos walk falls back to per-process pacing); 422 active ads with a NULL category that no walk can nominate, so they can never be delisted; ceskereality's chaty-chalupy scope double-counting houses; `migration_drift` blind to same-name redefinitions (the migration-438 class); the dark estimation lane; the unused count-probe lane; the no-op `coverage_gate` workflow; the dead `supports_complete_walk` flag.

## 3. The architecture

**Three measurers, one teller, one results table, one incident table, one view.**

- **The registry** (`scripts/verify_pipeline.py`) declares every check once: key, class (alarm or gauge), host, cadence, time budget, label, unit, owner page. Check bodies move into `scripts/checks/` by domain and import the shared predicates they need (the served-location predicate, the dedup backlog SQL, the delisting constants) instead of re-spelling them; no file there names a portal (rule 21). A check returns a status, a value and *cells*: one verdict per portal, job, lane, provider or file, each with its own message. Each host projects its own keys into a small `health_checks` registry table, so pg_cron and the page know every key's class, cadence and staleness budget, and a retired key leaves at once.
- **Measurer 1, the worker:** a `health` lane in the realtime worker (a constant 60-second tick like the text lane, no settings knob) runs the checks that are due: alarms every 10 minutes, gauges hourly or six-hourly, each in its own transaction under its budget. A check that cannot be measured writes *Not measured* with the reason.
- **Measurer 2, the GitHub canary:** the one remaining monitoring workflow, hourly (GitHub delivers about five runs a day). It runs the workflow-failure poller, the two checks that need GitHub (`migration_drift` needs the repository checkout; the weekly URL probe needs an address other than the scrapers'), and then asserts four things: the database answers, the worker's checks are fresh, every failing liveness cell has an incident, and every notice of the last window was delivered or seen. Only those assertions turn the run red, so GitHub's failure email becomes a true signal that needs neither Supabase's scheduler nor Railway.
- **Measurer 3, pg_cron liveness:** a five-minute SQL function judges what must not be judged from inside itself: the worker's heartbeat age, the health lane, every alarm's staleness, the API's background loops (from the watchdog matcher's lease). It writes a result row and nothing else.
- **The teller:** `toolkit/system_alerts.reconcile`, run every 60 seconds by the API beside the delivery outbox (telling already lives there: the outbox delivers and the bell is an API route). One `ops_incidents` row per failing cell, alarm or gauge. A measured Problem opens it, Watch keeps it open, a measured OK held for at least two cadences (and at least 30 minutes) closes it with one recovery notice; Not measured is inert. An alarm reminds at 6 h, 24 h, 72 h and weekly until you acknowledge on Health; a gauge sends one notice when it first reads Problem and one reminder a day while it stays non-OK; acknowledging silences its reminders like an alarm's; the page's Open-incidents tile counts alarm incidents only. Each notice is one `notification_dispatches` row, delivered by the existing outbox to the channel you choose.
- **Who watches whom:** pg_cron watches the worker; the canary watches pg_cron and the API teller; the worker's checks watch the canary's poller.
- **Thresholds:** one home in code, each beside its measured basis. The `app_settings` override row goes (every override already equals its default), and so do the matview's SQL literals, the browser's constants and `portals.scrape_cadence_minutes`.
- **Reads:** one admin-gated view, `pipeline_checks_public`, serves registry, newest result, effective status (Not measured when stale) and incidents. The page polls it every five minutes while visible.

## 4. The check catalogue

Fifteen checks replace 30 Python keys, 16 × 9 SQL verdicts, 17 browser-judged artifacts and two banners. *Alarm* = a check for something that should not happen; its incidents remind at 6 h, 24 h, 72 h and then weekly. *Gauge* = a standing condition shown with a trend; a gauge sends one notice when it first reads Problem and one reminder a day while it stays non-OK; acknowledging silences its reminders like an alarm's; the page's Open-incidents tile counts alarm incidents only. Cadence is how often the check is measured; a key not measured within three cadences reads **Not measured**, never OK.

### `scraping` — portals, alarm, every 10 min

**Measures.** Is every portal still finding and fetching its ads? Four arms; cells per portal, and per category unit or walk scope where noted.
- Walks: per (portal, unit), hours since the START of the newest finished index walk in which every scope of that unit reached the portal's end. G = the portal's median completed walk-start gap over 14 d; the open gap never enters G; fewer than 10 gaps falls back to the slowest portal's G. A portal with no finished walk keeps being scored, so it cannot drop out.
- Inflow: ads discovered in the trailing 24 h against the median of the same 24 h window 7, 14, 21 and 28 days earlier. Discovered = written ads by first_seen_at plus NEW-priority queue rows by enqueued_at, so a stopped drain is not misread as lost discovery.
- Fetch errors: of the distinct detail ids attempted in 24 h by both runtimes (terminal completions plus live queue rows with attempts > 0), the share with a failed attempt. Ids given up 3 or more times in 7 d are named in the cell message, not excluded.
- Missing: per walk scope at each end-reached walk, (declared − collected) / declared. A scope whose adapter declares Portal.total_is_count (remax, maxima: their 'total' is what they saw) reads unknown.

**Data.** scrape_runs (run_type index, started_at, ended_at; by_category[]: the declared total under portal_runner.DECLARED_TOTAL_KEY, collected, walk_reached_end, total_is_count) via scrape_runs_source_started_idx; listings (source, first_seen_at) via listings_first_seen_source_idx; listing_detail_queue (priority, enqueued_at, attempts, given_up, last_error); 24 h of detail_queue_completions. About 1 s.

**Thresholds and their basis.** Walks:
- Watch above 3G. Problem above 6G, or when a unit walked in the last 30 d has had no end-reached walk in 14 d.
- Measured G: crawlers 7.15-7.33 h, sreality 4.42 h, so Problem sits at about 43-44 h and 26.5 h.
- 14-day replay: no unit crossed 6G; the worst gaps were transient Watch (bazos byt/pronajem 32.1 h, idnes komercni/pronajem 26.7 h). This is the operator's 10-13 three/six-walk ruling expressed in time.

Inflow:
- Problem below 20 %, Watch below 50 % of baseline; a spike above 300 % is Watch.
- Baseline must be at least 20 per 24 h, else 7-day windows are compared; below 20 per 7 d the cell is unscored.
- Weekday medians: bazos 1,618, sreality 1,456, idnes 1,564, ceskereality 807; same-window baselines absorb the 30-45 % weekend dip. Hourly backtest: 0 Problem evaluations in 14 d.
- A sustained partial drop erodes the baseline within 2-3 weeks; the coverage gauge, judged against each portal's own declared totals, owns that case.

Fetch errors:
- Wilson LB: Watch above 2 %, Problem above 10 %; at least 200 ids, else the window widens to 7 d, else unknown.
- Worst normal day 0.18 % (bazos). sreality's 25 % false FAIL was one poison id (615387212, HTTP 409); under this denominator it is 1 of ~6,060 ids, 0.02 %.

Missing:
- Watch above 2 %, Problem above 5 % of a scope's declared total (declared ≥ 500).
- Latest walks collected 99.96-100 % of declared; no historical incident. It catches a parser that drops cards on a walk that still reaches the end.

**Replaces.** walk_end_streak; walk_coverage (both arms) and _SELF_CERTIFYING_TOTALS (now the Portal.total_is_count seam); SQL index_completeness (its missing half); SQL liveness (sreality on a hand-set 60-min cadence vs a real 4.3 h gap; FAIL 34 % of the time); SQL data_freshness; SQL runs_completing; SQL new_listings ('> 0 in 24 h'); SQL error_rate (scrape_runs sees ~1 % of drain work); SQL fetch_failures and the sreality-only 'Fetch failures · top 10' card; portals.scrape_cadence_minutes; StaleScrapeBanner (36 h); Recent scrapes chart and table (GitHub-run counters); per-portal declared Pipeline schedule panel

### `parsing` — portals, alarm, every 360 min

**Measures.** Are newly scraped ads still read the way they were? The regression detectors, which fired about once per real defect. Cells per (portal, field), (portal, category, deal) or portal.
- Fill and vocabulary: per (portal, field), fill drop and off-canon rise against the blessed baseline (scraper/field_census.py, data/field_capture baselines).
- Measures of new ads: the share of ads first seen in 7 d that carry a price and a positive area (measure_plausibility_by_source).
- Median shift: week-over-week median area and Kč/m² per (portal, category, deal), against this check's own cell 6-14 d ago.
- Payload shape: the share of ads first seen in 48 h whose location payload the contracts cannot read, using the per-portal predicates location_data's contract module exports, so this file names no portal.
- Snapshot churn: snapshots written in 24 h per active ad, per portal. A volatile hashed field re-snapshots the corpus and fans out watchdog price-drop keys.
Budget 180 s. The ~12 s plausibility read is shared with data_quality through run_due's pass context.

**Data.** listings via scraper/field_census.py with data/field_capture baselines; measure_plausibility_by_source; listings.raw_json of ads first seen in 48 h; listing_snapshots by scraped_at joined to listings (~0.26 s, as the old matview); this check's own history.

**Thresholds and their basis.** Carried with their calibration, moved into THRESHOLDS:
- Fill and off-canon per field_census: 1 real fail onset in 104 d (09-22). Live, idnes has_balcony 26.8 % → 13.7 % reads Watch, awaiting triage or a re-bless.
- Measures of new ads: Watch below 0.95, Problem below 0.90, n ≥ 200 (7 onsets, last on 09-14).
- Median shift: Watch 2×, Problem 3×, n ≥ 200 (5 onsets 09-19..09-25 for one real shift). Cells read unknown for their first 6 days.
- Payload shape: Watch 3 %, Problem 10 %, n ≥ 30, 48 h window (0 fails in 144 runs).
- Snapshot churn: Watch 0.5, Problem 1.5 snapshots per active ad per 24 h (migration 354, sized after the idnes A/B/A hash-thrash storm that re-snapshotted tens of thousands of ads for weeks). Live 0.003-0.045.

**Replaces.** field_fill_matrix; ppm2_measure_coverage; ppm2_median_shift; location_payload_shape_drift, with SREALITY_SHAPE_CASE_SQL and _LOCATION_SHAPE_REMEDY (portal literals in shared code); SQL snapshot_churn and snapshot_churn_24h_mv; SQL field_null_drift (self-eroding 8-day baseline)

### `publishing` — portals, alarm, every 10 min

**Measures.** Do discovered ads reach Browse quickly, with their photos, and does Browse keep holding what is servable? Cells per portal and arm, plus fleet cells.
- Ingest: age of the oldest NEW-priority queue row that is not given up, claimed or not.
- Changes: the same for CHANGED-priority rows.
- Visible: of active ads first WRITTEN 30 min-24 h ago, the share not in browse_list, split by the stage that blocks them: no property, property not active, location not served (resolved with no servable outcome included), or not yet in browse_list. 'Served' is claims_common.SERVED_LOCATION_PREDICATE, imported. Queued ads belong to ingest.
- Stock: of all active properties, the share missing from browse_list, by the same stages. A portal's cell counts properties with an active ad on that portal; a fleet cell counts all.
- Photos: of the photos of ads first written 1-24 h ago, excluding only unavailable_reason 'listing_taken_down', the share not stored (source_unavailable and not_an_image count as not stored, because a CDN that refuses our transform lands there), and the share stored narrower than 600 px. Facts: the active stock's stored share and the top last_error reason.
- Fleet: age of the oldest dirty_properties row.
Shown, not judged: p95 minutes from discovered to servable beside the 2026-07-02 15-min target; p90 minutes to the first stored photo.

**Data.** listing_detail_queue; listings; properties; listing_location; browse_list; images via images_listing_id_idx from listings_first_seen_source_idx (150 ms on 2,000 ads and 30k photos); dirty_properties. The stock arm measured 1.47 s for all nine portals.

**Thresholds and their basis.** Ingest and changes:
- Watch above 3 h, Problem above 6 h.
- 7-day maxima: NEW 119-122 min (bazos politeness bursts on 10-12, 10-13 and 10-16), CHANGED 106 min.

Visible:
- Wilson LB: Watch above 2 %, Problem above 10 %; n ≥ 50, else a 72 h window, else unknown.
- 2 of 2,374 were not visible on 10-17. Bazos bursts (10-13: about 1,110 ads waiting at once, the oldest 119 min) stay green, because queued ads are ingest's.

Stock:
- Watch above 0.1 %. Problem when the Wilson LB exceeds 1 % with n ≥ 200 (pooled below).
- Live: 20 of 362,883 active properties (0.0055 %: ceskereality 18, realitymix 1, mmreality 1).
- The 09-14 regression (Browse ~350k → 45,810 rows) reads ~87 %; the 09-17 one (29,545 bazos ads without a location) reads Problem on bazos.

Photos:
- Not stored: Wilson LB Watch above 2 %, Problem above 10 %, n ≥ 200 photos. Today 30,136 of 30,137 are stored (1 source_unavailable). The iDNES throttle took new ads with given-up photos from 5 % to 52 %.
- Under 600 px: Watch above 10 %, Problem above 50 %. Today 0-2.8 %, medians 1,199-1,600 px; a thumbnail-template regression drives it towards 100 %.

Fleet:
- Watch above 4 h, Problem above 8 h. The 7-day maximum is 3.4 h, mid-sweep; the 08-20 outage held dirty rows for 6 d (09-15's 165 h was the sweep's age, which the property_sweep artifact cell now judges; the dirty queue stayed under 1 h that day).

**Replaces.** acquisition_lag; property_maintenance (its dirty arm here; its sweep arm becomes the property_sweep artifact cell in jobs); SQL detail_queue_lag and detail_queue_backlog (both read '0 min' on an empty queue); SQL e2e_latency; SQL property_attach_lag; sreality_image_template (a live CDN probe outside the shared rate budget) and sreality_image_canary.yml; Image mirror and Image failures panels (stored share and top reason kept as facts); health_summary active counts and portal_health_mv held counts; CategoryTable Queue proxy; detail_latency_recent view

### `delisting` — portals, alarm, every 10 min

**Measures.** Are ads that left their portal made inactive on time, at normal volume, and correctly? Cells per portal and arm.
- Outflow: ads made inactive in the trailing 24 h (inactive_at) against the median of the same window 7, 14, 21 and 28 days earlier (as built: each week's quietest 24 h within ±3 h of this hour for a drop, its busiest for a spike; the As built note).
- Stuck confirmations: active ads whose first unrefuted gone verdict is older than the effective dwell + 2 × the portal's p90 walk gap + 1 h. The effective dwell is max(delist_policy.GONE_DWELL, delist_policy.RECHECK_THROTTLE), both imported; RECHECK_THROTTLE is db.presence_candidates' 24 h literal, named in W4 and unchanged.
- Verify backlog: age of the oldest VERIFY-priority queue row.
- Deferrals: units whose consecutive end-reached walks had nominations deferred by the flip cap.
- Precision: of ads made inactive 48-72 h ago (completions with outcome flipped), the share active again. Portals with fewer than 50 such ads are pooled over 2-7 d.

**Data.** listings_inactive_at_idx; detail_queue_completions (the gone native index, outcome flipped; 7 d); listings (is_active); listing_detail_queue (VERIFY priority); scrape_runs.by_category[].verify_deferred; scraper/delist_policy constants. Measured at 121 ms (outflow with the ±3 h windows; 73 ms fixed) + 0.31 s (precision, the plain range).

**Thresholds and their basis.** Outflow:
- Problem below 20 %, Watch below 50 % of baseline. A spike above 300 % and at least 500 is Watch. Needs a baseline of at least 50 a day; maxima is unscored. As built the floor is judged on the quietest windows, so remax's and mmreality's outflow read Not measured about 68 % and 50 % of hours at ±3 h (45 % each on fixed windows): their quietest days sit near the floor. Judging the floor on the busiest or the offset-0 median instead is an open operator ruling.
- On 10-10 all 9 portals made 0 ads inactive, against medians of 400-1,900.
- Revivals clear inactive_at 48-72 h after a flip, so baseline windows lose up to ~7 % of flips (sreality; ≤ 5 % elsewhere) that the current window still holds: the ratio reads high by that margin.

Stuck confirmations:
- Watch at 25 or more, Problem at 250 or more.
- Live 0; the oldest pending verdict is 27.2 h. First verdict to inactive runs at p90 27.8-35.2 h, under limits of 38.9 h (sreality) and 43.5-47.5 h (crawlers).

Verify backlog:
- Watch above 6 h, Problem above 24 h. The 7-day maximum is 203 min.

Deferrals:
- Watch after 2 consecutive walks; never Problem, because the cap is a throttle by design. The 10-13/15 ceskereality catch-up deferred nominations in two runs (2 walks on 10-13, then 3 from 10-14 17:25 to 10-15 04:56) and cleared itself each time.

Precision:
- Wilson LB: Watch above 10 %, Problem above 15 %, n ≥ 50.
- Post-dwell steady state: sreality ~7 % (LB ~6 %); all others at most 4.9 % (LB ≤ 2.1 %). The blink cohorts read 26-49 %.
- The fixed horizon removes the right-censoring that kept the old check non-ok on 23 of 24 runs.

**Replaces.** false_delist_share (mixed 7-day window); delist_flip_refused; SQL delisting_latency (36 h / 72 h, below the policy's own floor); SQL delisting_spike (read scrape_runs, which never sees the worker's flips); category_trends 'flipped today / 7d' (keyed on last_seen_at, structurally 0); portal_health_mv inactive_7d and health_summary flip fields; the unattainable 6 h delisting SLO

### `coverage` — portals, gauge, every 60 min

**Measures.** Do we hold what each portal shows? A standing condition, so a gauge.
- Residual, per (portal, declared unit) at the latest end-reached walk: declared D (non-overlapping units), active now H, in dwell P (active with a pending unrefuted gone verdict); R = (H − P − D) / D. Entries with total_is_count read unknown.
- Cannot be nominated, per portal: active ads unseen since before the start of their unit's (or, with no unit, the portal's) newest end-reached walk, with no queue row and no completion since, minus that walk's deferrals. This outcome definition counts the NULL-category ads without naming a portal.
Each cell carries a rolling 30-day trend and a worse-than-7-days-ago flag.

**Data.** scrape_runs.by_category (declared total, collected, walk_reached_end, verify_deferred, total_is_count); listings (is_active, last_seen_at index; ~2.4 s count); detail_queue_completions gone index; listing_detail_queue. About 3 s, hourly.

**Thresholds and their basis.** Residual:
- Watch above 2 %, Problem above 5 % of D (D ≥ 500).
- With in-dwell ads subtracted, R is 0-0.15 % on idnes, sreality and bazos (held +1,708 / +1,405 / +1,362 against in-dwell 1,600 / 1,251 / 1,371).
- ceskereality houses read about −4 % from the chaty-chalupy double count (filed). ceskereality byt/prodej's 27-day hole read +37 % (13,373 held vs 9,771 declared).

Cannot be nominated:
- Watch at 25 or more, Problem at 100 or more per portal.
- 422 today (ceskereality 124, realitymix 298, oldest unseen 110 d; the parser issue is filed). Bazos's ~9 in-flight ads stay ok.

**Replaces.** SQL stale_active (all its rows were NULL-category ads); walk_coverage gap arm; category_trends_mv, its RPC, CategoryTable and TrendChart (Index %, doubled 'In DB', invented Queue); browser Index-% colours; coverage_gate (script, workflow, portal_coverage_gate) and the ceskereality/mmreality slice writes only it read; portal_health_mv held counts

### `dedup` — dedup, alarm, every 10 min

**Measures.** Does production honour your rulings, and is the engine deciding and merging?

State, in precedence order: off (realtime_autodedup_interval_seconds ≤ 0), building (rt_bootstrap), merging, idle. These are states that read ok with a state fact, never statuses.

Arms:
- Rulings: ad pairs whose newest operator verdict is 'different', or that sit in must_not_link, now on one property through a source='autodedup' merge newer than the ruling; plus active broker suppressions whose two identities share a broker. Only live AUTODEDUP stores and source 'autodedup' or 'operator' merges are read (rule 15); legacy groups stay the W8 retire tool's business, never a Health arm.
- Build progress: backlog from the lane's own RT_SCOPE_BACKLOG_SQL (imported; 48 ms), split active/inactive; decisions per hour from rt_fp.first_decided_at over 3 h; ETA as a lower bound.
- Merge pause: age of the newest merge inside the apply scope while building.
- Decision lag: in-scope ads first seen 1-24 h ago with no rt_fp decision, using the lane's new-feed predicate (imported).
- Lease: rt_lease held by a holder that is not the live worker.
- Undo: the 7-day cohort undo rate of autodedup merge groups from property_merge_events.

Facts: merges in 24 h and 7 d by autodedup and by operator; newest merge age. Lane exceptions are judged by jobs, not here.

**Data.** app_settings; autodedup.settings, rt_scope_ids, rt_fp (autodedup_rt_fp_decided_idx), rt_lease, verdicts, must_not_link; property_merge_events (source autodedup and operator only); broker_merge_suppressions; worker_heartbeats; the live-stream predicate in autodedup/ui_sql.py. Under 0.5 s.

**Thresholds and their basis.** Rulings:
- Problem at 1 or more. Live: 0 attributable to autodedup.

Build progress:
- Problem at 0 decisions in 3 h with backlog > 0 and the lane not braked. Watch when the ETA exceeds 7 d.
- Normal rate 3,000-7,600 decisions/h. The 10-16/17 scan-cap stall ran ~90/h (ETA ~30 d, Watch); the 10-02 stall decided nothing.

Merge pause:
- Watch above 48 h, never Problem (Q3). 29 h was observed on 10-16/17.

Decision lag:
- Wilson LB: Watch above 1 %, Problem above 10 %, n ≥ 20. Arrival to decision: p50 5.9 / p90 8.0 min.

Lease:
- Watch while within its 40-min TTL (the 10-10 strand lasted 39 min and healed itself); Problem 10 min past expiry.

Undo:
- Watch when the LB exceeds 2 % with at least 100 groups. Measured 7 of 1,626 = 0.43 %.

**Replaces.** broker_merge_suppression; the owed autodedup heartbeat check (CUTOFF C7, PROGRAM W7, roadmap/autodedup.md:65,72); heartbeat latency_p50_s / latency_p95_s (seed age read as latency); heartbeat 'dirty' read as a backlog; AutodedupProgress 'SHADOW · merges nothing' as the de-facto dedup status

### `location_town_coverage` — data_quality, gauge, every 360 min

**Measures.** Rule 25's invariant, over ACTIVE ads first written more than 24 h ago, per portal: no listing_location row, or non-foreign with no town. As today, Czech address-grain rows at the current resolver with no katastr_kod also count. Plus the RÚIAN registry's vintage and staging age. The inactive legacy stock is shown as a fixed number and not judged. Location Quality owns the fix (its All-sources table owns town, building and street per portal) and is the row's link. Each cell carries a rolling 30-day trend.

**Data.** listings (is_active) joined to listing_location; location registry stamps.

**Thresholds and their basis.** - Problem above 0: red until zero (rule 25). As a gauge it sends one notice when it first reads Problem, then one reminder a day until it recovers or is acknowledged.
- Registry: Watch when the vintage is older than 40 d or a staged load is older than 24 h.
- Live: 44 (ceskereality 40, realitymix 3, mmreality 1), against 13,239 under the whole-corpus definition, of which 13,192 are inactive ads whose pages are gone.
- Under the old definition: 0 ok rows in 132 runs and 59 alerts.

**Replaces.** its whole-corpus alarm (W15 ruling reversed per Q2; 18 false 'Recovered' on timed-out runs); CoverageLine geo/street % (owned by Location Quality)

### `data_quality` — data_quality, gauge, every 360 min

**Measures.** Standing value-quality conditions: known debt with an owner, shown with a trend; as a gauge, a Problem sends one notice and then one reminder a day until it recovers or is acknowledged.
- Per (portal, category, deal) over measure_plausibility_by_source: per-m² basis-floor share (stock and first-seen-7d arms), area-vs-usable divergence, estate-sum share.
- Per portal: active ads with no portal URL.
Share arms are judged on each cell's Wilson LB. Each cell carries a rolling 30-day trend and a worse-than-7-days-ago flag.

**Data.** measure_plausibility_by_source (one ~12 s read, shared with parsing through the pass context); listings.source_url.

**Thresholds and their basis.** The existing calibrated numbers, moved into THRESHOLDS:
- Basis floor: Watch 5 %, Problem 10 %, min 100. Sized 2026-08-25 to indict the unit-price masquerade; the worst cell, 13.5 % of 133 (LB 8.7 %), reads amber. 66 alerts in 104 d, never green.
- Area vs usable: 20 / 40 % (26 alerts); estate sum: 5 / 10 %, min 100.
- URL-less ads: 50 / 500 (live 13).

**Replaces.** ppm2_basis_floor_share; area_vs_usable_divergence; outbound_url_coverage; floor_convention (deleted per Q4: 0 fails in 90 runs, the costliest check, an idnes literal); data_quality_by_source and the capture-data-quality job

### `text_extraction` — data_quality, gauge, every 60 min

**Measures.** Are new ads' descriptions read promptly, and when will the archive re-read finish?
- Flow: the share of ACTIVE eligible ads first seen 1-24 h ago with no reading yet, using the lane's own eligibility predicate (imported from toolkit.description_extraction), and p95 minutes from first_seen to reading beside the lane's 20-min SLO.
- Stock: the waiting count and an ETA of waiting / readings in the last 24 h.
Lane exceptions belong to jobs; a provider outage belongs to llm.

**Data.** listings first_seen index joined to listing_description_enrichments; the lane predicate's count for the stock (~10 s).

**Thresholds and their basis.** - Flow: Wilson LB Watch above 5 %, Problem above 25 %.
- Stock: Watch when the ETA exceeds 14 d, or when there were 0 readings in 24 h while rows wait.
- Live: 80,667 waiting at 16,637 a day, an ETA of 4.8 d, so ok. The old check read FAIL (3,410 h) on every run while the lane was healthy; it sent 45 alerts.

**Replaces.** text_extraction_lag (a stock read as a flow); its wedge arm (now a jobs lane cell) and abort arm (now llm)

### `outbound_url_conformance` — data_quality, alarm, every 10080 min

**Measures.** Do the portal links we store still open the right page?
- A weekly sample keyed by ISO week: a quarter of sreality's 48-entry sub-category codebook (12 codes × 3 active ads) plus one active ad per other portal, 44 HEAD requests, sent from the GitHub runner (off the scrapers' IP).
- Cells per portal.
- The sreality codebook rotation and its URL-segment rule stay in scripts/verify_outbound_urls.py: the one named exception to the rule-21 rail, with the Portal-seam refactor filed.

**Data.** listings.source_url; outbound HEAD requests from the canary when the key is due.

**Thresholds and their basis.** - Problem on a concentrated non-conforming cell (≥ cell_min samples), unchanged.
- Watch when the overall share's Wilson LB exceeds 5 %. Today 4 of 44 = 9.1 % has an LB of 3.6 %, so ok; under the raw 5 % line, 3 of 4 runs warned on scattered delisting-lag 404s.
- 0 concentrated failures in 104 d.

**Replaces.** verify_outbound_urls.yml and the script's own main/insert/alert path; outbound_url_parity (frozen since 09-11; leaves the page with the registry)

### `jobs` — platform, alarm, every 10 min

**Measures.** Is every scheduled machine, on every runtime, running and succeeding? One cell per job.
- GitHub workflows, production runs only (event schedule, or push on main; dispatch runs are attended and stay in Settings › GitHub Actions; the canary is excluded): the newest production run's result, consecutive reds since the last success with the stored reason line and run link, and the time since the last success against the workflow's own start-gap distribution (recent_starts). A workflow whose file left main has no cell; a disabled one reads off. A 'GitHub poller' cell judges the poller cursor's age; while it is stale, every GitHub cell reads unknown.
- pg_cron jobs: failures in the last 3 runs, and lateness against the job's own median start gap.
- Worker lanes, from the heartbeat (seeded from the previous heartbeat at boot, so deploys reset nothing): interval ≤ 0 reads off; late; failing (failing_since, last_error); a portal failing inside the probe or drain lane (portal_errors); in flight; absent; flapping (failed_passes since this check's previous result).
- Derived artifacts: public.health_stale(last_succeeded_at, staleness_budget); never succeeded is unknown. Facts: producer, host, last rows, last duration, budget.

**Data.** workflow_failures (+ reason) and workflow_run_health (+ state, on_main, recent_starts), written by the canary's poller step; app_settings.workflow_failures_cursor; cron.job and cron.job_run_details; worker_heartbeats.details (per lane: interval_s, passes, failed_passes, failing_since, last_error, in_flight_s, portal_errors); derived_artifacts (10 rows after W9: 9 kept plus property_sweep).

**Thresholds and their basis.** GitHub workflows:
- Problem when the newest production run failed and the workflow's median start gap is at least 24 h (one red loses a whole cycle: location_registry_load's single 09-01 timeout left ~91.6 % of katastr polygons missing for weeks), at 3 consecutive reds, or when the time since its last success exceeds 3 × its own p90 start gap. Watch on any lone red; main-branch CI goes red after ~10 % of merges.
- Basis: in migration 462's corpus 35 of 63 red streaks were a lone failure; recompute_mf_yields' 3rd red was ~15:00 on 09-13. The four stale streaks (recompute_mf_yields, autodedup_realtime, validate_vision_models gone from main; dinov3_embed_backfill dispatch-only) get no cell.
- Poller cell: Problem when the cursor is older than 18 h (the canary's measured 13.3 h maximum gap plus margin); workflow_poller_liveness's 46 fails 09-18..10-05 were real poller outages.

pg_cron:
- Problem when 2 of the last 3 runs failed, or no run started for 3 × the job's median gap. Watch on any failure in 24 h.

Lanes:
- Problem when late (now − last_pass_at > max(3 × interval, interval + 2 × last duration, 15 min) after boot); failing for max(3 × interval, 15 min), or failing at all when the interval is ≥ 1 h (daily lanes pass about once per boot); a portal failing for max(3 × the lane interval, 15 min); in flight more than 1,800 s (LANE_PASS_TIMEOUT_SECONDS); or absent.
- Watch when the newest pass failed, in flight more than 1,200 s, or 50 % of at least 3 passes failed since the previous result.
- Why: autodedup showed failed_passes 6 on 10-02 while worker_lane_stall read ok.

Artifacts:
- Problem past staleness_budget (each at least 3 cadences, e.g. browse_list 45 min on */15). All live rows are inside budget.

**Replaces.** worker_lane_stall (in_flight_s only); workflow_poller_liveness (now the poller cell); broker_resolution_freshness (33 of 34 onsets were edge flaps; broker_region_type_stats is an artifact cell and broker_maintenance a lane cell); property_maintenance sweep arm (the property_sweep artifact); db_saturation failure-rate arm; the ops_incidents signature engine, ops_incident_runs and failure_signature's tiers; Derived artifacts card and lib/derivedArtifacts.ts; StaleHealthDataBanner (25 min vs the registry's 45); Selhané workflow běhy card and workflow_failure_summary (PR CI excluded; a lone red is Watch)

### `capacity` — platform, alarm, every 30 min

**Measures.** Is the database, and the worker's memory, keeping up?
- Per pg_cron job over 24 h: p50 and p95 duration against its schedule interval and statement_timeout, and against a reference the incident cannot erode: the job's lowest weekly p50 since CAPACITY_REFERENCE_FROM (a blessed date in THRESHOLDS, at most 90 d back, which a CI rail enforces). Before 7 days of post-reference history the relative arm reads unknown; re-blessing after a deliberate change is a dated PR.
- The share of health-check runs that timed out in 24 h.
- The oldest open transaction, excluding autovacuum and its own backend.
- Connections against max_connections.
- The worker's peak RSS against the container limit.
- The DB restart time, as a fact.

**Data.** cron.job_run_details (kept since 06-01) and cron.job; pipeline_check_results (unknown with reason timeout); pg_stat_activity; max_connections; worker_heartbeats (autodedup peak_rss_mb, memory_limit_mb); pg_postmaster_start_time().

**Thresholds and their basis.** Per job:
- Watch when p50 ≥ 3 × reference (and ≥ 30 s), or p95 ≥ 50 % of its interval.
- Problem when the 24 h p50 ≥ 4 × reference (and ≥ 60 s), or p95 ≥ 80 % of its interval or statement_timeout.
- Basis (measured this session): browse-list-rebuild's daily p50 ran 151-277 s from 08-17 against a lowest weekly p50 of ~9.9 s (ratio 15-28) and hit 1,800 s timeouts 09-26..10-03. A trailing 28-day baseline rose from 25.7 s (08-18) to 49.5 s (08-31), 176.6 s (09-15) and 242 s (10-09), so it would have read 'recovered' by early September.
- Reference date at build: 2026-10-17, the first full day on the 2XL instance (p50 41-100 s).

Check timeouts: Watch above 5 %, Problem above 20 % (up to ~12 %/day during the saturation; 0 since 10-10).
Open transaction: Watch above 60 min, Problem above 6 h (saturation-month maximum 18.3 min).
Connections: Watch above 70 %, Problem above 90 % (live 29 of 380).
Worker memory: Watch above 0.8, Problem above 0.9 (live 7.4 of 22.9 GB).

**Replaces.** db_saturation (failure rate only; ok on all 61 runs of the saturation week); long_open_transaction (never non-ok in 196 runs, no fail tier)

### `llm` — platform, alarm, every 10 min

**Measures.** Are LLM providers serving, and is spend sane?
- Per provider: a credit or quota outage, meaning the newest credit-pattern error is newer than that provider's own newest success, over a 72 h lookback so it stays red after callers back off.
- Per (provider, called_for): the 24 h error share while that provider's newest call errors, and starvation (attempts but no success in the last hour).
- Fleet spend over 24 h.
A provider with no calls reads ok with the state idle.

**Data.** llm_calls (24-72 h; ~2 s).

**Thresholds and their basis.** - Credit outage: Problem.
- Error share: Watch at 20 % or more over at least 20 calls.
- Starvation: Problem at 5 or more attempts with 0 successes in an hour.
- Spend: Watch above $130, Problem above $150; the code default moved from 90 to 130 in W1 (the DB override's value).
- Basis: 5 of 5 onsets since 08-27 were real; normal spend is $22-32/day.
- One provider's success no longer masks another's failure (D30); the pin at test_verify_pipeline.py:759 is replaced.

**Replaces.** llm_errors (any-provider rate gate, 24 h provider window); llm_burn_rate including its starved and idle arms; text_extraction_lag's abort arm (three detectors for one outage become one); the duplicate llm_spend definition (code 90 vs app_settings 130)

### `migration_drift` — platform, alarm, every 60 min

**Measures.** Do the objects declared by the newest 25 migration files on main exist in the live catalog? The unchanged name probe, run on every canary run from the GitHub checkout (the image has no migrations/ folder).
- One cell per file whose objects are absent (explicit cells from W6).
- No files means unknown.
- Files whose objects all existed before them (redefinitions) are named as unverifiable.

**Data.** migrations/ in the GitHub checkout; pg_catalog (~1.2 s). The probe SQL moves to scripts/migration_objects.py, shared with apply_migration_receipt.

**Thresholds and their basis.** - Problem when a file's objects are wholly absent; Watch when only some are.
- 3 real onsets in 104 d (473/474, 480, 574), 0 false.
- Staleness budget = cadence + 18 h, covering the canary's 13.3 h maximum gap.
- Same-name redefinitions (the 438 class) stay invisible; an exact apply ledger is filed as an issue.

**Replaces.** its 6-hourly full-lane host; 'no migrations folder reads ok'

### `liveness` — platform, alarm, every 5 min

**Measures.** Are the watchers alive? Judged by pg_cron, outside the worker and the API; it writes a result row and nothing else.
- worker: the heartbeat's age.
- health_lane: judged once health_checks holds realtime-worker rows; no completed pass in 10 min after boot, or failing for 10 min.
- stale:<key>: every registered alarm whose newest measured result is past its staleness_budget (public.health_stale on greatest(last measured, declared_at)). While the heartbeat is stale, worker-hosted keys fold into the one worker cell.
- api: the API's background loops, read from notification_matcher_lease. A NULL lease (clean shutdown) is unknown until it has stayed NULL for 20 min (null_since carried in its own result); off at interval 0.
- unalerted:<key>: an alarm cell measured fail now and at least 2 cadences ago with no open incident (the teller is not telling).
Facts for the page: system_health_channels, last delivered alert, undelivered alerts, oldest unseen alert, the canary's median gap.

**Data.** worker_heartbeats; health_checks; newest and newest-measured pipeline_check_results per key (loose index scan); open ops_incidents; notification_matcher_lease; app_settings; channel_sends.

**Thresholds and their basis.** - Worker: Problem above 10 min. Beats are every 30 s, the watchdog exits at 300 s, deploy gaps are under 2 min.
- Health lane: Problem above 10 min: a full pass (at most 360 s since wave 4, 300 s in wave 3) plus its 60 s tick stays under the 10 min line.
- Stale: past each row's budget, 3 × cadence (30 min for 10-min alarms) or, on GitHub, cadence + 18 h.
- API: Problem when the lease is older than 2 × notifications_matcher_interval_seconds + 60 s (passes run ~300.5 s apart) or NULL for 20 min.
- Unalerted: Problem at 1.
- Structural constants in the function, not tunables. The 09-29/30 freeze took 40 min to reach any check.

**Replaces.** worker_liveness (a 5-min limit sampled every ~4.9 h); emit_verification_stale_alert (24 h on max(run_at) over all keys; never fired); the weekly heartbeat digest as proof of life; migration-551-style hand retirement of frozen keys; a separate api_matcher_liveness check

## 5. The page

`/health` becomes the first Admin entry, in English. One read, one component tree, shared Panel / Stat / StatusDot / Skeleton / ErrorBanner components, one colour scale on the existing tokens (OK sage, Watch ochre, Problem brick, Not measured a hollow grey dot). Sections, labels, units, order and owner links come from the registry; the browser holds no threshold.

1. **Header:** "Health · checks measured 3 min ago", coloured by the liveness check and quoting its message when something is wrong ("the realtime worker has not reported for 11 min").
2. **Top strip:** Open incidents (alarm incidents only: count, oldest) · Problems · Watch · Not measured · Gauges worse than last week · Alerts reach ("Telegram · last sent 2 h ago", or "bell only · GitHub emails an unseen alarm at the next canary run, median 4.8 h"). Below it, one chip per open incident group that jumps to its cell; when clear, "Nothing open · 11 alarms measured".
3. **Portals:** nine rows, worst first. Columns Scraping, Parsing, Publishing, Delisting (alarms) and Coverage (gauge). A cell is a dot plus the worst arm's value ("44 h since a walk reached the end", "48 % photos not stored"); tapping expands that portal's arms. Below 768 px the rows stack and OK cells hide; an empty matrix reads "Nothing open".
4. **Dedup:** state chip (off, building, merging, idle), backlog and ETA (a lower bound), decisions per hour, merges 24 h / 7 d, undo rate, ruling breaches; links to the AUTODEDUP pages.
5. **Data quality:** location coverage (→ Location Quality), value quality, description reading; each a gauge with its target and a 30-day sparkline.
6. **Platform:** jobs with failing cells first (a workflow with its reason line and run link; a pg_cron job; a worker lane; an artifact with producer, rows, duration and budget), capacity, LLM providers (→ LLM Costs), migrations, liveness. One fixed line names what is not measured here (user-facing request errors and latency live in the Railway and Supabase logs).
7. **Alerting:** open incidents grouped by check with an Acknowledge button, incidents closed in the last 14 days, delivery facts, links to Notifications › System and Settings › Delivery.

Every row: dot and word, label, value with unit, "measured X ago" or "not measured since T: reason", the message, the failing cells (top five, then all), the incident line ("since 06:40 · 2 notices · not acknowledged"), the owner link. An OK row is one muted line. A failed first read shows an error with Retry; a failed refetch keeps the data and says so; an empty result is an error, never "all clear". Each system alert in Notifications gains an "Open Health" link; reading it marks it read, and only Acknowledge on Health stops reminders.

Removed from the page (each replaced or owned elsewhere; the list is question 4): both stale banners, the pipeline-checks and derived-artifacts cards, the portal cards with their posture chips and parser facet, the coverage line, the category table and chart, the per-portal schedule, recent scrapes, the image tables, snapshot density, freshness checks, fetch failures, and the workflow-failures card.


## 6. The waves (one draft PR each, in this order)

### Wave 1 · One vocabulary, one registry, one ladder

**Goal.** Every health fact says ok, warn, fail or unknown, and every check is declared once with its class, host and cadence. One ladder, toolkit/system_alerts, run by the API beside the outbox, turns measured fails into one stored incident per cell and tells each once. Incidents are acknowledged on purpose; notices can finally be read.

**PR.** Draft PR on feature/health-w1-contract. It contains:
- registry metadata and its per-host projection;
- the unknown path and the cells contract;
- toolkit/system_alerts.reconcile and the API's system-alerts loop;
- POST /admin/health/acknowledge;
- migration 619;
- docs.
The GitHub lanes keep running every check unchanged and stop alerting; the old page only learns to draw unknown as neutral. ~~Roughly line-neutral: the ladder, loop, route and migration (about +550) against the re-derivation, weekly heartbeat, day-keyed alerts and their tests (about −650).~~ Not as built: the measured delta is in the As-built list below. The second incident model it replaces goes in W3.

**As built (2026-10-18).** Wave 1 (branch `feature/health-w1-contract`, then its review follow-up `fix/health-w1-followups`) follows this plan except where its build contract was more precise:
- Moved forward from wave 2: `--only` and its acute-list test, `sreality_image_canary.yml` and `verify_outbound_urls.yml` (their keys now run by cadence, 86,400 s and 604,800 s), `verify_outbound_urls.py`'s own `main()` (its probe is the registered `outbound_url_conformance` check) and the hourly job's 30-minute timeout.
- The two lanes are identical but for their crons: both run the due checks with `--exit-nonzero-on-fail`, so a failing alarm on either reds its run and GitHub emails (a gauge never reds a run), and both sit in one concurrency group, `pipeline-verification`, so they take turns instead of measuring a check twice.
- A check is due when its newest result is older than its cadence less a slack of min(cadence / 10, 15 min), so a run that comes a few minutes short of a full cadence after the last one does not skip a whole cadence. The due query counts only results written since the key's `declared_at`, the page's and the ladder's rule, so a newly registered or re-declared key is due in the run that projects it (before the follow-up the daily image probe and the weekly URL probe, last measured before wave 1 registered them, read Not measured for a whole cadence). The slack is spelled once, `toolkit/system_alerts.due_after_s`, which the harness imports.
- The workflow-failure poller (`scripts/record_workflow_failures.py`) opens no incident for a `check:<key>|fail` signature on a run that concluded `failure` (the `--exit-nonzero-on-fail` exit): a run reddened by a failing alarm is the ladder's to tell, so the signature engine opens no second incident for it. Every other red keeps flowing: a timed-out, cancelled or interrupted lane logs no exception, so its signature names a warn or unknown CHECK line, and the ladder never tells a kill.
- `Check` has no `sort` field: the sort is the position in `CHECKS`. `health_checks.domain` holds the page section (scraping, publishing, delisting, dedup, coverage, data_quality, platform); neither it nor `host` has a CHECK, and the table is not seeded: each lane's `project_registry` writes its own rows.
- The SYSTEM-row arm is a separate permissive policy, `notification_dispatches_admin_update`: `account_id` = the SYSTEM account `00000000-0000-0000-0000-000000000000` (`account_id` is NOT NULL since migration 364) and the hoisted `is_platform_admin()`; the 292 tenant policy is unchanged.
- The URL probe judges only what reached at least half of its draw: below that it reads Not measured (`unknown`, with `details.skipped` "probed N of M sampled URLs before the 120 s budget ran out"), because the concentration rule judges whole cells and the cells the budget never reached could hold the defect. It probes its draw in an order shuffled by the ISO week (`random.Random(week)`), so a slow sreality cannot starve the crawler portals, and each HEAD's connect and read timeouts are min(20 s, time left); DNS is not bounded, so one request can run past the budget, and the job's 180 s headroom absorbs that.
- Beyond the listed unmeasured paths, location_town_coverage's "no listings read", false_delist_share with no portal at 50 flips and worker_lane_stall with no heartbeat also read unknown; only ppm2_median_shift's "compared NOTHING" stays warn, until wave 6.
- `effective_status` is unknown when the newest row is missing, unknown, or older than the key's staleness budget; `last_measured_at` is the newest row that is not unknown.
- The ladder reads staleness the same way: a newest result past its key's staleness budget is inert for it, Not measured like an unknown row. It also counts only results written since the key's `declared_at`, because the old harness stored a check's own error as fail.
- Recovery needs an OK measured across the hold, two cadences less the due slack, never under 30 min (`max(2 × due_after_s(cadence), 30 min)`): the newest OK's `run_at` must be at least the hold after the first OK's (`ok_since`), so one lone OK never recovers, however long ago it ran. The plan's two full cadences made OKs measured a due interval apart (59 minutes, on an hourly key) wait for a fourth; they now recover at the third.
- A cell closes silently once it has gone unmeasured for max(7 d, 2 × cadence), so the weekly URL probe's incidents wait two weeks. Only results written since the key's `declared_at` count as measured there too, so rows the ladder ignores everywhere else cannot hold a cell open past its silent close.
- The grouped reminder and recovery dedupe keys also carry the incidents they tell, as `{oldest first_seen_at}:{lowest id}x{count}`, so two groups of one check told in different passes never share a key; an onset stays keyed by its result's `run_at`, because one result opens all its cells in one pass.
- The loop runs beside the watchdog matcher; the outbox, which starts only once a transport is configured, delivers its rows then.
- The old page gains one control besides the neutral Not measured: a minimal Acknowledge button on the pipeline-checks card (the rebuilt page comes in wave 8). Its badge reads `N problem`, else `N watch`, else `N not measured`, else `OK`: the page's words, not the build contract's `N fail`.
- Both of the signature engine's auto-resolve statements (`_RESOLVE_ON_SUCCESS_SQL` and `_RESOLVE_MAX_AGE_SQL`) carry the `cell:` fence.
- The replay rail covers 14 days, and the CI import step also imports `scripts.record_workflow_failures`.
- Measured delta of the first build (41ccb1ea) against f8e76ac5 (`git diff --numstat`): code about +26 net, the migration +194, workflows −103, tests about +1,363 and docs about +1,376, of which 1,264 is this plan. It is not line-neutral. Later deletions pay for the test growth: waves 3 and 6 rewrite `tests/scripts/test_verify_pipeline.py` per check as they delete the legacy keys and their tests, and wave 3 also deletes `toolkit/ops_incidents.py` with `tests/toolkit/test_ops_incidents.py`.
- `pipeline_checks_public` serves only rows written since the key's `declared_at`, the ladder's own rule, so an old-harness verdict never shows as current after the cutover; the live view test seeds its keys 30 days back for that reason.

**Operator gate.** Q2 (which checks become gauges), answered upfront.

**Adds.**
- Check(key, fn, domain, cls, host, cadence_s, budget_s, label, unit, owner_path, sort) on the 28 registered keys plus outbound_url_conformance, all host github-actions at today's lane cadences (3,600 acute; 21,600 full; 604,800 weekly).
- Classes per Q2. Gauges: location_town_coverage, ppm2_basis_floor_share, area_vs_usable_divergence, outbound_url_coverage, text_extraction_lag, floor_convention, walk_coverage, delist_flip_refused. The quiet regression detectors (field_fill_matrix, ppm2_measure_coverage, ppm2_median_shift, location_payload_shape_drift) and every other key stay alarms.
- verify_pipeline.project_registry(conn, host) (~30 lines): upserts this host's rows (staleness_budget = 3 × cadence, or cadence + 18 h on github-actions; declared_at reset when host or cadence changes) and deletes this host's undeclared rows. Both GitHub lanes call it; each host only ever touches its own rows.
- Result contract: status ok|warn|fail|unknown; details {message, cells[{subject, portal, status, value, unit, message, facts}], facts, elapsed_ms}. Every unmeasured path returns unknown with its reason: timeouts and not-run (3888-3920, 3959-3968), errors (3969-3977), the in-check 'scored nothing' warns (1316, 1775-1810, 2236, 2704, 3271, 3595). Off, idle and building return ok with a state fact. Legacy checks emit one implicit cell (deleted in W6).
- toolkit/system_alerts.reconcile(conn) (~230 lines), the one ladder:
- one ops_incidents row per failing cell, alarm or gauge (signature 'cell:<key>:<subject>', a prefix the signature engine never produces; the open-signature unique index guarantees one);
- a measured fail opens; warn keeps it open; ok_since starts at the first measured ok, and an ok held max(2 × cadence, 30 min) closes it with one recovery notice;
- unknown is inert; a gauge differs from an alarm only in its reminders (Q2: a gauge sends one notice when it first reads Problem and one reminder a day while it stays non-OK; acknowledging silences its reminders like an alarm's; the page's Open-incidents tile counts alarm incidents only); retired keys and cells with no measured result for 7 d close silently;
- reminders while not recovered and not acknowledged: an alarm at 6 h / 24 h / 72 h / weekly, a gauge every 24 h;
- at most one onset, one reminder and one recovery notice per check per pass (dedupe sys:<key>:<kind>:<run_at>, the existing sys: namespace);
- runs under pg_try_advisory_xact_lock.
- api/main.py: the system-alerts loop (~25 lines), every 60 s as a constant, started beside the matcher under its existing producer guard (tests set it), so no new flag. Telling lives in the process that already runs the outbox and the bell.
- POST /admin/health/acknowledge {key} (require_admin, service role, ~25 lines): sets ops_incidents.acknowledged_at on that key's open incidents; joins the admin route census.
- Ladder values spelled once, in toolkit/system_alerts. The llm spend code default moves from 90 to 130 (the DB override's value).
- A one-predicate fence on the retiring engine: toolkit/ops_incidents._RESOLVE_MAX_AGE_SQL skips signature LIKE 'cell:%', so it can never send a 'Recovered' for a cell incident. Deleted with the module in W3.
- SPA: lib/pipelineChecks.normalizePipelineStatus maps unknown to a neutral not-measured state. CHECK_LABELS stay until W8.
- CI: a clean-environment step 'pip install -e . && python -c "import scripts.verify_pipeline"', the canary's own install, so an import regression cannot turn every canary run red unseen.
- migration 619

**Deletes.**
- toolkit/system_alerts.py re-derivation: CheckState, _collapse, _TRUE_ONSET_SQL, _resolve_truncated_onset, check_states, latest_statuses, emit_transition_alerts and its 30-day history read on every run (~250 lines)
- Legacy day-keyed alerts (_today_utc, the default dedupe key, the untimestamped branches) and emit_weekly_heartbeat
- In verify_pipeline: the alert call in main(), write_results (no caller), _WEEKLY_CHECKS, --weekly, full_lane_budget_s(weekly=), the Monday 'date +%u' branch in verify_pipeline.yml, DEFAULT_THRESHOLDS verification_stale_hours (no Python reader), and AlertPolicy's two duplicate constant sets
- verify_outbound_urls.py's own alert call
- SPA: normalizePipelineStatus's fold-to-ok and its test pin
- ~350 re-derivation test lines in tests/toolkit/test_system_alerts.py

**Migrations.**
- 619_health_contract.sql — ADDITIVE. Opens with SET lock_timeout='5s'; applied via apply_migration.yml before merge.
- Widens the pipeline_check_results status CHECK to ok|warn|fail|unknown (a superset; all 11,514 rows validate).
- CREATE TABLE health_checks (key pk, domain CHECK portals|dedup|data_quality|platform, class CHECK alarm|gauge, host CHECK realtime-worker|github-actions|pg_cron (derived_artifacts' own spellings), cadence_s, staleness_budget interval, label, unit, owner_path, sort, declared_at default now()). RLS on, no policy, revoked from anon and authenticated. Seeded once with W1's github-actions rows; the canary's projection owns them afterwards, and no CI test pins the seed.
- CREATE FUNCTION health_stale(stamp timestamptz, budget interval) RETURNS boolean, STABLE SQL: stamp IS NULL OR now() − stamp > budget. The one staleness predicate.
- ALTER TABLE ops_incidents ADD COLUMN ok_since timestamptz, ADD COLUMN acknowledged_at timestamptz.
- notification_dispatches: REVOKE UPDATE FROM authenticated; GRANT UPDATE (seen_at, estimation_run_id) TO authenticated (the only columns the tenant routes write; merges run as the service role). ~~ALTER POLICY notification_dispatches_tenant_update adds OR (account_id = SYSTEM AND (SELECT is_platform_admin())) to USING and WITH CHECK, mirroring the read arm.~~ Superseded by the build: the SYSTEM arm is a separate permissive policy, notification_dispatches_admin_update, and the 292 tenant policy is unchanged (As built, above).
- CREATE OR REPLACE VIEW pipeline_checks_public: one row per registered key, today's six columns first (the old page keeps working), then label, unit, domain, class, host, cadence_s, owner_path, sort, last_measured_at, effective_status (unknown when health_stale(greatest(last_measured_at, declared_at), staleness_budget)) and incidents (open cells with since, last seen, notices sent, acknowledged; cells closed in the last 14 d). Admin gate unchanged. Keys outside the registry, such as outbound_url_parity, leave the page with no data delete.

**Tests.**
- reconcile:
- onset once per cell, grouped into one notice per check per pass;
- unknown neither opens, closes nor ages an incident;
- warn keeps it open and never recovers it;
- recovery only after a measured ok held for the hold time;
- reminders only while failing and unacknowledged; Acknowledge stops them, a seen notice does not;
- gauges open incidents like alarms and remind every 24 h;
- retired keys and cells unmeasured 7 d close silently;
- dedupe is idempotent under two concurrent callers (advisory lock).
- Replay rail: the last 30 d of pipeline_check_results (an aggregate fixture of key, run_at, status and cell statuses; no ids, because the repo is public) run through reconcile. Asserts 0 recoveries on unmeasured runs, and that every onset that marked a real event still opens.
- Each listed unmeasured path returns unknown with its reason; off/idle/building return ok with a state fact.
- Projection: idempotent; a host deletes only its own undeclared rows; declared_at resets on a cadence change.
- Migrations lane: registry-driven view rows; a never-measured key reads 'not measured yet'; effective_status ages through health_stale. tests/test_pipeline_checks_latest_per_key.py is rewritten against registered keys (it seeded unregistered ones).
- tests/test_tenant_isolation_live.py: a platform admin can set seen_at on a SYSTEM-account row but cannot change message, target_channels or dedupe_key; a tenant cannot touch SYSTEM rows; tenant rows unchanged. health_checks joins the lists in test_migration_rls_grants.py and test_tenant_isolation_live.py.
- API: the acknowledge route is admin-only (route census); the system-alerts loop starts beside the matcher and stays off under the test guard.
- frontend/src/lib/pipelineChecks.test.ts: unknown never renders as ok.

**Docs in the same PR.**
- GLOSSARY.md: a Health section with Check, Cell, Alarm, Gauge, Incident, System alert, Acknowledge and Not measured ('alarm' names a check class, 'system alert' the notice it sends; 'watchdog' stays the saved-filter word)
- docs/architecture.md rule 16: vocabulary, registry, per-cell incidents, the API teller, read vs acknowledged, admin-gated reads (replaces 'hourly … anon views')
- scraper-ops SKILL.md: the Pipeline verification section (:401-448) cut from ~49 to ~14 lines pointing to the reference, because it states the warn-for-unmeasured and cooldown doctrine this wave replaces
- scraper-ops references/pipeline-verification.md: escalation-ladder section rewritten
- toolkit-api SKILL.md: the system-alerts loop and the acknowledge route (edited in place)
- roadmap/reliability-track.md: a dated section absorbing realtime-scrapers.md's W5b and roadmap/next.md:61-62; the ROADMAP.md reliability status cell
- the .github/scripts/docs-staleness-check.sh map gains scripts/verify_pipeline.py, scripts/checks/ and toolkit/system_alerts.py, mapped to scraper-ops

**Risks.**
- A ladder rewrite can mute or double alerts; the 30-day replay rail and idempotent dedupe keys guard it.
- Cutover sends one grouped onset per currently failing check (alarm or gauge) — today the three failing gauges, location_town_coverage, ppm2_basis_floor_share and text_extraction_lag — once, which is expected; the operator may clear the 861 old alerts first with Mark all read.
- The widened CHECK is live before merge, so no writer can emit unknown into the narrower constraint.
- The API redeploys ~11 times a day; incident state lives in the DB, and the first pass after boot catches up.
- Until W2, a dead API is told by no one, as today (the bell is an API route); llm_health's GitHub email still runs.
- The signature engine runs beside cell incidents until W3, fenced; its sys:ops_incident:* alerts stay in the bell, because no channel is set until W7.

### Wave 2 · Watchers: pg_cron liveness and the GitHub canary

**Goal.** A dead worker, a stopped check or a broken teller is noticed within 15 minutes by a runtime that does not depend on it, and GitHub's email becomes the one path that needs neither Supabase's scheduler nor Railway.

**PR.** Draft PR on feature/health-watchers. It contains:
- health_liveness() (migration 620);
- verify_pipeline.run_due with per-check deadlines;
- verify_pipeline.yml rewritten as the hourly canary, absorbing llm_health.yml, the 6-hourly full lane, sreality_image_canary.yml and verify_outbound_urls.yml;
- worker_liveness deleted.
Legacy checks keep their logic and cadence and run where they run today, on GitHub, until the wave that replaces each; nothing moves host. ~~Net about −250 lines (three workflows and the lane-budget machinery against run_due, the assertions and the liveness function).~~ Not as built: measured +1,526 (the As built note, below).

**As built (2026-10-18).** Wave 2 (branch `feature/health-w2-watchers`) follows this plan except where its build contract was more precise:
- Moved forward from wave 3: the workflow-failure poller is the canary's first step, and `monitor_workflow_failures.yml` is deleted; its logic (the production filter, reason lines and workflow facts) still changes in wave 3. It now polls at the canary's rate, about five runs a day, instead of on its own `*/30` cron (which GitHub ran 80–256 min apart); at 330–484 workflow runs a day (2026-10-15..17), even a 13 h gap stays far inside its 1,000-run page budget. `sreality_image_canary.yml` and `verify_outbound_urls.yml` had already gone in wave 1.
- `--exit-nonzero-on-fail` is deleted with the two lanes, because the canary's fourth assertion replaces it: a run goes red when an alarm's notice has gone unhandled for 15 minutes, not whenever it measured a failing alarm. GitHub therefore emails about an alarm nobody has read, whichever host measured it, and reading the notice stops the email.
- The assertions are their own script, `scripts/health_canary.py`, the workflow's last step (`if: always()`); the step before it runs `verify_pipeline --host github-actions`. It exits 2 when the database does not answer and 1 when an assertion fails, printing each failure as one GitHub error annotation, `::error::CANARY FAIL <name>: <reason>`. The poller step keeps its old job's 5-minute limit, so a wedged poll cannot eat the checks' time. The second assertion reads the watcher's newest row (under 30 min old, six missed ticks) rather than the worker's checks, and is skipped while `health_checks` has no `liveness` key, so the canary can merge before migration 620 is applied. The CI import step also imports `scripts.health_canary`, and the docs-staleness map sends `scripts/health_canary.py` to the scraper-ops skill.
- The fourth assertion reads a fixed window instead of "since the previous canary run": alarm notices of the last 12 h that are at least 15 min old, matched on the `sys:<key>:onset:` and `sys:<key>:reminder:` prefixes. A notice is handled when it was seen, when it was delivered (a `channel_sends` row `sent` or `delivered`), or when its key has no open unacknowledged incident left. Gauge notices are excluded: the operator ruled that gauges ring daily in the bell, and the email path is for alarms.
- The `api` cell's NULL lease: the API's clean shutdown releases the matcher's lease to NULL on every deploy, and the next process claims it on its first pass. A NULL lease reads Not measured ("lease released; a clean API shutdown"), without the planned 20-minute `null_since` clock carried in the watcher's own result. A lapsed lease is a Problem once `expires_at` is more than 2 × `notifications_matcher_interval_seconds` + 60 s in the past (the lease lasts 15 min, so a crashed API reads Problem about 26 min after its last renewal, outside this wave's 15-minute goal), and interval 0 reads OK with the state fact `off`. An API that stops cleanly and never comes back is therefore no Problem by itself; it shows once a liveness cell fails without an incident, which the canary's third assertion emails.
- `unalerted` is one cell whose facts name the keys and cells, not one cell per key: a cell failing in an alarm's newest in-budget result and in its newest measured result at least two cadences older, with no open incident. Like the ladder, it reads only results written since the key's `declared_at` and never a stale or unmeasured one.
- Beyond the plan: every open liveness incident whose subject no longer fails gets an OK cell, so a `stale:<key>` measured again recovers on a measured OK, with one recovery notice, instead of closing silently after 7 days; a dead worker's cell also folds the `health_lane` cell; and the apply's post-condition calls the function once and keeps that row, so the canary and Health see liveness at once.
- The watcher's registry row reads label "Watchers", unit `min`, sort 999 and staleness budget 15 min (3 × 300 s). The cron command sets a 60 s `statement_timeout` (the migration-371 idiom), not 30 s. The result carries its cells, message and `elapsed_ms`; the delivery facts listed in § 4 are not built in this wave. `emit_verification_stale_alert` is only unscheduled; migration 624 drops it.
- The harness: `run_due` runs alarms before gauges, in registry order inside each class (not most-overdue first), under the summed budgets of the due checks. `_LaneBudget` stays (as `LaneBudget`), now holding a per-check deadline and living in `scripts/checks/clock.py` with `_fetchone` / `_fetchall` (the clock every check's statement runs under, importable by a check module without importing the harness; the harness keeps its old spellings as aliases until wave 3 moves the checks beside it) (and `_JOB_HEADROOM_S` stays for the rail that fits every due check plus the headroom into the harness step's 27-minute limit, inside the canary's 40-minute job: 28 budgets sum to 1,410 s): every statement's timeout is the check's time left (at least 1 s), and an overrun is `unknown` with `budget_seconds` and `elapsed_seconds`. The raw-cursor checks read through `_fetchone` / `_fetchall`, their SQL unchanged. A check the run never reached writes nothing and stays due (`_not_run_result` is deleted).
- `migration_drift` keeps its 6-hourly cadence until wave 3 moves it into `scripts/checks/platform.py`; with no migration files to read it is `unknown` with `details.skipped`.
- Every legacy key stays on GitHub at its cadence, so with the 6-hourly lane gone it is sampled at the canary's delivered rate alone (about five runs a day, against about 8.5 for the two lanes together) until waves 3–6 move it.
- Integration (2026-10-18, on top of the wave-1 review follow-ups, #1864): the job is 40 minutes, not 30, and the harness step has its own 27-minute limit: the rail `test_the_canary_job_fits_every_github_check_due_at_once` reads the YAML and fits the 28 budgets (1,410 s) plus 180 s headroom under the step, and the poller's 5 minutes, the step and 8 minutes for setup and the assertions under the job, so an overrun fails the step (a red run, an email) instead of the job's silent kill; the harness step runs after a failed poll but not after a cancel (`!cancelled()`, where the contract said `always()`). `MONITOR_WORKFLOW_NAME` is the canary's name (the poller skips its own runs by it; a test pins the two equal), so the canary's own red runs never reach `workflow_failures` — GitHub's email is their path. The replay rail drops a retired key's rows as the ladder does (`worker_liveness` stays in the 14-day fixture, read-only).
- Measured against wave 1 (the final commit, `git diff --numstat 462a98ba`): code +488/−411 (net +77: `llm_health.yml` and `monitor_workflow_failures.yml` -101 and the harness -108, against `scripts/health_canary.py` +150, `scripts/checks/clock.py` +95 moved out of the harness and the canary workflow +32), migration 620 +311, tests +933, docs +205: +1526 in all. The plan's "about −250" counted the lane-budget machinery as deleted, but `LaneBudget` stays as the per-check clock, and the two probe workflows' −100 was banked in wave 1.
- Owed to wave 3, named here so it is not lost: `record_workflow_failures._is_ladder_owned` and its two constants are dead after this wave — the canary is the only workflow that prints CHECK lines and the poller skips it by name — and go with the poller rewrite and its fence tests. Two rulings for the operator: the `api` cell reads Problem about 26 min after a crashed API's last renewal (the literal 2 × interval + 60 s on `expires_at`), outside the 15-minute goal; and an API that stops cleanly and never returns leaves a NULL lease that reads Not measured, so it is seen only once some other cell fails without an incident (the third assertion) — a dead API with nothing else failing is not told.

- Review of this wave (three lenses, 2026-10-18): `stale:<key>` reads the newest MEASURED result since `declared_at` (ok/warn/fail, 619's rule), as § 4 and the GLOSSARY say — the build had read the newest row of any status, so an alarm whose every run was Not measured would have been told by no one; `verify_pipeline --host` accepts only hosts that declare a Check and `project_registry` refuses the others, because projecting `pg_cron` would have retired the watcher's own registry row and silently closed its incidents; `draw_samples` and the text lane's two toolkit reads run on the check clock too, so "every statement" holds. The 15-minute goal as built, per cell: a dead worker within 15 min (5-min cron, 10-min rule); a stopped alarm within 5 min of passing its staleness budget — cadence + 18 h for this wave's GitHub-hosted keys, 3 × cadence once wave 3 hosts alarms on the worker (30 min for a 10-min alarm); an untold Problem two cadences after its first measured fail; the `api` cell about 26 min after a crashed API's last renewal; and GitHub's email trails by the canary's delivery gap (hours). The goal is met for the worker now and for worker-hosted alarms from wave 3; a GitHub-hosted key cannot meet it by construction, which is why waves 3–6 move them. A ruling for the operator: an alarm's unread onset keeps the canary red through the incident's recovery hold even when the cell already measured OK (up to 11.5 h for a 6-hourly key); the contract's "handled" could also count `ok_since is not null`. Owed with the wave-3 poller rewrite: `MAX_LOG_FETCHES` (25 logs per poll) meets about 20 reds per poll at five polls a day.

**Depends on.** W1

**Adds.**
- verify_pipeline.run_due(conn, host) (~60 lines): due = newest run_at older than cadence_s, read from the DB, so restarts rerun nothing. Each check runs in its own transaction with SET LOCAL statement_timeout set to its remaining budget and re-armed to the time left by _fetchone/_fetchall, so the 8 raw-cursor checks are bounded without being rewritten. A check skipped because the pass budget ran out writes nothing and stays due; after alarms, due checks run most-overdue first. _PLAUSIBILITY_CACHE is cleared at the top of every call.
- The canary, python -m scripts.verify_pipeline --host github-actions: projects github-actions rows; runs due github checks (legacy keys at today's cadences, migration_drift on every run at cadence 3,600, outbound_url_conformance by ISO week); then four assertions, each printed as an ::error:: annotation:
- the DB answers (otherwise exit 2);
- the newest liveness result is under 30 min old;
- no liveness cell has been failing for 15 min without an open incident;
- every system alert (onset or reminder) dispatched between the previous canary run minus 15 min and now minus 15 min has a sent channel_sends row or a seen_at. The previous run is the newest earlier migration_drift run_at, so no GitHub API call or fixed fallback is needed; the 15 min grace covers the outbox's 120 s interval and first retry.
Only these fail the run.
- .github/workflows/verify_pipeline.yml: hourly cron; job timeout 40 min with a 27-min harness step while it carries legacy keys (both resized after W6; a job under 13 min also needs the poller step's 5-minute limit or the rail's 8-minute allowance cut).
- migration 620

**Deletes.**
- .github/workflows/llm_health.yml, sreality_image_canary.yml and verify_outbound_urls.yml
- check_worker_liveness and worker_stale_fail_minutes
- The acute --only list and test_acute_lane_only_list_resolves_to_registered_checks; --exit-nonzero-on-fail; _LANE_BUDGET_S, the _LANE/_LaneBudget module globals, full_lane_budget_s and _JOB_HEADROOM_S; the ship-soak-promote registry comments
- verify_outbound_urls.py's main/insert path (its probe becomes the outbound_url_conformance body) and tests/scripts/test_verify_outbound_urls.py (rewritten as a check test)
- The emit-verification-stale-alert schedule (the function is dropped in 624)
- migration_drift reading 'no migrations folder' as ok (0 files now reads unknown)

**Migrations.**
- 620_health_liveness.sql — ADDITIVE, safe before merge.
- CREATE FUNCTION public.health_liveness(): SECURITY DEFINER, search_path pinned, execute revoked from public, anon and authenticated. It writes one liveness result (cells worker, health_lane, stale:<key>, api, unalerted:<key>; delivery facts) and nothing else: incidents and notices are the teller's.
- The health_lane cell is judged only once health_checks holds realtime-worker rows (none until W3), so applying this before any worker lane exists raises nothing; it sets no delivery channel.
- INSERT its own health_checks row (liveness, platform, alarm, pg_cron, 300 s, budget 15 min).
- cron.unschedule('emit-verification-stale-alert'); cron.schedule('health-liveness', '*/5 * * * *', 'set statement_timeout=''30s''; select public.health_liveness();').

**Tests.**
- run_due: on a fake clock only due checks run; due-ness survives a restart; a budget skip writes nothing and stays due; most-overdue first.
- Deadline: a three-statement check is cancelled at budget_s in total and stored unknown (timeout); a raw-cursor statement is bounded.
- Canary exit codes: DB down 2; stale liveness 1; a liveness cell failing 15 min without an incident 1; an unseen, undelivered onset older than the grace 1; one inside the grace is ignored; recovery and sys:ops_incident:* notices are ignored; otherwise 0.
- Migrations lane, health_liveness(): a stale heartbeat writes one worker cell with worker keys folded in; a key past its budget gets a cell; a key within 3 cadences of declared_at reads 'not measured yet'; a NULL lease reads unknown, then fails after 20 min via null_since; matcher interval 0 reads off; no realtime-worker rows means no health_lane cell; a fail with no incident yields unalerted; it writes no ops_incidents or notification_dispatches row.

**Docs in the same PR.**
- scraper-ops SKILL.md :204-209 (the llm_health acute lane) rewritten in place as the canary
- pipeline-verification.md: hosting, liveness and canary sections (who watches whom); the verify_pipeline.yml header
- docs/architecture.md rule 16: hosts, cadences, liveness, canary

**Risks.**
- The canary carries the legacy set until W6, on a 30-min job like today's full lane.
- GitHub delivers the canary about 5 times a day, so its email lags (median ~4.8 h); the page states this.
- GitHub emails the last modifier of the cron line. The rewrite is committed under the operator's identity, and the first real red is checked to arrive.
- No worker lane exists yet, so health_lane is not judged until W3.
- Legacy property_maintenance (its 3 h dirty line flaps during the 04:15 sweep) and broker_resolution_freshness keep today's sampling, damped by W1's hold, until W3/W4 delete them.

### Wave 3 · Platform checks and the worker health lane

**Goal.** Every scheduled machine on every runtime, the database and the LLM providers are judged once, per job or provider, every 10 minutes from the worker, with the failing run's reason. The signature engine retires, leaving one incident model.

**PR.** Draft PR on feature/health-platform. It contains:
- the health lane;
- scripts/checks/platform.py (jobs, capacity, llm; migration_drift moved in);
- heartbeat lane fields seeded at boot;
- the poller folded into the canary, with its production filter, reason lines and workflow facts;
- the outbox subject and deep link;
- migration 621;
- the retirement of toolkit/ops_incidents.py and failure_signature's tiers.
~~Net about −900 lines (the signature engine, its tests and seven legacy checks against three checks and the lane).~~ Not as built: measured +2,749 (the As built note, below).

**As built (2026-10-19).** Wave 3 (branch `feature/health-platform`) follows this plan except where its build contract was more precise, and the contract except where the review of draft #1871 corrected it (the last group below):
- Lanes are registered from one module constant, `LANES`: the 14 lanes in their order, with `health` between `text_extract` and `autodedup`, 15 in all. `_amain` seeds each with `interval_s` None before any task starts and asserts that the names it registers equal `LANES`, so nothing imports the worker to learn the list, and `_lane_loop` writes `interval_s` on every wake, the idle branch included. § 4's "absent" became "never woke": a lane whose `interval_s` is still None after 15 min of uptime is a Problem. The heartbeat lane records its own pass (`{"beat": true}`) like any lane, which is the pseudo-lane special case this wave deletes; the jobs check skips it (the watcher judges the beat) and judges `health` like any lane.
- Heartbeat fields per lane: `interval_s`, `passes`, `failed_passes`, `last_pass_at`, `last_duration_s`, `started_at`, `in_flight_s`, `last`, `failing_since`, `last_error` (at most 300 characters: `<Type>: <message>` for a raise, `pass exceeded 1800 s` for the timeout) and, on the probe and drain lanes, `portal_errors`. A pass fails exactly where it failed before (a raise, the 1,800 s timeout, `text_extract`'s `fatal`, autodedup's refusal); nothing new counts as a failure. `_record_pass` carries `interval_s`, `portal_errors` and every key it does not set itself.
- Boot seeding carries every key but `started_at`, `in_flight_s`, `interval_s` and `last_failure_at` (the counters, the last pass, `last`, the failing spell, `portal_errors`, autodedup's `recut`), not only the plan's failing fields, in one bounded read before the first beat (`connect_timeout` 10, no retry; a miss seeds nothing and logs once), and never in `_new_state()`, which 60 test sites call.
- A drain or images pass with a slice ≤ 0 records `{"skipped": "slice<=0"}`, which the jobs check reads as off. A probe portal whose every category failed (which raises nothing) counts as raised, `every category failed`, and a portal the lane's disabled-sources setting now holds loses its `portal_errors` entry. The crash path in `_record_run_crash` keeps its log line and loses its incident write.
- Lane cells: one per lane, carrying its worst condition with every reason in `facts.conditions`. Late is judged only once the worker has been up past the lane's bound, a lane with no pass yet counting from the worker's start; a lane with no pass in the worker's first 15 min has no cell.
- GitHub cells: migration 621 also adds `workflow_failures.event` and `head_branch`, and a row from before this wave (`event` NULL) is never a job's record. A cell exists for `on_main = true` only (NULL: none yet). A `state` other than `active` reads OK with the fact `state` (the plan's "reads off"). The p90 arm needs 10 `recent_starts`; until then it is Not measured inside the cell's facts, never a verdict.
- pg_cron failure arms (the last 3 runs, failures in 24 h, the quoted failure) read finished runs only (`end_time IS NOT NULL`), the lateness arm counts every start; an inactive job reads OK with `active=false`; the overdue arm needs 5 starts in 7 days. Unreadable or empty `cron` tables make the cron arm Not measured with the reason, never OK, because pg_cron's row security returns zero rows to the wrong role. No cron grant: the role already reads them.
- Artifact cells carry `is_serving` as a fact, never a verdict; a GitHub-hosted artifact is judged with the delivery slack (below).
- capacity: a job's interval is its median start gap, not its schedule (no cron-expression parser), and its `statement_timeout` is parsed from the command's `set statement_timeout='NNNs'` prefix (absent: that arm is not judged). `check_timeouts` counts rows with `details ? 'timed_out'`, one cell per host (below); `worker_memory` is Not measured until the autodedup lane has passed in this process (below); `db_restart` is a fact cell that is always OK.
- llm: the credit cell matches the credit patterns `llm_errors` used, quoted verbatim, and an authentication failure does not count (the plan said "credit or quota"); the error-share Watch also needs the provider's newest call to be an error; "idle" means no call in 72 h.
- Registry: `jobs` (600 s, "Jobs"), `capacity` (1,800 s, "Capacity") and `llm` (600 s, "LLM providers"), all unit "cells" with the 45 s budget, right after the platform block in `CHECKS`; `migration_drift` stays on GitHub and moves from 21,600 s to 3,600 s. `CHECKS` holds 23 keys, 20 on GitHub and 3 on the worker. `verify_pipeline.main()`'s `--host` accepts `github-actions` only, and the worker never calls `main()`.
- `THRESHOLDS` is the one home (`DEFAULT_THRESHOLDS` and `load_thresholds` are deleted), every entry with a one-line basis comment; the slack is spelled `GITHUB_DELIVERY_SLACK_S` (18 × 3,600). Nothing reads the `pipeline_check_thresholds` row after this wave.
- The health lane projects the registry on its process's first pass rather than "at lane start", prunes once per 24 h per process, 10,000 rows at a time by `ctid` until none is left, records `last` = `{"measured": [keys], "pruned": n}`, and has no kill switch (the text_extract precedent: a brake is a deploy). The worker imports `scripts.verify_pipeline` lazily inside the pass; the image has `scripts/`, and `migrations/`, which only `migration_drift` reads, stays dockerignored.
- Poller: the production filter applies to successes as well as failures, and the canary is excluded by name from both; a poll with `GITHUB_REF_NAME` set and not `main` writes nothing and says so in one log line. `workflow_run_health` is upserted for every production run seen (a row is born on a failure too); `state` comes from one workflows-list call per poll, and `on_main` from the file's presence in the checkout. The reason is fetched for newly inserted rows within `MAX_LOG_FETCHES` (kept at 25, which answers wave 2's owed question) and `REASON_PASS_BUDGET_S` (90 s, renamed from `INCIDENT_PASS_BUDGET_S`); a row past the budget keeps `reason` NULL, with no retry. `record_incidents`, the claim and emit path, `_is_ladder_owned` and its two constants (owed by wave 2) are deleted; the cursor logic, the cancelled gate and `_advance_cursor` are unchanged.
- `reason_line`: tier 1 is the LAST `CHECK <key> status=fail` line (a fail only); tier 2 skips `##[group]`…`##[endgroup]` blocks and adds `Actions` to the boilerplate words; the Actions timestamp prefix is stripped; at most 300 characters. The fixture, `tests/fixtures/workflow_job_116299577551.txt`, is that job's log (116299577551 is a job id, not a run id), scrubbed.
- Outbox: the subject is the text before the first ` — ` (else the first 120 characters; "System alert" when empty), not "the message's first line", because every ladder message is one line of up to 1,500 characters. The link is `/health#<key>` when the dedupe key matches `^sys:([a-z0-9_]+):`, else `/health`, and an unset `SPA_BASE_URL` keeps today's empty origin. Both drain passes now select `d.dedupe_key`: two SELECTs, two unpacks, two compose dicts and their test fakes, more than the plan's ~8 lines. The old Health page's pipeline-check rows gain `id={check_key}`, and the section scrolls to the hash (below), so the anchor lands before wave 8, and the SPA route contract declares `health`, not `notifications`, as the route the API emits. Q1 stands: `system_health_channels` stays `[]`, so this is readiness.
- Migration 621 resolves with `not starts_with(signature, 'cell:')` (619's idiom) and `resolve_reason = 'retired: signature engine'`, under a 5 s `lock_timeout`; its post-conditions check the six columns, that no open non-cell incident is left and that the grants are unchanged. It drops nothing (the columns and `ops_incidents_open_signature_uq` are 624's), and the legacy `sys:ops_incident:*` notices stay as they are. A signature row the old code opens between apply and merge is read by nothing (every reader filters on `cell:`), and 624 repeats the resolve, as planned.
- Migration 621 also re-creates `health_liveness()` (620's body with one cell changed) so that `health_lane` follows § 4's rule, the lane's own pass, read from its heartbeat entry (`details -> 'health'`). 620 had judged the newest worker-hosted result row, but the lane passes every 60 s and writes a result only for a due check (jobs and llm every 600 s, capacity every 1,800 s), so gaps of 540-600 s are normal and a deploy restart near a cycle's end crossed 10 min: a false Problem with an onset notice. Problem: no completed pass for 10 min, counted from the later of the last pass and the worker's boot (the seed carries the last pass across a restart, so a restart is judged 10 min after boot), or failing for 10 min (the message quotes `last_error`), and also a pass in flight for 10 min, because a pass skipped behind an abandoned one still records a pass. The cell exists once the heartbeat carries a `health` entry, not once `health_checks` holds worker keys; the post-condition also checks the function's grants and body and keeps one liveness row.
- Rails: the replay rail's registry copy is host-aware (3 × cadence on the worker), and its lone-OK test moved from `llm_errors` to `walk_coverage`'s 2026-10-08/09 streak; the three `R2_CARRIERS` skip pins that guarded `scripts/backfill_child_listing_ids.py` moved to `tests/scripts/test_backfill_child_listing_ids.py`; the PREPARE allowlist gains entries for `scripts/checks/platform.py`; `test_verify_pipeline`'s host-blind rails (the count of 28, the slack set, "all on GitHub") are host-aware. The jobs, capacity and llm replays are INPUT replays: `tests/fixtures/health_jobs_14d.csv`, `health_capacity_cron_8w.csv` (weekly aggregates per pg_cron job from 2026-08-10) and `health_llm_14d.csv` (hourly, per provider and `called_for`), each with its refresh SQL in the test's header (wave 1's pattern); what their quiet windows can show is stated below.
- `monitor_workflow_failures.yml` was already deleted in wave 2 (its As built note), so nothing of it is left here; `toolkit/ops_incidents.py` was 401 lines and its test 377, not 396 and 369.
- Owed and stated, named here so it is not lost: `recent_starts` accumulates from this wave with no history import, so the start-gap arm is Not measured until a workflow has 10 scheduled starts (about two weeks for a daily job); a red run past the reason budget keeps `reason` NULL and is not retried; a pg_cron tick that skips itself is indistinguishable from a run and biases p50 down, which the sticky reference only strengthens; `app_settings.broker_resolution_last_complete` is still written by `scripts/resolve_brokers.py` and read by nothing, and `dual_write_watermark` is read by nothing, both for 624 to drop (wave 9's Deletes list them; the broker row is brokers territory, untouched here); at merge the canary's first projection from main retires the eight legacy keys, the ladder closes their open cell incidents as `retired` with no notice (the open `llm_errors` one included), and the new `llm` check opens a fresh onset within minutes of the worker deploy, which the PR body says and no code softens; wave 2's three rulings (the `api` cell's 26 min, the NULL lease, the red held through a recovery hold) are unchanged.

The review of draft #1871 (2026-10-19) changed or stated twelve things the contract had wrong or left out:
- **`worker_memory` judges only a reading this process took.** The boot seed carries each lane's `last` across a restart, and keeps doing so because `text_extraction_lag` reads the text lane's, so until the autodedup lane's first pass in a new process its `last.peak_rss_mb` is the dead process's peak. `_WORKER_MEMORY_SQL` also reads the lane's `last_pass_at` and the heartbeat's `started_at`; while `last_pass_at` is NULL or older than the boot, the cell is Not measured ("no autodedup pass since the worker booted"), with `last_pass_at` and `boot` in its facts. The contract judged any `last`.
- **A GitHub-hosted artifact gets the delivery slack.** An `artifact/<name>` row whose `host` is `github-actions` is stale only past `staleness_budget` + `THRESHOLDS["GITHUB_DELIVERY_SLACK_S"]` (18 h): GitHub delivers a cron late by hours (54 of the canary's last 65 scheduled gaps exceeded 6 h: median 7.2 h, longest 13.5 h, 09-28..10-18), so a budget of a few cadences would ring on its ordinary lateness. Its facts carry `budget_s` and `slack_s`; every other host keeps the bare budget. The contract judged every row by `public.health_stale(last_succeeded_at, staleness_budget)` alone.
- **`check_timeouts` is one cell per host that measures through the harness**: `check_timeouts/github-actions` and `check_timeouts/realtime-worker`, each the share of that host's own keys' results in 24 h that timed out, so one host's timeouts are not averaged into the other's. The watcher's key (host `pg_cron`) runs on no harness clock and is left out. The thresholds are unchanged (Watch above 5 %, Problem above 20 %), measured on the GitHub population. The contract had one cell over every result.
- **`recent_starts` holds scheduled starts only.** The poller adds a start for an `event = 'schedule'` run alone, because a push has no cadence to be overdue against. A push on `main` is still a production run: it creates or updates the workflow's `workflow_run_health` row (`last_success_at`, `state`, `on_main`), and its red is inserted into `workflow_failures`. So for a workflow that only runs on push, the long-cycle and overdue arms are Not measured, a lone red is Watch and 3 reds in a row are a Problem. The contract stored every production start.
- **A lane entry from before wave 3 is Not measured.** A heartbeat lane entry with no `interval_s` key at all, as a pre-wave-3 worker writes it, makes that lane's cell Not measured, which is distinct from the seeded None (the key present and null) that reads "never woke" after 15 min. This guards the first deploy, while the old process's beat can still be the newest row and its uptime is long past the boot grace.
- **`pg_stat_activity` can hide sessions.** `connections` and `open_transaction` also count the rows whose `backend_type` is NULL, which is how the view shows a session the reading role may not inspect. When there is any, both cells are Not measured, "pg_stat_activity hides other sessions from this role", because a count or an oldest transaction over the visible sessions alone reads low and passes. The contract read the view as complete.
- **The capacity reference expires.** A rail in `tests/scripts/test_verify_pipeline.py` fails once `THRESHOLDS["CAPACITY_REFERENCE_FROM"]` is more than 90 days before today (§ 4: "at most 90 d back"), so the dated re-blessing PR is forced, not remembered. The contract stated the bound but nothing enforced it.
- **The pg_cron SQL runs for real.** The hermetic tests answer each statement by a fragment of its text, so no cron SQL had ever executed. In CI's replay database, which has no pg_cron, a rolled-back transaction creates a stub `cron` schema (`job`: `jobid`, `jobname`, `schedule`, `command`, `active`; `job_run_details`: `runid`, `jobid`, `status`, `return_message`, `start_time`, `end_time`), seeds runs and executes `_CRON_JOBS_SQL`, `_CAPACITY_CRON_SQL` and `_CAPACITY_REFERENCE_SQL` through psycopg with their real parameters, asserting that only finished runs count, that the 7-day window counts back from the job's newest start, "2 of the last 3", and whole-week reference buckets; the artifact read runs there too, on the replay's own `derived_artifacts` with the GitHub delivery slack the check sends. The rail, `tests/test_platform_cron_sql_live.py`, is one step in `migrations.yml` after 621's (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`) and skips locally.
- **Flapping, worded as built.** A lane flaps when, since this check's previous result, at least as many passes failed as completed, over at least 3 attempts (`passes` counts completed passes only), which is § 4's "50 % of at least 3 passes failed". The contract's "the `failed_passes` delta ≥ 50 % of the `passes` delta" (failed ≥ 0.5 × completed) was a mis-statement the code never followed, and the skill reference that repeated it is corrected.
- **Limits stated, not coded.** The long-cycle arm (median start gap ≥ 24 h) is Not measured until a workflow has 2 scheduled starts, one to two cycles for a weekly or monthly job, because `recent_starts` accumulates with no bootstrap. The overdue arm is judged as of the poller's cursor, not now: the time since the last success counts to the cursor (the newest completion a whole poll has seen), which is how far the workflow's runs are known. A provider's credit outage is told twice until wave 6, by `llm`'s `<provider>/credit` cell and by `jobs`' `lane/text_extract` cell, because the text lane's provider-fatal pass is a failed pass. A workflow that dispatches itself is recorded by its cron run only: its chained hops are dispatch runs, which the production filter drops. The replay rails' quiet windows are what the data allows: 8 quiet days for pg_cron (10-11..10-18), the week of 10-12 for capacity, with the relative arm Not measured on the blessed 10-17 reference, and none for GitHub workflows, which stored no success but the newest before this wave (llm's quiet fortnight, 09-14..09-27, is a full one). The capacity fixture starts 2026-08-10, a week before the 08-17 saturation, and its `browse-list-rebuild` replay runs on a reference date overridden to 08-10: the incident opens inside that reference week through the p95 arm, before the relative arm can judge, and the relative arm then holds it red to the end.
- **The deep link lands.** The old Health page renders its pipeline-check rows after their fetch, so the browser's own jump to `/health#<key>` finds no element; `PipelineChecksSection` scrolls to `location.hash` once its rows exist (the `EstimationsBlock` pattern). The contract added the `id` only.
- **The boot seed does not carry `interval_s`.** Contract § 2.4 left out only `started_at`, `in_flight_s` and `last_failure_at`, but a carried interval makes a lane that never wakes in the new process read late against the old value (or off, if it was off) instead of "never woke". The registration's None stands until the lane's first wake, and every list of the keys the seed leaves out names `started_at`, `in_flight_s`, `interval_s` and `last_failure_at`.

- Measured against main at the merge (`git diff --numstat`, after the review fixes): code +1842/−2231 (net -389: the harness −1065, `toolkit/ops_incidents.py` −401, `failure_signature.py` -183, against `scripts/checks/platform.py` +1016, the worker +214 and the poller -32), migration 621 +371, tests +2354 (of which production-export fixtures and the job-log fixture +1231), docs +413: +2,749 in all. The plan's "about −900" did not count the three checks' rules (one cell family per runtime), the input-replay fixtures, the two live rails or the watcher's redefinition; the eight legacy checks, the signature engine and its tests did go. The canary job is 35 min with a 21-min harness step (20 budgets, 1,050 s; 40 and 27 before).

- First night (2026-10-19 02:46, fixed the same morning on `fix/health-lane-start-grace`): the autodedup lane came off its brake with its first pass in flight and `jobs` read it "late: no completed pass for 2.1 h", counting from the worker's boot; a lane that read off at this check's previous measurement now counts from that measurement. One false onset and recovery were sent.

**Depends on.** W2

**Operator gate.** Q5 (the 35-day prune) and Q6 (dual_write_parity), answered upfront.

**Adds.**
- scraper/realtime_worker.py health lane (~50 lines): HEALTH_TICK_SECONDS = 60 as a constant (no app_settings row, following text_extract), _HEALTH_PASS_LOCK, asyncio.to_thread(verify_pipeline.run_due, host='realtime-worker'), 300 s pass budget. It projects realtime-worker rows at lane start and prunes results older than 35 days once a day (Q5).
- The jobs, capacity and llm checks (scripts/checks/platform.py)
- Heartbeat: _lane_loop writes interval_s on every wake, idle lanes included, so 'off' is visible. failing_since and last_error (≤ 300 chars) replace last_failure_at; failed_passes stays carried. The probe and drain passes return the portals that raised → portal_errors {portal: failing_since, last_error}, cleared on that portal's next success. At boot every lane seeds these from the previous worker_heartbeats row, so ~11 deploys a day reset nothing.
- Poller (scripts/record_workflow_failures.py), now the canary's first step (permissions actions: read and GITHUB_TOKEN; the harness step runs with if: !cancelled() and the assertions step with if: always(), both built in wave 2, so a failing poller still lets the checks run and the run ends red). It records only production runs (event schedule, or push on main; the canary's own runs excluded), stores each failure's reason line in workflow_failures.reason, and upserts workflow_run_health.state (Actions workflows API), on_main (the file is in the checkout) and recent_starts (the last 20 production starts).
- failure_signature.reason_line() (~50 lines kept of 251): the CHECK line first, then the last exception line that is not runner boilerplate (Image, Version, Commit, Region, Cloud, Contents, Metadata, Packages, 'Note: switching to'), then ##[error].
- api/notification_outbox.compose_message: a system_health subject is the message's first line, and the link is SPA_BASE_URL/health#<key>, taken from the sys:<key> dedupe key (~8 lines). This replaces the fixed Czech subject and the /notifications link.
- _MIGRATION_OBJECT_PROBE_SQL and _SAFE_IDENT move to scripts/migration_objects.py; apply_migration_receipt's import follows them.
- DEFAULT_THRESHOLDS becomes THRESHOLDS (no override path left), gaining CAPACITY_REFERENCE_FROM = 2026-10-17 (the first full day on the 2XL instance) and GITHUB_DELIVERY_SLACK = 18 h, each with its basis.
- migration 621

**Deletes.**
- Legacy keys worker_lane_stall, broker_resolution_freshness, workflow_poller_liveness, db_saturation, long_open_transaction, llm_errors and llm_burn_rate, and (Q6) dual_write_parity with arm_dual_write_parity, --arm-dual-write-parity and its R2_CARRIERS consumer; their thresholds and tests go with them.
- toolkit/ops_incidents.py (396 lines, W1's fence included) and tests/toolkit/test_ops_incidents.py (369)
- failure_signature's signature tiers (~190 lines) and most of test_failure_signature.py
- record_workflow_failures.record_incidents and its claim/emit path (~120 lines)
- portal_runner.record_failure_signature and the incident write in _record_run_crash; realtime_worker._record_lane_failure (a blocking DB write per failing portal)
- ~~.github/workflows/monitor_workflow_failures.yml (its step lives in the canary)~~ Deleted in wave 2 (its As built note).
- The heartbeat pseudo-lane special case and the lane field last_failure_at
- load_thresholds' app_settings merge: every override already equals its code default (the row is deleted in 624)

**Migrations.**
- 621_platform_checks.sql — ADDITIVE.
- ALTER TABLE workflow_failures ADD COLUMN reason text.
- ALTER TABLE workflow_run_health ADD COLUMN state text, ADD COLUMN on_main boolean, ADD COLUMN recent_starts timestamptz[].
- UPDATE ops_incidents SET resolved_at = now(), resolve_reason = 'retired: signature engine' WHERE resolved_at IS NULL AND signature NOT LIKE 'cell:%'. This works by predicate (4 rows today) and is reversible; 624 repeats it for any row reopened between apply and merge.

**Tests.**
- jobs:
- an MF-shaped history (3 production reds) fails with the reason line;
- a monthly job's single red fails; a lone red on a frequent job is Watch;
- dispatch and pull-request runs are ignored; a file gone from main has no cell; a disabled workflow reads off;
- a stale poller cursor turns every GitHub cell unknown;
- lanes off, late, failing, a daily lane's single failure, portal failing, in flight, absent and flapping; counters survive a restart;
- pg_cron 2 of 3 failed, and overdue;
- artifacts through health_stale with their facts; never succeeded is unknown.
- capacity: the 08-17 → 10-10 replay fixture never recovers against the sticky reference; p95 against interval and timeout; timeout share; open transaction; connections; memory; the relative arm is unknown before 7 days of post-reference history.
- llm: one provider's success no longer masks another's credit outage; the 72 h lookback; starvation; spend; a provider with no calls reads ok idle.
- tests/scripts/test_failure_signature.py: the run 116299577551 log fixture yields its CHECK line, not 'Metadata: read'.
- tests/scripts/test_record_workflow_failures.py: PR and non-main runs are skipped for both failures and successes; the reason, state, on_main and recent_starts are stored.
- Heartbeat: interval_s on idle lanes; failing_since set and cleared; portal_errors; boot seeding.
- Lane: registered with a constant tick and a pass lock; projects only realtime-worker rows; prunes once a day.
- Replay rails for jobs, capacity and llm: 14-28 d quiet-window aggregates give 0 onsets, and incident-shaped fixtures fire.

**Docs in the same PR.**
- CLAUDE.md: 'health checks' joins the worker's lane sentence and rule 16's system_health note names cell incidents, both in place (298 lines kept)
- scraper-ops SKILL.md in place: :3 (lane list gains health; the llm_burn_rate trigger becomes the health lane), :201-203 (the monitor workflow becomes the canary's poller step), :318 (the crash path writes no incident; jobs cells judge it), :326 (worker_lane_stall becomes jobs lane cells)
- pipeline-verification.md: jobs, capacity and llm sections; the signature section removed
- toolkit-api SKILL.md: system_health delivery subject and deep link (in place)

**Risks.**
- Grouping failures across workflows by error text goes; one grouped jobs notice per pass lists every newly failing job with its reason line.
- The GitHub arm lags by the canary's gap (median ~4.8 h).
- Lane thresholds rest on 7 days of Railway logs read at build; the only stored episode is autodedup's 10-02 stall.
- Each merge restarts the worker, and with it the autodedup lane, so merges are timed around autodedup seed sittings.
- A dead outbox cannot deliver its own alarm; the canary's delivery assertion covers that case.

### Wave 4 · Portal checks: scraping, publishing, delisting, coverage

**Goal.** Each portal's discovery, publishing, delisting and holdings are judged by four checks built on ledgers that see the worker's work, with limits taken from policy constants and each portal's own behaviour; Browse's whole stock is watched, not only its newest ads.

**PR.** Draft PR on feature/health-portal-checks. It contains:
- scripts/checks/{scraping,publishing,delisting}.py and the coverage gauge;
- the Portal.total_is_count seam and portal_runner.DECLARED_TOTAL_KEY;
- delist_policy.RECHECK_THROTTLE, named and unchanged;
- the property_sweep artifact (migration 622);
- retirement of the walk, queue, delisting and image-probe checks, coverage_gate, and the slice writes only it read.
No delisting behaviour changes. ~~Net about −1,100 lines.~~ Not as built: measured +15,584 (the As built note, below).

**As built (2026-10-19).** Wave 4 (branch `feature/health-portal-checks`) follows this plan except where its build contract was more precise:
- **One set of result helpers.** `scripts/checks/common.py` holds the result-shape helpers moved verbatim out of `platform.py` under public names (`cell`, `judged`, `result`, `oneline`, `ago`, `num`, `as_utc`, `iso`, with `graded`, `now` and `CLOCK_SQL`; `platform.py` imports them under its old private names, the wave-2 clock precedent) and one new function, `wilson_lower(hits, n, z=1.96)` (None when n is 0), the one Wilson bound behind every share arm. No module re-spells a result shape. Every arm judged on that bound (fetch errors, visible, stock's Problem, photos' not-stored share, precision) stores the raw share in percent as its `value`, what happened, and the bound it was judged on in `facts.lower_bound` and in its message.
- **Registry.** `scraping`, `publishing` and `delisting` (alarms, 600 s) and `coverage` (a gauge, 3,600 s), all hosted on `realtime-worker`, unit "cells", the 45 s budget, right after `llm` in `CHECKS`; `check_coverage` lives in `scripts/checks/scraping.py`, beside the alarm that reads the same walk ledgers, rather than in a module of its own. `CHECKS` holds 20 keys, 7 on the worker and 13 on GitHub, whose budgets sum to 660 s (1,050 s in wave 3). Every cell carries `portal`; the fleet cells are `fleet/stock` and `fleet/dirty`, with `portal` null. all four checks take their portal list from `location_data.claims_common.SOURCES` (delisting's was the union of its ledgers until the review), so a portal never drops out: one for which no walk of the last 30 d recorded a unit (no walk at all, or none finished) reads a `<portal>/walks` Problem and keeps its inflow, fetch-error and unnominated cells (the last Not measured until a walk reaches the end), and `<portal>/walks` is measured on every pass, OK with the hours since the portal's newest finished walk started once a walk records a unit, so that incident recovers on a measured OK instead of closing silently after 7 days.
- **Words** (`GLOSSARY.md` § Collecting ads). A walk is one `scrape_runs` row with `run_type = 'index'`, finished when `ended_at` is set and `by_category` is not empty: a crashed or killed walk keeps `[]` and is stamped later by the API's boot sweep, so it is never finished and never enters G. A unit is one `(source, category_main, category_type)` label; it reached the end in a walk when every entry of that label did, and a unit absent from a walk was not walked in it. G is a portal's median start-to-start gap of its finished walks over 14 d (the open gap never counts; under 10 gaps, the slowest portal's G).
- **Missing is per unit, not per walk scope** (§ 4 said "per walk scope"): entries carry no scope key and labels repeat (bazos writes 22 entries over 10 labels; ceskereality and realitymix list `dum` twice). An entry whose `total_is_count` is true ("declared is a count of what was seen") or ABSENT ("not yet recorded by this walk's adapter", every entry written before this wave) is Not measured, never 0 %; `declared_is_upper_bound` rides as a fact. Coverage's residual follows the same rule.
- **Publishing.** Ingest counts NEW-priority rows that have no listings row for `(source, native_id)`, so the known ads `refresh_stale_image_urls` queues at that priority do not count; changes counts the CHANGED and FAILURE priorities; "location not served" is judged on the property's canonical ad; a property born after the newest successful `browse_list` rebuild started, or whose canonical ad's location was resolved or re-resolved after it (`listing_location.resolved_at` moves on every re-resolve, and the resolver never patches `browse_list`), is a fact ("waiting for the next rebuild"), not a blocker, so a failed rebuild is told once, by `jobs`' `artifact/browse_list`; stock counts properties with `status = 'active' AND is_active`, a portal's cell counted from listings' `source`, `is_active` and `property_id` columns, in one short statement; the photos' narrow share leaves out stored rows with no width, and the photo facts are the top `last_error` and the count of new ads with no image rows. The "active stock stored share" fact of § 4 is DROPPED: an 8M-row scan every 10 min; the image panels keep it until wave 8.
- **Delisting.** The stuck limit is the effective dwell `max(GONE_DWELL, RECHECK_THROTTLE)` (24 h today) + 2 × the portal's p90 walk-start gap + 1 h, the first unrefuted verdict found per ad before counting; the portal-level gap understates portals whose units rotate (idnes's stalest-first order, ceskereality and realitymix slice groups, remax and maxima agendas): stated, not modelled. Deferrals are judged per PORTAL, not per unit as § 4 said, because an agenda portal files an agenda's deferrals on one descriptor and a sibling-buffer portal on a group's last; a walk counts there when any unit reached the end, while coverage's portal-level walk needs every unit to (each answers its own question). The verify backlog counts claimed rows, and the location refetch's rows share its priority. Outflow compares the trailing 24 h with each of the four weeks back read at every 24 h window ending within ±3 h (`delisting_outflow_align_h`) of this hour, not the same 24 h alone as § 4 and the contract said: Problem and Watch against the median of each week's quietest window, the spike against the median of its busiest, because a crawler's flips land in bursts at its walk times and on 2026-10-05 realitymix's midday walk started two hours later than in the baseline weeks (one of its Sunday walks missing too), which the same-hour rule read as a one-hour Problem on a normal day. Precision reads a plain `completed_at` range over the pruned 7-day ledger, with no portal predicate: the planner reads it whole (about 200k rows, ~0.3 s) more cheaply than per portal through `detail_queue_completions_source_completed_idx`, which it prices about three times higher, and the gone index § 4 names is partial on `outcome = 'gone'`. Outflow's statement reads one index range per lag binned by the hour and sums the ±3 h windows from the bins (a range per window made the planner seq-scan `listings`: 4.6 s against 0.12 s on 10-19); its params are `lags` and `align_h`, the one new key `delisting_outflow_align_h` = 3. The 50-a-day floor applies to the quietest windows, so remax and mmreality are Not measured about 68 % and 50 % of hours (45 % each before): disclosed in § 4; an operator ruling could move the floor to the busiest median.
- **Coverage.** The residual is judged per (portal, unit) at the unit's newest end-reached walk, R beyond ±2 % / ±5 % with D ≥ 500; "cannot be nominated" falls back, for an ad with no unit, to the portal's newest walk in which every entry reached the end, and counts an ad with no queue row and no completion newer than its last sighting, not "since that walk's start" as the contract read § 4: an ad checked inside the 24 h recheck throttle before that walk is in flight, not stuck, and the literal reading flagged about 3,600 such ads on 2026-10-19 (Problem on five portals); every cell carries `trend_30d` (its own value 7 and 30 days ago, from this check's results) and `worse_than_7d`, § 4's rolling trend and worse-than-7-days flag.
- **Pass cap.** `HEALTH_PASS_BUDGET_SECONDS` is 360 s, not wave 3's 300 s: 8 × 45 s, the seven worker checks now and wave 5's dedup the eighth. The watcher's 10-min `health_lane` rule still holds a full pass and its 60 s tick.
- **Seams.** `PortalDefaults.total_is_count = False`, read through `getattr(portal, "total_is_count", False)` because the runner's test fakes do not inherit the defaults; remax and maxima set it True, one line each, and every `by_category` entry also carries `declared_is_upper_bound` from the existing `coverage_denominator_is_upper_bound`. `portal_runner.DECLARED_TOTAL_KEY` is `'sreality_result_size'`, the live matviews' key until wave 9. `delist_policy.RECHECK_THROTTLE` is rendered into `db.presence_candidates`' f-string as `interval '24 hours'`, so the SQL text is byte-identical, and a rail pins both the rendered text and the constant, so a change there is a visible decision.
- **property_sweep.** Migration 622 registers it with a 38 h staleness budget, not 56 h: the `jobs` check adds GitHub's 18 h delivery slack to a `github-actions` artifact, so the effective Problem stays at today's 56 h, and the 52 h Watch tier is lost, because an artifact cell has none. A separate update seeds `last_succeeded_at` and `complete_through` from `app_settings.property_sweep_last_complete` (continuity in production, a no-op in CI). The sweep stamps through `db.stamp_derived_artifact` inside `_finalize`'s transaction, between the dirty clear and the cursor delete, with `rows` = the properties the finishing run recomputed and `duration_ms` = that run's time; `complete_through` is the stamp time, not the cycle's start (`docs/architecture.md` rule 20). The `app_settings` write and its tests are deleted, and with them the `runs`, `cycle_started_at` and `max_property_id` facts, which nothing reads.
- **coverage_gate's retirement** also deletes `portal_factory.canonical_category_count` and `_config_for`, dead without the gate though not on the Deletes list; the ceskereality descent tests and mmreality walk tests that observed through the slice ledger moved to the smallest other observable. idnes keeps its slices (`slice_staleness`).
- **Rule 21, as a rail.** `tests/scripts/test_checks_rule21.py` walks every `scripts/checks/*.py` and fails when a string literal outside a docstring holds a portal name as a whole word (any case), the value of `portal_runner.DECLARED_TOTAL_KEY` or the identifier `sreality_id`, so the modules import the portal list and `DECLARED_TOTAL_KEY` rather than spell them, cannot join on `sreality_id` (they key joins on `(source, native_id)`), and say "maximum values" where English would say "maxima". They import the queue priorities, `delist_policy`'s constants and the served-location rule too, by convention: numbers and SQL the rail cannot see.
- **Replays.** Each check has an input replay over fixtures exported read-only with their refresh SQL in the test's header; the queue and completions exports ran first, before the 7-day ledger lost the 10-13 bazos burst and the 10-12/13 cohorts. The incident-shaped fixtures for the 08-17 starvation, the 09-14 Browse drop, the 09-17 bazos location loss, the iDNES photo throttle and (h) the 30 % revived cohort are SYNTHETIC, because no store keeps their history, and say so in their tests' docstrings with the figures they reproduce; the 10-10 zero-flip day is real (`listings.inactive_at` has no stamp from 2026-10-09 21:39Z to 10-11 06:04Z). What the input replays open, per arm. Scraping: walks, ceskereality byt/prodej's 27-day hole (open from the replay's first tick, closed on a measured OK on 10-13) and the 403 wall (nine units opened together on 09-30, told in one grouped notice, closed 10-02), every other gap Watch at most; inflow, the 403 wall (ceskereality, 09-27 to 10-03) and the 09-28/29 stall (idnes, mmreality; a day before the 09-29/30 worker freeze, cause not established), the stall a replay artefact: the replay has no queue history, while live the arm adds the NEW rows still queued, so a stopped drain is publishing's ingest (past 6 h), not lost discovery; missing and fetch errors, nothing. Coverage (daily): the residual of ceskereality dum/prodej, a Problem every day (the double count, #1854), and of byt/prodej from 10-14, when the hole's first end-reached walk landed (about +40 %, then Watch while that stock drains); sreality byt/pronajem on three single days past 5 % (09-21, 09-28, 10-01, each closed two days later); and eleven units on six portals on 10-14/15, when the walks after the 10-12/13 delisting tail held 5-8 % more active ads than declared. The replay can set aside only the in-dwell count of 10-19 (no store keeps an as-of count), so how many of those eleven were ads waiting out their dwell, which the live arm sets aside, is unknown. Unnominated keeps no history. Publishing: the queue's week and the dirty queue's 35 days, nothing, the bazos 10-13 burst (about 1,110 ads waiting at once, the oldest 119 min) included; visible, five Problems on four sweep days (ceskereality and sreality 10-02, ceskereality 10-05, sreality 10-08, bezrealitky 10-09), each closed on a measured OK within 80 min and none since 10-17: new ads waited 41-100 min for their property while the daily full sweep held the maintenance lease, because `recompute_property_stats` attaches stragglers once, at its start, and the worker's maintenance lane skips while the lease is held. Those ads really were out of Browse past the 15-min target, so the onsets are disclosed, not silenced (owed below). Delisting: outflow (09-21..10-19 hourly): the contract's fixed windows open 17 onsets; aligned by ±3 h they open 13, the same 13 at ±2 and ±4 h: the 10-10 zero-flip day on six portals (mmreality joins remax and maxima as Not measured, its quietest windows under the 50-a-day floor), ceskereality's 403 wall (09-29 15:00 to 10-02 13:00), the 09-28/29 stall on realitymix and the 10-12/13 tail on bazos, realitymix, mmreality, idnes and remax; realitymix's late walk of 10-05 opens nothing, and two marginal fixed-window onsets go with it (idnes 09-29 00:00 at 19.7 %, sreality 10-13 02:00 at 19.4-20.0 %; those stalls read Watch at 24-47 % and 20-49 % of their quietest windows). The 28 days hold no 14-day onset-free stretch (quiet: 09-21..09-28, 10-02..10-10, 10-14..10-19); precision, the 10-13 post-dwell cohorts (sreality and idnes, open 10-15, closed 10-16) and nothing after; stuck and the verify backlog, nothing; deferrals, a Watch only (ceskereality's 10-13/15 catch-up). The synthetic shapes fire as the Tests below ask: the starved queue one Problem at 6 h, six portals in one notice, the 09-14 and 09-17 stock drops, the photo throttle and the 30 % cohort. The rules the checks encode in SQL also run for real: three seeded, rolled-back live tests (`tests/test_checks_{scraping,publishing,delisting}_sql_live.py`, one step of CI's migrations lane) execute each module's statements with the check's own parameters on the replayed schema, because the hermetic tests feed canned rows, the replays model the SQL in Python and PREPARE checks only names and types. The wave-1 output replay was re-exported over the surviving keys; its recovery rail now rests on the `jobs` check's own recovery of 2026-10-19 02:46Z, and its lone-OK rail went, because no surviving key holds a lone OK inside a fail streak in the new window (the hermetic ladder tests keep the rule).
- **Retirement at merge**, as wave 3's: the canary's first projection from main deletes the seven GitHub keys' registry rows, the ladder closes their open incidents silently as `retired` (`false_delist_share`'s Watch included), and the new checks open fresh onsets where they measure Problem; until that canary run both sets show on the page.

Owed and stated, named here so it is not lost:
- The missing and residual arms are Not measured for every unit until its first walk after the deploy writes `total_is_count`, and for remax and maxima after it too, by their own declaration.
- The fetch-error denominator approximates "attempted in 24 h" (the queue keeps no per-attempt stamp); the stuck limit uses the portal-level walk gap; deferrals are per portal; the photos arm cannot judge ads with zero image rows (a fact counts them), and its narrow share sees only a downgrade below 600 px: the one on record, the 749 px cropped frames `docs/architecture.md` describes under Photos, would read 0 % narrow (owed: judge the stored width against the declared source width).
- At ship (the production read of 2026-10-19 and the replays): the coverage gauge opens a Problem on realitymix/unnominated (151 NULL-category ads, the oldest last seen 112 d ago, #1853) and reads Watch on ceskereality/unnominated (96); from the unit's first walk after the deploy that records `total_is_count`, ceskereality's dum/prodej residual reads Problem (about −14 %: 13.7 k declared against 11.8 k held, the chaty-chalupy double count, #1854), not Watch. `delisting` read Watch on idnes/outflow that morning (a 423 % spike against the same hour one to four weeks back); against the busiest 24 h within ±3 h of that hour the same 24 h is about 210-240 %, under the 300 % spike line. sreality's 10-18 flip cohort reads ~17.5 % active again (lower bound ~15.5 %), so its precision cell may open Problem on 10-20/21: the blink tail #1858 addresses, told truthfully, not suppressed.
- On a day the full property sweep holds the maintenance lease longer than about 40 min (its budget allows 6,000 s), new ads wait for their property past visible's 30-min grace, and visible opens Problem for the sweep's duration, recovering on a measured OK when the lease is released (the replay's five onsets above). Owed: attach stragglers while the sweep runs (`roadmap/scraper-track.md`, Next).
- Left behind, and owed to wave 9's list, which today names only `portal_coverage_gate`: the 150 ceskereality and mmreality rows of `portal_index_slices`, read by nothing; `portals.supports_complete_walk`, which has no writer now (the old page's portal posture still reads it); and `delist_flip_refusals`, still written by `db.enqueue_presence_checks` though its one reader, `delist_flip_refused`, is gone (a wave-9 drop removes the writer first).
- The 52 h sweep Watch tier is gone; the Problem stays at 56 h effective.
- The old page's SQL checks (`delisting_latency`, `delisting_spike` and the rest of the matview family) keep rendering until wave 9.
- A dispatched `--max-pages`, `--limit` or bazos region run still finishes as an index walk, so it shortens its portal's G and never reaches the end; marking such runs in `by_category` is the scraper track's Next.

- Measured against main at the merge (`git diff --numstat`, after the review fixes): code +1,757/−1,735 (net +22: the harness -874, `scripts/coverage_gate.py` -311, the two scraper mains -81, against `scripts/checks/scraping.py` +606, `publishing.py` +388, `delisting.py` +345 and `common.py` +123, of which 96 lines moved out of `platform.py`), migration 622 +66, tests +15,145 (fixtures +12,638 net: nine input-replay exports +12,709 and the output-replay re-export -71; the three live SQL rails +937), docs +351: +15,584 in all. The plan's "about −1,100" counted the seven checks and the gate against three modules; it did not count four checks' rules over nine portals, the replay fixtures or the rails. The seven checks, the gate with its workflow and tests, the slice writers and the app_settings stamp did go; the code is net flat (+22 after the review fixes).

**Depends on.** W3

**Operator gate.** Q3 (the delisting wait, as information for its own follow-up PR, and the publishing lines), answered upfront.

**Adds.**
- Alarms scraping, publishing and delisting, and the coverage gauge (see the catalogue)
- Portal.total_is_count (default False; the remax and maxima adapters set True, a one-line override each), written into every by_category entry by portal_runner. A rule-21 seam replacing _SELF_CERTIFYING_TOTALS: measured, 56 of 110 units had declared = collected on every walk in 14 d (sreality 15 of 20), so the property cannot be inferred from data.
- portal_runner.DECLARED_TOTAL_KEY = 'sreality_result_size' (the historical by_category key), so scripts/checks names no portal
- scraper/delist_policy.RECHECK_THROTTLE = timedelta(hours=24): the literal in db.presence_candidates, named and read there, behaviour-neutral. The delisting check imports it and GONE_DWELL; the Q3 (a) ruling lands as its own scraper PR.
- scripts/recompute_property_stats.py stamps derived_artifacts 'property_sweep' through stamp_derived_artifact
- migration 622

**Deletes.**
- Checks acquisition_lag, property_maintenance (both arms), walk_coverage with _SELF_CERTIFYING_TOTALS, walk_end_streak, delist_flip_refused, false_delist_share, and sreality_image_template with its probe seam (~240 lines), plus their thresholds and tests
- scripts/coverage_gate.py, .github/workflows/coverage_gate.yml and tests/test_coverage_gate.py (299 lines)
- ceskereality _record_slices and mmreality _record_slice (only coverage_gate read them) and their tests; idnes keeps its own slices through slice_staleness
- recompute_property_stats' app_settings property_sweep_last_complete write

**Migrations.**
- 622_property_sweep_artifact.sql — ADDITIVE. INSERT the derived_artifacts row property_sweep (producer scripts/recompute_property_stats.py, host github-actions, cadence '15 4 * * *', staleness_budget ~~56 h~~ 38 h: the As built note).

**Tests.**
- Per-arm fixtures and replay rails: 14-28 d quiet windows give 0 onsets. Incident-shaped fixtures must fire:
- (a) a starved NEW queue fails publishing at 6 h;
- (b) six portals raising: per-portal cells in one grouped notice;
- (c) a unit with no end-reached walk for 6G fails scraping;
- (g) 52 % unsaved photos fail, and CDN-refused transforms stored as source_unavailable count as not stored;
- (h) a 30 % revived 48-72 h cohort fails;
- the 10-10 zero-flip day fails delisting;
- the 09-14 Browse drop and the 09-17 bazos location loss fail the publishing stock arm.
The bazos 10-13 burst (about 1,110 ads waiting at once, the oldest 119 min) must stay green.
- coverage: total_is_count entries read unknown; NULL-category ads count as cannot-be-nominated with no portal branch; deferrals are subtracted.
- Rule-21 rail: no portal name appears as a literal anywhere in scripts/checks/.
- db.presence_candidates reads RECHECK_THROTTLE and walk-path outcomes are unchanged.

**Docs in the same PR.**
- docs/architecture.md: the rule 3 monitoring paragraph (the effective dwell is max(GONE_DWELL, RECHECK_THROTTLE), stated as today's behaviour; the alarm's limits derive from it) and the coverage note in Data sources
- references/coverage-and-delisting.md and pipeline-verification.md: the scraping, publishing, delisting and coverage sections
- scraper-ops SKILL.md in place: :162 (coverage_gate removed), :266 ('Health false_delist_share' becomes the delisting check)
- .claude/skills/database/SKILL.md: the derived_artifacts list gains property_sweep (in place)
- roadmap/scraper-track.md: coverage_gate and the slice writes removed; the chaty-chalupy, NULL-category, poison-id, bazos rate-ledger and dwell-ruling issues linked

**Risks.**
- Hot-table reads stay index-bound: the stock arm measured 1.47 s, the hourly coverage count ~3 s.
- Removing the slice writes touches two portal mains; the seam touches two adapters.
- The 28-day baselines exist in listings.

### Wave 5 · Dedup check

**Goal.** The engine's state (off, building, merging, idle), its progress, its near-real-time decisions and your no-merge rulings are judged from durable stores, reading only the live engine's output. The only dedup status page stops saying 'shadow'.

**PR.** Draft PR on feature/health-dedup-check: scripts/checks/dedup.py, the removal of the heartbeat latency fields, and the corrected AutodedupProgress copy. About +150 net lines for the owed heartbeat check (CUTOFF C7, PROGRAM W7), paid at sprint level by W3's deletion of toolkit/ops_incidents.py.

**Depends on.** W3

**Operator gate.** Q3 (c), the rebuild pause, and Q4, the AutodedupProgress copy; both answered upfront.

**Adds.**
- The dedup check. It imports RT_SCOPE_BACKLOG_SQL (autodedup/incremental_sql.py), the live-stream predicate (autodedup/ui_sql.py) and the lane's new-feed predicate, and reads property_merge_events only for source 'autodedup' and 'operator'.
- One AutodedupProgress sentence (Q4): 'The engine merges in production inside the apply scope; live lane health is on Health.'

**Deletes.**
- broker_merge_suppression (folded into the rulings arm)
- The heartbeat's latency_p50_s / latency_p95_s and their log fields
- AutodedupProgress's MODE='SHADOW', its chip, the 'four small blocks … merges nothing' lede and the test pin

**Tests.**
- State precedence: off, building, merging, idle; off and building read ok with a state fact, never unknown.
- Build: Problem at 0 decisions in 3 h with backlog and the lane not braked; Watch when the ETA exceeds 7 d.
- Merge pause: Watch above 48 h, never Problem.
- Decision lag: Wilson LB.
- Lease: Watch within the TTL, Problem 10 min past expiry.
- Rulings: a 'different' pair merged by autodedup after the ruling fails; a rail asserts scripts/checks/dedup.py issues no query on source 'auto' (rule 15).
- Undo cohort.
- The AutodedupProgress test is updated.

**Docs in the same PR.**
- roadmap/autodedup.md:65,72: the owed heartbeat check is delivered
- .claude/skills/scraper-ops/SKILL.md: the heartbeat-keys line edited in place
- docs/design/realtime-scrapers.md:453
- pipeline-verification.md: a dedup section

**Risks.**
- Rule 15: only live AUTODEDUP stores and source='autodedup' merges are read; legacy groups stay the W8 retire tool's business, never a Health arm.
- Engine predicates change under the E96x stack; they are imported, never copied.
- The decision rate decays as the store grows, so the ETA is labelled a lower bound.

### Wave 6 · Parsing alarm and quality gauges

**Goal.** Parser regressions keep ringing as one per-portal Parsing alarm; standing data-quality conditions become gauges with their own 30-day trend and owner; rule 25 is judged over active ads. After this wave every check emits explicit cells and no legacy key remains.

**PR.** Draft PR on feature/health-quality. It contains:
- scripts/checks/quality.py: parsing (alarm), data_quality, location_town_coverage and text_extraction (gauges);
- per-portal payload-shape predicates exported from location_data's contract module;
- run_due's per-pass context;
- explicit cells for migration_drift and outbound_url_conformance, and the implicit-cell fallback deleted;
- floor_convention deleted.
Net about −700 lines (nine legacy keys and floor_convention against four checks).

**Depends on.** W3

**Operator gate.** Q2 (gauges and the location population) and Q4 (floor_convention), answered upfront.

**Adds.**
- The parsing alarm: fill and vocabulary, measures of new ads, median shift, payload shape and snapshot churn
- The data_quality gauge; location_town_coverage over active ads first written more than 24 h ago (the inactive stock becomes a fixed fact); the text_extraction gauge (flow plus ETA)
- location_data's contract module exports PAYLOAD_SHAPE_PREDICATES {portal: SQL readability predicate} beside the Python readers they mirror, so the parsing check iterates it and names no portal
- Gauge trends: each gauge cell carries a rolling 30-day series (Europe/Prague days) built from its previous result plus today's value, and worse_7d (status worse than 7 days ago)
- run_due's pass context: one memo per pass shares the ~12 s measure_plausibility read between parsing and data_quality, replacing the module-global _PLAUSIBILITY_CACHE
- migration_drift (a cell per unapplied file) and outbound_url_conformance (a cell per portal) emit explicit cells

**Deletes.**
- Legacy keys ppm2_median_shift, ppm2_basis_floor_share, ppm2_measure_coverage, area_vs_usable_divergence, field_fill_matrix, outbound_url_coverage, location_payload_shape_drift and text_extraction_lag
- floor_convention: the check, its 'idnes' literal and its tests
- SREALITY_SHAPE_CASE_SQL and _LOCATION_SHAPE_REMEDY in shared code
- location_town_coverage's inactive arm
- The field_census threshold constants and the REGISTRY_MAX_* module constants (moved into THRESHOLDS)
- _PLAUSIBILITY_CACHE
- The reconcile's implicit-cell fallback (no result without cells remains)
- The canary's 35-minute job and the harness step's 21-minute limit (wave 3 resized them from 40 and 27 for 20 checks), resized again once the legacy keys are gone (a job under 13 min also needs the poller step's 5-minute limit or the rail's 8-minute allowance cut)

**Tests.**
- Fold equivalence: each arm reproduces its old verdict on the same fixture; arms now judged on the Wilson LB are pinned separately.
- The median-shift arm reads unknown until 6 d of its own history exist.
- location: active ads only; the 24 h edge with publishing holds, and both count unservable ads (by age, no carve-out).
- text_extraction: flow p95 and ETA.
- Gauge trends roll from the previous result; worse_7d flips on a status change.
- Two passes rebuild the plausibility memo.
- The rule-21 rail stays green (payload predicates live in location_data); the rule-23 measures census stays green with the moved site.

**Docs in the same PR.**
- docs/architecture.md § Location data: the invariant line (a gauge over active ads)
- pipeline-verification.md: parsing and gauges sections
- toolkit/measures.py REGISTERED_SITES: path updated
- the location contract doc gains the exported predicates (in place)

**Risks.**
- A gauge sends one notice when it first reads Problem and one reminder a day while it stays non-OK, and its incidents stay out of the Open-incidents tile; the parsing and publishing alarms catch regressions.
- The median-shift arm is unknown for its first 6 days.
- parsing and data_quality start new histories, so their trends fill over 30 days.

### Wave 7 · Delivery: alarms reach a person

**Goal.** System alerts leave the app on the channel the operator chose, once every alarm is a new-model alarm, and the canary checks each one was delivered.

**PR.** Draft PR on feature/health-delivery: migration 623 (system_health_channels and one announced delivery-test alert), docs, and click-by-click steps for the Railway secret and the recipient. No code. With 'bell only' (Q1) the PR is docs alone.

**Depends on.** W4, W5, W6

**Operator gate.** Q1: add TELEGRAM_BOT_TOKEN (or RESEND_API_KEY and EMAIL_FROM) on the Railway API service and the recipient in Settings › Delivery (steps in the PR); confirm the delivery-test alert arrived before merge.

**Adds.**
- migration 623
- The canary's delivery assertion now passes on a sent channel_sends row (it already accepted seen_at)

**Deletes.**
- The 'bell only' statements in roadmap/reliability-track.md and the toolkit-api SKILL delivery lines (edited in place); the 2026-08-04 ruling in docs/design/media-integrity-architecture.md marked superseded

**Migrations.**
- 623_health_delivery.sql — ADDITIVE config change, operator-ruled (Q1). UPDATE app_settings SET value = Q1's channel list WHERE key = 'system_health_channels'; INSERT one system_health notice, 'Delivery test: Health alerts now reach you here' (dedupe sys:delivery_test:<date>). Applied only after the transport secret is set on the Railway API service and the recipient in Settings › Delivery, because the outbox starts at boot only when a transport is configured.

**Tests.**
- Migrations lane: a system_health dispatch is stamped with the configured channel.
- The outbox delivers a system_health row with its first-line subject and the /health link.
- The canary's assertion passes on a sent row and fails on an undelivered one past the grace.

**Docs in the same PR.**
- toolkit-api SKILL.md delivery lines (in place)
- roadmap/reliability-track.md
- docs/design/media-integrity-architecture.md: the bell-only ruling superseded, dated

**Risks.**
- A misconfigured transport leaves alerts undelivered; the canary fails and GitHub emails.
- Delivery starts after six waves; until then the canary's GitHub email carries unseen alarms.

### Wave 8 · The Health page

**Goal.** One screen, at 1,280 px and on the phone, shows what is wrong, where and why from one view, and links to the page that owns the fix.

**PR.** Draft PR on feature/health-page: the frontend rewrite on shared Panel, Stat and StatusDot; one fetcher; page tests; the 'Open Health' link on system alerts; the removal of the workflow-docs portal tags only the old page used. No migration: the old read objects stay until W9. Net about −2,100 lines.

**Depends on.** W7

**Operator gate.** Q4, answered upfront. After Railway's vite status reads success, a read-only check in a real browser against production.

**Adds.**
- pages/Health.tsx (~350), components/health/PortalMatrix.tsx (~180), components/health/CheckRow.tsx (~180)
- components/Panel.tsx, Stat.tsx and StatusDot.tsx (~100)
- lib/healthChecks.ts (~80): the scale, worst-first sort and the matrix pivot by cell.portal. Sections, labels, units, order and owner links come from the registry, so there is no per-key table and no threshold.
- fetchHealthChecks: one view every 5 min while visible and on focus; Acknowledge through POST /admin/health/acknowledge
- Health as the first Admin entry
- Notifications SystemNotificationRow: an 'Open Health' link beside the row (~6 lines; a sibling, never nested in the mark-seen button)
- pages/Health.test.tsx (~260) and lib/healthChecks.test.ts (~60)

**Deletes.**
- The old Health.tsx (2,384 lines) and every panel under Removed
- lib/pipelineChecks.ts (CHECK_LABELS, CHECK_VALUE_UNIT, the humanizer) and lib/derivedArtifacts.ts, with their tests
- 11 health fetchers (including the dead fetchRecentWorkflowFailures), ~25 health types and ten refetch copies
- WorkflowDoc.portal; generate_workflow_docs' _PORTAL_TAG, _PORTAL_LINE and _portal; the 14 '# portal:' workflow tags and their codegen test cases

**Tests.**
- Health.test.tsx 'Pins:' docblock:
- the strip counts;
- the matrix sorts worst first;
- unknown and stale never render as ok;
- the header renders from liveness cells;
- a failed refetch keeps its data; a failed first read shows ErrorBanner with Retry;
- an empty result is an error, not 'all clear';
- Acknowledge posts the admin route;
- below 768 px OK cells hide and 'Nothing open' shows when empty;
- anchors #key and #key:subject exist;
- polling at 5 min only while visible;
- expectNoNestedInteractive;
- no threshold constant in client code.
- healthChecks.test.ts: the sort, the pivot and the scale.
- Notifications.test.tsx: the Open Health link; reading a system alert never acknowledges it.
- tests/test_workflow_docs_codegen.py: updated.
- A Python rail: every registry owner_path is a route in frontend/src/lib/routes.ts.

**Docs in the same PR.**
- docs/architecture.md: the Health section (one view, polling, states)
- .claude/skills/database/SKILL.md:335: the Health read line
- .claude/skills/scraper-ops/SKILL.md:218-224: the '# portal:' paragraph removed; Settings › GitHub Actions named as the workflow catalogue

**Risks.**
- A visible change (Q4).
- Cached old bundles may hit stale chunks; the existing lazy-chunk handling covers this.
- The phone reflow is one CSS-responsive tree, pinned by a test.

### Wave 9 · Drop the old health layer

**Goal.** The second engine and its read path stop costing database time and stop existing.

**PR.** Draft PR on cleanup/health-legacy-drop. First the code, tests and rails that only served the dropped objects (images.yml's refresh step, scripts/refresh_image_stats.py, the by_category walk_coverage and active_db writes, the measures census entry); after it deploys, one destructive migration with a backup. Net about −1,200 lines and ~15 h of DB time a week.

**Depends on.** W8

**Operator gate.** Q5 OK, a successful backup_before_drop.yml receipt, the confirmed W8 deploy, and 48 h with no reads of the old objects.

**Adds.**

**Deletes.**
- 8 matviews: health_summary_mv, portal_health_mv, scraper_health_checks_mv, category_trends_mv, snapshot_churn_24h_mv, health_mv_refresh_stamp, image_storage_overview_mv, images_failure_overview_mv
- pg_cron jobs refresh-health-dashboard and capture-data-quality
- 11 functions: health_summary, portal_health_summary, scraper_health_checks, category_trends, image_storage_overview, images_failure_overview, recent_scrape_runs, workflow_failure_summary, recent_workflow_failures, refresh_health_matviews, emit_verification_stale_alert
- 12 views: derived_artifacts_public (readable by any signed-in user), worker_liveness, detail_latency_recent, listing_presence_checks_public, scrape_runs_public, listing_detail_queue_public, listing_fetch_failures_public, parsed_url_activity, portals_public, portal_listing_counts (ungated), data_quality_by_source, pipeline_check_history_public (no reader)
- Tables pipeline_check_results_retired_2026_09_22, ops_incident_runs (603 rows), portal_coverage_gate (153 rows) and dual_write_watermark (unread since W3)
- Columns ops_incidents.workflow_paths, origins, sample_run_url, sample_excerpt and failure_count, and portals.scrape_cadence_minutes
- 8 derived_artifacts rows (17 → 10 with property_sweep); app_settings rows pipeline_check_thresholds and broker_resolution_last_complete (both unread since W3; scripts/resolve_brokers.py stops writing the second in the same PR) and property_sweep_last_complete
- Code: scripts/refresh_image_stats.py and its images.yml step; the by_category walk_coverage and active_db writes in portal_runner; the scraper_health_checks_mv entry in toolkit/measures.py; tests/test_health_image_matviews_listing_id.py (175 lines); the rail entries for the dropped objects

**Migrations.**
- 624_drop_legacy_health_layer.sql — DESTRUCTIVE, operator OK via Q5.
- Gate: W8's vite deploy confirmed; 48 h with no SELECT or EXECUTE by authenticated or authenticator on the dropped functions and views (pg_stat_statements by userid; the pg_cron refresh's own reads run as postgres and do not count); a successful backup_before_drop.yml receipt (pg_dump to R2 of every dropped table and column, the matview definitions and data_quality_snapshots).
- Applied after W9's code merges and deploys, within 6 h (the image artifacts' budget), so no stale artifact cell fires.
- SET lock_timeout='5s'; unschedule the two jobs first; drop matviews, then wrapper views and functions in dependency order; drop the three tables and the columns; delete the 8 registry rows and the two settings rows.
- Repeat 621's resolve-by-predicate; rewrite COMMENT ON TABLE ops_incidents for cell incidents.
- data_quality_snapshots is kept frozen unless Q5 says drop.

**Tests.**
- Schema replay in the migrations lane.
- Rails updated: tenant isolation, RLS grants, matview refresh convention, matview indexes, the derived_artifacts registry at 10 rows, and the cron statement-timeout guard; the derived_artifacts_public text scan retires with the view.
- The rule-23 census passes without the matview entry.

**Docs in the same PR.**
- .claude/skills/database/SKILL.md: matview mentions removed
- docs/architecture.md: the health-matview blue-green doctrine removed
- scraper-ops SKILL.md in place: :308-309 (scrape_cadence_minutes liveness scaling), :316-317 (scraper_health_checks arms), :469 ('drift feeds the Health page')
- roadmap/reliability-track.md closed, and the ROADMAP.md status cell

**Risks.**
- An unseen reader of a wrapper view: pg_depend and a grep are re-run at apply time, and the R2 dump restores it.
- Irreversible without the backup receipt.
- Lock conflicts with the */10 refresh: unschedule first, lock_timeout 5 s, retry.

### Wave 10 · Shared admin primitives

**Goal.** Every admin page draws one status, panel, stat, skeleton and error banner the same way, with no visual change.

**PR.** Draft PR on cleanup/admin-shared-primitives. Costs, LocationQuality, LocationPinAudit, NewDedupDashboard, BrowseStats and AutodedupProgress move onto W8's primitives. Presentational only; net about −150 lines.

**Depends on.** W8

**Adds.**

**Deletes.**
- Local Card copies in Costs, LocationQuality, LocationPinAudit, NewDedupDashboard and BrowseStats
- Stat and Tile copies in Costs, LocationQuality and AutodedupProgress
- Local animate-pulse skeletons in Costs, LocationQuality and BrowseStats, in favour of components/Skeleton
- Error-banner copies in settings/SectionChrome, LocationQuality and Costs, in favour of components/ErrorBanner
- Per-file sage/ochre/brick status maps, in favour of StatusDot

**Tests.**
- The existing page tests pass unchanged.
- expectNoNestedInteractive on the touched pages.
- A Panel/Stat class-string pin.

**Docs in the same PR.**
- .claude/skills/interface-design/SKILL.md: one line naming the shared admin primitives, edited in place

**Risks.**
- LocationQuality's header-bar card is the one visual outlier; it is covered by Q4.


## 7. The subtraction ledger

**engines.** 4 judges → one registry measured on three hosts, and one teller.

Before:
- verify_pipeline on 4 GitHub lanes;
- scraper_health_checks_mv, 16 SQL checks × 9 portals on pg_cron;
- the browser: derived-artifact budgets, the 25 min and 36 h banners, Index-% colours and the Queue proxy;
- the ops_incidents signature engine, beside a second, re-derived check-incident model.

After:
- One Python registry, run by the worker's health lane and the GitHub canary.
- One SQL check, health_liveness on pg_cron: the watcher that must not run inside what it watches.
- One ladder, toolkit/system_alerts.reconcile, run by the API beside the outbox.
- The canary's four assertions: the escalation that by construction cannot go through the ladder.

**vocabularies.** 5 → 1.
- Before: ok/warn/fail; pass/warn/fail; ok/stale/never/paused/unknown; RollupStatus plus idle/loading; the page words Healthy/Watch/Problem/Not started and OK/Watch/Problem.
- After: ok/warn/fail/unknown, shown as OK / Watch / Problem / Not measured, with one class axis (alarm or gauge). Off, idle and building are states that read ok. 'Alarm' names a check class and 'system alert' the notice it sends.

**threshold stores.** 6 → 3 homes, and each threshold is spelled once.
- Before: DEFAULT_THRESHOLDS; the app_settings row; module constants (field_census, REGISTRY_MAX_*, wedge hours, '>= 20 calls', the 1/2 ratio); SQL literals in the matview; SPA constants; AlertPolicy and ops_incidents fallbacks; portals.scrape_cadence_minutes. The ladder was spelled 3 times.
- After: (1) THRESHOLDS in scripts/verify_pipeline.py, every check limit beside its measured basis, including CAPACITY_REFERENCE_FROM and the 18 h GitHub slack (the app_settings row and load_thresholds' merge are deleted: every override already equals its code default); (2) the watcher's structural constants inside health_liveness(); (3) staleness_budget on each derived_artifacts row. The ladder's rungs and hold are spelled once, in toolkit/system_alerts; the browser holds none.

**workflows.** 6 monitoring workflows plus images.yml's matview-refresh step → 1.
- Removed: llm_health, the 6-hourly verify_pipeline lane, sreality_image_canary, verify_outbound_urls, monitor_workflow_failures and coverage_gate.
- They become verify_pipeline.yml: the hourly canary, with the poller as its first step.
- Runner time drops from about 60 to about 15 minutes a day once W6 removes the last legacy keys.

**matviews.** Matviews 8 → 0 (6 health, 2 image). pg_cron jobs 7 → 5: refresh-health-dashboard dropped, capture-data-quality unscheduled, emit-verification-stale-alert replaced by health-liveness.

Health DB time: about 20 h/week → about 5 h/week (estimated).
- Before: refresh-health-dashboard 18.1 h/7 d (the #1 pg_cron cost, with 4 timeouts), data-quality capture 0.8 h, GitHub lanes about 1 h.
- After: worker alarms ~7 s per 10 min (≈ 1.9 h/week, the 1.47 s stock arm included), gauges ~25 min/day, liveness and the API teller ~5 min/day.
- Each result's elapsed_ms measures the real figure from W3.

**checks.** Before:
- 30 stored keys (28 registered, 1 weekly external, 1 frozen);
- 16 SQL checks × 9 portals (144 verdicts);
- 17 artifacts judged in the browser;
- 2 banners;
- 4 open signature incidents.

After: 15 registered keys.
- 11 alarms: scraping, parsing, publishing, delisting, dedup, jobs, capacity, llm, migration_drift, outbound_url_conformance, liveness.
- 4 gauges: coverage, location_town_coverage, data_quality, text_extraction.
- Their ~250 cells (portal, unit, field, job, lane, provider, file) are the incident subjects.
- 26 of the 28 registered keys deleted or folded, the frozen outbound_url_parity gone, 3 kept by name (location_town_coverage, migration_drift, outbound_url_conformance).

**page lines.** About 3,355 → about 1,250.
- Before: Health.tsx 2,384, pipelineChecks.ts 96, derivedArtifacts.ts 210, their tests 260, ~230 type lines and ~175 fetcher lines.
- After: Health.tsx ~350, PortalMatrix ~180, CheckRow ~180, Panel/Stat/StatusDot ~100, lib/healthChecks.ts ~80, one fetcher ~30, tests ~330.

**requests per minute.** About 17 → about 0.2.
- Before: 10 query sites polling every 60 s, 9 of them per-portal RPCs, about 2.4 MB a minute.
- After: one view every 5 min while the tab is visible and on focus (~1 MB of JSON, ~100-150 kB compressed, gauge trends included). The unread badge keeps Shell's existing poll.

**net lines estimate.** About −6,300 ± 1,500 lines.
**Measured so far** (`git diff --numstat`; code / migrations / tests / docs): wave 1 with its follow-ups +172 / +201 / +1,999 / +1,446; wave 2 +77 / +311 / +933 / +205; wave 3 -389 / +371 / +2,354 / +413; wave 4 +22 / +66 / +15,145 / +351 (their As built notes have the detail). The estimate is the end state and holds only if waves 3–9 delete what their rows list; the tests sub-budget is already exceeded (about −2,000 planned, +2,932 after two waves: per-check rails, the replay and the live tests), and the docs one too (this document, the As built notes and the skill reference).
- Python ≈ −2,100: ops_incidents −396; system_alerts −426 + reconcile and loop +280; failure_signature −200; record_workflow_failures −120 + workflow facts +60; coverage_gate −311; refresh_image_stats −55; verify_outbound_urls −113; verify_pipeline 4,100 → ~700 plus scripts/checks ~2,600 (≈ −800); worker lane and heartbeat +100; acknowledge route +30; slice writers −135; portal_runner/worker incident bits −60; seams and moved predicates +25.
- Tests ≈ −2,000: test_ops_incidents −369, test_coverage_gate −299, test_health_image_matviews −175, test_failure_signature −150, test_system_alerts −177, test_verify_pipeline −1,450 (rewritten per check), outbound −80, slice tests −250, new lane/run_due/canary/liveness/API/replay tests about +900.
- SPA ≈ −2,300: page −2,105, workflow-doc tags −64, sibling copies −150, AutodedupProgress −27, the Notifications link +6.
- Workflows ≈ −265.
- Docs ≈ −300: pipeline-verification.md −300, scraper-ops SKILL −35, architecture −20, GLOSSARY and reliability track +55.
- Migrations 619-624: ≈ +650.

**New objects (each names what it replaces).**
- public.health_checks (619): key, domain (the page section), class, host (derived_artifacts' spellings realtime-worker | github-actions | pg_cron), cadence_s, staleness_budget, label, unit, owner_path, sort, declared_at. Each host projects only its own rows. It replaces the SPA's CHECK_LABELS/CHECK_VALUE_UNIT/humanizer, hand-set cadences and migration-551-style retirements.
- public.health_stale(stamp, budget) (619): the one staleness predicate, read by pipeline_checks_public, health_liveness and the jobs artifact cells. It replaces the 25-min banner, the browser's budget parser, the 24 h dead-man and two separate cadence rules.
- pipeline_check_results status 'unknown', via the widened CHECK; it replaces 'warn' as the code for unmeasured (619).
- ops_incidents.ok_since and acknowledged_at: the recovery hold and the deliberate acknowledgement (619). The signature-only columns go in 624.
- notification_dispatches: a SYSTEM-account arm on the update policy for platform admins, with UPDATE narrowed to (seen_at, estimation_run_id) (619).
- Appended pipeline_checks_public columns: label, unit, domain, class, host, cadence_s, owner_path, sort, last_measured_at, effective_status, incidents (open and closed within 14 d) (619).
- toolkit/system_alerts.reconcile, the one ladder, and the API's 60 s system-alerts loop (W1). They replace emit_transition_alerts' 30-day re-derivation, the GitHub lanes' alert calls and, from W3, the ops_incidents signature engine.
- POST /admin/health/acknowledge (W1), replacing 'acknowledging is impossible'.
- public.health_liveness() and the pg_cron job health-liveness (*/5) (620). They replace emit_verification_stale_alert and worker_liveness.
- verify_pipeline.run_due with per-check deadlines, and verify_pipeline.yml rewritten as the hourly canary with the poller step (W2-W3). They replace llm_health.yml, the 6-hourly full lane, sreality_image_canary.yml, verify_outbound_urls.yml and monitor_workflow_failures.yml.
- The worker health lane: a constant 60 s tick with _HEALTH_PASS_LOCK (W3).
- Heartbeat lane fields interval_s, failing_since, last_error and portal_errors, seeded at boot (W3). They replace last_failure_at and _record_lane_failure's incident writes.
- workflow_failures.reason and workflow_run_health.state, on_main, recent_starts (621). They replace the signature incidents' sample_excerpt and the 14-day staleness window.
- The derived_artifacts row property_sweep (622), replacing app_settings.property_sweep_last_complete and property_maintenance's sweep arm.
- Portal.total_is_count with by_category total_is_count, portal_runner.DECLARED_TOTAL_KEY and delist_policy.RECHECK_THROTTLE (W4). They replace _SELF_CERTIFYING_TOTALS, a portal-named key literal in checks and a SQL literal.
- location_data's PAYLOAD_SHAPE_PREDICATES (W6), replacing SREALITY_SHAPE_CASE_SQL and _LOCATION_SHAPE_REMEDY in shared code.
- Check keys scraping, parsing, publishing, delisting, coverage, dedup, jobs, capacity, llm, liveness, data_quality and text_extraction; migration_drift, outbound_url_conformance and location_town_coverage are kept.
- scripts/checks/clock.py (W2): the per-check wall clock (LaneBudget, fetchone, fetchall), moved out of verify_pipeline so a check module imports it, never the harness; scripts/checks/{scraping,publishing,delisting,dedup,platform,quality}.py; failure_signature.reason_line() (about 50 of the module's 251 lines); scripts/migration_objects.py's shared probe SQL.
- system_health_channels set to the operator's channel, with one announced delivery-test alert (623).
- SPA: components/health/{PortalMatrix,CheckRow}.tsx, components/{Panel,Stat,StatusDot}.tsx, lib/healthChecks.ts, pages/Health.test.tsx, lib/healthChecks.test.ts and SystemNotificationRow's 'Open Health' link. They replace 6 Card copies, 4 Stat copies, 4 status maps, 11 fetchers and ~25 types.

**Deleted objects.**
- Matviews health_summary_mv, portal_health_mv, scraper_health_checks_mv, category_trends_mv, snapshot_churn_24h_mv, health_mv_refresh_stamp, image_storage_overview_mv, images_failure_overview_mv
- pg_cron jobs refresh-health-dashboard and capture-data-quality (emit-verification-stale-alert is replaced by health-liveness)
- Functions health_summary, portal_health_summary, scraper_health_checks, category_trends, image_storage_overview, images_failure_overview, recent_scrape_runs, workflow_failure_summary, recent_workflow_failures, refresh_health_matviews, emit_verification_stale_alert
- Views derived_artifacts_public, worker_liveness, detail_latency_recent, listing_presence_checks_public, scrape_runs_public, listing_detail_queue_public, listing_fetch_failures_public, parsed_url_activity, portals_public, portal_listing_counts, data_quality_by_source, pipeline_check_history_public
- Tables pipeline_check_results_retired_2026_09_22, ops_incident_runs and portal_coverage_gate; data_quality_snapshots is kept frozen unless Q5 says drop
- Columns ops_incidents.workflow_paths, origins, sample_run_url, sample_excerpt and failure_count, and portals.scrape_cadence_minutes
- 8 derived_artifacts rows; app_settings rows pipeline_check_thresholds (with its keys verification_stale_hours, llm_spend_24h_warn_usd, llm_error_rate_warn and ops_incident_*) and property_sweep_last_complete
- Workflows llm_health.yml, sreality_image_canary.yml, verify_outbound_urls.yml, monitor_workflow_failures.yml (folded into the canary) and coverage_gate.yml, plus the refresh_image_stats step in images.yml
- Python: toolkit/ops_incidents.py, scripts/coverage_gate.py, scripts/refresh_image_stats.py, the signature tiers of failure_signature.py, the system_alerts re-derivation and weekly heartbeat, record_incidents, portal_runner.record_failure_signature, realtime_worker._record_lane_failure, the ceskereality/mmreality slice writers, the by_category walk_coverage and active_db fields, the heartbeat latency_p50_s/p95_s and last_failure_at fields, load_thresholds' DB merge, SREALITY_SHAPE_CASE_SQL, _LOCATION_SHAPE_REMEDY, _SELF_CERTIFYING_TOTALS and _PLAUSIBILITY_CACHE
- In verify_pipeline: write_results, _WEEKLY_CHECKS, --weekly, --only, --exit-nonzero-on-fail, --arm-dual-write-parity and full_lane_budget_s (`_LANE`/`_LaneBudget` stay as the per-check clock, in `scripts/checks/clock.py` since wave 2, and `_JOB_HEADROOM_S` as the harness step's headroom — wave 2 As built)
- Checks worker_liveness, worker_lane_stall, db_saturation, long_open_transaction, dual_write_parity, property_maintenance, broker_resolution_freshness, broker_merge_suppression, workflow_poller_liveness, acquisition_lag, walk_coverage, walk_end_streak, delist_flip_refused, false_delist_share, sreality_image_template, ppm2_median_shift, ppm2_basis_floor_share, ppm2_measure_coverage, area_vs_usable_divergence, field_fill_matrix, outbound_url_coverage, location_payload_shape_drift, floor_convention and text_extraction_lag; the frozen outbound_url_parity; the 16 SQL per-portal checks (snapshot_churn's measure survives as the parsing churn arm)
- SPA: the old Health.tsx, pipelineChecks.ts and derivedArtifacts.ts with their tests, ~25 health types, 11 fetchers, WorkflowDoc.portal with the generator code and 14 '# portal:' tags, AutodedupProgress's SHADOW constant/chip/lede/test pin, and the sibling Card, Stat, skeleton and error-banner copies
- Tests: test_ops_incidents.py, test_coverage_gate.py, test_health_image_matviews_listing_id.py and test_verify_outbound_urls.py; most of test_verify_pipeline.py (3,348 lines), test_system_alerts.py (577) and test_failure_signature.py rewritten


## 8. Incident replay — would the new design have caught it, where, how fast?

| Incident | Surfaces as | Time to surface |
|---|---|---|
| (a) sreality ingest starvation, 2026-08-17 → 08-27: new ads sat unclaimed in the detail queue for 9.3 days until a human noticed | publishing · sreality, ingest arm. The oldest never-fetched NEW queue row passes 3 h (Watch), then 6 h (Problem); within a minute the teller sends one grouped onset: 'sreality: 640 new ads waiting to be fetched, oldest 6.1 h; limit 6 h; normal worst 2 h'. It is delivered by Telegram or email after W7, otherwise by the canary's GitHub email. Scraping's inflow arm stays green, because queued ads count as discovered, so the drain stall is told once. | About 6 h 10 min to the notice (was 9.3 days). |
| (b) Six-portal CheckViolation outage, 2026-08-24 22:09 → 08-26 22:07 (migration 438 merged, not applied) | jobs · lane/drain × each raising portal: portal_errors.failing_since passes 15 min, so each portal's cell turns Problem with last_error 'CheckViolation … listings_area_basis_check', all in one jobs notice; scraping's fetch-error arms cross Problem within the hour. On the August GitHub drains, the gh cells fail at the 3rd production red with the CHECK reason line. migration_drift still cannot see a same-name redefinition (filed). | About 25-30 min on the worker drain; 2-6 h via the GitHub runs (was about 48 h). |
| (c) ceskereality byt/prodej walks never reached the portal's end, 2026-09-16 → 10-13 (27 days) | scraping · ceskereality, walks arm for unit byt/prodej: Watch past 3G (about 22 h), Problem past 6G (about 44 h): 'byt/prodej has not reached the portal's end for 44 h; its gone ads cannot be nominated'. The coverage gauge shows byt/prodej at a +37 % residual (13,373 held against 9,771 declared). | About 22 h to amber and about 44 h to the notice (was 27 days). |
| (d) MF rent job dead: recompute_mf_yields.yml red 83 times from 2026-09-13 04:58, found about 11 days later | jobs · gh/recompute_mf_yields.yml: the 3rd consecutive production red opens its own incident, with the reason line 'QueryCanceled: canceling statement due to statement timeout', instead of being absorbed by a catch-all signature incident. | About 10-15 h: the 3rd red was around 15:00, plus the canary's poller gap (was 11 days). |
| (e) Autodedup bootstrap statement-timeout stall, 2026-10-02 ~11:00 → 10-03 13:43 | jobs · lane/autodedup: failing_since passes 15 min (3 × the 60 s interval, floor 15 min), so the cell turns Problem quoting last_error 'QueryCanceled'. The dedup row reads 'building', and its progress arm fails after 3 h with 0 decisions: '0 decisions in 3 h, 64,432 ads undecided'. | About 25-30 min (was found the same day, only by the session running the build). |
| (f) OpenAI credit outage, 2026-10-02 07:53 → 10-12 (detected in 1.5 h, seen after 8 days) | llm · openai/credit: the newest credit error is newer than openai's own newest success, kept red by the 72 h lookback after callers back off. One notice on the next pass, then reminders at 6 h, 24 h and 72 h until it is acknowledged on Health. The description-reading gauge shows the stalled flow; once it reads Problem it sends one notice of its own, then one a day until acknowledged. | Detected in 10 min or less. Delivered at once with Q1's channel, otherwise at the next canary run (median about 4.8 h). It was seen after 8 days. |
| (g) iDNES image throttle: 429s from 2026-10-01, a 403 wall from 10-05 12:38, found 10-06 ~17:30 | publishing · idnes, photos arm: the not-stored share of new ads' photos (Wilson LB; source_unavailable counts as not stored) passes 2 % (Watch) and then 10 % (Problem): 'idnes stored 48 % of new ads' photos in 24 h (normal 99.9 %); top reason HTTP 403'. | Problem within about 2 h of the 10-05 403 wall, as the 1-24 h cohort fills. The 429 phase from 10-01 was not re-measured. It took 5.7 days to find. |
| (h) sreality 'blink' false delists after presence-verified delisting went live on 2026-09-07; found 10-09 | delisting · sreality, precision arm. Of the ads made inactive 48-72 h earlier, 26-49 % were active again (LB above 15 %), which reads Problem as soon as the first cohort matured. After the 10-10 dwell it rests at about 7 % (LB about 6 %), under the 10 % Watch line, visible as the cell's value. | About 3 days after 09-07 (was about 32 days). |
| (i) Supabase CPU saturation, 2026-08-17 → 10-10, while db_saturation stayed green on all 61 runs | capacity · cron/browse-list-rebuild: daily p50 151-277 s against a lowest weekly p50 of about 9.9 s since the blessed reference, so Watch at 3× (about 08-18) and Problem once the 24 h p50 holds above 4× (about 08-19): 'browse-list-rebuild takes 3.4 min, 15× its reference'. The sticky reference keeps it red until 10-10; a trailing 28-day baseline would have read 1.2× by 09-15 and sent a false 'Recovered'. The check-timeout share adds Watch from about 09-10, and the 09-26 timeouts also fail jobs · cron/browse-list-rebuild. | About 1 day to amber and about 2 days to the notice, held for the whole incident (was never flagged). |
| (j) Autodedup rt_lease stranded by the 2026-10-10 05:57 DB restart (39 min, self-healing) | dedup · lease shows Watch: 'held by a previous process since 05:57, expires 06:35; passes wait; this heals itself'. capacity names the DB restart. It becomes Problem, with a notice, only if the lease outlives its expiry by 10 min. | Within 10 min on the page; deliberately no notice. |
| (k) July admin-gate silent zero: the health matviews refreshed on time but read all zeros for about 7 h (2026-07-20) | The class is gone: no health matview remains; checks read base tables as service roles; the page reads one admin-gated view, and an empty or denied result renders as an error, never as zeros or 'all clear'. A check that scores nothing reads Not measured, and liveness rings when an alarm stays unmeasured past its budget. | Not applicable, because it is structurally removed. A gate fault is visible on the next poll (5 min or less). |
| Realtime worker freeze, 2026-09-29/30 (7 h 40 min): alerted at 40 min only by luck of the GitHub schedule, and unseen until morning | liveness · worker, judged by pg_cron outside the worker: a heartbeat older than 10 min fails the cell, and the API teller opens the incident and sends one notice: 'the realtime worker has not reported for 11 min: drains, publishing, photos, dedup and every worker check are paused'. Worker checks fold into that cell with no further notices; reminders keep going because the teller is not in the worker. The canary emails if the notice is neither delivered nor seen. Recovery is told once beats resume and hold 30 min. | About 12-18 min, and delivered (was 40 min, into an unseen bell). |
| (added) 2026-09-14 location regression: a full-resolve sweep re-judged 595,816 ads with no admissible claim, and Browse fell from about 350k rows to 45,810 | publishing · fleet and every portal, stock arm: after the next */15 browse_list rebuild about 87 % of active properties are missing from Browse, blocked at 'location not served'. Problem on the next 10-min pass, in one grouped notice. | About 25-30 min. Not in the 12-incident replay; its detection time then was not recorded. |
| (added) 2026-09-17 W18-b: 29,545 bazos ads lost their location after a contract bump | publishing · bazos, stock and visible arms ('location not served'): Problem on the first pass after the next rebuild. The location gauge shows the bazos jump a day later and, as a gauge, sends one notice of its own, then one a day until acknowledged. | About 25-30 min (estimated; the per-portal share was not re-measured). |

## 9. Alternatives considered and rejected

- **Host every check in pg_cron as PL/pgSQL (one in-database engine)** — It would rewrite about 2,000 lines of tested Python checks and re-spell lane predicates that must be imported (RT_SCOPE_BACKLOG_SQL, the text lane's eligibility, the delisting constants). It would also make the database judge itself. pg_cron keeps only liveness, the one check that must not run inside the runtimes it watches.
- **Keep GitHub cron as the check host and tune the schedules** — GitHub delivers about 20 % of the declared cadence: 4.9 runs a day for an hourly cron, p50 gap 4.6-4.9 h, max 13.3 h. A 5-min liveness limit sampled every ~5 h catches an outage of length D with probability of only about D/4.9 h.
- **Host the checks in the API process, like the watchdog matcher** — Detection would share one uvicorn process with user traffic, so one crash would silence detection, telling and delivery together. Only the ladder (telling) runs in the API: telling already depends on it, because the outbox delivers from there and the bell is an API route. Detection stays in the worker, pg_cron and GitHub.
- **Run the ladder in SQL (a health_reconcile function) from pg_cron (honesty review, option a)** — Every ladder change would become a full function body in a new append-only migration, a starved pg_cron (browse-map-rebuild starved on 09-14) would silence telling for every worker-detected incident, and the binding spine names toolkit/system_alerts as the ladder's home. One Python ladder run by the API resolves the same two-opener defect.
- **Let health_liveness open its own incidents under a written contract with the reconcile (feasibility review)** — It keeps two incident writers in two languages, and a liveness incident would get no reminders while the worker is dead, which is exactly when they matter. Liveness writes a result; the API teller turns it into an incident.
- **Liveness writes results only and the canary escalates a dead worker (honesty review, option b)** — A dead worker would reach a person in about 5 h at the median. With the teller in the API, the 09-29/30 freeze is told in about 15 min.
- **Rebuild scraper_health_checks_mv with corrected thresholds (realtime-scrapers W5b as written)** — It keeps two engines and two vocabularies, the SQL-literal thresholds, 18 h a week of refresh, and the gated-view coupling behind July's silent zeros. The base tables answer at check time: photos 150 ms, delisting 121 ms (outflow) + 0.31 s (precision), dedup backlog 48 ms, the Browse stock 1.47 s.
- **Keep the ops_incidents signature engine beside check incidents** — Its signatures keyed on runner boilerplate: 0 CHECK signatures ever, 645 failures folded into 3 never-closing incidents, and 37 of 53 alerted incidents were PR CI. It swallowed the MF outage. A per-job jobs cell with a stored reason line judges the same failures once.
- **One incident per check, with a 'spread' notice per newly failing cell** — A chronic cell would hold the check's incident open and absorb new, unrelated failures as mere spread notices, the MF-outage failure mode. Per-cell incidents grouped into one notice per check per pass still ring once for a fleet-wide cause.
- **Carry incident state on result rows and drop ops_incidents** — Every writer, pg_cron included, would have to copy the state forward. ops_incidents already has the shape and a unique index on the open signature that guarantees one open incident per subject.
- **Keep re-deriving incidents from 30 days of results** — At worker cadence it reads about 50k rows a pass, its anchors drift as rows age out, and treating unmeasured runs as green produced 54 false 'Recovered' alerts.
- **Open an incident only after 2 consecutive measured fails** — Persistence belongs in each threshold (durations, trailing 24-h windows, Wilson bounds), and the 14-28-day replay rails prove it. A confirmation rule adds a special case and 10 minutes of latency.
- **Acknowledge = any notice of the incident was seen (the draft's notice_ids column)** — 'Mark all read' is account-blind and defaults to every source, so after the RLS fix one click on the bell would silently stop every reminder and satisfy the canary. Acknowledgement is a deliberate act on Health (ops_incidents.acknowledged_at); reading only marks a notice seen.
- **Exclude system alerts from 'Mark all read' (regression review's fix)** — A special case in the bell. Separating 'read' from 'acknowledged' removes the need.
- **A bulk-acknowledge data migration for the 861 old alerts** — Once the RLS arm exists, the operator clears them with the existing 'Mark all read'. Those rows carry empty target_channels, so configuring a channel cannot resend them.
- **Make a working external channel the gate for deleting llm_health.yml in wave 2** — The canary keeps GitHub's email for every unseen alarm, a superset of llm_health's seven keys, sent once per notice instead of on every red run. The channel gate sits on the delivery wave, where the operator confirms a test alert.
- **Turn external delivery on in wave 2 (the draft's migration 620)** — emit_system_alert stamps every system_health insert with the channels, so the retiring signature engine (37 of 53 alerts PR CI) and flapping legacy checks would page the operator first. Delivery starts in W7, after the last legacy alarm is replaced.
- **Move the legacy checks into the worker in wave 2 (the draft plan)** — Eight raw-cursor conversions for checks deleted one to four waves later, and 6-60× more sampling of checks known to flap (broker_resolution_freshness: 33 of 34 onsets were edge flaps). Legacy keys stay on the canary at today's cadences until their replacement lands; new checks are born at their final host.
- **Register every check as a derived_artifacts row, one freshness registry (honesty review)** — The ladder, liveness and page need class, cadence, label, unit and owner per check, columns no artifact has; widening the artifact registry would give every artifact row four meaningless fields. Both registries instead share one host vocabulary and one staleness predicate, public.health_stale.
- **No registry table: keys age to unknown and hide after a week** — Retired keys would sit on the page as 'not measured' for a week after every retirement wave, never-measured keys would be invisible, and the ladder and pg_cron could not read class and cadence.
- **One projector for all registry rows (only the worker, or only the canary), seeded in a CI-pinned migration** — Deploy skew between the canary's checkout and the worker's image would let one host retire the other's live keys, and a CI test comparing an append-only seed to the living registry breaks on the first change. Each host projects and deletes only its own rows.
- **Give the health lane an app_settings interval (absent = 60 s, 0 = brake)** — A brake on the monitor is a hidden flag, and the brief says NO FLAGS. text_extract's constant interval is the precedent; a stopped lane is itself told by liveness.
- **Detect self-certified portal totals from data (declared = collected on every walk for 14 d)** — Measured this session: 56 of 110 units qualify, including 15 of 20 sreality units and 9 of 10 bezrealitky units whose totals are real. The adapter declares it through the Portal.total_is_count seam.
- **Judge pg_cron durations against the job's previous-28-day p50** — Measured: browse-list-rebuild's trailing baseline rose from 25.7 s (08-18) to 242 s (10-09) while its daily p50 stayed 151-277 s, so the saturation would have read 'recovered' by early September. The reference is the lowest weekly p50 since a blessed date.
- **Judge inflow on 6-hour same-weekday windows** — Crawler discovery per 6 h is walk-timing noise: a 7-day backtest was in Problem 15.5 % of hours with 14 onsets and no incident. The trailing 24 h against the same window 7/14/21/28 d earlier had 0 Problem evaluations in 14 d.
- **Daily median + MAD per day type as the flow alarm** — It judges complete days only, so it lags 24 h or more; the daily points stay in the check's details.
- **Count queued ads as 'not visible' after 30 min or 1 h** — Bazos politeness bursts queue up to ~1,110 ads (10-13) for more than 30 min, which would open 1-2 false incidents in 6.5 days. Queued ads belong to the ingest arm.
- **Carve resolved-but-unservable ads out of publishing into the location gauge (the draft)** — A resolver regression that makes ads unservable would reach you only through the location gauge: hours later, and with no onset at all on a portal whose cell is already a standing Problem (red until zero), where it only deepens an open, probably acknowledged incident. The 09-14 sweep took Browse from ~350k to 45,810 rows. The visible and stock arms count every unservable ad under 'location not served'.
- **Turn every data-quality check into a gauge (the draft's Q2)** — The regression detectors (field fill, measures of new ads, median shift, payload shape, snapshot churn) fired about once per real defect; as gauges a new parser regression would wait a day for its first reminder instead of 6 h, and stay out of the Open-incidents tile, which counts alarm incidents only. They become the Parsing alarm; only standing conditions become gauges.
- **Delete snapshot_churn with the matview ('no reader')** — It is one of the 16 rendered cards and exists because the idnes A/B/A hash-thrash storm re-snapshotted tens of thousands of ads for weeks with no signal. It survives as the parsing churn arm (live 0.003-0.045, thresholds 0.5 / 1.5).
- **Exclude all terminal unavailable_reason photos from the photos denominator** — A CDN that refuses our transform chain (HTTP 400/401/415) is recorded as source_unavailable, so that class would read green. Only listing_taken_down is excluded; today that adds 1 photo in 30,137.
- **Judge GitHub workflows only while their newest run is within 14 d (the draft)** — Monthly and quarterly production jobs would never be judged (location_registry_load's single red left ~91.6 % of katastr polygons missing for weeks), lone reds would vanish, and manual dispatches would open incidents. Each workflow is judged against its own start gaps, production runs only; a file gone from main has no cell.
- **Delete workflow_poller_liveness with no successor** — A dead or capped poller looks exactly like 'nothing failed', and it had 46 real fails. The jobs check keeps a poller cell on the cursor's age and reads every GitHub cell unknown while it is stale.
- **Exclude poison ids from the fetch-error share** — Under the two-runtime denominator one poison id is 1 of ~6,060 ids (0.02 %), far below the 2 % Watch line. Repeat offenders are named in the cell message.
- **Walk Problem at 24 h or 3 × G90** — It would ring on bazos byt/pronajem's 32.1 h and idnes komercni/pronajem's 26.7 h, both normal. Six median gaps matches the operator's 10-13 three/six-walk ruling, with 0 false fails in 14 d.
- **Put stuck nominations, including the NULL-category ads, in the delisting alarm** — It would create standing ambers (ceskereality ~97, realitymix ~151) on an alarm. Those ads are a coverage fact owned by a parser issue; a nomination-path failure still surfaces as an outflow collapse.
- **Delisting precision Watch at 5 %** — sreality's post-dwell steady ~7 % (LB ~6 %) would be a permanent amber. The alarm watches for a collapse; the cell shows the steady rate.
- **Change GONE_DWELL inside the Health sprint (the draft's W4)** — The re-check throttle and the flip dwell are two policies; coupling them would let a dwell change silently change re-check load, and it lands a scraper behaviour change in a Health PR. The check imports both constants; the Q3 (a) ruling lands as its own scraper PR.
- **A daily-points history view (the draft's migration 622)** — A folded key's headline has no meaning as a sparkline, and the per-cell series would need a heavier read. Gauges carry their own rolling 30-day per-cell trend, alarms show incidents open and closed, and the old view (no reader) is dropped.
- **A 'Red gauges' tile in the top strip** — It is non-zero by construction (rule 25's 44 ads, the unit-price masquerade, the NULL-category ads), so it recreates on the page the fatigue that acknowledging removes from the bell. The strip counts gauges whose status is worse than a week ago.
- **Poll the page every 60 s** — Alarms change every 10 min and liveness every 5; the hydration doctrine says never poll faster than the data can change. 5 min while visible, plus focus.
- **A separate phone list component, or a matrix scrolling inside its container** — Two render paths. One CSS-responsive tree stacks rows and hides OK cells below 768 px.
- **Keep an empty app_settings override row as a no-deploy tuning path** — After W1 every override equals its code default; an unread merge path is a second store. Thresholds change by PR, with replay rails.
- **A weekly health digest** — A second telling surface; the old one was never seen and misreported unmeasured checks as healthy. The canary's delivery assertion replaces 'proof of life'.
- **Harness rows written by every worker pass** — pg_cron judges each key's freshness from the results themselves, and each result's elapsed_ms measures the cost.
- **Keep the live sreality image probe, routed through the rate ledger** — The passive photo arms judge every portal from real downloads, CDN-refused URLs included, with no outbound request and no portal special case.
- **Delete AutodedupProgress to pay for the dedup wave (honesty review)** — Its iteration log is alive: 225 rows, the newest written on 2026-10-16. Only its false SHADOW copy goes.
- **Move the outbound URL probe into Portal seams in this sprint** — A scraper-side refactor outside the north star. The probe stays the one named exception to the rule-21 rail, and the refactor is filed.
- **Remove ceskereality's chaty-chalupy scopes in this sprint** — A scraper-config change that needs a seen-set cross-check. The coverage gauge names the double count, and the fix is filed.
- **New admin views for worker_heartbeats and ops_incidents, or an 'unacknowledged alerts' tile** — Lane facts reach the page as jobs cells and incidents through pipeline_checks_public, so no new read object is needed; the bell badge already counts unseen.
- **External monitoring SaaS (Sentry, Healthchecks.io, PagerDuty, Prometheus), or a Health badge in the nav or phone tab** — A second alert path, and the SaaS adds dependencies and secrets (rule 7). The outbox, Telegram or email, and GitHub's email cover every replayed incident.
- **Make migration_drift exact with an apply ledger in this sprint** — It needs a verified backfill of migrations 592-614 and a write from apply_migration.yml. It is filed; meanwhile the check names the files it cannot verify.

## 10. Assumptions the plan rests on

- Migration numbers: main (f8e76ac5) ends at 614, and 615-618 are claimed (#1846 holds 616; #1732 holds 617 and 618). This sprint uses 619-624 and renumbers at PR time if another lands first.
- The one-alert-spine rule is honoured: the ladder stays toolkit/system_alerts, the one escalation policy, and only its runner changes, from the GitHub lanes to an API loop beside the outbox that already delivers its notices. If the orchestrator reads the rule as also fixing the runner, the fallback is the feasibility review's contract (liveness opens only its own incidents), at the cost of no reminders while the worker is dead.
- The API stays one always-on uvicorn process (7 d: 0 crash restarts, boots equal deployments), and a ~100 ms reconcile every 60 s beside the matcher does not affect request latency. The API's service-role connection can write ops_incidents and notification_dispatches.
- The worker's DB connection has the same rights the GitHub lanes use today: cron.job_run_details, pg_stat_activity, the autodedup schema, llm_calls and the notification tables.
- GitHub emails the last modifier of the cron line on failed scheduled runs, as llm_health.yml relies on today. The canary is delivered like llm_health (~4.9 runs/day, max gap 13.3 h), so the 18 h slack covers it. GitHub keeps scheduled workflows enabled while the repo is active, and its workflows API reports a manually disabled workflow as state 'disabled_manually' (from GitHub's documentation, not verified here).
- The canary's 15 min grace covers the outbox's 120 s interval and its first retry; the retry backoff was not re-measured.
- listings holds more than 28 days of first_seen_at and inactive_at history behind listings_first_seen_source_idx and listings_inactive_at_idx. Revivals clear inactive_at 48-72 h after a flip, so baseline windows lose up to ~7 % of sreality's flips (≤ 5 % elsewhere) that the current window still holds, and the outflow ratio reads high by that margin.
- detail_queue_completions keeps 7 days, and every arm fits that window: fetch errors 24 h, the precision cohort 48-72 h, pooled cells 7 d or less.
- Visible in Browse means the property_id is in browse_list; servable means claims_common.SERVED_LOCATION_PREDICATE.
- property_merge_events is the production merge truth; only source 'autodedup' and 'operator' rows are read, and autodedup.applied_merges is not read.
- The API outbox delivers system_health rows to a configured transport with no change beyond the subject and deep link (W3). The 861 old rows carry empty target_channels, so configuring a channel sends no backlog.
- Railway redeploys the worker and the API on every merge (no watchPatterns; filed). State lives in the DB, but each merge also restarts the autodedup lane, so merges are timed around autodedup seed sittings.
- Thresholds and costs were measured on the 2XL instance, including this panel's live reads on 10-18: 20 of 362,883 active properties missing from browse_list in 1.47 s; 30,136 of 30,137 new-ad photos stored, 1 source_unavailable; 56 of 110 units self-certifying; the browse-list-rebuild baselines; the alert counts per key. Each result's elapsed_ms re-measures cost after W3.
- Capacity's relative arm uses a blessed reference date, 2026-10-17 (the first full day on the 2XL instance), and reads unknown until 7 days of history exist after it; re-blessing after a deliberate change is a dated PR.
- Lane failure thresholds (failing for max(3 × interval, 15 min); any failure of a lane with an interval of at least 1 h) are calibrated at W3 build time from the 7 days of Railway logs; the only stored episode is autodedup's 10-02 stall.
- Moving the payload-shape SQL predicates into location_data's contract module, beside the Python readers they mirror, is accepted by the location program (rule 25). If not, that arm stays a named exception to the rule-21 rail, like the URL probe.
- The replay fixtures committed to this public repo hold aggregates only (counts, ages and statuses per portal and time), with no listing ids or URLs.
- At worker cadence pipeline_check_results grows about 1,800 rows (~3-4 MB compressed) a day; the 35-day prune bounds it near 130 MB.
- parsing and data_quality start new histories, so their gauge trends and the median-shift arm fill over their first 6-30 days.
- The operator answers the six questions once, before W1. With 'bell only' the design still holds: the canary remains the external path, W7 shrinks to docs, and the page states the measured email gap.
- The incident-replay timings are estimates from stored history at 10-minute sampling. Incident (g)'s 429 phase, (h)'s pre-10-02 rate and the two added location incidents' per-portal shares were not re-measured.
- The WSL host runs one heavy process at a time, so tests run one file at a time locally and CI runs the suite.
- Out-of-scope defects are filed as GitHub issues, not fixed here: the poison sreality id 615387212, the bazos rate-ledger smallint overflow, the 422 NULL-category ads (parsers), ceskereality's chaty-chalupy double count, migration_drift's same-name blind spot (an exact apply ledger), the dark estimation and count_probe lanes, the supports_complete_walk flag, API root logging, Railway watchPatterns, the GONE_DWELL-vs-throttle ruling (Q3 a), Portal seams for the outbound URL probe, and user-facing request telemetry (shown as 'not measured here').

## 11. The six questions

**Rulings (2026-10-18).** The operator answered all six:
- **Q1:** bell only for now. The system is kept ready for both email and Telegram; GitHub's failure email (the canary's, from wave 2) stays the one external path, and wave 7 becomes readiness and docs, with no channel switched on.
- **Q2:** yes, and gauges keep ringing, "when first time read and then each day once": a gauge sends one notice when it first reads Problem and one reminder a day while it stays non-OK; acknowledging silences its reminders like an alarm's; the page's Open-incidents tile counts alarm incidents only.
- **Q3:** yes; for (a), 24 h becomes the one delisting rule, in its own scraper PR (issue #1858).
- **Q4:** yes to all.
- **Q5:** yes. The old health layer is dropped in wave 9, only after the new page has run 48 h with zero reads of the old objects and after a pg_dump to R2; check results keep 35 days, pruned daily; `data_quality_snapshots` stays as frozen history.
- **Q6:** finished, delete: `dual_write_parity` goes in wave 3, and the legacy-column drop is filed (issue #1859).

The questions are kept below as they were asked.

Answer in any form ("approve all recommendations" is a complete answer). Each has a recommended answer; the plan is built on them.

**Q1 — Where should an alarm reach you?** This replaces the 2026-08-04 "bell only" ruling. *Telegram* (a bot token on the Railway API service and your chat id in Settings › Delivery; click-by-click steps come with the delivery PR) or *email* (a Resend key and a verified sender). Either way: the bell stays the log, reading an alert there marks it read, and only Acknowledge on Health stops its reminders; after wave 1 you can clear the 861 old alerts yourself with "Mark all read". Delivery switches on in wave 7, once every alarm is a new-model alarm; until then GitHub emails you any alarm you have not seen at the next canary run (median 4.8 h). If you prefer, delivery can switch on after wave 3 instead, at the price of a few possible false pages from two legacy checks until waves 4–6 land. If you keep bell only, nothing reaches you on the phone, which has no bell. **Recommended: Telegram, from wave 7.**

**Q2 — May standing conditions become gauges** (red or amber on Health with a 30-day trend, never a notice)? They are: location coverage counted over ACTIVE ads only (44 today; the 13,192 inactive legacy ads whose pages are gone stay as a fixed number, which reverses the W15 ruling to count them), the per-m² unit-price masquerade, area-vs-usable divergence, ads with no portal link, portal coverage residuals and the 422 ads no walk can close, and the description-reading backlog. Rule 25's "red until zero" stays true on the page. The regression detectors keep ringing as one per-portal Parsing alarm (a field's fill dropping, new ads losing price or area, a jump in medians, an unreadable location payload, snapshot churn). **Recommended: yes.**

**Q3 — Do these limits match what you consider broken?**

- (a) Delisting: the code's effective wait is 24 h plus a walk; you ruled 12 h. The new alarm derives its limits from what the code does, so it is right either way, but the contradiction needs one rule: 24 h as the one rule (nothing changes on the walk path), or a true 12 h (ads go inactive 10–14 h sooner; sreality, which already revives about 7 % of the ads it makes inactive, would revive more). Either lands as its own small scraper PR after this sprint. **Recommended: 24 h.**
- (b) New ads: Watch when a new ad has waited 3 h to be fetched; Problem at 6 h, when over 10 % of fetched ads are still missing from Browse after 30 min, or when over 1 % of everything on the market is missing from Browse. Bazos burst days (waits up to 2 h on 10-12, 10-13 and 10-16, traced to the filed rate-ledger overflow) stay green. **Recommended: yes.**
- (c) A dedup rebuild pauses every merge (29 h on 10-16/17): it shows as "building", turns amber after 48 h and never rings; it rings only when the build stops deciding (0 decisions in 3 h). **Recommended: yes.**
- (d) The 2026-07-02 targets (15 min to Browse, 6 h delisting, 30 min for watched ads, 2 min notifications, 60 min photo merge) retire; the measured p95 is shown beside the old target where it exists. **Recommended: yes.**

**Q4 — May the page make these visible changes?** Health becomes the first Admin entry, in English; the panels listed at the end of § 5 go (each is wrong, frozen, duplicated or owned by another page); the floor-convention check is deleted (0 fails in 90 runs, the costliest check, measured against idnes); system alerts gain an "Open Health" link; AutodedupProgress's "SHADOW · merges nothing" becomes "The engine merges in production inside the apply scope; live lane health is on Health"; LocationQuality's cards lose their header bar to match the other admin pages. **Recommended: yes to all.**

**Q5 — May I drop the old health layer** once the new page has run 48 h with no reads of the old objects, after a pg_dump to R2? The 8 summary tables and their 10-minute refresh, the 11 read functions and 12 views only they used, the 6-hourly data-quality capture, the 1,822-row backup table from migration 551, `ops_incident_runs` and `portal_coverage_gate`, five `ops_incidents` columns and `portals.scrape_cadence_minutes`, and the two `app_settings` rows that nothing reads after the sprint. Check history keeps 35 days, pruned daily by the worker. `data_quality_snapshots` stays as frozen history. **Recommended: yes.**

**Q6 — Is the July listing-identity dual-write finished?** If yes, `dual_write_parity` (the most expensive check: p50 57 s, timing out in a third of runs, 2 false alarms and 0 real ones) is deleted and the legacy-column drop is filed as its own issue. If it still runs, it moves to the worker as a 6-hourly alarm with a 150 s budget and a cell per carrier. **Recommended: finished, delete it.**

## 12. Out of scope here, filed as GitHub issues on approval

The nine defects of § 2.9, plus: Railway `watchPatterns` so docs-only merges stop restarting the worker and the API (about 11 restarts a day); an exact migration-apply ledger; the outbound URL probe's move into Portal seams; user-facing request telemetry.

Filed on 2026-10-18:
- #1851 the poison sreality ad 615387212 (HTTP 409), re-armed forever
- #1852 the bazos rate-ledger lease overflowing `smallint`
- #1853 the 422 NULL-category ads no walk can nominate
- #1854 ceskereality's chaty-chalupy scopes double-counting houses
- #1855 `migration_drift`'s same-name blind spot (the migration-438 class; its options include the exact apply ledger)
- #1856 the dark lanes: the estimation lane that never ran and the count-probe lane nothing reads
- #1857 Railway `watchPatterns`
- #1858 24 h as the one delisting dwell rule (Q3 a)
- #1859 the legacy listing-identity columns, now that the dual-write is finished (Q6)
- #1860 the outbound URL probe's Portal seam (rule 21)
- #1861 user-facing request telemetry

No issue yet: the dead `supports_complete_walk` flag (§ 2.9) and API root logging (§ 10). The no-op `coverage_gate` workflow needs none, because wave 4 deletes it.

## 13. How the sprint is delivered

- One short-lived branch and one draft PR per wave, in the order of § 6; every PR carries its tests and its doc updates (architecture rule 16, the scraper-ops skill, the pipeline-verification reference, the reliability track, GLOSSARY). CI (pytest, schema replay, vitest, build, docs budget) gates every merge; nothing is pushed to `main`.
- Additive migrations (619–623) are applied before merge through `apply_migration.yml` and verified with a SELECT; the one destructive migration (624, wave 9) waits for the Q5 answer, a backup receipt, the confirmed deploy of the new page and 48 hours without a read of the old objects.
- After each merge Railway's per-service commit status is confirmed; the page wave gets a read-only check in a real browser against production.
- Every new alarm is replayed over 14–28 days of stored history as a committed test (aggregates only, no listing ids in the public repo) and ships only with zero false onsets.
- Every merge restarts the worker (and so the autodedup lane), so worker-touching merges are timed around autodedup seed sittings; on this machine tests run one file at a time.
- Migration numbers 619–624 sit above 615–618, which are claimed (#1846 holds 616; #1732 holds 617 and 618); they are renumbered at PR time if needed.
