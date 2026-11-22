"""The WSDP client's unit search and report steps, over real answers of ČÚZK's trial service (fictitious data)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from api import wsdp_client as w

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "wsdp"
ERROR = ('<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body><a:X xmlns:a="u" '
         'xmlns:c="urn:cz:gov:cuzk:iskn:types:common:3.1"><a:vysledek><c:zprava kod="505" uroven="CHYBA">x'
         "</c:zprava></a:vysledek></a:X></S:Body></S:Envelope>")


class _Resp:
    def __init__(self, body: bytes | str, status: int = 200) -> None:
        self.status_code, self.content = status, body if isinstance(body, bytes) else body.encode()


@pytest.fixture()
def sent(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {"answer": b""}

    def post(url: str, **kw: Any) -> _Resp:
        box.update(url=url, data=kw["data"].decode(), headers=kw["headers"])
        return _Resp(box["answer"])

    monkeypatch.delenv(w.BASE_ENV, raising=False)
    monkeypatch.setattr(w.requests, "post", post)
    return box


def test_a_buildings_units_come_with_ids_and_the_cadastres_labels(sent):
    sent["answer"] = (FIX / "najdiJednotku_stavba311151306.resp.xml").read_bytes()
    units = w.find_units(311151306, "u", "p")
    assert len(units) == 38 and units[0] == w.Unit(18866306, "133/19")
    assert sent["url"].endswith("/vyhledat") and "<typ:stavbaId>311151306</typ:stavbaId>" in sent["data"]
    assert sent["headers"]["SOAPAction"].endswith(':vyhledat:NajdiJednotku"')


@pytest.mark.parametrize(("packed", "label"), [("1330019", "133/19"), ("50001", "5/1"), ("12", "12"), ("", "")])
def test_the_packed_unit_number_unpacks(packed, label):
    assert w.unit_label(packed) == label


def test_ordering_a_report_names_every_unit_once_and_reads_its_id(sent):
    sent["answer"] = (FIX / "cenyNem_bldg311151306_1gen.resp.xml").read_bytes()
    report = w.generate_unit_prices([1, 2, 3], "u", "p")
    assert report == w.Report(222936340011, "čeká", None)
    assert sent["data"].count("<typ:seznamObjektu>") == 3 and "<typ:seznamObjektuTyp>JED_S<" in sent["data"]


@pytest.mark.parametrize("count", [0, 201])
def test_a_report_takes_one_to_two_hundred_units(sent, count):
    with pytest.raises(ValueError):
        w.generate_unit_prices(list(range(count)), "u", "p")
    assert "url" not in sent, "nothing is sent"


def test_a_finished_report_states_its_price(sent):
    sent["answer"] = (FIX / "cenyNem_bldg311151306_2poll_222936340011.resp.xml").read_bytes()
    assert w.report_state(222936340011, "u", "p") == w.Report(222936340011, "zpracován", 50)


def test_a_fetched_report_is_its_decoded_file(sent):
    poll = (FIX / "cenyNem_bldg311151306_2poll_222936340011.resp.xml").read_text(encoding="utf-8")
    file = base64.b64encode(b"<CenoveUdajeDleNemovitosti/>").decode()
    sent["answer"] = poll.replace("</ns0:report>", f"<ns0:souborSestavy>{file}</ns0:souborSestavy></ns0:report>")
    report, body = w.fetch_report(222936340011, "u", "p")
    assert report.price_czk == 50 and body == b"<CenoveUdajeDleNemovitosti/>"


def test_a_refusal_inside_a_200_answer_is_an_error(sent):
    sent["answer"] = ERROR
    with pytest.raises(w.WsdpError) as refused:
        w.generate_unit_prices([1], "u", "p")
    assert (refused.value.reason, refused.value.status) == ("status", 505)
