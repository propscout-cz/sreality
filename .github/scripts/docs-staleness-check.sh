#!/usr/bin/env bash
# Non-blocking staleness nudge: this is the anti-recurrence mechanism, not a gate.
# Warns (GitHub warning annotation, never fails the build) when a PR changes code a
# skill documents but touches no file under that skill's directory. Plenty of PRs
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

skill_touched() {
  local skill="$1"
  grep -q "^\.claude/skills/${skill}/" <<< "$changed"
}

warn_if_stale() {
  local pattern="$1" skill="$2" label="$3"
  if grep -qE "$pattern" <<< "$changed" && ! skill_touched "$skill"; then
    echo "::warning::This PR changes ${label} but doesn't touch .claude/skills/${skill}/SKILL.md — confirm the skill still describes current behavior (or that this change genuinely doesn't need it). See CLAUDE.md's Git-workflow rule: update the owning skill in the same PR."
  fi
}

warn_if_stale '^migrations/'                                    database       "migrations/"
warn_if_stale '^(toolkit|api)/'                                  toolkit-api    "toolkit/ or api/"
warn_if_stale '^api/llm_client\.py$|^api/providers/'             llm-pipelines  "the LLM provider/client layer"
warn_if_stale '^scraper/|^\.github/workflows/.*index_walk' scraper-ops "scraper/ or an index-walk workflow"
warn_if_stale '^scripts/verify_pipeline\.py$|^scripts/checks/|^toolkit/system_alerts\.py$|^scripts/health_canary\.py$|^scripts/record_workflow_failures\.py$|^scripts/failure_signature\.py$|^\.github/workflows/verify_pipeline\.yml$' scraper-ops "the health check harness, its ladder, the canary or its workflow"

# The admin Agent platform page states each ticket of the programme; the PR that closes one moves it there.
agent_map='frontend/src/lib/agentPlatformMap.ts'
if ! grep -qx "$agent_map" <<< "$changed"; then
  for n in $(grep -oiE '(close[sd]?|fix(e[sd])?|resolve[sd]?) #[0-9]+' <<< "${PR_BODY:-}" | grep -oE '[0-9]+'); do
    if grep -qE "\bn: ${n}, done: false" "$agent_map"; then
      echo "::warning::This PR closes #${n} but doesn't touch ${agent_map}: flip its done so the admin Agent platform page stays true."
    fi
  done
fi

exit 0
