"""ČÚZK's "Informace o jednotkách" report (XML) for one unit: what it states of the unit itself.

The report also names the unit's owners (name, address, birth number) and carries free text on seals and other
legal relations. Nothing here reads them, and `redact` builds the file we keep from new elements holding ONLY the
text of the leaves `parse` reads, plus the report's element NAMES (no text, no attributes), so whatever else the
report holds, now or after ČÚZK changes its layout, is never stored (operator ruling 2026-11-23).

The floor area is recorded by the cadastre only since 2024, and ČÚZK's published sample of this report has no
floor-area element at all. So three answers are kept apart (`area_state`): `stated` (a number), `empty` (the
element is there and blank: the cadastre records none), and `unknown` (no such leaf where the schema puts it: we
could not tell, which is NOT the cadastre's statement). `shape` is every element path under the unit, names only:
what one bought report shows of the real layout."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

ROOT = "InformaceOJednotkach"
_AREA = "PODLAHOVA_PLOCHA"
# The leaves `parse` reads, as paths under JEDNOTKA: the kept file holds the text of these and nothing else.
_LEAVES = ("ZP_VYUZITI", "CISLO_JED/jednotka/id", "CISLO_JED/jednotka/cp_ident", "CISLO_JED/jednotka/jed_ident",
           "VYUZ_TYP", "BUD_PODIL/citatel", "BUD_PODIL/jmenovatel", _AREA)
_SHAPE = "SHAPE"  # in a kept file only: the original report's element paths
MAX_SHAPE = 300


class UnitReportError(ValueError):
    """The document is not the report this module reads."""


@dataclass(frozen=True)
class UnitFacts:
    iskn_id: int
    label: str  # "<č.p.>/<unit number>", as a deed names it
    floor_area_m2: Decimal | None
    area_state: str        # stated | empty | unknown
    use_code: str | None   # ZP_VYUZITI, the cadastre's "způsob využití jednotky" code
    use_label: str | None  # VYUZ_TYP, the report's own short text ("byt byt.z.")
    share_num: int | None  # the unit's share of the building's common parts
    share_den: int | None
    shape: tuple[str, ...]  # element paths under JEDNOTKA, names only


def _root(document: bytes) -> ET.Element:
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        raise UnitReportError("not XML") from None
    for el in root.iter():  # the published sample carries no namespace; one that did must read the same
        el.tag = el.tag.rsplit("}", 1)[-1]
    if root.tag != ROOT:
        raise UnitReportError("not a unit report")
    return root


def _leaf(unit: ET.Element, path: str) -> str | None:
    """The stripped text of a leaf; None when it is absent or is not a leaf."""
    found = unit.find(path)
    return None if found is None or len(found) else (found.text or "").strip()


def _paths(el: ET.Element, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for child in el:
        path = f"{prefix}{child.tag}"
        paths.add(path)
        paths |= _paths(child, f"{path}/")
    return paths


def _shape(root: ET.Element, unit: ET.Element | None) -> tuple[str, ...]:
    """The element paths under the unit; of the whole report when it holds no unit where we look for one, so a
    layout we did not expect is still kept as names."""
    kept = root.find(_SHAPE)
    paths = ({(p.text or "").strip() for p in kept} if kept is not None
             else _paths(unit) if unit is not None else _paths(root))
    return tuple(sorted(p for p in paths if p)[:MAX_SHAPE])


def redact(document: bytes) -> bytes:
    """A new document holding, of each unit, only the text of the leaves `parse` reads, and the element paths of
    the first unit (of the whole report when it holds none). Raises when the input is not the report: such a file is not kept."""
    root = _root(document)
    kept = ET.Element(ROOT)
    units = list(root.iter("JEDNOTKA"))
    for unit in units:
        copy = ET.SubElement(kept, "JEDNOTKA")
        for path in _LEAVES:
            text = _leaf(unit, path)
            if text is None:
                continue
            at = copy
            for name in path.split("/"):
                nxt = at.find(name)
                at = nxt if nxt is not None else ET.SubElement(at, name)
            at.text = text
    shape = ET.SubElement(kept, _SHAPE)
    for path in _shape(root, units[0] if units else None):
        ET.SubElement(shape, "p").text = path
    return ET.tostring(kept, encoding="utf-8")


def _int(text: str | None) -> int | None:
    return int(text) if text and text.isdigit() else None


def _area(text: str | None) -> tuple[Decimal | None, str]:
    if text is None:
        return None, "unknown"
    cleaned = text.replace(" ", "").replace(",", ".")
    if not cleaned:
        return None, "empty"
    try:
        return Decimal(cleaned), "stated"
    except InvalidOperation:
        raise UnitReportError("unreadable floor area") from None


def parse(document: bytes) -> UnitFacts:
    """The one unit of the report. A report over no unit, or several, is not what was ordered."""
    root = _root(document)
    units = list(root.iter("JEDNOTKA"))
    if len(units) != 1:
        raise UnitReportError(f"{len(units)} units in a report ordered for one")
    unit = units[0]
    iskn_id = _leaf(unit, "CISLO_JED/jednotka/id") or ""
    if not iskn_id.isdigit():
        raise UnitReportError("a unit without its id")
    area, area_state = _area(_leaf(unit, _AREA))
    return UnitFacts(
        iskn_id=int(iskn_id),
        label=f"{_leaf(unit, 'CISLO_JED/jednotka/cp_ident') or ''}/{_leaf(unit, 'CISLO_JED/jednotka/jed_ident') or ''}",
        floor_area_m2=area,
        area_state=area_state,
        use_code=_leaf(unit, "ZP_VYUZITI") or None,
        use_label=_leaf(unit, "VYUZ_TYP") or None,
        share_num=_int(_leaf(unit, "BUD_PODIL/citatel")),
        share_den=_int(_leaf(unit, "BUD_PODIL/jmenovatel")),
        shape=_shape(root, unit),
    )
