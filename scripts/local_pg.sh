#!/usr/bin/env bash
# A local Postgres that matches CI's schema-and-sql job (.github/workflows/migrations.yml):
# PostgreSQL 17 + PostGIS + pgvector — no sudo, no Docker. Linux: conda-forge via micromamba under
# ~/.local/opt. macOS: Homebrew (conda-forge has no PostGIS for PG17 on macOS; Homebrew's builds for postgresql@17).
# The replayed schema is kept as a template database; every test run gets a fresh copy.
#
#   scripts/local_pg.sh install        # one-time: Postgres 17 + PostGIS + pgvector (~550 MB on Linux)
#   scripts/local_pg.sh replay         # start the server, replay every migration into the template
#   scripts/local_pg.sh test <args>    # a private copy (ci_<pid>, dropped on exit) + pytest <args>
#   scripts/local_pg.sh lanes          # a private copy + every pytest lane of the CI job, in CI's order
#   scripts/local_pg.sh db <name>      # a long-lived copy named <name>; prints its TEST_DATABASE_URL
#   scripts/local_pg.sh drop <name>    # drop such a copy
#   scripts/local_pg.sh psql [db] [psql args]   # psql as the script's user into db (default: the template)
#   scripts/local_pg.sh start|stop|status       # status also prints the template's stamp and the copies
# Several sessions share one cluster: every run gets its own database, and replay (which rebuilds the
# cluster, as roles are cluster-wide) refuses while any other copy exists; pass --force to override.
# replay stamps the template with the migrations it applied; a copy is refused while the template lacks
# one of the calling worktree's migrations/ (replay, or STALE_OK=1 to run against it anyway).
set -euo pipefail

OPT="$HOME/.local/opt"
MM="$OPT/micromamba/bin/micromamba"
ENV_DIR="$OPT/sreality-pg17"
DATA="$HOME/.local/share/sreality-pg17"
PORT=54329
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" == Darwin ]]; then
  PG_BIN="$(brew --prefix 2>/dev/null || echo /opt/homebrew)/opt/postgresql@17/bin"
  LIBC_LOCALE=en_US.UTF-8
else
  PG_BIN="$ENV_DIR/bin"
  LIBC_LOCALE=C.UTF-8
  export PROJ_DATA="$ENV_DIR/share/proj"
fi
export PATH="$PG_BIN:$PATH"
export PGHOST=127.0.0.1 PGPORT=$PORT PGUSER=postgres PGPASSWORD=postgres
url_for() { echo "postgresql://postgres:postgres@127.0.0.1:$PORT/$1"; }

die() { echo "local_pg: $*" >&2; exit 1; }

install() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-*)
      command -v brew >/dev/null || die "macOS needs Homebrew first: https://brew.sh"
      brew install postgresql@17 postgis pgvector ;;
    Linux-x86_64 | Linux-aarch64)
      if [[ ! -x "$MM" ]]; then
        mkdir -p "$OPT/micromamba"
        local arch=64; [[ "$(uname -m)" == aarch64 ]] && arch=aarch64
        curl -Ls "https://micro.mamba.pm/api/micromamba/linux-$arch/latest" | tar -xj -C "$OPT/micromamba" bin/micromamba
      fi
      # CI runs 17.11; conda-forge's PG17 PostGIS lacks protobuf-c, so ST_AsMVT tile tests skip here (CI runs them).
      [[ -x "$ENV_DIR/bin/postgres" ]] || MAMBA_ROOT_PREFIX="$OPT/micromamba/root" "$MM" create -q -y -p "$ENV_DIR" \
        -c conda-forge --override-channels postgresql=17.11 postgis=3.6.0 pgvector=0.8.1 ;;
    *) die "unsupported platform $(uname -sm)" ;;
  esac
  [[ "$(postgres --version)" == *" 17."* ]] || die "expected PostgreSQL 17 (CI's major), got: $(postgres --version)"
  local ext
  for ext in postgis vector pg_trgm; do
    [[ -f "$(pg_config --sharedir)/extension/$ext.control" ]] || die "extension $ext is not visible to $(command -v postgres)"
  done
  echo "installed: $(postgres --version), PostGIS + pgvector visible"
}

