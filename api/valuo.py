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

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api import valuo_client
from api.routes.property_location import load_location
from toolkit.filter_registry import CATEGORY_MAIN_NOUN_CS, DISPOSITION_OPTIONS, label_cs, type_label_cs
from toolkit.measures import ppm2_basis

LOG = logging.getLogger(__name__)

FLAT, HOUSE, LAND, COMMERCIAL = "flat", "house", "land", "commercial"
ALL = (FLAT, HOUSE, LAND, COMMERCIAL)
BUILT = (FLAT, HOUSE, COMMERCIAL)

AD, PROPERTY, INFERRED, ASSUMED, MISSING, USER = "ad", "property", "inferred", "assumed", "missing", "user"

_PRAGUE = ZoneInfo("Europe/Prague")
_HISTORY_LIMIT = 50
_STALE_PENDING = "2 minutes"
_TEXT_MAX = 200


@dataclass(frozen=True)
class Field:
    """One Valuo parameter. `applies` = the endpoints whose v3 schema takes it (Valuo silently drops any other);
    `required`/`recommended` name the types Valuo's docs ask it of; `fixed` = decided by the property itself."""

    name: str
    label: str
    type: str  # enum | number (every v3 number is an area, m²) | integer | boolean | date | text
    options: tuple[tuple[str, str], ...] = ()
    applies: tuple[str, ...] = ALL
    required: tuple[str, ...] = ()
    recommended: tuple[str, ...] = ()
    minimum: int = 0
    fixed: bool = False

    def spec(self, ptype: str) -> dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "type": self.type, "unit": "m²" if self.type == "number" else None,
            "options": [{"value": v, "label": lab} for v, lab in self.options],
            "required": ptype in self.required,
            "group": "basic" if ptype in self.required or ptype in self.recommended else "more",
            "fixed": self.fixed,
            "max_length": _TEXT_MAX if self.type == "text" else None,
        }


# The production engine rejects any other local_type (the sandbox accepts anything, so only the docs say so).
LOCAL_TYPES = ("1+0", "1+kk", "1+1", "2+kk", "2+1", "3+kk", "3+1", "4+kk", "4+1", "5+kk", "5+1", "6+kk", "6+1",
               "7+kk", "7+1")

