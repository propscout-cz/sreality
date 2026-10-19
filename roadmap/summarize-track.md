> Track file — part of [ROADMAP.md](../ROADMAP.md). After shipping, edit only this file + its index row.

## Summarize track (parallel)

LLM-derived natural-language summaries over the data the user is
already viewing. Distinct from the `browse-*` track (UI primitives
and navigation) and from the future `agent-*` track (multi-tool
reasoning). Powered by the Claude API via the FastAPI service —
the browser never holds an Anthropic key.

### summarize-1: Annotated distribution charts (done, then REMOVED 2026-10-19)
- **Removed by the Stats dashboard PR (migrations 626 + 627).** The one- to two-sentence LLM
  annotation per per-disposition Kč/m² box plot in Browse › Stats went with the box plots it
  described: the operator replaced the fixed Stats cards with charts the user composes
  (`docs/design/stats-dashboard.md`, ruling 1). The toolkit module, its tool route, request
  schemas, tests and SPA query were deleted in that PR; migration 627 drops the per-(filter set,
  day) cache table and the two `app_settings` prompt/model rows (applied on the operator's word).
  Its `llm_calls` history stays, under its own `called_for`. How it worked is in git history and
  migration 104's header.
- Was the track entry-point for Phase 6's `summarize_listing` and
  `compare_listing_images` — all three now sit in the same family.

