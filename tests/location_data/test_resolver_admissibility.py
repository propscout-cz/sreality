"""Admissibility — the ONE gate, evaluated once and handed to every step.

`bind.admissible()` refuses four classes: `subject_scoped=false` (the remax carousel),
`licence_class='ephemeral_display_only'` (Mapy), a portal-proprietary identifier, and a
claim the normalizer rejected. It used to live in the survivorship evaluator, which applied
it — while candidate generation and position assignment did not. So the carousel street
ranked a candidate, that candidate carried the admin chain, and the preserve-if-null
registry fill wrote the poisoned address back out as `registry_derived`.

Refused means "may not win", never "discarded": the claim row is still in
`location_claims`, and W2-a additionally puts the licence half of the gate in the claim
SELECT itself, so a Mapy-class coordinate is not refused at the winner — it is never read.
"""

from __future__ import annotations

from datetime import datetime, timezone

from location_data.resolver import bind as step_bind
from location_data.resolver import core, normalize, resolve_db
from location_data.resolver.types import Claim
from location_data.resolver.version import RESOLVER_VERSION
from tests.location_data import mini_mirror as mm

# The Prague address the mini-mirror carries end to end.
STREET = "Nad Bořislavkou 487/40"


def _resolve(claims):
    return core.resolve(
        claims, mm.context(), resolver_version=RESOLVER_VERSION,
        registry_version="ruian:2026-07-31",
    )


def _reason(claim):
    return step_bind.admissible(claim, normalize.normalize_all([claim]).get(claim.id))


# ------------------------------------------------------------------- the four classes


def test_the_four_refusals_name_themselves():
    assert _reason(mm.claim(1, "street_name", value_text=STREET, subject_scoped=False)) == (
        "not_subject_scoped"
    )
    assert _reason(
        mm.claim(2, "coordinate", lat=50.0, lon=14.0, licence_class="ephemeral_display_only")
    ) == "licence_ephemeral"
    assert _reason(mm.claim(3, "portal_admin_id", value_text="5122")) == (
        "portal_proprietary_identifier"
    )
    assert _reason(mm.claim(4, "street_name", value_text=STREET)) is None


def test_the_licence_gate_is_in_the_claim_read_not_only_in_the_code():
    """Migration 384 spent three CHECK constraints and a `position_licence_class` column on
    each store of record to make a Mapy coordinate unstorable. `listing_location` has no such
    column because the guard moved upstream and got stronger: the resolver's own projection
    refuses to SELECT one."""
    rail = ", ".join(f"'{c}'" for c in resolve_db.ADMISSIBLE_LICENCE_CLASSES)
    assert resolve_db.ADMISSIBLE_LICENCE_CLASSES == ("portal", "operator")
    for sql in (resolve_db._CLAIMS_SQL, resolve_db._CLAIMS_BULK_SQL):
        flat = " ".join(sql.split()).lower()
        # The SQL is BUILT from the tuple the wave shadow reads too: one list, two readers.
        assert f"and c.licence_class in ({rail})" in flat, flat
        assert "and (c.licence_class = 'operator' or exists (" in flat, flat


def test_the_one_read_takes_one_claim_per_type_the_operators_first():
    """Location W3: the store holds one claim per listing, type and author, and the read one
    per listing and type, the operator's ahead of the portal's (ruling Q5). No version window:
    a retired version's claim stays readable until a re-mine changes it, so a bump can never
    black a listing out (W11) nor a partial re-mine blank a type (W18-b)."""
    for sql in (resolve_db._CLAIMS_SQL, resolve_db._CLAIMS_BULK_SQL):
        flat = " ".join(sql.split()).lower()
        assert "select distinct on (c.listing_id, c.claim_type) c.*" in flat, flat
        # Operator first, then the newest observation: ids no longer order by recency once a
        # changed value is an UPDATE in place.
        assert ("order by c.listing_id, c.claim_type, (c.licence_class = 'operator') desc, "
                "c.first_observed_at desc, c.id desc") in flat, flat
        for gone in (" over (", "version <=", "newest_version", "portal_contract_entries pce"):
            assert gone not in flat, gone
        # A type the active contract no longer declares is not read: an EXISTS, not a join,
        # because the active contract may declare one type on several surfaces.
        assert ("exists ( select 1 from portal_contract_entries ace join portal_contracts act "
                "on act.id = ace.contract_id and act.is_active where act.source = c.source "
                "and ace.claim_type = c.claim_type))") in flat, flat


