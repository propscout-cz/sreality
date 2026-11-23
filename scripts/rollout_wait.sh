#!/usr/bin/env bash
# rollout_wait.sh <merge-sha>, short or full, from a checkout: wait for Railway's rollout of a merge.
# Expects every status context on the first parent, plus any on the merge; prints each one's state and description
# (a success reading "No deployment needed" is a watch skip, not a deploy).
# Exit 0: all green. 1: a failure, at once. 2: 20 minutes gone (naming what is missing), an unresolvable commit or bad
# usage. Poll errors retry within the 20 minutes. A merge that adds or removes a service: the IaC wizard's wait decides.
set -uo pipefail

BUDGET=1200 INTERVAL=30 waited=0
errors=$(mktemp)
trap 'rm -f "$errors"' EXIT

# read_commit <path under commits/> <jq filter>: REPLY = the answer. No such commit stops here; other errors are blips.
read_commit() {
  REPLY=$(gh api "repos/{owner}/{repo}/commits/$1" --jq "$2" 2>"$errors") && return 0
  case "$(cat "$errors")" in *"HTTP 404"* | *"HTTP 422"*) cat "$errors" >&2; exit 2 ;; esac
  echo "poll error, retrying: $(cat "$errors")" >&2
  return 1
}

# pause <what is outstanding>: spend one interval of the budget; with none left, exit 2.
pause() {
  (( waited + INTERVAL <= BUDGET )) || { echo "TIMEOUT after $(( waited / 60 )) minutes, waiting on: $1" >&2; exit 2; }
  sleep "$INTERVAL"
  waited=$(( waited + INTERVAL ))
}

[[ $# == 1 && $1 =~ ^[0-9a-fA-F]{7,40}$ ]] || { echo "usage: scripts/rollout_wait.sh <merge-sha>" >&2; exit 2; }
until read_commit "$1" '.sha, .parents[0].sha'; do pause "the commit"; done
{ read -r merge; read -r parent; } <<<"$REPLY"
until read_commit "$parent/status" '.statuses[].context'; do pause "the first parent's statuses"; done
expected=$REPLY

declare -A state detail
printed="" why="any status on the merge"
while :; do
  if read_commit "$merge/status" '.statuses[] | [.context, .state, .description] | @tsv'; then
    state=() detail=() table="" failed=() outstanding=()
    while IFS=$'\t' read -r context s d; do
      if [[ -n $context ]]; then state[$context]=$s detail[$context]=$d; fi
    done <<<"$REPLY"
    mapfile -t contexts < <(printf '%s\n' "$expected" "${!state[@]}" | sed '/^$/d' | sort -u)
    for context in "${contexts[@]}"; do
      s=${state[$context]:-missing}
      printf -v line '  %-30s %-8s %s\n' "$context" "$s" "${detail[$context]:-not reported yet}"
      table+=$line
      case $s in success) ;; failure | error) failed+=("$context") ;; *) outstanding+=("$context") ;; esac
    done
    if [[ $table != "$printed" ]]; then printf '%s' "$table"; printed=$table; fi
    if (( ${#failed[@]} )); then echo "FAILED on ${merge:0:9}: ${failed[*]}. Take it to the operator." >&2; exit 1; fi
    if (( ${#contexts[@]} && ! ${#outstanding[@]} )); then echo "Rollout of ${merge:0:9}: all green."; exit 0; fi
    if (( ${#outstanding[@]} )); then printf -v why '%s, ' "${outstanding[@]}"; why=${why%, }; fi
  fi
  pause "$why"
done
