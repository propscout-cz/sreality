"""THE comparison of what an ad says with what the RÚIAN register states about its building: one verdict per fact
both sides state on the same basis, read by every surface that marks a difference. It says that the two differ,
never which is right (the register's lift data is old for old buildings; an ad is a seller's claim), and corrects
neither.

  lift          the ad's yes/no against the register's "with"/"without".
  floors        the register counts storeys below ground too, an ad only those above. So only one direction is a
                contradiction: an ad counting MORE storeys than the register, or a flat on a storey above the
                register's count. An ad counting fewer may be leaving out basements, and is no difference.
  construction  only where both vocabularies name the same class (masonry, panel, wood). "skelet", "smíšená",
                "montovaná", "ocelová" and the register's monolith and "other materials" have no counterpart.

A fact one side does not state, or states outside those classes, has no verdict (None)."""

from __future__ import annotations

from typing import Any

_LIFT = {1: True, 2: False}
_MASONRY, _PANEL, _WOOD = "masonry", "panel", "wood"
_AD_CONSTRUCTION = {"cihla": _MASONRY, "kamen": _MASONRY, "panel": _PANEL, "drevo": _WOOD, "roubena": _WOOD}
_REGISTER_CONSTRUCTION = {
    **dict.fromkeys((1, 2, 3, 5, 10), _MASONRY),
    **dict.fromkeys((4, 41, 43), _PANEL),
    **dict.fromkeys((6, 61, 62, 63, 64, 65, 66), _WOOD),
}

FACTS = ("lift", "floors", "construction")


def _code(coded: dict[str, Any] | None) -> int | None:
    return None if coded is None else coded["code"]


def _verdict(differs: bool, ad: Any) -> dict[str, Any]:
    return {"differs": differs, "ad": ad}


def compare(ad: dict[str, Any], building: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
    """The verdicts of one ad, or of the property's own shown values, against the building's register record
    (`api/property_building.load_building`)."""
    out: dict[str, dict[str, Any] | None] = dict.fromkeys(FACTS)

    register_lift = _LIFT.get(_code(building["lift"]))
    if register_lift is not None and ad["has_lift"] is not None:
        out["lift"] = _verdict(ad["has_lift"] != register_lift, ad["has_lift"])

    floors, total, floor = building["floors"], ad["total_floors"], ad["floor"]
    if floors is not None and (total is not None or floor is not None):
        # `listings.floor` is ground = 0, so the storey's ordinal is one more.
        above = (total is not None and total > floors) or (floor is not None and floor + 1 > floors)
        out["floors"] = _verdict(above, {"total_floors": total, "floor": floor})

    register_class = _REGISTER_CONSTRUCTION.get(_code(building["construction"]))
    ad_class = _AD_CONSTRUCTION.get(ad["building_type"])
    if register_class is not None and ad_class is not None:
        out["construction"] = _verdict(ad_class != register_class, ad["building_type"])
    return out