class _ClaimCursor:
    """A fake that OBEYS the claim read's rails instead of ignoring them: it refuses a
    statement that does not carry them, then applies them to its own rows, so the tests below
    go red both when a rail leaves the SQL and when the loader stops using that SQL."""

    DECLARED = {"sreality": ("obec_name", "coordinate"),
                "bazos": ("obec_name", "psc", "coordinate", "street_name")}

    #  id, listing_id, source, claim_type, licence_class
    ROWS = (
        (1, 77, "sreality", "obec_name", "portal"),
        (3, 77, "sreality", "obec_name", "operator"),       # outranks the portal's
        (4, 77, "sreality", "coordinate", "ephemeral_display_only"),  # a Mapy coordinate
        (5, 77, "sreality", "coordinate", "portal"),
        (10, 99, "bazos", "street_name", "portal"),         # first stated under bazos@7
        (11, 99, "bazos", "obec_name", "portal"),           # first stated under bazos@6
        (12, 99, "bazos", "psc", "portal"),
    )

    def __init__(self, declared: dict | None = None) -> None:
        self.declared = dict(self.DECLARED if declared is None else declared)
        self.result: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        assert "c.licence_class in ('portal', 'operator')" in flat, "licence rail missing"
        assert "ace.claim_type = c.claim_type" in flat, "active-contract type gate missing"
        assert "distinct on (c.listing_id, c.claim_type)" in flat, "one claim per type"
        chosen: dict[tuple, tuple] = {}
        for row in self.ROWS:
            _, listing, source, claim_type, licence = row
            if licence == "portal" and claim_type not in self.declared.get(source, ()):
                continue
            if licence not in ("portal", "operator"):
                continue
            key = (listing, claim_type)
            if key not in chosen or licence == "operator":
                chosen[key] = row
        self.result = sorted((_row(r[0], r[1], r[2], r[3]) for r in chosen.values()),
                             key=lambda row: (row[1], row[0]))

    def fetchall(self):
        return self.result


def _row(claim_id: int, listing_id: int, source: str = "sreality",
         claim_type: str = "obec_name") -> tuple:
    return (claim_id, listing_id, source, claim_type, "api_json",
            "portal_structured_field", "portal", mm._T0, "Praha", None, None, None,
            {}, None, None, "none", "high", True)


class _ClaimConn:
    def __init__(self, declared: dict | None = None) -> None:
        self.cur = _ClaimCursor(declared)

    def cursor(self):
        return self.cur


def test_one_claim_per_type_reaches_the_resolver_and_the_operators_wins():
    """Four rows for one listing: the Mapy coordinate is never read, and of the two town
    claims the operator's outranks the portal's."""
    loaded = resolve_db.load_claims_bulk(_ClaimConn(), [77])
    assert [c.id for c in loaded[77]] == [3, 5]


def test_claims_first_stated_by_an_older_version_stay_readable():
    """W11 / W18-b, by construction: provenance is not admissibility, so a type a newer
    version has not re-mined (or restated unchanged) keeps its claim."""
    loaded = resolve_db.load_claims_bulk(_ClaimConn(), [99])
    assert [c.id for c in loaded[99]] == [10, 11, 12]


def test_a_type_the_active_contract_no_longer_declares_is_not_read():
    """A deliberately dropped entry stops being evidence the moment it is dropped."""
    declared = dict(_ClaimCursor.DECLARED)
    declared["bazos"] = tuple(t for t in declared["bazos"] if t != "psc")
    loaded = resolve_db.load_claims_bulk(_ClaimConn(declared), [99])
    assert [c.id for c in loaded[99]] == [10, 11]


# --------------------------------------------------------------- the carousel street


def test_a_carousel_street_never_wins_the_street_field():
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "street_name", value_text=STREET),
        mm.claim(3, "street_name", value_text="Milady Horákové 12", subject_scoped=False),
    ])
    assert resolution.street_name == "Nad Bořislavkou"


def test_a_carousel_street_never_ranks_a_candidate_or_carries_the_admin_chain():
    """With NO admissible street claim the listing must resolve at the obec rung, not at
    whatever address the carousel happened to name."""
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "street_name", value_text=STREET, subject_scoped=False),
        mm.claim(3, "house_number_cp", value_text="487", subject_scoped=False),
    ])
    assert resolution.street_name is None
    assert resolution.ruian_adm_kod is None
    assert resolution.granularity == "obec"


def test_a_carousel_coordinate_never_becomes_the_pin():
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "coordinate", lat=49.5936, lon=17.2987, subject_scoped=False),
        mm.claim(3, "coordinate", lat=50.0755, lon=14.4378),
    ])
    assert (resolution.lat, resolution.lon) == (50.0755, 14.4378)


# ------------------------------------------------------------ purity: no local timezone


def test_a_naive_and_an_aware_claim_set_resolve_identically():
    """`datetime.timestamp()` on a naive value silently applies the HOST's timezone, so the
    same claims would hash differently on two machines."""

    def _claims(tzinfo):
        moment = datetime(2026, 8, 1, 12, 0, tzinfo=tzinfo)
        return [
            Claim(
                id=i, listing_id=900001, source="sreality", claim_type=claim_type,
                surface="api_json", extraction_method="portal_structured_field",
                licence_class="portal", observed_at=moment,
                value_text=value, subject_scoped=True,
            )
            for i, (claim_type, value) in enumerate(
                (("obec_name", "Praha"), ("street_name", STREET)), start=1
            )
        ]

    naive = _resolve(_claims(None))
    aware = _resolve(_claims(timezone.utc))
    assert naive.claim_set_hash == aware.claim_set_hash
    assert naive.street_name == aware.street_name
    assert naive.granularity == aware.granularity
