#!/usr/bin/env bash
# The docs-budget job, once: CI and the pre-push hook both call this, so a local run gets CI's verdict (#2235). Docs
# checks only; ruff and codegen keep their single home. The budget blocks and reads the working tree; the symbol and
# staleness checks warn and read the committed head. Only annotations print: no OK lines.
#
# Usage: .github/scripts/docs-check.sh [base-ref]
#   Judges HEAD against its merge-base with base-ref, by default origin/main: run `git fetch origin main` first. CI passes
#   HEAD^1, the first parent of the merge it checked out, which is the same commit when origin/main is fresh.
# Exit: 0 passed (warnings may print), 1 the budget refuses, 2 no base can be computed, 3 the budget script broke.
set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd) && cd "$root" || exit 3  # the tree this script sits in, from any folder

ref="${1:-origin/main}"
if ! base=$(git merge-base "$ref" HEAD 2>/dev/null); then
  echo "::error::no merge-base of '${ref}' and HEAD to judge against. Fetch it first: git fetch origin main"
  exit 2
fi

annotations() { sed -n '/^::/p'; }

warn_only() {
  local out rc=0
  out=$("$@") || rc=$?
  annotations <<< "$out"
  (( rc == 0 )) || echo "::warning::docs check '$*' did not finish (exit ${rc})"
}

budget_rc=0
budget=$(.github/scripts/docs-budget-check.sh . "$base") || budget_rc=$?
annotations <<< "$budget"
warn_only python3 scripts/docs_symbol_check.py --base "$base"
warn_only .github/scripts/docs-staleness-check.sh "$base"

(( budget_rc == 0 )) && exit 0
[[ "$budget_rc" == 1 && "$budget" == *"Docs budget check FAILED." ]] && exit 1
echo "::error::docs-budget-check.sh broke (exit ${budget_rc}): no verdict"
exit 3
