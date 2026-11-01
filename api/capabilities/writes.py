"""The writes a run may propose and only its user may apply (ADR-0004), each over the module its route writes through.

A write names no collection, tag or stage: a proposal may only write into what the run itself creates, so there is no id here
to point at someone else's row, and an unknown key is refused (`Args`). A new collection therefore holds its members in the one
proposal, and "add to a collection" is that proposal's `property_ids`, never a write of its own.

Undo removes exactly what Apply added. The Apply's stamp tells the two apart: its rows carry `applied_at`, `now()` being the
transaction's start (migration 712), and a merge moves a row without changing that. A `draw` is built from our own data; the
model's words stay in `agent_text`, marked as its."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Self
from zoneinfo import ZoneInfo

import psycopg
from fastapi import HTTPException
from pydantic import AwareDatetime, Field, model_validator

from api import curation, dismissals, pipeline
from api import notifications as nf
from api import schemas as s
from api.capabilities.entry import Context, Effect, Tenancy, UndoCheck, WriteEntry
from api.filter_args import Args, Chip, MapAreaArgs, agenda_ids, filter_model
from toolkit import filter_registry as fr
from toolkit.property_identity import resolve_active_property_id, resolve_active_property_ids

NEW_COLLECTION_MAX = 200
_PRAGUE = ZoneInfo("Europe/Prague")
_LOCATION = "location"
_PLACES_SQL = (
    "SELECT level, code, name FROM ruian_place_names_public "
    "WHERE (level, code) IN (SELECT * FROM unnest(%s::text[], %s::bigint[]))"
)
_TITLES_SQL = (
    "SELECT property_id, disposition, subtype, area_m2, price_czk, category_type, display_label "
    "FROM properties_public WHERE property_id = ANY(%s)"
)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


def _account(ctx: Context) -> Any:
    if ctx.account_id is None:
        raise RuntimeError("a write's Context names the run's account")
    return ctx.account_id


def _drawn(*, title: str, before: list[str], after: list[str], agent_text: Iterable[tuple[str, str | None]] = (),
           properties: list[dict[str, Any]] | None = None, filters: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"title": title, "before": before, "after": after, "properties": properties or [], "filters": filters or [],
            "agent_text": [{"label": label, "text": text} for label, text in agent_text if text]}


def _money(price: int, category_type: str | None) -> str:
    return f"{price:,} Kč".replace(",", " ") + (" a month" if category_type == "pronajem" else "")


def _titled(conn: psycopg.Connection, property_ids: list[int]) -> list[dict[str, Any]]:
    """Each property as our own data names it, a merged-away id as its survivor; one we no longer hold says so."""
    survivors = resolve_active_property_ids(conn, property_ids)
    with conn.cursor() as cur:
        cur.execute(_TITLES_SQL, (list(survivors.values()),))
        rows = {int(r[0]): r[1:] for r in cur.fetchall()}
    out: dict[int, dict[str, Any]] = {}
    for asked in property_ids:
        pid = survivors.get(asked)
        if pid is None or pid not in rows:
            out[asked] = {"property_id": None, "title": f"Property {asked}: we no longer hold it"}
            continue
        disposition, subtype, area, price, category_type, label = rows[pid]
        parts = (disposition or subtype, f"{float(area):g} m²" if area is not None else None, label,
                 _money(price, category_type) if price is not None else None)
        out[pid] = {"property_id": pid, "title": " · ".join(p for p in parts if p) or f"Property {pid}"}
    return list(out.values())


# --- a new collection, holding its members -------------------------------------------------------------------------

class NewCollectionArgs(Args):
    name: str = Field(min_length=1, max_length=200, description="The collection's name, as the user will read it.")
    description: str | None = Field(None, max_length=2000, description="What the collection is for. Null = none.")
    property_ids: list[int] = Field(
        min_length=1, max_length=NEW_COLLECTION_MAX, description="The properties it holds, each one a tool returned.")


def _draw_new_collection(ctx: Context, args: NewCollectionArgs) -> dict[str, Any]:
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT 1 FROM collections WHERE account_id = %s AND lower(name) = lower(%s)", (_account(ctx), args.name))
        clash = cur.fetchone() is not None
    properties = _titled(ctx.conn, args.property_ids)
    before = (["You already have a collection with this name, so Apply is refused until one of them is renamed."]
              if clash else ["No such collection."])
    return _drawn(title="New collection", before=before, after=[f"A new collection holding {_plural(len(properties), 'property', 'properties')}."],
                  agent_text=(("Name", args.name), ("Description", args.description)), properties=properties)


def _new_collection(ctx: Context, args: NewCollectionArgs) -> dict[str, Any]:
    made = curation.create_collection(ctx.conn, s.CreateCollectionIn(name=args.name, description=args.description),
                                      account_id=str(_account(ctx)))
    added = curation.add_properties_to_collection(ctx.conn, made["id"], s.AddPropertiesToCollectionIn(property_ids=args.property_ids))
    return {"data": {"collection_id": made["id"], "added_ids": added["added_ids"],
                     "answer": f"Created the collection with {added['added']} of the properties."},
            "undo": {"collection_id": made["id"], "name": args.name, "description": args.description}}


def _undo_check_new_collection(ctx: Context, record: Mapping[str, Any], applied_at: datetime) -> UndoCheck:
    plan = curation.new_collection_undo_plan(ctx.conn, record, applied_at)
    if plan is None:
        return UndoCheck(False, "You already deleted this collection.")
    refusal, removable, stays = plan
    if refusal is not None:
        return UndoCheck(False, refusal)
    if stays:
        return UndoCheck(True, f"Removes the {len(removable)} the agent added and keeps the collection: it also holds {stays} of yours.")
    return UndoCheck(True, "Deletes the collection.")


def _undo_new_collection(ctx: Context, record: Mapping[str, Any], applied_at: datetime) -> str:
    done = curation.undo_new_collection(ctx.conn, record, applied_at)
    return ("Removed the agent's properties; the collection stays, with yours in it." if done["kept_collection"]
            else "Deleted the collection.")


# --- a note or a viewing -------------------------------------------------------------------------------------------

class NoteArgs(Args):
    property_id: int = Field(description="The property the note is on, one a tool returned.")
    body: str = Field("", max_length=4000, description="The note's text; empty only for a viewing with a date.")
    viewing_at: AwareDatetime | None = Field(
        None, description="A viewing is a note with a date: when the user sees the property in person, with its UTC offset. "
        "Null = a plain note.")

    @model_validator(mode="after")
    def _text_or_viewing(self) -> Self:
        if not self.body and self.viewing_at is None:
            raise ValueError("a note needs text or a viewing date")
        return self


def _when(at: datetime) -> str:
    return at.astimezone(_PRAGUE).strftime("%-d %b %Y, %H:%M")


def _draw_note(ctx: Context, args: NoteArgs) -> dict[str, Any]:
    pid = resolve_active_property_id(ctx.conn, args.property_id)
    count = len(curation.notes_of(ctx.conn, [pid])) if pid is not None else 0
    title = "New viewing" if args.viewing_at else "New note"
    after = f"{_plural(count + 1, 'note')} on it, the new one private to you."
    if args.viewing_at:
        after += f" A viewing on {_when(args.viewing_at)}, Prague time."
    return _drawn(title=title, before=[f"{_plural(count, 'note')} you can read on this property."], after=[after],
                  agent_text=(("Note", args.body),), properties=_titled(ctx.conn, [args.property_id]))


def _add_note(ctx: Context, args: NoteArgs) -> dict[str, Any]:
    note = curation.create_note(ctx.conn, args.property_id, s.CreateNoteIn(body=args.body, viewing_at=args.viewing_at),
                                str(_account(ctx)))
    return {"data": {"note_id": note["id"], "answer": "Added the viewing." if args.viewing_at else "Added the note."},
            "undo": {"note_id": note["id"]}}


def _undo_check_note(ctx: Context, record: Mapping[str, Any], applied_at: datetime) -> UndoCheck:
    refusal = curation.added_note_undo_refusal(ctx.conn, record["note_id"], applied_at)
    return UndoCheck(False, refusal) if refusal else UndoCheck(True, "Deletes the note.")


def _undo_note(ctx: Context, record: Mapping[str, Any], applied_at: datetime) -> str:
    curation.undo_added_note(ctx.conn, record["note_id"], applied_at)
    return "Deleted the note."


# --- a pipeline card at the entry stage ----------------------------------------------------------------------------

class CardArgs(Args):
    property_id: int = Field(description="The property to put on the user's board at its entry stage, one a tool returned.")


def _draw_card(ctx: Context, args: CardArgs) -> dict[str, Any]:
    account = _account(ctx)
    pid = resolve_active_property_id(ctx.conn, args.property_id)
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT label FROM pipeline_stages WHERE is_entry AND account_id = %s LIMIT 1", (account,))
        entry = cur.fetchone()
        cur.execute("SELECT ps.label FROM property_pipeline pp JOIN pipeline_stages ps ON ps.id = pp.stage_id "
                    "WHERE pp.property_id = %s AND pp.account_id = %s", (pid, account))
        held = cur.fetchone()
    properties = _titled(ctx.conn, [args.property_id])
    if held:
        return _drawn(title="Pipeline card", before=[f"On your board, at {held[0]}."],
                      after=["No change: Apply writes nothing while it is on your board."], properties=properties)
    after = [f"On your board, at {entry[0] if entry else 'the entry stage'}."]
    before = ["Not on your board."]
    if pid is not None and dismissals.is_dismissed(ctx.conn, pid, account_id=account):
        before.append("You dismissed this property.")
        after.append("Back from your dismissed properties, since a live deal is never dismissed.")
    return _drawn(title="Pipeline card", before=before, after=after, properties=properties)


def _add_card(ctx: Context, args: CardArgs) -> dict[str, Any]:
    account = _account(ctx)
    pid = resolve_active_property_id(ctx.conn, args.property_id)
    dismissed = pid is not None and dismissals.is_dismissed(ctx.conn, pid, account_id=account)
    card = pipeline.add_card(ctx.conn, s.AddPipelineCardIn(property_id=args.property_id), account_id=account)
    answer = (f"Added to your board at {card['stage_label']}." if card["added"]
              else f"Already on your board at {card['stage_label']}; nothing changed.")
    return {"data": {"property_id": card["property_id"], "added": card["added"], "answer": answer},
            "undo": {"property_id": card["property_id"], "added": card["added"], "dismissed": dismissed}}


def _undo_check_card(ctx: Context, record: Mapping[str, Any], applied_at: datetime) -> UndoCheck:
    if not record["added"]:
        return UndoCheck(False, "Nothing to undo: Apply changed nothing, and the card there is yours.")
    refusal = pipeline.added_card_undo_refusal(ctx.conn, record["property_id"], applied_at, account_id=_account(ctx))
    if refusal:
        return UndoCheck(False, refusal)
    return UndoCheck(True, "Takes this card off your board." + (" The property goes back to your dismissed ones." if record["dismissed"] else ""))


def _undo_card(ctx: Context, record: Mapping[str, Any], applied_at: datetime) -> str:
    if not record["added"]:
        raise HTTPException(409, "Nothing to undo: Apply changed nothing, and the card there is yours.")
    account = _account(ctx)
    pipeline.undo_added_card(ctx.conn, record["property_id"], applied_at, account_id=account)
    if record["dismissed"]:
        dismissals.dismiss(ctx.conn, s.DismissPropertyIn(property_id=record["property_id"]), account_id=account)
    return "Took the card off your board."


# --- a watchdog ----------------------------------------------------------------------------------------------------

_WatchdogFields = filter_model("_WatchdogFields", (i for i in agenda_ids(fr.Agenda.WATCHDOG) if i != _LOCATION), None,
                               required={"category_type"})


class WatchdogFilters(_WatchdogFields, MapAreaArgs):
    """What the watchdog alerts on: the Browse filters a watchdog takes, each stated by its registry id."""


def _spec(filters: WatchdogFilters, places: Mapping[tuple[str, int], str] | None = None) -> nf.WatchdogFilterSpec:
    """The stored spec. A chip's name is what the watchdog's page shows, so it comes from RÚIAN, never from the model."""
    values = filters.model_dump(by_alias=True)
    if places is not None and filters.districts:
        values["districts"] = [{**chip, "name": places[chip["level"], chip["id"]], "context": None}
                               for chip in values["districts"]]
    return nf.WatchdogFilterSpec.model_validate(values)


class WatchdogArgs(Args):
    name: str = Field(min_length=1, max_length=200, description="The watchdog's name, as the user will read it.")
    filters: WatchdogFilters = Field(description="What it alerts on; category_type is required (null = every deal type).")

    @model_validator(mode="after")
    def _renderable(self) -> Self:
        """Refused at submit, since a spec the matcher cannot render can never be applied (the module checks again)."""
        try:
            nf.check_filter_spec(_spec(self.filters))
        except nf.SPEC_REJECTIONS as exc:
            raise ValueError(f"filters: {exc}") from exc
        return self


def _places(conn: psycopg.Connection, chips: list[Chip] | None) -> dict[tuple[str, int], str]:
    if not chips:
        return {}
    with conn.cursor() as cur:
        cur.execute(_PLACES_SQL, ([c.level for c in chips], [c.id for c in chips]))
        return {(level, int(code)): name for level, code, name in cur.fetchall()}


def _enum_label(fid: str, value: Any) -> str:
    options = fr.REGISTRY[fid].enum_values or ()
    return next((o.label_en for o in options if o.value == value), str(value))


def _filter_value(fid: str, value: Any, category_type: str | None) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(_enum_label(fid, v) if not isinstance(v, dict) else f"{v['index_name']} {v['op']} {v['value']:g}" for v in value)
    f = fr.REGISTRY[fid]
    # Rule 23: a per-m² number never goes without its unit, which the watchdog's own deal type decides.
    per_month = {"pronajem": " a month", "prodej": "", None: ", a month on a rent"}
    return f"{_enum_label(fid, value)}{f' {f.unit}' if f.unit else ''}{per_month.get(category_type, '') if f.basis else ''}"


def _filter_lines(filters: WatchdogFilters, places: Mapping[tuple[str, int], str]) -> list[dict[str, str]]:
    """Each filter the spec sets, from the registry and RÚIAN: the words the watchdog will act on, not the model's."""
    lines: list[dict[str, str]] = []
    for chip in filters.districts or ():
        where = places.get((chip.level, chip.id), f"unknown {chip.level} {chip.id}: Apply is refused")
        lines.append({"label": "Not in" if chip.excluded else "In", "value": where})
    if filters.bounds:
        b = filters.bounds
        lines.append({"label": "Map box", "value": f"{b.south:g}, {b.west:g} to {b.north:g}, {b.east:g}"})
    if filters.circle:
        c = filters.circle
        lines.append({"label": "Map circle", "value": f"{c.radius_m:g} m around {c.lat:g}, {c.lng:g}"})
    if filters.category_type is None:
        lines.append({"label": "deal type", "value": "every deal type"})
    dumped = filters.model_dump(exclude={"districts", "bounds", "circle"})
    for fid, value in dumped.items():
        if value not in (None, [], type(filters).model_fields[fid].default):
            lines.append({"label": fid.replace("_", " "), "value": _filter_value(fid, value, filters.category_type)})
    return lines


