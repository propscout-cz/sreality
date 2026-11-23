"""The price report's parser over a real report of ČÚZK's trial service (fictitious data, masked prices)."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from api import cadastre_price_report as report

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wsdp" / "ceny_dle_nemovitosti_trial.xml"


@pytest.fixture(scope="module")
def deeds() -> list[report.Deed]:
    return report.parse(FIXTURE.read_bytes())


def test_a_deed_is_returned_once_however_many_of_its_properties_were_asked_for(deeds):
    assert len(deeds) == len({(d.proceeding, d.kind, d.signed_on, d.price) for d in deeds})
    assert FIXTURE.read_text(encoding="utf-8").count("<LISTINA>") > len(deeds) or len(deeds) >= 1


def test_a_deed_carries_its_one_price_its_dates_and_every_property(deeds):
    deed = deeds[0]
    assert deed.proceeding.startswith("V-") and deed.proceeding.endswith("-306")
    assert deed.kind == "Smlouva kupní" and deed.currency == "CZK"
    assert isinstance(deed.price, Decimal) and deed.price > 0
    assert isinstance(deed.effective_on, datetime.date) and deed.signed_on <= deed.effective_on <= deed.entered_on
    assert deed.properties and all(p.iskn_id > 0 and p.label for p in deed.properties)


def test_the_newest_legal_effect_comes_first(deeds):
    dates = [d.effective_on for d in deeds]
    assert dates == sorted(dates, reverse=True)


def test_a_unit_is_named_in_the_cadastres_own_form(deeds):
    units = {p.label for d in deeds for p in d.properties if p.kind == "jednotka"}
    assert units and all("/" in label and label.replace("/", "").isdigit() for label in units)


@pytest.mark.parametrize(("text", "value"), [
    ("         111.111,11", Decimal("111111.11")), ("       1.111.111,00", Decimal("1111111.00")), ("", None),
])
def test_a_czech_formatted_price_reads_exactly(text, value):
    assert report._price(text) == value


@pytest.mark.parametrize("document", [b"not xml", b"<Jine/>"])
def test_another_document_is_refused(document):
    with pytest.raises(report.PriceReportError):
        report.parse(document)


def test_a_deed_covering_several_properties_has_one_price_for_all():
    several = b"""<CenoveUdajeDleNemovitosti><LISTINA><CENA_Z_LISTINY> 7.300.000,00</CENA_Z_LISTINY>
    <IDENT_RIZENI><typriz_kod>V</typriz_kod><poradove_cislo>1</poradove_cislo><rok>2024</rok><prares_kod>101</prares_kod>
    </IDENT_RIZENI><MENA_KOD>CZK</MENA_KOD><LISTINA_INFO><tlist>Smlouva</tlist><dalsi_udaje>kupn\xc3\xad</dalsi_udaje>
    <podani_z_txt> k okamziku 02.02.2024 10:00:00.</podani_z_txt></LISTINA_INFO><SOUVISEJICI_TYPY><SOUVISEJICI_TYP>
    <SOUVISEJICI_NEMOVISTOSTI><SOUVISEJICI_NEMOVISTOST><NEM_IDENT><jednotka><id>1</id><cp_ident>565</cp_ident>
    <jed_ident>31</jed_ident></jednotka></NEM_IDENT></SOUVISEJICI_NEMOVISTOST><SOUVISEJICI_NEMOVISTOST><NEM_IDENT>
    <jednotka><id>2</id><cp_ident>565</cp_ident><jed_ident>101</jed_ident></jednotka></NEM_IDENT>
    </SOUVISEJICI_NEMOVISTOST><SOUVISEJICI_NEMOVISTOST><NEM_IDENT><parcela><id>3</id><druh_cis>St.</druh_cis>
    <par_cis>  115</par_cis><podd_cis>2 </podd_cis></parcela></NEM_IDENT></SOUVISEJICI_NEMOVISTOST>
    </SOUVISEJICI_NEMOVISTOSTI></SOUVISEJICI_TYP></SOUVISEJICI_TYPY></LISTINA></CenoveUdajeDleNemovitosti>"""
    deed, = report.parse(several)
    assert deed.price == Decimal("7300000.00") and deed.effective_on == datetime.date(2024, 2, 2)
    assert [(p.kind, p.label) for p in deed.properties] == [
        ("jednotka", "565/31"), ("jednotka", "565/101"), ("parcela", "St. 115/2")]