ensure_server() {
  [[ -x "$PG_BIN/postgres" ]] || die "not installed; run: scripts/local_pg.sh install"
  if [[ ! -f "$DATA/data/PG_VERSION" ]]; then
    mkdir -p "$DATA"
    initdb -D "$DATA/data" -U postgres --auth=trust --encoding=UTF8 \
      --locale-provider=icu --icu-locale=en-US --locale="$LIBC_LOCALE" >/dev/null
    # CI's container runs UTC with default memory settings; durability is irrelevant for a scratch replay.
    cat >> "$DATA/data/postgresql.conf" <<CONF
listen_addresses = '127.0.0.1'
port = $PORT
unix_socket_directories = '$DATA'
timezone = 'Etc/UTC'
log_timezone = 'Etc/UTC'
fsync = off
synchronous_commit = off
full_page_writes = off
CONF
  fi
  pg_ctl -D "$DATA/data" status >/dev/null 2>&1 || pg_ctl -D "$DATA/data" -l "$DATA/server.log" -w start >/dev/null
}

psql_q() { psql -X -v ON_ERROR_STOP=1 -q "$@"; }

copies() {
  psql_q -At -d postgres -c "select datname from pg_database where not datistemplate and datname <> 'postgres'"
}

# Every copy a replay would refuse over (a dead run's ci_<pid> it drops itself), with its age, on one line.
blocking_copies() {
  local db age
  while IFS='|' read -r db age; do
    [[ "$db" =~ ^ci_[0-9]+$ ]] && ! kill -0 "${db#ci_}" 2>/dev/null && continue
    echo "$db (${age:-age unknown})"
  done < <(psql_q -At -d postgres -c "select datname, date_trunc('minute', now()
             - (pg_stat_file('base/' || oid || '/PG_VERSION', true)).modification) || ' old'
           from pg_database where not datistemplate and datname <> 'postgres' order by datname") \
    | paste -sd, - | sed 's/,/, /g'
}

migration_names() { (cd "$REPO/migrations" && ls -- *.sql | LC_ALL=C sort); }
template_names() {
  psql_q -At -d postgres -c "select shobj_description(oid, 'pg_database') from pg_database where datname = 'replay'"
}
stamp_of() { [[ -n "$1" ]] && echo "$(grep -c . <<<"$1") $(tail -1 <<<"$1")" || echo unstamped; }
# The migrations of $2 that $1 lacks, as "<count> (first <name>)"; nothing when none.
lacking() {
  local names
  names=$(LC_ALL=C comm -13 <(printf '%s\n' "$1") <(printf '%s\n' "$2") | grep . || true)
  [[ -z "$names" ]] || echo "$(grep -c . <<<"$names") (first $(head -1 <<<"$names"))"
}

# A template that lacks a migration of this worktree would hide its schema change behind unrelated failures.
require_current_template() {
  local have want missing blockers
  have=$(template_names)
  want=$(migration_names)
  missing=$(lacking "$have" "$want")
  [[ -n "$have" && -z "$missing" ]] && return
  local msg="template stale ($(stamp_of "$have") vs $(stamp_of "$want")): run scripts/local_pg.sh replay"
  if [[ "${STALE_OK:-}" == 1 ]]; then
    echo "local_pg: STALE_OK=1, running anyway: $msg" >&2
    return
  fi
  blockers=$(blocking_copies)
  [[ -n "$have" ]] && missing="it lacks $missing of this worktree's migrations" || missing="it was replayed before stamps"
  die "$msg
  $missing; copies that block a replay: ${blockers:-none}
  (ask their owners, or replay --force; STALE_OK=1 runs against the stale template)"
}

drop_stale_runs() {
  local db
  for db in $(copies | grep -E '^ci_[0-9]+$' || true); do
    kill -0 "${db#ci_}" 2>/dev/null || psql_q -d postgres -c "drop database if exists $db with (force)"
  done
}

