"""Valuo valuations: Valuo's vocabulary (FIELDS, the only home of its codes and Czech labels), how a
property becomes a Valuo request with the provenance of every value, and the store — one
`valuo_valuations` row per paid call, INSERTed `pending` before it and UPDATEd once after (rule 12)."""

from __future__ import annotations

import contextlib
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api import valuo_client
from api.routes.property_location import load_location
from toolkit.filter_registry import DISPOSITION_OPTIONS, SUBTYPE_OPTIONS, label_cs, type_label_cs
from toolkit.measures import ppm2_basis

LOG = logging.getLogger(__name__)

FLAT, HOUSE, LAND = "flat", "house", "land"
ALL = (FLAT, HOUSE, LAND)
BUILT = (FLAT, HOUSE)

# Where a value came from. `ignored` is not stored: it is read off Valuo's own `ignored_ids`.
AD, PROPERTY, INFERRED, ASSUMED, MISSING, USER = "ad", "property", "inferred", "assumed", "missing", "user"

_PRAGUE = ZoneInfo("Europe/Prague")
_HISTORY_LIMIT = 50
_STALE_PENDING = "2 minutes"


@dataclass(frozen=True)
class Field:
    """One Valuo parameter. `required`/`recommended` name the property types Valuo's docs ask it of;
    `fixed` = decided by the property itself, never by the user."""

    name: str
    label: str
    type: str  # enum | number | integer | boolean | date | text
    options: tuple[tuple[str, str], ...] = ()
    applies: tuple[str, ...] = BUILT
    required: tuple[str, ...] = ()
    recommended: tuple[str, ...] = ()
    unit: str | None = None
    minimum: int = 0
    fixed: bool = False

    def spec(self, ptype: str) -> dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "type": self.type, "unit": self.unit,
            "options": [{"value": v, "label": lab} for v, lab in self.options],
            "required": ptype in self.required,
            "group": "basic" if ptype in self.required or ptype in self.recommended else "more",
            "fixed": self.fixed,
        }


def _area(name: str, label: str, applies: tuple[str, ...] = BUILT, **kw: Any) -> Field:
    return Field(name, label, "number", applies=applies, unit="m²", **kw)


def _count(name: str, label: str, minimum: int = 0) -> Field:
    return Field(name, label, "integer", minimum=minimum)


DISPOSITIONS = ("1+kk", "1+1", "2+kk", "2+1", "3+kk", "3+1", "4+kk", "4+1")

