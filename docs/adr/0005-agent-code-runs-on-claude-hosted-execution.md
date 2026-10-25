---
status: accepted
date: 2026-10-25
---
# Agent-written Python runs on Claude's hosted code execution, re-taking Phase 7d

Phase 7d (roadmap/next.md) excluded provider-hosted code execution and planned a sandboxed subprocess inside our Railway
container. Railway's service containers cannot build that sandbox (no user namespaces), the alternatives (a secretless
code-runner with a system-call filter, Railway Sandboxes in beta with open egress, a new sandbox vendor) each cost weeks
or a vendor, and the operator wants the first agents to run Python. We chose Anthropic's hosted code execution tool:
Claude runs the code in Anthropic's sealed container with no network, files travel through the Files API, outputs land
in our storage, and we operate nothing. Consequences the operator accepted: code-running agents are Claude-only (the
analyst core starts on Claude Opus 5.5; graded evals decide whether Sonnet 5.5 holds), and what a code run sends is
processed in the US with 30-day retention. The 2026-08-27 memo narrows to: cheap pool for the high-volume lanes, agents
choose per agent. Reopen a self-hosted runner if an agent must run code on a non-Claude model or on our own
infrastructure. Operator ruling Q21, Q27, Q28 of 2026-10-25.
