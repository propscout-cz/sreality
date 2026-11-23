"""The backup lane dumps exactly what a destructive migration names, and nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import backup_before_drop as bbd
from scripts.backup_before_drop import Target, _copy_rows, column_copy_sql, parse_targets


def test_a_target_is_a_table_or_a_key_plus_columns() -> None:
    assert parse_targets(["property_tags", "listings:id,mf_gross_yield_pct"]) == [
        Target("property_tags", ()),
        Target("listings", ("id", "mf_gross_yield_pct")),
    ]
    assert Target("property_tags", ()).filename == "property_tags.sql.gz"
    assert Target("listings", ("id", "x")).filename == "listings.csv.gz"


@pytest.mark.parametrize("spec", [
    "listings:id",                 # a key with nothing to back up
    "public.listings",             # the schema is fixed: public
    "listings:id,mf; drop table",  # anything outside the identifier charset
    "Listings:id,x",
    "listings:",
])
def test_a_malformed_target_is_refused(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_targets([spec])


def test_a_table_named_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        parse_targets(["listings:id,a", "listings:id,b"])


def test_the_column_dump_keeps_only_rows_that_hold_a_value() -> None:
    """An absent row restores as NULL, which is what it was, so only set rows are kept."""
    target = parse_targets(["properties:id,mf_reference_rent_czk,mf_reference_rent"])[0]
    assert column_copy_sql(target).as_string(None) == (
        'COPY (SELECT "id", "mf_reference_rent_czk", "mf_reference_rent" FROM public."properties"'
        ' WHERE "mf_reference_rent_czk" IS NOT NULL OR "mf_reference_rent" IS NOT NULL'
        ' ORDER BY "id") TO STDOUT WITH (FORMAT csv, HEADER)'
    )


def test_the_table_dump_row_count_is_read_out_of_the_dump() -> None:
    dump = b"SET x;\nCOPY public.t (a) FROM stdin;\n1\n2\n\\.\nCOPY public.u (b) FROM stdin;\n3\n\\.\n"
    assert _copy_rows(dump) == 3


class _R2:
    def __init__(self, size_back: int | None = None) -> None:
        self.files: dict[str, str] = {}
        self._size_back = size_back

    def upload_file(self, key: str, path: str, content_type: str = "application/zip") -> None:
        self.files[key] = path
        self.types = {**getattr(self, "types", {}), key: content_type}

    def object_size(self, key: str) -> int | None:
        return self._size_back if self._size_back is not None else Path(self.files[key]).stat().st_size


def test_a_made_file_is_streamed_to_r2_under_its_label_and_read_back(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The retired-claims backup is a multi-GB CSV made by psql: streamed from disk, never held in memory.
    backup = tmp_path / "retired_claims_42.csv.gz"
    backup.write_bytes(b"\x1f\x8b" + b"x" * 100)
    r2 = _R2()
    monkeypatch.setattr(bbd.R2Client, "from_env", classmethod(lambda cls: r2))
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_SESSION_URL", raising=False)
    assert bbd.main(["retired-claims", "--file", str(backup)]) == 0
    ((key, path),) = r2.files.items()
    assert key.startswith("backups/retired-claims/") and key.endswith("/retired_claims_42.csv.gz")
    assert path == str(backup)


def test_a_file_that_does_not_read_back_whole_fails_the_run(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backup = tmp_path / "retired_claims_42.csv.gz"
    backup.write_bytes(b"\x1f\x8b" + b"x" * 100)
    monkeypatch.setattr(bbd.R2Client, "from_env", classmethod(lambda cls: _R2(size_back=7)))
    assert bbd.main(["retired-claims", "--file", str(backup)]) == 1


def test_neither_a_target_nor_a_file_is_refused() -> None:
    with pytest.raises(SystemExit):
        bbd.main(["retired-claims"])


def test_an_archived_artifact_zip_keeps_its_zip_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # An Actions artifact is archived as the zip GitHub serves, structure and all.
    artifact = tmp_path / "autodedup-judge_123_456.zip"
    artifact.write_bytes(b"PK" + b"x" * 50)
    r2 = _R2()
    monkeypatch.setattr(bbd.R2Client, "from_env", classmethod(lambda cls: r2))
    assert bbd.main(["actions-artifacts", "--file", str(artifact)]) == 0
    ((key, kind),) = r2.types.items()
    assert key.endswith("/autodedup-judge_123_456.zip") and kind == "application/zip"
