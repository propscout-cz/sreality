"""A predicate-scripted fake psycopg connection for the hermetic curation tests.

Each statement is answered by the first (matcher, rows) whose matcher accepts its
whitespace-collapsed text; rows may be an exception instance, which the statement raises.
`rowcount` is len(rows).
The transaction records "commit" or "rollback" in `tx`, so a test can prove an error rolled back.
"""

from __future__ import annotations

from typing import Any, Callable

Script = list[tuple[Callable[[str], bool], "list[tuple[Any, ...]] | BaseException"]]


class ScriptTx:
    def __init__(self, conn: "ScriptConn") -> None:
        self._conn = conn

    def __enter__(self) -> "ScriptTx":
        return self

    def __exit__(self, exc_type: Any, *exc: Any) -> None:
        self._conn.tx.append("rollback" if exc_type else "commit")


class ScriptCur:
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

    def transaction(self) -> ScriptTx:
        return ScriptTx(self)

    def cursor(self) -> ScriptCur:
        return ScriptCur(self)
