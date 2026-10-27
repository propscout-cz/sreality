#!/usr/bin/env bash
# Hard-fail context-budget gate for always-loaded docs (CLAUDE.md's Context-discipline
# rule). Every byte here is paid for on every request in every session, so budgets are
# enforced in CI instead of relying on review discipline.
#
# Caps count BYTES, not lines: under a 300-line cap CLAUDE.md held at 291-300 lines over the
# 60 days to 2026-10-26 while it grew from 23.2 KB to 34.0 KB, because a line cap pays authors
# to join lines. Each cap was set at the file's size + 1.5 %, rounded up to 250 B (ROADMAP.md
# + 3 % and CLAUDE.md + 600 B for PRs then in flight). Caps only ratchet down: a raise fails
# unless its line records the operator's ruling as `# raised <date>: <ruling>`. Growth is judged
# against the base ref: a PR that grows a file past its cap fails, one that trims a file under
# 95 % of its cap fails until it sets the lower cap printed, and a file the PR does not grow
# only warns.
#
# Usage: .github/scripts/docs-budget-check.sh [repo-root] [base-ref]
#   Without base-ref only the absolute caps apply (a local run).
set -uo pipefail

ROOT="${1:-.}"
BASE="${2:-}"
HEAD_SELF=$(<"${BASH_SOURCE[0]}")
cd "$ROOT" || exit 1

CLAUDE_MD_BYTES=35000
ROADMAP_MD_BYTES=15750
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
  echo "::warning::no base ref: growth is not attributed to this PR and the ratchet only warns. Pass the PR's base (its merge commit's HEAD^1) as the second argument."
fi
BASE_SELF=""
[[ -n "$BASE" ]] && BASE_SELF=$(git show "${BASE}:${SELF}" 2>/dev/null)

round_up_250() { echo $(( ($1 + 249) / 250 * 250 )); }

base_bytes() {
  [[ -n "$BASE" ]] && git cat-file -e "${BASE}:$1" 2>/dev/null || return 0
  git show "${BASE}:$1" | wc -c | tr -d ' '
}

# The line of script source $1 that sets the first of the settings $2.. it has.
setting_line() {
  local src="$1" s
  shift
  for s in "$@"; do
    awk -v p="$s=" 'index($0, p) == 1 { print; f = 1; exit } END { exit !f }' <<< "$src" && return
  done
}

line_cap() { local v="${1#*=}"; echo "${v%%[!0-9]*}"; }

check_bytes() {
  local file="$1" cap="$2" setting="$3"
  [[ -f "$file" ]] || return 0
  local n b old_line new_line suggested grew=1 shrank=0 delta=""
  n=$(wc -c < "$file" | tr -d ' ')
  b=$(base_bytes "$file")
  if [[ -n "$b" ]]; then
    (( n > b )) || grew=0
    (( n < b )) && shrank=1
    delta=" ($(printf '%+d' $(( n - b ))) B in this PR)"
  fi
  old_line=$(setting_line "$BASE_SELF" "${@:3}")
  if [[ -n "$old_line" ]] && (( cap > $(line_cap "$old_line") )); then
    new_line=$(setting_line "$HEAD_SELF" "${@:3}")
    if [[ "$new_line" == *"# raised "* && "${new_line#*# raised }" != "${old_line#*# raised }" ]]; then
      echo "::warning file=${SELF}::${new_line%%=*} rises from $(line_cap "$old_line") to ${cap}, raised ${new_line#*# raised }"
    else
      echo "::error file=${SELF}::${new_line%%=*} rises from $(line_cap "$old_line") to ${cap}. Caps only ratchet down: trim ${file} instead. A raise is the operator's ruling; record it on that line as '# raised <date>: <ruling>'."
      fail=1
    fi
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
  check_bytes "$skill_file" "${SKILL_BYTES[$name]:-$NEW_SKILL_BYTES}" "SKILL_BYTES[${name}]" NEW_SKILL_BYTES
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
