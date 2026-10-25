# Agent platform: the MVP path (rearranged plan, 2026-10-25)

> **Status: PROPOSAL, awaiting operator review.** This file rearranges [PROGRAM.md](./PROGRAM.md)'s committed
> path (W0 → W1 → spike → W2, about 5–6.5 weeks) into **M0 → M1 → M2, about 3 weeks**, after the operator's
> instructions of 2026-10-25: the set of agents is open-ended and not yet defined; the foundations must let new
> agents be added that use the app's data and features; results first, scaling later, no overkill. PROGRAM.md
> stays the reference design for everything after M2; §6 below says where each deferred piece went, so nothing is
> dropped silently. Vocabulary: *module, interface, seam, adapter, depth, leverage, locality* as in the
> `codebase-design` skill; domain words from `GLOSSARY.md`.

*Produced with the `/improve-codebase-architecture` lens over the current code and the plan; the review report with
before/after diagrams is published beside the reading page. Four readers walked the agent layer, the data surface,
the write features and the plan item by item; their reports fed this file.*

## 0. What changed, in one paragraph

The registry seam PROGRAM proposes is already real: twelve toolkit functions have two hand-written adapters today
(a `/tools/*` route body and an agent `_ToolDef`), and the two copies already disagree. What stops a second agent
is not a missing registry but that every agent handler reads the estimator's private loop state
(`api/agent.py:1137-1341`). So the MVP builds the registry with explicit-argument handlers and a generic loop, hosts
the executor as a lane inside the API process, lets agents **read** the app's data through three composite
operations and **act** on it through *propose-then-apply* (the agent writes nothing; the user clicks Apply), and
defers every piece that fails the deletion test at today's scale: three database logins, lock files, dollar
reservations, priority claims, journal resume, the approval state machine, a fourth Railway service, the
artifacts bucket, the E1/E2 eval tiers. Each deferred piece keeps the trigger PROGRAM already names.

## 1. What keeps the agent set open

An agent is added by adding files, never Python, when four things hold:

1. **One registry of operations** (`api/capabilities/`): each entry is a name, a description, a Pydantic argument
   model that refuses unknown keys, a `handler(ctx, args)`, a tenancy (`market` or `tenant`) and an effect (`read`,
   `write`). `registry.call(ctx, name, raw_args)` validates, picks the connection, maps errors and truncates. The
   loop, later MCP, and later the REST routes are adapters at this seam; today the seam has two adapters already,
   so it is real, not hypothetical.
2. **Handlers take explicit arguments** (a property id, an ad id, a point), as the REST bodies already do. The
   estimator's implicit `target`, `base_filters` and `last_cohort` become either arguments or a run-scoped
   `ctx.scratch`. This deletes `_dispatch_tool` and `_FCR_OVERRIDE_FIELDS`, which silently drops bad values.
3. **The definition is the skill folder.** `skills/<name>/SKILL.md` frontmatter carries the agent configuration
   (tools, model per provider, limits, input schema, output schema) and the body is the instruction set. That keeps
   the brief's three concepts distinct (tool = registry entry; skill = the body; agent = the frontmatter) in one
   folder. A separate `agents/*.yaml` arrives only when one agent composes several skills; a lock file arrives when
   a second author or an unbumped-edit incident asks for it. The Dockerfile gains one `COPY skills/` line; a run
   pins the definition text, the tool-set sha and `RAILWAY_GIT_COMMIT_SHA` at submit.
4. **One generic loop and one terminator.** The loop renders the input as the opening message, enforces caps on
   iterations, cost (summed from `llm_calls` by run id) and wall clock, and ends on `submit_result(markdown,
   cited_property_ids, proposed_actions)`. The server checks that every cited id appeared in this run's tool
   outputs and that every proposed action names a registry write entry whose schema it passes. The estimator keeps
   its own `record_estimate` finaliser until it moves onto the new loop (W3).

Three composite reads give any agent the app's data without new code: `search_properties` (a third adapter at the
`compile_filter_where` seam, beside comparables and the watchdog), `get_property_dossier` (market facts for up to
20 properties, each per-m² figure with its basis) and `get_my_property_context` (the account's collections, notes,
tags and pipeline card, under RLS). Their input schemas are generated from the filter registry, which already
carries 76 field descriptions. Model-written SQL is **rejected** for the MVP: RLS identifies the user from a session
setting that any SELECT could rewrite with `set_config`, session state leaks through the transaction pooler, and
free joins over a million rows cost too much (see the research lens; reopen only as an admin-only tool with a parser
dependency).

