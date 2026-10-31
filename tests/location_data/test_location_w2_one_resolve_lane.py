"""Migration 697 (location quality programme W2): the queue's three reasons, its claim index and
the one resolve runtime, pinned as text so a later edit cannot drift from the drain."""

from __future__ import annotations

import re
from pathlib import Path

from location_data.resolver import drain

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "697_location_w2_one_resolve_lane.sql"


def _flat() -> str:
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_the_reason_check_shrinks_to_the_three_reasons_code_writes_after_a_relabel():
    flat = _flat()
    check = re.search(r"check \(reason in \(([^)]*)\)\)", flat)
    assert check is not None
    assert sorted(v.strip(" '") for v in check.group(1).split(",")) == [
        "claim_insert", "full_sweep", "operator_edit"]
    relabel = flat.index("update dirty_locations set reason = 'full_sweep'")
    assert relabel < flat.index("add constraint dirty_locations_reason_check"), (
        "a row under a dead label must be relabelled BEFORE the CHECK can refuse it")


def test_the_claim_index_is_the_drains_order_and_the_old_one_goes():
    flat = _flat()
    order = re.search(r"order by (.+?) for update", " ".join(drain._CLAIM_SLICE_SQL.split()).lower())
    assert order is not None
    assert f"on dirty_locations ({order.group(1)})" in flat
    assert "drop index if exists dirty_locations_due" in flat


def test_the_lease_row_names_the_worker_and_the_flag_row_goes():
    flat = _flat()
    assert "set runner = 'railway-realtime-worker' where job_name = 'location_resolve_incremental'" in flat
    assert "delete from app_settings where key = 'realtime_location_resolve_enabled'" in flat
    assert "lock_timeout = '5s'" in flat
