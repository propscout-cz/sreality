"""The R2 key-collision repair re-queues rows the way the code defines "no bytes", and only through its backup.

The file restates three things the code owns: what invalidating a row's derived signals clears, what puts a row
back in the download queue, and which renditions only post-provenance uploads write. Each is read back here and
held against its owner, so a later change to one of them shows up as a failure here instead of drifting silently."""

from __future__ import annotations

import re
from pathlib import Path

from scraper import db, image_storage

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "670_repair_colliding_image_storage_paths.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.lstrip().startswith("--"))
BACKUP = "backup_670.images_r2_key_collision"


def _update() -> str:
    match = re.search(r"update public\.images i\s+set (.*?)\s+from " + re.escape(BACKUP), CODE, re.S)
    assert match is not None
    return match.group(1)


def _cleared() -> set[str]:
    return set(re.findall(r"(\w+)\s*=\s*(?:null|0)\b", _update()))


def test_clears_what_the_derived_signal_chokepoint_clears() -> None:
    chokepoint = set(re.findall(r"(\w+)\s*=\s*NULL", db._INVALIDATE_DERIVED_SIGNALS_SQL))
    assert chokepoint == {"phash", "clip_tagged_at"}
    assert chokepoint <= _cleared()


def test_a_cleared_row_satisfies_the_download_queue_predicate() -> None:
    assert {"storage_path", "unavailable_reason", "download_attempts"} <= _cleared()
    assert {"rendition", "stored_width", "stored_height", "last_error"} <= _cleared()


def test_the_clear_is_driven_by_the_backup_and_is_a_compare_and_set() -> None:
    assert CODE.index(f"insert into {BACKUP}") < CODE.index("update public.images i")
    assert "on conflict (image_id) do nothing" in CODE
    tail = CODE[CODE.index("update public.images i"):CODE.index("do $post$")]
    assert "where b.image_id = i.id" in tail and "and i.storage_path = b.storage_path" in tail


def test_measured_renditions_are_the_ones_only_post_provenance_uploads_write() -> None:
    measured = re.search(r"coalesce\(i\.rendition, ''\) in \(([^)]*)\) as measured", CODE)
    assert measured is not None
    assert set(re.findall(r"'([^']+)'", measured.group(1))) == {
        image_storage.RENDITION_SREALITY_MASTER,
        image_storage.RENDITION_NATIVE,
    }
    assert image_storage.RENDITION_SREALITY_LEGACY_CROP not in measured.group(1)


def test_the_backup_is_owner_only() -> None:
    assert "revoke all on schema backup_670 from anon, authenticated;" in CODE
    assert f"alter table {BACKUP} enable row level security;" in CODE
    assert "grant " not in CODE.lower()


def test_one_transaction_with_its_timeouts() -> None:
    assert CODE.count("begin;") == 1 and CODE.rstrip().endswith("commit;")
    assert "set local lock_timeout = '5s';" in CODE
    assert "set local statement_timeout = '120s';" in CODE


def test_the_header_says_when_to_apply() -> None:
    assert "APPLY AFTER DEPLOY; OPERATOR OK." in SQL.splitlines()[2]
