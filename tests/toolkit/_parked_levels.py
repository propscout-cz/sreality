"""The four filters of the parked condition levels (rule 14), which no surface may offer."""

_PARKED_CONDITION_LEVELS = frozenset({
    "building_condition_level_min", "building_condition_level_max",
    "apartment_condition_level_min", "apartment_condition_level_max",
})
