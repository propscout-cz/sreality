"""Broker outreach CRM (Phase 4) — campaign + message write layer.

Human-in-the-loop: the operator creates a campaign, the LLM drafts a personalised
message per targeted broker, the operator reviews / edits / approves and sends
MANUALLY (the UI offers mailto + copy). There is no automated email send in v1 —
`sent_via='manual'` records that the operator sent it themselves; the schema is
ready for an 'email' channel when a provider is wired.

GDPR (legitimate-interest B2B basis): suppressed brokers are never targeted, the
draft prompt requires an opt-out sentence, and every message records the contact
used + timestamps for auditability. Reads stay in toolkit.brokers; this module is
the WRITE path and lives in api/ (toolkit is read-only, rule #5).
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api import tenant_pool
from api.broker_board import BoardArgs, board
from api.location_filter import DistrictChip
from toolkit.brokers import BROKER_COUNTS_SQL, footprint

_DRAFT_MODEL_KEY = "outreach_draft_model"
_DRAFT_PROMPT_KEY = "outreach_draft_system_prompt"
_CALLED_FOR = "outreach_draft"

RECORD_OUTREACH_DRAFT_TOOL: dict[str, Any] = {
    "name": "record_outreach_draft",
    "description": "Record the drafted outreach email. Call exactly once with both fields.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "subject": {"type": "string", "description": "Email subject, Czech, max 80 chars."},
            "body": {"type": "string", "description": "Email body, Czech, max ~140 words, includes an opt-out sentence."},
        },
        "required": ["subject", "body"],
    },
}


# --- Campaigns -------------------------------------------------------------

class CampaignTarget(BaseModel):
    """The cohort `select_targets` ranks over: its place, category and deal type, live properties. Unknown keys are
    refused, so a stale client's (or a stored row's) `region_ids` or count `metric` is an error, never a campaign
    silently aimed at the whole country or another status."""

    model_config = ConfigDict(extra="forbid")

    districts: list[DistrictChip] = Field(default_factory=list)
    category_main: str | None = None
    category_type: str | None = None


class StaleTarget(ValueError):
    """A stored target that is not a chip target (migration 609 rewrote them)."""


def create_campaign(conn: Any, *, name: str, goal: str | None = None,
                    guidance: str | None = None, target: dict[str, Any] | None = None,
                    created_by: str | None = None) -> dict[str, Any]:
    from psycopg.types.json import Jsonb
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO outreach_campaigns (name, goal, guidance, target, created_by) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING *",
            (name, goal, guidance, Jsonb(target or {}), created_by))
        conn.commit()
        return _iso_campaign(cur.fetchone())


def list_campaigns(conn: Any) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT c.*, "
            "  coalesce(m.total, 0) AS message_count, "
            "  coalesce(m.sent, 0) AS sent_count, "
            "  coalesce(m.approved, 0) AS approved_count, "
            "  coalesce(m.draft, 0) AS draft_count "
            "FROM outreach_campaigns c "
            "LEFT JOIN ("
            "  SELECT campaign_id, count(*) AS total, "
            "    count(*) FILTER (WHERE status='sent') AS sent, "
            "    count(*) FILTER (WHERE status='approved') AS approved, "
            "    count(*) FILTER (WHERE status='draft') AS draft "
            "  FROM outreach_messages GROUP BY campaign_id"
            ") m ON m.campaign_id = c.id "
            "ORDER BY c.created_at DESC")
        return [_iso_campaign(r) for r in cur.fetchall()]


def get_campaign(conn: Any, campaign_id: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM outreach_campaigns WHERE id = %s", (campaign_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute(
            "SELECT status, count(*) AS n FROM outreach_messages "
            "WHERE campaign_id = %s GROUP BY status", (campaign_id,))
        stats = {r["status"]: r["n"] for r in cur.fetchall()}
    out = _iso_campaign(row)
    out["message_stats"] = stats
    return out


def update_campaign(conn: Any, campaign_id: int, *, name: str | None = None,
                    goal: str | None = None, guidance: str | None = None,
                    status: str | None = None, target: dict[str, Any] | None = None
                    ) -> dict[str, Any] | None:
    from psycopg.types.json import Jsonb
    sets, params = ["updated_at = now()"], {"id": campaign_id}
    for col, val in (("name", name), ("goal", goal), ("guidance", guidance), ("status", status)):
        if val is not None:
            sets.append(f"{col} = %({col})s")
            params[col] = val
    if target is not None:
        sets.append("target = %(target)s")
        params["target"] = Jsonb(target)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"UPDATE outreach_campaigns SET {', '.join(sets)} WHERE id = %(id)s RETURNING *", params)
        row = cur.fetchone()
        conn.commit()
    return _iso_campaign(row) if row else None


# --- Targeting + drafting --------------------------------------------------

# The board's top brokers `select_targets`' exclusions run over before its cut: past the wire's cap.
CANDIDATES = 2000


def select_targets(conn: Any, target: dict[str, Any], *, campaign_id: int | None = None,
                   limit: int = 25) -> list[dict[str, Any]]:
    """The broker board's top brokers by live properties in the campaign's cohort (its place, category and deal type)
    that are reachable (have an email), not suppressed, and not already drafted for this campaign."""
    try:
        spec = CampaignTarget.model_validate(target)
        args = BoardArgs(
            districts=[c.model_dump() for c in spec.districts], category_type=spec.category_type,
            category_main_in=[spec.category_main] if spec.category_main else None, status="active",
        ).model_copy(update={"limit": CANDIDATES})
    except ValidationError as exc:
        raise StaleTarget("this campaign's target predates the location chips (migration 609), names no registry "
                          "category or still counts by a metric; pick it again and save it") from exc
    limit = max(1, min(int(limit), 500))
    rows = board(conn, args, contacts=True)["data"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT broker_id FROM broker_outreach_suppression WHERE broker_id = ANY(%(ids)s::bigint[]) "
            "UNION SELECT broker_id FROM outreach_messages "
            "WHERE campaign_id = %(campaign)s::bigint AND broker_id = ANY(%(ids)s::bigint[])",
            {"ids": [r["broker_id"] for r in rows], "campaign": campaign_id})
        excluded = {r[0] for r in cur.fetchall()}
    return [r for r in rows if r["primary_email"] and r["broker_id"] not in excluded][:limit]


def generate_drafts(conn: Any, llm_client: Any, campaign_id: int, *, limit: int = 25) -> dict[str, Any] | None:
    campaign = get_campaign(conn, campaign_id)
    if campaign is None:
        return None
    targets = select_targets(conn, campaign.get("target") or {}, campaign_id=campaign_id, limit=limit)
    if not targets:
        return {"generated": 0, "targets": 0}
    system = llm_client.resolve_system_prompt(_DRAFT_PROMPT_KEY)
    model = llm_client.resolve_model(_DRAFT_MODEL_KEY)
    generated = 0
    for t in targets:
        # The draft quotes the broker's own counts, as a regenerated one does: a board row carries the cohort's.
        ctx = _broker_context(conn, t["broker_id"])
        if ctx is None:
            continue
        resp = llm_client.call(
            called_for=_CALLED_FOR,
            messages=[{"role": "user", "content": _build_payload(campaign, ctx)}],
            system=system, tools=[RECORD_OUTREACH_DRAFT_TOOL], model=model)
        draft = _extract_tool(resp.tool_calls, "record_outreach_draft")
        if not draft:
            continue
        _upsert_message(conn, campaign_id, t, draft, resp)
        generated += 1
    conn.commit()
    return {"generated": generated, "targets": len(targets)}


def regenerate_message(conn: Any, llm_client: Any, message_id: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT m.*, c.goal, c.guidance FROM outreach_messages m "
            "JOIN outreach_campaigns c ON c.id = m.campaign_id WHERE m.id = %s", (message_id,))
        msg = cur.fetchone()
    if msg is None:
        return None
    if msg["status"] not in ("draft", "approved"):
        return _iso_message(msg)  # don't clobber sent/replied
    ctx = _broker_context(conn, msg["broker_id"])
    if ctx is None:
        return None
    system = llm_client.resolve_system_prompt(_DRAFT_PROMPT_KEY)
    model = llm_client.resolve_model(_DRAFT_MODEL_KEY)
    resp = llm_client.call(
        called_for=_CALLED_FOR,
        messages=[{"role": "user", "content": _build_payload({"goal": msg["goal"], "guidance": msg["guidance"]}, ctx)}],
        system=system, tools=[RECORD_OUTREACH_DRAFT_TOOL], model=model)
    draft = _extract_tool(resp.tool_calls, "record_outreach_draft")
    if not draft:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "UPDATE outreach_messages SET subject=%s, body=%s, status='draft', "
            "llm_call_id=%s, model=%s, cost_usd=%s, generated_at=now(), approved_at=NULL "
            "WHERE id=%s RETURNING *",
            (draft["subject"], draft["body"], resp.llm_call_id, resp.model, resp.cost_usd, message_id))
        row = cur.fetchone()
        conn.commit()
    return _iso_message(row)


# --- Messages --------------------------------------------------------------

def list_messages(conn: Any, campaign_id: int, *, status: str | None = None) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT m.*, b.display_name AS broker_name, f.display_name AS firm_name "
            "FROM outreach_messages m "
            "JOIN brokers b ON b.id = m.broker_id "
            "LEFT JOIN firms f ON f.id = b.primary_firm_id "
            "WHERE m.campaign_id = %s AND (%s::text IS NULL OR m.status = %s::text) "
            "ORDER BY m.status, m.generated_at DESC",
            (campaign_id, status, status))
        return [_iso_message(r) for r in cur.fetchall()]


def update_message(conn: Any, message_id: int, *, status: str | None = None,
                   subject: str | None = None, body: str | None = None,
                   notes: str | None = None) -> dict[str, Any] | None:
    sets, params = [], {"id": message_id}
    if subject is not None:
        sets.append("subject = %(subject)s"); params["subject"] = subject
    if body is not None:
        sets.append("body = %(body)s"); params["body"] = body
    if notes is not None:
        sets.append("notes = %(notes)s"); params["notes"] = notes
    if status is not None:
        sets.append("status = %(status)s"); params["status"] = status
        if status == "approved":
            sets.append("approved_at = now()")
        elif status == "sent":
            sets.append("sent_at = now()")
            sets.append("sent_via = coalesce(sent_via, 'manual')")
    if not sets:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"UPDATE outreach_messages SET {', '.join(sets)} WHERE id = %(id)s RETURNING *", params)
        row = cur.fetchone()
        conn.commit()
    return _iso_message(row) if row else None


# --- Suppression -----------------------------------------------------------

def suppress_broker(conn: Any, broker_id: int, *, reason: str | None = None,
                    created_by: str | None = None) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO broker_outreach_suppression (broker_id, reason, created_by) "
            "VALUES (%s, %s, %s) ON CONFLICT (broker_id) DO UPDATE SET "
            "reason = EXCLUDED.reason, suppressed_at = now(), created_by = EXCLUDED.created_by "
            "RETURNING *",
            (broker_id, reason, created_by))
        row = cur.fetchone()
        conn.commit()
    row["suppressed_at"] = _iso(row.get("suppressed_at"))
    return row


def unsuppress_broker(conn: Any, broker_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM broker_outreach_suppression WHERE broker_id = %s", (broker_id,))
        deleted = cur.rowcount
        conn.commit()
    return bool(deleted)


def list_suppressions(conn: Any) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT s.broker_id, s.reason, s.suppressed_at, b.display_name AS broker_name "
            "FROM broker_outreach_suppression s "
            "JOIN brokers b ON b.id = s.broker_id ORDER BY s.suppressed_at DESC")
        rows = cur.fetchall()
    for r in rows:
        r["suppressed_at"] = _iso(r.get("suppressed_at"))
    return rows


# --- Internals -------------------------------------------------------------

def _broker_context(conn: Any, broker_id: int) -> dict[str, Any] | None:
    """The facts a draft quotes: the broker's own numbers, the ones every broker surface shows
    (toolkit.brokers.BROKER_COUNTS_SQL), as one bounded read; None for an unknown or merged-away broker."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        tenant_pool.bound_read(cur)
        cur.execute(
            "SELECT p.display_name, p.primary_email, p.primary_phone, p.firm_name, p.firm_domain, n.* "
            f"FROM brokers_public p CROSS JOIN {BROKER_COUNTS_SQL} WHERE p.broker_id = %(broker_id)s",
            {"broker_id": broker_id})
        leader = cur.fetchone()
        if leader is None:
            return None
        regions, categories = footprint(cur, broker_id)
    return {
        "broker_id": broker_id,
        "name": leader.get("display_name"),
        "firm": leader.get("firm_name") or leader.get("firm_domain"),
        "email": leader.get("primary_email"),
        "phone": leader.get("primary_phone"),
        "active_properties": leader.get("active_properties"),
        "regions": [r["name"] for r in regions[:3] if r["name"]],
        "categories": [f"{c['category_main']}/{c['category_type']}" for c in categories[:3]
                       if c["category_main"] and c["category_type"]],
    }