Five reversible writes make agents useful: create a collection, add properties to a collection, add a note (a
viewing is a note with a date), add a pipeline card at the entry stage, create a watchdog from a filter. In the MVP
they are **Apply targets**, not agent tools: a run ends with proposed actions, the Runs page renders each as a
server-drawn diff, and Apply runs the registry entry under the user's own `tenant_conn`, once (an `applied_at`
compare-and-set). Each write declares its one-call inverse, so "Undo" is a reverse replay when it is wanted.

## 2. What the MVP builds, and what it reuses

| Piece | Reuses | Adds | Leaves for later (trigger) |
|---|---|---|---|
| Registry | `to_jsonschema_properties`, the toolkit envelope, `tenant_transaction` | `api/capabilities/` with about 8 read entries and 5 write entries | REST generated from the registry (a second HTTP consumer); MCP (W5) |
| Definitions | `api/skills.py` validators, the SKILL.md format | a loader over `skills/` in the image; pinning at submit | `agents/*.yaml` (multi-skill agents); lock file + `--check` (second author) |
| Loop | `LLMClient`, the five providers, `_ScriptedProvider` | `run_loop(definition, input, ctx, recorder)`, `submit_result` | the estimator on the new loop (W3) |
| Run ledger | the `_insert_run` idempotent shape, `Timeline`, `RunHistory` | `agent_runs`, `agent_run_steps` (append-only diary), `llm_calls.agent_run_id` | status trigger; `capability_calls` (MCP) |
| Executor | the push-delivery lease pattern, the lane harness, `system_alerts_loop` as the lifespan precedent | a two-slot lane in the API process; FIFO claim with a per-account cap; a 2-minute lease; retry from the start, twice; a daily dollar ceiling; a brake (absent = dark) | agent-worker (concurrent runs > 8, API memory > 2 GB, an incident); priorities and reserved slots; journal resume (runs > 5 min or mid-run approvals); dollar reservations (when the allowlist opens) |
| Who may start runs | `app_settings` | an **allowlist of accounts** (absent = nobody) checked at `POST /runs`; X-Act-As refused | plan budgets and trial caps (AR14, when the list opens) |
| Tenancy | `tenant_transaction` with a claims dict | `read_only=True` keyword; `SET LOCAL statement_timeout = '8s'`; a row cap; one claims builder | the three narrow logins and the grant census (agent-worker leaves the API process) |
| Acting on results | `api/curation.py`, `api/pipeline.py`, `api/notifications.py` functions as they are | propose-then-apply; `check_filter_spec` moved into `create_subscription`; `add_properties_to_collection` returns the added ids | approvals as parked steps (mid-run asks); autonomous reversible writes with Undo (AR9 re-take after trust) |
| Results | the run row | `result_md` rendered as pre-wrapped text with property links from cited ids | the EU R2 bucket, `agent_artifacts`, the sanitiser, the CSP (a workbook or PDF is asked for) |
| Evals | `_ScriptedProvider`, the golden pattern | E0 cassettes + a security subset in the existing pytest job; `scripts/agent_eval_live.py` (10 cases, $2 cap) | E1 (200-run queue test), E2 (weekly judges) |
| Surfaces | `Timeline` | a minimal Runs page: a start form, the step feed, the result, proposed actions with Apply | chat, SSE, the Phone-app approvals inbox, saved workflows and the trigger job (#2000) |

Not in the MVP at all, with their PROGRAM home: code tiers (§3.3, AR4), mail (§3.8), MCP (§3.7), the artifacts
bucket (§3.6), narrowing GUCs and the W8 database binding (§3.5).

## 3. Sequence

**M0, days 1–3: close the stranger-spend holes.** Two pull requests, no agent code.
- Revoke the browser's INSERT and UPDATE on the run tables, keeping the `scenario` column grant (PROGRAM §2.1 item 4).
- `/buildings*` admin-only (item 5); `verify_jwt` pins the issuer, requires `sub`, `exp`, `role`, refuses `client_id`
  (item 6); boot refuses `API_AUTH_OPTIONAL` under `RAILWAY_ENVIRONMENT` (item 7); `force_refresh` admin-only (item 8).
- The run-start allowlist setting, so M1 can go to production dark.
- In parallel, the operator's items: credential rotation (item 2) and the Railway IaC move (item 1, due 2026-11-15,
  coordinated with the Actions-exit program's W0/W2; whoever lands first does it).

*Exit:* a trial JWT gets 403 on `/buildings` and on `force_refresh`; a PostgREST INSERT into `estimation_runs` is
denied while the scenario PATCH still works; boot refuses `API_AUTH_OPTIONAL` under `RAILWAY_ENVIRONMENT`; a
foreign-issuer token gets 401.

**M1, weeks 1–2: the spine and the first agent.**
- *M1a (days 2–6, which is also the spike):* `api/capabilities/` with the read entries (`search_properties` with
  status and recency hooks on a search grain, `get_property_dossier`, `get_my_property_context`,
  `find_comparables_relaxed`, `analyze_distribution`, `compute_market_velocity`, `estimate_yield`,
  `describe_neighborhood`); the read-only helper; the loop with its caps and `submit_result`; the skills loader;
  `skills/regional_scout_v1/`; `scripts/agent_run.py` with a record mode, run locally against production data
  read-only. The operator grades 10 shortlists on two models (gpt-5.6-luna, then qwen). Below 7 of 10, change the
  skill, not the spine.
- *M1b (days 6–12):* one additive migration (`agent_runs`, `agent_run_steps`, `llm_calls.agent_run_id`); the
  executor lane in the API lifespan (2 slots, lease, FIFO with a per-account cap, the daily ceiling, the brake);
  `POST /runs` (allowlist), `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/steps`, `POST /runs/{id}/cancel`; the E0
  cassettes and the security subset in pytest; one Health cell (queue depth, oldest queued, restarts).

*Exit:* the operator starts a scout run in production from a minimal page (a form plus `Timeline`); a trial account
gets 403 on `POST /runs`; a redeploy mid-run completes with attempts = 2; a $0.01 ceiling parks runs; the cassette
replays in CI; the cross-tenant tests are green; the run row carries the definition sha, the tool-set sha, the
commit sha, the provider and the model.

**M2, week 3: acting on results, and the second agent.**
- The Runs page: result, steps, proposed actions rendered as server-drawn diffs, Apply (once) under the user's
  `tenant_conn`, Undo where the inverse exists.
- The five write entries registered as Apply targets; `check_filter_spec` moved into `create_subscription`.
- A **second, different agent** added without touching the loop, the executor or the registry: for example a
  pipeline brief that reads `get_my_property_context` and the dossier for every card on the board and proposes
  notes.
- `scripts/agent_eval_live.py`.

*Exit:* the second agent's PR touches only `skills/` and `tests/`; a double Apply writes once; Apply under another
account's JWT gets 404; 10 live cases cost under $2; the operator rates at least 7 of 10 outputs useful.

**After M2:** PROGRAM's usage gate (AR20) decides what follows. The order of the later waves is unchanged: W3
(estimator onto the new loop, saved workflows through #2000's scheduler, chat), W4 (artifacts and renderers), W5
(MCP after the domain cutover), W6 (tier-2 code, only with proven network denial), W7 (mail), W8 (hardening).
agent-worker, the narrow logins, reservations and journal resume each move from "W2" to "on trigger".

**Total committed: about 3 weeks**, against 5–6.5 in PROGRAM §7.

## 4. Rulings this path needs now

| # | Ruling | Recommendation |
|---|---|---|
| AR8 (fallback) | Executor host | the lane in the API process now; agent-worker on PROGRAM's own triggers |
| MVP-A (new) | Who may start runs | an `app_settings` allowlist: the operator plus one test account; AR14 waits until the list opens |
| AR9 (simplified) | Agent writes | the agent writes nothing; every effect is a proposed action the user applies; autonomous reversible writes with Undo are the next step, by ruling |
| AR3 | Models | gpt-5.6-luna default, qwen second; both through the existing providers |
| AR6 | Run ledger | `agent_runs` beside `estimation_runs`; the estimator untouched until W3 |
| AR5 (partial) | Definitions | new agents live in git only; the estimator's drifted skill waits for W3 |
| MVP-B (new) | Search semantics | agent search = the watchdog's `compile_filter_where`, plus status and recency on a search grain; curation scopes come from `get_my_property_context` (AR7 by default) |
| MVP-C (new) | Model-written SQL | not in the MVP; reopen only as an admin-only analysis tool |

Not needed before M2: AR1 runs in parallel; AR4, AR7, AR10–AR19 wait for their waves; AR20 after M2.

## 5. Deletion tests on the largest deferrals

- **agent-worker (a fourth Railway service).** Delete it: the executor lane runs in the API lifespan, where
  `system_alerts_loop` already runs. What is lost is memory isolation (the API peaks at 1.25 of 24 GB today) and
  credential separation (no renderers or untrusted code run in the MVP). A new service also cannot exist before the
  IaC cutover, because new services cannot use Config as Code. Reinstate on PROGRAM's triggers.
- **Three narrow database logins + grant census.** One adapter each, so hypothetical seams. Agent reads run as the
  run's principal through the read-only helper; the toolkit's 13 tools stay on the service role as today.
- **Approval state machine.** With propose-then-apply the executor never writes tenant rows, so there is nothing to
  park, bind or consume. The machinery returns when an agent must ask mid-run.
- **Journal resume.** Retry from the start, at most twice, costs a few cents per crash at MVP run sizes. Resume
  returns for runs over about 5 minutes or $0.50, or with mid-run approvals.
- **Lock files.** The commit sha pinned into the run row answers "which version ran". A lock returns with a second
  author or the first unbumped-edit incident.
- **Dollar reservations, priorities, reserved slots.** With an allowlist of two accounts, a per-account in-flight
  cap, a per-run token cap and a platform daily ceiling, concurrent overspend is bounded by the ceiling.
- **The artifacts bucket.** A Markdown result in the run row, rendered as text, serves the scout and the brief. The
  bucket returns with the first workbook or PDF.

## 6. Where each PROGRAM piece went

| PROGRAM piece | MVP | Trigger to reinstate |
|---|---|---|
| §2.1 Wave 0 items 1–2 (IaC, rotation) | parallel, operator | deadline 2026-11-15 |
| §2.1 items 3 (probes), 9 (extension sender check), 10 (attachment key trigger) | later | before tier-2 code; before any extension sink; before shares grow |
| §2.1 items 4–8 | **M0** | n/a |
| §3.1 registry, explicit arguments, error shim in dispatch | **M1a** | n/a |
| §3.1 cost class, `market_cache` class, REST generated from the registry | dropped / later | MCP or a second HTTP consumer |
| §3.2 skills in git, pinned at submit | **M1a** | n/a |
| §3.2 `agents/*.yaml`, lock files, read-only Settings editor, refiner PRs | later | multi-skill agent; second author; W3 |
| §3.3 generic loop, hard caps | **M1a** | n/a |
| §3.3 agent-worker, code tiers, Sandbox rung | later / never for MVP | PROGRAM's triggers; AR4 |
| §3.4 run + step tables, lease, cancel, FIFO claim with account cap, daily ceiling | **M1b** | n/a |
| §3.4 admission reservations, priorities, journal resume, status trigger, waits, fan-in, triggers, chat | later | allowlist opens; long runs; multi-agent; #2000; demand |
| §3.5 principal stamped at submit, one claims builder, read-only reads, no act-as on runs | **M1b** | n/a |
| §3.5 effect classes beyond read/write, approvals, narrow logins, narrowing GUCs, W8 binding | later | mid-run asks; agent-worker; paying tenants |
| §3.6 artifacts | later (result_md now) | first workbook or PDF |
| §3.7 MCP, `capability_calls` | W5 | domain cutover + a user asks |
| §3.8 mail | W7 | demand + rulings |
| §3.9 E0 cassettes + security subset | **M1b** | n/a |
| §3.9 E1, E2, judges, review queue, Langfuse | later | PROGRAM's triggers |
| §8 proof of concept criteria 1–2 (200-run queueing, interactive p95) | later | before the allowlist opens |
| §8 criteria 3–10 | **M1b / M2** exits | n/a |

## 7. Open questions for the operator

1. Propose-then-apply first (recommended), or autonomous reversible writes with Undo from day one?
2. The second agent: a pipeline brief over your board, a redevelopment memo without code, or a correspondence
   drafter that only drafts? Any of the three touches no Python.
3. Who is on the run-start allowlist besides your account?