FIELDS: tuple[Field, ...] = (
    Field("property_type", "Nemovitost", "enum", (("flat", "Byt"), ("house", "Dům"), ("land", "Pozemek")),
          applies=ALL, required=ALL, fixed=True),
    Field("place", "Poloha", "text", applies=ALL, required=ALL, fixed=True),
    Field("kind", "Typ obchodu", "enum", (("sale", "Prodej"), ("lease", "Pronájem")), applies=ALL, required=ALL),
    _area("floor_area", "Plocha", required=BUILT),
    _area("lot_area", "Plocha pozemku", applies=(HOUSE, LAND), required=(LAND,)),
    Field("rating", "Stav", "enum", (
        ("bad", "Neobyvatelný"), ("nothing_much", "Před rekonstrukcí"), ("good", "Průměrný"),
        ("very_good", "Po kompletní rekonstrukci"), ("new", "Novostavba"), ("excellent", "Luxusní"),
    ), required=BUILT),
    Field("land_type", "Druh pozemku", "enum", (
        ("building", "Stavební"), ("field", "Pole"), ("garden", "Zahrada"), ("meadow", "Louka"),
        ("forest", "Les"), ("other", "Jiný"),
    ), applies=(LAND,), required=(LAND,)),
    Field("as_of", "Ke dni", "date", applies=ALL, recommended=ALL),
    Field("house_type", "Typ domu", "enum", (
        ("family_house", "Rodinný dům"), ("villa", "Vila"), ("cottage", "Chalupa"), ("hut", "Chata"),
        ("other", "Jiný"),
    ), applies=(HOUSE,), recommended=(HOUSE,)),
    Field("local_type", "Dispozice", "enum", tuple((d, d) for d in DISPOSITIONS) + (("jiné", "Jiná"),),
          applies=(FLAT,)),
    Field("ownership", "Vlastnictví", "enum", (
        ("private", "Osobní"), ("cooperative", "Družstevní"), ("council", "Obecní/státní"), ("other", "Jiné"),
    ), applies=ALL),
    Field("construction", "Konstrukce", "enum", (
        ("brick", "Cihlová"), ("panel", "Panelová"), ("mixed", "Smíšená"), ("wood", "Dřevěná"),
        ("stone", "Kamenná"), ("montage", "Montovaná"), ("low_energy", "Nízkoenergetická"), ("other", "Jiná"),
    )),
    _count("floor", "Podlaží", minimum=-3),  # scraper/floor.py's own bound; the ad's underground floor waits
    _count("total_floors", "Počet podlaží", minimum=1),
    Field("energy_performance", "Energetická třída", "enum", tuple((c, c) for c in "ABCDEFG")),
    Field("elevator", "Výtah", "boolean"),
    Field("equipment", "Vybavení", "boolean"),
    Field("easy_access", "Bezbariérový přístup", "boolean"),
    _area("balcony_area", "Balkon"),
    _area("loggia_area", "Lodžie"),
    _area("terrace_area", "Terasa"),
    _area("cellar_area", "Sklep"),
    _area("attic_area", "Půda"),
    _area("garden_area", "Zahrada"),
    _area("built_up_area", "Zastavěná plocha"),
    _area("other_area", "Další plochy"),
    _count("rooms", "Pokoje"),
    _count("bedrooms", "Ložnice"),
    _count("bathrooms", "Koupelny"),
    _count("kitchens", "Kuchyně"),
    _count("garages", "Garáže"),
    _count("parking_spaces", "Parkovací místa"),
)
_BY_NAME: dict[str, Field] = {f.name: f for f in FIELDS}

# Our stored value -> (Valuo's value, how it maps), per `listings` column. "direct" takes the column's own
# provenance class (_SOURCE); None = not sent. Every canonical member of a mapped column is a key — the
# census in tests/api/test_valuo.py reds a new vocabulary member until someone rules on it.
_DIRECT = "direct"
MAPS: dict[str, tuple[str, dict[str, tuple[Any, str]]]] = {
    "category_main": ("property_type", {
        "byt": (FLAT, _DIRECT), "dum": (HOUSE, _DIRECT), "pozemek": (LAND, _DIRECT),
        "komercni": (None, MISSING), "ostatni": (None, MISSING),  # Valuo has no such type: refused
    }),
    "category_type": ("kind", {
        "prodej": ("sale", _DIRECT), "pronajem": ("lease", _DIRECT),
        "drazba": ("sale", ASSUMED), "podil": ("sale", ASSUMED),
    }),
    "condition": ("rating", {
        "novostavba": ("new", _DIRECT), "po_rekonstrukci": ("very_good", _DIRECT), "dobry": ("good", _DIRECT),
        "pred_rekonstrukci": ("nothing_much", _DIRECT), "k_demolici": ("bad", _DIRECT),
        "projekt": ("new", INFERRED), "ve_vystavbe": ("new", INFERRED), "udrzovany": ("good", INFERRED),
        "v_rekonstrukci": ("nothing_much", INFERRED), "spatny": ("nothing_much", INFERRED),
        "velmi_dobry": ("good", ASSUMED),  # operator ruling 2026-10-19: careful, not very_good
    }),
    "building_type": ("construction", {
        "cihla": ("brick", _DIRECT), "panel": ("panel", _DIRECT), "smisena": ("mixed", _DIRECT),
        "drevo": ("wood", _DIRECT), "kamen": ("stone", _DIRECT), "montovana": ("montage", _DIRECT),
        "nizkoenergeticka": ("low_energy", _DIRECT), "jina": ("other", _DIRECT),
        "roubena": ("wood", INFERRED), "modularni": ("montage", INFERRED),
        "skelet": ("other", INFERRED), "ocelova": ("other", INFERRED),
    }),
    "subtype": ("house_type", {
        "rodinny_dum": ("family_house", _DIRECT), "vila": ("villa", _DIRECT), "chalupa": ("cottage", _DIRECT),
        "chata": ("hut", _DIRECT), "vicegeneracni_dum": ("family_house", INFERRED),
        "na_klic": ("family_house", INFERRED), "zemedelska_usedlost": ("other", INFERRED),
        "pamatka_jine": ("other", INFERRED),
        # commercial subtypes: komercni is refused before anything is mapped
        **{o.value: (None, MISSING) for o in SUBTYPE_OPTIONS if o.group == "komercni"},
    }),
    "disposition": ("local_type", {
        **{o.value: (o.value, _DIRECT) if o.value in DISPOSITIONS else ("jiné", INFERRED)
           for o in DISPOSITION_OPTIONS},
    }),
    "ownership": ("ownership", {
        "osobni": ("private", _DIRECT), "druzstevni": ("cooperative", _DIRECT),
        "statni": ("council", _DIRECT), "jine": ("other", _DIRECT),
    }),
    "energy_rating": ("energy_performance", {
        **{c: (c, _DIRECT) for c in "ABCDEF"},
        "G": (None, ASSUMED),  # operator ruling 2026-10-19: an ad without a PENB certificate must say G too
    }),
    "furnished": ("equipment", {"ano": (True, INFERRED), "ne": (False, INFERRED), "castecne": (None, MISSING)}),
}
_SOURCE: dict[str, str] = {
    "category_main": AD, "category_type": AD, "condition": AD, "subtype": AD, "disposition": AD,
    "building_type": PROPERTY, "ownership": PROPERTY, "energy_rating": PROPERTY,
}
# Columns sent as they stand, through the gate a user's value passes: one Valuo cannot take is not sent.
_COPIED = (("total_floors", "total_floors", AD), ("garden_area", "garden_area", PROPERTY),
           ("parking_lots", "parking_spaces", PROPERTY))
