#!/usr/bin/env bash
# Fetch GitHub Actions logs for a run, pre-filtered so only relevant lines enter context.
# Usage: scripts/logs.sh <run-id> [grep-pattern]
#   no pattern -> each failed job's last 100 lines up to its last ##[error]; with pattern -> grep every job's log.
#   A run still in progress works too: its finished jobs are listed on stderr and read one by one.
set -uo pipefail
run_id="${1:?usage: scripts/logs.sh <run-id> [grep-pattern]}"
pattern="${2:-}"
status=$(gh run view "$run_id" --json status -q .status) || exit 1
# Every job is read through the per-job logs endpoint (it works mid-run), each line prefixed with its job.
jobs=$(gh run view "$run_id" --json jobs \
  -q '.jobs[] | select(.status == "completed") | "\(.databaseId)\t\(.conclusion)\t\(.name)"') || exit 1
if [ "$status" != completed ]; then
  finished=$(cut -f2,3 <<<"$jobs" | grep . | tr '\t' ' ' | paste -sd, - | sed 's/,/, /g')
  echo "logs.sh: run $run_id is $status; finished jobs: ${finished:-none yet}" >&2
fi
job_log() { gh api "repos/{owner}/{repo}/actions/jobs/$1/logs" | awk -v job="$2" '{ print job "\t" $0 }'; }
failed=0 unread=0
while IFS=$'\t' read -r job conclusion name; do
  [ -n "$job" ] || continue
  if [ -n "$pattern" ]; then
    job_log "$job" "$name" | grep -E "$pattern" | tail -100
    [ "${PIPESTATUS[0]}" -eq 0 ] || unread=$((unread + 1))
  elif [ "$conclusion" = failure ] || [ "$conclusion" = timed_out ]; then
    failed=$((failed + 1))
    job_log "$job" "$name" | awk '{ line[NR] = $0 } /##\[error\]/ { end = NR }
      END { if (!end) end = NR; for (i = end > 100 ? end - 99 : 1; i <= end; i++) print line[i] }'
    [ "${PIPESTATUS[0]}" -eq 0 ] || unread=$((unread + 1))
  fi
done <<<"$jobs"
if [ "$unread" -gt 0 ]; then
  echo "logs.sh: $unread job log(s) of run $run_id could not be fetched (gh's error above)" >&2
  exit 1
fi
[ -n "$pattern" ] || [ "$failed" -gt 0 ] || echo "logs.sh: no failed job in run $run_id (yet)" >&2
