"""The apply scope as data: the operator's `autodedup_apply_scope` row parsed, and its predicate.

Moved verbatim out of `autodedup/apply.py` (which imports every name back) so a reader that only
asks "is this advert inside the scope?" (the wave shadow's `--scope apply`) need not import the
apply path, whose importers `test_apply` keeps to the lane (`test_nothing_but_the_lane_...`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

SCOPE_SETTING: str = "autodedup_apply_scope"
DEFAULT_MAX_CLUSTERS_PER_RUN: int = 200
SCOPE_KEYS: tuple[str, ...] = (
    "category_types", "blocks", "listing_ids", "all_blocks", "max_clusters_per_run",
)

# A scope block, spelled as the export lane spells it: `town:<code>` (an advert's
# listing_location.obec_kod) / `quarter:<code>` (its cast_obce_kod) — and `district:<code>`
# (its okres_kod, E961), which the export lane has no block for: the real-time lane's `okres:`.
GRAINS: tuple[str, ...] = ("town", "quarter", "district")

# E904 — why a group is outside the scope, counted per reason in the run summary. Order is the
# order a group's reason is picked in when its members fall outside for several.
OUT_CATEGORY = "category_outside_scope"
OUT_BLOCKS = "member_outside_blocks"
OUT_NO_LOCATION = "member_without_location"
OUT_LISTING_IDS = "member_outside_listing_ids"
OUT_OF_SCOPE_REASONS: tuple[str, ...] = (OUT_CATEGORY, OUT_BLOCKS, OUT_NO_LOCATION,
                                         OUT_LISTING_IDS)


@dataclass(frozen=True)
class Scope:
    """Which groups a run may touch. `None` = unrestricted on that axis; an EMPTY set admits
    nothing (a narrowing that intersected to nothing must not read as 'no filter')."""

    category_types: frozenset[str] | None = None
    blocks: frozenset[str] | None = None
    listing_ids: frozenset[int] | None = None
    all_blocks: bool = False
    max_clusters_per_run: int = DEFAULT_MAX_CLUSTERS_PER_RUN

    def to_json(self) -> dict[str, Any]:
        return {
            "category_types": sorted(self.category_types) if self.category_types is not None
            else None,
            "blocks": sorted(self.blocks) if self.blocks is not None else None,
            "listing_ids": len(self.listing_ids) if self.listing_ids is not None else None,
            "all_blocks": self.all_blocks,
            "max_clusters_per_run": self.max_clusters_per_run,
        }

    def live_problems(self) -> list[str]:
        """Why this scope may not drive a LIVE run; empty when it may. An empty list names
        nothing, like an absent key: no deal types or no area merges nothing (E904)."""
        problems: list[str] = []
        if not self.category_types:
            problems.append("the live scope names no category_types")
        if not self.blocks and not self.listing_ids and not self.all_blocks:
            problems.append(
                "the live scope names no area: set blocks, listing_ids, or all_blocks=true"
            )
        return problems

    def outside(self, listing: "Member") -> str | None:
        """Why one advert is outside the scope, None when it is inside (E904). Inside the blocks
        means LOCATED in one: its live obec_kod names a `town:` of the scope, its cast_obce_kod
        a `quarter:` or its okres_kod a `district:` (legacy_retire.AREA_SQL's area); an advert
        with none of the three codes is in none."""
        if self.category_types is not None and listing.category_type not in self.category_types:
            return OUT_CATEGORY
        if self.blocks is not None:
            if (listing.obec_kod is None and listing.cast_obce_kod is None
                    and listing.okres_kod is None):
                return OUT_NO_LOCATION
            if (f"town:{listing.obec_kod}" not in self.blocks
                    and f"quarter:{listing.cast_obce_kod}" not in self.blocks
                    and f"district:{listing.okres_kod}" not in self.blocks):
                return OUT_BLOCKS
        if self.listing_ids is not None and listing.listing_id not in self.listing_ids:
            return OUT_LISTING_IDS
        return None

    def group_outside(self, members: Sequence["Member"]) -> str | None:
        """Why a group is outside the scope (the first of OUT_OF_SCOPE_REASONS any member
        gives), None when every member is inside; the advert its merge would carry along is
        `_set_reasons`' (SKIP_CARRIES_OUT_OF_SCOPE)."""
        found = {self.outside(m) for m in members}
        return next((why for why in OUT_OF_SCOPE_REASONS if why in found), None)


def normalize_block(raw: Any) -> str:
    """`town:563510` / `quarter:490245` / `district:3209`; anything else is refused."""
    grain, _, code = str(raw).strip().lower().partition(":")
    if grain not in GRAINS or not code.isdigit():
        raise ValueError(
            f"block {raw!r}: expected town:<code>, quarter:<code> or district:<code>")
    return f"{grain}:{int(code)}"


def _words(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [w for w in value.replace("/", " ").split() if w]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(w).strip() for w in value if str(w).strip()]
    return [str(value).strip()]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"expected a boolean, got {value!r}")


def scope_fields(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and normalise the scope keys present in `raw` (a setting or lane args). A key
    set to null is absent: the seeded setting spells every key so the operator sees them."""
    raw = raw or {}
    unknown = sorted(set(raw) - set(SCOPE_KEYS))
    if unknown:
        raise ValueError(f"unknown scope key(s): {', '.join(unknown)}")
    raw = {key: value for key, value in raw.items() if value is not None}
    out: dict[str, Any] = {}
    if "category_types" in raw:
        out["category_types"] = frozenset(w.lower() for w in _words(raw["category_types"]))
    if "blocks" in raw:
        out["blocks"] = frozenset(normalize_block(w) for w in _words(raw["blocks"]))
    if "listing_ids" in raw:
        out["listing_ids"] = frozenset(int(w) for w in _words(raw["listing_ids"]))
    if "all_blocks" in raw:
        out["all_blocks"] = _truthy(raw["all_blocks"])
    if "max_clusters_per_run" in raw:
        value = int(raw["max_clusters_per_run"])
        if value < 1:
            raise ValueError(f"max_clusters_per_run must be >= 1, got {value}")
        out["max_clusters_per_run"] = value
    return out


def _int(value: Any) -> int | None:
    return int(value) if value is not None else None


@dataclass(frozen=True)
class Member:
    """One advert as the plan reads it: its property, deal type and category, and where it IS
    (its live listing_location codes, all None without a row)."""

    listing_id: int
    property_id: int | None
    category_type: str | None
    category_main: str | None
    obec_kod: int | None = None
    cast_obce_kod: int | None = None
    okres_kod: int | None = None

    @classmethod
    def read(cls, row: Sequence[Any]) -> "Member":
        """A row of PROPERTY_LISTINGS_SQL / LOCK_PROPERTY_LISTINGS_SQL, or of MEMBERS_SQL
        after its cluster_key."""
        lid, pid, ctype, cmain, obec, cast, okres = row
        return cls(int(lid), _int(pid), ctype, cmain, _int(obec), _int(cast), _int(okres))
