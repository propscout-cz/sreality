import datetime
import pathlib
import zipfile

import pytest

from location_data import ruian_vfr

_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<vf:VymennyFormat xmlns:vf="urn:cz:isvs:ruian:schemas:VymennyFormatTypy:v1"
  xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:soi="urn:cz:isvs:ruian:schemas:StavObjIntTypy:v1"
  xmlns:ami="urn:cz:isvs:ruian:schemas:AdrMistoIntTypy:v1" xmlns:com="urn:cz:isvs:ruian:schemas:CommonTypy:v1"
  xmlns:pai="urn:cz:isvs:ruian:schemas:ParcelaIntTypy:v1">
<vf:Hlavicka><vf:VerzeVFR>3.1</vf:VerzeVFR><vf:TypZaznamu>Platne</vf:TypZaznamu>
<vf:TypDavky>{batch}</vf:TypDavky><vf:TypSouboru>{kind}</vf:TypSouboru>
<vf:TransakceOd><vf:Id>1</vf:Id></vf:TransakceOd></vf:Hlavicka>
<vf:Data>"""

_BODY = """
<vf:Parcely><vf:Parcela gml:id="PA.1"><pai:Id>2099603101</pai:Id><pai:VymeraParcely>212</pai:VymeraParcely>
</vf:Parcela></vf:Parcely>
<vf:StavebniObjekty>
<vf:StavebniObjekt gml:id="SO.21647836"><soi:Kod>21647836</soi:Kod>
<soi:CislaDomovni><com:CisloDomovni>727</com:CisloDomovni><com:CisloDomovni>728</com:CisloDomovni></soi:CislaDomovni>
<soi:IdentifikacniParcela><pai:Id>2099603101</pai:Id></soi:IdentifikacniParcela>
<soi:TypStavebnihoObjektuKod>1</soi:TypStavebnihoObjektuKod><soi:ZpusobVyuzitiKod>6</soi:ZpusobVyuzitiKod>
<soi:CastObce><coi:Kod xmlns:coi="urn:x">400017</coi:Kod></soi:CastObce>
<soi:Momc><mci:Kod xmlns:mci="urn:y">500054</mci:Kod></soi:Momc>
<soi:PlatiOd>2023-12-18T00:00:00</soi:PlatiOd><soi:IsknBudovaId>896183101</soi:IsknBudovaId>
<soi:Dokonceni>1945-12-31T00:00:00</soi:Dokonceni><soi:DruhKonstrukceKod>7</soi:DruhKonstrukceKod>
<soi:PocetBytu>75</soi:PocetBytu><soi:PocetPodlazi>8</soi:PocetPodlazi>
<soi:PripojeniKanalizaceKod>1</soi:PripojeniKanalizaceKod><soi:PripojeniPlynKod>2</soi:PripojeniPlynKod>
<soi:PripojeniVodovodKod>3</soi:PripojeniVodovodKod><soi:VybaveniVytahemKod>1</soi:VybaveniVytahemKod>
<soi:ZastavenaPlocha>2270</soi:ZastavenaPlocha><soi:ZpusobVytapeniKod>4</soi:ZpusobVytapeniKod>
<soi:ZpusobyOchrany><com:ZpusobOchrany><com:Kod>18</com:Kod><com:TypOchranyKod>2</com:TypOchranyKod>
</com:ZpusobOchrany><com:ZpusobOchrany><com:Kod>16</com:Kod></com:ZpusobOchrany></soi:ZpusobyOchrany>
<soi:DetailniTEA><soi:DetailniTEA><soi:Kod>150</soi:Kod><soi:PocetBytu>0</soi:PocetBytu>
<soi:PocetPodlazi>3</soi:PocetPodlazi></soi:DetailniTEA></soi:DetailniTEA>
<soi:Geometrie><soi:DefinicniBod><gml:Point><gml:pos>-742355.27 -1042665.01</gml:pos></gml:Point>
</soi:DefinicniBod></soi:Geometrie></vf:StavebniObjekt>
<vf:StavebniObjekt gml:id="SO.5"><soi:Kod>5</soi:Kod><soi:PlatiOd>2020-01-02T00:00:00</soi:PlatiOd>
</vf:StavebniObjekt>
</vf:StavebniObjekty>
<vf:AdresniMista>
<vf:AdresniMisto gml:id="AD.21723796"><ami:Kod>21723796</ami:Kod><ami:CisloDomovni>727</ami:CisloDomovni>
<ami:StavebniObjekt><soi:Kod>21647836</soi:Kod></ami:StavebniObjekt></vf:AdresniMisto>
<vf:AdresniMisto gml:id="AD.9"><ami:Kod>9</ami:Kod></vf:AdresniMisto>
</vf:AdresniMista>
</vf:Data></vf:VymennyFormat>"""


def _zip(tmp_path, kind="OB_UZSZ", batch="Plna", members=1):
    path = tmp_path / "20261031_OB_554782_UZSZ.xml.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for i in range(members):
            archive.writestr(f"file{i}.xml", _HEAD.format(kind=kind, batch=batch) + _BODY)
    return path


def test_reads_a_building_with_its_own_attributes(tmp_path):
    rows = list(ruian_vfr.iter_obec_state(_zip(tmp_path)))
    building = rows[0]
    assert building == ruian_vfr.BuildingRow(
        code=21647836, plati_od=datetime.date(2023, 12, 18), typ_so=1, usage_code=6,
        iskn_building_id=896183101, identifying_parcel_id=2099603101, cast_obce_kod=400017, momc_kod=500054,
        house_numbers=(727, 728), completed_on=datetime.date(1945, 12, 31), construction_code=7, floors=8,
        dwelling_count=75, built_up_area_m2=2270, floor_area_m2=None, enclosed_volume_m3=None, lift_code=1,
        water_code=3, sewer_code=1, gas_code=2, heating_code=4, protection_codes=(16, 18))


def test_an_entrance_record_never_overrides_the_building(tmp_path):
    building = next(iter(ruian_vfr.iter_obec_state(_zip(tmp_path))))
    assert (building.floors, building.dwelling_count) == (8, 75)


def test_a_bare_building_reads_as_gaps(tmp_path):
    bare = list(ruian_vfr.iter_obec_state(_zip(tmp_path)))[1]
    assert (bare.code, bare.floors, bare.house_numbers, bare.completed_on) == (5, None, (), None)


def test_links_only_address_points_that_name_a_building(tmp_path):
    links = [r for r in ruian_vfr.iter_obec_state(_zip(tmp_path)) if isinstance(r, ruian_vfr.AddressLink)]
    assert links == [ruian_vfr.AddressLink(21723796, 21647836)]


@pytest.mark.parametrize("kwargs", [{"kind": "OB_UKSH"}, {"batch": "Zmenova"}, {"members": 2}])
def test_refuses_another_product(tmp_path, kwargs):
    with pytest.raises(ruian_vfr.VfrSchemaError):
        list(ruian_vfr.iter_obec_state(_zip(tmp_path, **kwargs)))


_REAL = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "location_data" / "20261031_OB_550680_UZSZ.trimmed.xml"


def test_a_real_file_reads_through(tmp_path):
    """Obec 550680 of vintage 2026-10-31, trimmed to five buildings and their address points (ČÚZK, CC BY 4.0)."""
    path = tmp_path / "real.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("20261031_OB_550680_UZSZ.xml", _REAL.read_bytes())
    rows = list(ruian_vfr.iter_obec_state(path))
    buildings = [r for r in rows if isinstance(r, ruian_vfr.BuildingRow)]
    links = [r for r in rows if isinstance(r, ruian_vfr.AddressLink)]
    assert len(buildings) == 5 and len(links) == 4
    assert {link.building_code for link in links} <= {b.code for b in buildings}
    assert any(b.floors for b in buildings) and any(b.completed_on for b in buildings)
    assert any(not b.house_numbers for b in buildings), "a building with no house number is read too"
    assert all(b.iskn_building_id is None or b.iskn_building_id > 0 for b in buildings)


def test_a_truncated_file_is_refused_not_read_short(tmp_path):
    whole = _HEAD.format(kind="OB_UZSZ", batch="Plna") + _BODY
    path = tmp_path / "cut.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("cut.xml", whole[: whole.index("<vf:AdresniMista>")])
    with pytest.raises(ruian_vfr.VfrSchemaError, match="unreadable"):
        list(ruian_vfr.iter_obec_state(path))
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    with pytest.raises(ruian_vfr.VfrSchemaError, match="unreadable"):
        list(ruian_vfr.iter_obec_state(tmp_path / "junk.zip"))


def test_a_full_file_with_no_building_is_refused(tmp_path):
    body = _BODY[: _BODY.index("<vf:StavebniObjekty>")] + _BODY[_BODY.index("<vf:AdresniMista>"):]
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("empty.xml", _HEAD.format(kind="OB_UZSZ", batch="Plna") + body)
    with pytest.raises(ruian_vfr.VfrSchemaError, match="no building"):
        list(ruian_vfr.iter_obec_state(path))


def test_a_value_that_is_not_a_number_is_a_schema_error(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("bad.xml", _HEAD.format(kind="OB_UZSZ", batch="Plna")
                         + _BODY.replace("<soi:PocetPodlazi>8<", "<soi:PocetPodlazi>osm<"))
    with pytest.raises(ruian_vfr.VfrSchemaError, match="unreadable"):
        list(ruian_vfr.iter_obec_state(path))