# Editor order; commercial_type right after the fixed type: it matters most there (operator, 2026-10-20).
FIELDS: tuple[Field, ...] = (
    Field("property_type", "Nemovitost", "enum", (
        (FLAT, "Byt"), (HOUSE, "Dům"), (LAND, "Pozemek"), (COMMERCIAL, "Komerční prostor"),
    ), required=ALL, fixed=True),
    Field("commercial_type", "Typ prostoru", "enum", (
        ("office", "Kancelář"), ("storage", "Sklad"), ("retail", "Obchodní prostor"), ("production", "Výroba"),
        ("restaurant", "Restaurace"), ("accommodation", "Ubytování"), ("agriculture", "Zemědělský objekt"),
        ("apartment_building", "Činžovní dům"), ("apartment", "Apartmán"), ("medical_office", "Ordinace"),
        ("other", "Jiný"),
    ), applies=(COMMERCIAL,), recommended=(COMMERCIAL,)),
    Field("place", "Poloha (adresa nebo souřadnice)", "text", required=ALL),
    Field("kind", "Typ obchodu", "enum", (("sale", "Prodej"), ("lease", "Pronájem")), required=ALL),
    Field("floor_area", "Plocha", "number", applies=BUILT, required=BUILT),
    Field("lot_area", "Plocha pozemku", "number", applies=(HOUSE, LAND, COMMERCIAL), required=(LAND,)),
    Field("rating", "Stav", "enum", (
        ("bad", "Neobyvatelný"), ("nothing_much", "Před rekonstrukcí"), ("good", "Průměrný"),
        ("very_good", "Velmi dobrý"), ("excellent", "Luxusní"),
    ), required=ALL),
    Field("new_building", "Novostavba", "boolean", applies=(FLAT, HOUSE), recommended=(FLAT, HOUSE)),
    Field("land_type", "Druh pozemku", "enum", (
        ("building", "Stavební"), ("field", "Pole"), ("garden", "Zahrada"), ("meadow", "Louka"),
        ("forest", "Les"), ("commercial", "Komerční"), ("other", "Jiný"),
    ), applies=(LAND,), required=(LAND,)),
    Field("as_of", "Ke dni", "date", recommended=ALL),
    Field("house_type", "Typ domu", "enum", (
        ("family_house", "Rodinný dům"), ("villa", "Vila"), ("cottage", "Chalupa"), ("hut", "Chata"),
        ("other", "Jiný"),
    ), applies=(HOUSE,), recommended=(HOUSE,)),
    Field("local_type", "Dispozice", "enum", tuple((d, d) for d in LOCAL_TYPES) + (("jiné", "Jiná"),),
          applies=(FLAT,)),
    Field("ownership", "Vlastnictví", "enum", (
        ("private", "Osobní"), ("cooperative", "Družstevní"), ("council", "Obecní/státní"), ("other", "Jiné"),
    ), applies=(FLAT,)),
    Field("construction", "Konstrukce", "enum", (
        ("brick", "Cihlová"), ("panel", "Panelová"), ("mixed", "Smíšená"), ("wood", "Dřevěná"),
        ("stone", "Kamenná"), ("montage", "Montovaná"), ("low_energy", "Nízkoenergetická"), ("other", "Jiná"),
    ), applies=(FLAT, HOUSE)),
    Field("floor", "Podlaží", "integer", applies=(FLAT,), minimum=-3),  # scraper/floor.py's bound; a basement waits
    Field("total_floors", "Počet podlaží", "integer", applies=(FLAT,), minimum=1),
    Field("energy_performance", "Energetická třída", "enum", tuple((c, c) for c in "ABCDEFG"), applies=(HOUSE,)),
    Field("balcony_area", "Balkon", "number", applies=(FLAT,)),
    Field("cellar_area", "Sklep", "number", applies=(FLAT, HOUSE)),
    Field("garden_area", "Zahrada", "number", applies=(FLAT,)),
    Field("garages", "Garáže", "integer", applies=(FLAT, HOUSE)),
    Field("parking_spaces", "Parkovací místa", "integer", applies=(FLAT, HOUSE)),
)
_BY_NAME: dict[str, Field] = {f.name: f for f in FIELDS}