def _draw_watchdog(ctx: Context, args: WatchdogArgs) -> dict[str, Any]:
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM notification_subscriptions WHERE account_id = %s AND retired_at IS NULL", (_account(ctx),))
        count = int(cur.fetchone()[0])
    return _drawn(title="New watchdog", before=[f"{_plural(count, 'watchdog')}."],
                  after=[f"{_plural(count + 1, 'watchdog')}. This one alerts only on properties that appear from now on, "
                         "never on what is already there."],
                  agent_text=(("Name", args.name),), filters=_filter_lines(args.filters, _places(ctx.conn, args.filters.districts)))


def _new_watchdog(ctx: Context, args: WatchdogArgs) -> dict[str, Any]:
    places = _places(ctx.conn, args.filters.districts)
    if unknown := [c.id for c in args.filters.districts or () if (c.level, c.id) not in places]:
        raise HTTPException(422, f"No such place in our register: {unknown}.")
    made = nf.create_subscription(ctx.conn, name=args.name, filter_spec=_spec(args.filters, places), account_id=_account(ctx))
    return {"data": {"subscription_id": made["id"], "answer": "Created the watchdog."}, "undo": {"subscription_id": made["id"]}}


def _undo_check_watchdog(ctx: Context, record: Mapping[str, Any], applied_at: datetime) -> UndoCheck:
    refusal, alerts = nf.new_subscription_undo_check(ctx.conn, record["subscription_id"], applied_at)
    if refusal:
        return UndoCheck(False, refusal)
    return UndoCheck(True, "Retires the watchdog." + (f" Its {_plural(alerts, 'alert')} leave your feed." if alerts else ""))


