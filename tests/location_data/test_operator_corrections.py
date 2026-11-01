"""Offline contract tests for the W1v operator-correction producer.

No DB. Since location W3 the operator writes through the lane's own one write
(`claims_store.write_claims`): one claim per listing and type with licence `operator`,
which the resolver's read puts ahead of the portal's. The vocabulary discipline stays.
"""

from __future__ import annotations

import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from location_data import operator_corrections as oc

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


class _Conn:
    """The listing read answers; everything else is recorded and answered with nothing."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def cursor(self, **_: Any) -> "_Conn":
        return self

    def transaction(self) -> Any:
        return nullcontext()

    def __enter__(self) -> "_Conn":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> None:
        self.executed.append(" ".join(sql.split()))

    def fetchone(self) -> dict[str, Any]:
        return {"id": 7, "source": "idnes", "source_id_native": "n7"}


def test_the_operator_writes_one_operator_claim_through_the_one_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[tuple[list[Any], str]] = []

    def fake_write(cur: Any, claims: list[Any], reason: str = "claim_insert") -> tuple[int, int]:
        written.append((list(claims), reason))
        return 1, 1

    monkeypatch.setattr(oc, "write_claims", fake_write)
    result = oc.submit_correction(_Conn(), listing_id=7, claim_type="street_name",
                                  value_text=" Dlouhá ")

    ((claim,), reason), = written
    assert reason == "operator_edit"
    assert (claim.listing_id, claim.claim_type, claim.value_text) == (7, "street_name", "Dlouhá")
    assert (claim.licence_class, claim.surface, claim.extraction_method) == (
        "operator", "operator_input", "operator_manual")
    assert (claim.contract_entry_id, claim.claim_confidence, claim.blur_evidence) == (
        None, "exact", "none")
    assert not claim.retract
    assert (result["written"], result["enqueued"]) == (True, True)
    assert "restatement" not in result


def test_the_correction_route_never_imports_the_intake_lane() -> None:
    """The one write lives in `claims_store`, so the API's correction route pays for one small
    module, not the 2,000-line intake lane and its page readers."""
    import ast

    tree = ast.parse(Path(oc.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "location_data.claims_store" in imported
    assert "location_data.claims_intake" not in imported


def test_every_allowed_claim_type_has_an_operator_policy_row():
    sql = (MIGRATIONS / "400_location_w1v_operator_field_policy.sql").read_text()
    fields = re.search(r"unnest\(array\[(.*?)\]", sql, re.S).group(1)
    seeded = set(re.findall(r"'([a-z_]+)'", fields))
    missing = set(oc.ALLOWED_CLAIM_TYPES) - seeded
    assert not missing, f"allowed correction types without a policy row: {missing}"
    assert "'operator', 'operator_manual', 50" in re.sub(r"\s+", " ", sql)


def test_migration_400_policy_rows_cover_the_full_v1_field_set():
    """400 must cover every field the v1 seed (383 + 388) arbitrates — a field
    with portal rows but no operator row would make corrections to it lose."""
    def fields_of(name: str) -> set[str]:
        sql = (MIGRATIONS / name).read_text()
        blocks = re.findall(
            r"insert into location_field_policy.*?unnest\(array\[(.*?)\]", sql, re.S
        )
        out: set[str] = set()
        for block in blocks:
            out |= set(re.findall(r"'([a-z_]+)'", block))
        return out

    v1_fields = fields_of("383_location_w1_resolutions.sql") | fields_of(
        "388_location_w1_projection_quality_columns.sql"
    )
    operator_fields = fields_of("400_location_w1v_operator_field_policy.sql")
    missing = v1_fields - operator_fields
    assert not missing, f"v1 fields without an operator row: {missing}"


def test_allowed_types_are_typed_column_claims():
    """Text corrections only in W1v — geometry claims need their own input
    surface; `coordinate` must stay out until that exists."""
    assert "coordinate" not in oc.ALLOWED_CLAIM_TYPES
    assert oc.ALLOWED_CLAIM_TYPES <= {
        "address_point_id", "street_name", "house_number_cp", "house_number_co",
        "psc", "obec_name", "cast_obce_name", "okres_name",
    }