def _build_payload(campaign: dict[str, Any], ctx: dict[str, Any]) -> str:
    lines = [
        "OUTREACH GOAL:",
        (campaign.get("goal") or "Navázat kontakt a zjistit, zda makléř nabízí off-market nemovitosti.").strip(),
    ]
    if campaign.get("guidance"):
        lines += ["", "OPERATOR GUIDANCE:", campaign["guidance"].strip()]
    lines += [
        "", "BROKER FACTS (use only these; do not invent):",
        f"- Name: {ctx.get('name') or 'neznámé'}",
        f"- Firm: {ctx.get('firm') or 'neuvedeno'}",
    ]
    if ctx.get("active_properties") is not None:
        lines.append(f"- Active properties tracked: {ctx['active_properties']}")
    if ctx.get("regions"):
        lines.append(f"- Most active regions: {', '.join(ctx['regions'])}")
    if ctx.get("categories"):
        lines.append(f"- Typical inventory (category_main/type): {', '.join(ctx['categories'])}")
    return "\n".join(lines)


def _upsert_message(conn: Any, campaign_id: int, leader: dict[str, Any],
                    draft: dict[str, Any], resp: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO outreach_messages (campaign_id, broker_id, to_email, to_phone, "
            "  subject, body, llm_call_id, model, cost_usd) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (campaign_id, broker_id) DO UPDATE SET "
            "  subject=EXCLUDED.subject, body=EXCLUDED.body, llm_call_id=EXCLUDED.llm_call_id, "
            "  model=EXCLUDED.model, cost_usd=EXCLUDED.cost_usd, generated_at=now() "
            "  WHERE outreach_messages.status = 'draft'",
            (campaign_id, leader["broker_id"], leader.get("primary_email"),
             leader.get("primary_phone"), draft["subject"], draft["body"],
             resp.llm_call_id, resp.model, resp.cost_usd))


def _extract_tool(tool_calls: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for tc in tool_calls or []:
        if tc.get("name") == name:
            return tc.get("input")
    return None


def _iso(v: Any) -> Any:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


def _iso_campaign(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    for k in ("created_at", "updated_at"):
        if k in row:
            row[k] = _iso(row[k])
    return row


def _iso_message(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    for k in ("generated_at", "approved_at", "sent_at"):
        if k in row:
            row[k] = _iso(row[k])
    if "cost_usd" in row and row["cost_usd"] is not None:
        row["cost_usd"] = float(row["cost_usd"])
    return row