replay() {
  # A fresh cluster, as CI's fresh container: roles are cluster-wide, so the bootstrap cannot re-run on an old one.
  if [[ "${1:-}" != --force ]] && pg_ctl -D "$DATA/data" status >/dev/null 2>&1; then
    drop_stale_runs
    local others
    others=$(copies | tr '\n' ' ')
    [[ -z "$others" ]] || die "other copies live on this cluster (${others% }); a replay deletes them. Ask their owners, or: replay --force"
  fi
  if [[ -f "$DATA/data/PG_VERSION" ]]; then
    pg_ctl -D "$DATA/data" status >/dev/null 2>&1 && pg_ctl -D "$DATA/data" -m fast stop >/dev/null
    rm -rf "$DATA/data"
  fi
  ensure_server
  cd "$REPO"
  psql_q -d postgres -c "create database replay"
  export PGDATABASE=replay PGOPTIONS="-c client_min_messages=warning"
  # The postgis/postgis image creates these in its default database before CI's bootstrap runs.
  psql_q -c "create extension if not exists postgis; create extension if not exists postgis_topology;
             create extension if not exists fuzzystrmatch; create extension if not exists postgis_tiger_geocoder;"
  psql_q -f scripts/ci_db_bootstrap.sql
  local started=$SECONDS
  # CI's runner sorts in C.UTF-8, i.e. byte order; pin it so a machine's locale cannot reorder the replay.
  for f in $(ls migrations/*.sql | LC_ALL=C sort); do
    case "$(basename "$f")" in
      295_*) psql_q -c "insert into auth.users (email) values ('ci-operator@replay.local');" ;;
      510_*) continue ;;  # retired by 513; see migrations.yml
    esac
    psql_q -f "$f" >/dev/null || die "replay failed at $f"
  done
  psql_q -c "select 'listings'::regclass, 'properties_public'::regclass, 'image_clip_embeddings'::regclass" >/dev/null
  psql_q -f migrations/656_notifications_w6_prune.sql >/dev/null
  psql_q -d postgres -c "alter database replay is_template true"
  # psql interpolates :'var' only in commands it reads from stdin, not from -c.
  local applied
  applied=$(migration_names)
  psql_q -d postgres -v applied="$applied" <<<"comment on database replay is :'applied'"
  echo "replayed $(grep -c . <<<"$applied") migrations in $((SECONDS - started)) s (last: $(tail -1 <<<"$applied"))"
}

copy_of_template() {
  ensure_server
  psql_q -At -d postgres -c "select 1 from pg_database where datname = 'replay' and datistemplate" | grep -q 1 \
    || die "no replayed template; run: scripts/local_pg.sh replay"
  require_current_template
  psql_q -d postgres -c "set client_min_messages = warning" -c "drop database if exists $1 with (force)" -c "create database $1 template replay"
}

# This run's private copy, dropped when the script exits (a crashed run's copy is dropped by the next replay).
RUN_DB="ci_$$"
fresh() {
  copy_of_template "$RUN_DB"
  trap 'psql_q -d postgres -c "drop database if exists $RUN_DB with (force)" >/dev/null 2>&1 || true' EXIT
  drop_stale_runs
}

status() {
  pg_ctl -D "$DATA/data" status || return
  local have want verdict missing copies
  have=$(template_names)
  want=$(migration_names)
  missing=$(lacking "$have" "$want")
  if [[ -z "$have" ]]; then verdict="replay to stamp it"
  elif [[ -n "$missing" ]]; then verdict="stale, it lacks $missing"
  else verdict="current"; fi
  echo "template: $(stamp_of "$have"); this worktree: $(stamp_of "$want"); $verdict"
  copies=$(blocking_copies)
  echo "copies: ${copies:-none}"
}

run_pytest() {
  cd "$REPO"
  TEST_DATABASE_URL="$(url_for "$RUN_DB")" DB_RAILS_REQUIRED=1 python3 -m pytest "$@"
}

lanes() {
  fresh
  cd "$REPO"
  local lane failed=()
  while IFS= read -r lane; do
    echo ">>> $lane"
    # bash -c keeps the workflow's own quoting (e.g. -k "not migration_633").
    TEST_DATABASE_URL="$(url_for "$RUN_DB")" DB_RAILS_REQUIRED=1 bash -c "python3 -m $lane" </dev/null || failed+=("$lane")
  done < <(python3 - <<'PY'
import yaml
steps = yaml.safe_load(open(".github/workflows/migrations.yml"))["jobs"]["schema-and-sql"]["steps"]
for s in steps:
    if "TEST_DATABASE_URL" in (s.get("env") or {}) and "pytest" in s.get("run", ""):
        print(" ".join(s["run"].replace("\\\n", " ").split()))
PY
)
  ((${#failed[@]} == 0)) && { echo "all lanes passed"; return; }
  printf 'FAILED lane: %s\n' "${failed[@]}"
  return 1
}

case "${1:-}" in
  install) install ;;
  replay) replay "${2:-}" ;;
  test) shift; fresh; run_pytest "$@" ;;
  lanes) lanes ;;
  start) ensure_server; echo "running on port $PORT" ;;
  stop) pg_ctl -D "$DATA/data" -m fast stop ;;
  status) status ;;
  psql) shift; ensure_server; db=replay
        [[ $# -gt 0 && "$1" != -* ]] && { db=$1; shift; }
        exec psql -X -d "$db" "$@" ;;
  db) [[ "${2:-}" =~ ^[a-z][a-z0-9_]*$ && "$2" != replay && "$2" != postgres ]] || die "usage: db <name> (lowercase, not replay/postgres)"
      copy_of_template "$2"; url_for "$2" ;;
  drop) [[ "${2:-}" =~ ^[a-z][a-z0-9_]*$ && "$2" != replay && "$2" != postgres ]] || die "usage: drop <name>"
        ensure_server; psql_q -d postgres -c "drop database if exists $2 with (force)" ;;
  *) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 2 ;;
esac
