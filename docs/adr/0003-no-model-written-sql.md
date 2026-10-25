---
status: accepted
date: 2026-10-25
---
# Agents call typed operations, never model-written SQL

Letting a model write its own SQL under the user's login looked like the most general way to "ask the data". We rejected
it: row-level security learns who the user is from a session setting that any SELECT could rewrite with `set_config`,
so a query could impersonate another account; session state leaks through the transaction pooler; free joins over a
million rows run for minutes; and the rules that keep figures consistent (per-m² measures with their basis, served
locations, parked condition levels) cannot be enforced on arbitrary text. With parity (ADR-0002) the typed operations
cover what the screens do. Reopen only as an admin-only analysis tool behind a SQL parser, if an operator ever needs a
question no operation answers. Operator ruling Q15 of 2026-10-25.
