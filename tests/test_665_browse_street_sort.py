"""Migration 665's rails: the table widens before the view under the rebuild key, and the restated view is 649's
body plus one Czech-collated column. The append-only prefix rail is tests/test_location_w3_projection.py."""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL = (MIGRATIONS / "665_browse_street_sort.sql").read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SQL)
STREET = '      ll.street_name collate "cs-CZ-x-icu" as street_name\n'


def _view(text: str) -> str:
    return re.search(r"create or replace view browse_projection as\n(.*?\n  ;\n)", text, re.S).group(1)


def test_the_table_widens_before_the_view_under_the_rebuild_key() -> None:
    at = CODE.index
    assert at("pg_advisory_xact_lock(hashtext('rebuild_browse_list'))") < at(
        'alter table public.browse_list add column if not exists street_name text collate "cs-CZ-x-icu";') < at(
        "create or replace view browse_projection as") < at("commit;")


def test_the_view_is_649s_body_plus_the_street_last() -> None:
    live = _view((MIGRATIONS / "649_web_portal.sql").read_text(encoding="utf-8"))
    mine = _view(SQL)
    assert STREET in mine
    assert mine.replace("p.newest_ad_at_web,\n" + STREET, "p.newest_ad_at_web\n") == live


def test_the_post_condition_holds_the_collation_and_the_column_order() -> None:
    post = CODE[CODE.index("do $post$"):]
    assert "c.collname = 'cs-CZ-x-icu'" in post
    assert "'%, newest_ad_at_web timestamp with time zone, street_name text'" in post
