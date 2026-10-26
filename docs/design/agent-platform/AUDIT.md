# Agent platform: what exists today (audit, 2026-10-25)

> **Status: AUDIT for the agent-platform proposal ([PROGRAM.md](./PROGRAM.md)). Read-only: nothing was
> changed, built, applied or deployed.** Repository `main` @ 3f0ff5b8a. Live figures come from read-only
> checks on 2026-10-25: aggregate Supabase SELECTs, Railway metrics and variable *names* (never values).
> Ten area audits fed this file. Where a live check disagreed with an audit, the live check wins and is
> marked *live*.

*How to read it.* Findings F1–F76 are grouped by area (A–J). Each has a statement, its evidence (path:line
or a live figure) and why it matters, as brief points B1–B13 (the operator's brief of 2026-10-25). Five
inventories follow: T1 capabilities, T2 routes by gate, T3 job primitives, T4 ledgers and storage, T5
exists vs needs building, per brief point.

**Terms used here.**
- **Service role**: the database login that bypasses row-level security (RLS).
- **Tenant pool**: the login the API uses to act as one signed-in user under RLS.
- **Lane**: one loop inside the always-on realtime worker.
- **Lease**: a "mine for N minutes" stamp on a queue row, so a crashed holder's work comes back.
- **Census**: a test that counts a population (routes, tables, tools) and fails on anything its reasoned
  allowlist does not name.
- **IDOR**: one user reaching another user's rows by changing an id in a request.
- **OOM**: out of memory; the system kills the process. **JWKS**: the published public keys a JWT signature is
  checked against. **VAPID**: the key pair that signs web-push messages. **DPIA, LIA**: the GDPR data-protection
  impact assessment and legitimate-interest assessment. **DSN**: a database connection string, login included.
- **Cloudflare R2**: the object store for photos and files. It is always written "R2" with a store word next to it
  (bucket, key); the program's rulings are numbered AR1–AR20, and "R2b" is the existing notification-channel ruling.

## A. Topology, deploys and infrastructure

**F1. Three Railway projects, one service each, no private network between them.** API (`uvicorn api.main:app`),
realtime-worker (`python -m scraper.realtime_worker`, same Dockerfile) and vite (Caddy) each sit in their own
project in Amsterdam, one replica, 24 GB limit each, with no shared variables, volumes or buckets. Supabase is
in Ireland (eu-west-1), so every database call crosses regions. Railway: services in different projects cannot
talk over the private network.
*Evidence:* live Railway checks; `railway.json`, `railway.worker.json`, `frontend/railway.json`; RESEARCH §7.5.
*Matters:* a new service that must reach another privately has to share its project (B1, B5).

**F2. Load is tiny and there are no outside API consumers.** Last 7 days: 62,954 API requests, of which 2
`POST /estimations`, 0 `/tools/find_comparables`, 0 `/listings/summaries`; API CPU average 0.0035 vCPU, memory
average 0.40 GB. 5 of 7 accounts active in 30 days; 5 auth users; 0 push subscriptions; Stripe has never received
an event. 106 estimation runs ever, 7 in the last 90 days. The market behind it: 1,039,401 properties and
1,054,810 ads, 393,030 of them active.
*Evidence:* live.
*Matters:* "hundreds of simultaneous requests" is a design target, not today's load; every new piece of
machinery is pure operating cost until then (B7).

**F3. The realtime worker is near its memory ceiling and is one blast radius.** Memory average 7.4 GB, peak 21.5 GB
of 24 GB (one autodedup pass: 11.4 GB). 17 lanes share one process and one 32-thread executor
(`scraper/realtime_worker.py:236-246`). It was OOM-killed at the earlier 8 GB limit, and a blocked portal once
froze every lane for 7 h 40 min. At list prices it costs about $85 a month (7.4 GB average × $10 plus 0.55 vCPU ×
$20); the API about $4.
*Evidence:* live metrics; memory notes railway-worker-memory-limit-8gb-2026-10-10 and
realtime-worker-thread-starvation-2026-09-30.
*Matters:* an agent run inside this process cannot be memory-capped; its OOM would stop scraping,
notifications and push together (B5, B7).

**F4. Every merge redeploys the API and the worker.** Only vite has watch patterns. About 50 API deploys between
10-22 and 10-25; 118 PR merges between 10-18 and 10-25. The worker drains for 30 s; the API sets no draining. Paid
work running as an API `BackgroundTasks` dies with the old container (`api/estimation_runs.py:855-870`;
`api/building_runs.py:451-456`).
*Matters:* durable runs must survive deploys by design (B7).

**F5. Railway stops reading `railway.json` files on 2026-12-01.** Config as Code is deprecated: existing files
"continue to work … until 2026-12-01 (hard cutoff)", and new services cannot opt in. All three services rely on
these files for builder, healthcheck, restart policy, start command and draining; the dashboard settings differ
(Railpack, no start command, no healthcheck). The replacement is Infrastructure as Code (a TypeScript file under
.railway/, applied by the Railway CLI).
*Evidence:* `railway.json:1-13`, `railway.worker.json:1-11`, `frontend/railway.json:1-16`; RESEARCH §7.3.
*Matters:* independent of agents; and any new service must be IaC (B5). **Do now.**

**F6. Railway advises rotating credentials after its 2026-10-21 incident.** Changelog, 2026-10-23: "we recommend
that all customers revoke and replace sensitive credentials stored on Railway before October 21." No rotation is
recorded.
*Evidence:* RESEARCH §7.7.
*Matters:* every database, LLM, R2, Valuo, VAPID and GitHub secret on the three services predates the incident
(B8). **Do now.**

**F7. Secrets are broad and co-located.** API (20 variables) and worker (14) each hold the RLS-bypassing
`SUPABASE_DB_URL`, four LLM keys, one full-bucket R2 key pair and `RUNPOD_API_KEY`. The API also holds
`TENANT_POOL_DB_URL` and a GitHub token (`api/price_stats.py:120-136`). No `api/` code reads `RUNPOD_API_KEY`.
`TENANT_POOL_DB_URL` is an impersonation credential: the app sets the user claims itself after
`SET LOCAL ROLE authenticated` (`api/tenant_pool.py:44-83`), so whoever holds it can act as any account.
*Evidence:* live variable names; toolkit-api skill, secrets reference.
*Matters:* agent-written code must never run where these live (B8).

**F8. The image runs as root and ships no runtime skills and no spreadsheet or CAD library.** The Dockerfile has no
`USER` line and copies `scraper toolkit api scripts location_data contracts data autodedup`, not `skills/`
(`Dockerfile:13-45`). `reportlab` is installed; openpyxl, pandas and CAD libraries are not. Base tags float.
*Matters:* skills reach production only through migrations and live edits (F31). A root container can drop each
code run to its own unprivileged uid with setuid, no namespaces needed (B6, B8).

**F9. Railway service containers cannot build namespace sandboxes; what else works is unknown.** No privileged mode
and no Docker-in-Docker (Railway docs). A Railway bot reply (2026-09-16): service containers "do not support the
unshare calls bubblewrap needs". seccomp filters, Landlock and /dev/kvm are unconfirmed. The probe
("unshare -Ur true"; Seccomp and CapEff in /proc/self/status; the Landlock ABI) has not been run, and the Railway
CLI is not installed on the operator's machine.
*Evidence:* RESEARCH §7.2; live.
*Matters:* the Phase 7d premise, "sandboxed subprocess on the Railway container", does not hold as written (B6, B8).

**F10. The operator owns propscout.cz; the first wave of the cutover is approved.** Cloudflare nameservers.
app.propscout.cz (SPA) and api.propscout.cz (API) as DNS-only records, because Cloudflare's proxy cuts responses at
125 s; apex and www redirect (302) to app; the zone gets a null MX, SPF "-all" and DMARC p=reject now, with no mail
on the domain. MCP, the privacy page and Google consent branding are outside that sprint.
*Evidence:* operator ruling 2026-10-25; `docs/design/brand-domain-and-store/PROGRAM.md:131-132, 219-255`.
*Matters:* the MCP resource URL becomes https://api.propscout.cz/mcp; any platform mail needs its own subdomain;
Google verification needs a real homepage on a domain we own (B10, B11).

**F11. A proposed Actions-exit program adds a batch-worker with one job registry and one lease row per job.** Draft
PR #2000 (docs/design/actions-exit/PROGRAM.md on branch roadmap/actions-exit-program; PROPOSED 2026-10-25, seven
rulings owed) moves every scheduled production job onto one new Railway service built from the same image: a JOBS
registry in code (name, argv, interval, timeout, memory), the `location_jobs` table renamed scheduled_jobs as the
one due-and-lease row per job, one subprocess per job with SIGTERM at its timeout, aggregate memory admission at
70 % of an 8 GB limit, and the lane supervisor moved into a module both workers share. It would make the repo
private (Actions on a private repo would bill about $650/month; about $150 after the move).
*Evidence:* that program §4.2–4.3, §6, §8.
*Matters:* scheduled agent workflows must ride that registry and lease, not a second scheduler (B7). A private repo
changes the eval-data posture (B13). It is a coordination point, not settled.

