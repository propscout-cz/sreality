# Reliability program (applied guarantees, one voice for failure)

The reliability rebuild designed after the 2026-08-26 six-portal ingest outage. Full
detail — root-cause chain, design principles, per-wave PR breakdown, the explicit
out-of-scope list and the eight open questions for the operator — lives in
[`docs/design/reliability-program.md`](../docs/design/reliability-program.md). This
track records sequencing + shipped state only.

**North Star.** Two guarantees: (1) **every merged migration is a live, applied
guarantee** — a migration that merged but did not land must be loud within an hour, and
the oracle is the Postgres catalog, never the applied-migrations ledger and never a
fresh-container CI replay; (2) **every fleet-wide failure surfaces once, to one place,
with its root cause attached** — one event table, one bell namespace, one escalation
policy, extended and never duplicated. The constraint on *how*: build on the existing
shared framework (`portal_runner`, `verify_pipeline.py`'s `CHECKS`,
`notification_dispatches`/`channel_sends`, `migrations/`) — two tables and zero
dependencies across the whole program.

## What happened (2026-08-26)

Between ~20:00 and ~21:30 UTC six of nine portal scrapers (mmreality, ceskereality,
maxima, bezrealitky, realitymix, remax) began failing every run on
`CheckViolation: … "listings_area_basis_check"`. Migration 423's stale-guard
(`if not exists (select 1 from pg_constraint where conname = …)`) saw a pre-existing
four-token constraint of that name on `listings`, skipped, and left the table on the
wrong version while `properties` got the correct five-token one from the same file.
Migration 438 was written to fix exactly that drift — it merged 2026-08-25 17:12 UTC and
was applied 2026-08-26 22:06 UTC, **29 hours later, by the session investigating the
outage**, because migration apply and PR merge are wholly uncoupled and four independent
monitoring gaps kept the failure invisible (`scrape_runs.errors = 0` on all six portals).

## 2026-08-31 addendum

The original incident is closed, verified with data (`area_basis='plot'` 0 → 344+; one
transient portal failure in four days; OpenAI topped up, LLM pipeline healthy). Two NEW
fires filled the inbox since — full analysis in the design doc's addendum:
**(a)** the location claim-intake lane dead since 2026-08-27 (PR #1209 edited prose
inside the ceskereality contract's governed hash without a version bump; no CI gate
exists over contract immutability → W0.5a: v4 bump + lockfile rail in `test.yml`), and
**(b)** hourly acute-health false-reds from `llm_liveness`'s 4h threshold, calibrated to
the dedup-vision workload deleted 2026-08-06 (→ W0.5b: 13h default, folded into W0.3's
PR). Neither is a portal-scraper defect — the fleet is green; W1 stays queued but is not
the urgent lane. Both fires are the program's thesis in new domains: a repo-declared
truth enforced only in production, and N identical emails for one cause.

## Wave status

