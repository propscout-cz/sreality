"""ČÚZK's "Informace o jednotkách" for one unit: what is read, and that the owners are cut out of what is kept.
The document has the shape of ČÚZK's published sample (services.cuzk.gov.cz, ukázky); the owner is invented."""

from __future__ import annotations

from decimal import Decimal

import pytest

from api import cadastre_unit_report as report


def _doc(*, area: str = "", owners: bool = True, stray: str = "", units: int = 1) -> bytes:
    owner = ("<VLASTNICTVI><LV><tel_id>2</tel_id><cislo_tel>1516</cislo_tel></LV><SUBJEKTY><SUBJEKT><VLA_IDENT>"
             "<oprav_subjekt><id>9</id><rc6>755101</rc6><rc7>1234</rc7><prijmeni>Novotná</prijmeni><jmeno>Jarmila"
             "</jmeno><adresa><nazev_ulice>Dlouhá</nazev_ulice></adresa></oprav_subjekt></VLA_IDENT><PODIL/>"
             "<TYPRAV_NAZEV>Vlastnické právo</TYPRAV_NAZEV></SUBJEKT></SUBJEKTY></VLASTNICTVI>") if owners else ""
    unit = ("<JEDNOTKA><ZP_VYUZITI>1</ZP_VYUZITI><CISLO_JED><jednotka><id>1005719101</id><caobce></caobce>"
            "<cp_ident>1161</cp_ident><jed_ident>10</jed_ident><tel_id>2261492101</tel_id></jednotka></CISLO_JED>"
            "<VYUZ_TYP>byt byt.z.</VYUZ_TYP><BUD_PODIL><citatel>344</citatel><jmenovatel>35966</jmenovatel></BUD_PODIL>"
            f"<PODLAHOVA_PLOCHA>{area}</PODLAHOVA_PLOCHA>{stray}{owner}<PARCELY/></JEDNOTKA>")
    return ("<?xml version='1.0' encoding='utf-8'?><InformaceOJednotkach><KATASTR><OBEC><kod>554782</kod><nazev>Praha"
            f"</nazev></OBEC><STAVBY><STAVBA><JEDNOTKY>{unit * units}</JEDNOTKY></STAVBA></STAVBY></KATASTR>"
            "<PLATNOST>k 23.11.2026</PLATNOST></InformaceOJednotkach>").encode()


def test_the_unit_is_read_with_its_use_and_share_and_an_unrecorded_area_is_unknown():
    assert report.parse(_doc()) == report.UnitFacts(
        iskn_id=1005719101, label="1161/10", floor_area_m2=None, use_code="1", use_label="byt byt.z.",
        share_num=344, share_den=35966)


@pytest.mark.parametrize(("stated", "read"), [("34.40", "34.40"), ("34,4", "34.4"), ("120", "120")])
def test_a_recorded_floor_area_is_read_as_stated(stated, read):
    assert report.parse(_doc(area=stated)).floor_area_m2 == Decimal(read)


def test_the_kept_file_holds_only_what_is_read_and_reads_the_same():
    kept = report.redact(_doc(area="34.40", stray="<VAZBA_JPV><TEXT>ve prospěch: Novotná</TEXT></VAZBA_JPV>"))
    for gone in (b"VLASTNICTVI", b"oprav_subjekt", b"755101", "Novotná".encode(), "Dlouhá".encode(), b"VAZBA_JPV",
                 b"PARCELY", b"Praha", b"PLATNOST"):
        assert gone not in kept
    assert report.parse(kept) == report.parse(_doc(area="34.40"))
    assert report.redact(kept) == kept, "cutting twice changes nothing"


@pytest.mark.parametrize("tag", sorted(report._PERSONAL - {"VLASTNICTVI", "SUBJEKT"}))
def test_a_personal_field_among_the_kept_ones_refuses_the_file(tag):
    inside = _doc().replace(b"<citatel>", f"<{tag}>x</{tag}><citatel>".encode())
    with pytest.raises(report.UnitReportError, match="personal"):
        report.redact(inside)


def test_an_error_never_quotes_the_document():
    with pytest.raises(report.UnitReportError) as raised:
        report.parse(_doc(area="Novotná"))
    assert "Novotná" not in str(raised.value)


@pytest.mark.parametrize("document", [b"not xml", b"<CenoveUdajeDleNemovitosti/>", _doc(units=0), _doc(units=2),
                                      _doc(area="velká")])
def test_anything_else_is_not_this_report(document):
    with pytest.raises(report.UnitReportError):
        report.parse(document)