# Our stored value -> (Valuo's value, how it maps), per `listings` column, sent as the parameter its type takes
# ({type: parameter} where the column answers a different one per type). "direct" takes the column's own
# provenance class (_SOURCE); None = not sent. Every canonical member of a mapped column is a key — the
# census in tests/api/test_valuo.py reds a new vocabulary member until someone rules on it.
_DIRECT = "direct"
MAPS: dict[str, tuple[str | dict[str, str], dict[str, tuple[Any, str]]]] = {
    "category_main": ("property_type", {
        "byt": (FLAT, _DIRECT), "dum": (HOUSE, _DIRECT), "pozemek": (LAND, _DIRECT),
        "komercni": (COMMERCIAL, _DIRECT), "ostatni": (None, MISSING),  # Valuo's `other` is unproven: refused
    }),
    "category_type": ("kind", {
        "prodej": ("sale", _DIRECT), "pronajem": ("lease", _DIRECT),
        "drazba": ("sale", ASSUMED), "podil": ("sale", ASSUMED),
    }),
    "condition": ("rating", {
        # v3 has no "new" rating: a new building is rated very good and says so in `new_building` (below)
        "novostavba": ("very_good", ASSUMED), "po_rekonstrukci": ("very_good", _DIRECT),
        "velmi_dobry": ("very_good", _DIRECT), "dobry": ("good", _DIRECT),
        "pred_rekonstrukci": ("nothing_much", _DIRECT), "k_demolici": ("bad", _DIRECT),
        "projekt": ("very_good", INFERRED), "ve_vystavbe": ("very_good", INFERRED), "udrzovany": ("good", INFERRED),
        "v_rekonstrukci": ("nothing_much", INFERRED), "spatny": ("nothing_much", INFERRED),
    }),
    "building_type": ("construction", {
        "cihla": ("brick", _DIRECT), "panel": ("panel", _DIRECT), "smisena": ("mixed", _DIRECT),
        "drevo": ("wood", _DIRECT), "kamen": ("stone", _DIRECT), "montovana": ("montage", _DIRECT),
        "nizkoenergeticka": ("low_energy", _DIRECT), "jina": ("other", _DIRECT),
        "roubena": ("wood", INFERRED), "modularni": ("montage", INFERRED),
        "skelet": ("other", INFERRED), "ocelova": ("other", INFERRED),
    }),
    "subtype": ({HOUSE: "house_type", COMMERCIAL: "commercial_type"}, {
        "rodinny_dum": ("family_house", _DIRECT), "vila": ("villa", _DIRECT), "chalupa": ("cottage", _DIRECT),
        "chata": ("hut", _DIRECT), "vicegeneracni_dum": ("family_house", INFERRED),
        "na_klic": ("family_house", INFERRED), "zemedelska_usedlost": ("other", INFERRED),
        "pamatka_jine": ("other", INFERRED),
        "kancelar": ("office", _DIRECT), "sklad": ("storage", _DIRECT), "obchodni_prostor": ("retail", _DIRECT),
        "vyroba": ("production", _DIRECT), "restaurace": ("restaurant", _DIRECT),
        "ubytovani": ("accommodation", _DIRECT), "zemedelsky": ("agriculture", _DIRECT),
        "cinzovni_dum": ("apartment_building", _DIRECT), "apartmany": ("apartment", _DIRECT),
        "ordinace": ("medical_office", _DIRECT), "ostatni": ("other", _DIRECT),
        "virtualni_kancelar": (None, MISSING),  # an address service, not premises Valuo could compare
    }),
    "disposition": ("local_type", {o.value: (o.value, _DIRECT) if o.value in LOCAL_TYPES else ("jiné", INFERRED)
                                   for o in DISPOSITION_OPTIONS}),
    "ownership": ("ownership", {
        "osobni": ("private", _DIRECT), "druzstevni": ("cooperative", _DIRECT),
        "statni": ("council", _DIRECT), "jine": ("other", _DIRECT),
    }),
    "energy_rating": ("energy_performance", {
        **{c: (c, _DIRECT) for c in "ABCDEF"},
        "G": (None, ASSUMED),  # operator ruling 2026-10-19: an ad without a PENB certificate must say G too
    }),
}
# The condition also answers Valuo's `new_building`, with this class; every other condition sends nothing.
NEW_BUILDING: dict[str, str] = {"novostavba": AD, "projekt": INFERRED, "ve_vystavbe": INFERRED}
_SOURCE: dict[str, str] = {
    "category_main": AD, "category_type": AD, "condition": AD, "subtype": AD, "disposition": AD,
    "building_type": PROPERTY, "ownership": PROPERTY, "energy_rating": PROPERTY,
}
# Columns sent as they stand, through the gate a user's value passes: one Valuo cannot take is not sent.
_COPIED = (("total_floors", "total_floors", AD), ("garden_area", "garden_area", PROPERTY),
           ("parking_lots", "parking_spaces", PROPERTY))
_NOT_SENT_NOTES = {("energy_rating", "G"): "Třídu G uvádějí i inzeráty bez průkazu PENB, proto se neposílá."}
LAND_RATING_NOTE = "Valuo vyžaduje stav i u pozemku."
_PTYPE_CATEGORY = {v[0]: k for k, v in MAPS["category_main"][1].items() if v[0]}
_KIND_CATEGORY = {"sale": "prodej", "lease": "pronajem"}

