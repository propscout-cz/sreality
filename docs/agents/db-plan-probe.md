# Plan-probing a function re-sign on production (no DDL)

Prove that a re-signed or rewritten SQL function keeps its plan BEFORE the migration ships,
against production data, without creating anything. Done this way for migration 629 on
2026-10-19 (`migrations/629_curated_set_scope.sql`, its "Probed on production" paragraph).

## Steps

1. **Learn PostgREST's real call shape** from `extensions.pg_stat_statements` (filter `query ilike
   '%<fn>%'`). The probe must reproduce that shape, because the plan depends on it:
   - GET rpc with arguments: called directly, `"public"."fn"("a" := $1::text, ...)`.
   - POST rpc: `(SELECT $1 AS json_data) pgrst_payload, LATERAL (SELECT ... FROM
     json_to_record(pgrst_payload.json_data) AS _(...)) pgrst_args`.
   - Zero-arg set-returning function: `WITH pgrst_source AS (SELECT ... FROM "public"."fn"() pgrst_call)`.
2. **Inline the planned body as a subquery in exactly that shape.** A `language sql stable`
   invoker function with no `SET` is inlined, so the planner sees the same tree. Stand in for each
   bound parameter with `current_setting('probe.<name>', true)` (cast as the parameter is), so
   nothing constant-folds at plan time. Set each with
   `select set_config('probe.<name>', '<value>', false);`.
3. **Make RLS real.** The MCP runs as `postgres`, which bypasses RLS. Set the caller with
   `select set_config('request.jwt.claims', '{"sub":"<user uuid>","role":"authenticated"}', false);`
   and spell each policy predicate on the base tables yourself
   (`account_id in (select current_account_ids())`).
4. **End the MCP call with the EXPLAIN.** The Supabase MCP returns only the LAST statement's result,
   so `explain (analyze, costs off, timing off, summary on) <probe>` goes last, after the
   `set_config` lines. To collect several plans in one call, run them in a `DO` block that writes
   each into a temp table, then `select` that table last.
5. **Never create a function for a probe.** The auto-mode classifier declines DDL in `execute_sql`
   (and a probe must leave nothing behind anyway). If the shape needs a function, inline it.
6. **Record the baseline next to the probe.** Run the same probe against today's body (or the live
   function) and put both numbers in the migration header and the PR body.

## What to read in the plan

- The index the live function uses must still appear (keyset `Index Scan` / `Index Only Scan` on
  the read model, no new top-level `Sort`).
- An inactive branch (a defaulted parameter left null) should show `(never executed)`, e.g. a
  hashed SubPlan that never runs.
- `Execution Time` from `summary on` is the number to compare; `timing off` keeps the overhead low.

## Reference: the 629 numbers (production, 2026-10-19)

| Probe | Baseline | Re-signed |
| --- | --- | --- |
| Browse card page (GET, direct call shape) | 0.9 ms | 2.3 ms |
| Exact count, 177k-row byt+pronájem cohort, all three exclusions | 44 ms | 208 ms |
| Inactive scope sides | n/a | hashed SubPlan, never executed |
| The re-signed body probed in the POST `LATERAL json_to_record` shape | n/a | 171 ms: loses the keyset index, adds a top-N sort |

The last row is why step 1 matters: probing the wrong call shape gives a plan production never runs.

## Related

- Re-signing rules (exact-signature drop + create + ACL in one transaction, md5 gate on the live
  body): `.claude/skills/database/SKILL.md`, "Re-signing `browse_stats_properties` / `browse_map_cells`".
- Reading production cheaply (`psql` first, MCP `execute_sql` fallback): the `database` skill.
