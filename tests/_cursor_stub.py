"""A connection whose cursor records every statement and answers with the rows it was given, no database."""

from __future__ import annotations

from typing import Any


class _Cur:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows, self.seen = rows, []

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.seen.append((sql, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.rows)

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class _Conn:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.cur = _Cur(rows or [])

    def cursor(self, **kw: Any) -> _Cur:
        return self.cur
