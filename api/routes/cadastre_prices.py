"""Cadastre prices on request. Mounted at `/properties/{id}/building/prices`.

GET answers the caller's own purchase and deeds for the property's building, from our database alone: it never
calls ČÚZK. POST acts, on the caller's own stored ČÚZK login, and only with the price acknowledged in the body:
`{"cost_czk": 50}` starts a new purchase, `{"cost_czk": 50, "repeat": true}` a new one of a building already paid
for, and `{"cost_czk": 50, "resume": <purchase id>}` continues that one purchase and can never start another;
`{"cancel": <purchase id>}` gives a purchase in flight up and sends nothing, so it needs no price. Both
are the caller's OWN account (never acted for); a bought price is visible to no other account. POST resolves the
account in a transaction of its own (`own_account_id_released`) because it then waits on ČÚZK.

The card these answer is Czech, so every text a user can be shown here is Czech, and each says truthfully whether
money moved.
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

_WHERE = "(menu účtu → ČÚZK login)"
_NO_PROPERTY = "Nemovitost nenalezena"
_NO_BUILDING = "Katastr u adresy této nemovitosti žádnou budovu neeviduje."
_NO_LOGIN = f"Nejprve uložte své přihlášení k ČÚZK {_WHERE}."
_UNREADABLE = f"Uložené přihlášení k ČÚZK už nelze přečíst. Zadejte ho znovu {_WHERE}. Nic nebylo odesláno ani zaúčtováno."
_LOGIN_REFUSED = (f"ČÚZK vaše uložené přihlášení odmítl. Další pokusy na něj proto neodesíláme, aby se váš účet u "
                  f"ČÚZK nezablokoval. Uložte ho znovu {_WHERE}. Tento pokus nic nezaúčtoval.")
_REFUSED_NOW = (f"ČÚZK vaše uložené přihlášení právě odmítl. Tento pokus nic nezaúčtoval. Uložte přihlášení znovu "
                f"{_WHERE} a pak pokračujte.")
_NO_ANSWER = ("ČÚZK neodpověděl nebo odpověděl chybou. Tento pokus nic nezaúčtoval. Než to zkusíte znovu, počkejte "
              "několik minut: druhý neúspěšný pokus krátce po prvním pozastaví v naší aplikaci vaše přihlášení k ČÚZK "
              "až na den (váš účet u ČÚZK tím zablokován není).")
_TOO_MANY = "Tato budova má více než 200 jednotek, což jeden výstup zatím nepokryje. Nic nebylo objednáno."
_GONE_UNCHARGED = ("ČÚZK objednanou sestavu už nenabízí. Nebyla vyzvednuta, takže nebylo nic zaúčtováno. Ceny můžete "
                   "načíst znovu.")
_GONE_MAYBE_CHARGED = ("ČÚZK tuto sestavu už nenabízí (je smazaná). Byla u něj vyžádána, takže mohla být zaúčtována; "
                       "ověříte to ve vyúčtování svého účtu u ČÚZK. Zde ji lze získat jen novým placeným načtením.")
_UNCERTAIN = ("Sestava byla u ČÚZK vyžádána, ale odpověď nedorazila, takže MOHLA být zaúčtována. Nezačínejte nový "
              "nákup: za několik minut zvolte Dokončit a načte se tatáž sestava bez dalšího poplatku (ČÚZK účtuje jen "
              "první otevření).")
_PAID_UNREAD = ("ČÚZK sestavu vydal a zaúčtoval, ale nepodařilo se ji přečíst. Je u nás uložená; přečíst ji lze "
                "zkusit znovu bez placení a nic dalšího se neúčtuje.")
_NOT_RESUMABLE = ("Toto načtení už není rozpracované, není tedy co dokončit ani zrušit. Nic nebylo odesláno ani "
                  "zaúčtováno.")
_NO_KEY = "Přihlášení k ČÚZK nejsou na tomto serveru nastavena."


def _request(body: Any) -> tuple[bool, int | None]:
    """(repeat, resume) of a body that acknowledges the price; anything else is refused before anything starts."""
    price = cadastre_prices.PRICE_CZK
    if not isinstance(body, dict) or body.get("cost_czk") != price or isinstance(body.get("cost_czk"), bool):
        raise HTTPException(status_code=409, detail={
            "reason": "price_not_acknowledged", "price_czk": price,
            "message": f"Tento požadavek stojí {price} Kč na vašem účtu u ČÚZK a musí být potvrzen s touto cenou."})
    repeat, resume = body.get("repeat") is True, body.get("resume")
    if resume is not None and (isinstance(resume, bool) or not isinstance(resume, int) or repeat):
        raise HTTPException(status_code=422, detail="Neplatný požadavek: dokončit lze jen jedno určené načtení.")
    return repeat, resume


def _throttled(exc: cuzk_login.Throttled) -> str:
    wait = (f"{-(-exc.retry_after_s // 3600)} h" if exc.retry_after_s >= 3600
            else f"{max(1, round(exc.retry_after_s / 60))} min")
    why = ("toto přihlašovací jméno k ČÚZK nebylo při posledních pokusech přijato" if exc.by == "username"
           else "tento účet použil své přihlášení k ČÚZK v poslední hodině příliš často")
    return (f"Na ČÚZK nebylo nic odesláno a nic nebylo zaúčtováno: {why}. Další pokusy zatím neodesíláme, aby se váš "
            f"účet u ČÚZK nezablokoval. Zkuste to znovu asi za {wait}.")


def _login(conn: Any, account_id: uuid.UUID) -> dict[str, bool]:
    login = cuzk_login.state(conn, account_id)
    return {"login_set": login["set"], "login_refused": login["refused"]}


def _state(account_id: uuid.UUID, property_id: int) -> dict[str, Any]:
    with deps.open_background_conn() as conn:
        try:
            building = cadastre_prices.building_of(conn, property_id)
        except cadastre_prices.NoProperty:
            raise HTTPException(status_code=404, detail=_NO_PROPERTY) from None
        except cadastre_prices.NoBuilding:
            return {"available": False, "price_czk": cadastre_prices.PRICE_CZK, "running": False, "purchase": None,
                    "deeds": [], **_login(conn, account_id)}
        return {"available": True, **_login(conn, account_id),
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
    """Start, continue or give up the caller's purchase of the building's price report, then answer as GET does,
    plus `notice`: null when the request did what it asked, else why it did not (`InProgress.why`): the state is
    then as it stands, and the card says which."""
    account_id = tenant_pool.own_account_id_released(claims, act_as, "POST")
    cancel = body.get("cancel") if isinstance(body, dict) else None
    if cancel is not None and (isinstance(cancel, bool) or not isinstance(cancel, int) or len(body) != 1):
        raise HTTPException(status_code=422, detail="Neplatný požadavek: zrušit lze jen jedno určené načtení.")
    repeat, resume = (False, None) if cancel is not None else _request(body)
    notice: str | None = None
    try:
        if cancel is not None:
            cadastre_prices.cancel(account_id, property_id, cancel, opener=deps.open_background_conn)
        else:
            cadastre_prices.buy(account_id, property_id, opener=deps.open_background_conn, repeat=repeat,
                                resume=resume)
    except cadastre_prices.InProgress as exc:
        notice = exc.why  # nothing was done; the state as it stands, and why, so the card can say so
    except cadastre_prices.NoProperty:
        raise HTTPException(status_code=404, detail=_NO_PROPERTY) from None
    except cadastre_prices.NoBuilding:
        raise HTTPException(status_code=422, detail=_NO_BUILDING) from None
    except cadastre_prices.AlreadyBought as exc:
        raise HTTPException(status_code=409, detail={
            "reason": "already_bought", "price_czk": cadastre_prices.PRICE_CZK,
            "bought_at": exc.finished_at.isoformat() if exc.finished_at else None,
            "message": f"Za ceny této budovy z katastru jste už platili nebo o ně žádali. Nové načtení stojí dalších "
                       f"{cadastre_prices.PRICE_CZK} Kč na vašem účtu u ČÚZK a je třeba ho potvrdit."}) from None
    except cadastre_prices.NotResumable:
        raise HTTPException(status_code=409, detail={"reason": "not_resumable", "message": _NOT_RESUMABLE}) from None
    except cadastre_prices.PriceChanged as exc:
        stated = "žádnou cenu" if exc.stated_czk is None else f"cenu {exc.stated_czk} Kč"
        raise HTTPException(status_code=409, detail={
            "reason": "price_changed", "price_czk": exc.stated_czk,
            "message": f"ČÚZK u této sestavy uvádí {stated}, ne {cadastre_prices.PRICE_CZK} Kč, které jste "
                       "potvrdili. Sestava proto nebyla vyzvednuta a nic nebylo zaúčtováno."}) from None
    except cuzk_login.Throttled as exc:
        LOG.warning("CADASTRE price request throttled by=%s retry_after_s=%d", exc.by, exc.retry_after_s)
        raise HTTPException(status_code=429, detail=_throttled(exc),
                            headers={"Retry-After": str(exc.retry_after_s)}) from None
    except cadastre_prices.NoLogin:
        raise HTTPException(status_code=409, detail=_NO_LOGIN) from None
    except cadastre_prices.LoginRefused:
        raise HTTPException(status_code=409, detail=_LOGIN_REFUSED) from None
    except cuzk_login.Unreadable:
        raise HTTPException(status_code=409, detail=_UNREADABLE) from None
    except cuzk_login.NotConfigured:
        raise HTTPException(status_code=503, detail=_NO_KEY) from None
    except cadastre_prices.TooManyUnits:
        raise HTTPException(status_code=422, detail=_TOO_MANY) from None
    except cadastre_prices.ReportGone as exc:
        raise HTTPException(status_code=409,
                            detail=_GONE_MAYBE_CHARGED if exc.possibly_charged else _GONE_UNCHARGED) from None
    except cadastre_prices.ChargeUncertain:
        LOG.error("CADASTRE charging fetch sent, answer lost: account=%s property=%s", account_id, property_id)
        raise HTTPException(status_code=502, detail=_UNCERTAIN) from None
    except cadastre_prices.PaidUnread:
        LOG.error("CADASTRE report charged and not read: account=%s property=%s", account_id, property_id)
        raise HTTPException(status_code=502, detail=_PAID_UNREAD) from None
    except wsdp_client.WsdpError as exc:
        LOG.warning("CADASTRE price request failed reason=%s status=%s", exc.reason, exc.status)
        if exc.reason == "refused":
            raise HTTPException(status_code=422, detail=_REFUSED_NOW) from None
        raise HTTPException(status_code=502, detail=_NO_ANSWER) from None
    return {**_state(account_id, property_id), "notice": notice}
