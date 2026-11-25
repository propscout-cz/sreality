"""ČÚZK's "Údaje o dosažených cenách nemovitostí podle nemovitostí" report (XML 1.0): report -> deeds.

One deed has ONE price for everything it transfers, never split per property, so a deed is the unit here and it
carries every property it covers. The report lists a deed once under each requested property it touches; the
same deed is returned once. Nothing in the report states an area or an owner."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET


class PriceReportError(ValueError):
    """The document is not the report this module reads."""


@dataclass(frozen=True)
class DeedProperty:
    kind: str  # jednotka | stavba | parcela | pravo_stavby
    iskn_id: int
    label: str  # the cadastre's own short form: "133/19", "č.p. 88", "St. 115", "3/1"


@dataclass(frozen=True)
class Deed:
    proceeding: str  # "V-2382/2017-306"
    kind: str  # "Smlouva kupní"
    price: Decimal | None
    currency: str | None
    signed_on: datetime.date | None
    effective_on: datetime.date | None  # "Právní účinky zápisu k okamžiku …": when the transfer took legal effect
    entered_on: datetime.date | None
    properties: tuple[DeedProperty, ...]


_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_KINDS = {"jednotka": "jednotka", "stavba": "stavba", "parcela": "parcela", "pravo_stavby": "pravo_stavby"}


def _text(el: ET.Element | None, path: str) -> str:
    found = el.find(path) if el is not None else None
    return (found.text or "").strip() if found is not None else ""


def _date(text: str) -> datetime.date | None:
    m = _DATE.search(text)
    return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _price(text: str) -> Decimal | None:
    cleaned = text.strip().replace(".", "").replace(" ", "").replace(" ", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise PriceReportError(f"unreadable price {text!r}") from None


def _label(kind: str, el: ET.Element) -> str:
    if kind == "jednotka":
        return f"{_text(el, 'cp_ident')}/{_text(el, 'jed_ident')}"
    if kind == "stavba":
        return " ".join(p for p in (_text(el, "typbud_zkr"), _text(el, "cisla_domovni")) if p)
    if kind == "parcela":
        number = _text(el, "par_cis") + (f"/{_text(el, 'podd_cis')}" if _text(el, "podd_cis") else "")
        return " ".join(p for p in (_text(el, "druh_cis"), number) if p)
    return _text(el, "id")


def _property(ident: ET.Element) -> DeedProperty | None:
    for child in ident:
        kind = _KINDS.get(child.tag)
        iskn_id = _text(child, "id")
        if kind and iskn_id.isdigit():
            return DeedProperty(kind, int(iskn_id), _label(kind, child))
    return None


def _deed(listina: ET.Element) -> Deed:
    r = listina.find("IDENT_RIZENI")
    proceeding = (f"{_text(r, 'typriz_kod')}-{_text(r, 'poradove_cislo')}/{_text(r, 'rok')}"
                  f"-{_text(r, 'prares_kod')}")
    if not _text(r, "poradove_cislo"):
        raise PriceReportError("a deed without its proceeding")
    info = listina.find("LISTINA_INFO")
    properties = tuple(sorted(
        {p for ident in listina.iterfind("SOUVISEJICI_TYPY//NEM_IDENT") if (p := _property(ident)) is not None},
        key=lambda p: (p.kind, p.iskn_id)))
    return Deed(
        proceeding=proceeding,
        kind=" ".join(p for p in (_text(info, "tlist"), _text(info, "dalsi_udaje")) if p),
        price=_price(_text(listina, "CENA_Z_LISTINY")),
        currency=_text(listina, "MENA_KOD") or None,
        signed_on=_date(_text(info, "vystav_txt")),
        effective_on=_date(_text(info, "podani_z_txt")),
        entered_on=_date(_text(info, "zplatneni_txt")),
        properties=properties,
    )


def parse(document: bytes) -> list[Deed]:
    """Every deed of the report, once, newest legal effect first."""
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        raise PriceReportError("not XML") from None
    if root.tag != "CenoveUdajeDleNemovitosti":
        raise PriceReportError(f"not a price report: {root.tag}")
    deeds = {deed: None for listina in root.iter("LISTINA") for deed in (_deed(listina),)}
    return sorted(deeds, key=lambda d: (d.effective_on or datetime.date.min, d.proceeding), reverse=True)
