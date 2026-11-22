"""Cadastre prices bought on request: one building, one paid WSDP output, on the asking account's own ČÚZK login.

The purchase row is written BEFORE anything is ordered and carries the report's id from the moment ČÚZK accepts the
order, so a request that dies mid-way is resumed by the next one on the SAME report: never a second order, and a
report's second fetch is free. No database connection is held while waiting on ČÚZK. Everything is the buying
account's own (operator ruling 2026-11-22): every statement names the account (service-role connection)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api import cadastre_price_report, cuzk_login, wsdp_client

PRICE_CZK = 50  # vyhláška 358/2013, item 5010: one output over up to 200 properties of one type
POLL_EVERY_S = 2
POLL_FOR_S = 60
_READY = frozenset({"zpracován", "zaúčtován"})

Opener = Callable[[], AbstractContextManager[psycopg.Connection]]


class NoProperty(LookupError):
    """The property is unknown."""


class NoBuilding(LookupError):
    """The property's door names no building the cadastre knows."""


class NoLogin(LookupError):
    """The account has no ČÚZK login stored."""


class TooManyUnits(ValueError):
    """The building has more units than one output covers."""


class StillPreparing(RuntimeError):
    """ČÚZK has the order and has not finished the report; asking again resumes it."""


@dataclass(frozen=True)
class Building:
    code: int
    iskn_id: int


@dataclass(frozen=True)
class Purchase:
    id: int
    report_id: int | None


_BUILDING_SQL = """
select b.code, b.iskn_building_id, p.id is not null as found
  from properties p
  left join listing_location ll on ll.listing_id = p.location_listing_ref_id
  left join ruian_address_points ap on ap.kod_adm = ll.ruian_adm_kod
  left join ruian_building_objects b on b.code = ap.stavebni_objekt_code and b.valid_to is null
 where p.id = %s and p.status = 'active'
"""


def building_of(conn: psycopg.Connection, property_id: int) -> Building:
    with conn.cursor() as cur:
        cur.execute(_BUILDING_SQL, (property_id,))
        row = cur.fetchone()
    if row is None:
        raise NoProperty(property_id)
    if row[0] is None or row[1] is None:
        raise NoBuilding(property_id)
    return Building(int(row[0]), int(row[1]))


def begin(conn: psycopg.Connection, account_id: uuid.UUID, building: Building) -> Purchase:
    """The account's request in flight for this building, or a new one."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into cadastre_price_purchases (account_id, building_code, iskn_building_id)
            values (%s, %s, %s)
            on conflict (account_id, building_code) where status in ('pending', 'ordered') do nothing
            returning id, report_id
            """,
            (account_id, building.code, building.iskn_id),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "select id, report_id from cadastre_price_purchases "
                "where account_id = %s and building_code = %s and status in ('pending', 'ordered')",
                (account_id, building.code),
            )
            row = cur.fetchone()
    return Purchase(int(row[0]), row[1])


def mark_ordered(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int, report_id: int,
                 object_count: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set status = 'ordered', report_id = %s, object_count = %s "
            "where id = %s and account_id = %s",
            (report_id, object_count, purchase_id, account_id),
        )


def fail(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set status = 'failed', error = %s, finished_at = now() "
            "where id = %s and account_id = %s and status = 'pending'",
            (error[:200], purchase_id, account_id),
        )


def finish(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int, cost_czk: int | None,
           deeds: list[cadastre_price_report.Deed]) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set status = 'done', cost_czk = %s, finished_at = now() "
            "where id = %s and account_id = %s and status = 'ordered'",
            (cost_czk, purchase_id, account_id),
        )
        if cur.rowcount != 1:
            return  # another request finished it first; its deeds stand
        for deed in deeds:
            cur.execute(
                """
                insert into cadastre_deeds (purchase_id, account_id, proceeding, kind, price, currency,
                                            signed_on, effective_on, entered_on, properties)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (purchase_id, account_id, deed.proceeding, deed.kind, deed.price, deed.currency, deed.signed_on,
                 deed.effective_on, deed.entered_on,
                 Jsonb([{"kind": p.kind, "iskn_id": p.iskn_id, "label": p.label} for p in deed.properties])),
            )


_STATE_SQL = """
select id, status, cost_czk, object_count, requested_at, finished_at
  from cadastre_price_purchases
 where account_id = %s and building_code = %s and status <> 'failed'
 order by requested_at desc
 limit 1
"""
_DEEDS_SQL = """
select d.proceeding, d.kind, d.price, d.currency, d.signed_on, d.effective_on, d.entered_on, d.properties
  from cadastre_deeds d
 where d.account_id = %s
   and d.purchase_id = (select p.id from cadastre_price_purchases p
                         where p.account_id = %s and p.building_code = %s and p.status = 'done'
                         order by p.requested_at desc limit 1)
 order by d.effective_on desc nulls last, d.proceeding
"""


def state(conn: psycopg.Connection, account_id: uuid.UUID, building_code: int) -> dict[str, Any]:
    """The account's newest request for the building and the deeds of its newest finished one."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_STATE_SQL, (account_id, building_code))
        purchase = cur.fetchone()
        cur.execute(_DEEDS_SQL, (account_id, account_id, building_code))
        deeds = cur.fetchall()
    if purchase is not None:
        purchase = {"status": purchase["status"], "cost_czk": purchase["cost_czk"],
                    "object_count": purchase["object_count"],
                    "requested_at": purchase["requested_at"].isoformat(),
                    "finished_at": purchase["finished_at"].isoformat() if purchase["finished_at"] else None}
    return {
        "price_czk": PRICE_CZK,
        "purchase": purchase,
        "deeds": [{**d, "price": float(d["price"]) if d["price"] is not None else None,
                   **{k: d[k].isoformat() if d[k] else None for k in ("signed_on", "effective_on", "entered_on")}}
                  for d in deeds],
    }


def buy(account_id: uuid.UUID, property_id: int, *, opener: Opener,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Building:
    """Order (or resume) the account's price report for the property's building and store its deeds."""
    with opener() as conn:
        building = building_of(conn, property_id)
        login = cuzk_login.login_of(conn, account_id)
        if login is None:
            raise NoLogin(str(account_id))
        purchase = begin(conn, account_id, building)
    report_id = purchase.report_id
    if report_id is None:
        try:
            units = wsdp_client.find_units(building.iskn_id, *login)
            if len(units) > wsdp_client.MAX_PRICE_OBJECTS:
                raise TooManyUnits(len(units))
            object_type, ids = ("JED_S", [u.iskn_id for u in units]) if units else ("STA_S", [building.iskn_id])
            ordered = wsdp_client.generate_prices(object_type, ids, *login)
        except (wsdp_client.WsdpError, TooManyUnits) as exc:
            with opener() as conn:
                fail(conn, account_id, purchase.id, f"{type(exc).__name__}: {exc}")
            raise
        report_id = ordered.id
        with opener() as conn:
            mark_ordered(conn, account_id, purchase.id, report_id, len(ids))
    deadline = clock() + POLL_FOR_S
    while (report := wsdp_client.report_state(report_id, *login)).state not in _READY:
        if clock() >= deadline:
            raise StillPreparing(report.state)
        sleep(POLL_EVERY_S)
    fetched, document = wsdp_client.fetch_report(report_id, *login)  # the first fetch is the charge
    deeds = cadastre_price_report.parse(document)
    with opener() as conn:
        finish(conn, account_id, purchase.id, fetched.price_czk, deeds)
    return building