REFUSALS: dict[str, str] = {
    "unconfigured": valuo_client.ERRORS["unconfigured"],
    "unsupported_type": "Valuo oceňuje jen byty, domy, pozemky a komerční nemovitosti.",
    "no_location": "Bez polohy nemovitost ocenit nelze.",
    "not_cz": "Valuo oceňuje jen nemovitosti v Česku.",
    "country_unverified": "Není ověřeno, že nemovitost leží v Česku.",  # foreign is a finding, never a default
    "too_coarse": "Poloha je známa jen hruběji než obec — Valuo potřebuje alespoň obec.",
}
ABANDONED = {"code": "abandoned", "http_status": None, "message": "Ocenění se nedokončilo."}
DUPLICATE = {"code": "duplicate", "http_status": None, "message": "Stejné ocenění už je uložené."}

RESULT_KEYS = ("avg_price", "range_price", "avg_price_m2", "range_price_m2", "std_price_m2", "calc_area",
               "distance", "flags")  # range_price = Valuo's confidence band; min/max_price are not it
RECORD_KEYS = ("id", "place", "lat", "lon", "local_type", "calc_area", "lot_area", "floor", "total_floors", "rating",
               "new_building", "kind", "property_type", "price", "price_m2", "sale_price", "adjusted_price",
               "adjusted_price_m2", "distance", "score_prct", "days", "first_seen_at", "last_seen_at")
SUMMARY_KEYS = ("avg_distance", "avg_age", "avg_duration", "avg_score", "distance", "age")
ADVERT_LIMIT = 10  # comparables whose photos are asked for, in Valuo's order: one advert/detail call per valuation
PHOTO_LIMIT = 12
PHOTO_URL = "https://pics.valuo.cz/{size}/{path}"  # pt = 450 px, p = 1200 px; public, no token


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


def _word(column: str, raw: str) -> str:
    """Our Czech word for a stored value; a category by its noun (Byt), not the filter's plural (Byty)."""
    return (CATEGORY_MAIN_NOUN_CS.get(raw) if column == "category_main" else label_cs(column, raw)) or raw


