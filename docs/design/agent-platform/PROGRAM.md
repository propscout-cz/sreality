# Agent platform: one capability layer, durable runs that act as the user

> **Status: PROPOSAL, awaiting operator review (2026-10-25). No build, no migration, no deployment until reviewed.**
> The as-is audit is [AUDIT.md](./AUDIT.md) (findings F1–F76 and inventories T1–T5, cited below); the external
> evaluation is [RESEARCH.md](./RESEARCH.md) (cited as RESEARCH §n). Twenty-two rulings are owed (§ 9: AR1–AR20,
> AR12 in three parts); eight are needed before the proof of concept. Wave 0 (§ 7) is worth doing whether or not the
> rest is approved. **The program commits only to W0, W1, a 3-day scout spike and W2;** every later wave waits for a
> demand trigger (§ 7).
>
> **Rearranged MVP path (2026-10-25, after the operator's instructions: open-ended agent set, results first, no overkill):**
> [MVP.md](./MVP.md) re-sequences the committed path into M0 → M1 → M2 → M3 (about 8 weeks; rulings taken 2026-10-25, MVP.md § 4)
> and says where every deferred piece of this document went. This file stays the reference design for everything
> after M3.

*Operator brief of 2026-10-25 (13 points); `main` @ 3f0ff5b8a. Ten audits and eight research tracks fed three drafts,
scored by three judges; this synthesis builds on the most reuse-heavy draft, and § 4.1 lists where it changed course.
Four reviewers (citations, security, operability, brief coverage) then checked it; § 4.2 lists what that changed.*

## Reading guide

| Brief point | Answered in |
|---|---|
| 1. Audit the existing architecture | § 2 (summary, today's topology); AUDIT.md (F1–F76, T1–T5) |
| 2. A reusable business service layer | § 3.0 (the product, worked backwards); § 3.1 (the capability registry); AUDIT T1 |
| 3. Headless, API-first | § 3.1 (surfaces); § 3.7 (MCP) |
| 4. Tools, skills and agents; multi-skill and multi-agent | § 3.2; § 3.4 (child runs, fan-in) |
| 5. Runtimes and hosting | § 3.3; § 4 (D1–D3); RESEARCH §1, §2 |
| 6. Lightweight and code-executing agents | § 3.3 (two code tiers); AR4 |
| 7. Asynchronous orchestration | § 3.4 |
| 8. Multi-tenant security and permissions | § 3.5; Wave 0 (§ 2.1, § 7) |
| 9. Persistent state and artifacts | § 3.4 (the step journal); § 3.6 |
| 10. External MCP interface | § 3.7; AR11 |
| 11. User mailboxes | § 3.8; AR12a–c |
| 12. Agent evaluation framework | § 3.9 (scorers, the bars table) |
| 13. Evals in development and production | § 3.9; the exit gates in § 7 |
| Decisions, alternatives, risks; roadmap, cost, proof of concept, rulings | § 4, § 5, § 6; § 7, § 8, § 9 |

## Terms

- **Agent** (new term, AR2): a configured specialist kept in one git file: which model, which skills, which tools, its
  limits, and when it must ask a human. Example: the regional investment scout.
- **Tool** (new term, AR2): one named platform operation that an agent, a REST call or an assistant can invoke, with a
  typed input and a factual output. Example: "find comparables".
- **Skill** (GLOSSARY): the instructions an AI follows to carry out a workflow; here also its templates and scripts.
- **Workflow, Run, Assistant, Plugin** (GLOSSARY): the work a user starts; one execution of it; the user's own Claude
  or ChatGPT; the package that connects that assistant to us.
- **Saved workflow** (new term, AR2): a user's saved workflow, with its input and an optional schedule or watchdog,
  kept like a filter preset.
- **Principal**: the user and account a run acts for, stamped by the API when the run is submitted.
- **MCP** (Model Context Protocol): the standard way ChatGPT and Claude call outside tools.
- **OAuth**: the sign-in hand-shake that gives an assistant a token to act for one user.
- **JWT**: the signed sign-in token every request carries. **RLS** (row-level security): database rules that show
  each account only its own rows.
- **Sandbox**: an isolated place where AI-written code runs with no secrets and no network. **Egress**: network
  traffic leaving a machine; denying it stops stolen data from leaving.
- **Durable execution**: each step of a run is written down before the next, so a crash or a deploy resumes it.
- **Lease**: a "mine for 2 minutes" stamp on a queued run, renewed while it works; a dead worker's run comes back.
- **Brake**: a setting whose default (0, or absent) keeps a new piece dark until the operator turns it on.
- **Approval**: a one-time yes from the user, bound to the exact action, before anything consequential happens.
- **Prompt injection**: text written by outsiders (ad descriptions, broker e-mails, PDFs) that tries to steer the
  model.
- **Eval**: a repeatable, graded test of agent output. **Trace**: a run's step-by-step record. **IaC**
  (Infrastructure as Code): Railway's new way to keep service settings in a file in git.
- **Census**: a test that lists a population (routes, tools, tables) and fails on anything not on its reasoned list.
- **Rule of Two**: an agent may hold at most two of (A) untrusted input, (B) private data, (C) the power to change
  state or send; holding all three (the "lethal trifecta") needs a human approval at the sink (RESEARCH §6.1).
  **Taint**: a run-level flag set once untrusted text enters the run. **Spotlighting**: wrapping that text in marked
  delimiters so the model treats it as data.
- **Names used here.** *Rulings* are AR1–AR20 (agent rulings); "R2b" is the existing notification-channel ruling, and
  "R2" alone always means Cloudflare's object store. *Eval tiers* are E0–E2; *code tiers* are "renderers" (tier 1) and
  "run_python" (tier 2). Other programs' waves carry their name: *Actions-exit W2* (PR #2000), *brand-domain W1*.
- **Platform words.** SPA: the web app. Proof of concept: the first end-to-end build that tests the risky parts.
  DSN: a database connection string, login included. Service role: the database login that bypasses RLS. PII:
  personal data such as broker phones. OOM: out of memory; the process is killed. Phase 7d: the 2026 roadmap entry that
  deferred agent code execution (`roadmap/next.md:125-161`). Plan agenda: the list of features a billing plan unlocks.
  Cheap pool: the low-price OpenAI and Qwen models used for high-volume lanes.
- **Database words.** SKIP LOCKED: a claim that skips rows another worker holds. ON CONFLICT: an insert that does
  nothing (or updates) when the row already exists, so a retry cannot duplicate. NOINHERIT, NOBYPASSRLS: login options
  that stop a role from borrowing others' rights or skipping RLS. GUC: a per-transaction database setting.
- **Sandbox words.** uid: the numeric user a process runs as; setuid switches to it. rlimits: per-process caps on
  memory, CPU, files and processes. seccomp: a kernel filter that refuses chosen system calls. Landlock: a kernel
  feature that limits which files and TCP ports a process may use.
- **Web and mail words.** PKCE: the OAuth variant for apps that cannot keep a secret. CSP (Content-Security-Policy): a
  browser rule listing what a page may load. SSE: a one-way stream of server events. HMAC: a keyed signature on a
  webhook body. MX, SPF, DKIM, DMARC: the DNS records that route mail and prove a sender. SES: Amazon's mail service.
  CASA: Google's yearly security assessment for restricted scopes. DPA: a data-processing agreement. ZDR: zero data
  retention at the model provider. DXF: a CAD drawing format.
- **Eval words.** pass^k: the share of cases that pass on all k repeated tries. Wilson interval: an honest error bar on
  a pass rate from few cases. Cassette: a recorded step journal replayed in CI.

## 1. North star

**Every surface (the SPA, the Phone app, the extension, the user's assistant over MCP, a schedule or an event) calls
one registry of tools, and every agent run acts as the user who started it, waits its turn instead of failing, writes
each step down as it goes, asks before anything consequential, and can be reproduced from git.**

1. **Bend before you build.** Every new service, dependency or vendor states why the existing piece cannot do the job.
2. **One capability layer,** promoted from the agent tool registry (F26), never written a second time.
3. **Every run acts as the user.** Tenant data is reached only as that user, under RLS; the model never names one.
4. **One scheduler, many queues.** PR #2000's JOBS registry decides *when*; queue tables hold *work*.
5. **Facts in ledgers, definitions in git.** Runs, steps and calls are append-only rows; agents and skills are files.
6. **Subtraction, dark by default.** Each wave names what it deletes and ships behind a zero-default brake.

## 2. What exists today and what is wrong

Live on 2026-10-25: 7 accounts (5 active in 30 days), 2 `POST /estimations` in 7 days, no outside API caller, no push
device; 393,030 active ads on 1,039,401 properties; $389 of LLM spend in 30 days, nearly all enrichment (F2, F29).

```
 SPA + Phone app (installed SPA, F64)          Chrome extension (F65)        no outside API caller (F2)
   | reads: supabase-js -> *_public views, RLS    | background worker only
   | writes: API, Supabase JWT                    | own PKCE session, JWT
   v                                              v
 +- project "vite" -+   +- project "API" -------------------------------------------+
 | Caddy, no CSP    |   | api: one uvicorn; REST; 16 /tools/*; /estimations          |
 | (F62, F63)       |   |  estimations + building fan-out run as BackgroundTasks     |
 +------------------+   |  (the lane is dark, F37); verify_jwt checks aud only (F12) |
                        |  service DSN + tenant DSN + 4 LLM keys + full R2 key (F7)   |
                        +--------------------------|---------------------------------+
                                                   | pooler, Amsterdam -> Ireland (F1, F43)
 +- project "realtime-worker" ---------+   +-------v-----------------------------------+
 | 17 lanes, one process, 21.5/24 GB   |-->| Supabase Postgres eu-west-1, RLS          |
 | peak (F3); dark estimation lane     |   | estimation_runs (browser-writable, F16),  |
 | (F37); service DSN, RunPod, VAPID   |   | skills (drifted, F31), llm_calls, ...     |
 +-------------------------------------+   +-------------------------------------------+
 Cloudflare R2: one bucket, one key (F51)    GitHub Actions: 84 workflows, public repo (T3, F68)
```

**Already built, in single-purpose form.**
- A provider-neutral agent loop with five providers and a server-derived, anti-hallucination result (F25, F28); a
  job-row queue on `estimation_runs` with metering, idempotency and `usage_ledger` (F24, F37).
- The best queue in the repo, `notification_deliveries`: lease, retry clock, fair claim, compare-and-set, live race
  tests (F40); the worker's lane harness and zero-default brakes (F42).
- `tenant_transaction` to act as one user under RLS; delegation with an actor ledger (F20, F21).
- Most business capabilities as Python functions (F45, T1); private R2 attachments (F52).
- Git-canonical contracts with a lock and a CI check, and prompt-hash stamping (F73); census rails (F72); the scripted
  provider, the offline judge and goldens for evals (F69).

**Wrong, by severity.**
1. **Open doors, independent of agents.** Browser-writable run tables (F16); the account-blind `/buildings*` routes
   (F15); a JWT check without issuer or client checks (F12); no production guard on `API_AUTH_OPTIONAL` (F19);
   unmetered spend behind open signup (F13, F17, F18).
2. **Two deadlines.** Railway stops reading `railway.json` on 2026-12-01 (F5); Railway advises rotating every
   credential stored before 2026-10-21 (F6).
3. **Runs cannot queue or survive.** Age sweeps fail waiting work; one claim at a time, global FIFO; no cancel, retry,
   resume or live progress; a racy cap; runs die with each deploy (F4, F24, F34, F37, F38).
4. **Everything runs on the service role.** No background path acts as the user (F20, F36).
5. **Skills: the database is canonical and has drifted;** edits reach production without an eval (F31, F66).
6. **Two tool registries; Browse semantics only in browser TypeScript** (F26, F46, F47).
7. **Missing:** code execution (F33), artifacts (F56), mail (F59, F60), MCP (F75), agent evals (F70).
8. **The realtime worker is no place for new work** (F3, F7).

### 2.1 Do now, regardless of this proposal

These ten items are worth doing even if nothing else here is approved. **This is the one list of Wave 0;** § 7 gives
its exit checks.
1. **Move the three services from `railway.json` to Infrastructure as Code by 2026-11-15** (Railway's hard cutoff is
   2026-12-01; F5; RESEARCH §7.3). Language: TypeScript (GA), one .railway/railway.ts file the Railway CLI evaluates;
   no Node code enters the Python tree. *Superseded by ruling Q24 (2026-10-25): this programme moves the three existing services in its M0 and
   Actions-exit W2 (#2025) keeps only its new runner service.*
2. **Rotate every credential stored on Railway before 2026-10-21** (F6; RESEARCH §7.7), from a per-secret consumer
   inventory: the API, the worker, the GitHub Actions secrets of the 34 scheduled workflows, the operator's local
   .env, any extension build. Dependency order, the database password last and in a quiet window; every consumer is
   updated before the old value is revoked; one verification per secret (a dispatched smoke workflow). Delete
   `RUNPOD_API_KEY` from the API (F7). Budget a full operator day.
3. **Run two probes and record them** (F9): the container probe on a throwaway service ("unshare -Ur true"; Seccomp
   and CapEff; the Landlock ABI; /dev/kvm), and a Sandbox probe (one Railway Sandbox in Amsterdam, outbound traffic
   dropped as root, the exfiltration checks run), because RESEARCH §6.4 marks the in-guest cut as untested.
4. **Close the browser write path on the run tables** (F16), keeping the one legitimate column, `scenario`. The PR
   updates the database skill and CLAUDE.md's frontend line: run tables, artifacts and correspondence become "writes by
   grant"; other tables stay "by convention".
5. **Close the account-blind `/buildings*` routes:** admin only (F15; AR19).
6. **Harden `verify_jwt`:** pin the issuer; require `sub`, `exp` and `role`; refuse tokens that carry client_id (F12).
7. **Refuse `API_AUTH_OPTIONAL` at boot whenever RAILWAY_ENVIRONMENT is set** (F19).
8. **`force_refresh` for admins only** (F17); agents never get it.
9. **A sender check in the extension's message listener** before any extension sink (F65).
10. **Attachment keys name the account** (F52): the API mints custom-attachments/{account_id}/property/{pid}/{uuid};
    an INSERT trigger on `property_attachments` requires the key's account segment to equal the row's account and
    refuses the old key form; a unique index on `storage_key`. Revoking the browser INSERT is not an option, because
    the API's own insert runs as `authenticated` through `tenant_conn`.

## 3. Target architecture

```
 SPA / Phone app       Chrome extension       Assistant (Claude, ChatGPT) over MCP
   | JWT                  | JWT                   | OAuth token, audience https://api.propscout.cz/mcp
   v                      v                       v
 +----------------- Railway project "API" (Amsterdam) ---------------------------------+
 | api (exists): REST shim + /mcp (W5) --> capability registry (W2, from _ToolDef)      |
 |   POST /runs: stamp the caller's own account and principal from the verified token;  |
 |   one admission function (per-account lock, quota, dollar reservation); queued run;  |
 |   steps, cancel and approval routes (JWT)                                            |
 |                                                                                      |
 | agent-worker (NEW W2, same image, dark)          private net    code-runner (NEW W6, |
 |  fair SKIP LOCKED claim, priorities, leases ----- code+files --> own slim image,     |
 |  generic loop -> LLMClient -> 5 providers     <------ /out ----- only if AR4 allows):|
 |  policy gate allow/ask/deny, taint                               no variables,       |
 |  renderers in a child process with rlimits                       network denied,     |
 |  market tools: market_reader login, READ ONLY                    one uid per run     |
 |  cache tools: cache_writer login; ledger: agent_ledger login (no service DSN)        |
 |  tenant tools: tenant_transaction(run principal, run account)                        |
 +---------------|----------------------------------------|-----------------------------+
                 | pooler                                 | bucket-scoped tokens
 Supabase Postgres eu-west-1: agent_runs, agent_run_steps, agent_artifacts,     R2 bucket, EU
 capability_calls, saved workflows, correspondence (NEW); estimation_runs,       jurisdiction
 llm_calls (+run id), curation, RLS (EXIST)                                     (NEW, W4)
                 ^
 batch-worker (PR #2000): one JOBS entry, "workflow triggers", under one scheduled_jobs lease,
   turns due saved workflows and new visible watchdog dispatches into batched runs (W3)
 realtime-worker: scraping and producers only; never agent code; estimation lane deleted (W3)
 Mail (W7): mailto + ledger -> reply.propscout.cz inbound (M1) -> gmail.send as the user (M2)
```

### 3.0 The product, worked backwards

Every component below exists because one of these agents or surfaces needs it.

| Agent or surface | Trigger | Operations (wrap = exists; new = to build) | Produces | Effect ceiling |
|---|---|---|---|---|
| Regional investment scout | manual; a saved workflow (weekly); a new watchdog match | wrap: comparables, velocity, reference rent, yield, ad history; new: search_properties, get_property_dossier, sold_comps | a ranked shortlist (.md); its own collection | writes only its own collection; anything else asks |
| Redevelopment analyst | manual on a property, with uploaded plans | wrap: `read_floor_plan` (revived on run inputs as a tenant tool, F35), `extract_building_units`, yield, comparables for the after-product; new: get_property_dossier, render_workbook, render_pdf, run_python (gated) | .xlsx with named cells (per-m² figures as values with their basis; live formulas only for the operator's own arithmetic), a PDF memo, .md; the area takeoff | artifacts; Valuo asks; code only with network denied |
| Broker correspondence | from a pipeline card; an inbound reply (M1) | wrap: broker reads behind `apply_pii_policy`; new: get_property_dossier, property_brokers, draft_message, request_send_approval, extract_reply (quarantined) | drafts, approval requests, proposed notes and viewings | external = always ask; the agent never sends; recipients come from the server |
| Estimation (exists) | SPA, extension, API | the 13 tools and `record_estimate` (F25) | an `estimation_runs` row (rule 12) | reads and cache writes |
| In-app chat | a message | read tools; start_run, get_run, get_run_output | cited answers; child runs | read; starts runs |
| Assistant over MCP | the user's Claude or ChatGPT | the same registry | structured results, links to artifacts | per tool; writes become approvals in our app |

Three consequences. **Composite tools beat chatty ones:** get_property_dossier returns detail, canonical ad, location,
price history, yield and masked brokers in one call (as built in #2063: under the user's login with the runs the page
lists, and no broker; the brokers are their own market read, get_property_brokers, unmasked as the page shows them,
AR13 re-taken). The dossier is the property page's own read under the user's login, the user's runs included; the
user's own notes, cards and collections on that property come from a second call, get_my_property_context (the
extension's state read), so each entry is one screen's reader.
**Most analyst "code" is a template:** the model, the memo and the
drawing render from a structured spec through git templates (tier 1), so AI-written code is off the critical path.
**The assistant is a peer surface:** a skill "runs in a user's assistant or inside the platform" (GLOSSARY).

### 3.1 Capability layer: one operations registry

**Shape.** Promote `_ToolDef` (F26) into one transport-neutral registry module. Each entry declares:
- a name, title and description; an input schema (Pydantic, with filter fields from `to_jsonschema_properties`) and an
  output schema;
- a handler, handler(ctx, args), that reaches data only through ctx;
- a **tenancy class**: market (the market_reader login, READ ONLY transaction), market_cache (the cache_writer login,
  allowed only for the six agent cache writers that stay market-wide, F27) or tenant (`tenant_transaction` as the
  run's principal, narrowed to the run's account). Any tool whose input is a user's file is tenant;
- an **effect class** (§ 3.5) and a **cost class** (free, llm, paid_api, live_fetch);
- the surfaces it is exposed on (agent, rest, mcp), and MCP annotations derived from the effect class.

**Rails (censuses, F72).**
1. Every entry declares all three classes.
2. No handler opens a connection except through ctx.
3. Only the API's dependencies and the agent-worker's one claims builder call `tenant_transaction`.
4. No input schema carries `account_id`, an act-as field, a user id or any principal field: identity comes only from
   the run.
5. The market_cache class is allowed only for six agent cache writers, by name: `verify_listing_freshness`,
   `compute_walkability`, `compute_amenity_supply`, `find_comparables_along_axis`, `summarize_listing` and
   `compare_listing_images`. F27's seventh, `read_floor_plan`, is tenant (§ 3.6), and the two parked condition writers
   of the nine exceptions (`.claude/skills/toolkit-api/SKILL.md:37-70`; rule 14) are excluded.
6. A market or market_cache handler takes no id of a tenant-owned row (attachments, notes, collections, runs).

**Validation.** Arguments are schema-validated before dispatch; unknown arguments are rejected, not dropped. That
deletes `_FCR_OVERRIDE_FIELDS` (F26).

**Errors.** One shim maps domain errors to an HTTP status, a tool error or an MCP error result. The write modules'
`HTTPException`s move into the REST shim, curation and pipeline first (F48).

**First set: 30 operations, 22 of them wraps or thin adapters (T1).**
- The 13 agent tools: `find_comparables_relaxed`, `analyze_distribution`, `find_distribution_outliers`,
  `describe_neighborhood`, `verify_listing_freshness`, `compute_market_velocity`, `compute_listing_velocity`,
  `compute_walkability`, `compute_amenity_supply`, `find_comparables_along_axis`, `summarize_listing`,
  `compare_listing_images`, `read_floor_plan` (`record_estimate` stays the estimator's terminator).
- Two broker reads behind `apply_pii_policy` (`listing_brokers`, `get_broker`); `estimate_yield` and
  `compute_reference_rent`; one Valuo valuation (costly, over `new_valuation`).
- Four curation writes: add to a collection, create a note, attach a tag, set a pipeline card.
- New: search_properties over `compile_filter_where` with the served and published predicates (Watchdog semantics,
  AR7); get_property_dossier (tenant); get_my_property_context (tenant); a sold_comps adapter; start_run, get_run,
  get_run_output and cancel_run.

**Surfaces (D17, D18).** The SPA keeps its PostgREST reads under RLS, because RLS and the SQL functions are already the
shared rule, and the brief allows endpoints to differ (F62). Writes go through the API, as today; the extension keeps
its API
calls. Agents and MCP use the registry. The `/tools/*` bodies are generated from it, gaining an ad id where they are
sreality-only. `POST /tools/find_comparables` stays, under the 2026-09-21 ruling, until an MCP tool replaces it (F75).
A server-side Browse compiler stays out of scope (§ 5).

### 3.2 Tools, skills and agents, kept separate and kept in git

| Kind | Lives in | Holds | Versioned by |
|---|---|---|---|
| Tool | the registry (Python) | schema, handler, classes | a sha over all schemas: the tool-set sha |
| Skill | skills/<name>/SKILL.md, with templates/, scripts/, references/ | instructions, templates, scripts | a version and sha256 in a skills lock file; a `--check` in CI |
| Agent | agents/<name>.yaml | a model per provider and a default; ordered skills; a tool allowlist; an effect policy within the ceilings; limits (dollars, iterations, wall clock, max tokens, code seconds); an output schema and a server finaliser; allowed triggers; a plan agenda; a Rule-of-Two declaration | the same lock pattern |

- **Git is canonical** (AR5). The lock copies the contract-lock pattern as it stands (F73). The Dockerfile copies
  `skills/` and agents/ (F8). The `skills` table becomes a read-only projection, kept for history and old runs.
- **A run pins its full definition at submit.** The API copies the agent, its skill texts and the tool-set sha into
  the run; the executor never reloads them, which closes the submit-versus-claim drift (F31). The run stamps the agent,
  skill and tool-set shas, the provider and model, and `RAILWAY_GIT_COMMIT_SHA` (already read at
  `scraper/realtime_worker.py:1886`), as one version string in the style of `extractor_version()` (F73).
- **Deleted:** runtime reads of `skills`; editing in Settings › Skills, which becomes a viewer (F66); the refiner's
  Apply. The refiner instead opens a pull request with the SKILL.md change and the flagged run as an eval case.
- **Multi-skill** is an agent's ordered skill list. **Multi-agent** is a start_child_run tool plus the fan-in hook
  (§ 3.4); a coordinator agent (the scout's shortlist, then one analyst run per property) arrives in W8. No graph
  engine.
- **Plugin.** skills/ plus the MCP connection are packaged as an assistant Plugin (GLOSSARY), eval cases excluded.

**Authoring a new agent (built in W1, about 2 days).**
1. Write agents/<name>.yaml and its skill folder.
2. Run it locally against the schema-replay database with the developer's own provider keys (a proposed
   scripts/agent_run.py with --agent and --db).
3. A record mode writes the run's step journal as a cassette under tests/evals/<agent>/cassettes/.
4. The pull request "blesses" the cassette and shows the scorer numbers (the characterisation-golden pattern, F69).
5. The lock is bumped; CI's `--check` fails an unbumped edit.

### 3.3 Runtimes, and where runs execute

**Tool-only agents run on the plain loop, generalised** (D1; RESEARCH §1.5, §2.1). The estimation-specific parts of
`run_agent_estimation` (the opening message, `record_estimate`, `_AGENT_ESTIMATE_KIND`, the fixed `called_for`) move
into the estimator's agent file and a finaliser hook. The provider `Literal` widens to the five registered providers
(F28). No dependency is added. The Agent SDK and Managed Agents are rejected for customer runs: both are Claude-only,
and Managed Agents keeps data in the US without zero data retention (RESEARCH §1.2; reopen conditions in § 5).

**Hard limits.**
- Before each model call the loop caps `max_tokens` so that the call's worst case (input tokens times the input price,
  plus `max_tokens` times the output price) fits the run's remaining reservation. Nested LLM calls made by cache tools
  (`summarize_listing`, `compare_listing_images`) and by `read_floor_plan` carry the run id and are priced into the
  same pre-call cap, so they cannot escape the run's budget.
- At admission (§ 3.4) the run's max_cost_usd is **reserved** against the account's monthly dollar budget from the
  plan and settled at the terminal state, so concurrent runs cannot overshoot; child runs draw from the root run's
  reservation (depth at most 2, fan-out capped per run). Chat gets a per-message and a cumulative budget.
- A platform-wide daily dollar ceiling in `app_settings`, checked at claim: reaching it parks runs as queued and
  raises a Problem cell. This is the real stop. Agent-lane provider keys sit in separate provider projects with caps,
  but those are lagging backstops ("enforcement is not instantaneous", RESEARCH §6.5), and a Google Cloud budget for
  Gemini only alerts. This fixes F30.

**Where runs execute (D2, AR8).**

| Host | Verdict | Why |
|---|---|---|
| A lane in the API process | possible, the cheaper option | with leases and the journal a deploy no longer loses work, and a dedicated executor pool (4–8 threads) keeps it off the request threadpool; it loses by default because one agent step's OOM would take requests down with it, and the API holds Stripe, GitHub and the full R2 key beside the run |
| A lane in the realtime-worker | no | 21.5 of 24 GB peak, and an OOM stops every latency lane; the 7 h 40 min freeze; it holds RunPod, VAPID, the proxy and the full R2 key (F3, F7) |
| The Actions-exit batch-worker (PR #2000) | no for runs; yes for the trigger tick | it is a due-based subprocess scheduler with timeout kills and 70 % memory admission sized for walks; an interactive run must start within seconds and may wait days for an approval; #2000 is not settled (F11) |
| **A fourth service of the same image: agent-worker** | **yes** | its own memory limit, so a poison run cannot stop requests or scraping; fewer credentials than the API (no service DSN, no Stripe, GitHub or main R2 key; § 3.5); its own replicas; a private network to the code-runner; about $5 a month |

agent-worker does not survive deploys better than the API would: in the week to 2026-10-25, 61 of 135 first-parent
merges touched paths it imports, about 9 redeploys a day. The lease and the journal are what save the work. AR8 may
therefore take the API lane for W2, one service less for a solo operator; agent-worker then becomes required when
concurrent runs pass 8, API memory passes 2 GB, an executor incident hurts API p95 latency, or tier-2 code needs a
caller that holds no tenant DSN.

agent-worker uses the same Dockerfile with its own start command, in the API project (private networking, F1), set up
by IaC only, since new services cannot use Config as Code (F5): restart always, drainingSeconds 180, a 2–4 GB memory
limit, watch patterns limited to what it imports. It reuses the lane supervisor, watchdog and heartbeat (F42) through
the shared module Actions-exit W2 creates (whichever change lands first does that pure move), with its own liveness
subject. It runs N slots (8 to start). A slot keeps its run between consecutive steps instead of re-claiming it, and
POST /runs and every user message nudge the executor (a 1 s poll for interactive sources). Its brake is a runs
interval in `app_settings`: absent means 0, which means dark.

**A poison run cannot take the others down.**
- Heavy or untrusted work (the renderers, PDF rasterising, image payloads, openpyxl on a model's spec) runs in a child
  process with an empty environment and an RLIMIT_AS cap; it takes bytes in and gives bytes back, and fails alone.
- A "step started" row is journalled before every heavy step, with the in-flight LLM call's estimated cost, so a call
  lost in a crash is still charged.
- A run whose lease expired twice at the same step fails with "crash at step n", with no third try.
- Admission checks free memory before a heavy step; a Health cell counts agent-worker restarts a day.

**Code-executing agents: two tiers (D7, AR4).**
- **Tier 1, renderers, in agent-worker's child process. No AI-written code runs.** The model fills a structured
  spec, and git templates in the skill render it: render_workbook (openpyxl, a new dependency, AR18), render_pdf
  (`reportlab`, already shipped, F8), render_dxf (ezdxf, only if a DXF need is confirmed, AR18).
  - Renderers XML-escape every model string, allow no URL-bearing markup (no link or image tag from the spec), embed
    images only from run-local bytes, and set hyperlinks only from platform URLs the server resolves. A spec cannot
    make agent-worker fetch anything at render time.
  - Workbook cells refuse external-reference formulas (WEBSERVICE, FILTERXML, HYPERLINK, external links, DDE), and any
    model string that starts with =, +, - or @ is stored as text.
  - **Rule 23 holds inside workbooks:** every per-m² figure is imported as a value from `measure_price_per_m2`, with
    its `measure_price_per_m2_basis` label in an adjacent named cell. Live formulas are allowed only for the
    after-product arithmetic on the operator's own inputs (costs, yields). The renderer templates join
    `toolkit.measures.REGISTERED_SITES`.
- **Tier 2, run_python, in a code-runner service, only if AR4 allows it.** Its own slim Dockerfile (openpyxl,
  xlsxwriter, reportlab, ezdxf; AR18), so the shared image stays unchanged (rule 7). Same project as agent-worker,
  private-network inbound only, no public domain, **no variables** (a boot assertion refuses any, apart from Railway's
  injected RAILWAY_* names). It runs as root only for the launcher, which drops each run to its own uid. Per run:
  - one job at a time per code-runner replica, in a fresh 0700 directory owned by **a distinct unprivileged uid for
    that run**, taken with setuid, so no namespaces are needed (F8); TMPDIR inside that directory, with a total disk
    quota per run;
  - rlimits (address space, CPU, file size, open files, processes) set in the launcher, never in a preexec_fn
    (RESEARCH §2.2); python -I; an empty environment; a wall-clock kill of the process group;
  - the launcher talks to the child over inherited pipes only; its job endpoint accepts only agent-worker's private
    address, returns results on the job's own response and never serves stored outputs;
  - after every run: kill every process of that uid, delete its directory, sweep /tmp and /dev/shm, free the uid.
  - **Network denial is a mandatory exit gate:** a seccomp allowlist, with no_new_privs, that denies socket() for
    AF_INET, AF_INET6, AF_PACKET, AF_NETLINK and AF_UNIX, and denies io_uring_setup (io_uring can open sockets
    around a socket filter), ptrace and process_vm_*. Landlock is only a second layer: its network rules cover TCP
    (ABI 4; UDP only from ABI 10, RESEARCH §2.2) and never raw sockets. Without proven denial, tier 2 does not ship.
- **Second rung, if seccomp fails:** a Railway Sandbox VM per run, in a separate, **empty** Railway project, placed in
  its own workspace or under a hard usage limit, with no domain-capable environment and a rotated token: no
  sandbox-only token scope exists, and a project token can deploy services and attach domains. A Health cell alerts
  on any service or domain in that project. Region pinned to Amsterdam; ISOLATED networking; a root bootstrap that
  drops outbound traffic before it runs the code as an unprivileged user (**unverified**: RESEARCH §6.4 marks the
  in-guest cut as untested, so W0's Sandbox probe tests it); files moved in chunks (HTTP exec cuts streams at 16 KB);
  destroyed in a finally block (RESEARCH §7.1). Staff call it beta and "not for production", so it is a ruling (AR4).
  If neither rung passes, tier 2 waits and tier 1 still delivers the files.
- **Rule of Two.** run_python's effect class, "code", counts as external unless network denial is proven, and CI fails
  an agent holding untrusted input, private data and code without the gate. Phase 7d's audit table becomes a
  code_execution step kind in the step journal (§ 3.4).

### 3.4 Durable orchestration

**Two new tables beside `estimation_runs`** (D4, AR6). Rule 12 is untouched.

agent_runs holds three groups of columns:
- **Scope and source:** account_id NOT NULL with no SYSTEM default (F22); principal_user_id; act-as and share mode;
  agent and agent_version; the pinned definition; input; source (spa, phone, extension, mcp with the client id,
  schedule, event or api); priority (interactive, event, schedule); saved_workflow_id; property_id, classified in
  `PROPERTY_CARRIERS`; estimation_run_id as a link.
- **State:** status (queued, running, awaiting_approval, awaiting_input, awaiting_children, succeeded, failed or
  cancelled); attempts; lease_until; heartbeat_at; cancel_requested_at; parent_run_id; root_run_id; the reserved
  dollars.
- **Outcome:** idempotency_key, result, cost_usd, the git sha, timestamps.

A status-transition trigger allows only legal moves: a terminal status is final, and queued → running happens only
through the claim.

agent_run_steps is append-only (a trigger refuses UPDATE and DELETE outside the retention prune): (run, step number);
kind (llm_turn, tool_call, step_started, approval_request, approval, user_message, code_execution, child,
checkpoint); capability; input hash; a bounded summary and the full payload (capped, with overflow to the artifacts
bucket); `llm_call_id`; the gate's evaluated permission and reason code; the tool-set sha in force; duration and cost.
**This one table is the progress feed and the resume journal, and the trace of every non-estimation run.**
`llm_calls` gains a run id and a step number (additive), which makes per-account spend exact (F36). Browser roles get
SELECT under `current_account_ids()` only. The API writes runs after it resolves the **caller's own** account with
`require_account_id`; act-as is recorded only as a narrowing (§ 3.5), so the browser has no write path (the lesson of
F16) and a delegate never spends for the owner.

| Need | Mechanism |
|---|---|
| Hundreds queued | queued rows wait as long as needed; **nothing is swept by age** (fixes F38) |
| Admission | **one admission function**, called by POST /runs, the trigger job, start_child_run and MCP start_run: a per-account `pg_advisory_xact_lock` (pooler-safe, as at `toolkit/property_carriers.py:138`), the monthly quota, a reservation of the run's dollar ceiling against the monthly budget, then insert as queued with ON CONFLICT on the idempotency key; no 429 for being in flight, runs wait (fixes F24) |
| Fair concurrency | one claim under a global transaction lock: the highest-priority, then oldest, queued run whose account is under its cap, within the global slot total, `FOR UPDATE SKIP LOCKED`; 2 of the 8 slots are reserved for interactive runs; the push claim's fairness grouping, re-keyed from device to account (F40). Target: p95 queue wait under 10 s for interactive runs with 200 queued |
| Crash or deploy | a 2-minute lease renewed every step; an expired lease is re-queued with attempts + 1 (compare-and-set on id and attempts) and failed after 3, or after 2 at the same step (§ 3.3); on SIGTERM the worker stops between steps and releases its leases |
| Step errors | separate from lease re-queues: a provider 429, 5xx or timeout retries inside the step up to 3 times with backoff (2, 8, 30 s, with jitter); a schema refusal, any other 4xx, an exhausted budget or a policy denial ends the step |
| Resume | messages rebuilt from the steps; committed steps never replay; an approved action runs the arguments stored in its approval step verbatim, and the model is never asked for them again; every tenant write carries a unique (run, step) key; the Valuo write-ahead row and the mail outbox make sinks idempotent (F49) |
| Code changed since submit | on every resume the pinned tool-set sha is compared with the live one; if a tool the run still needs changed its schema, a pending approval's arguments are re-validated and voided if they fail; both shas go in the step; a pinned skill that names a removed tool fails the run with "definition changed, re-run" |
| Cancel | a flag checked between steps and before every sink |
| Waits | any awaiting status **releases the slot**; the approval route, the next user message or the last child's terminal move re-queues the run |
| Fan-in | a child's terminal transition re-queues its awaiting_children parent once every sibling is terminal: the hook the building flow lacks (F39) |
| Progress | the SPA polls new steps with backoff from 2 to 10 s into the existing `Timeline`; SSE later, with a heartbeat under 5 minutes and a reconnect at 15 (RESEARCH §7.4); the API record is DNS-only, so Cloudflare's 125 s cut never applies (F10). Targets: p95 submit-to-first-step under 5 s; chat overhead beyond model latency under 3 s |
| Connections | none held across a model call; one per step on the transaction pooler; the brake settings read in one query per wake. 380 is the direct-connection limit (it matches the 2XL tier, whose pooler takes about 1,500 clients, RESEARCH §8.2); the binding limit is the transaction pool size, shared with the scrapers and Actions jobs and not published, so W2 measures and quotes it; a Health cell watches pooler client connections (F43) |

**Triggers.**
- **Chat, manual, extension, MCP and API** all call POST /runs and its admission function.
- **Saved workflows** (a schedule or a watchdog) are a third saved-set kind after filter presets and dashboards: one
  `SavedSetKind` entry and its table, holding a name, an agent, an input and an optional schedule (daily or weekly, at
  an hour) or a watchdog id. It holds **no lease, cursor or due column.** Unlike the other saved sets, it is written
  through the API only (no browser INSERT or UPDATE grant), its policies use `current_account_ids()`, never the
  acting set, and a composite foreign key (watchdog id, account id) onto `notification_subscriptions` makes a
  foreign account's watchdog impossible to name.
- **One scheduled job turns them into runs:** a JOBS entry, "workflow triggers" (every 300 s), held by one
  scheduled_jobs lease row and executed by the Actions-exit batch-worker.
  - It **batches**: at most one run per saved workflow per tick, taking every new dispatch since that workflow's last
    run as input. Idempotency keys are "schedule:<saved workflow>:<period>" and "watch:<saved workflow>:<tick>", ON
    CONFLICT DO NOTHING, so due-ness and de-duplication come from agent_runs itself and a restart re-runs nothing.
  - It *reads* `notification_dispatches` as a consumer, through `VISIBLE_SQL` (`api/notifications.py:346`), so
    duplicates, retired watchdogs and 'new' events a merge withdrew never fire. It takes producer 'watchdog' only,
    requires dispatch, workflow and watchdog to share one account, fires only on published properties (settled or
    6 h old, rule 16), and waits one maintenance pass after a dispatch so merges can settle. It is not a fourth
    producer, so rule 16 is untouched (AR10).
  - A daily fire cap per saved workflow and per account (AR14), and every run it inserts passes the admission
    function.
- Scheduled and event runs **re-check the principal** (membership and entitlement) when they fire, and run under the
  stricter column of § 3.5: no costly action and no live portal fetch. Their model and cache spend counts against the
  account budget like any other run.
- **If PR #2000 has not landed when W3 starts,** the same function runs in agent-worker under one `location_jobs` row,
  the very table #2000 renames, and moves into JOBS when #2000 lands. One scheduler either way.

**Chat** is a long-lived run of a chat agent: each user message appends a step and re-queues the run from
awaiting_input, with the context bounded by a step window and a summary step, and a per-message and a cumulative
budget. No chat table; a lazy-loaded panel that the Phone app frames for free (F63, F64).

**Estimation moves onto agent-worker in W3, and rule 12 holds.**
- `create_estimation_run` still INSERTs the `estimation_runs` row at submit, as today, and inserts the linked agent
  run **in the same transaction**, so neither can exist without the other. The executor writes the terminal status
  into the `estimation_runs` row in place.
- `estimation_runs.trace` (with `estimation_trace_payloads`) stays the estimation's trace of record, written at the
  terminal state as today. agent_run_steps is the operational journal and is never read as the estimation's audit
  (an AR6 clarification of rule 12).
- `/estimations*` is unchanged for the SPA and the extension (F65).
- **Brake:** a new key, agent_estimation_executor_enabled (absent = 0). The existing `estimation_job_lane_enabled`
  would arm the realtime-worker's lane (F37, F38), so it stays off, and that lane is deleted in the same change that
  first sets the new key. The age sweeps skip rows linked to an agent run.
- A Health cell flags any pending `estimation_runs` row with no live linked agent run.
- **Runs with no principal fail closed.** Estimation runs with `claims=None` or on SYSTEM run market tools only; the
  claims builder refuses every tenant tool for a NULL principal. SYSTEM-account agent runs are admin-visible only,
  with no all-accounts read arm. The building fan-out stays admin-only (AR19) and never takes a principal-less tenant
  path.
- After the W3 gate (§ 7), the worker's estimation lane, the API `BackgroundTasks` path and the age sweeps are deleted.

### 3.5 Multi-tenant security

**The run principal (D8).**
- At submit the API stamps principal_user_id and account_id from the verified JWT or OAuth token. **account_id is
  always the caller's own account** (`require_account_id`): quota, dollar budget, Valuo cap and any mail identity are
  the caller's. Act-as and share mode are stamped only to narrow the tenant tools to the shared owner's board; costly
  and external effects are refused under act-as, unless the owner grants a new, explicit share mode later.
- No token is stored, forwarded or refreshed: Supabase refresh tokens are single-use, so forwarding one would sign the
  user out (RESEARCH §6.3).
- In agent-worker, **one claims builder** turns the stamped columns into {"sub": principal, "role": "authenticated",
  "run_id": id} for each tenant transaction, READ ONLY for reads, as every GET/HEAD tenant read is today (F20,
  F21), and sets two settings (GUCs): request.agent_run = true and request.account_id = the run's account.
- **Two narrowing-only changes to the identity functions ship in W2.** `is_platform_admin()` returns false whenever
  request.agent_run is set, so a run started by an admin never carries admin powers (no SYSTEM or NULL arms, no
  shared-table writes such as `manual_rental_estimates`, no admin-gated DEFINER functions). `current_account_ids()`
  and `acting_account_ids()` intersect with request.account_id when it is set, so a user with two memberships gets a
  run that sees only the run's account. Both changes can only remove power: whoever sets the settings gains nothing,
  and with them unset both functions behave exactly as today (a census and live tests pin that).
- Under act-as the builder sets the acting identity exactly as the X-Act-As header does, so the existing
  `delegated_edits` trigger stamps every delegated write; it gains the run id from the claims (additive).
- Broker contacts: a run sees them as the page shows its user, at most 20 people per call, every call recorded; MCP
  stays masked (AR13 re-taken 2026-10-25, ADR-0002; built in #2063 as get_property_brokers).
- `current_account_ids()` is evaluated live, so a revoked membership takes effect at the next tool call. A NULL
  principal fails closed (§ 3.4).

**No service DSN in agent-worker (D19).** Market tools run in the same process that parses untrusted ad text, so a
handler bug must not become a whole-database read. Before the proof of concept, W2 adds three login roles, all
NOBYPASSRLS:
- market_reader: SELECT on market relations only; no grant on accounts, curation, attachments or auth;
- cache_writer: INSERT and UPDATE on the six cache tables of § 3.1 only;
- agent_ledger: the run, step, artifact and call tables, `llm_calls`, and the estimation finaliser's terminal writes.

A grant census pins all three; it is the grant measurement the W8 binding was waiting for. The tenant DSN stays, used
only by the claims builder.

**Effect classes.** A deterministic gate checks every call before dispatch and logs its decision and reason code in
the step.

| Class | Examples | User-started run | Scheduled or event run | In the agent file |
|---|---|---|---|---|
| read_market | comparables, velocity, yield, broker reads (unmasked, ≤ 20 people per call; AR13 re-taken) | allow | allow | n/a |
| market_cache | summaries, the OSM mirror, transit lines, freshness (the six, § 3.1) | allow, metered; a live portal fetch is rate-limited per run and per account | allow, metered; **no live portal fetch** | n/a |
| read_tenant | collections, notes, the pipeline board, watchdogs, run inputs | allow | allow | n/a |
| own_collection | adding to a collection the run itself created; rows carry the run id for one-click undo | allow | allow | can only be tightened |
| write_tenant | notes, tags, pipeline cards, other collections | ask (AR9) | ask | can only be tightened |
| costly | Valuo, Mapy credits | ask; refused under act-as | deny: no paid call without a user present | fixed |
| external | sending mail; anything that leaves the platform | always ask; refused under act-as | always ask | fixed |
| code | run_python | only if declared and network denial is proven | same | fixed |
| admin | merge and split (rule 15), skills, settings, `force_refresh` | deny, never exported | deny | fixed |

`verify_listing_freshness` fetches portals live and nominates delist checks (rule 3), shared state for every tenant,
so its live_fetch cost class carries the rate limit above; W3 decides whether agent calls queue on
`listing_detail_queue` at a low priority instead of fetching inline.

A census rejects any agent file that loosens a class past its ceiling, and any agent that holds untrusted input,
private data and an external or code effect without an always-ask sink (the Rule of Two).

**Approvals.**
- An ask writes an approval_request step holding the tool, the canonical arguments, their sha256, the principal and an
  expiry (24 hours). The diff the user sees is **rendered by the server**, deterministically, from the canonical
  arguments; any reason the model wrote is shown as labelled, untrusted commentary, so injected text cannot describe a
  harmless action while the arguments do something else. The run parks in awaiting_approval and releases its slot.
- The approval binds (run, step, tool, argument sha, principal). Only the run's principal may decide (`sub` equals
  principal_user_id, with no X-Act-As), and for an external send only the account owner; the decision is appended as
  an approval step. Users decide from the approvals inbox in the SPA and the Phone app.
- On resume the executor re-checks the hash, the expiry, the entitlement and the cancel flag, then **runs the stored
  arguments verbatim**. Edited arguments void the approval; so does a schema change that the arguments fail (§ 3.4).
- **Consumption is one transaction on one connection.** For a tenant-side sink (the outbox, a curation write), one
  SECURITY DEFINER function, callable under the tenant role and only with the run setting the claims builder sets,
  checks that the run's principal is `auth.uid()` and that the run's lease is live, inserts the consuming step (a
  unique column, so an approval is used once) and claims the outbox or spend row. Service-side sinks follow the Valuo
  write-ahead row (F49) and the push compare-and-set (F40).
- A ChatGPT or Claude confirm dialog is a hint, never our approval (RESEARCH §3.2).

**Prompt injection.** Tool envelopes mark third-party fields (ad text, broker mail, uploads) as untrusted; the loop
spotlights them and taints the run; mail and document bodies pass first through a tool-free, schema-forced extraction
call, the text lane's pattern (`toolkit/description_extraction.py`). Mail recipients are never model arguments: the
server resolves them from the canonical broker's known addresses for the thread's property (§ 3.8). The
server-derived terminator result stays the main defence (F25).

**Credentials, per service.**

| Service | Holds | Refused (a boot assertion over an allowlist of variable names; Railway's RAILWAY_* names allowed) |
|---|---|---|
| api | as today, minus `RUNPOD_API_KEY` (unused, F7); plus a read token for the artifacts bucket (W4) | none |
| realtime-worker | as today | it never runs agent code |
| agent-worker | the market_reader, cache_writer and agent_ledger DSNs; `TENANT_POOL_DB_URL` (claims builder only); agent-lane provider keys from capped projects; the artifacts write token; `VALUO_SANDBOX_API_TOKEN` (the live Valuo token only once AR14 caps Valuo per account) | `SUPABASE_DB_URL`, the main R2 key, RunPod, VAPID, the scraper proxy, GitHub tokens, Stripe, Google mail tokens |
| code-runner | nothing | everything |
| Sandbox project (second rung only) | nothing; agent-worker holds a token for that empty project | n/a |

**Later hardening, W8 (AR17).** Bind runs in the database: an agent_runtime login role (NOINHERIT, NOBYPASSRLS)
whose identity functions honour its claims only for a running, leased run whose principal equals `sub`, so a
compromised agent-worker could act only for users with a run in flight, and agent-worker drops the tenant DSN. The
grant census (W2), the status trigger (W2) and live forged-claims tests are its prerequisites. Unlike W2's narrowing
settings, it changes when the two identity functions every RLS policy reads grant power, so it waits for a trigger:
paying tenants, a second operator, or any agent-worker incident.

### 3.6 State and artifacts

- **Store** (D11, AR16): Cloudflare R2, in a **new EU-jurisdiction bucket** (jurisdiction cannot change later,
  RESEARCH §8.2), with
  bucket-scoped tokens: agent-worker writes, the API reads; the main bucket's key is never shared (F51). Supabase
  Storage is rejected: a second store whose RLS the API proxy would bypass anyway (F53; RESEARCH §6.6).
- **agent_artifacts:** account_id NOT NULL, run id, step, property_id (carried on merge through `PROPERTY_CARRIERS`,
  rule 18), logical name, version number, kind (md, xlsx, pdf, dxf, csv, png, script), sha256, mime, size, storage key,
  agent version, retention class, deleted_at. Browser roles SELECT through RLS on `current_account_ids()`, from a view
  without the storage key; no browser INSERT, UPDATE or DELETE.
- **Keys:** {account}/{run}/{sha256}; staged inputs under tmp/{run}/, removed by a 7-day lifecycle rule.
- **Inputs:** the API copies the attachments the user picked, read under that user's `tenant_conn`, into the run's tmp
  prefix. A run sees only what its user handed it. A tool that reads a user's file (`read_floor_plan`) is tenant-class
  and takes only an input handle staged for this run, never an attachment id from the model; its analysis lands in the
  run's steps or artifacts under the run's account, not in the account-less `building_attachment_analyses` cache (F27).
- **Outputs:** only declared output files are promoted, after an allowlist check (md, csv, xlsx, pdf, docx, dxf, png).
  html and svg are never promoted from tier-2 code. Every promoted artifact, from either tier, passes one sanitiser
  that strips formulas using WEBSERVICE, FILTERXML, HYPERLINK or DDE, external workbook links, docx external
  relationships and remote templates, and PDF JavaScript, URI and OpenAction entries, so a file cannot call out when
  the user opens it. Bytes count toward a per-account quota (a new `usage_ledger` action). The API serves them with
  `attachment` and `nosniff` (`api/attachments.py:153-164`); links handed to MCP clients stay authenticated, never
  presigned. A Content-Security-Policy with img-src 'self' and a sanitising Markdown renderer that drops remote images
  come before any in-app rendering (F63).
- **Temporary vs persistent:** run directories die with the run; nothing undeclared is promoted; "save to property"
  copies an artifact into `property_attachments` (whose allowlist gains Markdown). AI-written scripts are artifacts of
  kind script and never enter skills/; skill changes travel only as pull requests (§ 3.2).
- **Retention and erasure (AR15), built in W4:** the account-erasure route deletes artifact bytes and rows and fixes
  the foreign keys that lack ON DELETE (F55); the 180-day step-retention prune is a JOBS entry. W7 extends erasure to
  correspondence rows and bytes.

### 3.7 External MCP adapter

- **Endpoint: https://api.propscout.cz/mcp,** one stateless endpoint mounted last at "/" in the API, with the session
  manager in the lifespan and explicit allowed hosts (RESEARCH §3.4). It launches only after the brand-domain cutover,
  because the resource URL must equal the URL the user typed and must not change once users connect (RESEARCH §3.2).
  The API record stays DNS-only: Cloudflare's proxy cuts at 125 s (F10). The brand-domain program's MCP section assumed
  the realtime worker's estimation lane (`docs/design/brand-domain-and-store/PROGRAM.md:246`); W5 replaces that line.
- **Dependency:** mcp>=2.3,<3 as an extra (AR18), the one new dependency on this path; hand-rolling the protocol
  would bend nothing we already have.
- **The verifier is the seam.** It checks the issuer, the audience (or the client-id rule), expiry and `sub`, and on
  every request the user's per-client consent record. It feeds the same principal into ctx.tenant. Swapping the
  authorization server changes only the verifier. REST is hardened first (Wave 0, F12).
  - With Supabase OAuth, `sub` is the Supabase user.
  - With WorkOS, a token maps to a Supabase user **only through a link table** (WorkOS user id ↔ `auth.users` id)
    that the user creates in an explicit linking flow while signed in to Supabase, or AuthKit takes Supabase as its
    upstream identity provider so the subject is the Supabase `sub`. **Never by e-mail.** Revoking the link ends MCP
    access, and a test refuses an unlinked WorkOS user whose e-mail matches.
- **Authorization server (AR11).** Spike the Supabase OAuth 2.1 server (beta, disabled on our project) on a branch
  project against the research's gates (consent with resource and offline access for a public client; claude.ai,
  ChatGPT and Claude Code connect; a 1 h refresh succeeds; the SPA is unaffected; the registration limit is
  configurable) **plus three more, each with a named mechanism:**
  1. the token is bound to the /mcp audience;
  2. it cannot read or write PostgREST: a `pgrst.db_pre_request` hook on the authenticator role raises whenever the
     JWT carries client_id, which covers PostgREST and pg_graphql; Storage and Realtime are tested separately. This
     matters beyond reads: curation writes are granted to `authenticated` "by convention, not grant", so an MCP token
     on PostgREST could write the user's curation past every effect class;
  3. it cannot call the auth user endpoint (#2877: a password change on accounts without MFA). GoTrue cannot be
     hooked, so this gate is decisive: if GoTrue accepts the token, the program goes to WorkOS, decided in advance.

  Any failed gate → WorkOS AuthKit (a new processor; check its DPA and residency), never a hand-rolled server
  (RESEARCH §3.3).
- **Tools:** registry entries flagged mcp: read_market, read_tenant, get_property_dossier,
  get_my_property_context, whoami, and start_run, get_run, get_run_output. start_run returns a run id and a poll hint;
  each call stays under Claude's 240 s; a run id is re-authorised against the caller's account on every call. Results
  are a summary, structured content and an authenticated link to the artifact proxy. write_tenant tools come later;
  costly and external tools only create pending approvals that the user confirms in our app; admin tools are never
  exported. `POST /brokers/by-listings`' read stays off MCP (AR13 re-taken: agents read it as get_property_brokers;
  MCP stays masked).
- **Ledger and limits:** every call made outside a run appends a capability_calls row (account, user, surface, client
  id, capability, argument hash, outcome, cost, duration). That one ledger gives the spec's rate limit (a count over 60
  s per cost class, checked under the same per-account advisory lock as admission, so concurrent calls cannot all pass
  one count), metering, and the missing "who called what" audit (F18). An exhausted quota returns an error result with
  the retry time; HTTP 429 is kept for transport abuse. The user can revoke a per-client consent record; the verifier
  checks it on every request, because a token stays valid for up to an hour. The source value "mcp" with the client
  id is stamped by the server (an additive CHECK change).

### 3.8 Mailbox, staged by verification cost

The platform owns the ledger, the approvals and duplicate prevention, whatever carries the mail (RESEARCH §4.3). Each
stage waits for demand (§ 7, W7).

- **M0, W7: no vendor, no new channel.**
  - correspondence_threads and correspondence_messages, with account_id and RLS on `current_account_ids()` (never
    the acting set), written through `tenant_conn`, anchored on property_id (carried on merge) and on the canonical
    broker, and re-pointed by broker merges (which fixes the lapse in F59).
  - **Recipients come from the server:** the canonical broker's known addresses for the thread's property. A model
    cannot add an address; the user may type another one on the approval screen, where it is shown in red.
  - An outbox whose states M0 can actually observe: draft → approved → handed_to_mail_client, with the approval bound
    to a hash of recipients, subject and body. A `mailto:` link cannot set a Message-ID, and at M0 the user's mail
    client sends, so the platform records the handoff, not a send.
  - **Deduplication by address:** one inbox is the primary for 1,202 brokers (F59). Suppression keyed by address,
    checked at approval, kept when lifted. An Art. 14 notice on the first contact (RESEARCH §4.5).
  - Property-anchored and user-initiated only; **campaigns stay out** (Act 480/2004). Replies are pasted or forwarded
    into extract_reply. The assistant channel: MCP tools broker_context, draft_broker_email and log_correspondence,
    marked unverified. **No agent tool sends.** The outreach CRM (0 rows) is deleted.
- **M1: inbound only, one vendor, no Google.**
  - A reply subdomain, reply.propscout.cz, with its own MX, SPF, DKIM and DMARC, because the apex is null-MX by ruling
    (F10). Inbound mail arrives through SES in eu-west-1 (AR12b; Mailgun EU the fallback) at an HMAC-verified API
    webhook with an idempotency table: the Stripe webhook pattern (F61).
  - Thread addresses carry 128 random bits. The `mailto:` link adds the thread's BCC address, and that BCC copy is the
    proof of the handoff: the outbox moves to confirmed when it threads. Inbound mail counts as from the broker only
    when SPF or DKIM aligns with the thread's broker address; otherwise it is marked unverified.
  - The platform still sends nothing.
- **M2: send as the user, gmail.send only (W8, AR12c).**
  - A sensitive scope: about 10 business days and no yearly CASA assessment (RESEARCH §4.1). The Reply-To is the
    thread address, so replies flow without restricted read scopes.
  - **Send-once lives here:** the outbox gains sending → sent | unknown, claimed by compare-and-set, with a
    platform-minted Message-ID stored before the send; a timeout lands as unknown and is never retried automatically.
  - Google refresh tokens are encrypted with a key that only a narrow outbox sender holds (the API, or one JOBS
    entry), never agent-worker or the code-runner, and it sends only from an approved outbox row.
  - Google requires a real homepage and a privacy policy on a domain we own; the approved cutover redirects the apex
    to app, so that part must be re-taken (AR12c). Microsoft Graph only on demand (publisher verification).
- **M3, on paying demand only:** restricted scopes with our own CASA (about $500–1,800 a year) or Unipile. Never IMAP
  passwords; never send-as (it ends January 2027).
- **Rejected as the default: the platform as sender** (SES outbound under a platform address). It makes the platform
  the controller of mail to a scraped directory, builds one reputation shared by every tenant, and is a new outbound
  channel under R2b. Reopen if Google verification stalls a quarter or Seznam users dominate.

### 3.9 Evaluation and observability

**Cases live in git, beside the tests** (D14): tests/evals/<agent>/cases/*.json, each with its input, pinned
`snapshot_id`s, expected facts, invariants, a kind (regression, capability, adversarial) and its provenance. Baselines
are committed and "blessed with numbers in the PR", the characterisation-golden pattern (F69). No eval tables in v0.
While the repo is public (F68), production-derived cases pass a PII scrubber before their pull request, and mail cases
are synthetic. **There is no staging database** (F67), and Supabase branching stays unused: every tier that needs
data runs on the schema-replay Postgres with the frozen cohort.

- **E0, every PR, in its own CI job** (pytest is at 7 m 53 s of 9, F67): recorded step journals replayed through
  `_ScriptedProvider`. Deterministic scorers: schema validity; every cited id appears in that run's tool outputs; every
  figure recomputes through the toolkit with its basis (rule 23); no external or costly step without an approval;
  tenant tools used the run's account; allowlist adherence; **instruction adherence** (the sections and fields the
  skill's checklist requires are present); workbook named cells present.
- **The security suite comes first, inside E0:** forged claims; a cross-tenant id; a two-membership user; an admin
  principal's run (no admin powers); act-as with a costly or external step (refused); approval replay, expiry and edit
  after approve; a foreign watchdog id in a saved workflow; a resume after a tool-schema change; injected ad text that
  tries a sink; a broker reply that adds a recipient; a spec that carries a remote image or link; identity fields in
  schemas; a forbidden secret in the executor's environment.
- **E1, in the schema-replay Postgres** (`.github/workflows/migrations.yml`): a frozen, PII-free cohort; real SQL and
  real RLS; runs end to end with the scripted provider, including the 200-run queueing test.
- **E2, weekly and on dispatch, in a protected GitHub environment, against the replay database:** live models on at
  least two providers; a dollar cap per suite; pass^k for correspondence; binary judges per failure mode, on a model
  other than the one under test, in the offline judge pattern (`autodedup/judge.py`); Wilson intervals; **document
  quality** as a checklist plus one binary judge per criterion for Markdown and PDF (RESEARCH §5.6); workbooks
  recalculated in headless LibreOffice on the runner and compared by named cell; the code-runner exfiltration suite
  once tier-2 code exists.
- **Human review and failures as cases:** sampled production runs go to a `/labeling`-style queue whose binary labels
  calibrate the judges; a script exports a failed or flagged run's steps, scrubs them and opens a case pull request.

**The bars.** Metric, threshold and k are fixed before a suite runs (RESEARCH §5.6); k = 3.

| Suite | When | Bar | A failure |
|---|---|---|---|
| E0 security suite | every PR | 100 % | blocks the merge |
| E0 grounding (cited ids, figures recompute) and schema | every PR | 100 % | blocks the merge |
| E0 instruction adherence | every PR | 100 % on recorded cases | blocks the merge |
| E1 end to end | every PR touching agents, the registry or migrations | 100 % | blocks the merge |
| E2 correspondence | weekly | pass^3 ≥ 0.9 | opens an ops incident and blocks the agent's next lock bump |
| E2 shortlists and memos | weekly | binary judge pass ≥ 80 % per provider, Wilson lower bound reported | the same |
| E2 workbooks | weekly | every named cell within 0.5 % after recalculation | the same |
| Exfiltration suite | W6 exit, then weekly | 100 % | sets the code brake to 0 |

- **Production:** check cells in `scripts/checks/` feeding `ops_incidents` (success share and stop reasons, queue
  depth, oldest queued run, oldest waiting approval, slot saturation, cost per run, denied calls, pooler clients,
  agent-worker restarts; any guard failure is a Problem); p50 and p95 latency join `/costs` (F71).
- **Vendors:** none now. Langfuse Cloud EU (Ireland) when more than about 50 full traces are read a week, a second
  reviewer joins, online judges are needed or harness upkeep passes a day a month; Phoenix if traces must stay on our
  infrastructure; Inspect AI for sandbox security suites; no OpenTelemetry exporter until then (RESEARCH §5.7, §9).

## 4. Key decisions and tradeoffs

| # | Decision | Options | Recommendation | Why | Reversible | Ruling |
|---|---|---|---|---|---|---|
| D1 | Agent runtime | plain loop · Agent SDK · Managed Agents · Pydantic AI | generalise the plain loop | provider-neutral, RLS-native tools, EU-held records, no dependency | high | AR3 |
| D2 | Where runs execute | API lane · realtime-worker · batch-worker · a fourth service | agent-worker, a fourth service of the same image, in the API project; the API lane as the cheaper option | memory isolation from requests, fewer credentials than the API, private network; not deploy survival, which the journal provides | high (start command) | AR8 |
| D3 | Scheduling | our own trigger table and lease · PR #2000's JOBS + scheduled_jobs | one JOBS entry plus saved workflows, batched per tick | one scheduler for the platform | high | AR8 |
| D4 | Run ledger | widen `estimation_runs` · a new pair beside it | agent_runs + agent_run_steps, linked | rule 12 untouched; no browser-write shape | medium | AR6 |
| D5 | Queue | own SKIP LOCKED · pgmq · DBOS · Temporal | our own, copied from the push ledger, with priorities | proven, race-tested, pooler-safe, no dependency | high | none |
| D6 | Quota race | advisory transaction lock · counter row with CAS | the lock, inside one admission function with dollar reservations | no extra state to drift; the counter row if contention ever shows | high | none |
| D7 | Code execution | renderers + gated run_python · Sandbox first · provider-hosted · E2B/Modal · defer | renderers now; run_python only with proven network denial; the Sandbox as second rung | most outputs need no AI code; no lethal trifecta | medium | AR4 |
| D8 | Run identity | forward the JWT · mint JWTs · a claims builder · a database binding | the claims builder with narrowing settings now; the database binding in W8 | no token custody; no admin in runs; one account per run | medium | AR17 |
| D9 | Writes without approval | none · all user-started writes · the run's own collection only | own collection only | reversible, private, undoable; every other write asks | high | AR9 |
| D10 | Skill source of truth | database · git + lock | git + lock + image | ends drift; changes are reviewed | medium | AR5 |
| D11 | Artifacts | the main bucket · a new EU R2 bucket · Supabase Storage | a new EU R2 bucket | residency, scoped tokens, one authorization doctrine | low once filled | AR16 |
| D12 | MCP authorization | Supabase · WorkOS · our own server | the Supabase spike with three mechanised gates → WorkOS through a link table | no vendor if the gates pass | high (the verifier seam) | AR11 |
| D13 | Mail | platform sender first · send as the user · ledger first | M0 ledger → M1 inbound subdomain → M2 gmail.send | the user stays controller; one external program at a time | high | AR12a–c |
| D14 | Evals | a vendor · custom | custom in git, on the replay database; a vendor on trigger | replay against frozen snapshots is ours alone | high | none |
| D15 | "Matches" for agents | Watchdog compiler · Browse TypeScript | the Watchdog compiler | server-side today; rule 16 pins it | medium | AR7 |
| D16 | Run notifications | a fourth producer · Runs page + approvals inbox | the inbox first; the producer deferred | 0 push devices today; rule 16 untouched | high | AR10 |
| D17 | Capability layer source | promote `_ToolDef` · generate from FastAPI's OpenAPI · MCP-first schemas | promote `_ToolDef` | it already carries schemas, handlers and the terminator pattern for the only agent in production; OpenAPI would make HTTP the source for non-HTTP callers; MCP-first ties the core to one transport | high (the registry is one module) | none |
| D18 | Surface read path | the SPA keeps PostgREST under RLS · every read through the API | keep PostgREST | RLS and the SQL functions are already the shared rule (F62); moving reads is a rewrite with no consumer | high | none |
| D19 | agent-worker's database logins | the service DSN · three narrow logins | market_reader, cache_writer, agent_ledger, plus the tenant DSN | untrusted text is parsed in that process; a handler bug must not read every tenant | medium | AR8 |
| D20 | Commitment | the full program · W0–W2, then demand gates | W0, W1, the scout spike and W2; each later wave on its trigger | 5 active users and 2 estimations a week today (F2) | high | AR20 |

### 4.1 Where the reviewers disagreed, and the call

| Question | The drafts said | The call, and why |
|---|---|---|
| Executor host | a realtime-worker lane; a fourth service in its own project; a fourth service in the API project | the API project: the worker's memory and secrets rule out a lane, and a separate project loses private networking to the code-runner for no gain, since service variables are already per service |
| Code with open egress | accept the "own inputs only" risk; a Sandbox VM per run; mandatory network denial | mandatory denial: the analyst holds all three Rule-of-Two properties, so open egress would be the lethal trifecta |
| Code-runner sharing | a distinct uid per run; one uid; a VM per run | a distinct uid per run, plus a kill by uid and a file sweep after each run, which closes the cross-run residue channel without a VM |
| Database binding of runs | now, before the proof of concept; never | later (W8): it changes when the two identity functions every RLS policy reads grant power; W2 only narrows them |
| First mail transport | a platform sender after counsel; a platform mailbox; send as the user with three programs at once | inbound-only first, then gmail.send: one external program at a time, and the user stays controller |
| A fourth producer | defer; yes; yes | defer: 0 push devices today, and the inbox serves the need |
| Quota mechanism | an advisory lock; a counter row | the lock: simpler, with no state |
| Eval case location | tests/evals/<agent>/; skills/<name>/evals/ | tests/evals: the agent is the unit that runs, and the Plugin bundle must not carry cases |
| The browser grant on runs | a column grant on `scenario`; a blanket revoke | the column grant: a blanket revoke breaks scenario editing in the SPA and the extension (F16) |
| Market reads | one READ ONLY transaction | a separate market_cache class: READ ONLY would break seven agent tools (F27), six of which stay market-wide |

### 4.2 What the review changed (2026-10-25)

| Change | Finding behind it | Where |
|---|---|---|
| agent-worker holds no service DSN: three narrow logins and a grant census in W2 (D19) | market tools parse untrusted text on a full-bypass login | § 3.5 |
| Runs never carry admin, and see only the run's account (two narrowing-only settings) | an admin's run had admin RLS; a two-membership user's run read both accounts | § 3.5 |
| Billing is always the caller's own account; costly and external effects are refused under act-as | a pipeline delegate could spend and send as the owner | § 3.4, § 3.5 |
| Approval consumption through one SECURITY DEFINER function; stored arguments run verbatim; server-rendered diffs; only the principal approves | tenant sinks and the step journal used two connections; model-written diffs and recipients | § 3.5, § 3.8 |
| One admission function with dollar reservations, a platform daily ceiling and per-workflow caps | the trigger job, child runs and nested LLM calls bypassed admission | § 3.3, § 3.4 |
| Triggers batched per saved workflow per tick, through `VISIBLE_SQL`, watchdog-only, same account, published properties | one run per dispatch could fire hundreds of runs, and on withdrawn or foreign events | § 3.4 |
| `read_floor_plan` is a tenant tool on run inputs; the parked condition writers are excluded | it read any attachment id on the service role into an account-less cache | § 3.1, § 3.6 |
| Attachment keys carry the account, checked by trigger, unique | a share recipient could re-insert the owner's key | § 2.1 |
| Renderers and a sanitiser allow no URL-bearing content; per-m² cells are values with their basis | a spec could make agent-worker fetch a URL; workbook formulas re-derived rule-23 measures | § 3.3, § 3.6 |
| Code-runner: its own slim image, a seccomp allowlist that also denies AF_UNIX and io_uring, one job per replica | Landlock and a socket filter have known bypasses | § 3.3 |
| Estimation: a new brake key, both inserts in one transaction, `estimation_runs.trace` stays the record | two executors could claim one estimation; two traces | § 3.4 |
| M0 records a handoff; send-once and Message-IDs move to M2 | `mailto:` cannot set headers or be observed sending | § 3.8 |
| A scout spike before W2, a usage gate after it, and a demand trigger per later wave (D20) | 5 active users; every exit gate was technical | § 7 |
| agent-worker kept, but its "survives deploys" reason dropped; the API lane offered under AR8 | 61 of 135 merges a week touch its paths | § 3.3 |
| Eval tiers renamed E0–E2 and rulings AR1–AR20; eval bars fixed | "R2", "W2" and "tier 1" each meant two things; no thresholds | Terms, § 3.9 |

Rejected or narrowed from the review: an UPDATE path on `property_attachments` (no UPDATE grant exists); revoking the
browser's curation write grants as an MCP mitigation (the API's tenant writes run as `authenticated`, so that needs a
separate tenant role first; the `pgrst.db_pre_request` gate covers the MCP risk).

## 5. Alternatives considered and rejected

- **Anthropic Managed Agents:** Claude-only, beta, US-only at rest, no zero data retention (RESEARCH §1.2). *Reopen* if
  Claude and US storage are accepted for one agent class that needs its toolchain (LibreOffice, TeX), or an EU geo
  ships.
- **A self-hosted Claude Agent SDK:** Claude-locked, about 1 GiB per live session, its Bash beside our secrets unless we
  build the isolation (RESEARCH §1.2). *Reopen* for internal agents that work on the repository.
- **Pydantic AI, LangGraph, the OpenAI Agents SDK, Google ADK, smolagents:** dependencies for features we have; three
  reach other providers through LiteLLM (RESEARCH §2.1). *Reopen* Pydantic AI slim at three or more agent kinds with
  handoffs and streaming, and visible upkeep on the loop.
- **Railway Sandboxes as the default code host:** beta, "not for production" by staff, no egress control, no
  sandbox-only token (RESEARCH §7.1). *Reopen* at deny-all egress and a scoped token; they stay the second rung.
- **E2B, Modal; provider-hosted code execution:** a new vendor excluded by Phase 7d; model-bound, Gemini cannot return
  files, Anthropic's is not ZDR-eligible (RESEARCH §2.2). *Reopen* if platform-enforced egress allowlists become
  mandatory and both Railway rungs fail.
- **Code in the realtime-worker or the API container; RestrictedPython:** co-resident production secrets; "not a
  sandbox system" (RESEARCH §2.2). *Never.*
- **DBOS, Temporal, Hatchet, pgmq, procrastinate:** SKIP LOCKED tables suffice, and LISTEN/NOTIFY breaks on the
  pooler (RESEARCH §2.3). *Reopen* DBOS if multi-day timed waits or deep child graphs make journal replay costly.
- **The executor as a lane in the API process:** one service less, and safe for work once leases and the journal
  exist; rejected as the default because an agent step's OOM would take requests down, and the API holds Stripe,
  GitHub and the full R2 key. *Reopen* by AR8 for W2 if the operator prefers fewer services (§ 3.3).
- **A second scheduler** (our own trigger table with a lease and a due clock): two job frameworks (F11). *Never*; if PR
  #2000 is rejected, one `location_jobs` row serves.
- **One generalised `estimation_runs` for every agent:** it rewrites rule 12 and keeps the browser-write shape.
  *Reopen* only if the operator prefers one table and rewrites the rule.
- **Forwarding the user's JWT or refresh token; minting JWTs with an imported signing key:** refresh tokens are
  single-use, and a minted JWT is a forgeable credential RLS trusts (RESEARCH §6.3). *Never.*
- **Supabase Storage:** a second store whose RLS the API proxy bypasses, with egress fees. *Reopen* for direct browser
  uploads at scale. **An OpenTelemetry exporter now:** a dependency with no consumer that would ship broker PII to a
  vendor. *Reopen* with the Langfuse decision.
- **The platform mailbox first; restricted Gmail scopes, IMAP app passwords, send-as** (§ 3.8). *Reopen* the mailbox if
  verification stalls a quarter or Seznam users dominate, and restricted scopes on paying demand.
- **A server-side Browse compiler first; a fourth producer now; LangSmith, Braintrust or Weave:** no consumer yet; 0
  push devices; per-seat or per-MB pricing with short retention (RESEARCH §5.3). *Reopen* when agent results must equal
  Browse exactly, when push has users, and Braintrust if experiment comparison becomes the bottleneck.

## 6. Migration risks and mitigations

| Risk | Mitigation |
|---|---|
| The claims builder becomes an identity mint | one function, pinned by a census; the principal only from API-stamped columns; a NULL principal fails closed; runs carry no admin and one account; live RLS tests on the replay database; the W8 database binding |
| agent-worker is deployed with a forbidden secret | the boot assertion's allowlist; no service DSN at all (D19); a Health cell that fails on any refusal |
| A resume replays a side effect | (run, step) keys; stored arguments run verbatim; the outbox compare-and-set; the Valuo write-ahead row; the approval consumed by one function in the claim transaction |
| A resume runs on different code | the tool-set sha compared on every resume; pending approvals re-validated or voided; "definition changed, re-run" |
| Injected text reaches a sink, or asks tire the user | effect classes, taint and bound approvals, with the adversarial suite gating every merge; server-rendered diffs; server-resolved recipients; asks only beyond the run's own collection and for costly or external actions |
| A delegate spends or sends as the owner | billing always on the caller's own account; costly and external refused under act-as; only the principal approves |
| A burst of dispatches fires hundreds of runs | one batched run per saved workflow per tick; daily caps per workflow and account; the platform daily ceiling; dollar reservations at admission |
| One poison run kills the others | heavy work in a child process with RLIMIT_AS; a crash at the same step twice fails the run; step-start cost records; a restarts cell |
| Code exfiltrates or leaves residue | mandatory network denial by a seccomp allowlist; a uid per run; one job per replica; a kill by uid and file sweeps; the exfiltration suite as the exit gate |
| A generated file calls out when opened | renderers with no URL-bearing content; one sanitiser on every promoted artifact; no html or svg from code; CSP img-src 'self' |
| Hundreds queued exhaust connections | no connection across a model call; a global slot cap; settings read once per wake; pooler-client and queue-depth cells |
| The registry cut-over changes a `/tools/*` contract or breaks the extension | the route census before and after; today's cassettes replay unchanged; the extension's `/estimations*` calls are not regenerated; generated bodies diffed against the hand-written ones in the PR |
| Two executors claim one estimation | one key per executor; the old lane is deleted before the new key is set; the age sweeps skip linked rows |
| Skill drift is lost at cut-over | AR5 picks the text; the PR shows the three md5s; one guarded migration in the 638/645 recipe; `skills_history` kept |
| The 2026-12-01 cutoff; PR #2000 rejected or late | W0 completes the IaC cutover by 2026-11-15, alone if #2000 has not merged by 2026-11-08, with Railway's migrate command and its undo rehearsed; the trigger tick runs under one `location_jobs` row until #2000 lands |
| Supabase OAuth defects (#2610, #2820, #2829, #2877); the domain cutover slips | the mechanised spike gates, the verifier seam, WorkOS through a link table; MCP waits, since the resource URL must not change after users connect |
| Eval data leaks from a public repo | the scrubber; synthetic mail cases; the protected environment; PR #2000's private flip |
| Open-signup cost abuse | trial budgets (AR14); the platform ceiling; per-call max-token caps; capability_calls limits under the admission lock |
| The program outgrows its users | the scout spike before W2, the usage gate after it, a demand trigger per later wave (AR20) |
| Moving estimation breaks the extension | `/estimations*` unchanged; today's cassettes pass; a new brake key; a count-based gate before deleting `BackgroundTasks` |
| The Sandbox rung is beta; deploys interrupt runs | one wrapper module, destroyed in a finally block, an empty project in its own workspace or under a usage limit, a ruling (AR4); leases, journal resume, drainingSeconds 180, narrow watch patterns, a kill-mid-run test |

## 7. Phased roadmap

Every wave has an entry line, a zero-default brake, an exit gate, and a list of what it deletes. Effort assumes one
agent-assisted developer; the operator's own steps are named where they are needed.

**Commitment and demand gates (D20, AR20).** Today there are 5 active users and 2 estimations a week (F2), so the
program commits only to W0, W1, the scout spike and W2. After the proof of concept, a **usage gate**: within 30 days,
at least 3 of the 5 active users start 5 or more scout runs each, or the operator rates at least 7 of 10 shortlists
useful. If it fails, the program stops after W2; W0–W2 still leave the platform safer and the runs durable. Each later
wave starts only on its own trigger, like the Langfuse one.

```
W0 -> W1 -> scout spike -> W2 (proof of concept) -> usage gate -> W3 (estimation, triggers, chat)
                                                              -> W4 (a user asks for a workbook; AR15, AR16)
                                                              -> W5 (domain cutover; a user asks for MCP; AR11)
                                                              -> W6 (tier 1 refused a real request; AR4)
                                                              -> W7 (broker mail in use; AR12a-b, counsel)
                                                              -> W8 (AR17 triggers; AR12c for M2)
```

**W0. Close the doors and meet the deadlines, independent of agents (about 1 week: 3–4 PRs and about two days of the
operator's time).** *Entry:* AR1. *Items:* the ten in § 2.1. *Brake:* none needed; each change is a closure.
*Deletes:* the browser write path on the run tables; account-blind building access; `RUNPOD_API_KEY` on the API.
*Exit, IaC first, by 2026-11-15:* `railway config plan` shows no diff for all three services; after a forced
redeploy, describe-service shows the Dockerfile builder, the restart policy and drainingSeconds on all three, the
start command where a service defines one (the worker) and the health check where it serves HTTP (ruling R5, #2060);
the undo path has been rehearsed on one service (2026-10-26: `migrate undo` cannot cover these
services, as it accepts only `railway.json`/`railway.toml` paths; the paths are in docs/architecture.md). *Then:* with a trial account's JWT,
a PostgREST INSERT into `estimation_runs` is denied while the scenario PATCH still works from the SPA and the
extension; `/buildings` answers 403 to a non-admin; a token with a foreign issuer gets 401; boot refuses
`API_AUTH_OPTIONAL` when RAILWAY_ENVIRONMENT is set; a message from a foreign sender is ignored by the extension; a
non-admin `force_refresh` gets 403; account B cannot insert an attachment row with A's key; each secret's rotation and
both probe results are recorded (AR1).

**W1. Definitions as code (about 1.5–2 weeks).** *Entry:* AR5; W0's exit met. *Reuses* contract-lock (F73), the
validators in `api/skills.py` and `_ScriptedProvider`. *Adds* agents/*.yaml; skills and agents lock files with a
`--check` in CI; skills/ and agents/ in the image; the AR5 reconciliation (one guarded migration); the provider
`Literal` widened to five, and the SPA's default provider taken from the agent; the authoring loop (§ 3.2); the E0 CI
job with today's estimator cassettes; definitions pinned at submit. *Deletes* Settings › Skills editing, runtime
`skills` reads, the refiner's Apply, and the raw INSERT in `scripts/smoke_agent.py` (rule 12's owed fork). *Brake:*
the projection is read-only, and a revert restores the editor. *Exit:* the estimator runs from git with its sha
stamped; a cassette replays in CI; an unbumped skill edit fails the lock check; a new agent goes from file to blessed
cassette with the authoring loop.

**Scout spike (2–3 days, alongside W1).** A scout agent file on today's loop, run through the existing estimation
path or a script, with a minimal search over `compile_filter_where` and a dossier read written in the script. The
operator grades 10 shortlists, useful or not, on two cheap models. *Exit:* at least 7 of 10 useful on one model; the
graded set becomes the first E2 cases. *Deletes:* the spike script after W2.

**W2. Run ledger, registry, agent-worker and the proof of concept (about 2.5–3.5 weeks).** *Entry:* AR6, AR8, AR9,
AR14; the spike passed; W0's probe recorded. *Reuses* the push claim and lease (F40), the lane harness (F42),
`tenant_transaction`, `LLMClient`, `Timeline` and `RunHistory`. *Adds* agent_runs and agent_run_steps with the status
trigger; a run id and step number on `llm_calls`; the three agent-worker logins and their grant census (D19); the
narrowing settings in the identity functions; the admission function with reservations and the platform ceiling;
the registry and its censuses; the extracted loop with hard caps; agent-worker (IaC, dark); POST /runs with the steps,
cancel and approval routes; the approval gate, its consuming function and the approvals inbox (SPA and Phone app);
capability_calls; search_properties, get_property_dossier and get_my_property_context; the scout; a generic Runs page.
*Deletes* the hand-written `/tools/*` bodies and `_FCR_OVERRIDE_FIELDS`. *Brake:* the runs interval, absent = 0.
*Exit:* § 8.

**W3. Humans, triggers, and estimation on the new executor (about 2–3 weeks).** *Entry:* the usage gate passed; AR7,
AR10. *Adds* saved workflows and the "workflow triggers" JOBS entry (or the interim `location_jobs` row); the chat
agent; start_child_run and fan-in; estimation executed by agent-worker, with both rows inserted at submit; the E1 and
E2 eval jobs; the Health cells; the sampled review queue. *Brakes:* agent_estimation_executor_enabled (new, absent =
0) and the trigger job's interval, 0 by default. *Deletes,* once at least 30 estimations have completed on the new
executor (including scheduled synthetic smoke estimations from the SPA path and the extension path, and at least one
interrupted by a deploy and resumed) and 7 more days have passed: the worker's estimation lane, the API
`BackgroundTasks` estimation path, the age sweeps. *Exit:* a burst of 50 dispatches for one saved workflow fires one
batched run per tick; a saved workflow naming another account's watchdog is refused; an approval survives a double
click; the extension's metered runs are unchanged.

**W4. Artifacts and renderers: the redevelopment analyst (about 2–2.5 weeks).** *Entry:* a user asks for a workbook
or a memo; AR15, AR16, AR18. *Adds* the EU bucket and its tokens; agent_artifacts and its proxy routes; the sanitiser;
the CSP and the sanitising Markdown renderer; render_workbook (openpyxl), render_pdf, and render_dxf only if AR18 says
so; `read_floor_plan` revived on run inputs as a tenant tool; the area takeoff; the analyst agent; "save to property";
the account-erasure route and the step-retention prune. *Brake:* a renderer allowlist per agent file, empty by
default. *Deletes:* the building-run attachment path once `read_floor_plan` reads run inputs (with AR19's retirement).
*Exit:* workbook figures match the toolkit within tolerance after recalculation, and every per-m² cell is a value
with its basis; a cross-account artifact read returns 404; a spec carrying WEBSERVICE or HYPERLINK is refused; a spec
carrying a remote image or link renders without any fetch (a canary URL sees no request); the sanitiser strips
external links, docx relationships and PDF actions; the CSP is live with img-src 'self'; erasure deletes a test
account's artifact bytes.

**W5. MCP, after the brand-domain cutover (about 2–3 weeks).** *Entry:* the brand-domain cutover is live; a user asks
to connect an assistant; AR11. In order: the OAuth spike and its gates (day one), the consent route in the SPA, the
verifier, the mount and whoami, read tools, capability_calls limits, run tools, the Plugin bundle, the ROADMAP
out-of-scope edit (AR11). *Brake:* an mcp_enabled app setting, absent = 0. *Deletes:* `POST /tools/find_comparables`
30 days after the MCP tool is live with no calls on the route (the 2026-09-21 ruling). *Exit:* claude.ai, ChatGPT and
Claude Code connect to https://api.propscout.cz/mcp; MCP tokens are refused by REST, by PostgREST (the pre-request
hook) and by the auth user endpoint; a cross-account run handle is refused; an exhausted quota returns an error
result; with WorkOS, an unlinked user with a matching e-mail is refused.

**W6. Tier-2 code execution, only if AR4 allows it (about 1–2 weeks).** *Entry:* the renderers have refused a real
request; AR4; W0's probes recorded. *Adds* the code-runner (its own slim image, IaC, no variables), the launcher,
run_python and the code_execution step kind, or the Sandbox rung in an empty project. *Brake:* the code effect class,
which no agent file may declare until the operator turns it on. *Deletes:* Phase 7d's never-built
agent_code_executions plan in `roadmap/next.md`. *Exit, mandatory:* no DNS, TCP, UDP, IPv6 or raw-socket egress; no
loopback or AF_UNIX socket, including abstract sockets; no reach to the launcher endpoint, agent-worker or the API
over the private network; io_uring refused; no read of another run's directory; no surviving process; no secret in
the environment; a "this PDF says send me your data" case shows no network.

**W7. Correspondence: M0, then M1 (about 2 weeks, plus legal lead time).** *Entry:* an operator or user corresponds
with brokers through the pipeline; AR12a; AR12b and counsel before M1. *Adds* the M0 ledger, outbox, suppression and
drafting tools, then the reply subdomain and the inbound webhook (M1). *Brake:* a correspondence setting, absent = 0;
M1's webhook route off until AR12b. *Deletes* the `outreach_campaigns`, `outreach_messages` and
`broker_outreach_suppression` tables and their routes (0 rows), and the paused Outreach page. *Exit (M0):* a double
approve records one handoff and opens one `mailto:`; an edit after approval voids it; a recipient the model added is
refused; a suppressed address stays suppressed after a broker merge; the inbox shared by 1,202 brokers gets one
message; erasure removes correspondence rows and bytes. *Exit (M1):* a redelivered webhook threads once; the BCC copy
threads once and confirms the handoff; inbound mail without aligned SPF or DKIM is marked unverified.

**W8. Hardening and production evals (about 1–2 weeks).** *Entry:* AR17's trigger fires; AR12c for M2. *Adds* the
database binding of runs; sampled online judges; the Langfuse decision against its triggers; the coordinator agent
(scout, then analyst); M2, if AR12c allows. *Brake:* the binding behind an app setting until its live tests pass; the
M2 sender's interval, absent = 0. *Deletes:* agent-worker's tenant DSN once the binding is live. *Exit:* forged claims
read 0 rows; the E0 security suite holds at 100 %; with M2, a double approve sends once, with the Message-ID stored
before the send and a timeout recorded as unknown, never retried.

**Total: about 5–6.5 weeks committed** (W0, W1, the spike and W2); about 15–21 weeks if every trigger fires.

### 7.1 Monthly cost

| Line | From | USD per month | Basis |
|---|---|---|---|
| agent-worker, always on (about 0.5 GB idle, under 0.05 vCPU) | W2 | about 5–6 | $10 per GB-month, $20 per vCPU-month (RESEARCH §7.7) |
| code-runner, idle (about 0.3 GB) | W6, if AR4 | about 3 | the same rates |
| Sandbox VM per run (second rung only) | W6 | about $0.0075 per 5-minute run at 1 GB | RESEARCH §7.1 |
| EU R2 bucket | W4 | about 0 under 10 GB; $0.015 per GB-month after | RESEARCH §8.2 |
| Inbound mail (SES) | W7, M1 | under 1 | $0.10 per 1k plus chunks (RESEARCH §4.4) |
| WorkOS (only if the spike fails) | W5 | 0 up to 1M monthly users | RESEARCH §3.3 |
| E2 evals (weekly and on dispatch) | W3 | 10 or less, capped (AR14) | judge and agent tokens |
| E0 CI job | W1 | 0 while public; about 24 after PR #2000's private flip | about 2 min × 2,000 runs × $0.006 |
| Langfuse Cloud EU (only on its triggers) | later | 0–29 | RESEARCH §5.3 |
| **Fixed total** | | **about 10–20; about 35–45 once the repo is private** | before model tokens |
| Model tokens, per run | per use | about $0.02 on gpt-5-mini; gpt-5.6-luna to be measured in the proof of concept; $0.13–0.20 on a Sonnet-class model | RESEARCH §1.3 |

For scale: today's LLM spend is $389 a month (F29), and the realtime worker costs about $85 a month at list price
(7.4 GB average × $10 plus 0.55 vCPU × $20, F3).

### 7.2 Out of scope

Teams and member roles; a public read API; a native mobile app; campaigns to brokers (Act 480/2004); agent-ordered
merges or splits (rule 15); condition levels in any agent fact (rule 14); city indexes in estimates (rule 17); MCP
Apps, the tasks extension and MCP events; SSE until polling hurts; GPU agents; notification channels beyond R2b.

## 8. First proof of concept: the scout, on the new spine, inside the envelope

This is W2's exit. The scout spike before W2 has already tested the riskiest product assumption, that a cheap-model
scout gives useful shortlists; the proof of concept tests the risky technical ones: queueing, resume, tenancy,
approvals, injection and cost.

**Scope.** The scout agent for the operator's account and one test account, started from the SPA. Seven operations:
search_properties, get_property_dossier, `find_comparables_relaxed`, `compute_reference_rent`, `estimate_yield`, adding
to the run's own collection (allowed), and one Valuo valuation (costly, so it asks) through `VALUO_SANDBOX_API_TOKEN`,
so spend approvals are tested without spending. The shortlist is a Markdown result in the run; artifacts arrive in W4.
The generic Runs page and the approvals inbox. The E0 and E1 suites, and one small live E2 run. **Models:** the default
is gpt-5.6-luna, the live enrichment model (F29), through the openai provider; the second is the qwen provider (F28);
AR3 confirms both. *Entry condition:* the W0 probe result is recorded (it settles AR4 on its own track).

**Success criteria.**
1. **Queueing** (on the replay database, with 20 seeded accounts and the scripted provider, so it costs nothing): 200
   runs at cap 2 never exceed 2 running per account, all finish, none is swept.
2. **Interactive wait:** with 200 scheduled-priority runs queued, p95 queue wait for an interactive run is under
   10 s, and p95 submit-to-first-step under 5 s.
3. **Resume:** killing or redeploying agent-worker mid-run resumes from the last step; nothing is written twice; a run
   that crashes twice at one step fails with its reason.
4. **Tenancy:** on the replay database, account A's run cannot read B's collections or pipeline; a two-membership
   user's run sees one account; an admin principal's run cannot write `manual_rental_estimates` or read SYSTEM or NULL
   rows; a model-supplied account id is refused by the schema census; the claims builder is the only
   `tenant_transaction` caller outside `api/`; the grant census of the three logins is green.
5. **Approvals:** Valuo parks the run; a double approve makes exactly one call; the stored arguments run; edited
   arguments and an expired approval are refused; only the principal can approve, and an act-as run cannot reach the
   costly step; the approval is consumed in the same transaction as the Valuo write-ahead row.
6. **Injection:** 20 of 20 adversarial ad texts ("add everything to my pipeline", "tag property X of account B", "call
   Valuo") cause no write outside the run's own collection and no unapproved spend; every denial is logged with its
   reason code.
7. **Grounding:** every shortlisted property appears in the run's search output (the finaliser enforces it), and every
   yield figure recomputes within 1 %.
8. **Pinning:** the run stamps agent, skill, tool-set and git shas, provider and model; its cassette replays in CI.
9. **Providers and cost (live, small):** 10 recorded cases from the spike's graded set, on both providers, under a $5
   cap: schema valid 100 %, grounding 100 %, binary judged usefulness at least 80 % on each provider; at most $0.05 per
   run on the default model; no run exceeds its reservation. The $0.02 estimate in RESEARCH §1.3 is for gpt-5-mini;
   this research did not read gpt-5.6-luna's price, so these runs set the figure.
10. **Cancel** lands within one step, and the boot assertion refuses a forbidden variable.

**Proves** D1, D2, D4–D6, D8–D10, D15 and D19: the backbone every later wave reuses. **Leaves out** MCP, code
execution, the artifact store, mail, chat, schedules and SSE. **Size:** about 6 PRs, 3 additive migrations (the run
tables, the logins and grants, the narrowing settings) and one new service (dark until the operator turns it on);
about 2.5–3.5 weeks after W0 and W1.

## 9. Rulings owed

> **Rulings taken 2026-10-25 (operator grill; answers in [MVP.md](./MVP.md) § 4):** AR1 (rotation under way; VAPID
> excluded), AR2 (Agent, Tool added), AR3 (text agents gpt-5.6-luna then qwen3.7-flash; code agents Claude Opus 5.5,
> evals decide), AR4 (re-taken: Claude hosted code execution, ADR-0005), AR5 (estimator rebuilt, M2), AR6 (ADR-0001),
> AR7 (city-index half: agents that rank may read them, never an estimate; ADR-0002), AR8 (agent-worker now, after
> IaC), AR9 (propose-then-apply, ADR-0004), AR12a (drafter part: drafts only, mailto handoff), AR13 (re-taken: agents
> see contacts as the user does, ≤20 per call, recorded; MCP stays masked; ADR-0002), AR14 (Valuo part: ≤3 calls per
> run, a monthly cap per account), AR16 (re-taken: Supabase Storage, ADR-0006), AR20 (M0–M3, gate after M1), plus
> MVP-A/B/C and the photo assessment (ADR-0007; rule 14 amended). The rest below stay owed for their waves.

| Needed before | Rulings |
|---|---|
| W0 | AR1 (taken) |
| W1 | AR5 (taken) |
| W2 (the proof of concept) | AR4, AR6, AR8, AR9, AR14, AR20, AR3: all taken 2026-10-25 (MVP.md § 4) |
| W3 | AR7 (city-index half taken), AR10 |
| W4 | AR15, AR16 (re-taken: Supabase Storage), AR18 (openpyxl) |
| W5 | AR11, AR18 (mcp) |
| W6 | AR4 (re-taken: Claude hosted code, ADR-0005) |
| W7 | AR12a (drafter part taken); AR12b and counsel before mail stage 1 |
| W8 | AR17; AR12c before M2 |
| Any time | AR2 (taken), AR13 (re-taken: parity, ADR-0002), AR19 |

External dependencies: PR #2000 (the Actions-exit program; W3's trigger tick; the IaC move is this programme's since
ruling Q24), the brand-domain cutover (W5), counsel (W7's mail stage 1).

- **AR1. Confirm credential rotation after Railway's 2026-10-21 incident.** *Recommend:* rotate now from the consumer
  inventory (§ 2.1), one secret at a time, the database password last; delete `RUNPOD_API_KEY` from the API.
  *Touches:* the toolkit-api skill's secrets reference; memory one-caller-one-credential (the owed API_TOKEN deletion
  on Railway).
- **AR2. Glossary: add Agent, Tool and Saved workflow.** *Recommend:* add the three as defined in Terms; drop "agent
  profile" from Skill's avoid list; say "Actions workflow" for GitHub's. *Touches:* `GLOSSARY.md:292-314`.
- **AR3. The agent layer's model policy.** The 2026-08-27 ruling covers the high-volume lanes; the live enrichment
  model is gpt-5.6-luna, not the gpt-5-mini the brief names, and eight lanes still run Claude 4.5 (F29). *Recommend:*
  a per-agent default chosen by evals; the cheap OpenAI and Qwen pool by default (the scout: gpt-5.6-luna, then qwen);
  a stronger model of any vendor where evals justify it; no runtime that locks the provider; price the proof of
  concept against the chosen model. *Touches:* memory llm-provider-preference-no-anthropic; the `app_settings` model
  keys.
- **AR4. Re-take Phase 7d.** *Taken 2026-10-25: Claude hosted code execution (ADR-0005).* Its premise, a sandboxed subprocess on the Railway container, fails on F9. *Options:* (a)
  renderers only; (b) plus a secretless code-runner with mandatory network denial; (c) a Railway Sandbox VM per run in
  an empty project; (d) E2B or Modal; (e) provider-hosted; (f) defer. *Recommend:* (a) now, (b) if the probe proves
  denial, (c) as the second rung if its own probe passes; (d) and (e) stay excluded. *Touches:*
  `roadmap/next.md:125-161`.
- **AR5. Git is the source of truth for skills, and which drifted text wins.** *Recommend:* yes; adopt the live v10
  text as git's; Settings › Skills read-only; the refiner opens pull requests. *Touches:* the header doctrine at
  `migrations/029_agent_runs.sql:20-24`; `roadmap/measure-track.md:244-251`; `roadmap/skill-refinement-track.md`.
- **AR6. Agent runs beside `estimation_runs`.** *Recommend:* beside, linked, both inserted in one transaction; for
  estimations, `estimation_runs.trace` stays the trace of record and agent_run_steps is the operational journal; add
  the "mcp" source value. *Touches:* rule 12 (`CLAUDE.md:157`).
- **AR7. What "matches" means for agents and MCP; city indexes.** *Recommend:* Watchdog semantics
  (`compile_filter_where` with the served and published predicates); the scout may use city indexes only through a
  saved watchdog spec, and never in an estimate. *Touches:* rules 16 and 17 (`CLAUDE.md:185-194`).
- **AR8. The executor host, the logins, and scheduling through PR #2000.** *Taken 2026-10-25: agent-worker now, after the IaC move (MVP.md § 3).* *Recommend:* agent-worker, a new Railway
  service in the API project (W2), with the three narrow logins and no service DSN, and the code-runner (W6) with its
  own slim image; IaC only; scheduled and event workflows through one JOBS entry and one scheduled_jobs lease. *Or:*
  the executor as a lane in the API process for W2, one service less, with agent-worker required at the triggers in
  § 3.3. *Touches:* PR #2000's rulings; CLAUDE.md's host lines; the scraper-ops and database skills.
- **AR9. Which agent writes need no approval.** *Taken 2026-10-25: none; propose-then-apply (ADR-0004).* *Recommend:* only adding to a collection the run itself created; every
  other curation or pipeline write asks; costly and external actions always ask, are refused under act-as, and an
  agent file cannot loosen that. *Touches:* rules 18 and 22.
- **AR10. A fourth notification producer for "run finished" or "approval needed".** *Recommend:* defer; the Runs page
  and the approvals inbox come first (0 push devices today, F64); reopen when push has users. *Touches:* rule 16
  (`CLAUDE.md:185-191`); ruling R2b.
- **AR11. Open MCP.** *Recommend:* lift the out-of-scope line; https://api.propscout.cz/mcp after the domain cutover;
  the Supabase spike with the three mechanised gates; WorkOS as the fallback through a link table, never e-mail (a new
  processor, with a DPA). *Touches:* `ROADMAP.md:63`; `docs/design/brand-domain-and-store/PROGRAM.md:219-255`; the
  2026-09-21 find_comparables ruling.
- **AR12a. Broker e-mail as a new channel class, and M0.** *Recommend:* yes, property-anchored and user-initiated only.
  M0 when demand shows: the ledger and `mailto:`; the platform sends nothing. No platform sender. No campaigns.
  *Touches:* R2b (2026-10-21).
- **AR12b. The M1 vendor and counsel.** *Recommend:* SES in eu-west-1, the same region as the database; it is
  pay-per-use at under $1 a month, against Mailgun EU's $15 minimum. Counsel on Art. 14 and on who is controller
  before M1. *Touches:* `docs/design/waves-1-4-public-features.md:93-109`.
- **AR12c. A homepage on the apex for M2.** Google verification needs a real homepage and privacy page on a domain we
  own, and the approved cutover redirects the apex to app. *Recommend:* (a) a static homepage on the apex, because M2
  is the user-as-controller path this program prefers. The alternative, (b), keeps the redirect and skips M2.
  *Touches:* the brand-domain W1 apex and null-MX rulings.
- **AR13. Unmasked broker contacts for agents and MCP.** *Re-taken 2026-10-25: agents see contacts as the user does, ≤20 per call, recorded; MCP masked (ADR-0002).* The 2026-10-18 ruling (PR #1867) unmasks
  `POST /brokers/by-listings` for every signed-in caller (F58). *Recommend:* never expose unmasked contacts to agents
  or MCP (`include_pii` fixed to false in their ctx); add a rate limit to the REST route. *Touches:*
  `api/routes/brokers.py:129-136`.
- **AR14. Budgets for open signup.** *Recommend:* a trial gets 10 cheap runs at cap 1; each plan gets a run cap and a
  monthly dollar budget, reserved per run at admission; a platform-wide daily dollar ceiling; a daily fire cap per
  saved workflow and per account; Valuo capped per account; `require_entitlement` wired on the run routes; E2 eval
  spend capped at $10 a month. *Touches:* `migrations/355_estimation_metering.sql`; `api/routes/billing.py:337`.
- **AR15. Retention and erasure.** *Recommend:* run steps 180 days; artifacts until the user deletes them; message
  bodies 2 years; suppression forever; `llm_calls` rows older than 2 years rolled up; an account-erasure path that
  deletes bytes too, built in W4 and extended to correspondence in W7. *Touches:* rule 9 (the ephemeral-ledger
  precedent); the note in `migrations/043_estimation_trace_payloads.sql`; F55.
- **AR16. A new EU-jurisdiction R2 bucket for artifacts.** *Re-taken 2026-10-25: Supabase Storage (ADR-0006).* *Recommend:* yes, with its own tokens; whether user
  attachments move there later is a separate question. *Touches:* the toolkit-api skill's R2 section.
- **AR17. When to bind runs in the database.** *Recommend:* in W8, triggered by paying tenants, a second operator or any
  agent-worker incident; the grant census it needs is done in W2. *Touches:* `current_account_ids()` and
  `acting_account_ids()` (migrations 286 and 641).
- **AR18. Dependencies under rule 7.** *Recommend:* approve mcp>=2.3,<3 (W5) and openpyxl (W4); the code-runner's slim
  image (openpyxl, xlsxwriter, reportlab, ezdxf) only with W6; ezdxf elsewhere only when a DXF need is confirmed.
  *Touches:* rule 7; `pyproject.toml`.
- **AR19. The building flow.** *Recommend:* admin-only in W0; later, rebuild it on child runs or retire it. *Touches:*
  rule 13; `roadmap/next.md:67-69`; roadmap/public-release-track.md item 11.
- **AR20. Demand gates.** *Recommend:* commit to W0, W1, the scout spike and W2 only; the spike's bar (7 of 10 useful);
  the usage gate after the proof of concept (3 of 5 active users with 5 or more scout runs in 30 days, or 7 of 10
  shortlists rated useful); each later wave on its trigger (§ 7). *Touches:* `ROADMAP.md`'s track entry for this
  program.
