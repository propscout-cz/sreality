#!/usr/bin/env bash
# Non-blocking staleness nudge: this is the anti-recurrence mechanism, not a gate.
# Warns (GitHub warning annotation, never fails the build) when a PR changes code a
# skill (or the design system) documents but touches no file of that doc. Plenty of PRs
# legitimately need no skill change — a check that cries wolf gets ignored or disabled,
# so this only ever warns, making "did the skill move too?" a visible question.
#
# Usage: .github/scripts/docs-staleness-check.sh <base-ref> [head-ref]
set -uo pipefail

BASE="${1:?usage: docs-staleness-check.sh <base-ref> [head-ref]}"
HEAD="${2:-HEAD}"

changed=$(git diff --name-only "${BASE}...${HEAD}" 2>/dev/null || git diff --name-only "${BASE}" "${HEAD}")
if [[ -z "$changed" ]]; then
  exit 0
fi

doc_touched() {
  local doc="$1" f
  while IFS= read -r f; do
    [[ "$f" == "$doc"* ]] && return 0
  done <<< "$changed"
  return 1
}

warn_if_stale() {
  local pattern="$1" doc="$2" label="$3"
  if grep -qE "$pattern" <<< "$changed" && ! doc_touched "$doc"; then
    echo "::warning::This PR changes ${label} but doesn't touch ${doc} — confirm it still describes current behavior (or that this change genuinely doesn't need it). See CLAUDE.md's Git-workflow rule: update the owning doc in the same PR."
  fi
}

warn_if_stale '^migrations/'                                    .claude/skills/database/      "migrations/"
warn_if_stale '^(toolkit|api)/'                                  .claude/skills/toolkit-api/   "toolkit/ or api/"
warn_if_stale '^api/llm_client\.py$|^api/providers/'             .claude/skills/llm-pipelines/ "the LLM provider/client layer"
warn_if_stale '^scraper/|^\.github/workflows/.*index_walk' .claude/skills/scraper-ops/ "scraper/ or an index-walk workflow"
warn_if_stale '^scripts/verify_pipeline\.py$|^scripts/checks/|^toolkit/system_alerts\.py$|^scripts/health_canary\.py$|^scripts/record_workflow_failures\.py$|^scripts/failure_signature\.py$|^\.github/workflows/verify_pipeline\.yml$' .claude/skills/scraper-ops/ "the health check harness, its ladder, the canary or its workflow"

# The interface-design skill is a craft guide no code change can make stale; the binding design system is, through
# the files it names as building each pattern. Generated files (the registries, workflow-docs.json) match none.
design_files=$(git show "${HEAD}:.interface-design/system.md" 2>/dev/null | grep -oE '`[A-Za-z0-9_./-]+\.(tsx?|css)`' | tr -d '`' | sed 's/\./\\./g' | sort -u | paste -sd'|')
if [[ -n "$design_files" ]]; then
  warn_if_stale "(^|/)(${design_files})\$" .interface-design/system.md "a file .interface-design/system.md names"
fi

exit 0