def _undo_watchdog(ctx: Context, record: Mapping[str, Any], applied_at: datetime) -> str:
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT 1 FROM notification_subscriptions WHERE id = %s FOR UPDATE", (record["subscription_id"],))
    refusal, _ = nf.new_subscription_undo_check(ctx.conn, record["subscription_id"], applied_at)
    if refusal:
        raise HTTPException(409, refusal)
    nf.delete_subscription(ctx.conn, record["subscription_id"])
    return "Retired the watchdog."


ENTRIES = (
    WriteEntry(
        name="create_collection", description="A new collection in the user's account holding these properties, in one "
        "step. There is no way to add to a collection that already exists.",
        args=NewCollectionArgs, handler=_new_collection, tenancy=Tenancy.TENANT, effect=Effect.WRITE,
        draw=_draw_new_collection, undo_check=_undo_check_new_collection, undo=_undo_new_collection,
    ),
    WriteEntry(
        name="add_note", description="A private note, or a viewing (a note with a date), on one property. The user reads "
        "the text before applying it.",
        args=NoteArgs, handler=_add_note, tenancy=Tenancy.TENANT, effect=Effect.WRITE,
        draw=_draw_note, undo_check=_undo_check_note, undo=_undo_note,
    ),
    WriteEntry(
        name="add_pipeline_card", description="One property on the user's deal board at its entry stage. A property already "
        "on the board stays as it is.",
        args=CardArgs, handler=_add_card, tenancy=Tenancy.TENANT, effect=Effect.WRITE,
        draw=_draw_card, undo_check=_undo_check_card, undo=_undo_card, undo_confirm=True,
    ),
    WriteEntry(
        name="create_watchdog", description="A watchdog that alerts the user on properties matching these filters as they "
        "appear from now on, never on those already there. The filters are the ones a watchdog takes on Browse.",
        args=WatchdogArgs, handler=_new_watchdog, tenancy=Tenancy.TENANT, effect=Effect.WRITE,
        draw=_draw_watchdog, undo_check=_undo_check_watchdog, undo=_undo_watchdog,
    ),
)