## B. API, authentication and tenancy

**F12. One credential, but `verify_jwt` checks too little.** Every caller presents a Supabase JWT; `_bearer` is the
only reader of `Authorization` (`api/dependencies.py:157-164`). `verify_jwt` checks the signature (ES256/RS256
against the project JWKS) and `audience="authenticated"`: no issuer pin, no required claims (PyJWT checks `exp` only
if present), no rejection of tokens that carry client_id (`api/dependencies.py:202-232`). A token without `sub`
raises a 500 in `resolve_account_id`.
*Matters:* a token minted by an OAuth server with `aud=authenticated` would pass every REST gate, admin routes
included (B8, B10). **Immediate hardening.**

**F13. Signup is open, with an automatic trial.** Supabase Auth: signup open, email confirmation required, Google and
email providers. `handle_new_user` creates a personal account, an owner membership, the default board, a monitoring
collection and a 7-day trial entitlement (`migrations/612_one_signup_path_default_board.sql:85-109`).
*Evidence:* live.
*Matters:* "any signed-in user" means anyone who registers. Every `require_token` route and every unmetered spend
path is open to a stranger (B8).

**F14. About 281 route-method pairs, each behind one of nine gate shapes.** See T2. Censuses pin the shapes
(`tests/api/test_admin_route_coverage.py`, `tests/api/test_account_scope_census.py`,
`tests/api/test_acting_routes_census.py`).
*Matters:* agent and MCP surfaces must join the same census discipline (B3).

**F15. The ten `/buildings` routes are account-blind.** They run on the service role behind `require_token` with no
account predicate (`api/main.py:1042-1175`). `create_building_run` never stamps an account, so every building run
lands on SYSTEM (`api/building_runs.py:78-104`). `confirm_units` fans out agent-mode rent children with
`claims=None`, unmetered (`api/building_runs.py:205-222`). The entry point was retired 2026-10-20
(`roadmap/next.md:67-69`); production holds 0 building runs.
*Matters:* a latent IDOR with paid fan-out; roadmap/public-release-track.md item 11 (B8). **Immediate hardening.**

**F16. The browser role can write the run tables directly.** Migration 291 grants `select, insert, update` on
`estimation_runs` and `building_runs` to `authenticated`, with own-account policies and no column restriction
(`migrations/291_estimation_building_runs_account_id.sql:29-43`). Migration 292 grants insert on
`estimation_trace_payloads` and insert/update on `estimation_feedback`, each with a SYSTEM arm
(`migrations/292_child_grain_account_scoping.sql:137, 162`). The worker executes any pending row that has a payload,
without re-checking entitlement (`scraper/realtime_worker.py:1621-1698`). The one legitimate browser-role UPDATE is
the scenario edit: `PATCH /estimations/{run_id}/scenario` runs on `tenant_conn` (`api/main.py:819-831`), and
`update_scenario` sets only `scenario` (`api/estimation_runs.py:1048-1090`). No SPA or extension code writes these
tables through PostgREST (grep).
*Matters:* a signed-in user could queue runs past the quota, back-date them, or rewrite a terminal result, which
breaks rule 12's immutable result (B8). Not live-tested; the grant text is unambiguous. **Immediate hardening.**

**F17. Several `require_token` routes spend money for any caller.** `force_refresh` on `/tools/summarize_listing` and
`/tools/compare_listing_images`, and the batch `/listings/summaries` (LLM; `api/main.py:660-740`); `/maps/suggest`
and the static map (Mapy credits, about 4 per map); `/tools/verify_listing_freshness` (a live portal fetch); and
Valuo (behind `verify_jwt`, a paid outside valuation, deduplicated only per identical request, no per-account cap;
`api/routes/valuo.py:26-34`).
*Matters:* an MCP or agent surface would expose every one of these (B8, B10).

**F18. No rate limit, request audit or enforced budget.** No limiter middleware exists; the only 429s are the
estimation quota and cap. `_check_daily_cost` only logs (`api/llm_client.py:327-345`). `require_entitlement(agenda)`
exists, but its docstring says "Not wired to any route yet" (`api/routes/billing.py:337-359`); plan agendas gate only
the SPA navigation. Nothing records who called what; `delegated_edits` is the only per-write actor ledger.
*Matters:* the MCP spec requires rate limiting, and per-account metering is missing everywhere but estimations
(B8, B10).

**F19. `API_AUTH_OPTIONAL=1` has no production guard.** It opens `_bearer`, which serves `require_token` and
`account_scope` (`api/dependencies.py:157-164`); only `tests/conftest.py:25` sets it.
*Matters:* one mis-set Railway variable would open 42 routes: the 40 `require_token` routes and the 2 `account_scope`
routes of T2 (B8).

**F20. The tenant pool is the working "act as the user" primitive, and it trusts its caller.** `tenant_transaction`
opens a connection with `TENANT_POOL_DB_URL`, runs `SET LOCAL ROLE authenticated` and binds the claims, method and
act-as header; acting GETs run READ ONLY; there is no fallback connection (`api/tenant_pool.py:44-116`). The role is
NOINHERIT and NOBYPASSRLS, capped at 50 connections (`migrations/293_tenant_role.sql`). It takes a claims dict, not a
signed token. Nothing outside `api/` uses it.
*Matters:* a background run can act as its user under RLS through this one seam, as long as the claims come only
from columns the API stamped (B8).

