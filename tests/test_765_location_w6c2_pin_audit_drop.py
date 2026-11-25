"""Migration 765 (W6-c2, #2361): the old pin audit page, its matview, its refresh and its registry row go; the revert
rebuilds what production held on 2026-11-22, and nothing in the repo still reads the relation."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.migration_objects import _dropped, parse_objects

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "migrations"
SQL = (MIGRATIONS / "765_location_w6c2_pin_audit_drop.sql").read_text(encoding="utf-8")
REVERT = (MIGRATIONS / "reverts" / "765_revert_location_w6c2_pin_audit_drop.sql").read_text(encoding="utf-8")
CRON_COMMAND = "set statement_timeout='900s'; select public.refresh_matview('location_pin_audit_mv');"
LIVE_INDEXES = ["location_pin_audit_mv_category", "location_pin_audit_mv_first_seen", "location_pin_audit_mv_last_seen",
                "location_pin_audit_mv_pk", "location_pin_audit_mv_quality", "location_pin_audit_mv_sibling",
                "location_pin_audit_mv_source", "location_pin_audit_mv_state"]


def _code(text: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", text).split()).lower()


def _block(text: str, tag: str) -> str:
    code = _code(text)
    at = code.index(f"do ${tag}$")
    return code[at: code.index(f"${tag}$;", at)]


def test_exactly_the_matview_goes_and_nothing_cascades() -> None:
    assert _dropped(SQL) == {("relation", "location_pin_audit_mv")}
    assert parse_objects(SQL) == []
    assert "cascade" not in _code(SQL)


def test_the_steps_run_in_order_and_the_timeouts_are_reset() -> None:
    code = _code(SQL)
    steps = ["set lock_timeout = '5s';", "set statement_timeout = '120s';", "do $cron$",
             "cron.unschedule('refresh-location-pin-audit')",
             "drop materialized view if exists public.location_pin_audit_mv;",
             "delete from public.derived_artifacts where name = 'location_pin_audit_mv';",
             "do $post$", "reset statement_timeout;", "reset lock_timeout;"]
    at = [code.index(step) for step in steps]
    assert at == sorted(at)
    assert all(code.count(step) == 1 for step in steps)


def test_the_unschedule_is_guarded_for_no_pg_cron_and_a_job_already_gone() -> None:
    cron = _block(SQL, "cron")
    assert cron.index("to_regclass('cron.job') is null") < cron.index("cron.unschedule(")
    assert "exists (select 1 from cron.job where jobname = 'refresh-location-pin-audit')" in cron


def test_the_post_check_refuses_a_relation_a_row_or_a_job_left_behind() -> None:
    post = _block(SQL, "post")
    assert "to_regclass('public.location_pin_audit_mv') is not null" in post
    assert "from public.derived_artifacts where name = 'location_pin_audit_mv'" in post
    assert "from cron.job where jobname = 'refresh-location-pin-audit'" in post
    assert post.count("raise exception") == 3


def test_the_revert_rebuilds_the_live_relation_its_refresh_and_its_row() -> None:
    assert "create materialized view if not exists public.location_pin_audit_mv as" in _code(REVERT)
    assert sorted(re.findall(r"create (?:unique )?index if not exists (location_pin_audit_mv_\w+) on", _code(REVERT))) \
        == LIVE_INDEXES
    assert "revoke all on public.location_pin_audit_mv from anon, authenticated;" in _code(REVERT)
    assert "grant select on public.location_pin_audit_mv to authenticated;" in _code(REVERT)
    assert f"'refresh-location-pin-audit', '25 * * * *', $${CRON_COMMAND}$$" in _code(REVERT)
    assert CRON_COMMAND in " ".join((MIGRATIONS / "719_location_audit_waterfall_drop.sql").read_text(encoding="utf-8").split())
    assert ("values ('location_pin_audit_mv', 'refresh_matview', 'pg_cron', '25 * * * *', interval '02:10:00', true)"
            in _code(REVERT))
    assert not list(MIGRATIONS.glob("765_revert*.sql")), "a revert beside 765 would be replayed by CI"


def test_no_surface_reads_the_relation_or_routes_the_page() -> None:
    src = REPO / "frontend" / "src"
    for gone in ("pages/LocationPinAudit.tsx", "pages/LocationPinAudit.test.tsx", "lib/pinAudit.ts",
                 "lib/pinAudit.test.ts"):
        assert not (src / gone).exists(), gone
    for path in [*src.rglob("*.ts"), *src.rglob("*.tsx")]:
        text = path.read_text(encoding="utf-8")
        for name in ("location_pin_audit_mv", "pin-audit", "PinAudit", "pinAudit", "AUDIT POLOH", "pin audit"):
            assert name not in text.replace("Pin audit", "pin audit"), f"{path.relative_to(REPO)} still names {name}"
    for tree in ("api", "scraper", "toolkit", "scripts", "location_data", "autodedup"):
        for path in (REPO / tree).rglob("*.py"):
            assert "location_pin_audit_mv" not in path.read_text(encoding="utf-8"), path.relative_to(REPO)
