# Agent platform: external research (2026-10-25)

> **Status: RESEARCH for the agent-platform proposal ([PROGRAM.md](./PROGRAM.md)). Facts read on 2026-10-25.**
> Eight research tracks, one section each. Confidence follows the source reports: **[V]** verified today on an
> official page or in source code; **[L]** likely (a secondary source, a forum or staff post, or an inference from
> official text); **[U]** unverified or conflicting. Prices are list prices in USD. Repository facts are cited from
> [AUDIT.md](./AUDIT.md) as F-numbers. § 9 lists where the program departs from a recommendation here, and why.

**Terms used here.** *Runtime*: the code that runs an agent's loop (call the model, run the tools it asks for,
repeat). *Sandbox*: an isolated place where AI-written code runs. *Egress*: network traffic leaving a machine.
*Durable execution*: work that survives a crash or a deploy because each step is written down. *Authorization
server*: the service that signs a user in for an outside app and issues its tokens. *Eval*: a repeatable, graded test
of agent output. *OTel* (OpenTelemetry): the open standard for shipping traces to a viewer. *GA*: generally available,
no longer beta. *SLA*: a contractual uptime promise. *ZDR*: zero data retention at the model provider.

## 1. Agent runtimes: Claude Agent SDK on Railway, Anthropic Managed Agents, the plain loop

### 1.1 The three options

- **(a) Claude Agent SDK, self-hosted on Railway.** The package runs a bundled Claude Code binary as one subprocess
  per session (shell, working directory, local transcripts) with Claude Code's harness: tools, hooks, permissions,
  subagents, skills, MCP, budgets. Custom tools run in-process. Claude only: Anthropic "doesn't support routing Claude
  Code to non-Claude models through any gateway". [V]
- **(b) Anthropic Managed Agents (beta).** Anthropic hosts the loop: a versioned agent, an environment and one session
  per run, in a fresh cloud container or on a worker we host; vaults, memory, cron, graded outcomes, multi-agent
  rosters, permission policies, dollar budgets. Claude only; not on Bedrock, Vertex or Foundry. [V]
- **(c) The repo's plain loop.** `run_agent_estimation` with typed Python tools over Postgres and the provider-neutral
  client (F25, F28). No code sandbox.

### 1.2 Findings by criterion

- **Operations.** (a) runs long-lived processes whose disk is lost on restart, needs a session store, and turns every
  SDK upgrade into a harness upgrade. (b) has nothing to host, but its event stream has no replay, webhooks are set by
  hand in the Console, and archive is permanent. (c) already runs in production. [V]
- **Isolation.** (a) documents isolation but does not provide it; its Bash inherits the parent environment "including
  any secrets" unless scrubbed. (b) cloud gives a fresh container per session (Ubuntu 24.04, 8 GB RAM, 10 GB disk;
  inputs read-only, outputs through a Files API). (c) has typed tools only and no agent-written code yet. [V]
- **Python dependencies.** (b) preinstalls NumPy, pandas, openpyxl, python-docx, pypdf, LibreOffice, pandoc and TeX;
  extra packages open PyPI "by host, not by operation", and environments are not versioned. (a), (c): our image. [V]
- **EU residency.** Managed Agents stores data and runs code in the US only, is not eligible for zero data retention
  (ZDR), and keeps transcripts "until you delete them", self-hosted included. The SDK and the plain loop keep
  transcripts in our storage; EU-only Claude inference exists through Bedrock EU (+10 %), 5.5 availability [U]. [V]
- **Scale.** Managed Agents: 300 creates and 1,200 reads per minute; no published session cap; a rate-limited
  scheduled run is not retried. Every Claude option shares organisation token limits and monthly spend caps (Start
  $500, Build $1,000, then 429). The SDK needs about 1 GiB of RAM per live session. [V]
- **Lock-in.** (a) and (b) are Claude-only, and (b) is a proprietary beta API; SKILL.md skills and MCP tools stay
  portable [L for the open-format claim]. (c) runs on five registered providers (anthropic, gemini, openai, qwen, oss;
  F28). [V]

### 1.3 Economics of a typical run (15 tool calls, about 16 model requests, 60k input and 8k output tokens)

| Model | Uncached | Cached (40k read, 20k written) | + Managed Agents runtime (5 min) | Per 1,000 runs (cached; + MA runtime for Claude rows) |
|---|---|---|---|---|
| Haiku 5.5 | $0.010 | $0.0069 | $0.0136 | ≈ $14 |
| Sonnet 5.5 | $0.20 | $0.134 | $0.141 | ≈ $141 |
| Opus 5.5 | $0.40 | $0.268 | $0.275 | ≈ $275 |
| gpt-5-mini on the plain loop | $0.031 | ≈ $0.022 | n/a | ≈ $22 |

Managed Agents adds $0.08 per session-hour, billed only while running. A Railway container run costs about
$0.001–0.002; a Railway Sandbox about $0.0075 for 5 minutes at 1 GB. **The model drives cost, not the runtime.** The
real cost of (a) and (b) is that they make the provider choice at the runtime layer. [V]; arithmetic from verified
rates.

### 1.4 Decision matrix

Scores: ++ strong, + adequate, − weak, −− blocking for this repo.

| Criterion | (a) SDK on Railway | (b1) MA cloud | (b2) MA self-hosted | (c) Plain loop |
|---|---|---|---|---|
| Operations | − process per session, own isolation | ++ nothing to host | − our workers and isolation | + already running |
| Per-run isolation | − not on a plain Railway container | ++ fresh container | − ours to build | ++ for typed tools |
| Tenant-scoped tools (RLS) | + in-process MCP | + custom tools run on our side | + custom tools | ++ native |
| Model pluggability | −− Claude only | −− Claude only | −− Claude only | ++ five registered providers (F28) |
| EU residency, ZDR | + ZDR; EU via Bedrock [U] | −− US only, no ZDR | − history still in the US | ++ under our control |
| Multi-agent, cron, graded outcomes built in | + subagents | ++ | ++ | − build (cron and lanes exist) |
| Maturity | GA library | beta | beta | in production |

### 1.5 Recommendation (as reported)

Keep (c) and generalise it into a run executor driven by agent definitions; borrow two ideas, a hard per-run budget
and an always-ask policy on consequential tools, not the runtimes. Do not self-host the Agent SDK for customer runs;
it fits internal agents that work on the repository. For code-executing agents, decide between a Railway Sandbox per
run (path Y) and Managed Agents as a code backend only (path X) by proof of concept; path X wins only if the operator
accepts Claude-only, US storage and a beta API.

**Sources.** https://code.claude.com/docs/en/agent-sdk/hosting · https://code.claude.com/docs/en/agent-sdk/overview ·
https://code.claude.com/docs/en/agent-sdk/custom-tools · https://code.claude.com/docs/en/agent-sdk/permissions ·
https://code.claude.com/docs/en/agent-sdk/secure-deployment · https://code.claude.com/docs/en/llm-gateway · https://code.claude.com/docs/en/sandboxing ·
https://platform.claude.com/docs/en/managed-agents/overview · https://platform.claude.com/docs/en/managed-agents/choosing-an-environment ·
https://platform.claude.com/docs/en/managed-agents/environments · https://platform.claude.com/docs/en/managed-agents/cloud-sandboxes-reference ·
https://platform.claude.com/docs/en/about-claude/pricing · https://platform.claude.com/docs/en/api/rate-limits ·
https://platform.claude.com/docs/en/manage-claude/data-residency · https://platform.claude.com/docs/en/manage-claude/api-and-data-retention ·
https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock · https://developers.openai.com/codex/skills · https://docs.railway.com/sandboxes ·
https://docs.railway.com/reference/pricing/plans

