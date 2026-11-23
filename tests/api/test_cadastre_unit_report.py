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


def _area_doc(element: str) -> bytes:
    return _doc().replace(b"<PODLAHOVA_PLOCHA></PODLAHOVA_PLOCHA>", element.encode())


def test_the_unit_is_read_with_its_use_and_share():
    facts = report.parse(_doc())
    assert (facts.iskn_id, facts.label, facts.use_code, facts.use_label, facts.share_num, facts.share_den) == (
        1005719101, "1161/10", "1", "byt byt.z.", 344, 35966)


@pytest.mark.parametrize(("stated", "read"), [("34.40", "34.40"), ("34,4", "34.4"), ("120", "120")])
def test_a_recorded_floor_area_is_read_as_stated(stated, read):
    facts = report.parse(_doc(area=stated))
    assert (facts.floor_area_m2, facts.area_state) == (Decimal(read), "stated")


@pytest.mark.parametrize(("element", "state"), [
    ("<PODLAHOVA_PLOCHA></PODLAHOVA_PLOCHA>", "empty"),  # there and blank: the cadastre records none
    ("<PODLAHOVA_PLOCHA/>", "empty"),
    ("", "unknown"),                                      # as in ČÚZK's published sample: no such element
    ("<PODLAHOVA_PLOCHA><m2>34</m2></PODLAHOVA_PLOCHA>", "unknown"),  # not a leaf: another layout than the schema's
    ("<PLOCHA>34</PLOCHA>", "unknown"),
])
def test_an_area_we_could_not_find_is_not_the_cadastres_statement(element, state):
    facts = report.parse(_area_doc(element))
    assert (facts.floor_area_m2, facts.area_state) == (None, state)


def test_the_reports_shape_is_its_element_names_and_nothing_else():
    shape = report.parse(_area_doc("<PLOCHA>34</PLOCHA>")).shape
    assert {"PLOCHA", "BUD_PODIL/citatel", "CISLO_JED/jednotka/id", "VLASTNICTVI/SUBJEKTY/SUBJEKT/VLA_IDENT/oprav_subjekt/rc6"} <= set(shape)
    assert not any(value in " ".join(shape) for value in ("Novotná", "755101", "344", "Dlouhá"))


def test_the_kept_file_holds_only_the_text_of_the_leaves_we_read_and_reads_the_same():
    full = _doc(area="34.40", stray="<VAZBA_JPV><TEXT>ve prospěch: Novotná</TEXT></VAZBA_JPV>").replace(
        b"<citatel>344</citatel>", b"<citatel kdo='Novotna'>344<oprav_subjekt>x</oprav_subjekt></citatel>ocas Novotna")
    kept = report.redact(full)
    for gone in (b"Novotn", b"755101", "Dlouhá".encode(), b"Praha", b"kdo=", b"ocas", b"1516"):
        assert gone not in kept
    assert report.parse(kept).shape == report.parse(full).shape, "the names survive, as names"
    plain = _doc(area="34.40")
    assert report.parse(report.redact(plain)) == report.parse(plain)
    assert report.redact(report.redact(plain)) == report.redact(plain), "cutting twice changes nothing"


def test_a_leaf_that_carries_children_is_not_copied():
    kept = report.redact(_doc().replace(b"<VYUZ_TYP>byt byt.z.</VYUZ_TYP>",
                                        b"<VYUZ_TYP>byt<prijmeni>Novotna</prijmeni></VYUZ_TYP>"))
    assert b"Novotna" not in kept and report.parse(kept).use_label is None


def test_an_error_never_quotes_the_document():
    with pytest.raises(report.UnitReportError) as raised:
        report.parse(_doc(area="Novotná"))
    assert "Novotná" not in str(raised.value)


@pytest.mark.parametrize("document", [b"not xml", b"<CenoveUdajeDleNemovitosti/>", _doc(units=0), _doc(units=2),
                                      _doc(area="velká")])
def test_anything_else_is_not_this_report(document):
    with pytest.raises(report.UnitReportError):
        report.parse(document)


@pytest.mark.parametrize("document", [b"not xml", b"<Jine><prijmeni>Novotna</prijmeni></Jine>"])
def test_what_is_not_the_report_is_not_cut_down(document):
    with pytest.raises(report.UnitReportError):
        report.redact(document)


def test_a_layout_we_did_not_expect_is_kept_as_its_names_so_a_fixed_parser_can_be_written():
    odd = "<InformaceOJednotkach><BYTY><BYT><ID>7</ID><VLASTNIK>Novotna</VLASTNIK></BYT></BYTY></InformaceOJednotkach>"
    kept = report.redact(odd.encode())
    assert b"<p>BYTY/BYT/VLASTNIK</p>" in kept and b"Novotna" not in kept and b">7<" not in kept
    with pytest.raises(report.UnitReportError):
        report.parse(kept)


def test_a_namespaced_report_reads_the_same():
    spaced = _doc(area="34.40").replace(b"<InformaceOJednotkach>", b"<InformaceOJednotkach xmlns='urn:x'>")
    assert report.parse(spaced) == report.parse(_doc(area="34.40"))
    assert report.parse(report.redact(spaced)) == report.parse(_doc(area="34.40"))
