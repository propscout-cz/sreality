"""The WSDP client's unit search and report steps, over real answers of ČÚZK's trial service (fictitious data)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from api import wsdp_client as w

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "wsdp"
ERROR = ('<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body><a:X xmlns:a="u" '
         'xmlns:c="urn:cz:gov:cuzk:iskn:types:common:3.1"><a:vysledek><c:zprava kod="505" uroven="CHYBA">x'
         "</c:zprava></a:vysledek></a:X></S:Body></S:Envelope>")


@pytest.fixture()
def sent(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {"answer": b""}

    def transport(url: str, data: bytes, headers: dict, given: Any) -> tuple[int, bytes]:
        box.update(url=url, data=data.decode(), headers=headers)
        answer = box["answer"]
        return 200, answer if isinstance(answer, bytes) else answer.encode()

    monkeypatch.delenv(w.BASE_ENV, raising=False)
    monkeypatch.setattr(w, "_transport", transport)
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
    report = w.generate_prices("JED_S", [1, 2, 3], "u", "p")
    assert report == w.Report(222936340011, "čeká", None)
    assert sent["data"].count("<typ:seznamObjektu>") == 3 and "<typ:seznamObjektuTyp>JED_S<" in sent["data"]


@pytest.mark.parametrize("count", [0, 201])
def test_a_report_takes_one_to_two_hundred_units(sent, count):
    with pytest.raises(ValueError):
        w.generate_prices("JED_S", list(range(count)), "u", "p")
    assert "url" not in sent, "nothing is sent"


def test_a_finished_report_states_its_price(sent):
    sent["answer"] = (FIX / "cenyNem_bldg311151306_2poll_222936340011.resp.xml").read_bytes()
    assert w.report_state(222936340011, "u", "p") == w.Report(222936340011, "zpracován", 50)


def test_a_fetched_report_is_its_decoded_file_and_reads_as_deeds(sent):
    """The answer as the trial service gave it (recorded 2026-11-22), not one built by this test."""
    from api import cadastre_price_report

    sent["answer"] = (FIX / "cenyNem_bldg311151306_3fetch.resp.xml").read_bytes()
    report, body = w.fetch_report(222937329011, "u", "p")
    assert (report.id, report.state, report.price_czk) == (222937329011, "zaúčtován", 50)
    deeds = cadastre_price_report.parse(body)
    assert deeds and all(d.proceeding and d.properties for d in deeds)


def test_a_report_file_that_is_not_base64_is_an_error_after_the_login_was_accepted(sent):
    poll = (FIX / "cenyNem_bldg311151306_2poll_222936340011.resp.xml").read_text(encoding="utf-8")
    sent["answer"] = poll.replace("</ns0:report>", "<ns0:souborSestavy>%%%</ns0:souborSestavy></ns0:report>")
    with pytest.raises(w.WsdpError) as unreadable:
        w.fetch_report(222936340011, "u", "p")
    assert unreadable.value.reason == "unreadable" and unreadable.value.authenticated


def test_a_refusal_inside_a_200_answer_is_an_error(sent):
    sent["answer"] = ERROR
    with pytest.raises(w.WsdpError) as refused:
        w.generate_prices("STA_S", [1], "u", "p")
    assert (refused.value.reason, refused.value.status) == ("service", 505)
    assert refused.value.authenticated, "ČÚZK accepted the login before it refused the request"


def test_only_units_or_buildings_can_be_priced(sent):
    with pytest.raises(ValueError):
        w.generate_prices("PAR_S", [1], "u", "p")
    assert "url" not in sent


def test_an_answer_without_the_services_own_message_proves_nothing_about_the_login(sent):
    """A proxy's page or a stub that parses as XML is not ČÚZK accepting the login (second review, s8)."""
    sent["answer"] = "<html><body>maintenance</body></html>"
    for call in (lambda: w.find_units(1, "u", "p"), lambda: w.report_state(1, "u", "p"),
                 lambda: w.generate_prices("STA_S", [1], "u", "p"), lambda: w.fetch_report(1, "u", "p")):
        with pytest.raises(w.WsdpError) as unreadable:
            call()
        assert unreadable.value.reason == "unreadable" and not unreadable.value.authenticated