## 2. Agent frameworks, code sandboxes and durable execution

### 2.1 Agent frameworks

| Option | New core dependencies | Non-OpenAI models | Durable state | Tracing | Risk |
|---|---|---|---|---|---|
| Plain loop (repo) | 0 | native: five providers | own tables | own trace and `llm_calls` | estimation-specific, needs extraction |
| OpenAI Agents SDK 0.24.0 | 12 (openai, pydantic, mcp, websockets, starlette, …) | through LiteLLM, "best-effort, beta" | extras (Temporal, Dapr, Redis) | uploads to OpenAI by default; sensitive data on by default | active |
| Pydantic AI (slim) | 8, OTel API in core | first-class extras | eight engines incl. DBOS, Absurd, pgtask | OTel-native | Python 3.11+ |
| LangGraph 1.2.17 | the LangChain core | provider packages | Postgres checkpoints | LangSmith-first | LangChain gravity |
| smolagents | 6 | LiteLLM, OpenAI | none | extra | its local executor "can never be completely secure" |
| Google ADK | 24 (FastAPI, uvicorn, OTel SDK, …) | through LiteLLM | session services | OTel SDK | Gemini-centric |

Supply-chain note: malicious LiteLLM 1.82.7 and 1.82.8 reached PyPI on 2026-03-24 and harvested credentials on Python
start [V advisory, L details]; three of these frameworks reach other providers through LiteLLM, the surface rule 7
avoids.

**Recommendation (as reported).** Keep the plain loop and extract a small generic one: a JSON-schema tool registry,
a message list, a budget, a step journal, the existing client. Step up to Pydantic AI slim only with three or more
agent kinds with handoffs, streaming chat and an MCP client, visibly rising loop upkeep, and an approved dependency.

### 2.2 Code-execution sandboxes

**What is possible on Railway.**
- Service containers are unprivileged; Docker-in-Docker fails. bubblewrap and nsjail need user namespaces, which
  service containers do not offer (§7.2). [V for unprivileged; L for unshare]
- Primitives that need no privilege: resource.setrlimit, process groups, environment scrub, python -I, a per-run
  directory, a wall-clock kill; seccomp-bpf after no_new_privs can deny socket() (needs ctypes or libseccomp);
  Landlock limits files, TCP connect (ABI 4) and UDP (ABI 10). Whether Railway's kernel enables Landlock is [U].
- Python's preexec_fn "is NOT SAFE to use in the presence of threads": set limits in a small launcher instead. [V]
- RestrictedPython calls itself "not a sandbox system". [V]

| Option | Boundary | Egress control | Libraries | ≈ cost per minute (1 vCPU, 1 GB) | New vendor | Fit |
|---|---|---|---|---|---|---|
| A. Subprocess in the worker | process | only if seccomp or Landlock work [U] | the image | ~0 | no | fails "no production credentials" |
| B. Secretless code-runner service | container; no credentials co-resident | platform egress open [L]; seccomp or Landlock if allowed [U] | own image (openpyxl, reportlab, ezdxf) | container rates | no | matches the 7d ruling's spirit; tenants share a kernel |
| C. Railway Sandboxes | VM per run | outbound internet through NAT; no egress-off | apt templates, checkpoints | ≈ $0.0012–0.0023 | no | TypeScript SDK; account or workspace token; beta per staff |
| D. E2B | microVM [L] | allow and deny lists [V] | Python SDK | ≈ $0.0011 | yes | excluded by Phase 7d |
| E. Modal Sandboxes | gVisor or VM | block or allowlist [V] | Python-native | ≈ $0.0016 (derived from the per-core-second rate, 1 core = 2 vCPU) | yes | best third-party ergonomics |
| F–I. Daytona, Vercel, Cloudflare, Fly | VM or microVM | varies | varies | ≈ $0.001–0.002 | yes | no advantage here |
| J. OpenAI code interpreter | hosted | not stated | not stated | $0.03 per 20-min session | model-bound | ruled out |
| K. Anthropic code execution | hosted, "no outbound network" | best | rich (openpyxl, reportlab) | 1,550 free hours, then $0.05/h | model-bound | Claude-only, not ZDR-eligible |
| L. Gemini code execution | hosted, 30 s | not stated | 37 fixed libraries | tokens only | model-bound | "can't return other artifacts like media files" |

**Recommendation (as reported).** Option B: a separate code-runner service with no Supabase, R2 or LLM credentials,
reachable only over the private network; per run a launcher with rlimits, a timeout, a fresh directory, python -I, a
scrubbed environment, a non-root uid, and seccomp or Landlock denial if the probe passes. Step up to Railway Sandboxes
(same vendor) when code agents read untrusted content, tenants need kernel separation, or runs need persistent
workspaces. Step up to Modal or E2B only if platform-enforced egress rules become mandatory.

### 2.3 Durable execution and queues

"Pooler fit" means fit with Supabase's transaction pooler, on which LISTEN/NOTIFY and session advisory locks break. [V]

| Option | Dependencies | Pooler fit | Resume | Cancel | Per-account concurrency | Ops burden |
|---|---|---|---|---|---|---|
| Own SKIP LOCKED tables (repo pattern) | 0 | proven | build: a step journal | build: a flag | build: in the claim | low |
| Supabase Queues (pgmq) | 0 | OK | no | delete the message | no | adds an extension, little over a table |
| procrastinate | 7 | needs LISTEN off | no | yes | locks only | low |
| pgqueuer | 6 | LISTEN core, polling fallback | no | [U] | per entrypoint | low |
| DBOS Transact | 6 incl. sqlalchemy | direct or session pooler | automatic, from step checkpoints | at the next step | partitioned queues | low–medium |
| Temporal | temporalio + cluster | separate | deterministic replay | yes | app logic | high self-hosted; Cloud $25–50 per M actions |
| Inngest, Hatchet | SDK + engine | HTTP push / own engine | yes | yes | keyed (fair in Hatchet) | medium |
| pg_cron | 0 | in-DB | n/a | n/a | n/a | triggers only: at most 8 jobs, 10 min each |
| Railway cron | 0 | n/a | n/a | n/a | n/a | 5-minute minimum; skips if still running |

Queue costs are noise beside model spend; the real constraint is Postgres connections, so a run must not hold one
while it waits on a model (300 concurrent runs stepping every 5 s write about 60 small rows a second [L]).

**Recommendation (as reported).** Generalise the `estimation_runs` lane into a run table and an append-only step
table (leases with heartbeat, a fair claim that skips accounts at their cap, cancel between steps, retry with backoff,
resume from the journal). Schedules from pg_cron or the worker clock. DBOS only when multi-day waits or child workflows
appear. No LISTEN/NOTIFY library over the transaction pooler.