def _option_label(param: str, value: Any) -> str:
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

    for column, (params, table) in MAPS.items():
        param = params if isinstance(params, str) else params.get(ptype)
        raw = subject.get(column)
        if raw is None or param is None:
            continue
        value, rule = table.get(raw, (None, MISSING))
        if value is not None and value not in dict(_BY_NAME[param].options):  # another type's subtype
            value, rule = None, MISSING
        ours, theirs = _word(column, raw), _option_label(param, value)
        step = f"{ours} → {theirs}"
        if rule == _DIRECT:  # named only where Valuo words it otherwise: Dobrý → Průměrný, never 2+kk → 2+kk
            put(param, value, _SOURCE[column], _source(column, raw), None if ours == theirs else step)
        elif value is None:
            note = _NOT_SENT_NOTES.get((column, raw)) or (None if raw in table else "Hodnota mimo slovník.")
            put(param, None, rule, _source(column, raw), note)
        else:
            put(param, value, rule, _source(column, raw), step)
    condition = subject.get("condition")
    if condition in NEW_BUILDING:
        cls = NEW_BUILDING[condition]
        put("new_building", True, cls, _source("condition", condition),
            None if cls == AD else f"{_word('condition', condition)} → {_BY_NAME['new_building'].label}")

    if ptype == LAND and values.get("rating") is None:
        put("rating", "good", ASSUMED, None, LAND_RATING_NOTE)

    place = f"{location['lat']:.6f}, {location['lon']:.6f}"
    put("place", place, AD if location["precision"] == "street" else INFERRED,
        _source("listing_location.geom", place), " · ".join(p for p in (location["label"], location["note"]) if p))
    area = _plain(subject.get("area_m2"))
    if area and area > 0:
        put("lot_area" if ptype == LAND else "floor_area", area, AD, _source("area_m2", area))
    estate = _plain(subject.get("estate_area"))
    if ptype in (HOUSE, COMMERCIAL) and estate:
        put("lot_area", None, MISSING, _source("estate_area", estate),
            "Plochu pozemku zatím neposíláme, než ji Valuo ověřeně započítá.")
    for column, param, cls in _COPIED:
        raw = _plain(subject.get(column))
        if raw is not None:
            with contextlib.suppress(OverrideError):
                put(param, _coerce(_BY_NAME[param], raw), cls, _source(column, raw))
    for flag, param, noun in (("has_balcony", "balcony_area", "balkon"), ("cellar", "cellar_area", "sklep")):
        if subject.get(flag):
            put(param, None, MISSING, _source(flag, True), f"Inzerát uvádí {noun}, ne plochu.")
    floor = subject.get("floor")
    if floor is not None:
        if floor >= 0:
            put("floor", int(floor), AD, _source("floor", floor))
        else:  # Valuo's reading of a basement is unproven: the user sends it, the ad does not
            put("floor", None, MISSING, _source("floor", floor), "Podzemní podlaží se samo neposílá.")
    garage = subject.get("garage")
    if garage:
        put("garages", 1, INFERRED, _source("garage", True), "Garáž: ano → 1")
    elif garage is False:  # a no is not evidence: sreality's garage is never NULL, "not mentioned" reads as no
        put("garages", None, MISSING, _source("garage", False), "Inzerát garáž neuvádí jako ano — neposílá se.")
    put("as_of", datetime.now(_PRAGUE).date().isoformat(), ASSUMED, None, "Dnešní datum.")

    for param, value in overrides.items():
        field = _BY_NAME.get(param)
        if field is None or field.fixed or param not in names:
            raise OverrideError(f"Parametr {param} u této nemovitosti nastavit nelze.")
        put(param, _coerce(field, value), USER, (prov.get(param) or {}).get("from"))
    for f in fields_for(ptype):
        prov.setdefault(f.name, {"class": MISSING, "from": None, "note": None})

    request = {f.name: values[f.name] for f in FIELDS if values.get(f.name) is not None}
    request["country_code"] = valuo_client.COUNTRY_CODE
    return request, {f.name: prov[f.name] for f in FIELDS if f.name in prov}


def _coerce(field: Field, value: Any) -> Any:
    if value is None:
        return None
    number = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if field.type == "enum" and isinstance(value, str) and value in dict(field.options):
        return value
    if field.type == "boolean" and isinstance(value, bool):
        return value
    if field.type == "text" and isinstance(value, str) and 0 < len(value.strip()) <= _TEXT_MAX:
        return value.strip()
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
       floor, total_floors, condition, building_type, ownership, energy_rating, has_balcony, cellar, garage,
       parking_lots
  from properties_public
 where property_id = %s
"""
_COLUMNS = "id, created_at, listing_id, environment, status, request, provenance, response, error, adverts"
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
update valuo_valuations set status = %s, response = %s, error = %s, adverts = %s, environment = %s
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


def _photos(advert: dict[str, Any]) -> list[dict[str, str]]:
    """An advert's photos from its storage paths; [] when Valuo sent only `hashed_images` (no entitlement)."""
    images = advert.get("images")
    paths = [p.lstrip("/") for p in images if isinstance(p, str) and p.strip("/")] if isinstance(images, list) else []
    return [{"thumb": PHOTO_URL.format(size="pt", path=p), "full": PHOTO_URL.format(size="p", path=p)}
            for p in paths[:PHOTO_LIMIT]]