# A yes/no flag is sent only when the ad says yes: some portals store "not mentioned" as no (sreality's garage is
# never NULL), so a no is not evidence and is not sent.
_FLAGS = (("has_lift", "elevator", True, PROPERTY, "výtah"), ("garage", "garages", 1, INFERRED, "garáž"))
_NOT_SENT_NOTES: dict[tuple[str, str], str] = {
    ("energy_rating", "G"): "Třídu G uvádějí i inzeráty bez průkazu PENB, proto se neposílá.",
    ("furnished", "castecne"): "Částečné vybavení se neposílá.",
}
_PTYPE_CATEGORY = {v[0]: k for k, v in MAPS["category_main"][1].items() if v[0]}
_KIND_CATEGORY = {"sale": "prodej", "lease": "pronajem"}

REFUSALS: dict[str, str] = {
    "unconfigured": valuo_client.ERRORS["unconfigured"],
    "unsupported_type": "Valuo oceňuje jen byty, domy a pozemky.",
    "no_location": "Bez polohy nemovitost ocenit nelze.",
    "not_cz": "Valuo oceňuje jen nemovitosti v Česku.",
    "country_unverified": "Není ověřeno, že nemovitost leží v Česku.",  # foreign is a finding, never a default
    "too_coarse": "Poloha je známa jen hruběji než obec — Valuo potřebuje alespoň obec.",
}
ABANDONED = {"code": "abandoned", "http_status": None, "message": "Ocenění se nedokončilo."}

RESULT_KEYS = ("avg_price", "min_price", "max_price", "avg_price_m2", "min_price_m2", "max_price_m2",
               "std_price_m2", "calc_area", "distance", "flags")


class OverrideError(ValueError):
    """A user-set parameter Valuo cannot take; the message is one Czech sentence."""


# --- the mapping -----------------------------------------------------------------------------------


def fields_for(ptype: str) -> list[Field]:
    return [f for f in FIELDS if ptype in f.applies]


