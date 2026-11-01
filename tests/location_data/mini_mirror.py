"""An in-memory RÚIAN mini-mirror for the resolver tests.

No database, no network: the four steps all run against this. That is the whole point of
`types.RegistryView` being a protocol.

The gazetteer content is the design's own named regression material: two Krásný Les obce
~100 km apart, Bílovec vs its de-accented form, and a Prague street whose name merely
CONTAINS a village name (the Bořislav 40 case).

W2-a shrank this file by more than half, and the deletions say what the wave did: the four
CONFIG fixtures are gone (`location_field_policy`, `location_uncertainty_policy`,
`location_collision_policy`, `location_constants` — policy is code now), and so are the
registry answers whose questions went with them (parcels, the pin clusters, the boundary
distance, the ČástObce point lookup). What is left is the questions the protocol still
declares — the sliver fallback among them, because rule 25 does not allow a border pin to
have no town.

A fixture point's `katastr_kod` stands in for the KÚ point-in-polygon the SQL makes, and the
door rule is derived from it exactly as `_doors_katastr` decides it: one KÚ over every door.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from location_data.name_index import normalize_name, split_qualifier
from location_data.resolver.geo import haversine_m
from location_data.resolver.resolve_db import _path_code
from location_data.resolver.types import (
    AddressPoint,
    AdminUnit,
    Claim,
    PartPoint,
    ResolverContext,
    Street,
    StreetPoint,
)

# The Czech bounding box the resolver carries as a code constant (`check.CZ_BBOX`).
CZ_BBOX = (12.0, 48.0, 19.0, 51.5)


@dataclass
class MiniMirror:
    """A `RegistryView` over python dicts."""

    units: list[AdminUnit] = field(default_factory=list)
    streets: list[Street] = field(default_factory=list)
    points: list[AddressPoint] = field(default_factory=list)
    # obec_kod -> (lat, lon, radius_m) polygon stand-in: a circle around the centre.
    obec_polygons: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    cz_polygon: tuple[float, float, float] | None = (49.8, 15.5, 300_000.0)

    # ---- protocol
    def address_point(self, kod_adm: int) -> AddressPoint | None:
        return next((p for p in self.points if p.kod_adm == kod_adm), None)

    def address_points_by_number(
        self, *, obec_kod: int, street_name_norm: str | None,
        cislo_domovni: int | None, cislo_orientacni: int | None,
        typ_so: str | None = None, cast_obce_unit_id: int | None = None,
    ) -> list[AddressPoint]:
        return [
            p
            for p in self.points
            if p.obec_kod == obec_kod
            and (street_name_norm is None or p.street_name_norm == street_name_norm)
            and (cislo_domovni is None or p.cislo_domovni == cislo_domovni and typ_so in (None, p.typ_so))
            and (cislo_orientacni is None or p.cislo_orientacni == cislo_orientacni)
            and (cast_obce_unit_id is None or p.cast_obce_unit_id == cast_obce_unit_id)
        ]

    def streets_in_obec(self, obec_kod: int) -> list[Street]:
        return [s for s in self.streets if s.obec_kod == obec_kod]

    def street_point(self, street: Street) -> StreetPoint | None:
        """The same derivation `_STREET_POINT_SQL` makes, in python: the centroid of the
        street's own address points, and the distance from it to the FARTHEST of them. Keyed
        on `ulice_kod` here because the fixture points carry that and not a `street_id` —
        the two are 1:1 on every row of the real mirror."""
        points = [p for p in self.points
                  if p.ulice_kod == street.code and p.lat is not None and p.lon is not None]
        if not points:
            return None
        lat = sum(p.lat for p in points) / len(points)
        lon = sum(p.lon for p in points) / len(points)
        extent = max(haversine_m(lat, lon, p.lat, p.lon) for p in points)
        return StreetPoint(lat=lat, lon=lon, extent_m=extent, point_count=len(points),
                           katastr_kod=_doors_katastr(points),
                           part_unit_ids=tuple(sorted({p.cast_obce_unit_id for p in points},
                                                      key=lambda u: (u is None, u or 0))))

    def part_point(self, unit_id: int) -> PartPoint | None:
        """`_PART_POINT_SQL` in python, the street's derivation over the část's doors."""
        points = [p for p in self.points
                  if p.cast_obce_unit_id == unit_id and p.lat is not None and p.lon is not None]
        if not points:
            return None
        lat = sum(p.lat for p in points) / len(points)
        lon = sum(p.lon for p in points) / len(points)
        extent = max(haversine_m(lat, lon, p.lat, p.lon) for p in points)
        return PartPoint(lat=lat, lon=lon, extent_m=extent, point_count=len(points),
                         katastr_kod=_doors_katastr(points))

    def admin_units_by_name(self, name_norm: str, *, levels: Sequence[str] = ()) -> list[AdminUnit]:
        """By the unit's own key or, as `ruian_name_index` also indexes it, by its name with the
        qualifier stripped ("Vranovice u Břas" answers "vranovice")."""
        return [
            u
            for u in self.units
            if name_norm in (u.name_norm, _stripped_key(u.name))
            and (not levels or u.level in levels)
        ]

    def admin_chain(self, unit_id: int) -> list[AdminUnit]:
        """The unit ITSELF first, then its ancestors — the shape `_ADMIN_CHAIN_SQL` returns
        now that `admin_unit` and `admin_unit_by_code` are folded into it."""
        unit = self._unit_by_id(unit_id)
        if unit is None:
            return []
        chain = [unit]
        while unit is not None and unit.parent_id is not None:
            unit = self._unit_by_id(unit.parent_id)
            if unit is None:
                break
            chain.append(unit)
        return [self._with_sole_katastr(u) for u in chain]

    def admin_chain_by_code(self, level: str, code: int) -> list[AdminUnit]:
        unit = next((u for u in self.units if u.level == level and u.code == code), None)
        return [] if unit is None else self.admin_chain(unit.unit_id)

    def obec_codes_for_psc(self, psc: str) -> list[int]:
        return sorted({p.obec_kod for p in self.points if p.psc == psc})

    def containing_obec(self, lat: float, lon: float) -> AdminUnit | None:
        for code, (clat, clon, radius) in sorted(self.obec_polygons.items()):
            if haversine_m(lat, lon, clat, clon) <= radius:
                return next(
                    (u for u in self.units if u.level == "obec" and u.code == code), None
                )
        return None

    def nearest_obec_within(
        self, lat: float, lon: float, max_m: float
    ) -> tuple[AdminUnit, float] | None:
        best: tuple[AdminUnit, float] | None = None
        for code, (clat, clon, radius) in sorted(self.obec_polygons.items()):
            distance = max(0.0, haversine_m(lat, lon, clat, clon) - radius)
            unit = next((u for u in self.units if u.level == "obec" and u.code == code), None)
            if unit is None or distance > max_m:
                continue
            if best is None or distance < best[1]:
                best = (unit, distance)
        return best

    def in_czechia_polygon(self, lat: float, lon: float) -> bool | None:
        if self.cz_polygon is None:
            return None
        clat, clon, radius = self.cz_polygon
        return haversine_m(lat, lon, clat, clon) <= radius

    def doors_near(self, lat: float, lon: float, max_m: float) -> list[tuple[int, float]]:
        """`_DOORS_NEAR_SQL` in python: nearest first, the code breaks a tie, at most two."""
        near = ((haversine_m(lat, lon, p.lat, p.lon), p.kod_adm) for p in self.points
                if p.lat is not None and p.lon is not None)
        return [(kod, dist) for dist, kod in sorted(n for n in near if n[0] <= max_m)[:2]]

    # ---- fixture helper, not part of the protocol
    def _unit_by_id(self, unit_id: int) -> AdminUnit | None:
        return next((u for u in self.units if u.unit_id == unit_id), None)

    def _with_sole_katastr(self, unit: AdminUnit) -> AdminUnit:
        """What `_ADMIN_CHAIN_TAIL` answers on an obec row: its one KÚ child, else None."""
        if unit.level != "obec":
            return unit
        kus = [u.code for u in self.units
               if u.level == "katastralni_uzemi" and u.parent_id == unit.unit_id]
        return replace(unit, sole_katastr_kod=kus[0] if len(kus) == 1 else None)


def _stripped_key(name: str) -> str | None:
    base, qualifier = split_qualifier(name)
    return normalize_name(base) if qualifier else None


def _doors_katastr(doors: Sequence[AddressPoint]) -> int | None:
    """`resolve_db._doors_katastr` in python: the one KÚ holding every door, else None."""
    codes = {p.katastr_kod for p in doors}
    if len(codes) != 1 or None in codes:
        return None
    return codes.pop()


# --------------------------------------------------------------------------- fixtures


def _unit(unit_id, level, code, name, name_norm, path, parent=None, lat=None, lon=None,
          psc_set=()) -> AdminUnit:
    """The okres/kraj/obec codes come off the ltree path, as `resolve_db._admin_unit` does."""
    return AdminUnit(
        unit_id=unit_id, level=level, code=code, name=name, name_norm=name_norm, path=path,
        parent_id=parent, lat=lat, lon=lon, psc_set=tuple(psc_set),
        okres_kod=_path_code(path, "o"), kraj_kod=_path_code(path, "k"),
        obec_kod=_path_code(path, "b"),
    )


def default_mirror() -> MiniMirror:
    """One kraj/okres tree per regression case, plus a fully addressed obec.

    Unit POINTS are modelled as the mirror actually has them (W2-a3): stát, kraj, okres and
    obec carry one, a část obce or a městský obvod does NOT — `ruian_boundaries.LAYERS` draws
    no polygon at those levels and `ruian_admin_units.definition_point` is a column the
    loader has never written. A fixture that gave Vokovice a point would test a registry we
    do not have.
    """
    units = [
        # --- Liberecký kraj / okres Liberec / Krásný Les (the RIGHT one)
        _unit(1, "kraj", 51, "Liberecký kraj", "liberecky kraj", "k51",
              lat=50.6500, lon=14.9000),
        _unit(2, "okres", 3506, "Liberec", "liberec", "k51.o3506", parent=1,
              lat=50.7663, lon=15.0562),
        _unit(3, "obec", 563943, "Krásný Les", "krasny les", "k51.o3506.b563943", parent=2,
              lat=50.9330, lon=15.1500, psc_set=("46346",)),
        _unit(4, "katastralni_uzemi", 673986, "Krásný Les u Frýdlantu",
              "krasny les u frydlantu", "k51.o3506.b563943", parent=3),
        # --- Ústecký kraj / okres Ústí nad Labem / Krásný Les (the WRONG one, ~100 km west)
        _unit(5, "kraj", 42, "Ústecký kraj", "ustecky kraj", "k42",
              lat=50.5000, lon=13.8000),
        _unit(6, "okres", 3805, "Ústí nad Labem", "usti nad labem", "k42.o3805", parent=5,
              lat=50.6607, lon=14.0328),
        _unit(7, "obec", 567931, "Krásný Les", "krasny les", "k42.o3805.b567931", parent=6,
              lat=50.7676, lon=13.9353, psc_set=("40302",)),
        # --- Moravskoslezský kraj / okres Nový Jičín / Bílovec
        _unit(8, "kraj", 80, "Moravskoslezský kraj", "moravskoslezsky kraj", "k80",
              lat=49.7500, lon=18.0000),
        _unit(9, "okres", 3804, "Nový Jičín", "novy jicin", "k80.o3804", parent=8,
              lat=49.5944, lon=18.0103),
        _unit(10, "obec", 599212, "Bílovec", "bilovec", "k80.o3804.b599212", parent=9,
              lat=49.7573, lon=18.0158, psc_set=("74301",)),
        # --- Praha (street-name-contains-village trap) + Bořislav village
        _unit(11, "kraj", 19, "Hlavní město Praha", "hlavni mesto praha", "k19",
              lat=50.0755, lon=14.4378),
        _unit(12, "okres", 3100, "Hlavní město Praha", "hlavni mesto praha", "k19.o3100",
              parent=11, lat=50.0755, lon=14.4378),
        _unit(13, "obec", 554782, "Praha", "praha", "k19.o3100.b554782", parent=12,
              lat=50.0755, lon=14.4378, psc_set=("16000", "18000")),
        # No point: RÚIAN draws no ČástObce polygon at all, so a quarter-bound row takes
        # its town's point.
        _unit(14, "cast_obce", 490067, "Vokovice", "vokovice", "k19.o3100.b554782.c490067",
              parent=13),
        _unit(15, "obec", 567639, "Bořislav", "borislav", "k42.o3805.b567639", parent=6,
              lat=50.5794, lon=13.9200, psc_set=("41502",)),
        # --- W18's operator case, as the live register holds it: Mladá Boleslav and the
        # street of bazos ad 223293822. The town's own point is deliberately NOT the
        # street's — that difference is the whole of "a bound street decides the point".
        _unit(30, "kraj", 27, "Středočeský kraj", "stredocesky kraj", "k27",
              lat=50.0000, lon=14.5000),
        _unit(31, "okres", 3204, "Mladá Boleslav", "mlada boleslav", "k27.o3204", parent=30,
              lat=50.4114, lon=14.9030),
        _unit(32, "obec", 535419, "Mladá Boleslav", "mlada boleslav",
              "k27.o3204.b535419", parent=31, lat=50.4114, lon=14.9030,
              psc_set=("29301",)),
        # Kladno and its ČástObce Dubí — the dash-split line "Kladno - Dubí, Ke Křížku"
        # (a town, a quarter and a street in one string).
        _unit(33, "okres", 3201, "Kladno", "kladno", "k27.o3201", parent=30,
              lat=50.1477, lon=14.1028),
        _unit(34, "obec", 532053, "Kladno", "kladno", "k27.o3201.b532053", parent=33,
              lat=50.1477, lon=14.1028, psc_set=("27201",)),
        _unit(35, "cast_obce", 71773, "Dubí", "dubi", "k27.o3201.b532053.c71773",
              parent=34),
        # The 76-times-over collision, as one case: a ČástObce of Ostrava spelled exactly
        # like a street of the same town. On a LINE there is nothing to tell them apart, so
        # the segment is not a street candidate at all.
        _unit(36, "okres", 3807, "Ostrava-město", "ostrava mesto", "k80.o3807", parent=8,
              lat=49.8209, lon=18.2625),
        _unit(37, "obec", 554821, "Ostrava", "ostrava", "k80.o3807.b554821", parent=36,
              lat=49.8209, lon=18.2625, psc_set=("70200",)),
        _unit(38, "cast_obce", 554791, "Zábřeh", "zabreh", "k80.o3807.b554821.c554791",
              parent=37),
    ]
    streets = [
        Street(code=101, name="Nad Bořislavkou", name_norm="nad borislavkou", obec_kod=554782),
        Street(code=102, name="28. října", name_norm="28 rijna", obec_kod=554782),
        Street(code=103, name="Slunečná", name_norm="slunecna", obec_kod=599212),
        # The dropped-prefix class: the portal says `Budovatelů`, RÚIAN says `nám.
        # Budovatelů` — same street, and `name_norm` keeps the type word RÚIAN spells.
        Street(code=104, name="nám. Budovatelů", name_norm="nam budovatelu", obec_kod=599212),
        # W18: a street with a SPREAD, and an ASYMMETRIC one. The three points below are
        # shaped so the derived centroid is the live one (POINT(14.91365 50.42247), measured
        # over Jiráskova's 63 address points on 2026-09-16) and its farthest door is the live
        # 1,288 m away — while HALF THE BOUNDING DIAGONAL of the same set is only ~985 m. The
        # asymmetry is the fixture's whole job: it is what makes a test able to tell the two
        # definitions of `extent_m` apart, and a real door of the street falls outside the
        # wrong one.
        Street(code=105, name="Jiráskova", name_norm="jiraskova", obec_kod=535419),
        # W18's line cases. `Ke Křížku` is the street a dash-split line ends on; `Sokolovská`
        # gives the same line a SECOND street, which is what fail-closed means. `Zábřeh` is a
        # street spelled like a část obce of its own town, and `Livornská` is the register's
        # spelling of a name bazos writes inflected ("Livornské ulici").
        Street(code=106, name="Ke Křížku", name_norm="ke krizku", obec_kod=532053),
        Street(code=107, name="Sokolovská", name_norm="sokolovska", obec_kod=532053),
        Street(code=108, name="náměstí Míru", name_norm="namesti miru", obec_kod=535419),
        Street(code=109, name="Zábřeh", name_norm="zabreh", obec_kod=554821),
        Street(code=110, name="28. října", name_norm="28 rijna", obec_kod=554821),
        Street(code=111, name="Livornská", name_norm="livornska", obec_kod=554782),
        # The WIDENED-KEY COLLISION, as the register actually holds it: 45 keys across 32
        # obce collide once the type word is dropped, and Kladno is one of them — both of
        # these fold to `svobody`. It is why an exact full-name match has to win outright.
        Street(code=112, name="náměstí Svobody", name_norm="namesti svobody", obec_kod=532053),
        Street(code=113, name="Svobody", name_norm="svobody", obec_kod=532053),
        # The register spelling the generic word INTO the name (`Nová ulice` ×8, `V Ulici`,
        # `Na Ulici`, `I. ulice`…`IX. ulice`): folding it off leaves a key that can never bind.
        Street(code=114, name="Nová ulice", name_norm="nova ulice", obec_kod=532053),
        Street(code=115, name="Na Ulici", name_norm="na ulici", obec_kod=532053),
    ]
    points = [
        AddressPoint(
            kod_adm=21690278, obec_unit_id=13, obec_kod=554782, psc="16000",
            lat=50.10100, lon=14.34800, ulice_kod=101,
            street_name_norm="nad borislavkou", street_name="Nad Bořislavkou",
            cislo_domovni=487, cislo_orientacni=40,
            cast_obce_unit_id=14, cast_obce_kod=490067,
        ),
        AddressPoint(
            kod_adm=21690279, obec_unit_id=13, obec_kod=554782, psc="16000",
            lat=50.10110, lon=14.34810, ulice_kod=101,
            street_name_norm="nad borislavkou", street_name="Nad Bořislavkou",
            cislo_domovni=488, cislo_orientacni=41,
            cast_obce_unit_id=14, cast_obce_kod=490067,
        ),
        AddressPoint(
            kod_adm=33000001, obec_unit_id=10, obec_kod=599212, psc="74301",
            lat=49.75740, lon=18.01590, ulice_kod=103,
            street_name_norm="slunecna", street_name="Slunečná", cislo_domovni=12,
        ),
        AddressPoint(
            kod_adm=33000002, obec_unit_id=10, obec_kod=599212, psc="74301",
            lat=49.75750, lon=18.01600, ulice_kod=104,
            street_name_norm="nam budovatelu", street_name="nám. Budovatelů",
            cislo_domovni=5,
        ),
        AddressPoint(
            kod_adm=55000001, obec_unit_id=32, obec_kod=535419, psc="29301",
            lat=50.41090, lon=14.91200, ulice_kod=105,
            street_name_norm="jiraskova", street_name="Jiráskova", cislo_domovni=1,
        ),
        AddressPoint(
            kod_adm=55000002, obec_unit_id=32, obec_kod=535419, psc="29301",
            lat=50.42800, lon=14.91400, ulice_kod=105,
            street_name_norm="jiraskova", street_name="Jiráskova", cislo_domovni=40,
        ),
        AddressPoint(
            kod_adm=55000003, obec_unit_id=32, obec_kod=535419, psc="29301",
            lat=50.42851, lon=14.91495, ulice_kod=105,
            street_name_norm="jiraskova", street_name="Jiráskova", cislo_domovni=86,
        ),
    ]
    return MiniMirror(
        units=units,
        streets=streets,
        points=points,
        obec_polygons={
            563943: (50.9330, 15.1500, 3000.0),
            567931: (50.7676, 13.9353, 3000.0),
            599212: (49.7573, 18.0158, 4000.0),
            554782: (50.0755, 14.4378, 12000.0),
            567639: (50.5794, 13.9200, 2000.0),
            535419: (50.42247, 14.91365, 6000.0),
            532053: (50.1477, 14.1028, 5000.0),
            554821: (49.8209, 18.2625, 9000.0),
        },
    )


def context(mirror: MiniMirror | None = None) -> ResolverContext:
    return ResolverContext(registry=mirror or default_mirror())


_T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def claim(
    claim_id: int,
    claim_type: str,
    *,
    listing_id: int = 900001,
    source: str = "sreality",
    value_text: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    extraction_method: str = "portal_structured_field",
    surface: str = "api_json",
    licence_class: str = "portal",
    subject_scoped: bool | None = True,
    declared_precision_label: str | None = None,
    declared_radius_m: float | None = None,
    blur_evidence: str = "none",
    minutes: int = 0,
) -> Claim:
    return Claim(
        id=claim_id, listing_id=listing_id, source=source, claim_type=claim_type,
        surface=surface, extraction_method=extraction_method, licence_class=licence_class,
        observed_at=_T0.replace(minute=_T0.minute) if minutes == 0 else _T0,
        value_text=value_text, lat=lat, lon=lon, subject_scoped=subject_scoped,
        declared_precision_label=declared_precision_label,
        declared_radius_m=declared_radius_m, blur_evidence=blur_evidence,
    )


def statutory_city_mirror() -> MiniMirror:
    """The eight statutory cities as RÚIAN actually holds them (W9), plus the homonyms that
    make the fail-closed rule mean something.

    Codes, names and parentage are the live register's (`ruian_admin_units`,
    `ruian:2026-08-31`, read 2026-09-14): Praha's quarters are MOMC *and* ČástObce rows with
    different codes, "Poruba" is a ČástObce of Ostrava AND a MOMC of Ostrava AND a ČástObce
    of Orlová, there is a village called Podolí in okres Brno-venkov, and "Frýdek-Místek" and
    "Brno-venkov" are an obec and an okres spelled like a městský obvod. Every one of those
    is a case the deleted regex had to be told about by hand.

    No unit points: RÚIAN draws no polygon at ČástObce or MOMC level, and the towns here are
    never asked to place a row.
    """
    units = [
        # --- Jihomoravský kraj: Brno, its okres namesake, and the village called Podolí
        _unit(100, "kraj", 116, "Jihomoravský kraj", "jihomoravsky kraj", "k116",
              lat=49.1951, lon=16.6068),
        _unit(101, "okres", 3702, "Brno-město", "brno mesto", "k116.o3702", parent=100,
              lat=49.1951, lon=16.6068),
        _unit(102, "okres", 3703, "Brno-venkov", "brno venkov", "k116.o3703", parent=100,
              lat=49.1951, lon=16.6068),
        _unit(103, "obec", 582786, "Brno", "brno", "k116.o3702.b582786", parent=101,
              lat=49.1951, lon=16.6068),
        _unit(104, "cast_obce", 12114, "Dolní Heršpice", "dolni herspice",
              "k116.o3702.b582786.c12114", parent=103),
        _unit(105, "momc", 550973, "Brno-střed", "brno stred", "k116.o3702.b582786.m550973",
              parent=103),
        _unit(106, "obec", 583634, "Podolí", "podoli", "k116.o3703.b583634", parent=102,
              lat=49.2211, lon=16.7530),
        _unit(107, "cast_obce", 124257, "Podolí", "podoli", "k116.o3703.b583634.c124257",
              parent=106),
        # --- Praha: no okres at all, and the quarter at BOTH levels
        _unit(110, "kraj", 19, "Hlavní město Praha", "hlavni mesto praha", "k19",
              lat=50.0755, lon=14.4378),
        _unit(111, "obec", 554782, "Praha", "praha", "k19.b554782", parent=110,
              lat=50.0755, lon=14.4378),
        _unit(112, "momc", 500119, "Praha 4", "praha 4", "k19.b554782.m500119", parent=111),
        _unit(113, "momc", 539635, "Praha-Řeporyje", "praha reporyje",
              "k19.b554782.m539635", parent=111),
        _unit(114, "cast_obce", 400190, "Podolí", "podoli", "k19.b554782.c400190",
              parent=111),
        _unit(115, "cast_obce", 490270, "Řeporyje", "reporyje", "k19.b554782.c490270",
              parent=111),
        # --- Plzeň
        _unit(120, "kraj", 32, "Plzeňský kraj", "plzensky kraj", "k32",
              lat=49.7384, lon=13.3736),
        _unit(121, "okres", 3404, "Plzeň-město", "plzen mesto", "k32.o3404", parent=120,
              lat=49.7384, lon=13.3736),
        _unit(122, "obec", 554791, "Plzeň", "plzen", "k32.o3404.b554791", parent=121,
              lat=49.7384, lon=13.3736),
        _unit(123, "cast_obce", 406368, "Jižní Předměstí", "jizni predmesti",
              "k32.o3404.b554791.c406368", parent=122),
        _unit(124, "momc", 546003, "Plzeň 3", "plzen 3", "k32.o3404.b554791.m546003",
              parent=122),
        # --- Moravskoslezský kraj: Ostrava (Poruba twice over), Orlová, Opava, Frýdek-Místek
        _unit(130, "kraj", 132, "Moravskoslezský kraj", "moravskoslezsky kraj", "k132",
              lat=49.8209, lon=18.2625),
        _unit(131, "okres", 3807, "Ostrava-město", "ostrava mesto", "k132.o3807", parent=130,
              lat=49.8209, lon=18.2625),
        _unit(132, "obec", 554821, "Ostrava", "ostrava", "k132.o3807.b554821", parent=131,
              lat=49.8209, lon=18.2625),
        _unit(133, "cast_obce", 414085, "Poruba", "poruba", "k132.o3807.b554821.c414085",
              parent=132),
        _unit(134, "momc", 546224, "Poruba", "poruba", "k132.o3807.b554821.m546224",
              parent=132),
        _unit(135, "okres", 3802, "Karviná", "karvina", "k132.o3802", parent=130,
              lat=49.8543, lon=18.5421),
        _unit(136, "obec", 507292, "Orlová", "orlova", "k132.o3802.b507292", parent=135,
              lat=49.8654, lon=18.4302),
        _unit(137, "cast_obce", 413488, "Poruba", "poruba", "k132.o3802.b507292.c413488",
              parent=136),
        _unit(138, "okres", 3805, "Opava", "opava", "k132.o3805", parent=130,
              lat=49.9387, lon=17.9026),
        _unit(139, "obec", 505927, "Opava", "opava", "k132.o3805.b505927", parent=138,
              lat=49.9387, lon=17.9026),
        _unit(140, "cast_obce", 413909, "Kateřinky", "katerinky",
              "k132.o3805.b505927.c413909", parent=139),
        _unit(141, "okres", 3803, "Frýdek-Místek", "frydek mistek", "k132.o3803",
              parent=130, lat=49.6833, lon=18.3486),
        _unit(142, "obec", 598003, "Frýdek-Místek", "frydek mistek", "k132.o3803.b598003",
              parent=141, lat=49.6833, lon=18.3486),
        _unit(143, "cast_obce", 33235, "Místek", "mistek", "k132.o3803.b598003.c33235",
              parent=142),
        # --- Liberec: one MOMC, and it is spelled city-plus-name
        _unit(150, "kraj", 51, "Liberecký kraj", "liberecky kraj", "k51",
              lat=50.7663, lon=15.0562),
        _unit(151, "okres", 3506, "Liberec", "liberec", "k51.o3506", parent=150,
              lat=50.7663, lon=15.0562),
        _unit(152, "obec", 563889, "Liberec", "liberec", "k51.o3506.b563889", parent=151,
              lat=50.7663, lon=15.0562),
        _unit(153, "momc", 556891, "Liberec-Vratislavice nad Nisou",
              "liberec vratislavice nad nisou", "k51.o3506.b563889.m556891", parent=152),
        # --- Ústí nad Labem: the quarter as a ČástObce and as a city-prefixed MOMC
        _unit(160, "kraj", 42, "Ústecký kraj", "ustecky kraj", "k42",
              lat=50.6607, lon=14.0328),
        _unit(161, "okres", 3809, "Ústí nad Labem", "usti nad labem", "k42.o3809",
              parent=160, lat=50.6607, lon=14.0328),
        _unit(162, "obec", 554804, "Ústí nad Labem", "usti nad labem", "k42.o3809.b554804",
              parent=161, lat=50.6607, lon=14.0328),
        _unit(163, "cast_obce", 409448, "Střekov", "strekov", "k42.o3809.b554804.c409448",
              parent=162),
        _unit(164, "momc", 502316, "Ústí nad Labem-Střekov", "usti nad labem strekov",
              "k42.o3809.b554804.m502316", parent=162),
        # --- Pardubice: the Roman-numeral MOMC
        _unit(170, "kraj", 53, "Pardubický kraj", "pardubicky kraj", "k53",
              lat=50.0343, lon=15.7812),
        _unit(171, "okres", 3603, "Pardubice", "pardubice", "k53.o3603", parent=170,
              lat=50.0343, lon=15.7812),
        _unit(172, "obec", 555134, "Pardubice", "pardubice", "k53.o3603.b555134",
              parent=171, lat=50.0343, lon=15.7812),
        _unit(173, "cast_obce", 410632, "Polabiny", "polabiny", "k53.o3603.b555134.c410632",
              parent=172),
        _unit(174, "momc", 555151, "Pardubice II", "pardubice ii",
              "k53.o3603.b555134.m555151", parent=172),
    ]
    streets = [
        # The third token of "Brno - Dolní Heršpice, Bernáčkova": a street, and the register
        # holds it at no admin level at all, which is how the binder comes to ignore it.
        Street(code=200, name="Bernáčkova", name_norm="bernackova", obec_kod=582786),
    ]
    return MiniMirror(units=units, streets=streets, points=[], obec_polygons={})


# The 1a rows of text-reader W4 (operator verdicts 2026-10-14) as `ruian:2026-08-31` holds them
# (read 2026-10-14): listing, its town claim, its PSČ, the obec carrying that PSČ, every unit of
# that obec the claim names (and the část a KÚ's base name spells), a namesake obec elsewhere,
# and the part the row publishes — None for the two regression rows whose KÚ is the town's own.
W4_VILLAGES: tuple[tuple, ...] = (
    (18953356, "Podolí u Mohelnice", "78985", (2056, 540471, "Mohelnice"),
     (("katastralni_uzemi", 30397, 724327, "Podolí u Mohelnice"),
      ("cast_obce", 15657, 124320, "Podolí")), None, 124320),
    (18769720, "Kadaňská Jeseň", "43201", (3785, 563102, "Kadaň"),
     (("cast_obce", 18820, 173266, "Kadaňská Jeseň"),), None, 173266),
    (18763267, "Prostřední Bludovice", "73937", (6799, 598178, "Horní Bludovice"),
     (("cast_obce", 9958, 42412, "Prostřední Bludovice"),
      ("katastralni_uzemi", 24964, 642410, "Prostřední Bludovice")), None, 42412),
    (18783823, "Vlásenice-Drbohlavy", "39301", (2559, 547492, "Pelhřimov"),
     (("cast_obce", 8376, 19399, "Vlásenice-Drbohlavy"),
      ("katastralni_uzemi", 23468, 619396, "Vlásenice-Drbohlavy")), None, 19399),
    (18916159, "Ješovice", "27721", (1542, 535001, "Liběchov"),
     (("cast_obce", 12821, 81914, "Ješovice"),
      ("katastralni_uzemi", 27676, 681911, "Ješovice")), None, 81914),
    (18851233, "Vranovice", "33824", (3477, 559725, "Břasy"),
     (("cast_obce", 7946, 13579, "Vranovice"),
      ("katastralni_uzemi", 23060, 613576, "Vranovice u Břas")), (2162, 541567), 13579),
    (16216159, "Jirkov", "46822", (3860, 563871, "Železný Brod"),
     (("cast_obce", 11279, 60909, "Jirkov"),), (3784, 563099), 60909),
    (18772969, "Hracholusky", "33033", (3466, 559555, "Úlice"),
     (("cast_obce", 18894, 174173, "Hracholusky"),), (2819, 550221), 174173),
    (19092838, "Nynice", "33011", (3421, 558915, "Hromnice"),
     (("cast_obce", 14739, 108391, "Nynice"),
      ("katastralni_uzemi", 29482, 708399, "Nynice")), None, 108391),
    (18969925, "Hukovice", "74213", (6893, 599212, "Bartošovice"),
     (("cast_obce", 10470, 49271, "Hukovice"),
      ("katastralni_uzemi", 25450, 649279, "Hukovice")), None, 49271),
    (19113700, "Chrášťany", "76824", (5999, 588491, "Hulín"),
     (("cast_obce", 20028, 189766, "Chrášťany"),
      ("katastralni_uzemi", 34585, 789763, "Chrášťany u Hulína")), (1248, 532037), 189766),
    (18771241, "Krásné", "35301", (3223, 554880, "Tři Sekery"),
     (("cast_obce", 18663, 170828, "Krásné"),), (4568, 571652), 170828),
    (19062751, "Doubrava", "29412", (1738, 536971, "Žďár"),
     (("cast_obce", 20442, 195014, "Doubrava"),), (4304, 568864), 195014),
    (15797884, "Frýdek", "73801", (6787, 598003, "Frýdek-Místek"),
     (("cast_obce", 9500, 34991, "Frýdek"),
      ("katastralni_uzemi", 24539, 634956, "Frýdek")), None, 34991),
    (18834304, "Milokošť", "69801", (5832, 586722, "Veselí nad Moravou"),
     (("cast_obce", 19328, 180793, "Milokošť"),
      ("katastralni_uzemi", 33950, 780791, "Milokošť")), None, 180793),
    (13824760, "Doksy u Kladna", "27364", (1267, 532223, "Doksy"),
     (("katastralni_uzemi", 24042, 628191, "Doksy u Kladna"),
      ("cast_obce", 8969, 28193, "Doksy")), None, None),
    (18827725, "Nové Hrady u Skutče", "53945", (4595, 571920, "Nové Hrady"),
     (("katastralni_uzemi", 29355, 706302, "Nové Hrady u Skutče"),
      ("cast_obce", 14622, 106305, "Nové Hrady")), None, None),
)


def w4_mirror() -> MiniMirror:
    """The register rows behind W4's acceptance table (`ruian:2026-08-31`, read 2026-10-14):
    the real codes, names, streets and the parts their doors lie in, with stand-in ids and
    points for units a test only needs to exist. The bazos pins are "approximate" and sit in
    the PSČ's post-office town, as on production."""
    mirror = MiniMirror(cz_polygon=(49.8, 15.5, 300_000.0))
    units, points = mirror.units, mirror.points

    def obec(uid: int, code: int, name: str, psc: str, at: tuple[float, float] | None = None,
             radius: float = 3000.0) -> None:
        units.append(_unit(uid, "obec", code, name, normalize_name(name), f"b{code}",
                           lat=at[0] if at else None, lon=at[1] if at else None,
                           psc_set=(psc,)))
        points.append(AddressPoint(kod_adm=90_000_000 + uid, obec_unit_id=uid, obec_kod=code,
                                   psc=psc, lat=at[0] if at else None, lon=at[1] if at else None))
        if at:
            mirror.obec_polygons[code] = (*at, radius)

    def part(uid: int, level: str, code: int, name: str, town: tuple[int, int]) -> None:
        tag = "x" if level == "katastralni_uzemi" else "c"
        units.append(_unit(uid, level, code, name, normalize_name(name),
                           f"b{town[1]}.{tag}{code}", parent=town[0]))

    def street(code: int, sid: int, name: str, town: tuple[int, int], doors: tuple, psc: str
               ) -> None:
        mirror.streets.append(Street(code=code, name=name, name_norm=normalize_name(name),
                                     obec_kod=town[1], id=sid))
        for i, (cp, unit, unit_code, lat, lon) in enumerate(doors):
            points.append(AddressPoint(
                kod_adm=sid * 100 + i, obec_unit_id=town[0], obec_kod=town[1], psc=psc,
                lat=lat, lon=lon, ulice_kod=code, street_name_norm=normalize_name(name),
                street_name=name, cislo_domovni=cp, cast_obce_unit_id=unit,
                cast_obce_kod=unit_code))

    for _, _, psc, (uid, code, name), matched, namesake, _ in W4_VILLAGES:
        obec(uid, code, name, psc)
        for level, part_uid, part_code, part_name in matched:
            part(part_uid, level, part_code, part_name, (uid, code))
        if namesake:
            obec(namesake[0], namesake[1], matched[0][3], "99999")

    part(18898, "cast_obce", 174211, "Úlice", (3466, 559555))   # a part to claim against 1a
    # 1b — 19009300 "Boudy u Jesenice", PSČ 26401 (eighteen obce, Sedlčany among them).
    obec(2049, 540391, "Jesenice", "26401", (49.6125, 14.5150))
    part(21172, "cast_obce", 403407, "Boudy", (2049, 540391))
    obec(2134, 541281, "Sedlčany", "26401", (49.6600, 14.4260), 4000.0)
    obec(1955, 539325, "Jesenice", "25242")
    obec(2189, 541834, "Jesenice", "27033")
    obec(3687, 562122, "Boudy", "39804")
    part(13819, "cast_obce", 95486, "Boudy", (3687, 562122))
    part(28626, "katastralni_uzemi", 695483, "Boudy", (3687, 562122))
    part(21967, "cast_obce", 412961, "Boudy", (21966, 549894))
    # 1c — 18798279 Harrachov / "Nový Svět" / 701, and 19009248 Nový Hrádek / "Dlouhé" / Rzy.
    obec(5039, 577081, "Harrachov", "51246", (50.7720, 15.4300), 5000.0)
    part(9606, "cast_obce", 37231, "Harrachov", (5039, 577081))
    part(21784, "cast_obce", 410845, "Nový Svět", (5039, 577081))
    points.append(AddressPoint(kod_adm=81547374, obec_unit_id=5039, obec_kod=577081,
                               psc="51246", lat=50.78264, lon=15.41592, cislo_domovni=701,
                               cast_obce_unit_id=21784, cast_obce_kod=410845))
    obec(4829, 574287, "Nový Hrádek", "54901", (50.3570, 16.2400))
    for uid, level, code, name in ((14682, "cast_obce", 107310, "Dlouhé"),
                                   (14684, "cast_obce", 107344, "Nový Hrádek"),
                                   (14685, "cast_obce", 107352, "Rzy"),
                                   (29419, "katastralni_uzemi", 707317, "Dlouhé")):
        part(uid, level, code, name, (4829, 574287))
    # …and two "stored correctly" rows the street-named part must not move: 19083768's street
    # IS its claimed part (Smolná 28), 17936637's names a village of another obec (Janová).
    obec(759, 505391, "Bělá u Jevíčka", "56943", (49.6345, 16.7003))
    part(7255, "cast_obce", 1813, "Smolná", (759, 505391))
    part(16509, "cast_obce", 141551, "Smolná", (16508, 560600))
    points.append(AddressPoint(kod_adm=81000028, obec_unit_id=759, obec_kod=505391,
                               psc="56943", lat=49.6340, lon=16.7010, cislo_domovni=28,
                               cast_obce_unit_id=7255, cast_obce_kod=1813))
    obec(3141, 554821, "Ostrava", "72525", (49.8209, 18.2625), 12000.0)
    part(3142, "cast_obce", 414557, "Polanka nad Odrou", (3141, 554821))
    obec(4900, 570346, "Janová", "75501")
    part(4901, "cast_obce", 155292, "Janová", (4900, 570346))
    # 1d — 18625515: Praha holds Hanusova (Michle) AND Hanušova (Kbely), one accent-free key;
    # Liškova (Kamýk) and Lísková (Hloubětín) are the same, with a háček on both.
    praha = (3214, 554782)
    obec(*praha, "Praha", "14000", (50.0755, 14.4378), 12000.0)
    for uid, code, name in ((22197, 490130, "Michle"), (20932, 400777, "Kbely"),
                            (15872, 128457, "Kamýk"), (22189, 490059, "Hloubětín")):
        part(uid, "cast_obce", code, name, praha)
    street(447340, 67330, "Hanusova", praha, ((1001, 22197, 490130, 50.0430, 14.4470),), "14000")
    street(447358, 43703, "Hanušova", praha, ((1002, 20932, 400777, 50.1300, 14.5400),), "19700")
    street(500810, 54911, "Liškova", praha, ((1003, 15872, 128457, 50.0200, 14.4400),), "14200")
    street(485926, 71938, "Lísková", praha, ((1004, 22189, 490059, 50.1050, 14.5300),), "19800")
    # 1e — 19013478 "ČSA" in Moravská Třebová: the register's Čs. armády.
    mt = (5149, 578444)
    obec(*mt, "Moravská Třebová", "57101", (49.7570, 16.6640), 4000.0)
    street(561517, 61133, "Čs. armády", mt, ((7, 21813, 411183, 49.7580, 16.6650),), "57101")
    # 1f — 19120281: PSČ 68601 is five obce, "Na Splávku" is a street of one of them (and of
    # Prostějov, which the PSČ does not serve); the pin is in Staré Město.
    for uid, code, name, at in ((6279, 592005, "Uherské Hradiště", (49.0690, 17.4600)),
                                (2872, 550752, "Staré Město", (49.0750, 17.4300)),
                                (6305, 592293, "Kostelany nad Moravou", None),
                                (6329, 592587, "Staré Hutě", None),
                                (6333, 592633, "Stupava", None)):
        obec(uid, code, name, "68601", at, 2000.0)
    street(611034, 57541, "Na Splávku", (6279, 592005),
           ((1201, 21936, 412619, 49.0700, 17.4650),), "68601")
    obec(3000, 589250, "Prostějov", "79601")
    street(517925, 30242, "Na splávku", (3000, 589250),
           ((1301, 8342, 18864, 49.4700, 17.1100),), "79601")
    # 2a / 2b / 2c / 2d — the reader rows: the towns their readings name, and what else
    # carries their PSČ (the pins sit there).
    obec(3140, 553816, "Koloveč", "34543", (49.4930, 13.0960), 2000.0)
    street(100005, 67486, "Růžová", (3140, 553816), ((350, 11795, 68578, 49.4920, 13.0970),),
           "34543")
    obec(3188, 554391, "Únějovice", "34543", (49.4690, 13.1040), 2000.0)
    obec(2288, 544256, "České Budějovice", "37001", (48.9750, 14.4800))
    street(75817, 66556, "Pabláskova", (2288, 544256),
           ((1501, 21207, 403920, 48.9800, 14.4600),), "37001")
    obec(1559, 535176, "Planá", "37001", (48.9520, 14.4420), 2000.0)
    obec(4721, 573191, "Sadová", "50315", (50.3010, 15.6920))
    obec(4461, 570451, "Nechanice", "50315", (50.2570, 15.6330), 2000.0)
    obec(4398, 569810, "Hradec Králové", "50002", (50.2092, 15.8328), 5000.0)
    street(128899, 52536, "Masarykovo náměstí", (4398, 569810),
           ((1601, 21700, 409804, 50.2090, 15.8300),), "50002")
    return mirror