| Wave | Scope | Status |
| --- | --- | --- |
| W0 stop the silent losses | Four one-file fixes, each currently *discarding* information: poller cursor loss + a poller-liveness check (W0.1), honest crash accounting at the shared `portal_runner` seam (W0.2), LLM check keyed on state not recency + a per-`called_for` starvation arm (W0.3), graceful degradation of the acute health lane under its 120s budget (W0.4) | 🟢 shipped 2026-08/09; its open follow-ups superseded by the Health dashboard sprint (2026-10-18) |
| W0.5 the second week's fires | (a) ceskereality contract v4 + contract-immutability lockfile rail in `test.yml` — un-wedges the intake lane, closes the merged≠enforced gap for portal contracts; (b) `llm_liveness` recalibrated 4h → 13h to the real (post-dedup) producer cadence | 🟡 in progress (2026-08-31, shipped as hotfix PRs ahead of wave order — the lane was down) |
| W1 survivable write path | Non-transient errors at `_flush_drain_batch` become a bounded, loud quarantine (per-item replay → existing `_drain_record_failure` → `given_up` at 5) behind a circuit breaker; `write_rejects` counter + a `drain_write_rejects` check (`ingest_freshness` largely covered in parallel by the walk sprint's `acquisition_lag` + `walk_coverage` checks) | ⚪ proposed — needs operator sign-off |
| W2 migration file as contract | `-- apply:` / `-- assert:` header tokens above a watermark (≥ 444), asserts executed inside `migrations.yml`'s existing per-file apply loop and *validated as able to fail*, plus schema-vocabulary parity and the hot-DDL doctrine rails. **W2.3's live-catalog drift check shipped in parallel as PR #1204 (`check_migration_drift`)** — remaining W2.3 scope is hardening it, not building it | ⚪ proposed — needs operator sign-off (part shipped in parallel) |
| W3 one alert, one place, cause attached | Failure signatures produced in-process at the write chokepoint (`checkviolation\|listings_area_basis_check` on all six portals), an `ops_incidents` table that exits through the **existing** `system_health` spine (no parallel channel), and the re-escalation ladder placed in `toolkit/system_alerts` where all 19 checks inherit it | 🟢 shipped 2026-08-31 — W3.1–W3.3 (migs 462+463, PR #1247) + W3.4's ladder, flap cooldown and weekly heartbeat (PR #1246); one residue: external alert routing (`system_health_channels` / `notification_email_to`) awaits the operator — until flipped, incidents ring the in-app bell only. W3.4's ladder was replaced 2026-10-18 by the Health dashboard sprint's wave 1; the signature engine retires in its wave 3 |
| W4 apply becomes a job | Hot-table DDL applied out-of-band by a tested, extracted lock primitive (`attempts=1`, fail fast, escalate to a window — the retry-loop pattern is retired, not tuned); the existing PK-swap applier generalised into a dispatchable maintenance window | ⚪ proposed — needs operator sign-off + one human step (`REALTIME_WORKER_ENABLED`) |
| W5 constraint-only catalog diff | Named successor: diff CI's replayed schema against the live catalog. Catches 438 exactly, plus the ~241 migrations with no ledger row and no assert, plus the reverse drift direction W2 structurally cannot see — with zero per-migration convention | ⚪ named only, not built (open question 8) |

## W0 — shipped 2026-08/09; its open follow-ups superseded by the Health dashboard sprint (2026-10-18)

Approved 2026-08-27. Grouped into three PRs rather than the design doc's four, because
W0.1/W0.3/W0.4 all touch `scripts/verify_pipeline.py`'s check registry and stacking them
separately would only create merge-order pain:

- **Docs + track** — this file, `docs/design/reliability-program.md`, the index row.
- **W0.2 honest crash accounting** — `_run_phase`/`_finalize` lifted out of all nine
  `*_main.py` into `portal_runner` (rule #21: one shared framework, no per-portal
  special-casing), and the exception path stamps a non-zero error state instead of
  finalizing green. Nine copies → one; nine bugs → zero.
- **W0.1 + W0.3 + W0.4 — "make the health-check surface tell the truth"** — the poller
  keeps its cursor when page-capped and gains a `workflow_poller_liveness` check;
  `currently_failing` is derived from `last_ok_at < last_err_at` instead of a 90-minute
  staleness window; `llm_burn_rate` gains a per-`called_for` starvation arm; the acute
  lane persists each check as it completes under per-check and total wall-clock budgets.

**Risk carried into the wave:** ~~W0.3 reds the acute lane until the OpenAI account is
topped up~~ — **resolved 2026-08-30: credits topped up, pipeline verified healthy** (595
ok / 0 err calls in 24h). The remaining W0 risk is rebase drift: the walk-coverage
sprint (merged 2026-08-27..30) rewrote parts of `idnes_main.py`, `ceskereality_main.py`
and `portal_runner.py` under W0.2's feet; both W0 code PRs are being rebased onto it.

## W3 — W3.1–W3.3 shipped (2026-08-31)

Built on the corpus the design doc asked for first: 554 real failures over 14 days,
36 distinct signatures, so every threshold below is measured rather than guessed.

- **W3.1 signatures** — `scripts/failure_signature.py`, pure stdlib. The key comes from
  the error TEXT and never from `workflow_path`; the one shape that IS scoped by
  workflow is the unreadable-red fallback (unscoped, `step:|exit:1` merged 13 runs
  across 10 unrelated workflows into one meaningless mega-incident). Producers:
  `portal_runner._record_run_crash` (in-process, t+0, zero API cost) **and** the two
  `realtime_worker` lanes, which bypass `run_phase` entirely and had no failure record
  of any kind — both through one `record_failure_signature` seam, not two producers.
- **W3.2 `ops_incidents`** (migration 462) — one OPEN row per signature behind a partial
  unique index. Onset at `failure_count >= 2` **or** 2 distinct workflows, whichever
  first (the breadth arm is measurably faster: 8 min to the 2nd workflow vs up to 164
  min to a 2nd same-workflow failure under the Actions throttle). Closes on member-
  workflow success, at 168 h, or manually.
- **Review fixes, same wave (migration 463).** Two producers over one counter needed a
  correlation key and had none: the chokepoint recorded a portal crash at t+0 and the
  poller recorded the SAME concluded run hours later, so `failure_count` was ~2x reality
  and a lone crash crossed the measured onset threshold by itself. `ops_incident_runs`
  makes one Actions run at most one failure, and the poller claims before it downloads —
  which also replaces the earlier workflow-keyed signature reuse, whose lack of run
  correlation folded unrelated new reds into whatever incident last touched that workflow.
  With it: the onset claim and its emit became one transaction (autocommit connections
  were committing the claim alone, so a failed emit silenced that incident's only alert
  forever), the incident pass moved after the cursor write and gained a wall-clock budget,
  and the worker's recorder moved onto `asyncio.to_thread` — it was the one blocking DB
  call left on the event loop, worst exactly during a DB outage.
- **W3.3 exits through the existing spine** — one `system_health`
  `notification_dispatches` row per incident. **No new alert table, no parallel
  channel.** The outbox RETRY allowlist bug is fixed with it, so an ops alert now gets
  the same five delivery attempts a price drop gets instead of one.

**Corrections to the design doc, from the measurement pass.** The "~100 failures/day"
baseline counts cancellations; true `failure` volume is **39.8/day** and cancels are
concurrency-group evictions carrying no error (excluded). Portal workflows are only
**22%** of the corpus, so the log backstop is the majority path, not an optional extra.
The `listings_area_basis_check` cluster spans **8** workflows (iDNES included) and its
onset was **2026-08-24 23:09 UTC**, ~45 h earlier than the doc states. Migration 220's
`consecutive_failures`/`is_chronic` are computed over GitHub workflow runs, not
`pipeline_check_results`, so W3.4's ladder cannot consume them.

**Not flipped, on purpose:** `system_health_channels` is still `[]`. Incidents ring the
in-app bell only until the operator chooses routing.

## Open questions blocking W1–W4

Eight in the design doc; question 1 (OpenAI top-up) is **resolved**. Still load-bearing
for sequencing: **branch protection — `main` is currently not protected, so every CI
rail W2 adds AND the new W0.5a contract lockfile rail are advisory until that changes**,
alert routing + volume tolerance (W3), the two-human-step Railway procedure (W4), and
the disposition of migrations 433 and 434.

## W3.4 — shipped 2026-08-31

*Replaced 2026-10-18 by wave 1 of the Health dashboard sprint (section below): one incident per
failing cell, told by the API, instead of a ladder re-derived from 30 days of results.*

The escalation ladder, landed on its own ahead of the rest of W3 because it needs no new
table, no migration and no routing decision — it changes only *when* the existing
`system_health` bell rings.

- **The unit of alerting is an incident, not an edge.** One incident opens at the first
  `fail` and survives flaps inside a 6h cooldown; every dedupe key hangs off its onset
  timestamp, so `ON CONFLICT` gives exactly-once across both lanes with no new state.
- **6h / 24h / 72h / weekly rungs**, highest-due-only, so a six-day red says so four
  times instead of once (`property_maintenance`, 2026-08-20..26) and a six-day-old
  incident discovered on deploy emits one alert, not five.
- **Flap damping both ways**: a return to red inside the cooldown re-enters the same
  incident, and a recovery is announced only once the check has held non-fail for the
  cooldown — `llm_errors`' 114 alternating alerts collapse to one onset + one recovery.
- **A weekly heartbeat on the existing (empty) `--weekly` lane**, keyed per ISO week
  because the workflow appends `--weekly` to all four of Monday's runs. It is the half
  of the dead-man switch migration 274's in-DB `emit_verification_stale_alert` cannot
  cover: a harness that runs while delivery is silently broken.
- Policy is five scalar `pipeline_check_thresholds` keys (`load_thresholds` drops
  non-scalars, so an array would have been ignored undetectably); code defaults ship
  live, no migration.
- **Review fix — the anchor cannot be the history window's edge.** `check_states` reads
  30 days, so a check red for longer than that had every in-window row a `fail` and took
  the oldest surviving row as its onset — a value that advances one cadence per run as
  rows age out. Every dedupe key hangs off that anchor, so the ladder would have
  re-emitted **onset every run, forever** on an incident older than a month: the flood
  it exists to remove, in a new shape, and strictly worse than main's silence.
  `onset_truncated` now flags a streak that runs off the window edge and one extra query
  pins its real onset.

**Not** wired: migration 220's `consecutive_failures`/`is_chronic`, which the design doc
named. They are computed inside `workflow_failure_summary(int)` over `workflow_failures`
— the GitHub workflow-run domain, not `pipeline_check_results`. The pipeline-check streak
is read from its own table; the two domains stay separate.

## 2026-09-30 — the realtime worker cannot freeze silently (shipped with this PR)

The worker went silent 2026-09-29 23:42 → 07:22 UTC without exiting: ceskereality 403s pushed
the shared rate ledger an hour ahead, abandoned probe/drain passes leaked a thread each into the
default executor until it filled, and the heartbeat starved behind them while the loop lived on.
Fixed in one PR: a watchdog thread that `os._exit(1)`s after 300 s without a finished beat or a
pass lock held over 1 h (the heartbeat stays on the shared, now pinned, executor as its canary;
a `faulthandler` exit timer covers a stack dump stuck on a full stderr); pass locks on probe + drain,
all seven locks through one `_PassLock` whose age keeps a wedged lane in `in_flight_s`; the
worker's probe, drain and sold_comps refuse (never park on) a ledger wait over 120 s, and its
drain refuses past its deadline, while the Actions walks and drains stay unbounded; the retry
after a 403 leases one slot and a drain's lease is sized to its claims. Detail: `docs/design/realtime-scrapers.md` § Liveness.
- **Next:** a per-portal circuit breaker (stop re-probing a portal that 403s everything), Health
  alerts on `budget_refused` / `portal_rate_state.next_slot_at` running ahead, and a standing
  `faulthandler` timer for a GIL-holding stall the watchdog thread cannot see.

## 2026-10-10 — a text-lane pass the provider refused is a failed pass, and the checks name the provider (shipped with this PR)

OpenAI ran out of credit on 2026-10-02; the alarms fired but pointed the wrong way (no provider or model named, Anthropic jobs listed as down, the heartbeat booking every refused pass as a success) and nothing left the app (`system_health_channels` is `[]`). Fixed here: the refused pass is a failed pass; `text_extraction_lag` fails on the provider's refusal and names the model; `llm_errors` judges credit per provider and names the provider and model.

Next:
- ~~`verify_pipeline`: a timed-out warn counts as "not failing", closes the incident and sends a false "Recovered" (`text_extraction_lag` did it 6 times since 10-02); a timeout must keep the incident open.~~ Done 2026-10-18 (Health dashboard sprint wave 1: a timeout is `unknown`).
- `llm_errors` rate arm: `currently_failing` still reads the newest call of ANY provider; for ~24 h after a top-up a stray error can turn the check red again. → Health dashboard sprint wave 3 (the llm check).
- `worker_lane_stall` reports `failed_passes` but never alarms on it. → wave 3 (the jobs check's lane cells).
- Delivery: `system_health_channels` is `[]` and no transport is configured, so no system alert has ever left the app (operator: email key + recipient + channel). → Q1 ruled 2026-10-18: bell only for now; wave 7 keeps email and Telegram ready.

## 2026-10-18 — Health dashboard sprint, wave 1 (shipped with this PR)

The Health page and its alarms are rebuilt as one sprint of ten waves, one draft PR each, planned
in [`docs/design/health-dashboard.md`](../docs/design/health-dashboard.md). Its north star: a red
on Health is a real, current, explained problem — measured once, judged once, shown once, told
once. The operator answered its six questions on 2026-10-18 (the rulings head its § 11). It
supersedes the open follow-ups of this program's W0 (shipped 2026-08/09) and replaces W3.4's
ladder; the `ops_incidents` signature engine retires in its wave 3.

The ten waves: (1) one vocabulary, one registry, one ladder; (2) watchers — a pg_cron liveness
check and one hourly GitHub canary; (3) platform checks and the worker's health lane, retiring the
signature engine; (4) portal checks: scraping, publishing, delisting and coverage; (5) the dedup
check; (6) the parsing alarm and the quality gauges; (7) delivery, now readiness and docs only,
because the bell stays the one channel for now (Q1); (8) the new Health page; (9) dropping the old
health layer — destructive, after a backup and 48 h with no reads (Q5); (10) shared admin page
parts.

What wave 1 changed:
- **One vocabulary.** Every result is `ok`, `warn`, `fail` or `unknown` (OK / Watch / Problem /
  Not measured). A check that timed out, did not run, raised, or could not measure is `unknown`
  with its reason: never OK, and it never opens, recovers or ages an incident. Migration 619 widens
  the status CHECK.
- **One registry.** `scripts/verify_pipeline.py` `CHECKS` declares each of the 29 keys once
  (class, host, cadence, label, unit, owner page) and projects them into the new `health_checks`
  table. Both GitHub lanes run only the checks that are due, so the daily sreality image probe and
  the weekly portal-link probe became cadences, and `sreality_image_canary.yml` and
  `verify_outbound_urls.yml` are deleted. Each result keeps its message and its cells. Eight keys
  are gauges (Q2): location coverage, the Kč/m² price floor, area vs usable area, walk coverage,
  deferred delist nominations, ads with no portal link, description reading and the floor
  convention; the rest are alarms.
- **One ladder.** `toolkit/system_alerts.reconcile`, run by the API every 60 s, keeps one
  `ops_incidents` row per failing cell (`cell:<key>:<subject>`), sends one grouped onset per check,
  reminds until acknowledged on Health — an alarm at 6 h, 24 h, 72 h and then weekly, a gauge once
  a day (the Q2 ruling) — and sends one recovery once an OK is measured across
  `max(2 × cadence, 30 min)`. The 30-day re-derivation, the flap cooldown, the day-keyed alerts
  and the weekly heartbeat are deleted. The GitHub lanes write no system alert; a failing alarm
  still reds either lane, so GitHub emails.
- **Read is not acknowledged.** Platform admins can now mark a system alert read (migration 619's
  policy arm; all 861 had stayed unseen), and only Acknowledge stops reminders: a minimal
  Acknowledge button on Health's pipeline-checks card (`POST /admin/health/acknowledge`) until
  wave 8 rebuilds the page.
- **One admin-gated read.** `pipeline_checks_public` serves the registry, each key's newest result,
  its effective status (Not measured once stale) and its incidents; keys outside the registry, such
  as the frozen `outbound_url_parity`, leave the page.
- The retiring signature engine is fenced: it can never close or "recover" a cell incident, and
  its poller ignores the `check:` signatures of a red lane.

Owed items this absorbs:
- **`docs/design/realtime-scrapers.md`'s deferred W5b** (SLO-scaled health, push alerting,
  silent-green closures; also listed in `roadmap/next.md`): replaced by the sprint instead of a
  rewrite of `scraper_health_checks_mv` — wave 1 turns silence into Not measured, wave 2 watches
  the watchers, wave 4 derives each portal's limits from its own behaviour, and wave 9 drops the
  matview.
- **From the 2026-10-10 Next list:** the timed-out warn that closed an incident with a false
  "Recovered" is fixed (a timeout is `unknown`), and the ladder no longer re-derives incidents from
  30 days of results. The `llm_errors` any-provider rate arm and `worker_lane_stall`'s unalarmed
  `failed_passes` move to wave 3 (the llm and jobs checks); delivery follows Q1 (bell only for now).

**Next:** wave 2 — `health_liveness()` on pg_cron every 5 minutes and one hourly GitHub canary
(migration 620).
