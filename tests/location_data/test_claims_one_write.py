"""Location W3, offline: the ONE claim write and its key, read off the statement itself.

The executed behaviour (write-on-change, A -> B -> A, the tombstone, the operator outranking
the portal) is `test_claims_one_write_live.py`'s; these pin the shape that behaviour rests on.
"""

from __future__ import annotations

import re

from location_data import claims_store

_SQL = " ".join(claims_store._WRITE_CLAIMS_SQL.split())
_VALUE_COLUMNS = ("value_text", "value_num", "value_geom", "value_jsonb",
                  "declared_precision_label", "declared_radius_m", "blur_evidence",
                  "subject_scoped")


def _do_update() -> tuple[str, str]:
    """(the SET list, the WHERE of the claim upsert's DO UPDATE)."""
    clause = _SQL.split("ON CONFLICT (listing_id, claim_type, licence_class) DO UPDATE SET")[1]
    clause = clause.split("RETURNING")[0]
    set_list, _, where = clause.partition(" WHERE ")
    return set_list, where


def test_the_upsert_is_keyed_on_listing_type_and_author() -> None:
    assert "ON CONFLICT (listing_id, claim_type, licence_class) DO UPDATE SET" in _SQL
    assert "claim_fingerprint" not in _SQL
    assert "DISTINCT ON (listing_id, claim_type, licence_class)" in _SQL
    # Two claims of one key with one observation time: the later in the batch, never chance.
    assert "jsonb_array_elements(%(rows)s::jsonb) WITH ORDINALITY AS e(row, n)" in _SQL
    assert "ORDER BY listing_id, claim_type, licence_class, first_observed_at DESC, n DESC" in _SQL


def test_it_writes_only_when_one_of_the_eight_value_columns_moved() -> None:
    """Provenance (`first_observed_at`, the entry, the surface, the method) moves WITH the
    value and never decides on its own whether a row is written: a bump that restates the
    same value writes nothing."""
    set_list, where = _do_update()
    old, _, new = where.partition(" IS DISTINCT FROM ")
    assert re.findall(r"c\.(\w+)", old) == list(_VALUE_COLUMNS)
    assert re.findall(r"EXCLUDED\.(\w+)", new) == list(_VALUE_COLUMNS)
    for moved in ("first_observed_at", "contract_entry_id", "surface", "extraction_method"):
        assert f"{moved} = EXCLUDED.{moved}" in set_list, moved
        assert moved not in where, moved


def test_a_tombstone_deletes_its_own_key_and_never_inserts() -> None:
    gone = _SQL.split("gone AS (")[1].split("), enqueued AS")[0]
    for column in ("listing_id", "claim_type", "licence_class"):
        assert f"c.{column} = o.{column}" in gone, column
    assert "WHERE o.retract AND" in gone
    assert "WHERE NOT coalesce(o.retract, false)" in _SQL.split("ON CONFLICT")[0]


def test_only_a_changed_listing_is_enqueued_and_the_enqueue_bumps() -> None:
    enqueued = _SQL.split("enqueued AS (")[1]
    assert "FROM (SELECT listing_id FROM up UNION ALL SELECT listing_id FROM gone)" in enqueued
    assert "%(reason)s::text" in enqueued
    assert "ON CONFLICT (listing_id) DO UPDATE SET enqueued_at = now()" in enqueued
