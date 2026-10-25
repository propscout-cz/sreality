---
status: accepted
date: 2026-10-25
---
# Agent runs are recorded in their own tables, beside the estimation record

Every estimation is already recorded in `estimation_runs`, which CLAUDE.md rule 12 names the single source of truth for
estimations, with an immutable result and a row created at submit. When the agent platform needed a record for every
kind of run (a scout shortlist, a correspondence draft, later a redevelopment memo), the choice was to widen that table
or to add a new pair: `agent_runs` (one row per run: who started it, which agent and version, status, budget, result)
and `agent_run_steps` (an append-only diary written as the run goes). We chose the new pair, linked to the estimation
row where a run is an estimation. Widening `estimation_runs` would have rewritten rule 12, kept a table the browser
role can currently insert into, and mixed a trace written only at the end with a diary written step by step.
Ruling AR6 of `docs/design/agent-platform/PROGRAM.md`, taken by the operator on 2026-10-25.
