"""The curated-city seed's offline refusal (#2454): an unknown CSV column refuses the run by name before it connects,
so nothing is written. Its database behaviour is on the DB lane (tests/test_w8_readers_on_mirror_live.py)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts import seed_curated_cities


def test_an_unknown_csv_column_refuses_the_run_by_name_before_it_connects(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    csv_file = tmp_path / "obce_v_datech_2026.csv"
    csv_file.write_text("Město,Kraj,Index exekucí,Index kaváren\nObcov,w8,3,9\n", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://seed@nowhere.invalid/none")

    def no_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("the seed connected")

    monkeypatch.setattr(seed_curated_cities.psycopg, "connect", no_connection)
    with caplog.at_level(logging.ERROR, logger="seed_curated_cities"):
        assert seed_curated_cities.main(["--csv", str(csv_file)]) == 2
    assert "Index kaváren" in caplog.text and "nothing written" in caplog.text
