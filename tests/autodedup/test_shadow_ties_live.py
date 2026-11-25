"""The wave shadow's `ids` block, executed: `shadow.tied_ads` reads every ruling the judge reads
(`evaluate.read_rulings`) and the property's siblings, in one read-only statement. Runs in CI's
DB job (`TEST_DATABASE_URL`); every test rolls back."""

from __future__ import annotations

import uuid
from typing import Any

from autodedup import shadow
from tests._live_property import REQUIRED_DB, _advert, _property

pytestmark = REQUIRED_DB


def test_a_must_not_link_partner_and_an_operator_merge_ride_with_the_mover(cur: Any) -> None:
    """A mover whose only ruling is an operator must-not-link still brings its partner, so the
    judge sees the pair in both arms; a guard's must-not-link is no ruling and stays out."""
    pid = _property(cur)
    mover, sibling = _advert(cur, pid, source="idnes"), _advert(cur, pid, source="bazos")
    partner, guarded, merged = (_advert(cur, _property(cur), source="idnes") for _ in range(3))
    for other, source in ((partner, "operator"), (guarded, "guard")):
        lo, hi = sorted((mover, other))
        cur.execute("insert into autodedup.must_not_link (listing_lo, listing_hi, source) "
                    "values (%s, %s, %s)", (lo, hi, source))
    cur.execute("insert into autodedup.operator_merges (merge_group_id, merged_at, member_ids, "
                "member_sides, member_property_ids) values (%s, now(), %s, %s, %s)",
                (uuid.uuid4(), [mover, merged], [0, 1], [pid, pid]))
    assert shadow.tied_ads(cur.connection, [mover]) == sorted({mover, sibling, partner, merged})


def test_crossing_pairs_read_the_live_store_by_the_movers_ids(cur: Any) -> None:
    """Both index arms run on the real schema: a mover as `listing_lo` and as `listing_hi`, an ad inside the cohort out."""
    pid = _property(cur)
    outside_lo, mover, inside, outside_hi = sorted(_advert(cur, pid, source="idnes") for _ in range(4))
    for lo, hi, zone in ((outside_lo, mover, "band"), (mover, outside_hi, "merge"), (mover, inside, "merge")):
        cur.execute("insert into autodedup.pairs (generation, listing_lo, listing_hi, probes, zone) "
                    "values ('rt', %s, %s, '{addr}', %s)", (lo, hi, zone))
    out = shadow.crossing_pairs(cur.connection, [mover], {mover, inside})
    assert out == {"movers": 1, "pairs": 2, "by_zone": {"merge": 1, "band": 1, "reject": 0},
                   "merge_pairs": [[mover, outside_hi]]}


def test_the_spanning_read_counts_the_valid_parts_of_the_old_doors_number(cur: Any) -> None:
    """Q23's stage-1 read on the real schema: a loser counts when its old door's č.p. (same typ_so) has valid points in
    2+ části of the obec. Two doors in one část, a closed point, a č.ev. of the same digits and a missing kód do not."""
    version = cur.execute(
        "insert into registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline, "
        "  loaded_at, is_current) values (%s, 'baseline', current_date, '{}'::jsonb, 't', 't', now(), false) "
        "returning id", (f"shadow-{uuid.uuid4()}",)).fetchone()[0]

    def unit(level: str, code: int, parent: int | None = None) -> int:
        return cur.execute(
            "insert into ruian_admin_units (level, code, name, name_norm, parent_id, path, valid_from, "
            "  first_version_id, last_version_id) values (%s::ruian_level, %s, %s, %s, %s, %s::ltree, "
            "  current_date - 30, %s, %s) returning id",
            (level, code, f"t {code}", f"t {code}", parent, f"t{code}", version, version)).fetchone()[0]

    obec = unit("obec", 990_801)
    part_a, part_b = unit("cast_obce", 990_811, obec), unit("cast_obce", 990_812, obec)

    def door(kod: int, cp: int, part: int, typ: str = "č.p.", closed: bool = False) -> None:
        cur.execute(
            "insert into ruian_address_points (kod_adm, obec_unit_id, obec_kod, cast_obce_unit_id, typ_so, "
            "  cislo_domovni, psc, plati_od, valid_to, first_version_id, last_version_id) "
            "values (%s, %s, 990801, %s, %s, %s, '19700', current_date - 30, %s, %s, %s)",
            (kod, obec, part, typ, cp, "2026-01-01" if closed else None, version, version))

    door(99_801, 1168, part_a)
    door(99_802, 1168, part_b)
    door(99_803, 512, part_a)
    door(99_804, 512, part_a)
    door(99_805, 512, part_b, closed=True)
    door(99_806, 700, part_a, typ="č.ev.")
    door(99_807, 700, part_b)
    movers = [{"listing_id": i, "old": {"ruian_adm_kod": kod}, "new": {"ruian_adm_kod": None}}
              for i, kod in ((11, 99_801), (12, 99_803), (13, 99_806), (14, 99_899))]
    movers.append({"listing_id": 15, "old": {"ruian_adm_kod": 99_802}, "new": {"ruian_adm_kod": 99_801}})
    assert shadow.spanning_losers(cur.connection, movers) == {11}
