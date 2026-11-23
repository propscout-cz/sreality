"""The estimator's skills, one row each in the `skills` table (migration 029), until M2 retires the table (#2078).

The live text is the row: edits go through `update_skill` (Settings page -> PUT /admin/skills/{name}) and the
`skills_history` trigger keeps every prior version. The old `skills/` seed copies are legacy reference under
docs/design/agent-platform/legacy-skills/. `update_skill` runs the same checks as an agent folder
(`api.agents.definition`): tools named in the estimator's own `AGENT_TOOLS`, a priced model per named provider, and
limits within bounds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from api import dependencies
from api.agent import AGENT_TOOLS
from api.agents import definition
from api.agents.definition import Limits

if TYPE_CHECKING:
    import psycopg


class SkillNotFound(LookupError):
    """Raised by load_skill when the named row does not exist."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    system_prompt: str
    allowed_tools: list[str]
    preferred_model: dict[str, str]
    limits: Limits
    updated_at: str | None = None
    archived_at: str | None = None
    version: int = 1


def load_skill(conn: "psycopg.Connection", name: str) -> Skill:
    """Load one skill by name. Archived skills load fine — past
    estimations referencing them must stay readable. The
    new-estimation flow gates archived skills out at the schema /
    list-skills boundary instead."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, description, system_prompt, allowed_tools, "
            "preferred_model, limits, updated_at, archived_at, version "
            "FROM skills WHERE name = %s",
            (name,),
        )
        row = cur.fetchone()
    if row is None:
        raise SkillNotFound(f"skill {name!r} not found")
    return _row_to_skill(row)


def list_skills(
    conn: "psycopg.Connection", *, include_archived: bool = False,
) -> list[Skill]:
    """List skills. Archived skills are hidden by default — the
    operator's Settings page and any future picker should pass
    `include_archived=True` only when they explicitly want the full
    history."""
    base = (
        "SELECT name, description, system_prompt, allowed_tools, "
        "preferred_model, limits, updated_at, archived_at, version "
        "FROM skills"
    )
    where = "" if include_archived else " WHERE archived_at IS NULL"
    with conn.cursor() as cur:
        cur.execute(base + where + " ORDER BY name")
        rows = cur.fetchall()
    return [_row_to_skill(r) for r in rows]


def update_skill(
    conn: "psycopg.Connection",
    name: str,
    fields: dict[str, Any],
    *,
    updated_by: str | None = None,
) -> Skill:
    """Partial-update a skill row. Raises SkillNotFound if missing."""
    if not fields:
        return load_skill(conn, name)

    sets: list[str] = []
    params: dict[str, Any] = {"name": name}

    if "description" in fields:
        sets.append("description = %(description)s")
        params["description"] = definition.text(fields["description"], "description")
    if "system_prompt" in fields:
        sets.append("system_prompt = %(system_prompt)s")
        params["system_prompt"] = definition.text(fields["system_prompt"], "system_prompt")
    if "allowed_tools" in fields:
        tools = definition.tool_names(fields["allowed_tools"], AGENT_TOOLS)
        sets.append("allowed_tools = %(allowed_tools)s::jsonb")
        params["allowed_tools"] = _jsonb_dumps(tools)
    if "preferred_model" in fields:
        model_map = definition.models(fields["preferred_model"], dependencies.get_providers())
        sets.append("preferred_model = %(preferred_model)s::jsonb")
        params["preferred_model"] = _jsonb_dumps(model_map)
    if "limits" in fields:
        sets.append("limits = %(limits)s::jsonb")
        params["limits"] = _jsonb_dumps(asdict(definition.limits(fields["limits"])))
    if updated_by is not None:
        sets.append("updated_by = %(updated_by)s")
        params["updated_by"] = updated_by

    if not sets:
        return load_skill(conn, name)

    sql = (
        f"UPDATE skills SET {', '.join(sets)} "
        f"WHERE name = %(name)s RETURNING name"
    )
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if row is None:
        raise SkillNotFound(f"skill {name!r} not found")
    return load_skill(conn, name)


# --- helpers --------------------------------------------------------------

def _row_to_skill(row: tuple[Any, ...]) -> Skill:
    (
        name, description, system_prompt, allowed_tools,
        preferred_model, limits, updated_at, archived_at, version,
    ) = row
    return Skill(
        name=name,
        description=description,
        system_prompt=system_prompt,
        allowed_tools=list(allowed_tools or []),
        preferred_model=dict(preferred_model or {}),
        limits=Limits(
            max_iterations=int(limits.get("max_iterations", 12)),
            max_cost_usd=float(limits.get("max_cost_usd", 1.0)),
            wall_clock_timeout_s=float(limits.get("wall_clock_timeout_s", 120.0)),
        ),
        updated_at=str(updated_at) if updated_at is not None else None,
        archived_at=str(archived_at) if archived_at is not None else None,
        version=int(version) if version is not None else 1,
    )


def _jsonb_dumps(value: Any) -> str:
    import json
    return json.dumps(value)