def test_a_report_that_is_gone_is_error_304_and_nothing_else_is(sent):
    sent["answer"] = ERROR.replace('kod="505"', f'kod="{w.REPORT_GONE}"')
    with pytest.raises(w.WsdpError) as gone:
        w.report_state(1, "u", "p")
    assert (gone.value.reason, gone.value.status) == ("service", 304) and gone.value.authenticated


def test_a_call_ends_at_the_wall_clock_bound_and_the_request_is_ended_with_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fourth review's point 2: at the bound the caller stops waiting AND the request loses its socket, so
    nothing of ours is in flight afterwards. Each socket step has its own timeout, never their sum."""
    import threading
    import time

    shut, released = threading.Event(), threading.Event()

    class _Conn:
        sock = object()

    # The real shutdown, on a real TLS socket, is run in tests/api/test_wsdp_transport.py.
    monkeypatch.setattr(w, "_shutdown", lambda sock: shut.set() if sock is _Conn.sock else None)

    def trickle(url: str, data: bytes, headers: dict, given: Any) -> Any:
        assert given.may_send(_Conn())  # connected and sent; the answer then never ends
        shut.wait(30)
        released.set()
        raise w.WsdpError("timeout")

    monkeypatch.setattr(w, "_transport", trickle)
    monkeypatch.setattr(w, "CALL_MAX_S", 0.2)
    began = time.monotonic()
    with pytest.raises(w.WsdpError) as slow:
        w.report_state(1, "u", "p")
    assert slow.value.reason == "timeout" and time.monotonic() - began < 5
    assert shut.is_set() and released.wait(5), "the abandoned request's socket was shut, and it ended"


def test_a_request_the_caller_gave_up_on_before_it_was_sent_is_never_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fourth review's major, at its source: a request stuck before its send (a DNS lookup has no bound) must
    not leave after the caller has stamped it as unanswered."""
    import threading

    connected, sent = threading.Event(), []

    def stuck_in_dns(url: str, data: bytes, headers: dict, given: Any) -> Any:
        connected.wait(30)  # the lookup returns only after the caller gave up
        if given.may_send(object()):
            sent.append(url)
        raise w.WsdpError("timeout")

    monkeypatch.setattr(w, "_transport", stuck_in_dns)
    monkeypatch.setattr(w, "CALL_MAX_S", 0.2)
    with pytest.raises(w.WsdpError):
        w.report_state(1, "u", "p")
    connected.set()
    import time
    time.sleep(0.3)
    assert sent == []


def test_what_the_request_thread_raises_reaches_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, data: bytes, headers: dict, given: Any) -> Any:
        raise w.WsdpError("unreachable")

    monkeypatch.setattr(w, "_transport", refuse)
    with pytest.raises(w.WsdpError) as down:
        w.report_state(1, "u", "p")
    assert down.value.reason == "unreachable"


def test_the_login_check_keeps_the_read_timeout_main_has(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ČÚZK that answers in 12 s must not fail a save that succeeds on main."""
    assert w.TIMEOUT == (5, 20) and w.CALL_MAX_S > sum(w.TIMEOUT)


def test_the_server_is_verified_against_the_bundle_requests_used() -> None:
    """The fifth pass: a hand-written HTTPS layer must not depend on which CA store an image carries."""
    import ssl

    import certifi

    context = w.tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    loaded = context.get_ca_certs()
    assert len(loaded) > 50, "certifi's bundle is loaded, not an empty store"
    from pathlib import Path
    assert Path(certifi.where()).is_file()


def test_a_units_information_is_ordered_for_exactly_that_unit(sent):
    sent["answer"] = (FIX / "cenyNem_bldg311151306_1gen.resp.xml").read_bytes()  # every order answers alike
    assert w.generate_unit_info(18866306, "u", "p") == w.Report(222936340011, "čeká", None)
    assert sent["url"].endswith("/sestavy") and sent["headers"]["SOAPAction"].endswith(':sestavy:GenerujInfoOJednotkach"')
    assert ("<typ:GenerujInfoOJednotkachRequest><typ:jednotkaId>18866306</typ:jednotkaId><typ:format>xml</typ:format>"
            "</typ:GenerujInfoOJednotkachRequest>") in sent["data"]