def _records(result: dict[str, Any], adverts: Any) -> list[dict[str, Any]]:
    """Valuo's comparables in its own order, each cut to what the page shows, with its advert's photos."""
    records = result.get("records")
    by_id = {str(a.get("id")): a for a in adverts if isinstance(a, dict)} if isinstance(adverts, list) else {}
    pairs = ([(r, by_id.get(str(r.get("id")), {})) for r in records if isinstance(r, dict)]
             if isinstance(records, list) else [])
    return [{**{k: r.get(k) for k in RECORD_KEYS}, "photos": _photos(a), "advert_url": a.get("advert_url"),
             "address": a.get("address")} for r, a in pairs]


def _summary(response: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """What the answer rests on, for the reader to judge: Valuo grades nothing, so neither do we."""
    keep, flags = response.get("keep_ids"), result.get("flags")
    return {"count": len(keep) if isinstance(keep, list) else None, **{k: result.get(k) for k in SUMMARY_KEYS},
            "not_enough": isinstance(flags, list) and "NOT_ENOUGH_PROPERTIES" in flags}


def _error(error: dict[str, Any], response: Any) -> dict[str, Any]:
    out = {"code": error["code"], "message": error["message"]}
    if error["code"] == "form_errors":
        out["form_errors"] = valuo_client.form_issues(response)
    return out


def present(row: dict[str, Any]) -> dict[str, Any]:
    """A row as the page reads it. Valuo's `ignored_ids` stays in the stored answer only: v3 always sends []."""
    response = row["response"] if isinstance(row["response"], dict) else {}
    result = response.get("result") if row["status"] == "success" else None
    result = result if isinstance(result, dict) else None
    return {
        "id": row["id"], "created_at": row["created_at"], "listing_id": row["listing_id"],
        "environment": row["environment"], "status": row["status"],
        "request": row["request"], "provenance": row["provenance"],
        "result": {k: result.get(k) for k in RESULT_KEYS} if result else None,
        "records": _records(result, row.get("adverts")) if result else [],
        "summary": _summary(response, result) if result else None,
        "kind_label": _option_label("kind", row["request"].get("kind")),
        "error": _error(row["error"], response) if row["error"] else None,
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
            **{k: subject[k] for k in ("listing_id", "category_main", "category_type")},
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

    adverts = None
    try:
        response, error = valuo_client.calculate(cred, request), None
    except valuo_client.ValuoError as exc:
        response = exc.response
        error = {"code": exc.code, "http_status": exc.http_status, "message": exc.message, "detail": exc.detail}
    ids = [] if error else [r["id"] for r in _records(response["result"], None) if r["id"] is not None][:ADVERT_LIMIT]
    if ids:  # the photos are a garnish: their failure never fails the valuation it would decorate
        try:
            adverts = valuo_client.advert_details(cred, ids)
        except valuo_client.ValuoError as exc:
            LOG.warning("VALUO adverts not fetched row=%s code=%s", row["id"], exc.code)
    status, environment = "failed" if error else "success", valuo_client.answered_by(cred, response)
    answer = None if response is None else Jsonb(response)
    try:
        with conn.transaction():
            [done] = _rows(conn, _FINISH_SQL, (status, answer, None if error is None else Jsonb(error),
                                               None if adverts is None else Jsonb(adverts), environment,
                                               row["id"])) or [None]
    except psycopg.errors.UniqueViolation:  # a sandbox token named production met its own stored sandbox answer
        _rows(conn, _FINISH_SQL, ("failed", answer, Jsonb(DUPLICATE), None, environment, row["id"]))
        [twin] = _rows(conn, _TWIN_SQL, (account_id, environment, subject["listing_id"], Jsonb(request))) or [None]
        if twin is not None:
            return {"reused": True, "valuation": present(twin)}
        done = row | {"status": "failed", "error": DUPLICATE}
    if done is None:  # the sweep abandoned it mid-call: a paid answer with no row to land on
        LOG.warning("VALUO answer lost row=%s status=%s", row["id"], status)
        done = row | {"status": "failed", "error": ABANDONED}
    return {"reused": False, "valuation": present(done)}