**Sources.** https://openai.github.io/openai-agents-python/models/ · https://raw.githubusercontent.com/openai/openai-agents-python/main/pyproject.toml ·
https://pydantic.dev/docs/ai/integrations/durable_execution/overview/ · https://raw.githubusercontent.com/pydantic/pydantic-ai/main/pydantic_ai_slim/pyproject.toml ·
https://docs.langchain.com/oss/python/langgraph/durable-execution · https://huggingface.co/docs/smolagents/tutorials/secure_code_execution ·
https://adk.dev/agents/models/litellm/ · https://www.netspi.com/blog/executive-blog/ai-ml-pentesting/litellm-supply-chain-compromise/ ·
https://docs.railway.com/guides/github-actions-runners#known-limitations · https://station.railway.com/questions/codex-sandbox-fails-bubblewrap-cannot-c-71a2988d ·
https://railway.com/changelog/2026-09-18-railway-sandboxes · https://docs.railway.com/sandboxes · https://docs.kernel.org/userspace-api/landlock.html ·
https://docs.docker.com/engine/security/seccomp/ · https://github.com/anthropic-experimental/sandbox-runtime · https://docs.python.org/3.12/library/subprocess.html ·
https://restrictedpython.readthedocs.io/en/latest/ · https://pypi.org/pypi/ezdxf/json · https://modal.com/pricing · https://modal.com/docs/guide/sandbox-networking ·
https://e2b.dev/pricing · https://www.daytona.io/pricing · https://vercel.com/docs/vercel-sandbox/pricing · https://developers.cloudflare.com/sandbox/ ·
https://docs.fly.io/about/pricing · https://developers.openai.com/api/docs/guides/tools-code-interpreter ·
https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool · https://ai.google.dev/gemini-api/docs/code-execution ·
https://procrastinate.readthedocs.io/en/stable/howto/production/connections.html · https://github.com/janbjorge/pgqueuer ·
https://docs.dbos.dev/python/tutorials/queue-tutorial · https://www.dbos.dev/pricing ·
https://raw.githubusercontent.com/earendil-works/absurd/refs/heads/main/README.md · https://temporal.io/pricing · https://www.inngest.com/docs/guides/concurrency ·
https://docs.hatchet.run/home/concurrency · https://supabase.com/docs/guides/queues · https://supabase.com/docs/guides/cron ·
https://supabase.com/docs/guides/self-hosting/accessing-postgres.md

## 3. External MCP interface for ChatGPT and Claude

### 3.1 Specification (revision 2026-07-28, current)

- The core is stateless (no handshake, no session id). Deprecated, removal no earlier than 2027-07-28: Roots,
  Sampling, Logging and Dynamic Client Registration (DCR, a client registering itself). Tools may ask for input
  mid-call; a tasks extension lets a server hand back a task to poll. [V]
- Streamable HTTP: one POST per request, answered with JSON or a request-scoped SSE stream; protocol-version, method
  and name headers required; Origin validated. [V]
- Authorization: the server is an OAuth resource server that serves protected resource metadata (PRM) and "MUST reject
  tokens that do not include them in the audience claim or otherwise verify that they are the intended recipient"; no
  token passthrough; registration by pre-registration, then Client ID Metadata Documents (CIMD), then DCR; clients
  validate the issuer. [V]
- Tools: servers MUST validate inputs, enforce access control, "rate limit tool invocations" and sanitise outputs;
  annotations are untrusted hints (unannotated = destructive, open-world write); stateful work uses handles authorised
  on every call. [V]

### 3.2 Clients (October 2026)

- **ChatGPT.** Custom servers are added under "plugins"; prefers CIMD, else DCR once per connection; no
  machine-to-machine grants; "Write actions by default require confirmation"; renders MCP Apps. Plan access [L/U];
  tool timeout [U]. [V]
- **Claude** (web, Desktop, mobile, Claude Code). Free plans get one custom connector. A 401 starts sign-in; only the
  first listed authorization server is used; without advertised CIMD support, DCR registers "a new client on every
  fresh connection"; the PRM resource must equal the URL the user typed. **Hosted apps allow 240 s per tool call and
  about 150k characters per result.** Claude Code supports tasks (CLI 2.1.304+). The claude.ai proxy does not refresh
  custom-connector tokens: answer expired tokens with 401 [L]. [V]

### 3.3 Authorization server options