**F21. Delegation exists and is audited.** `pipeline_shares` (read or edit), `acting_account_ids()` driven by the
`x-act-as` header, party policies with `and not acting()`, and the append-only `delegated_edits` trigger on eleven
curation tables, readable by the owner only (`migrations/641_pipeline_shares_and_acting.sql:64-254`).
*Matters:* an agent acting for a delegate maps onto this model with no new identity machinery (B8).

**F22. SYSTEM is a shared-visibility default.** `estimation_runs` and `building_runs` default `account_id` to the
SYSTEM account (`migrations/291_estimation_building_runs_account_id.sql:13-18`), which every account can read
(`migrations/291_estimation_building_runs_account_id.sql:32-36`).
`create_estimation_run` falls back to SYSTEM when no account is passed (`api/estimation_runs.py:757`). 98 of 106
estimation runs sit on SYSTEM.
*Matters:* any new run table needs account_id NOT NULL and no SYSTEM read arm (B8, B9).

**F23. Two definitions of "admin".** The API trusts the JWT's `app_metadata.is_admin`, or a top-level `is_admin`
(`api/dependencies.py:266-278`); RLS trusts the `admins` table. They agree today (2 = 2) but nothing syncs them, and a
revoked admin keeps the claim until the token expires.
*Matters:* admin-only agent capabilities must check one source (B8).

**F24. The per-account in-flight cap can be raced.** `_insert_run` is one INSERT … SELECT … WHERE (monthly count) <
quota AND (in-flight count) < cap ON CONFLICT DO NOTHING (`api/estimation_runs.py:1823-1894`). The idempotency part
is atomic; the two COUNTs are READ COMMITTED snapshot reads, so two concurrent submits can both pass.
`_prepare_metered_submit` adds a check-then-act pre-check (`api/estimation_runs.py:641-688`).
*Matters:* a run queue needs a per-account lock or a compare-and-set counter (B7).

## C. The agent loop, skills and the LLM layer

**F25. One synchronous, estimation-specific agent loop, with a strong anti-hallucination design.**
`run_agent_estimation` (`api/agent.py:591`) checks iterations, cost and wall clock between turns (676-685), makes one
`LLMClient.call` per turn and dispatches tools in-thread on one connection (727-765). The result is server-derived:
`_finalise` (1539) uses the harness's last cohort, the model only excludes rows, and ids it invents become warnings
(1599-1625); `estimation_cohort_entries` keeps each comparable's `snapshot_id` (998-1063; rule 8). It is hard-wired
to rent: the opening message (1736-1807), `_AGENT_ESTIMATE_KIND` (1509), a fixed `called_for`.
*Matters:* once generalised, this loop and its terminator pattern are the platform's agent runtime (B4, B5).

**F26. Two tool registries and no argument validation.** Agent tools are `_ToolDef` entries (name, description,
input_schema, handler, is_terminator; `api/agent.py:101-106`) in `AGENT_TOOLS` (1847): 13 read tools plus
`record_estimate`. Only the relaxed comparables schema is generated from `to_jsonschema_properties` (138-140). The 16
`/tools/*` routes have hand-written Pydantic bodies, several sreality-only. `_FCR_OVERRIDE_FIELDS` (853-883) is a
second whitelist, and unknown arguments drop silently.
*Matters:* one registry must serve REST, agents and MCP (B2, B3, B10).

**F27. Seven of the thirteen agent tools write caches.** The toolkit is read-only "with nine explicit exceptions"
(`.claude/skills/toolkit-api/SKILL.md:37-70`). Seven agent tools write: `verify_listing_freshness` (writes
`listing_freshness_checks`, may write `listing_snapshots`, nominates a page check), `compute_walkability` and
`compute_amenity_supply` (the OSM mirror, through `find_anchor_amenities`; `toolkit/walkability.py:27, 91, 194`),
`find_comparables_along_axis` (`transit_lines`), `summarize_listing` (`listing_summaries`), `compare_listing_images`
(`listing_image_comparisons`) and `read_floor_plan` (`building_attachment_analyses`, keyed by attachment id and
model, with no account); handlers at `api/agent.py:1171-1341`. `describe_neighborhood` writes nothing. Two of the
nine exceptions, `discover_condition_markers` and `score_listing_condition`, belong to the parked condition program
(rule 14). About 31 toolkit modules carry write SQL in all.
*Matters:* market tools cannot all run in a READ ONLY transaction; the cache writers need their own class (B2, B8).

**F28. The model layer is provider-neutral; the agent entry is not.** `LLMClient.call` routes by model, prices the
call and writes one `llm_calls` row, failures included (`api/llm_client.py:174-260`). Five providers are registered:
anthropic, gemini, openai, qwen, oss (`api/dependencies.py:70-99`). But `CreateEstimationIn.provider` is
`Literal["anthropic","gemini"]` (`api/schemas.py:283`), the skills map only those two, and the SPA defaults to
`'anthropic'` (`frontend/src/components/BrowseExperience.tsx:479`,
`frontend/src/components/NewEstimationModalBody.tsx:362`, `frontend/src/components/estimation/RunPanel.tsx:1377`)
and offers only Claude or Gemini (`frontend/src/components/estimation/RunPanel.tsx:1593-1601`).
*Matters:* the agent layer stays pluggable with no new code; only the narrowing goes (B5).

**F29. Live model settings and spend.** `enrichment_model` is gpt-5.6-luna. Eight lower-volume lanes (building
extractor, condition discovery, condition, floor plan, image compare, refiner, summary, outreach draft) run Claude 4.5
models. `llm_calls`: 1,557,525 rows, 1,108,064 in 30 days, $389.18 in 30 days, nearly all enrichment.
*Evidence:* live.
*Matters:* the 2026-08-27 "no Anthropic for high-volume lanes" ruling is applied to the enrichment lane only; the
agent layer's model policy is an open ruling (B5).

**F30. Run limits are soft.** Guards run between turns, so one turn plus nested vision tools can exceed
`max_cost_usd`. `max_tokens` is fixed at 4096 (`api/llm_client.py:182`), and the agent call never overrides it
(`api/agent.py:687-695`). The wall clock can overrun by one provider
call (a 120 s HTTP timeout) plus tool time.
*Matters:* with open signup (F13), limits must be hard per call (B7, B8).

**F31. Skills: the database row is canonical, and it has drifted from git.** "After deploy, the DB row is the source
of truth" (`migrations/029_agent_runs.sql:20-24`). Live `rental_estimator_full_v1` is v10 at 10,578 characters; its
SKILL.md is 9,844; replaying migrations 076, 638 and 645 gives 9,010. Three texts. The refiner's Apply writes the live
row after one admin click, with no eval (`api/refiner.py:228-254`). A run stamps `skill_version` at submit but
reloads the prompt at claim (`api/estimation_runs.py:1732-1734`), so an edit in between runs under the old number.
*Evidence:* live SELECT; `roadmap/measure-track.md:244-251` records the drift.
*Matters:* the brief wants agents and skills authored, versioned and tested in GitHub (B4, B13).

**F32. A `skills` row bundles what the brief calls an agent, and the vocabulary has no Agent or Tool.** A row holds
`system_prompt`, `allowed_tools`, `preferred_model` and `limits` (`api/skills.py:47-63`). GLOSSARY defines Skill as
"The instructions an AI follows to carry out a workflow" and lists "agent profile" as a word to avoid
(`GLOSSARY.md:303-306`). There is no Agent or Tool entry, and "workflow" collides with GitHub's workflows. Developer
skills (`.claude/skills/`) and runtime skills (`skills/`) are unrelated namespaces.
*Matters:* tools, skills and agents need separate homes and names (B4).

