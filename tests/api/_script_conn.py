"""A predicate-scripted fake psycopg connection for the hermetic curation tests.

Each statement is answered by the first (matcher, rows) whose matcher accepts its
whitespace-collapsed text; rows may be an exception instance, which the statement raises.
`rowcount` is len(rows).
The transaction records "commit" or "rollback" in `tx`, so a test can prove an error rolled back;
`info.transaction_status` reads as psycopg's does: a statement that raised inside it leaves it INERROR.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

from psycopg.pq import TransactionStatus

Script = list[tuple[Callable[[str], bool], "list[tuple[Any, ...]] | BaseException"]]


class ScriptTx:
    def __init__(self, conn: "ScriptConn") -> None:
        self._conn = conn

    def __enter__(self) -> "ScriptTx":
        self._conn.depth += 1
        self._conn.info.transaction_status = TransactionStatus.INTRANS
        return self

    def __exit__(self, exc_type: Any, *exc: Any) -> None:
        self._conn.tx.append("rollback" if exc_type else "commit")
        self._conn.depth -= 1
        self._conn.info.transaction_status = TransactionStatus.INTRANS if self._conn.depth else TransactionStatus.IDLE


class ScriptCur:
    description = None  # no column names: a reader that maps rows by name sees none

    def __init__(self, conn: "ScriptConn") -> None:
        self._conn, self._rows, self.rowcount = conn, [], 0

    def __enter__(self) -> "ScriptCur":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        text = " ".join(sql.split())
        self._conn.executed.append((text, params))
        for matches, rows in self._conn.script:
            if matches(text):
                if isinstance(rows, BaseException):
                    if self._conn.depth:
                        self._conn.info.transaction_status = TransactionStatus.INERROR
                    raise rows
                self._rows, self.rowcount = list(rows), len(rows)
                return
        self._rows, self.rowcount = [], 0

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class ScriptConn:
    def __init__(self, script: Script) -> None:
        self.script = script
        self.executed: list[tuple[str, Any]] = []
        self.tx: list[str] = []
        self.depth = 0
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def transaction(self) -> ScriptTx:
        return ScriptTx(self)

    def cursor(self, row_factory: Any = None) -> ScriptCur:
        return ScriptCur(self)