- **Supabase Auth OAuth 2.1 server.** Beta, free beyond monthly active users, and **disabled on our project**
  (metadata answers "feature_disabled"). Has authorization code with PKCE, refresh rotation, offline access (fixed
  2026-07-30) and optional DCR; tokens are Supabase JWTs plus a client_id claim; the consent page is our own route.
  Gaps: no CIMD (#2850); the resource parameter is ignored and the audience stays "authenticated" (#2610, [L]); consent
  errors with resource, offline access or public clients (#2820, [L]); refresh errors not in RFC form (#2829, [L]);
  **OAuth tokens can call the user endpoint and change the password on accounts without MFA (#2877, [L])**; DCR rate
  limit default 10. The token hook sees client_id but not the resource. Whether a Supabase-issued MCP token can reach
  PostgREST directly with the user's full rights is **[U]: test it.** [V unless marked]
- **WorkOS AuthKit Standalone Connect.** Keeps our login; CIMD and DCR; the audience equals the requested resource.
  Free to 1M monthly active users; custom domain $99/month. New data processor; EU residency [U]. [V]
- **Auth0, Stytch, Clerk.** Viable; free tiers; add-ons. [V/L]
- **A thin authorization server in FastAPI.** The MCP SDK ships the routes, but no CIMD or issuer support; we would
  own PKCE, codes, refresh and CIMD SSRF guards. [V]

| Criterion (1–5) | A. Supabase OAuth | B. Own thin server | C. WorkOS | D. Auth0 / Stytch / Clerk |
|---|---|---|---|---|
| No new vendor | 5 | 5 | 2 | 2 |
| Spec fit (CIMD, audience, issuer) | 2 | 4 | 5 | 4 |
| Client compatibility risk | 2 | 3 | 4 | 3–4 |
| Security code we own | 3 (#2877) | 1 | 4 | 4 |
| Effort to first client | 5 | 2 | 3 | 2–3 |
| Fit with Supabase JWT and RLS | 5 | 4 | 4 | 3 |

### 3.4 Python SDK mount pattern (mcp 2.3.0, 2026-10-02)

- Dependencies include httpx2, jsonschema, the OTel API, sse-starlette, pyjwt and starlette; the server class is
  MCPServer (the old FastMCP name raises). [V]
- Mount the HTTP app at "/" as the **last** route (its discovery routes live under the well-known path), enter its
  session manager in the host's lifespan, and pass explicit allowed hosts and origins (the default allows only
  localhost). Turn the SDK's resource validation off and check the audience in our own verifier (on by default from
  3.0). One endpoint serves both protocol eras; SSE mode pings slow handlers. [V]

### 3.5 Patterns

- **Long work:** start returns a run id, status and poll hint; get returns progress; output returns links; each call
  stays well under 240 s; a run id is a handle re-authorised against the caller's account on every call.
- **Rate limits and metering** (a spec MUST): a per-account token bucket keyed on the verified subject, a cost class
  per tool, an error result with the retry time on exhaustion (HTTP 429 only for transport abuse), and one ledger row
  per call (account, client, tool, argument hash, duration, outcome, cost).
- **Files:** a summary, structured content and a resource link to an access-checked read or a short-lived URL.
- **Hygiene and security:** reads carry the read-only hint (it skips ChatGPT's confirmation); a send is destructive
  and open-world, and our server-side approval stays the real gate; a whoami tool; check the audience on every token;
  never forward the MCP token to Supabase, R2 or providers; treat ad text as untrusted.

### 3.6 Recommendation (as reported, with the domain now known)

One stateless endpoint mounted in the FastAPI app, mcp>=2.3,<3, at **https://api.propscout.cz/mcp** (the domain is
owned and its cutover approved; the API record is DNS-only, F10). Make the authorization server a seam: one verifier
checks issuer, audience (or the client_id rule) and expiry and maps to the Supabase user; harden REST `verify_jwt` in
the same change. Spike Supabase OAuth on a branch project with pass/fail gates; any failure → WorkOS, not a
hand-rolled server. 10–15 tools from one registry, read and write separated, workflows through start, get and output
with a server-stamped source; rate limits and the call ledger before launch; defer MCP Apps, tasks and events.

**Sources.** https://modelcontextprotocol.io/specification/versioning · https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization ·
https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration ·
https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations ·
https://modelcontextprotocol.io/specification/2026-07-28/server/tools · https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http ·
https://modelcontextprotocol.io/extensions/tasks/overview · https://modelcontextprotocol.io/extensions/apps/overview ·
https://developers.openai.com/apps-sdk/build/auth · https://developers.openai.com/api/docs/guides/custom-mcp-server ·
https://claude.com/docs/connectors/building/authentication · https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp ·
https://code.claude.com/docs/en/mcp · https://github.com/anthropics/claude-ai-mcp/issues/228 · https://supabase.com/docs/guides/auth/oauth-server ·
https://github.com/supabase/auth/issues/2850 · https://github.com/supabase/auth/issues/2610 · https://github.com/supabase/auth/issues/2820 ·
https://github.com/supabase/auth/issues/2829 · https://github.com/supabase/auth/issues/2877 · https://github.com/supabase/auth/pull/2630 ·
https://pypi.org/project/mcp/2.3.0/ · https://py.sdk.modelcontextprotocol.io/v2/migration/ · https://workos.com/docs/authkit/mcp · https://workos.com/pricing ·
https://auth0.com/ai/docs/mcp/guides/resource-param-compatibility-profile · https://stytch.com/blog/stytch-supports-cimd/ · https://clerk.com/pricing ·
https://docs.railway.com/networking/public-networking/specs-and-limits

## 4. Mailboxes for a broker-correspondence agent

### 4.1 Gmail scopes and verification [V]

| Scope | Class | Needed for |
|---|---|---|
| gmail.send | sensitive | sending only |
| gmail.compose | restricted | drafts and sending drafts |
| gmail.readonly, gmail.metadata | restricted | reading replies |
| gmail.modify, full mail | restricted | read, compose, send |
| gmail.settings.basic | restricted | send-as aliases |

- Sensitive scopes take about **10 business days**; restricted about 6 weeks plus a security assessment (CASA),
  required "if you store restricted scope data on servers (or transmit)", re-tested every year. Google charges
  nothing; labs charge about **$500–1,800 a year** for the lower assurance level [L].
- Requirements: **a homepage on a domain we own** (a login page does not count), a privacy policy on that domain,
  Search Console ownership, an English demo video, a written justification per scope.
- Limited Use: data serves user-facing features only; no humans reading without consent; no training of generalised
  models; a published disclosure.
- Unverified apps: a lifetime cap of 100 new users and a warning screen. Testing status: 100 test users, refresh
  tokens expire after 7 days. Internal (Workspace) apps need no verification.

### 4.2 Sync, threads, quotas [V]

Push via Cloud Pub/Sub with only an address and a history id per event, watch renewed at least weekly, events
"occasionally delayed or dropped" (poll history as a fallback; an expired id means a full sync). Threads need the
thread id, References and In-Reply-To headers and a matching subject. Quotas: 6,000 units per user per minute, a send
costs 100. Sending limits: about 500 a day for consumer accounts, 2,000 for Workspace.

### 4.3 Duplicate-send prevention

No idempotency key is documented for Gmail's send [U]; Resend offers a 24 h key [V/L]. The platform must own the
guarantee: an outbox row as the unit of intent (draft → approved → sending → sent | failed | unknown), claimed with a
compare-and-set; the approval bound to a hash of recipients, subject, body and attachments, voided by any edit; a
platform-minted Message-ID stored before the send; a timeout means "unknown", never an automatic retry; per-broker caps
and the suppression check at claim time.

### 4.4 Alternatives

- **The user's assistant and its own Gmail connector.** Claude can read, draft and send since 2026-08-18, asking
  before each send by default [V]; ChatGPT sends through Gmail and Outlook since 2026-06-08 [L]. The platform
  controls only the context its MCP tools serve: **a channel, never the system of record.**
- **Aggregators.** Unipile (its own verified app, CASA included; €49/month for 10 accounts, then €3–5 each; region
  [U]) and Nylas (Shared App on annual Pro plus add-ons; EU means London) remove our assessment but become
  sub-processors of mailbox content. [V]
- **IMAP with app passwords.** Google ended password IMAP on 2025-03-14; app passwords are "not recommended" and
  password-equivalents. Avoid; Seznam's IMAP is a possible late opt-in [L]. [V]
- **Platform mailbox.** SES ($0.10 per 1k out; inbound $0.10 per 1k plus $0.09 per 1k chunks; EU regions), Mailgun
  (EU region, from $15/month), Resend (US account data, idempotency keys), Postmark (US only). Shape: From "Name (via
  Platform)" on a reply subdomain with aligned SPF, DKIM and DMARC, a per-thread Reply-To token, a copy to the user.
  Cost: a foreign sender identity and one reputation shared by every tenant. [V; sender rules L]
- **Send-as alias.** "Starting January 2027, Gmail will no longer support the 'Send as' feature for third-party email
  addresses." [V]
- **Gmail add-on** (reads the open message, inserts a draft, sensitive scopes; consumer install [U]) and **Microsoft
  Graph** (Mail.Send returns 202 with no id; multitenant apps need publisher verification). [V]

### 4.5 GDPR and Czech law

- Brokers' named e-mails and phones are personal data. Legitimate interest fits a per-property inquiry after the
  three-part test [L]. **Art. 14(3)(b):** for data not collected from the person, the notice is due "at the latest at
  the time of the first communication" [V].
- **Act 480/2004 Sb.** (ÚOOÚ FAQ, [V]): commercial communications need prior opt-in and the law "does not distinguish
  the type of recipient"; fines up to CZK 10M; a targeted reply to a specific inquiry is not a commercial
  communication. An investor asking about a broker's own ad is, in the researcher's reading, not one either [L]; **a
  campaign pitching services to brokers is.** Keep the agent property-anchored and user-initiated.
- Roles: with the user's own mailbox, the user is controller and the platform a processor; with a platform mailbox and
  a scraped directory, the platform is controller or joint controller [L, counsel needed]. Keep suppression rows
  indefinitely (a "robinson" list) [V].

### 4.6 Decision matrix (1 worst, 5 best)

| Option | Control | Audit | Verification burden | Cost | Time | Sender trust | Deliverability |
|---|---|---|---|---|---|---|---|
| Platform mailbox (SES or Mailgun EU) | 5 | 5 | 5 | 5 | 4 | 2 | 3 |
| gmail.send + platform Reply-To | 4 | 4 | 3 | 5 | 3 | 5 | 5 |
| Gmail restricted, own CASA | 5 | 5 | 1 | 3 | 1 | 5 | 5 |
| Unipile or Nylas shared app | 4 | 4 | 4 | 2 | 4 | 4 | 5 |
| User's assistant connector | 1 | 1 | 5 | 5 | 5 | 5 | 5 |
| IMAP + app password | 4 | 4 | 4 | 4 | 3 | 5 | 5 |
| Send-as alias | 3 | 3 | 1 | 4 | 1 | 3 | ends 2027-01 |
| Graph Mail.Send | 4 | 4 | 3 | 5 | 3 | 5 | 5 |

### 4.7 Recommendation (as reported)

The platform owns the ledger, the approval gate and the outbox whatever carries the mail. Stage 0: a tenant-scoped
thread and message ledger anchored on the property and the canonical broker, the CAS outbox, hashed approvals, minted
Message-IDs, no agent tool that sends, `mailto:` as transport. Stage 1 (ship first): a platform mailbox on a reply
subdomain through SES or Mailgun EU. Stage 2: gmail.send only, keeping the platform Reply-To. Stage 3: restricted
scopes or Unipile on paying demand. Never IMAP passwords, never send-as. (The program departs: § 9.)

**Sources.** https://developers.google.com/workspace/gmail/api/auth/scopes · https://support.google.com/cloud/answer/13464325 ·
https://support.google.com/cloud/answer/13464321 · https://support.google.com/cloud/answer/13463817 · https://support.google.com/cloud/answer/13465431 ·
https://developers.google.com/terms/api-services-user-data-policy · https://developers.google.com/workspace/workspace-api-user-data-developer-policy ·
https://developers.google.com/identity/protocols/oauth2 · https://developers.google.com/workspace/gmail/api/guides/push ·
https://developers.google.com/workspace/gmail/api/guides/sync · https://developers.google.com/workspace/gmail/api/guides/threads ·
https://developers.google.com/workspace/gmail/api/reference/quota · https://support.google.com/mail/answer/22370 · https://support.google.com/mail/answer/17101213 ·
https://knowledge.workspace.google.com/admin/sync/transition-from-less-secure-apps-to-oauth ·
https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0 ·
https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview ·
https://support.claude.com/en/articles/10166901-use-google-workspace-connectors · https://help.openai.com/en/articles/20001495-managing-app-permissions-in-chatgpt ·
https://www.nylas.com/pricing/ · https://www.unipile.com/pricing-api/ · https://resend.com/pricing · https://postmarkapp.com/pricing ·
https://aws.amazon.com/ses/pricing/ · https://www.mailgun.com/pricing/ · https://gdpr-info.eu/art-14-gdpr/ ·
https://uoou.gov.cz/cinnost/obchodni-sdeleni/casto-kladene-otazky-k-zakonu-c-4802004-sb ·
https://www.switchlabs.dev/post/casa-tier-2-tier-3-security-review-providers-pricing-and-the-cheapest-option

## 5. Agent evaluation and LLM observability

### 5.1 The baseline a vendor must beat

`llm_calls` already gives per-call cost and latency per lane and per run; `estimation_runs` results are immutable
with `snapshot_id`-pinned comparables (F25; rule 12); the client is provider-neutral; labelling, judge and golden
patterns exist
(F69). Because comparables are pinned by snapshot, **an eval case here can replay against frozen database state**; no
vendor dataset can do that.

### 5.2 Market changes since mid-2026 [V unless marked]

The OpenAI Evals platform goes read-only 2026-10-31 and shuts down 2026-11-30. OpenAI bought promptfoo (still MIT).
ClickHouse bought Langfuse; Langfuse v4 (2026-08-17) needs ClickHouse 25.12+, and Cloud is v4-only from 2026-11-16.
LangSmith repriced on 2026-07-21 (about $5 per 1k base traces overage [L]). Helicone's ownership is in flux. The OTel
GenAI conventions, agent spans included, are still "Development"; OpenInference is more settled.

### 5.3 Options

| Option | Licence, hosting | EU | Price (seen today) | Strength |
|---|---|---|---|---|
| Langfuse Cloud | MIT core | eu-west-1 (Ireland) | Hobby free 50k units; Core $29 (100k units, 90 days); Pro $199; $8 per 100k extra | datasets from traces, managed judges, annotation queues, a PR action |
| Langfuse self-host | MIT | ours | six containers incl. ClickHouse; ≈ $60–90/month on Railway [L] | same features |
| Braintrust | proprietary | EU endpoint | Starter $0 (14-day retention); Pro $249 | best experiment comparison |
| LangSmith | proprietary | US or EU | Plus $39 per seat + pay as you go | pytest plugin, annotation queues |
| Arize Phoenix | ELv2, self-host | ours | ≈ $10–20/month on Railway [L] | one container plus Postgres |
| Opik | Apache-2.0 | [U] | Pro $19 for 100k spans | OTel, pytest |
| W&B Weave | proprietary | n/a | $0.10 per MB beyond 1.5 GB | costly at agent payload sizes |
| promptfoo | MIT (OpenAI-owned) | n/a | free red-teaming to 10k probes | trajectory and tool-call assertions; needs Node |
| DeepEval | Apache-2.0 | n/a | free | agentic metrics, conversation simulator; heavy |
| Inspect AI (UK AISI) | MIT | n/a | free | sandboxed code-agent and security evals |

### 5.4 Cost at volume (estimates)

At 20k agent traces a month (about 22 spans and 2 scores each, about 150 KB of context): Langfuse Core ≈ $61,
Braintrust Starter ≈ $8 plus scores, LangSmith Plus ≈ $89, Phoenix self-host ≈ $15–25, Weave ≈ $210, a custom harness
$0 plus judge tokens. At 100k: Langfuse Core ≈ $206, Weave ≈ $1,400. Online judging costs $0.01–0.10 per assessment
[V], so sample 5–10 % rather than score every trace.

### 5.5 Decision matrix (1 poor – 5 strong, for a solo operator, provider-neutral loop, EU data)

| Criterion (weight) | Custom | Langfuse Cloud EU | Langfuse self-host | Braintrust | LangSmith | Phoenix | Inspect AI |
|---|---|---|---|---|---|---|---|
| Provider neutrality (3) | 5 | 5 | 5 | 4 | 4 | 5 | 5 |
| Replay against frozen DB state (2) | 5 | 2 | 2 | 2 | 2 | 2 | 3 |
| Human review queue (2) | 2 | 4 | 4 | 4 | 5 | 3 | 2 |
| CI gate (2) | 5 | 4 | 4 | 4 | 4 | 3 | 3 |
| Trace → dataset flow (2) | 3 | 5 | 5 | 5 | 5 | 4 | 1 |
| Production monitoring (2) | 3 | 5 | 5 | 4 | 5 | 4 | 1 |
| Ops burden, solo operator (3) | 4 | 5 | 2 | 5 | 5 | 4 | 4 |
| EU residency (2) | 5 | 5 | 5 | 4 | 4 | 5 | 5 |
| Lock-in, vendor stability (2) | 5 | 4 | 5 | 2 | 2 | 4 | 5 |

### 5.6 Design guidance the harness must encode [V unless marked]

1. Read about 100 traces before writing metrics; build a counted failure taxonomy.
2. Deterministic checks first, then model judges, then humans. Grade outcomes; pin paths only for safety invariants
   (no send without an approval, tenant tools called with the caller's account, no tool outside the allowlist).
3. Binary judges, one per failure mode, calibrated on 100–200 held-out human labels, with an "unknown" option.
4. Balanced and adversarial sets: injection in ad text or a broker e-mail, a cross-tenant id, send without approval,
   a sale vs rent per-m² mix-up, an unresolved location. Start with 20–50 tasks from real failures.
5. Standard errors and paired differences; pass^k where consistency matters; fix metric, threshold and k before the
   run [synthesis]. Production evals are reference-free, sampled and asynchronous.
6. A clean environment per trial. Documents need custom scorers: recalculate an .xlsx in headless LibreOffice and
   compare named cells; check Markdown and PDF with a checklist plus one binary judge per criterion.

### 5.7 Recommendation (as reported)

Build the core, buy the viewer later, keep the wire format neutral. Phase A: eval case, run and result tables beside
`estimation_runs`; a scorer registry; pytest entry points (a fast recorded suite in CI, a nightly live suite with
thresholds and pass^k); failure → case; review on the `/labeling` pattern; OpenTelemetry spans with OpenInference
attributes, exporter off unless configured. Phase B: Langfuse Cloud EU when the operator reads more than about 50 full
traces a week, a second reviewer joins, online judges are needed, or harness UI upkeep passes a day a month; Phoenix if
traces must stay on owned infrastructure. Phase C: Inspect AI for sandboxed and security suites, promptfoo for
red-teaming. Avoid OpenAI Evals, Helicone, Weave and LangSmith at this volume.

**Sources.** https://langfuse.com/pricing · https://langfuse.com/self-hosting · https://langfuse.com/changelog/2026-08-17-langfuse-v4 ·
https://langfuse.com/integrations/native/opentelemetry · https://langfuse.com/docs/evaluation/overview · https://langfuse.com/security/data-regions ·
https://github.com/langfuse/experiment-action · https://clickhouse.com/blog/clickhouse-acquires-langfuse-open-source-llm-observability ·
https://www.braintrust.dev/pricing · https://www.braintrust.dev/docs/guides/self-hosting · https://www.langchain.com/pricing ·
https://docs.langchain.com/langsmith/pytest · https://github.com/langchain-ai/agentevals · https://arize.com/docs/phoenix/self-hosting/architecture ·
https://github.com/Arize-ai/openinference · https://www.comet.com/site/pricing/ · https://coreweave.com/forge-pricing ·
https://www.promptfoo.dev/docs/configuration/expected-outputs/ · https://openai.com/blog/openai-to-acquire-promptfoo ·
https://developers.openai.com/api/docs/deprecations · https://github.com/confident-ai/deepeval · https://inspect.aisi.org.uk/ ·
https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md ·
https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents · https://www.anthropic.com/research/statistical-approach-to-model-evals ·
https://hamel.dev/blog/posts/evals-faq/ · https://railway.com/deploy/arize-phoenix-observability

## 6. Securing multi-tenant agents, and artifact storage

### 6.1 Where 2025–2026 guidance agrees

- **OWASP LLM Top 10 (2025)** and the **Top 10 for Agentic Applications (2026)**: least privilege, human approval for
  high-risk actions, "execute extensions in user's context", complete mediation, rate limits, bounded identity and
  short-lived credentials, per-user memory, immutable logs. [V; agentic names L]
- **Meta's Agents Rule of Two** (2025-10-31): in one session an agent may hold at most two of (A) untrustworthy input,
  (B) sensitive systems or private data, (C) state change or external communication; all three need a human in the
  loop or a fresh session (Willison's "lethal trifecta"). [V]
- **Defences by design.** CaMeL (privileged planner, tool-less quarantined parser, capability checks; 77 % of AgentDojo
  tasks with provable security vs 84 % undefended; "not fully solved"). Microsoft FIDES (labels where the most
  restrictive wins; sinks declare what they accept; an approval bound to one invocation and consumed once; no cover
  for code execution). Spotlighting cut attack success from over 50 % to under 2 %, probabilistically. [V]
- **Consequence here:** brokers write ad text and outsiders write e-mails and PDFs, so nearly every agent holds (A).

### 6.2 Approval models

Managed Agents: always allow, always ask, or auto (which "is not a human checkpoint"); each tool event records the
evaluated permission and a reason code. Its guidance: always ask wherever effects cannot be undone; a budget on every
session; no agent that reads untrusted input holds an always-allow tool that writes or sends. Claude Code hooks: a
pre-tool decision of allow, deny, ask or defer. **Lesson: a deterministic, logged pre-tool gate inside our own loop.**
[V]

### 6.3 Identity for a run

Supabase access tokens last 1 h and refresh tokens are single-use: reuse outside 10 s counts as theft and ends the
session, so forwarding a browser's refresh token into a worker would log the user out, and forwarding the access token
breaks long runs. No Supabase token exchange was found [U]. Importing a signing key would let us mint JWTs that RLS
trusts: a powerful, forgeable credential. Never give an agent a model-provider key. **Fit:** the API stamps the run's
principal from the verified JWT at submit; for each tool transaction the trusted worker rebuilds minimal claims through
`tenant_transaction`; no token is stored, forwarded or refreshed; membership is evaluated live, so a revocation lands
at the next call. [V]

### 6.4 Sandboxing rules for any host [V]

No credentials in the sandbox; inputs copied in and declared outputs pulled back; a temporary directory per run,
destroyed at the end; memory, time and output caps; network denied by default; every credentialed operation (database,
Valuo, mail) is a worker-executed tool, never an HTTP call from sandboxed code. Railway Sandboxes have "no supported
egress firewall, allowlist, or deny-all option"; an in-guest network cut by root is untested [U].

### 6.5 Ledgers and caps

Keep events in our own store, log every blocked or gated call, keep logs immutable. Add per-account concurrency at
claim time and a monthly budget summed from `llm_calls`. A separate provider project with a hard limit is a backstop,
but "enforcement is not instantaneous". [V]

### 6.6 Artifact storage: R2 vs Supabase Storage

| Criterion | Supabase Storage | Cloudflare R2 (current store) |
|---|---|---|
| Authorization | RLS on the objects table; the service key bypasses it | bucket-scoped tokens; prefix-scoped temporary credentials; presigned URLs up to 7 days ("treat as bearer tokens") |
| Fit | a second store | already integrated: RLS'd row plus bytes proxied by the API (F52) |
| Storage price | 100 GB on Pro, then $0.0213/GB-month | $0.015/GB-month, 10 GB free |
| Egress | 250 GB included, then $0.09/GB | free |
| EU residency | the project's region (stated only in an old post) [L] | guaranteed only by an EU-jurisdiction bucket |

**Verdict (as reported):** stay on R2, in a new EU-jurisdiction bucket; Supabase's object RLS would be bypassed anyway
because the API proxies bytes with a service credential.

### 6.7 Threat model

| Threat | Control |
|---|---|
| Injection through ad text, e-mail or PDF | untrusted fields marked in envelopes; a run-level taint; quarantined, schema-constrained extraction |
| Injected text triggers a consequential action | an effect class per tool; sinks refuse under taint unless approved; Rule of Two per agent |
| Cross-tenant read or write; stale authority | tenant tools in `tenant_transaction` with run-principal claims rebuilt per transaction; the model never supplies the account |
| Token theft or replay | no user token stored, forwarded or placed in a sandbox; MCP audience checked |
| Agent code reads secrets or exfiltrates | sandbox with an empty environment; network denied; outputs only from a declared folder |
| Duplicate or unauthorised send or paid call | always ask; approval bound to the input hash, used once; an idempotency key per send |
| Approval fatigue | ask only for external or irreversible actions; show the diff and the reason |
| Runaway cost; repudiation | run, account and provider caps; append-only run, event and approval ledgers |
| Skill poisoning; artifact leak | agent-proposed skill edits become pull requests; RLS'd artifact rows, bytes through the API only |

### 6.8 Recommendation (as reported)

A run principal, not token forwarding. Effect classes on the tool manifest (read market, read tenant, write tenant,
external, costly or irreversible) mapped per agent to allow, ask or deny, the two consequential classes defaulting to
ask, with a logged pre-tool gate. Rule of Two declared per agent and checked in CI. FIDES-lite labels and a run-level
taint in the loop. Never run agent-written code in the worker; prove code isolation on a Railway Sandbox (ISOLATED,
empty environment, in-guest network cut, unprivileged user), else use the provider's container for analysis-only
steps. An RLS'd artifact table in a new EU R2 bucket; only declared outputs are promoted.

**Sources.** https://genai.owasp.org/llmrisk/llm01-prompt-injection/ · https://genai.owasp.org/llmrisk/llm062025-excessive-agency/ ·
https://genai.owasp.org/llmrisk/llm102025-unbounded-consumption/ · https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/ ·
https://ai.meta.com/blog/practical-ai-agent-security/ · https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/ · https://arxiv.org/abs/2503.18813 ·
https://arxiv.org/abs/2403.14720 · https://learn.microsoft.com/en-us/agent-framework/agents/security ·
https://platform.claude.com/docs/en/managed-agents/permission-policies · https://platform.claude.com/docs/en/managed-agents/secure-deployment ·
https://code.claude.com/docs/en/hooks · https://www.rfc-editor.org/rfc/rfc8693.html · https://supabase.com/docs/guides/auth/signing-keys ·
https://supabase.com/docs/guides/auth/sessions · https://supabase.com/docs/guides/auth/oauth-server ·
https://station.railway.com/questions/does-railway-support-unprivileged-bubble-dfd8911e ·
https://station.railway.com/questions/railway-sandbox-security-review-for-untr-62c5ced5 · https://gvisor.dev/docs/user_guide/rootless/ ·
https://developers.openai.com/api/docs/guides/spend-limits.md · https://supabase.com/docs/guides/storage/security/access-control ·
https://developers.cloudflare.com/r2/reference/data-location/ · https://developers.cloudflare.com/r2/api/s3/presigned-urls/ ·
https://developers.cloudflare.com/r2/api/tokens/ · https://developers.cloudflare.com/r2/api/s3/temporary-credentials/

## 7. Railway facts

### 7.1 Sandboxes

- **Status: the sources disagree.** The changelog of 2026-09-17 says "generally available"; the docs carry no beta
  banner. Against that, the TypeScript SDK README says "The SDK is in beta and there will be breaking changes", and a
  Railway employee wrote on 2026-09-29: "Sandboxes are currently in beta … We don't recommend building a production
  system on them, and we have no timeline for them leaving beta. Trial, Hobby and Pro plans don't include an SLA." [V]
- Region defaults to us-west2; Amsterdam is selectable through the SDK or API, untested [L]. Two network modes only:
  ISOLATED (outbound internet only) and PRIVATE; "Every sandbox has outbound internet access through a NAT gateway";
  "There is no supported egress firewall, allowlist, or deny-all option." [V]
- Tokens: account, workspace and project tokens all work; **no sandbox-only scoped credential exists**. [V]
- Price: $50 per GB-month and $50 per vCPU-month of CPU actually used, per second; egress $0.05/GB. Pro: default
  8 vCPU / 8 GB, max 32 / 32, 100 running per environment, idle timeout 30 min by default. [V]
- SDKs: TypeScript only (Node 22+). The public GraphQL API exposes create, exec, destroy, heartbeat and checkpoint, so
  Python can drive it over HTTP; streaming exec and files need WebSocket, and HTTP exec "is cut at 16,000 bytes … 2
  minutes by default, 10 at most". [V]

### 7.2 Service containers

No privileged mode, no Docker-in-Docker ("Railway containers are non-privileged") [V]. Staff: KVM and NET_ADMIN "would
not be possible on Railway" [L]. A Railway bot (2026-09-16): service containers "do not support the unshare calls
bubblewrap needs … and there is no service setting to enable them" [L]. seccomp, gVisor, Landlock, nsjail: no official
statement [U]. Sandboxes are VMs, Docker preinstalled; a bubblewrap bug there was reported fixed 2026-10-13 [V].

### 7.3 Config as Code: the 2026-12-01 cutoff

"Config as Code is deprecated. … Existing railway.json / railway.toml files continue to work for services that
already use them until 2026-12-01 (hard cutoff). New services cannot opt into Config as Code." The replacement is
Infrastructure as Code: a TypeScript file under .railway (GA; Python and Go beta), evaluated by the Railway CLI with
plan and apply, not read on every deploy. Migration commands since 2026-10-16: migrate, apply, cutover, status, undo.
"A service cannot be managed by both systems at the same time." Every key in the repo's `railway.json` and
`frontend/railway.json` (builder, Dockerfile path, watch patterns, healthcheck, restart policy) is a typed IaC field
[L]. [V]

### 7.4 Edge and request timeouts [V]

"Idle HTTP/1.1 connections are closed after 60 seconds between requests." "HTTP requests can run for up to 15 minutes
if data keeps transferring … and are otherwise closed after 5 minutes with no data transferred." Request bodies must
finish uploading within 5 minutes. WebSockets are exempt. SSE: heartbeat at least every 5 minutes, reconnect at
15 minutes. Older "flat 5-minute timeout" posts are superseded.

### 7.5 Private networking and shared variables [V]

"Services in different projects cannot communicate over the private network"; each environment has its own network.
Shared variables are scoped to one project; no workspace-level scope exists. Cross-project traffic needs public
networking or a TCP proxy.

### 7.6 Cron [V]

Any service can run on a UTC crontab; runs at least 5 minutes apart; if the previous run is still going, the new one
is skipped; timing varies by a few minutes; the process must exit when done.

### 7.7 Plan limits, prices, EU region, the incident [V]

Pro: up to 1,000 vCPU and 1 TB per service, up to 42 replicas at 24 vCPU / 24 GB each; $20/month including $20 of
usage; $20 per vCPU-month, $10 per GB-month, $0.05/GB egress, $0.15 per GB-month of volume; builds free; no
per-service fee; "99.99% Availability Target" but no SLA. One EU region: EU West Metal, Amsterdam. **Changelog
2026-10-23, "October 21 security incident": "we recommend that all customers revoke and replace sensitive credentials
stored on Railway before October 21."**

**Sources.** https://railway.com/changelog/2026-09-18-railway-sandboxes · https://docs.railway.com/sandboxes · https://docs.railway.com/sandboxes/quickstart ·
https://docs.railway.com/cli/sandbox · https://station.railway.com/questions/railway-sandbox-security-review-for-untr-62c5ced5 ·
https://github.com/railwayapp/railway-ts-sdk · https://pypi.org/project/railway-sdk/ · https://backboard.railway.com/graphql/v2 ·
https://docs.railway.com/deployments/regions · https://docs.railway.com/pricing/plans · https://railway.com/pricing ·
https://docs.railway.com/guides/github-actions-runners · https://station.railway.com/feedback/docker-in-docker-d07c4730 ·
https://station.railway.com/feedback/allow-services-to-be-run-in-privileged-m-8c66b22b ·
https://station.railway.com/questions/does-railway-support-unprivileged-bubble-dfd8911e ·
https://station.railway.com/questions/codex-sandbox-fails-bubblewrap-cannot-c-71a2988d ·
https://station.railway.com/questions/how-to-add-security-opt-seccomp-unconf-ca54cd6c · https://railway.com/changelog/2026-06-12-docker-in-sandboxes ·
https://docs.railway.com/config-as-code · https://docs.railway.com/infrastructure-as-code · https://railway.com/changelog/2026-10-16-activity-feed-filters ·
https://docs.railway.com/networking/public-networking/specs-and-limits · https://docs.railway.com/guides/sse-vs-websockets ·
https://docs.railway.com/networking/private-networking/how-it-works · https://docs.railway.com/variables · https://docs.railway.com/cron-jobs ·
https://docs.railway.com/deployments/scaling · https://railway.com/changelog/2026-10-23-october-21-security-incident

## 8. Prices (USD, read 2026-10-25) [V unless marked]

### 8.1 Models

| Model | Input / 1M | Cached input / 1M | Output / 1M | Notes |
|---|---|---|---|---|
| gpt-5 | 1.25 | 0.125 | 10.00 | Batch and Flex half price; regional processing +10 % for models from 2026-03-05 |
| gpt-5-mini | 0.25 | 0.025 | 2.00 | the older $0.125 / $1.00 quote is the Batch / Flex rate |
| gpt-5-nano | 0.05 | 0.005 | 0.40 | |
| Claude Opus 5.5 | 4 | 0.20 | 20 | Batch half price; no first-party EU option (inference and workspace geo "us"/"global" only) |
| Claude Sonnet 5.5 | 2 | 0.10 | 10 | EU through Google Cloud's "eu" endpoint at +10 %, without Managed Agents, Batches or code execution |
| Claude Haiku 5.5 (prompt ≤ 100k) | 0.10 | 0.01 | 0.50 | above 100k: 0.50 / 0.05 / 2.50 |
| gemini-3.8-flash | 0.75 (1.50 from 2027-01-01) | 0.075 | 3.75 (7.50) | |
| gemini-3.5-flash-lite | 0.30 | 0.03 | 2.50 | |
| gemini-2.5-pro (≤ 200k) | 1.25 | 0.125 | 10.00 | Gemini code execution returns text and inline images only |

Managed Agents runtime: $0.08 per session-hour while running; no batch mode.

### 8.2 Infrastructure and services

| Item | Price |
|---|---|
| Railway containers | $20 per vCPU-month, $10 per GB-month RAM, $0.05/GB egress, $0.15 per GB-month volume |
| Railway Sandboxes | $50 per GB-month, $50 per vCPU-month used, per second |
| Supabase branching (Pro) | $0.01344 per branch-hour; outside compute credits and the spend cap |
| Supabase Storage (Pro) | 100 GB included, then $0.0213/GB-month; egress 250 GB, then $0.09/GB |
| Supabase connections | Large 160 direct / 800 pooler clients; 2XL 380 / 1,500; default pool sizes no longer published [U] |
| Supabase Realtime, Edge Functions (Pro) | included: 500 connections, 5M messages; 2M invocations |
| Cloudflare R2 Standard | $0.015/GB-month; Class A $4.50/M, Class B $0.36/M; egress free; 10 GB free |
| R2 EU jurisdiction | chosen at creation, or through the EU S3 endpoint; **cannot be changed later**; up to 5 TiB per object |
| Langfuse Cloud | Hobby free (50k units); Core $29; Pro $199; overage $8 per 100k units; a unit is any trace, observation or score |
| Google Gmail verification | sensitive scopes free; restricted needs a yearly third-party assessment, about $500–1,800 a year [L] |

**Sources.** https://developers.openai.com/api/docs/pricing · https://platform.claude.com/docs/en/about-claude/pricing ·
https://platform.claude.com/docs/en/manage-claude/data-residency · https://platform.claude.com/docs/en/build-with-claude/claude-models-on-google-cloud/google-managed ·
https://ai.google.dev/gemini-api/docs/pricing · https://ai.google.dev/gemini-api/docs/code-execution · https://langfuse.com/pricing ·
https://langfuse.com/docs/data-security-privacy · https://supabase.com/pricing · https://supabase.com/docs/guides/platform/manage-your-usage/branching ·
https://supabase.com/docs/guides/platform/manage-your-usage/storage-size · https://supabase.com/docs/guides/platform/manage-your-usage/egress ·
https://supabase.com/docs/guides/platform/compute-and-disk · https://developers.cloudflare.com/r2/pricing/ ·
https://developers.cloudflare.com/r2/reference/data-location/ · https://developers.cloudflare.com/r2/platform/limits/ ·
https://developers.google.com/workspace/gmail/api/auth/scopes · https://support.google.com/cloud/answer/13465431 · https://support.google.com/cloud/answer/7454865 ·
https://tacsecurity.com/?p=18721 · https://deepstrike.io/blog/google-casa-security-assessment-2025

## 9. Where the program departs from this research

| Research recommendation | Program decision (PROGRAM.md) | Why | Reopen when |
|---|---|---|---|
| §1.5 path Y and §6.8: prove code isolation on a Railway Sandbox per run | Tier-1 renderers first; tier-2 code only in a secretless code-runner with **proven** network denial; a Sandbox VM in an empty project is the second rung | staff advise against production use; no egress control; no sandbox-only token; TypeScript-only SDK; HTTP exec cut at 16 KB (§7.1) | Sandboxes leave beta with deny-all egress and a scoped token |
| §2.2: code-runner with "a non-root uid" | one unprivileged uid **per run**, its processes killed and its files swept after the run, one run per launcher | a long-lived shared runner can let one run leave a process or files for the next tenant | n/a |
| §2.2 and §6.8 accept open platform egress for the runner | network denial is a mandatory exit gate; without it, tier 2 waits | an analyst that reads uploads (A) and private data (B) with open egress (C) is the Rule-of-Two trifecta (§6.1) | n/a |
| §2.3: schedules from pg_cron or the worker clock | scheduled and event workflows ride PR #2000's JOBS registry and scheduled_jobs lease (F11) | one scheduler for the platform | PR #2000 is rejected (then one `location_jobs` row) |
| §5.7 Phase A: eval tables and an OpenTelemetry exporter now | cases as git JSON with blessed baselines; no eval tables; no OTel until a viewer is chosen | a dependency with no consumer; exporting traces ships broker PII to a vendor | Langfuse's triggers fire |
| §4.7: ship a platform mailbox first | ledger and `mailto:` first; then an inbound-only reply subdomain; then gmail.send as the user | a platform sender makes the platform controller of mail to a scraped directory, builds one shared reputation, and is a new channel class under ruling R2b | Google verification stalls a quarter, or Seznam users dominate |
| §3.6: Supabase OAuth spike gates | the same gates plus three: the token is audience-bound to /mcp, cannot read PostgREST, cannot call the user endpoint (#2877) | a Supabase-issued token is a full Supabase session (§3.3) | n/a |
| §2.3: generalise the `estimation_runs` lane into a run table and a step table | a new pair, agent_runs and agent_run_steps, linked to `estimation_runs` (PROGRAM D4) | widening `estimation_runs` would rewrite rule 12 and keep its browser-write shape (F16) | the operator prefers one table and rewrites rule 12 |
| §6.8: else use the provider's container for analysis-only steps | provider-hosted execution stays excluded (PROGRAM AR4) | model-bound, US-held or not ZDR-eligible, and Gemini cannot return files (§2.2); Phase 7d excluded it | both Railway rungs fail and the operator accepts one provider for analysis steps |
| §1.5, §2.1: generic loop "driven by agent definitions" | kept; definitions are git files under a lock, pinned into each run at submit | ends the submit-vs-claim drift (F31) | n/a |
