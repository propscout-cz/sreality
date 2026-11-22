"""RÚIAN exchange format (VFR), per-obec state file: file -> building and address-link rows."""
from __future__ import annotations

import datetime
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

VFR_BASE = "https://vdp.cuzk.gov.cz/vymenny_format/soucasna"
OBEC_STATE_URL = VFR_BASE + "/{vintage}_OB_{obec}_UZSZ.xml.zip"

_FILE_TYPE = "OB_UZSZ"
_BATCH_TYPE = "Plna"
_VERSION_MAJOR = "3."


class VfrSchemaError(RuntimeError):
    """The file is not the product this parser reads."""


@dataclass(frozen=True)
class BuildingRow:
    code: int
    plati_od: datetime.date
    typ_so: int | None
    usage_code: int | None
    iskn_building_id: int | None
    identifying_parcel_id: int | None
    cast_obce_kod: int | None
    momc_kod: int | None
    house_numbers: tuple[int, ...]
    completed_on: datetime.date | None
    construction_code: int | None
    floors: int | None
    dwelling_count: int | None
    built_up_area_m2: int | None
    floor_area_m2: int | None
    enclosed_volume_m3: int | None
    lift_code: int | None
    water_code: int | None
    sewer_code: int | None
    gas_code: int | None
    heating_code: int | None
    protection_codes: tuple[int, ...]


@dataclass(frozen=True)
class AddressLink:
    kod_adm: int
    building_code: int


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ET.Element) -> dict[str, ET.Element]:
    return {_local(c.tag): c for c in el}


def _int(el: ET.Element | None) -> int | None:
    text = (el.text or "").strip() if el is not None else ""
    return int(text) if text else None


def _date(el: ET.Element | None) -> datetime.date | None:
    text = (el.text or "").strip() if el is not None else ""
    return datetime.date.fromisoformat(text[:10]) if text else None


def _nested_code(el: ET.Element | None) -> int | None:
    if el is None:
        return None
    c = _children(el)
    return _int(c.get("Kod", c.get("Id")))


def _building(el: ET.Element) -> BuildingRow:
    c = _children(el)
    code, plati_od = _int(c.get("Kod")), _date(c.get("PlatiOd"))
    if code is None or plati_od is None:
        raise VfrSchemaError("StavebniObjekt without Kod or PlatiOd")
    numbers = c.get("CislaDomovni")
    protections = c.get("ZpusobyOchrany")
    return BuildingRow(
        code=code,
        plati_od=plati_od,
        typ_so=_int(c.get("TypStavebnihoObjektuKod")),
        usage_code=_int(c.get("ZpusobVyuzitiKod")),
        iskn_building_id=_int(c.get("IsknBudovaId")),
        identifying_parcel_id=_nested_code(c.get("IdentifikacniParcela")),
        cast_obce_kod=_nested_code(c.get("CastObce")),
        momc_kod=_nested_code(c.get("Momc")),
        house_numbers=tuple(n for n in (_int(e) for e in numbers) if n is not None) if numbers is not None else (),
        completed_on=_date(c.get("Dokonceni")),
        construction_code=_int(c.get("DruhKonstrukceKod")),
        floors=_int(c.get("PocetPodlazi")),
        dwelling_count=_int(c.get("PocetBytu")),
        built_up_area_m2=_int(c.get("ZastavenaPlocha")),
        floor_area_m2=_int(c.get("PodlahovaPlocha")),
        enclosed_volume_m3=_int(c.get("ObestavenyProstor")),
        lift_code=_int(c.get("VybaveniVytahemKod")),
        water_code=_int(c.get("PripojeniVodovodKod")),
        sewer_code=_int(c.get("PripojeniKanalizaceKod")),
        gas_code=_int(c.get("PripojeniPlynKod")),
        heating_code=_int(c.get("ZpusobVytapeniKod")),
        protection_codes=tuple(sorted({
            k for k in (_int(_children(p).get("Kod")) for p in protections) if k is not None
        })) if protections is not None else (),
    )


def _address_link(el: ET.Element) -> AddressLink | None:
    c = _children(el)
    kod, building = _int(c.get("Kod")), _nested_code(c.get("StavebniObjekt"))
    if kod is None:
        raise VfrSchemaError("AdresniMisto without Kod")
    return AddressLink(kod, building) if building is not None else None


def _check_header(header: dict[str, str], member: str) -> None:
    version = header.get("VerzeVFR", "")
    if (not version.startswith(_VERSION_MAJOR) or header.get("TypSouboru") != _FILE_TYPE
            or header.get("TypDavky") != _BATCH_TYPE):
        raise VfrSchemaError(f"{member}: not a full {_FILE_TYPE} file of VFR {_VERSION_MAJOR}x: {header}")


def iter_obec_state(zip_path: Path) -> Iterator[BuildingRow | AddressLink]:
    """Every building and every address point's building link of one obec's state file."""
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise VfrSchemaError(f"{zip_path.name}: expected one member, found {len(members)}")
        with archive.open(members[0]) as handle:
            header: dict[str, str] = {}
            checked = False
            depth = 0
            for event, el in ET.iterparse(handle, events=("start", "end")):
                if event == "start":
                    depth += 1
                    if depth == 2 and _local(el.tag) == "Data" and not checked:
                        _check_header(header, members[0])
                        checked = True
                    continue
                depth -= 1
                if depth == 2 and not checked and el.text and el.text.strip():
                    header[_local(el.tag)] = el.text.strip()
                elif depth == 3:
                    kind = _local(el.tag)
                    if kind == "StavebniObjekt":
                        yield _building(el)
                    elif kind == "AdresniMisto":
                        link = _address_link(el)
                        if link is not None:
                            yield link
                    el.clear()
            if not checked:
                raise VfrSchemaError(f"{members[0]}: no Data element")
