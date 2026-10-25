"""The R2 key-collision repair re-queues rows the way the code defines "no bytes", and only through its backup.

The file restates three things the code owns: what invalidating a row's derived signals clears, what puts a row
back in the download queue, and which renditions only post-provenance uploads write. Each is read back here and
held against its owner, so a later change to one of them shows up as a failure here instead of drifting silently."""

from __future__ import annotations

import re
from pathlib import Path

from scraper import image_storage

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "670_repair_colliding_image_storage_paths.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.lstrip().startswith("--"))
BACKUP = "backup_670.images_r2_key_collision"


def _update() -> str:
    match = re.search(r"update public\.images i\s+set (.*?)\s+from picked p\b", CODE, re.S)
    assert match is not None
    return match.group(1)


def _cleared() -> set[str]:
    return set(re.findall(r"(\w+)\s*=\s*(?:null|0)\b", _update()))


def test_clears_the_two_producer_predicates() -> None:
    # phash and clip_tagged_at are the pHash and CLIP lanes' own selection predicates
    # (architecture rule 6): nulling them is what re-arms both after a row's bytes change.
    assert {"phash", "clip_tagged_at"} <= _cleared()


def test_a_cleared_row_satisfies_the_download_queue_predicate() -> None:
    assert {"storage_path", "unavailable_reason", "download_attempts"} <= _cleared()
    assert {"rendition", "stored_width", "stored_height", "last_error"} <= _cleared()


def _statement() -> str:
    start = CODE.index("with pair as (")
    return CODE[start:CODE.index(";", CODE.index("update public.images i"))]


def test_pick_backup_and_clear_are_one_statement() -> None:
    statement = _statement()
    assert CODE.count("update public.images i") == 1 and CODE.count(f"insert into {BACKUP}") == 1
    assert statement.index(f"insert into {BACKUP}") < statement.index("update public.images i")
    assert ";" not in statement


def test_every_cleared_row_is_in_the_backup_with_its_first_seen_values() -> None:
    statement = _statement()
    insert = statement[statement.index(f"insert into {BACKUP}"):statement.index("update public.images i")]
    assert "from picked p" in insert
    assert "on conflict (image_id) do nothing" in insert and "returning image_id" in insert
    tail = statement[statement.index("update public.images i"):]
    assert "p.id in (select image_id from backed_up)" in tail
    assert f"exists (select 1 from {BACKUP} b where b.image_id = p.id)" in tail


def test_the_clear_is_a_compare_and_set_on_the_key_this_run_read() -> None:
    tail = _statement()[_statement().index("update public.images i"):]
    assert "where p.id = i.id" in tail and "and i.storage_path = p.storage_path" in tail


def test_an_owner_is_re_queued_only_on_an_active_listing() -> None:
    picked = CODE[CODE.index("picked as ("):CODE.index("backed_up as (")]
    assert "r.rank_in_key > 1" in picked
    assert re.search(r"r\.measured and r\.is_active\s+and r\.last_download_attempt_at >= ", picked)


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