**F33. No code execution exists, and Phase 7d is deferred with a confirmed approach.** grep for exec(, eval(,
subprocess and RestrictedPython over `api/` and `toolkit/` finds nothing. Phase 7d: "Self-hosted sandboxed
subprocess on the Railway container. No third-party sandbox (rules out e2b), no provider-hosted code-exec beta", an
"agent_code_executions" audit table, a separate "computation_v1" skill, a "code_execution" trace step
(`roadmap/next.md:125-161`).
*Matters:* F9 undermines that premise; the ruling must be re-taken (B6).

**F34. A run's trace is written only at the end.** `TraceRecorder` keeps steps in memory
(`api/estimation_runs.py:157-255`); full outputs go to `estimation_trace_payloads` at terminal, and until then the
row holds a pending stub. A worker death leaves only `llm_calls` and cohort rows. The SPA polls every 2–3 s and
renders `Timeline` with a payload expander (`frontend/src/components/estimation/Timeline.tsx`).
*Matters:* live progress and resume need a step journal written as the run goes (B7, B9).

**F35. `read_floor_plan` is dead in production.** `_run_agent_path` never passes `building_run_id` or attachments,
so the handler raises outside the building flow (`api/agent.py:1269-1276`; `api/estimation_runs.py:1740-1750`),
yet both skills allow the tool.
*Matters:* the redevelopment analyst needs it revived on run inputs (B6).

**F36. Other LLM work has no run record and no account.** Outreach drafting runs up to 200 sequential calls inside one
HTTP request (`api/routes/outreach.py:116-130`; `api/outreach.py:158-181`); building extraction is one vision call;
the refiner bills to the estimation run (`api/refiner.py:109`). `llm_calls` has no `account_id`, by design
(`migrations/347_wave1_estimation_source_and_llm_calls_read.sql:14-16`).
*Matters:* per-account metering needs a run id on every agent call (B8, B13).

## D. Jobs and durable execution

**F37. The estimation job lane is dark; runs execute inside the API.** `estimation_job_lane_enabled` and
`agent_estimation_concurrency_cap` are absent from `app_settings` (code defaults: lane off, cap 3). No run has ever
been claimed by the lane: `job_payload` and `claimed_at` are NULL on all 106 rows. `POST /estimations` runs as a
post-response `BackgroundTasks` (`api/estimation_runs.py:420-446, 855-870`).
*Evidence:* live.
*Matters:* the durable path exists in code but has never run in production (B7).

**F38. The lane cannot queue.** It claims the oldest pending row once per 5 s pass, global FIFO, with no per-account
fairness (`scraper/realtime_worker.py:1621-1698`). `sweep_stuck_runs` fails pending rows by age, 10 min at API boot
and 15 min in the worker, and cannot tell a long run from a dead one (`api/estimation_runs.py:1096-1128`;
`api/main.py:134-147`). Statuses are pending, running, success and failed; there is no cancel, retry, resume or
progress.
*Matters:* "hundreds of requests without assuming all execute immediately" is structurally impossible today (B7).

**F39. The building fan-in breaks under the lane.** With the lane on, children come back pending;
`_finalise_building` no-ops while any child is non-terminal (`api/building_runs.py:317-330`) and nothing calls it
again, so the parent sits in "estimating" until a sweep fails it.
*Matters:* multi-agent work needs a fan-in hook (B4, B7).

**F40. The best queue in the repo is the push delivery ledger.** In `notification_deliveries`, `next_attempt_at` is
both the lease and the retry clock. The claim takes the due rows of at most 10 devices (a fairness grouping) with
`FOR UPDATE SKIP LOCKED` and a 2-minute lease (`api/push_delivery.py:76-96`); every later write is a compare-and-set
on (id, attempts) (112-117); backoff runs to 5 attempts. Live tests prove that concurrent claims never share a row
and that a stale claimer cannot overwrite a newer outcome (`tests/test_notifications_push_live.py`).
*Matters:* the run queue can copy this, re-keyed from device to account (B7).

**F41. Leases are pooler-safe but bespoke.** Doctrine: session advisory locks strand over the transaction pooler; use
a lease row with a compare-and-set, or a transaction-scoped advisory lock (database skill; e.g.
`toolkit/property_carriers.py:138`). Five lease implementations exist: `property_maintenance_lease`,
`broker_resolution_lock`, `location_jobs` (cadence, concurrency group, runner, lease holder and expiry, last outcome,
failure streak; `migrations/384_location_w1_serving.sql:566-584`), `autodedup/rt_lease.py`, and the push lease.
*Matters:* PR #2000 consolidates scheduled-job leases on `location_jobs` (F11); agent work must not add a sixth (B7).

**F42. The worker's lane harness and brakes are reusable.** `_lane_loop` re-reads its interval on every wake (0 or
less idles it) and caps a pass at 1800 s; `_supervised` restarts a crashed lane; a watchdog calls `os._exit(1)` on a
stalled heartbeat; dark lanes default to interval 0 (`scraper/realtime_worker.py:2921-2980` for the watchdog,
`2991-3110` for `_lane_loop` and `_supervised`, `3171-3181` for the zero default).
*Matters:* a new service gets supervision, heartbeats and a zero-default brake by reuse (B7).

**F43. Database headroom is ample, and the pooler has rules.** Postgres 17.6; `max_connections` 380 with about 30
sessions in use; pg_cron 1.6.4 with 5 jobs; pgmq not installed; no database branches. The transaction pooler caps
statements near 2 minutes and breaks LISTEN/NOTIFY and session advisory locks. Each API request opens a fresh
connection (`api/dependencies.py:28-35`).
*Evidence:* live; database skill.
*Matters:* a run must not hold a connection while it waits on a model (B7).

**F44. Request-time limits.** Railway's edge closes a request after 5 minutes with no data, caps a streaming request
at 15 minutes and closes idle HTTP/1.1 connections 60 s after a response. The SPA's single transport deadline is
130 s (`frontend/src/lib/api.ts:63-69`). Claude's hosted connectors allow 240 s per tool call.
*Evidence:* RESEARCH §7.4, §3.2.
*Matters:* long work must be submit-then-poll on every surface (B7, B10).

## E. Business capabilities

**F45. Most business capabilities exist as functions; few are services.** See T1. Comparables, distribution,
velocity, neighbourhood, ad history, freshness, summaries, yield, reference rent, brokers, curation, pipeline cards
and watchdogs all exist in Python. The toolkit returns a `{data, metadata}` envelope and raises no `HTTPException`.
*Matters:* the capability layer is mostly thin adapters (B2).

**F46. There is no headless property search, and there are three filter translators.** Browse compiles filters in
browser TypeScript into PostgREST predicates (`frontend/src/lib/registryQueryBuilder.ts:1-90`, plus about 25
hand-coded filters in `frontend/src/lib/queries.ts`). The stats and map RPCs take about 90 positional parameters.
Python's `compile_filter_where(values, PROPERTIES_GRAIN)` serves the watchdog matcher through `_build_match_clauses`
(`api/notifications.py:235`). Rule 16 pins the three per shared predicate only.
*Matters:* "find properties matching X" has a server-side core only in Watchdog semantics (B2, B3).

**F47. No get-property service; sold comps, stats and city indexes have no Python or HTTP face.** The property page
is assembled in the browser from about six views (`properties_public`, `listings_public`, `images_public`, …);
`sold_comparables` and `browse_stats_properties` are SPA-only RPCs.
*Matters:* agents and MCP clients need one composite dossier operation (B2, B3).

**F48. The API write modules are tied to HTTP.** They raise `HTTPException` (curation 28 times, attachments 16,
pipeline 14, property merge 12, building runs 11, estimation runs 7) and take `UploadFile`; `api/notifications.py`,
`api/outreach.py`, `api/ingest.py` raise none. `ComparableFilters` is hand-built in five places.
*Matters:* registry handlers need domain errors mapped once per transport (B2, B3).

**F49. Valuation exists at three levels.** Our estimation runs (`create_estimation_run`, rule 12);
`estimate_yield` (`api/estimate_yield.py:58`) and the MF reference rent `compute_reference_rent`
(`toolkit/rent_map.py:362`, no envelope); and the paid outside Valuo valuation, written ahead into
`valuo_valuations` before the call (`migrations/637_valuo_valuations.sql:34-52`).
*Matters:* the write-ahead row is the pattern for every paid or consequential agent step (B2, B8).

**F50. Standing rules bound what an agent may do.** Merges are ordered only by the operator or the autodedup engine
(rule 15). There are exactly three notification producers, which write facts, never routing (rule 16). City indexes
serve Browse and Watchdog only, never the estimation agent (rule 17). Curation is property-grain and follows merges
through `PROPERTY_CARRIERS` (rule 18). Every per-m² figure comes from the measure function with its basis (rule 23).
Condition levels are parked, and no agent fact reads them (rule 14).
*Evidence:* `CLAUDE.md:157-243`.
*Matters:* each agent's effect ceiling must respect these (B2, B8).

## F. Storage, files and ledgers

**F51. One R2 bucket and one key pair hold everything.** Listing photos, display copies, user files, content-addressed
page bodies, database backups and model weights share one bucket on the default, non-EU endpoint. One key pair sits
on the API, the worker and 35 workflows (`scraper/image_storage.py:288-415`). `upload_bytes` stamps
`public, max-age=2592000` on private objects too.
*Matters:* no agent or sandbox may ever see this key; artifacts need their own bucket and token (B8, B9).

**F52. User files are private and API-proxied, but the browser chooses the storage key.** `property_attachments`
(migration 660) is property-grain, under RLS on `acting_account_ids()`, with a MIME allowlist that excludes HTML, SVG,
scripts, Markdown and DXF, 25 MB per file, served with `attachment` and `nosniff`
(`api/attachments.py:37-68, 153-164`). `authenticated` may INSERT (`migrations/660_property_attachments.sql:155`; no
UPDATE grant), the API's own insert
runs as that role through `tenant_conn`, and `storage_key` has no prefix or uniqueness check. Keys name the property,
not the account (`custom-attachments/property/{property_id}/{uuid}`, `api/attachments.py:198`), and a share recipient
sees them through the shared-read policy (660:151-153), so a recipient who has seen a key could insert a row of their
own under that property and keep reading the owner's bytes after the share ends. Deletes
remove the row only.
*Matters:* an artifact store must give browsers SELECT only and hide keys (B9).

**F53. Supabase Storage is unused.** No code calls it; storage.buckets holds 0 rows.
*Evidence:* grep; live.
*Matters:* choosing it would add a second object store (B9).

**F54. Audit ledgers exist for estimations, money, merges, delegation and alerts; retention is prose.** See T4. Only
two automated prunes exist: `detail_queue_completions` at 7 days (`scraper/db.py:2465-2490`) and health check results
at 35 days.
*Matters:* run steps and artifacts need a retention class from day one (B9).

**F55. There is no erasure path.** No route deletes an account. Deleting an `auth.users` row cascades only
`account_members`. `usage_ledger` and `valuo_valuations` reference `accounts(id)` with no ON DELETE, so an account
delete would fail. File bytes are never deleted.
*Matters:* stored agent outputs and correspondence raise the GDPR stakes (B9, B11).

**F56. There is no generic run ledger and no artifact store.** "Run" exists only as `estimation_runs` and
`building_runs`. No table links a generated file to a run, a skill version, a model or a property; runs store JSONB
only.
*Matters:* the core of B9 needs building.

## G. Brokers and correspondence

**F57. The broker graph is large and API-only, with one masking chokepoint.** 24,979 active brokers, 17,043 with a
primary e-mail (live). Two layers, `broker_identities` and `brokers`, plus firms and contacts
(`migrations/185_broker_identity_foundation.sql:26-145`). Every broker view is revoked from browser roles
(`migrations/299_phase0_anon_hardening.sql:325-347`). `apply_pii_policy` masks e-mail and phone for non-admins
(`toolkit/brokers.py:66-157`), and a census pins it (`tests/api/test_broker_routes.py:457-505`).
*Matters:* broker reads wrap as tools as they are (B2, B11).

**F58. `POST /brokers/by-listings` returns unmasked contacts to every signed-in caller.** By operator ruling of
2026-10-18 (PR #1867): up to 1,000 ad ids per call, no rate limit (`api/routes/brokers.py:129-136`).
*Matters:* with open signup (F13), an agent or MCP path to it would be a bulk contact export (B8, B10).

**F59. The outreach CRM is global, manual and never used.** `outreach_campaigns`, `outreach_messages` and
`broker_outreach_suppression` have no `account_id` and are admin-only (`migrations/193_outreach_crm.sql:10-54`;
`api/routes/outreach.py`). "Send" is a `mailto:` link plus a "mark sent" button; any status is accepted and the body
stays editable after send (`api/outreach.py:232-257`). Suppression is keyed by broker, not address, and lapses when a
suppressed broker loses a merge. Nothing dedupes by address: 414 primary e-mails are shared by 3,894 active brokers,
and one address is the primary for 1,202. 0 campaigns, 0 messages, 0 suppressions (live).
*Matters:* B11 starts from a clean slate; the dedupe and merge lessons carry over.

**F60. There is no mail transport, by ruling and by deletion.** `channel_sends` and the Resend tables were dropped
(`migrations/656_notifications_w6_prune.sql`). Ruling R2b (2026-10-21) keeps notification channels to the in-app
feed and iPhone push, with e-mail kept inactive as a future push-ledger channel. The legal gates for per-tenant
outreach (DPIA, LIA, Art. 14, "Act 480/2004 opt-in before ANY per-tenant outreach") are listed and unstarted
(`docs/design/waves-1-4-public-features.md:93-109`).
*Matters:* outbound broker e-mail is a new channel class and needs a ruling (B11).

**F61. The parts a correspondence ledger needs exist elsewhere.** The drafting pattern: model and prompt in
history-tracked `app_settings`, a forced `record_outreach_draft` tool with a strict schema, every call in `llm_calls`
(`api/outreach.py:28-40, 165-174`). The push ledger's lease, compare-and-set and provider message id (F40). The
Stripe webhook's HMAC, replay window and idempotency table (`api/routes/billing.py:94-139`). `cryptography` is
already a dependency (`api/push.py:22-25`).
*Matters:* the first mail stage is mostly reuse (B11).

## H. Surfaces: SPA, Phone app, extension

**F62. The SPA reads directly under RLS and writes through the API.** Vite 5, React 18, TanStack Query, Tailwind v4,
supabase-js. Two adapters share one error model: `send()` with the 130 s deadline (`frontend/src/lib/api.ts:195-272`)
and `pgRead()` with 20 s (`frontend/src/lib/pgRead.ts:62-107`). Reads hit about 28 `*_public` views and RPCs; every
write goes through the API. `frontend/src/lib/api.ts` is 5,007 lines of hand-written wrappers; types are mirrored by
hand in the SPA and the extension; there is no OpenAPI codegen.
*Matters:* RLS plus the SQL functions are today's shared rule across surfaces; the registry adds a third face, not a
replacement (B3).

**F63. No chat, no agent UI, no streaming, no CSP.** There is no chat or assistant UI; no SSE, WebSocket or Supabase
Realtime anywhere; run pages poll at a fixed 2 s (`frontend/src/pages/EstimationDetail.tsx:29-38`) and unread counts
at 60 s, without backoff. The Caddyfile sends nosniff, Referrer-Policy and X-Frame-Options, but no
Content-Security-Policy (`frontend/Caddyfile:65-72`). There is no Markdown renderer.
*Matters:* rendering agent output that may echo injected ad text needs a CSP and a sanitiser first (B7, B8, B9).

**F64. The mobile application is the installed SPA.** The Phone app wraps the same routes in a phone frame below
768 px, with six tabs (`frontend/src/lib/phoneShell.ts:1-109`); routes without a phone design render inside the
frame. The phone property page cannot start runs. Push is Apple-only by design, and 0 devices are subscribed (live).
There is no native app.
*Matters:* any new route (runs, approvals, chat) appears in the Phone app for free (B1, B3).

**F65. The extension already starts metered agent runs.** Manifest V3, vanilla TypeScript, its own PKCE session,
every fetch through the background worker (`chrome-extension/src/api.ts:66-131`), a closed union of 26 message
types. Its estimation call sends mode agent and source extension (`chrome-extension/src/api.ts:196-206`), then polls
every 2 s up to 180 times (`chrome-extension/src/content.ts:77-82`); a 429, or a quota already spent, becomes an
upgrade prompt (`chrome-extension/src/content.ts:2091-2097, 1771-1794`). Its message listener ignores the sender
(`chrome-extension/src/background.ts:70-91`).
*Matters:* the estimation contract cannot change under it, and any extension sink needs a sender check (B3, B8).

**F66. Settings › Skills edits production rows from the SPA.** It edits `system_prompt`, `allowed_tools`,
`preferred_model` and `limits` in place (`frontend/src/pages/Settings.tsx:359-499`, the `SkillEditor`; its save at
474-480); `updated_by` is always
"settings_ui" (`api/routes/admin.py:149`).
*Matters:* it conflicts with "authored and tested in GitHub" (B4, B13).

## I. CI, tests, evals and observability

**F67. CI is strong and nearly full.** Five required checks (pytest, schema-and-sql, vitest, build, docs-budget).
pytest takes 7 m 53 s of its 9-minute limit (`.github/workflows/test.yml:19`). `.github/workflows/migrations.yml`
replays every migration into Postgres 17 with PostGIS, pgvector and the Supabase roles, then runs about 49 live tests,
including tenant isolation; "there is no staging DB" (`.github/workflows/migrations.yml:3-8`). No Python lint or type
check;
no end-to-end test in CI.
*Matters:* agent evals need their own job, and the replay database is a free eval bed with real SQL and real RLS
(B12, B13).

**F68. The repo is public, and its secrets are not fenced.** Repository secrets include `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `QWEN_API_KEY` and the production `SUPABASE_DB_URL`; no workflow uses a protected GitHub
environment. PR #2000 proposes making the repo private (F11).
*Matters:* paid evals stay off `pull_request` and behind a protected environment; production-derived eval cases need
PII scrubbing while the repo is public (B12, B13).

**F69. Eval-like harnesses exist, all outside agents.** `_ScriptedProvider` and `_FakeConn` drive the real loop in CI,
parametrised over anthropic and gemini only (`tests/api/_fakes.py:111-201`; `tests/api/test_agent.py`).
`scripts/smoke_agent.py` is manual, has never been dispatched, and writes production with a raw INSERT (rule 12's
owed fork). Elsewhere: the autodedup harness, seals and offline judge (`autodedup/harness.py`, `autodedup/seals.py`,
`autodedup/judge.py`); preregistrations with Wilson intervals; the text-extraction bake-off; characterisation goldens
"blessed with numbers in the PR" (`tests/scraper/test_attribute_characterisation.py:1-12`); production-aggregate
replays (`tests/scripts/test_checks_platform_replay.py`).
*Matters:* the B12 harness is assembly, not invention.

**F70. Agents have no eval data, scorers, judges, review queue or adversarial cases.** Feedback is free text on one
run (`api/feedback.py:27-57`; 3 rows live). The deterministic estimation mode is never used as an oracle. Tool data is
JSON-dumped straight back to the model (`api/agent.py:1494-1500`), and no test feeds hostile ad text through it.
*Matters:* B12 needs building.

**F71. Observability covers cost and errors, not outcomes or latency.** `llm_cost_hour_rollup` feeds `/costs`;
`check_llm` cells and `ops_incidents` drive the Health ladder and the hourly canary e-mail. `duration_ms` is stored but
not rolled up; trace steps do not carry `llm_call_id`; the API has no request id, error tracker or OpenTelemetry; the
`CalledFor` Literal has drifted from the database CHECK (`api/llm_client.py:55-74`).
*Matters:* monitoring agents in production is a few columns and check cells away (B13).

**F72. Census rails are how this repo keeps an invariant.** Five census tests count a population and fail on anything
their reasoned allowlist does not name, and each states its own blind spots. The tenant table registry is the only
place browser DML is allowed (`tests/_tenant_registry.py`; `tests/test_migration_rls_grants.py:495-541`).
*Matters:* every agent invariant (classes declared, no identity in tool schemas, one claims builder) can be a census
(B4, B8).

## J. Governance

**F73. Git-canonical definitions already work elsewhere.** Portal contracts are YAML with a `contract_version`; a lock
file pins version and sha256; CI runs a `--check`; a deploy-time projection stores `git_ref` and refuses changed bytes
under a known version (`location_data/contract_lock.py`; `tests/location_data/test_contract_immutability.py:52-98`).
It was built after an unbumped edit killed 14 runs. The text lane stamps `extractor_version()` (schema version, sha
of prompt and schemas, model) on every output (`toolkit/description_extraction.py:108-119`).
*Matters:* skills and agents can copy both as they stand (B4, B13).

**F74. Binding rules for the agent layer.** Rule 1 (migrations append-only), 7 (no dependency without a reason), 12
(`estimation_runs` is the single source of truth for estimations; the row is created at submit by
`create_estimation_run`; its result is immutable; sources ui, api, clickup, extension), 13 (building runs), 14, 15,
16, 17, 18, 22 (pipeline writes through the JWT-gated API; never a remove toggle), 23 and 25; and the toolkit's
"facts, not opinions" (`CLAUDE.md:118-255`; toolkit-api skill).
*Matters:* every proposed change names the rule it touches.

**F75. Scope lines and rulings in force.** `ROADMAP.md:63` lists "MCP server wrapping the toolkit" as out of scope
until opened, and `ROADMAP.md:64` a public read API. `POST /tools/find_comparables` stays for outside automation
until an MCP tool replaces it (ruling 2026-09-21). R2b (2026-10-21) fixes the notification channels.
*Matters:* MCP and broker e-mail need explicit rulings (B10, B11).

**F76. The vocabulary is ahead of the code.** GLOSSARY's AI-work terms (Assistant, Workflow, Skill, Run, Area
takeoff, Plugin; `GLOSSARY.md:292-314, 435-437`) arrived with PR #1703, whose planning lives in a private repo this
audit cannot read. "Broker contact record" and "Area takeoff" exist only in the glossary. docs/adr/ does not exist.
*Matters:* the program adds Agent, Tool and Saved workflow by ruling (B4).

## T1. Capability catalog

Tenancy today: **SR** = service role, market-wide; **TC** = `tenant_conn` under RLS; **SR+acct** = service role
with an explicit account predicate. "Class" is the proposed effect class, one of the nine in PROGRAM §3.5
(read_market, market_cache, read_tenant, own_collection, write_tenant, costly, external, code, admin); "n/a" marks a
row that is not a registry entry.

| Capability | Existing code | Tenancy | Exposed today | Verdict | Class |
|---|---|---|---|---|---|
| Property search | `browse_list_visible` + TS filters; Python `compile_filter_where` via `_build_match_clauses` | RLS / SR | SPA RPC; watchdog matcher | new search_properties on the watchdog compiler | read_market |
| Property detail | `*_public` views; `load_location` | views / SR | SPA direct; `GET /properties/{id}/location` | new get_property_dossier (market facts) and get_my_property_context (the user's notes, cards, collections) | read_market; read_tenant |
| Comparables | `find_comparables`, `find_comparables_relaxed`, `find_comparables_along_axis` | SR | `/tools/*`; agent | wrap | read_market; along-axis = market_cache |
| Distribution, outliers, clusters | `analyze_distribution`, `find_distribution_outliers`, `cluster_comparables` | pure / SR | `/tools/*`; agent | wrap | read_market |
| Velocity | `compute_market_velocity`, `compute_listing_velocity` | SR | `/tools/*` (sreality-only body); agent | wrap; ad-id adapter | read_market |
| Neighbourhood | `describe_neighborhood`, `find_anchor_amenities`, `compute_walkability`, `compute_amenity_supply` | SR | `/tools/*`; agent | wrap | read_market (describe); market_cache (walkability, amenity supply) |
| Ad history | `compare_snapshots` | SR | `/tools/compare_snapshots`; SPA view | wrap | read_market |
| Freshness | `verify_listing_freshness` | SR, writes | `/tools/*` (sreality-only) | adapter | market_cache, live fetch |
| Summaries, image compare | `summarize_listing`, `compare_listing_images` | SR, writes | `/tools/*`, `/listings/summaries` | adapter + metering | market_cache, LLM cost |
| Yield | `estimate_yield`; `compute_reference_rent`; `update_scenario` | SR / TC | `POST /estimate_yield` (no caller); scenario PATCH | wrap; envelope for reference rent | read_market |
| Our valuation runs | `create_estimation_run`, `execute_pending_run` | SR+acct | `/estimations*` (SPA, extension) | keep; executor moves (rule 12) | read_tenant, cost class llm (start_run, get_run; admitted and metered) |
| Valuo | `new_valuation` in `api/valuo.py` | SR+acct | `/properties/{id}/valuo` | adapter + approval | costly |
| Building paste | `create_building_run`, `confirm_units`, `extract_building_units`, `read_floor_plan` | SR, account-blind | `/buildings*` | admin-gate now; rebuild or retire | admin (deny); `read_floor_plan` becomes read_tenant on run inputs |
| Dedup merge / split | `merge_property_set`, `detach_listings`, `split_property` | SR, admin | admin routes; autodedup | never an agent tool (rule 15) | admin (deny) |
| Filter definitions | `filter_registry`, `to_jsonschema_properties` | n/a | agent schemas; TS codegen | wrap: the schema source | n/a |
| Stats, map | `browse_stats_properties`, `browse_map_cells`, `stats_registry` | RLS | SPA RPC | adapter later | read_market |
| Sold comps | `sold_comparables`, `sold_coverage` | RLS | SPA RPC | new adapter | read_market |
| City indexes | `curated_cities_public`, city index views | RLS | SPA; watchdog filters | Browse and Watchdog only (rule 17) | n/a |
| Pipeline | `add_card` and siblings (`api/pipeline.py:225`) | TC + acting | `/pipeline/*` | thin adapter | write_tenant |
| Curation | `create_collection`, `add_properties_to_collection`, `create_note`, `attach_tag`, dismissals | TC | REST | thin adapter | write_tenant; own collection |
| Attachments | `add_attachment`, `read_attachment` | TC, private R2 | `/properties/{id}/attachments*` | adapter (`UploadFile`) | read_tenant (run inputs) |
| Watchdogs | `create_subscription` and CRUD; producers `match_once` and siblings | TC / SR | `/notifications/subscriptions*` | wrap | read_tenant; write_tenant |
| Notifications | `list_dispatches`, `get_unread_count` | TC | `/notifications/*` | wrap reads; no new producer | read_tenant |
| Brokers | `leaderboard`, `search`, `get_broker`, `broker_listings`, `listing_brokers`, `apply_pii_policy` | SR | `/brokers/*` (masked unless admin; by-listings unmasked) | wrap, masking always on | read_market |
| Correspondence | `api/outreach.py` | SR, admin, no account | `/outreach/*` | rebuild as a tenant ledger | write_tenant (drafts); external (sending) |
| Documents | `render_property_sheet` (`api/property_sheet.py:368`), the download zip | SR + TC | `/properties/{id}/download.zip` | wrap: an artifact producer | read_tenant |
| Ad lookup, ingest | `lookup_portal_listings`, `ingest_link` | SR / TC | `/listings/lookup`; `/listings/ingest*` (admin) | wrap lookup | read_market |

## T2. Routes by gate (static walk of every handler at bdf2d5034; unchanged at main @ 3f0ff5b8a, 275 handlers at both)

| Gate (strongest reachable) | Count | Examples | For the agent layer |
|---|---|---|---|
| `require_admin` | 147 | `/admin/*`, merge and split, outreach, autodedup | never exported to agents or MCP |
| `tenant_conn`, RLS only | 53 | curation reads, UPDATE/DELETE by id, scenario PATCH | tenant reads and writes |
| `tenant_conn` + `require_acting_account_id` | 13 | curation writes | write_tenant |
| `tenant_conn` + `require_account_id` | 7 | own-account writes | write_tenant |
| `require_account_id`, then service role | 3 | `/push/*` writes | n/a |
| `verify_jwt` on the service role | 13 | `/brokers/*` (9), Valuo (2), `POST /estimations`, feedback | broker masking (F57, F58) |
| `account_scope` | 2 | `GET /estimations`, latest-by-listing | n/a |
| `require_token` | 40 | 16 `/tools/*`, 10 `/buildings*`, 3 `/maps/*`, `/listings/summaries`, `/estimate_yield`, 4 price-stats, 2 map overlays, 2 property location, `GET /push/key` | spend (F17), IDOR (F15) |
| Open | 3 | `/health`, `/images/{key}`, `/billing/webhook` | n/a |

About 281 route-method pairs at runtime: 275 decorated handlers plus six saved-set handlers mounted twice.

## T3. Job primitives

**Hosts.**

| Host | What runs | Limits | Reuse for agents |
|---|---|---|---|
| API process | requests; boot sweep; 60 s alerts teller; `BackgroundTasks` (estimations, building fan-out) | one uvicorn; every deploy kills in-flight work | submit and approval routes only |
| realtime-worker | 17 lanes, one asyncio supervisor, 32 threads | 21.5 / 24 GB peak; redeploys every merge | harness (F42), never agent code |
| GitHub Actions | 84 files: 34 scheduled, 5 CI, 45 dispatch-only | crons throttled to about hourly; public logs | CI and evals |
| pg_cron | 5 jobs (read-model rebuilds) | at most 8 concurrent, 10 min each | pure SQL only |
| RunPod | GPU pods | terminated in a finally block; heartbeats to Postgres | lifecycle rules for any VM |
| batch-worker (proposed, PR #2000) | scheduled jobs from the JOBS registry | 8 GB, 70 % aggregate admission, subprocess per job | the trigger tick for saved workflows |

**Queues.**

| Queue | Claim | Lease and retry | Fairness | Cancel, resume |
|---|---|---|---|---|
| `listing_detail_queue` | three reserved classes, SKIP LOCKED | `claimed_at`; attempts; given up at 5; reclaim after 30 min | priority classes | n/a |
| `estimation_runs` lane | one oldest pending row per 5 s | none; age sweeps at 10 and 15 min | global FIFO | none |
| `notification_deliveries` | due rows of at most 10 devices, SKIP LOCKED | `next_attempt_at` is lease and retry; CAS on (id, attempts); 5 attempts | per device | n/a |
| `dirty_properties` | set semantics, ON CONFLICT DO NOTHING | daily full sweep as backstop | n/a | n/a |

**Leases:** `property_maintenance_lease` (one row, CAS, 15-minute TTL); `broker_resolution_lock` (with a heartbeat);
`location_jobs` (lease holder and expiry, cadence, outcome; the basis of PR #2000's scheduled_jobs);
`autodedup/rt_lease.py` (commit-time fence, release on SIGTERM); the push lease. Transaction-scoped advisory locks:
`toolkit/system_alerts.py`, `toolkit/property_carriers.py:138`.

**Brakes and deadlines:** `app_settings` intervals re-read on every wake, dark lanes at 0 (F42); per-portal kill
switches; agent loop limits (F30); the vision batch's dollar ceiling (`toolkit/vision_batch.py:106`); drains at 120 s.

**Admission:** estimation in-flight cap 3, monthly quota, idempotency through a unique partial index
(`migrations/355_estimation_metering.sql`), a `usage_ledger` row per metered success.

## T4. Ledgers and storage

| Ledger | Shape | Retention today |
|---|---|---|
| `estimation_runs` | INSERT at submit, terminal UPDATE; `parent_run_id` for re-runs; skill name and version | forever |
| `estimation_trace_payloads` | one row per (run, step), never updated | "safe to delete after 30 days", no prune |
| `estimation_cohort_entries` | harness-written comparable set with `snapshot_id` | forever, cascades with the run |
| `llm_calls` | tokens, cost, duration, `estimation_run_id`; no prompt text, no account | forever; hourly rollup |
| `usage_ledger` | one metered action per row | forever; account FK without ON DELETE |
| `valuo_valuations` | pending row before the paid call, one terminal UPDATE, verbatim response | forever; service role only |
| `skills`, `skills_history` | trigger snapshots every prior version | forever |
| `app_settings_history` | same trigger pattern | forever |
| `property_merge_events`, `property_merge_carries` | merge chokepoint ledger and carry records | forever |
| `delegated_edits` | one trigger on eleven curation tables; owner-read | cascades with the owner account |
| `notification_dispatches`, `notification_deliveries` | events append-only for producers; leased deliveries | cascade with event and device |
| `ops_incidents` | one row per failing-cell signature | no prune |
| `outreach_messages` | draft, approved, sent; mutable after send | admin-only, no account |
| `stripe_webhook_events` | idempotency | forever |
| `listing_snapshots` | append-only on content change (rule 2) | never deleted (rules 3, 8) |

**Object storage (one R2 bucket, F51):** img/{listing_id}/{image_id}.jpg and thumb/w1080/… (photos);
custom-attachments/property/{pid}/… and custom-attachments/building/{run}/… (user files); payloads/{source}/…
(content-addressed page bodies, a template for artifact versioning: `location_data/payloads.py:8-45`);
backups/…; models/…. Public access only through `/images/{key}`, which whitelists listing-photo shapes
(`api/routes/images.py:146-156`). Supabase Storage: unused (F53).

## T5. Exists vs needs building, per brief point

| Brief point | Exists | Needs building |
|---|---|---|
| B1 Audit | this file | n/a |
| B2 Service layer | toolkit and API functions (T1), the envelope, the PII chokepoint | one registry; search_properties, get_property_dossier, a sold-comps adapter; domain errors |
| B3 Headless | REST for writes, RLS views for reads, the extension through the API | registry surfaces for agents and MCP; tool schemas from one source |
| B4 Tools, skills, agents | `_ToolDef`, `skills` rows, SKILL.md files, the contract-lock pattern | agent files, locks, git as source of truth, child runs and fan-in |
| B5 Runtimes | the provider-neutral loop, five providers | a generic loop, an executor host, hard per-call caps |
| B6 Code execution | `reportlab`, attachments | renderers, a code runner, a network-denial gate |
| B7 Orchestration | the dark estimation lane, the push queue pattern, the lane harness, `location_jobs` | run and step ledgers, fair claim, leases, cancel, resume, triggers, chat |
| B8 Security | JWT, RLS, tenant pool, delegation, censuses | run principal, effect classes, approvals, taint, rate limits, the F12/F15/F16 fixes |
| B9 State and artifacts | estimation trace and payloads, private R2 attachments | an artifact table, an EU bucket, retention classes |
| B10 MCP | nothing (out-of-scope line) | adapter, OAuth, limits, call ledger |
| B11 Mailboxes | a global manual CRM, the drafting pattern, the push ledger | a tenant ledger, an outbox, inbound mail, Gmail |
| B12 Eval framework | the scripted provider, the judge pattern, goldens, the replay database | cases, scorers, a security suite, judges, a review queue |
| B13 Evals in dev and prod | CI, `llm_calls`, the Health ladder | eval jobs per tier, version pinning, outcome cells |

## Open questions the audit could not settle

1. Does the live `skills` text match any git text? (F31 says no for the estimator; one aggregate query settles the
   others.)
2. Does the container probe allow seccomp filters or Landlock (F9)? This decides whether agent-written code can run
   on Railway containers at all.
3. Can a Supabase OAuth-server token read PostgREST directly, or call `/auth/v1/user`? (RESEARCH §3.3 marks it
   unknown.)
4. What is the live value of `CORS_ALLOW_ORIGINS`, and is `API_AUTH_OPTIONAL` unset on Railway? (Values were not
   read.)
5. What does the private planning repo behind PR #1703 hold about Plugins and assistant skills (F76)?
