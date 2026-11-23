"""ČÚZK's "Informace o jednotkách" report (XML) for one unit: what it states of the unit itself.

The report also names the unit's owners (name, address, birth number) and carries free text on seals and other
legal relations. Nothing here reads them, and `redact` builds the file we keep from ONLY the elements `parse`
reads, so whatever else the report holds, now or after ČÚZK changes its layout, is never stored (operator ruling
2026-11-23). The floor area is recorded by the cadastre only since 2024: for most units the element is empty or
absent, which is None here, never zero."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

ROOT = "InformaceOJednotkach"
# The children of JEDNOTKA that `parse` reads: the kept file holds these and nothing else.
_KEPT = ("ZP_VYUZITI", "CISLO_JED", "VYUZ_TYP", "BUD_PODIL", "PODLAHOVA_PLOCHA")
# Tags only an owner record carries (base.xsd, OpsubIdentType): a second check that none survived.
_PERSONAL = frozenset({
    "oprav_subjekt", "rc6", "rc7", "ico", "ico_doplnek", "prijmeni", "jmeno", "titul_pred", "titul_za", "adresa",
    "bsm1_prijmeni", "bsm1_jmeno", "bsm2_prijmeni", "bsm2_jmeno", "opravnena_po", "VLASTNICTVI", "SUBJEKT"})


class UnitReportError(ValueError):
    """The document is not the report this module reads."""


@dataclass(frozen=True)
class UnitFacts:
    iskn_id: int
    label: str  # "<č.p.>/<unit number>", as a deed names it
    floor_area_m2: Decimal | None
    use_code: str | None   # ZP_VYUZITI, the cadastre's "způsob využití jednotky" code
    use_label: str | None  # VYUZ_TYP, the report's own short text ("byt byt.z.")
    share_num: int | None  # the unit's share of the building's common parts
    share_den: int | None


def _root(document: bytes) -> ET.Element:
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        raise UnitReportError("not XML") from None
    if root.tag != ROOT:
        raise UnitReportError(f"not a unit report: {root.tag}")
    return root


def redact(document: bytes) -> bytes:
    """A document of the same shape holding only what `parse` reads of each unit. Raises when the input is not
    the report, or when a personal field would survive: such a file is not kept."""
    kept = ET.Element(ROOT)
    for unit in _root(document).iter("JEDNOTKA"):
        copy = ET.SubElement(kept, "JEDNOTKA")
        copy.extend(child for child in unit if child.tag in _KEPT)
    left = sorted({el.tag for el in kept.iter()} & _PERSONAL)
    if left:
        raise UnitReportError(f"personal fields among the kept ones: {left}")
    return ET.tostring(kept, encoding="utf-8")


def _text(el: ET.Element | None, path: str) -> str:
    found = el.find(path) if el is not None else None
    return (found.text or "").strip() if found is not None else ""


def _int(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def _area(text: str) -> Decimal | None:
    cleaned = text.replace(" ", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise UnitReportError("unreadable floor area") from None


def parse(document: bytes) -> UnitFacts:
    """The one unit of the report. A report over no unit, or several, is not what was ordered."""
    units = list(_root(document).iter("JEDNOTKA"))
    if len(units) != 1:
        raise UnitReportError(f"{len(units)} units in a report ordered for one")
    unit = units[0]
    ident = unit.find("CISLO_JED/jednotka")
    iskn_id = _text(ident, "id")
    if not iskn_id.isdigit():
        raise UnitReportError("a unit without its id")
    return UnitFacts(
        iskn_id=int(iskn_id),
        label=f"{_text(ident, 'cp_ident')}/{_text(ident, 'jed_ident')}",
        floor_area_m2=_area(_text(unit, "PODLAHOVA_PLOCHA")),
        use_code=_text(unit, "ZP_VYUZITI") or None,
        use_label=_text(unit, "VYUZ_TYP") or None,
        share_num=_int(_text(unit, "BUD_PODIL/citatel")),
        share_den=_int(_text(unit, "BUD_PODIL/jmenovatel")),
    )
