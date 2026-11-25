"""Which properties the register's building reads serve: none that holds an ad of a plot or of `ostatni`
(operator ruling 2026-11-23, "if any of the cards are pozemek or ostatní, then no RÚIAN card").

A door is the address the ad states, not a claim that the property is that building: a plot ad gives the
address of the house it belongs to or lies beside, and reading the building through it would describe a
neighbour's. One ad of either category is enough, whichever ad the property shows. Every read that goes
from a property through its door to a building takes this one predicate.
"""

from __future__ import annotations


def holds_no_building_ad(property_alias: str) -> str:
    """SQL, true when the property holds an ad the building reads do not serve."""
    return (f"exists (select 1 from listings scope_ad where scope_ad.property_id = {property_alias}.id"
            " and scope_ad.category_main in ('pozemek', 'ostatni'))")