def refusal(subject: dict[str, Any], location: dict[str, Any] | None) -> dict[str, str] | None:
    """Why this property cannot be valued at all — decided before any row exists."""
    entry = MAPS["category_main"][1].get(subject.get("category_main") or "")
    code = ("unsupported_type" if entry is None or entry[0] is None
            else "no_location" if location is None
            else "not_cz" if location["country_status"] == "foreign"
            else "country_unverified" if location["country_status"] != "cz"
            else "too_coarse" if location["precision"] == "coarse"
            else None)
    return {"code": code, "message": REFUSALS[code]} if code else None


def _plain(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def _source(column: str, value: Any) -> dict[str, Any]:
    return {"column": column, "value": _plain(value)}


def _option_label(param: str, value: Any) -> str:
    if isinstance(value, bool):
        return "ano" if value else "ne"
    return dict(_BY_NAME[param].options).get(value, str(value))


def build_request(
    subject: dict[str, Any], location: dict[str, Any], overrides: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """(the Valuo request body, provenance per parameter) for a property `refusal` let through."""
    ptype = MAPS["category_main"][1][subject["category_main"]][0]
    names = {f.name for f in fields_for(ptype)}
    values: dict[str, Any] = {}
    prov: dict[str, dict[str, Any]] = {}

    def put(param: str, value: Any, cls: str, source: dict[str, Any] | None = None,
            note: str | None = None) -> None:
        if param in names:
            values[param] = value
            prov[param] = {"class": cls, "from": source, "note": note}

    for column, (param, table) in MAPS.items():
        raw = subject.get(column)
        if raw is None:
            continue
        value, rule = table.get(raw, (None, MISSING))
        if rule == _DIRECT:
            put(param, value, _SOURCE[column], _source(column, raw))
        elif value is None:
            note = _NOT_SENT_NOTES.get((column, raw)) or (None if raw in table else "Hodnota mimo slovník.")
            put(param, None, rule, _source(column, raw), note)
        else:
            ours = label_cs(column, raw) or raw
            put(param, value, rule, _source(column, raw), f"{ours} → {_option_label(param, value)}")

    place = f"{location['lat']:.6f}, {location['lon']:.6f}"
    put("place", place, AD if location["precision"] == "street" else INFERRED,
        _source("listing_location.geom", place), location["note"])
    area = _plain(subject.get("area_m2"))
    if area and area > 0:
        put("lot_area" if ptype == LAND else "floor_area", area, AD, _source("area_m2", area))
    estate = _plain(subject.get("estate_area"))
    if ptype == HOUSE and estate:
        put("lot_area", None, MISSING, _source("estate_area", estate),
            "Plochu pozemku domu zatím neposíláme, než ji Valuo ověřeně započítá.")
    for column, param, cls in _COPIED:
        raw = _plain(subject.get(column))
        if raw is not None:
            with contextlib.suppress(OverrideError):
                put(param, _coerce(_BY_NAME[param], raw), cls, _source(column, raw))
    for flag, param, noun in (("has_balcony", "balcony_area", "balkon"), ("terrace", "terrace_area", "terasu"),
                              ("cellar", "cellar_area", "sklep")):
        if subject.get(flag):
            put(param, None, MISSING, _source(flag, True), f"Inzerát uvádí {noun}, ne plochu.")
    floor = subject.get("floor")
    if floor is not None:
        if floor >= 0:
            put("floor", int(floor), AD, _source("floor", floor))
        else:  # Valuo's reading of a basement is unproven: the user sends it, the ad does not
            put("floor", None, MISSING, _source("floor", floor), "Podzemní podlaží se samo neposílá.")
    for flag, param, value, cls, noun in _FLAGS:
        if subject.get(flag):
            put(param, value, cls, _source(flag, True))
        elif subject.get(flag) is False:
            put(param, None, MISSING, _source(flag, False), f"Inzerát {noun} neuvádí jako ano — neposílá se.")
    put("as_of", datetime.now(_PRAGUE).date().isoformat(), ASSUMED, None, "Dnešní datum.")

    for param, value in overrides.items():
        field = _BY_NAME.get(param)
        if field is None or field.fixed or param not in names:
            raise OverrideError(f"Parametr {param} u této nemovitosti nastavit nelze.")
        put(param, _coerce(field, value), USER, (prov.get(param) or {}).get("from"))
    for f in fields_for(ptype):
        prov.setdefault(f.name, {"class": MISSING, "from": None, "note": None})

    request = {f.name: values[f.name] for f in FIELDS if values.get(f.name) is not None}
    return request, {f.name: prov[f.name] for f in FIELDS if f.name in prov}


def _coerce(field: Field, value: Any) -> Any:
    if value is None:
        return None
    number = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if field.type == "enum" and isinstance(value, str) and value in dict(field.options):
        return value
    if field.type == "boolean" and isinstance(value, bool):
        return value
    if field.type == "integer" and number and value == int(value) and value >= field.minimum:
        return int(value)
    if field.type == "number" and number and value > 0:
        return float(value)
    if field.type == "date" and isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise OverrideError(f"Neplatná hodnota: {field.label}.")


def blocking(request: dict[str, Any]) -> list[str]:
    """The required parameters the request still lacks — the button waits for these."""
    ptype = request["property_type"]
    return [f.name for f in fields_for(ptype) if ptype in f.required and f.name not in request]


def result_basis(request: dict[str, Any]) -> str | None:
    """Rule 23: the per-m² basis of the REQUEST's type and kind — a flipped deal type cannot misplace the unit."""
    return ppm2_basis(_PTYPE_CATEGORY.get(request.get("property_type")), _KIND_CATEGORY.get(request.get("kind")))


# --- the property page's read and the paid call ------------------------------------------------------

_SUBJECT_SQL = """
select listing_id, category_main, category_type, subtype, disposition, area_m2, estate_area, garden_area,
       floor, total_floors, condition, building_type, ownership, energy_rating, furnished,
       has_lift, has_balcony, terrace, cellar, garage, parking_lots
  from properties_public
 where property_id = %s
"""
_COLUMNS = "id, created_at, listing_id, environment, status, request, provenance, response, error"
_HISTORY_SQL = f"""
select {_COLUMNS} from valuo_valuations
 where account_id = %s and listing_id in (select l.id from listings l where l.property_id = %s)
 order by created_at desc, id desc
 limit %s
"""
_ABANDON_SQL = f"""
update valuo_valuations set status = 'failed', error = %s
 where account_id = %s and status = 'pending' and created_at < now() - interval '{_STALE_PENDING}'
returning id
"""
_INSERT_SQL = f"""
insert into valuo_valuations (account_id, listing_id, environment, token_sha, request, provenance)
values (%s, %s, %s, %s, %s, %s)
on conflict do nothing
returning {_COLUMNS}
"""
# Spelled as the unique index's own key, so the index answers it and the two can never disagree.
_TWIN_SQL = f"""
select {_COLUMNS} from valuo_valuations
 where account_id = %s and environment = %s and listing_id = %s
   and md5(request::text) = md5(%s::jsonb::text) and status in ('pending', 'success')
 limit 1
"""
_FINISH_SQL = f"""
update valuo_valuations set status = %s, response = %s, error = %s
 where id = %s and status = 'pending'
returning {_COLUMNS}
"""


def _rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _load(conn: Any, property_id: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    [subject] = _rows(conn, _SUBJECT_SQL, (property_id,)) or [None]
    if subject is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Nemovitost nenalezena"})
    try:
        location = load_location(conn, property_id)
    except HTTPException as exc:
        if exc.status_code == 404 and exc.detail.get("reason") == "no_location":
            return subject, None
        raise
    return subject, location


def present(row: dict[str, Any]) -> dict[str, Any]:
    response = row["response"] if isinstance(row["response"], dict) else {}
    result = response.get("result") if row["status"] == "success" else None
    error = row["error"]
    ignored = response.get("ignored_ids") or []
    return {
        "id": row["id"], "created_at": row["created_at"], "listing_id": row["listing_id"],
        "environment": row["environment"], "status": row["status"],
        "request": row["request"], "provenance": row["provenance"],
        "result": {k: result.get(k) for k in RESULT_KEYS} if isinstance(result, dict) else None,
        "ignored_ids": ignored,
        # In Czech here, so the card and the ledger read even on a refused page, which carries no fields.
        "ignored_labels": [_BY_NAME[n].label if n in _BY_NAME else str(n) for n in ignored],
        "kind_label": _option_label("kind", row["request"].get("kind")),
        "error": {"code": error["code"], "message": error["message"]} if error else None,
        "ppm2_basis": result_basis(row["request"]),
    }


def page(conn: Any, property_id: int, *, account_id: uuid.UUID,
         cred: valuo_client.Credential | None) -> dict[str, Any]:
    """GET /properties/{id}/valuo: the editor's defaults and the caller's own history. Never a 503. It settles
    the caller's abandoned calls first, so a call the API died during is never shown as running."""
    subject, location = _load(conn, property_id)
    refused = refusal(subject, location)
    _rows(conn, _ABANDON_SQL, (Jsonb(ABANDONED), account_id))
    out: dict[str, Any] = {
        "environment": cred.environment if cred else None,
        "refusal": refused or (None if cred else {"code": "unconfigured", "message": REFUSALS["unconfigured"]}),
        "subject": {
            "listing_id": subject["listing_id"],
            "category_main": subject["category_main"],
            "category_type": subject["category_type"],
            "label": " · ".join(p for p in (type_label_cs(subject["category_main"], subject["subtype"]),
                                            subject["disposition"]) if p),
            "location": None if location is None else {
                k: location[k] for k in ("label", "granularity", "uncertainty_radius_m", "note")},
        },
        "fields": [], "defaults": {}, "provenance": {}, "blocking": [],
        "history": [present(r) for r in _rows(conn, _HISTORY_SQL, (account_id, property_id, _HISTORY_LIMIT))],
    }
    if refused is None:
        request, provenance = build_request(subject, location, {})
        ptype = request["property_type"]
        out.update(fields=[f.spec(ptype) for f in fields_for(ptype)], defaults=request,
                   provenance=provenance, blocking=blocking(request))
    return out


def _refuse(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def run(conn: Any, property_id: int, overrides: dict[str, Any], *, account_id: uuid.UUID,
        cred: valuo_client.Credential) -> dict[str, Any]:
    """POST /properties/{id}/valuo: refuse (4xx, no row), or save the row, pay for one call, record its answer."""
    subject, location = _load(conn, property_id)
    refused = refusal(subject, location)
    if refused:
        raise _refuse(422, **refused)
    try:
        request, provenance = build_request(subject, location, overrides)
    except OverrideError as exc:
        raise _refuse(422, "bad_override", str(exc)) from None
    missing = blocking(request)
    if missing:
        labels = ", ".join(_BY_NAME[m].label for m in missing)
        raise _refuse(422, "missing_required", f"Chybí povinné parametry: {labels}.", fields=missing)

    # `conn` is autocommit: each statement commits alone, so no transaction stays open across the paid call.
    _rows(conn, _ABANDON_SQL, (Jsonb(ABANDONED), account_id))
    [row] = _rows(conn, _INSERT_SQL, (account_id, subject["listing_id"], cred.environment, cred.token_sha,
                                      Jsonb(request), Jsonb(provenance))) or [None]
    if row is None:  # the unique index met this ad's identical pending or successful twin
        [twin] = _rows(conn, _TWIN_SQL, (account_id, cred.environment, subject["listing_id"],
                                         Jsonb(request))) or [None]
        if twin is not None and twin["status"] == "success":
            return {"reused": True, "valuation": present(twin)}
        raise _refuse(409, "in_progress", "Stejný dotaz právě běží.")

    try:
        response, error = valuo_client.calculate(cred, request), None
    except valuo_client.ValuoError as exc:
        response = exc.response
        error = {"code": exc.code, "http_status": exc.http_status, "message": exc.message}
    status = "failed" if error else "success"
    [done] = _rows(conn, _FINISH_SQL, (status, None if response is None else Jsonb(response),
                                       None if error is None else Jsonb(error), row["id"])) or [None]
    if done is None:  # the sweep abandoned it mid-call: a paid answer with no row to land on
        LOG.warning("VALUO answer lost row=%s status=%s", row["id"], status)
        done = row | {"status": "failed", "error": ABANDONED}
    return {"reused": False, "valuation": present(done)}
