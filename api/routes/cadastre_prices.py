"""Cadastre prices on request. Mounted at `/properties/{id}/building/prices`.

GET answers the caller's own purchase and deeds for the property's building. POST buys: one paid ČÚZK output on
the caller's own stored ČÚZK login, and only with the price acknowledged in the body (`{"cost_czk": 50}`), so
nothing is ever bought by a click that did not show its price. Both are the caller's OWN account (never acted
for); a bought price is visible to no other account. POST resolves the account in a transaction of its own
(`own_account_id_released`) because it then waits on ČÚZK.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from api import cadastre_prices, cuzk_login, tenant_pool, wsdp_client
from api import dependencies as deps

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["properties"])

_NO_PROPERTY = "Nemovitost nenalezena"
_NO_BUILDING = "The cadastre does not know a building at this property's address."
_NO_LOGIN = "Store your ČÚZK login first (account menu, ČÚZK login)."
_UNREADABLE = "Your stored ČÚZK login can no longer be read. Enter it again (account menu, ČÚZK login)."
_REFUSED = "ČÚZK refused your stored login. Enter it again (account menu, ČÚZK login)."
_NO_ANSWER = "ČÚZK did not answer. Nothing was charged for this attempt; try again."
_PREPARING = "ČÚZK is still preparing the report. Ask again in a moment: it is the same order, not a new one."
_TOO_MANY = "This building has more than 200 units, which one request cannot cover yet."


def _price_ack(body: Any) -> None:
    if not isinstance(body, dict) or body.get("cost_czk") != cadastre_prices.PRICE_CZK:
        raise HTTPException(status_code=409, detail={
            "reason": "price_not_acknowledged", "price_czk": cadastre_prices.PRICE_CZK,
            "message": f"This request costs {cadastre_prices.PRICE_CZK} Kč on your ČÚZK account."})


def _throttled(retry_after_s: int) -> str:
    return ("Too many uses of this ČÚZK login were not accepted, so nothing was sent to ČÚZK and nothing was "
            f"charged. Try again in about {max(1, round(retry_after_s / 60))} minutes; if your ČÚZK password "
            "changed, enter it again first (account menu, ČÚZK login).")


def _state(account_id: uuid.UUID, property_id: int) -> dict[str, Any]:
    with deps.open_background_conn() as conn:
        try:
            building = cadastre_prices.building_of(conn, property_id)
        except cadastre_prices.NoProperty:
            raise HTTPException(status_code=404, detail=_NO_PROPERTY) from None
        except cadastre_prices.NoBuilding:
            return {"available": False, "price_czk": cadastre_prices.PRICE_CZK, "purchase": None, "deeds": [],
                    "login_set": cuzk_login.state(conn, account_id)["set"]}
        return {"available": True, "login_set": cuzk_login.state(conn, account_id)["set"],
                **cadastre_prices.state(conn, account_id, building.code)}


@router.get("/properties/{property_id}/building/prices")
def get_building_prices(property_id: int,
                        account_id: uuid.UUID = Depends(tenant_pool.require_account_id)) -> dict[str, Any]:
    """The caller's own purchase and deeds for the property's building; `available` false when it has none."""
    return _state(account_id, property_id)


@router.post("/properties/{property_id}/building/prices")
def post_building_prices(
    property_id: int,
    body: Any = Body(),
    claims: dict = Depends(deps.verify_jwt),
    act_as: uuid.UUID | None = Depends(tenant_pool.act_as_header),
) -> dict[str, Any]:
    """Buy (or resume) the caller's price report for the property's building, then answer as GET does."""
    account_id = tenant_pool.own_account_id_released(claims, act_as, "POST")
    _price_ack(body)
    try:
        cadastre_prices.buy(account_id, property_id, opener=deps.open_background_conn,
                            repeat=body.get("repeat") is True)
    except cadastre_prices.NoProperty:
        raise HTTPException(status_code=404, detail=_NO_PROPERTY) from None
    except cadastre_prices.NoBuilding:
        raise HTTPException(status_code=422, detail=_NO_BUILDING) from None
    except cadastre_prices.AlreadyBought as exc:
        raise HTTPException(status_code=409, detail={
            "reason": "already_bought", "price_czk": cadastre_prices.PRICE_CZK,
            "bought_at": exc.finished_at.isoformat() if exc.finished_at else None,
            "message": f"You already hold this building's cadastre prices. A new report costs another "
                       f"{cadastre_prices.PRICE_CZK} Kč on your ČÚZK account."}) from None
    except cuzk_login.Throttled as exc:
        LOG.warning("CADASTRE price request throttled by=%s retry_after_s=%d", exc.by, exc.retry_after_s)
        raise HTTPException(status_code=429, detail=_throttled(exc.retry_after_s),
                            headers={"Retry-After": str(exc.retry_after_s)}) from None
    except cadastre_prices.NoLogin:
        raise HTTPException(status_code=409, detail=_NO_LOGIN) from None
    except cuzk_login.Unreadable:
        raise HTTPException(status_code=409, detail=_UNREADABLE) from None
    except cuzk_login.NotConfigured:
        raise HTTPException(status_code=503, detail="ČÚZK logins are not configured on this server.") from None
    except cadastre_prices.TooManyUnits:
        raise HTTPException(status_code=422, detail=_TOO_MANY) from None
    except cadastre_prices.StillPreparing:
        raise HTTPException(status_code=202, detail=_PREPARING) from None
    except wsdp_client.WsdpError as exc:
        LOG.warning("CADASTRE price request failed reason=%s status=%s", exc.reason, exc.status)
        if exc.reason == "refused":
            raise HTTPException(status_code=422, detail=_REFUSED) from None
        raise HTTPException(status_code=502, detail=_NO_ANSWER) from None
    return _state(account_id, property_id)
