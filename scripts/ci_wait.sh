#!/usr/bin/env bash
# Wait for a PR's CI on ONE commit (default: its head now): the latest run of every check per workflow and job, every
# commit status, every workflow run not yet completed, and each required check of the base branch, which counts as
# pending until it reports (just pushed, or a gate no workflow has started yet). Prints a line only on a change.
# Usage: scripts/ci_wait.sh <pr> [--sha <sha>] [--timeout-min N]   (default 40; CI_WAIT_POLL=<s> sets the poll, 30)
# Exit: 0 every check passed (success, skipped or neutral), 1 a check failed, 2 timeout, usage or a probe error.
set -uo pipefail
export LC_ALL=C
cd "$(dirname "${BASH_SOURCE[0]}")/.."

usage() { sed -n '2,6p' "$0" >&2; exit 2; }
pr="" sha="" timeout_min=40 poll="${CI_WAIT_POLL:-30}"
[[ "$poll" =~ ^[1-9][0-9]*$ ]] || usage
while (($#)); do
  case "$1" in
    --sha) (($# >= 2)) || usage; sha="$2"; shift 2 ;;
    --timeout-min) [[ "${2:-}" =~ ^(0|[1-9][0-9]*)$ ]] || usage; timeout_min="$2"; shift 2 ;;
    *) [[ -z "$pr" && "$1" =~ ^[0-9]+$ ]] || usage; pr="$1"; shift ;;
  esac
done
[[ -n "$pr" ]] || usage

info=$(gh pr view "$pr" --json headRefOid,baseRefName -q '.headRefOid + " " + .baseRefName') || exit 2
read -r head base <<<"$info"
sha="${sha:-$head}"
err=$(mktemp)
trap 'rm -f "$err"' EXIT
if required=$(gh api "repos/{owner}/{repo}/branches/$base/protection/required_status_checks" --jq '.contexts[]' 2>"$err"); then
  confirm=0
  cat "$err" >&2
else
  # Without the required list a workflow that has not started yet is invisible, so a green picture must hold twice.
  echo "ci_wait: cannot read $base's required checks ($(head -c 200 "$err" | paste -sd' ' -)); waiting on the checks that report" >&2
  required="" confirm=1
fi
echo "ci_wait: PR #$pr at ${sha:0:9}, required: $(paste -sd' ' - <<<"${required:-unknown}")" >&2

# One row per check: key, name, id, state, url. Re-runs share a workflow run's check suite, so the key holds.
# A workflow run still queued may have no check run yet, so it is a pending row of its own until it completes.
probe() {
  local runs checks statuses
  runs=$(gh api --paginate "repos/{owner}/{repo}/actions/runs?head_sha=$sha&per_page=100" \
    --jq '.workflow_runs[] | ["suite", .check_suite_id, .name, .status, .id, .html_url] | @tsv') || return
  checks=$(gh api --paginate "repos/{owner}/{repo}/commits/$sha/check-runs?per_page=100" \
    --jq '.check_runs[] | ["run", .check_suite.id, .app.slug, .name, .id, .status, (.conclusion // ""), .html_url] | @tsv') || return
  statuses=$(gh api --paginate "repos/{owner}/{repo}/commits/$sha/statuses?per_page=100" \
    --jq '.[] | ["status", "-", "status", .context, .id, "completed", .state, (.target_url // "")] | @tsv') || return
  printf '%s\n' "$runs" "$checks" "$statuses" | awk -F'\t' -v OFS='\t' '
    $0 == "" { next }
    $1 == "suite" { wf[$2] = $3; if ($4 != "completed") print $3 "/(run)", "run " $4 " [" $3 "]", $5, "pending", $6; next }
    { src = ($2 in wf) ? wf[$2] : $3
      state = $6 != "completed" || $7 == "pending" ? "pending" : $7 ~ /^(success|skipped|neutral)$/ ? "pass" : "fail:" $7
      print src "/" $4, $4 " [" src "]", $5, state, $8 }' \
    | sort -t$'\t' -k1,1 -k3,3nr | awk -F'\t' '!seen[$1]++'
}

deadline=$((SECONDS + timeout_min * 60))
errors=0 last="" green=""
while :; do
  if rows=$(probe); then
    errors=0
    names=$(cut -f2 <<<"$rows" | sed 's/ \[.*//')
    missing=$(LC_ALL=C comm -23 <(sort -u <<<"$required" | grep .) <(sort -u <<<"$names"))
    pending=$(awk -F'\t' '$4 == "pending" { print $2 }' <<<"$rows")
    [[ -n "$missing" ]] && pending=$(printf '%s\n' "$pending" "$(sed 's/$/ (not reported)/' <<<"$missing")" | grep .)
    failed=$(awk -F'\t' '$4 ~ /^fail/ { sub(/^fail:/, "", $4); print "FAIL " $2 " " $4 " " $5 }' <<<"$rows")
    total=$(( $(grep -c . <<<"$rows") + $(grep -c . <<<"$missing") ))
    line="${sha:0:9}: $((total - $(grep -c . <<<"$pending"))) of $total done${pending:+; pending: $(paste -sd, - <<<"$pending" | sed 's/,/, /g')}"
    [[ "$line" != "$last" ]] && echo "ci_wait: $line" && last="$line"
    if [[ -n "$failed" ]]; then
      echo "$failed"
      exit 1
    fi
    if [[ -z "$pending" && "$total" -gt 0 ]]; then
      if ((confirm == 0)) || [[ "$rows" == "$green" ]]; then
        echo "ci_wait: all $total checks passed on ${sha:0:9}: $(cut -f2 <<<"$rows" | paste -sd, - | sed 's/,/, /g')"
        exit 0
      fi
      green="$rows"
    fi
  elif ((++errors >= 3)); then
    echo "ci_wait: GitHub probe failed 3 times in a row" >&2
    exit 2
  fi
  if ((SECONDS + poll > deadline)); then
    echo "ci_wait: timed out after $timeout_min min; ${last:-nothing reported}" >&2
    exit 2
  fi
  sleep "$poll"
done
