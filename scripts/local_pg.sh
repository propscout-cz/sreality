#!/usr/bin/env bash
# A local Postgres that matches CI's schema-and-sql job (.github/workflows/migrations.yml):
# PostgreSQL 17 + PostGIS + pgvector from conda-forge via micromamba — no sudo, no Docker.
# The replayed schema is kept as a template database; every test run gets a fresh copy.
#
#   scripts/local_pg.sh install        # one-time: micromamba + the Postgres env (~550 MB)
#   scripts/local_pg.sh replay         # start the server, replay every migration into the template
#   scripts/local_pg.sh test <args>    # fresh copy + pytest <args> with TEST_DATABASE_URL set
#   scripts/local_pg.sh lanes          # fresh copy + every pytest lane of the CI job, in CI's order
#   scripts/local_pg.sh start|stop|status|url
set -euo pipefail

OPT="$HOME/.local/opt"
MM="$OPT/micromamba/bin/micromamba"
ENV_DIR="$OPT/sreality-pg17"
DATA="$HOME/.local/share/sreality-pg17"
PORT=54329
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$ENV_DIR/bin:$PATH" PROJ_DATA="$ENV_DIR/share/proj"
export PGHOST=127.0.0.1 PGPORT=$PORT PGUSER=postgres PGPASSWORD=postgres
URL="postgresql://postgres:postgres@127.0.0.1:$PORT/ci"

die() { echo "local_pg: $*" >&2; exit 1; }

platform() {
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) echo linux-64 ;;
    Linux-aarch64) echo linux-aarch64 ;;
    Darwin-arm64) echo osx-arm64 ;;
    Darwin-x86_64) echo osx-64 ;;
    *) die "unsupported platform $(uname -sm)" ;;
  esac
}

install() {
  if [[ ! -x "$MM" ]]; then
    mkdir -p "$OPT/micromamba"
    curl -Ls "https://micro.mamba.pm/api/micromamba/$(platform)/latest" | tar -xj -C "$OPT/micromamba" bin/micromamba
  fi
  # CI runs 17.11; conda-forge's PG17 PostGIS builds lack protobuf-c, so ST_AsMVT tile tests pass only in CI.
  [[ -x "$ENV_DIR/bin/postgres" ]] || MAMBA_ROOT_PREFIX="$OPT/micromamba/root" "$MM" create -q -y -p "$ENV_DIR" \
    -c conda-forge --override-channels postgresql=17.11 postgis=3.6.0 pgvector=0.8.1
  echo "installed: $(postgres --version)"
}

ensure_server() {
  [[ -x "$ENV_DIR/bin/postgres" ]] || die "not installed; run: scripts/local_pg.sh install"
  if [[ ! -f "$DATA/data/PG_VERSION" ]]; then
    mkdir -p "$DATA"
    initdb -D "$DATA/data" -U postgres --auth=trust --encoding=UTF8 \
      --locale-provider=icu --icu-locale=en-US --locale=C.UTF-8 >/dev/null
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

replay() {
  # A fresh cluster, as CI's fresh container: roles are cluster-wide, so the bootstrap cannot re-run on an old one.
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
  for f in $(ls migrations/*.sql | sort); do
    case "$(basename "$f")" in
      295_*) psql_q -c "insert into auth.users (email) values ('ci-operator@replay.local');" ;;
      510_*) continue ;;  # retired by 513; see migrations.yml
    esac
    psql_q -f "$f" >/dev/null || die "replay failed at $f"
  done
  psql_q -c "select 'listings'::regclass, 'properties_public'::regclass, 'image_clip_embeddings'::regclass" >/dev/null
  psql_q -f migrations/656_notifications_w6_prune.sql >/dev/null
  psql_q -d postgres -c "alter database replay is_template true"
  echo "replayed $(ls migrations/*.sql | wc -l) migrations in $((SECONDS - started)) s (last: $(ls migrations/*.sql | sort | tail -1))"
}

fresh() {
  ensure_server
  psql_q -d postgres -c "select 1 from pg_database where datname = 'replay' and datistemplate" | grep -q 1 \
    || die "no replayed template; run: scripts/local_pg.sh replay"
  psql_q -d postgres -c "drop database if exists ci with (force)" -c "create database ci template replay"
}

run_pytest() {
  cd "$REPO"
  TEST_DATABASE_URL="$URL" DB_RAILS_REQUIRED=1 python3 -m pytest "$@"
}

lanes() {
  fresh
  cd "$REPO"
  local lane failed=()
  while IFS= read -r lane; do
    echo ">>> $lane"
    # bash -c keeps the workflow's own quoting (e.g. -k "not migration_633").
    TEST_DATABASE_URL="$URL" DB_RAILS_REQUIRED=1 bash -c "python3 -m $lane" </dev/null || failed+=("$lane")
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
  replay) replay ;;
  test) shift; fresh; run_pytest "$@" ;;
  lanes) lanes ;;
  start) ensure_server; echo "running on port $PORT" ;;
  stop) pg_ctl -D "$DATA/data" -m fast stop ;;
  status) pg_ctl -D "$DATA/data" status ;;
  url) echo "$URL" ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
