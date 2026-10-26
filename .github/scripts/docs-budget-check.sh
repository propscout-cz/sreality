#!/usr/bin/env bash
# Hard-fail context-budget gate for always-loaded docs (CLAUDE.md's Context-discipline
# rule). Every byte here is paid for on every request in every session, so budgets are
# enforced in CI instead of relying on review discipline.
#
# Caps count BYTES, not lines: under a 300-line cap CLAUDE.md held at 291-300 lines over the
# 60 days to 2026-10-26 while it grew from 23.2 KB to 34.0 KB, because a line cap pays authors
# to join lines. Caps only ratchet down: each was set at the file's size + 1.5 %, rounded up to
# 250 B (CLAUDE.md + 600 B for three PRs then in flight). Growth is judged against the base ref:
# a PR that grows a file past its cap fails, one that trims a file under 95 % of its cap fails
# until it sets the lower cap printed, and a file the PR does not grow only warns.
#
# Usage: .github/scripts/docs-budget-check.sh [repo-root] [base-ref]
#   Without base-ref only the absolute caps apply (a local run).
set -uo pipefail

ROOT="${1:-.}"
BASE="${2:-}"
cd "$ROOT" || exit 1

CLAUDE_MD_BYTES=35000
ROADMAP_MD_BYTES=15500
declare -A SKILL_BYTES
SKILL_BYTES[database]=43000
SKILL_BYTES[interface-design]=23250
SKILL_BYTES[llm-pipelines]=13750
SKILL_BYTES[scraper-ops]=66500
SKILL_BYTES[toolkit-api]=49000
NEW_SKILL_BYTES=24000

SELF=".github/scripts/docs-budget-check.sh"
fail=0

if [[ -n "$BASE" ]] && ! git rev-parse -q --verify "${BASE}^{commit}" >/dev/null; then
  echo "::warning::base ref '${BASE}' not found; checking absolute caps only."
  BASE=""
fi
if [[ -z "$BASE" && -n "${GITHUB_ACTIONS:-}" ]]; then
  echo "::warning::no base ref: growth is not attributed to this PR and the ratchet only warns. Pass the PR's base branch as the second argument."
fi
BASE_SELF=""
[[ -n "$BASE" ]] && BASE_SELF=$(git show "${BASE}:${SELF}" 2>/dev/null)

round_up_250() { echo $(( ($1 + 249) / 250 * 250 )); }

base_bytes() {
  [[ -n "$BASE" ]] && git cat-file -e "${BASE}:$1" 2>/dev/null || return 0
  git show "${BASE}:$1" | wc -c | tr -d ' '
}

base_cap() { awk -v p="$1=" 'index($0, p) == 1 { print substr($0, length(p) + 1) + 0; exit }' <<< "$BASE_SELF"; }

check_bytes() {
  local file="$1" cap="$2" setting="$3"
  [[ -f "$file" ]] || return 0
  local n b old suggested grew=1 shrank=0 delta=""
  n=$(wc -c < "$file" | tr -d ' ')
  b=$(base_bytes "$file")
  if [[ -n "$b" ]]; then
    (( n > b )) || grew=0
    (( n < b )) && shrank=1
    delta=" ($(printf '%+d' $(( n - b ))) B in this PR)"
  fi
  old=$(base_cap "$setting")
  if [[ -n "$old" ]] && (( cap > old )); then
    echo "::warning file=${SELF}::${setting} rises from ${old} to ${cap}. Caps only ratchet down; raising one is the operator's call — say so in the PR body."
  fi
  suggested=$(round_up_250 $(( (n * 1015 + 999) / 1000 )))
  if (( n > cap )); then
    if (( grew )); then
      echo "::error file=${file}::${file} is ${n}/${cap} bytes${delta}. Caps only ratchet down: remove at least $(( n - cap )) bytes from ${file} before adding — move mechanics and incident history to docs/architecture.md, roadmap/<track>.md or the skill's references/, and leave the rule plus a one-line pointer."
      fail=1
    else
      echo "::warning file=${file}::${file} is ${n}/${cap} bytes, over its cap on the base branch; this PR does not grow it. The next PR that adds to ${file} first removes $(( n - cap )) bytes."
    fi
  elif (( n * 100 < cap * 95 && suggested < cap )); then
    if (( shrank )); then
      echo "::error file=${file}::${file} is ${n}/${cap} bytes${delta}, under 95 % of its cap. Lock the trim in: set ${setting}=${suggested} in ${SELF} in this PR."
      fail=1
    else
      echo "::warning file=${file}::${file} is ${n}/${cap} bytes, under 95 % of its cap: set ${setting}=${suggested} in ${SELF}."
    fi
  else
    echo "OK   ${file}: ${n}/${cap} bytes${delta}"
  fi
}

check_bytes "CLAUDE.md" "$CLAUDE_MD_BYTES" "CLAUDE_MD_BYTES"
check_bytes "ROADMAP.md" "$ROADMAP_MD_BYTES" "ROADMAP_MD_BYTES"

for skill_file in .claude/skills/*/SKILL.md; do
  [[ -f "$skill_file" ]] || continue
  name=$(basename "$(dirname "$skill_file")")
  check_bytes "$skill_file" "${SKILL_BYTES[$name]:-$NEW_SKILL_BYTES}" "SKILL_BYTES[${name}]"
done

# Frontmatter `description:` is always loaded into every session (not just when the
# skill fires), so it gets its own budget separate from the body.
for skill_file in .claude/skills/*/SKILL.md; do
  [[ -f "$skill_file" ]] || continue
  desc_line=$(grep -m1 '^description:' "$skill_file" || true)
  if [[ -z "$desc_line" ]]; then
    echo "::error file=${skill_file}::skill frontmatter has no 'description:' line."
    fail=1
    continue
  fi
  desc="${desc_line#description:}"
  words=$(wc -w <<< "$desc" | tr -d ' ')
  if (( words > 150 )); then
    echo "::error file=${skill_file}::frontmatter description is ${words}/~150 words. Descriptions are always-loaded into every session (unlike the body, which loads on demand) — trim it and move detail into the skill body instead."
    fail=1
  else
    echo "OK   ${skill_file} description: ${words}/~150 words"
  fi
done

# Routing completeness: a skill nobody routes to never fires. Every directory under
# .claude/skills/ must be named in CLAUDE.md's "Where the detail lives" table.
if [[ -f CLAUDE.md ]]; then
  for skill_dir in .claude/skills/*/; do
    [[ -d "$skill_dir" ]] || continue
    name=$(basename "$skill_dir")
    if ! grep -q "\.claude/skills/${name}\b" CLAUDE.md; then
      echo "::error file=CLAUDE.md::skill '${name}' exists under .claude/skills/ but isn't referenced in the \"Where the detail lives\" table, so a session doing that kind of work never learns it exists. Add a row: | <when to load> | \`.claude/skills/${name}\` |"
      fail=1
    else
      echo "OK   routing: ${name} referenced in CLAUDE.md"
    fi
  done
fi

echo
if (( fail )); then
  echo "Docs budget check FAILED."
  exit 1
fi
echo "Docs budget check passed."
