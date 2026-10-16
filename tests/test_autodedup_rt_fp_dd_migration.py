"""Shape gate for migration 604 — each advert's decision digest on `autodedup.rt_fp` (E960).

Offline, no DB. The column is what lets a pass answer a reject from the ledger (E943) without
reading the advert: it must stay inside schema `autodedup` (ruling D8), be added without a
rewrite (nullable, no default: a row written before it is read once and stamped by the lane),
and be the one column the lane's statements name.
"""

from __future__ import annotations

import re
from pathlib import Path

from autodedup import incremental_sql as S

_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION = _ROOT / "migrations" / "604_autodedup_rt_fp_decision_digest.sql"


def _code() -> str:
    body = "\n".join(line.split("--")[0]
                     for line in _MIGRATION.read_text(encoding="utf-8").lower().splitlines())
    return re.sub(r"\s+", " ", body)


def test_it_adds_one_nullable_column_without_a_rewrite() -> None:
    code = _code()
    assert re.findall(r"alter table (\S+)", code) == ["autodedup.rt_fp"]
    assert "add column if not exists dd text;" in code
    assert "default" not in code and "not null" not in code
    for verb in ("drop ", "delete from", "truncate", "update ", "create "):
        assert verb not in code, verb
    assert "set lock_timeout = '5s';" in code and "reset lock_timeout;" in code


def test_the_lane_writes_reads_and_checks_the_column_it_adds() -> None:
    assert "dd               = excluded.dd" in S.RT_FP_UPSERT_SQL
    assert "f.dd" in S.RT_FP_READ_SQL and "set dd = w.dd" in S.RT_FP_DD_SQL
    assert "attname = 'dd'" in S.RT_STORE_PRESENT_SQL
    assert "comment on column autodedup.rt_fp.dd is" in _MIGRATION.read_text().lower()
