# Agent platform: the MVP path (rulings taken 2026-10-25)

> **Status: RULED 2026-10-25 (operator grill, `/grill-with-docs`, three rounds). Ready for `/to-spec`.** This file
> replaces [PROGRAM.md](./PROGRAM.md)'s committed path (W0 → W1 → spike → W2) with **M0 → M1 → M2 → M3, about 7
> weeks**, after the operator's instructions: the set of agents is open-ended; agents get the same access to the
> app's data and features as humans, or deeper; results first, scaling later; the first agents must run Python and
> store files. PROGRAM.md stays the reference design for what comes after; § 6 says where each deferred piece went.
> Decisions that are hard to reverse are ADRs in `docs/adr/` (0001–0007). Vocabulary: `GLOSSARY.md` (Agent and Tool
> were added by this ruling) and the `codebase-design` skill (module, interface, seam, adapter, depth).

*Produced with the `/improve-codebase-architecture` lens over the code and the plan (review report beside the reading
page), then grilled with the operator. Round-by-round answers are in § 4.*

## 0. What changed, in one paragraph

The registry seam PROGRAM proposes is already real: twelve toolkit functions have two hand-written adapters (a
`/tools/*` route body and an agent `_ToolDef`), and the copies disagree. What stops a second agent is that every
agent handler reads the estimator's private loop state (`api/agent.py:1137-1341`). So the MVP builds the registry
with explicit-argument handlers and one generic loop; hosts the executor in a fourth Railway service, agent-worker,
once the Railway config move is done; gives agents **parity** with the screens (every database function a screen
calls, every dataset we hold, under the user's own login) and the deeper analytics the screens never show; lets
agents act through **propose-then-apply** (the agent writes nothing; the user clicks Apply); runs agent Python on
**Claude's hosted code execution** with files in **Supabase Storage**; rebuilds the obsolete estimator on the same
spine as the scout, as two agents over one valuation core; and defers what fails the deletion test at today's scale.

## 1. What keeps the agent set open

An agent is added by adding files, never Python, when these hold:

1. **One registry of operations** (`api/capabilities/`): an entry is a name, a description, a Pydantic argument model
   that refuses unknown keys, a `handler(ctx, args)`, a tenancy (`market` or `tenant`) and an effect (`read`,
   `write`, `costly`, `code`). `registry.call(ctx, name, raw_args)` validates, picks the connection, maps errors and
   truncates. The loop, later MCP and later REST are adapters at this seam, which already has two adapters today.
2. **Parity (ADR-0002).** Every database function the screens call (Browse search over all its filters, the stats
   dashboard, map counts and overlays, sold comparables, collections, pipeline, notes, brokers) is a registry entry
   run under the user's own login through `tenant_transaction`. Regional datapoints, including the curated city
   indexes, are readable by agents that rank; no price estimate reads them (rule 17, amended). Broker contacts are
   what the user sees, at most 20 per call, every read recorded on the run. A screen capability an agent lacks is a
   bug. **Typed operations only (ADR-0003):** no model-written SQL; the forms cover what the screens do.
3. **Handlers take explicit arguments** (a property id, an ad id, a point, filter ids); the estimator's hidden
   `target`, `base_filters` and `last_cohort` become arguments or a run-scoped `ctx.scratch`. `_dispatch_tool` and
   `_FCR_OVERRIDE_FIELDS` are deleted.
4. **The definition is the skill folder.** `skills/<name>/SKILL.md`: frontmatter = the agent (model per provider,
   skills, tools, limits, input and output schema), body = the skill; `references/` holds the operator's context
   (the customer-group page, the photo-facet list), versioned with it. Loaded from the image (one `COPY skills/`);
   pinned into the run at submit with the tool-set sha and `RAILWAY_GIT_COMMIT_SHA`. A separate `agents/*.yaml`
   arrives with the first multi-skill agent; a lock file with a second author.
5. **One generic loop and one terminator.** The loop renders the input, enforces caps on iterations, cost (summed
   from `llm_calls` by run id) and wall clock, and ends on `submit_result(markdown, cited_property_ids, files,
   proposed_actions)`. The server checks that every cited id appeared in this run's tool outputs and that every
   proposed action names a registry write entry whose schema it passes.
6. **Propose-then-apply (ADR-0004).** The executor writes no tenant rows. Proposals (a collection, members, a note or
   viewing, a pipeline card at the entry stage, a watchdog, a draft e-mail) are rendered as server-drawn diffs on the
   Runs page; Apply runs the entry under the user's own `tenant_conn`, once (`applied_at` compare-and-set); each
   write declares its inverse for Undo. Autonomous reversible writes are the next ruling, not this one.
7. **Code and files (ADR-0005, ADR-0006).** An agent whose definition allows `code` gets Claude's hosted code
   execution tool in its model calls (Claude only: the analyst core runs on Claude Opus 5.5 to start, evals decide
   whether Sonnet 5.5 holds); inputs go in through Anthropic's Files API, outputs come back as files and are stored
   in **Supabase Storage** (one bucket, rows in an artifacts table linked to the run, RLS by account, read directly
   by the website). This re-takes Phase 7d's exclusion of provider-hosted code; the operator accepted the
   consequences: US processing with 30-day retention at Anthropic for what a code run sends, and Claude-only for
   code-running agents. The 2026-08-27 memo now reads: cheap pool for the high-volume lanes; agents choose per agent.

## 2. The first agents

**One valuation core, two agents.** The core is a set of registry entries any agent may call; the scout and the
rebuilt estimator are two definitions over it; the drafter is a third agent on the cheap pool.

| Entry (new unless marked) | What it answers |
|---|---|
| `search_properties` (Search agenda = every Browse filter, incl. status, recency, time on market) | find properties matching X, with card facts and per-m² figures with their basis |
| `aggregate_properties` (over the stats dashboard's own function) | median price per m² and counts by cohort: new build, renovated panel flat 40–50 m², disposition, locality level |
| `market_evidence` | prices achieved: ads that went inactive after **2–60 days on market by default** (the agent may widen the window with a stated reason, e.g. luxury segments), last price before delisting as the proxy, labelled as a proxy; plus the reas.cz registered sales we hold |
| `get_property_dossier` | everything we know about ≤ 20 properties: canonical ad, location, history, estimates, sources, brokers as the user sees them |
| `get_my_property_context` | the user's collections, notes, tags and pipeline card for those properties |
| `assess_property_visuals` (ADR-0007) | a structured report from the ad's photos and floor plan: view, neighbourhood as seen, shared spaces, and for renovated flats kitchen, bathrooms, equipment, finish quality, lighting, styling; a score and a one-line evidence note per facet; cached per photo set and prompt version; ≤ 10 properties per run; facet list v1 drafted by Claude from the operator's brief, edited by the operator in `references/`; first 30 properties run on Opus 5.5 and Sonnet 5.5, the cheaper kept if they agree |
| `find_comparables_relaxed`, `analyze_distribution`, `compute_market_velocity`, `compute_listing_velocity`, `estimate_yield`, `compute_reference_rent`, `describe_neighborhood`, map overlays and counts (wraps) | the analytics the screens show or never show |
| `valuo_valuation` (wrap, `costly`) | Valuo's paid outside valuation: at most 3 calls per run, a monthly cap per account, every call recorded (the write-ahead row exists) |
| `run_python` (Claude hosted, `code`) | spreadsheets, financial models, renovation-arbitrage tables (2+1 → 3kk), charts; outputs to Supabase Storage |

**Scout v1 (M1).** Input: a region (town or district codes) and criteria (price band, size, disposition, yield
floor); the customer-group context from `references/` when the operator supplies it (later; the scout starts without
it). Output: a ranked shortlist with a reason per property, under- or over-priced verdicts against cohort statistics
and achieved prices, an optional workbook; every property drawn from search output, every figure recomputed by the
toolkit; proposals: a new collection with the shortlist. Graded by the operator: 7 of 10 shortlists useful.

**Estimator v2 (M2).** The same core; new instructions in git; the `estimation_runs` record and the `/estimations*`
contract unchanged first (the website and the Chrome extension keep working), then a richer output (value range,
confidence, verdict, evidence list) with their screens; the `skills` table, the Settings editor, `AGENT_TOOLS`, the
realtime-worker estimation lane and the `BackgroundTasks` path are deleted when the gate passes.

**Drafter (M3).** Proposes a draft (subject, body, broker) from the dossier, the user's notes and the pipeline card;
the user sees the draft with an "open in my mail" link and sends it; no thread tracking until mail stage M1.

## 3. Sequence

**M0, days 1–5: the floor, and the move agent-worker needs.**
- Two pull requests: revoke the browser's INSERT/UPDATE on the run tables (keep the `scenario` column grant);
  `/buildings*` admin-only; `verify_jwt` pins the issuer, requires `sub`, `exp`, `role`, refuses `client_id`; boot
  refuses `API_AUTH_OPTIONAL` under `RAILWAY_ENVIRONMENT`; `force_refresh` admin-only; the run-start allowlist
  setting (absent = nobody): hejtmanekp@gmail.com, k.haskova@gmail.com and the test accounts the build needs.
- **The Railway config move (Config as Code → IaC) for the three services, by this programme, this week.** No
  Actions-exit W0 ticket (#2008–#2018) holds it; #2008's repo transfer only re-links GitHub, so the two do not
  collide. agent-worker cannot be created before this is done.
- The remaining credential rotation through a guided script (`/wizard`): the database password and the two DSNs, the
  Supabase service key, the R2 key, Stripe, the GitHub token, Mapy, Valuo, the smoke-test accounts; database last.
  Already rotated: the AI provider keys, RunPod, Cloudflare. VAPID is new and stays.

*Exit:* a trial JWT gets 403 on `/buildings` and `force_refresh`; a PostgREST INSERT into `estimation_runs` is denied
while the scenario PATCH works; boot refuses `API_AUTH_OPTIONAL`; a foreign-issuer token gets 401; `railway config
plan` shows no diff for all three services after a forced redeploy; every rotated secret has one verification.

**M1, about 3.5 weeks: the spine, agent-worker, parity, code and files, the scout in production.**
- *M1a (days 2–9):* `api/capabilities/` with the entries of § 2 (the Search agenda in `filter_registry`, the stats
  mapper with a census that every dashboard parameter is mapped, `market_evidence`, the dossier and context reads,
  the broker reads with the 20-contact cap); the read-only helper (`tenant_transaction(read_only=True)`, 8 s
  statement timeout, row cap); the skills loader; the generic loop and `submit_result`; the Anthropic provider
  carries the hosted code tool and the Files API; `skills/regional_scout_v1/` with `references/` slots;
  `scripts/agent_run.py` with a record mode, run locally against production data read-only. The operator grades 10
  shortlists on Opus 5.5 and, where no code is needed, on gpt-5.6-luna. Below 7 of 10, change the skill, not the spine.
- *M1b (days 10–24):* one additive migration (`agent_runs`, `agent_run_steps`, `agent_artifacts`,
  `llm_calls.agent_run_id`, the visual-assessment cache, the allowlist and the brake); agent-worker as a Railway
  service in the API project (IaC; same image, its own start command; 2 slots to start; a 2-minute lease; FIFO claim
  with a per-account cap; a platform daily dollar ceiling; retry from the start, twice; the brake absent = dark);
  agent-worker holds the tenant DSN for the claims builder and, until the narrow logins arrive, the service DSN for
  the toolkit's base-table reads; the Supabase Storage bucket and the artifact rows; `assess_property_visuals`;
  `valuo_valuation` with its caps; `POST /runs` (allowlist), `GET /runs`, `GET /runs/{id}`, steps, cancel; the
  minimal Runs page (a form, `Timeline`, the result, files, proposals with Apply and Undo); E0 cassettes and the
  security subset in pytest; a live eval script (10 cases, a $5 cap); one Health cell.

*Exit:* the operator starts a scout run in production from the Runs page and gets a shortlist with a workbook; a trial
account gets 403 on `POST /runs`; a redeploy mid-run completes with attempts = 2; a $0.01 ceiling parks runs; the
cassette replays in CI; the cross-tenant tests are green; a model-supplied account id is refused by the schema census;
a double Apply writes once and Undo reverses it; a cross-account file read returns nothing; the run row carries the
definition sha, the tool-set sha, the commit sha, the provider and the model; Valuo stops at the third call.

**Usage gate (AR20), after M1:** the operator rates 7 of 10 shortlists useful, or starts scout runs weekly. Then:

**M2, 2–3 weeks: the estimator rebuilt.** Estimator v2 on the spine (§ 2), the estimation record kept, both rows
inserted in one transaction at submit (rule 12), the old paths deleted after 30 estimations and 7 quiet days; then
the richer output and its screens. *Exit:* the extension's metered runs are unchanged; the SPA shows the new output;
the `skills` table and the Settings editor are gone.

**M3, 1 week: the drafter.** The `draft_email` proposal kind, the mailto handoff, broker contacts as the user sees
them. *Exit:* a draft names only the broker the server resolved; the agent's PR touches only `skills/` and `tests/`.

**After M3:** PROGRAM's later waves on their triggers (saved workflows through #2000's scheduler, MCP after the domain
cutover, mail stage M1 with the reply subdomain, narrow logins, reservations and priorities when the allowlist opens,
journal resume for long runs, the approval state machine for mid-run asks).

**Total: about 7 weeks** (M0 one week, M1 three and a half, M2 two to three, M3 one).

## 4. Rulings taken (grill of 2026-10-25)

| # | Question | Ruling |
|---|---|---|
| Q1 / AR2 | Vocabulary | **Agent** and **Tool** added to the glossary; "agent profile" moved off the avoid list |
| Q2 / AR8 | Executor host | a fourth Railway service, agent-worker, **now**, after the IaC move |
| Q3 / MVP-A | Who may start runs | allowlist: hejtmanekp@gmail.com, k.haskova@gmail.com, test accounts; absent = nobody |
| Q4 / AR9 | Agent writes | propose-then-apply; agents write nothing (ADR-0004) |
| Q5 / AR3 | Models | gpt-5.6-luna default for text agents; qwen3.7-flash second; code agents on Claude Opus 5.5 to start, evals decide; any vendor per agent when evals justify |
| Q6 / AR6 | Run ledger | `agent_runs` + `agent_run_steps` beside `estimation_runs` (ADR-0001) |
| Q7 / AR5 | Estimator | rebuilt entirely on the spine (M2); `skills` table and Settings editor retired |
| Q8, Q14 / MVP-B | Search and data access | **parity**: every screen function and dataset, under the user's login (ADR-0002) |
| Q9, Q15 / MVP-C | Model-written SQL | no; typed operations only (ADR-0003) |
| Q10 | First agent | the regional scout as an investment analyst (§ 2) |
| Q11, Q23 | Second agent after the estimator | the correspondence drafter, drafts only, mailto handoff |
| Q12 / AR20 | Commitment | M0–M3; usage gate after M1 |
| Q13, Q24 / AR1 | Rotation and IaC | remaining secrets via a wizard (VAPID excluded); IaC by this programme in M0 |
| Q16 | Estimator shape | same job and record first, richer output second; one core, two agents |
| Q17, Q30, Q33 | Condition from photos | a new structured photo assessment for agents, cached, ≤ 10 per run; facets v1 drafted by Claude, edited by the operator; the parked 1–5 scorer stays parked; Browse untouched (ADR-0007; rule 14 amended) |
| Q18 | Valuo in runs | allowed: ≤ 3 calls per run, monthly cap per account, recorded |
| Q19 | Achieved-price evidence | inactive ads after 2–60 days on market by default, the agent may widen with a reason; last price as a labelled proxy; plus registered sales |
| Q20 | Regional data for agents | all of it, including city indexes for ranking; never inside a price estimate (rule 17 amended) |
| Q21, Q27, Q28 / AR4 | Python | Claude's hosted code execution, in M1; Phase 7d's exclusion re-taken (ADR-0005); US processing accepted |
| Q22 / AR16 | Files | Supabase Storage (ADR-0006; reverses PROGRAM D11) |
| Q29 / AR13 | Broker contacts for agents | as the user sees them, ≤ 20 per call, recorded; MCP stays masked |
| Q31 | Writing it down | ADRs now; rules 14 and 17 amended in CLAUDE.md on this PR |
| Q26, Q32, Q34 | Customer-group context | later; a page of prose into the scout's `references/`; the scout starts without it |

## 5. Deletion tests on the deferrals

- **Three narrow database logins + grant census:** one adapter each; agent-worker holds the tenant DSN and, for
  the toolkit's base-table reads, the service DSN. Reinstate before the allowlist opens to strangers.
- **Approval state machine:** nothing to park or consume while the executor writes nothing. Returns with mid-run asks.
- **Journal resume:** retry from the start, twice; returns for runs over about 5 minutes or $0.50.
- **Dollar reservations, priorities, reserved slots:** two allowlisted accounts, a per-account cap, a per-run token
  cap and a platform daily ceiling bound spend. Return when the allowlist opens.
- **Lock files, `agents/*.yaml`, `capability_calls`, the status trigger:** the commit sha, one skill per agent, the
  step journal and the brake answer the same questions.
- **The code-runner Railway service and the Sandbox rung (PROGRAM §3.3):** not needed while code runs on Claude's
  hosted tool; reopen if an agent must run code on a non-Claude model or on our own infrastructure.
- **The EU R2 artifacts bucket (PROGRAM D11):** replaced by Supabase Storage (ADR-0006).

## 6. Where each PROGRAM piece went

| PROGRAM piece | Now | Trigger to reinstate |
|---|---|---|
| §2.1 items 1–2 (IaC, rotation) | **M0**, this programme | n/a |
| §2.1 items 3 (probes), 9 (extension sender check), 10 (attachment key trigger) | later | before our own code runner; before any extension sink; before shares grow |
| §2.1 items 4–8 | **M0** | n/a |
| §3.1 registry, explicit arguments, error shim | **M1a** | n/a |
| §3.1 cost class, `market_cache` class, REST from the registry | dropped / later | MCP or a second HTTP consumer |
| §3.2 skills in git, pinned at submit | **M1a** | n/a |
| §3.2 `agents/*.yaml`, lock files, refiner PRs | later | multi-skill agent; second author |
| §3.3 generic loop, hard caps; agent-worker | **M1a / M1b** | n/a |
| §3.3 code tiers, code-runner, Sandbox rung | replaced by Claude hosted code (ADR-0005) | a non-Claude code agent |
| §3.4 run + step tables, lease, cancel, FIFO claim, daily ceiling | **M1b** | n/a |
| §3.4 reservations, priorities, journal resume, status trigger, waits, fan-in, triggers, chat | later | allowlist opens; long runs; multi-agent; #2000; demand |
| §3.5 principal at submit, one claims builder, read-only reads, no act-as on runs | **M1b** | n/a |
| §3.5 effect classes beyond read/write/costly/code, approvals, narrow logins, GUC narrowing, W8 binding | later | mid-run asks; strangers on the allowlist; paying tenants |
| §3.6 artifacts | **M1b**, Supabase Storage | n/a |
| §3.7 MCP, `capability_calls` | W5 | domain cutover + a user asks |
| §3.8 mail | M3 drafts; W7 for the ledger and inbound | demand + rulings |
| §3.9 E0 cassettes + security subset; live script | **M1b** | n/a |
| §3.9 E1, E2, judges, review queue, Langfuse | later | PROGRAM's triggers |
| §8 criteria 1–2 (200-run queueing, interactive p95) | later | before the allowlist opens |
| §8 criteria 3–10 | **M1b** exits | n/a |

## 7. Open items

1. The customer-group context page (operator, later).
2. The facet list v1 for the photo assessment (Claude drafts in M1a; operator edits).
3. The remaining credential rotation (operator, with the wizard, M0).
