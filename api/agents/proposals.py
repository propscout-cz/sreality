"""A run's proposals as the user meets them: drawn from our own data, applied once, undone (ADR-0004). The one place a write
entry runs, always in the caller's own tenant transaction, so the write and its marker commit or roll back as one.

Proposals are read only from `agent_runs.proposals`, which holds what the terminator accepted; the diary holds refused ones too.
The marker's writer is migration 712's definer functions: `authenticated` holds SELECT on the table, and agent_runs stays
read-only to it."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api.capabilities import registry
from api.capabilities.entry import Context, WriteEntry
from api.capabilities.errors import ToolError
from api.filter_args import Args

NOT_FOUND = "no such proposal"
_RUN = "SELECT account_id, proposals FROM agent_runs WHERE id = %s"
_MARKERS = ("SELECT proposal_index, applied_at, undone_at, effect FROM agent_proposal_applies WHERE run_id = %s "
            "AND (%s::integer IS NULL OR proposal_index = %s)")
_CLAIM = "SELECT agent_proposal_claim(%s, %s, %s)"
_SETTLE = "SELECT agent_proposal_settle(%s, %s, %s)"
_UNDO = "SELECT agent_proposal_undo(%s, %s)"


def _run(conn: psycopg.Connection, run_id: int) -> tuple[uuid.UUID, list[Any]]:
    """The account and proposals of a run this caller can read; any other id, a foreign one included, is a 404."""
    row = conn.execute(_RUN, (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such run")
    return row[0], row[1]


def _markers(conn: psycopg.Connection, run_id: int, index: int | None = None) -> dict[int, dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_MARKERS, (run_id, index, index))
        return {row["proposal_index"]: row for row in cur.fetchall()}


def _parsed(proposal: Mapping[str, Any]) -> tuple[WriteEntry, Args]:
    """Its entry and arguments, parsed as Apply will write them; a ToolError if today's schema no longer takes them."""
    entry, args = registry.validate(proposal["entry"], proposal["args"])
    if not isinstance(entry, WriteEntry):
        raise ToolError(registry.ToolErrorCode.REFUSED, f"{entry.name} is no write")
    return entry, args


def _view(ctx: Context, index: int, proposal: Mapping[str, Any], marker: Mapping[str, Any] | None) -> dict[str, Any]:
    base = {"index": index, "entry": proposal["entry"], "state": "open", "refused": None, "answer": None, "applied_at": None,
            "undone_at": None, "undo": None, "undo_confirm": False}
    try:
        entry, args = _parsed(proposal)
    except ToolError as exc:
        return {**base, "title": proposal["entry"], "before": [], "after": [], "agent_text": [], "properties": [],
                "filters": [], "refused": exc.message}
    base["undo_confirm"] = entry.undo_confirm
    if marker is None:
        return {**base, **entry.draw(ctx, args)}
    effect = marker["effect"]
    view = {**base, **effect["drawn"], "applied_at": marker["applied_at"], "undone_at": marker["undone_at"],
            "answer": effect["answer"], "state": "applied" if marker["undone_at"] is None else "undone"}
    if marker["undone_at"] is None:
        check = entry.undo_check(ctx, effect["undo"], marker["applied_at"])
        view["undo"] = {"available": check.available, "says": check.says}
    return view


def views(conn: psycopg.Connection, run_id: int) -> list[dict[str, Any]]:
    """Every proposal of a run, each as its state draws it."""
    account, proposals = _run(conn, run_id)
    markers = _markers(conn, run_id)
    ctx = Context(conn, account_id=account)
    return [_view(ctx, i, proposal, markers.get(i)) for i, proposal in enumerate(proposals)]


def _proposal(proposals: list[Any], index: int) -> Mapping[str, Any]:
    if not 0 <= index < len(proposals):
        raise HTTPException(404, NOT_FOUND)
    return proposals[index]


def apply(conn: psycopg.Connection, run_id: int, index: int) -> dict[str, Any]:
    """Write the proposal once: the claim is the compare-and-set, taken before the write, so a second Apply, however close,
    waits for the first's commit and then writes nothing. A refusal rolls the claim back with the transaction, and the
    proposal stays open to try again."""
    account, proposals = _run(conn, run_id)
    proposal = _proposal(proposals, index)
    try:
        entry, args = _parsed(proposal)
    except ToolError as exc:
        raise HTTPException(422, exc.message) from None
    ctx = Context(conn, account_id=account)
    if not conn.execute(_CLAIM, (run_id, index, entry.name)).fetchone()[0]:
        marker = _markers(conn, run_id, index)[index]
        if marker["undone_at"] is not None:
            raise HTTPException(409, "You undid this proposal; a proposal applies once.")
        return _view(ctx, index, proposal, marker)
    drawn = entry.draw(ctx, args)
    done = entry.handler(ctx, args)
    conn.execute(_SETTLE, (run_id, index, Jsonb({"undo": done["undo"], "answer": done["data"]["answer"], "drawn": drawn})))
    return _view(ctx, index, proposal, _markers(conn, run_id, index)[index])


def undo(conn: psycopg.Connection, run_id: int, index: int) -> dict[str, Any]:
    """Replay the recorded inverse once, marker first as in `apply`; a refusal (the user has since changed the thing) rolls the
    marker back, with the reason."""
    account, proposals = _run(conn, run_id)
    proposal = _proposal(proposals, index)
    marker = _markers(conn, run_id, index).get(index)
    if marker is None:
        raise HTTPException(409, "This proposal has not been applied.")
    ctx = Context(conn, account_id=account)
    if not conn.execute(_UNDO, (run_id, index)).fetchone()[0]:
        return _view(ctx, index, proposal, _markers(conn, run_id, index)[index])
    entry, _ = _parsed(proposal)
    entry.undo(ctx, marker["effect"]["undo"], marker["applied_at"])
    return _view(ctx, index, proposal, _markers(conn, run_id, index)[index])
