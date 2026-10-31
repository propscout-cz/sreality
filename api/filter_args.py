"""Argument models generated from toolkit/filter_registry, so each filter is declared once for every caller."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, Set
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, create_model, model_validator

from api.location_filter import DistrictChip, MapBounds, MapCircle, one_area
from toolkit import filter_registry as fr

_SCALARS: dict[fr.FilterType, type] = {
    fr.FilterType.INT: int, fr.FilterType.FLOAT: float, fr.FilterType.BOOL: bool, fr.FilterType.STRING: str,
}
INT4_MAX = 2**31 - 1


def as_str_list(v: Any) -> Any:
    """A bare string is a one-item list (callers still send `furnished="ano"`); None and lists pass."""
    return [v] if isinstance(v, str) else v


class Args(BaseModel):
    """Every argument model: an unknown key is refused, never dropped."""

    model_config = ConfigDict(extra="forbid")


class _GatedRecency(Args):
    # The base of a model carrying both max_age_days and lifecycle: a cohort applies the recency window only under
    # lifecycle='active' (toolkit/comparables._lifecycle_where), so any other pairing is refused, not dropped.
    @model_validator(mode="after")
    def _recency_needs_the_active_gate(self) -> Self:
        if self.max_age_days is not None and self.lifecycle != "active":
            raise ValueError("max_age_days applies only with lifecycle='active'")
        return self


# The composite filters, strict: the stored Watchdog blobs keep parsing through the lenient models they extend.
class Chip(DistrictChip):
    model_config = ConfigDict(extra="forbid")


class Box(MapBounds):
    model_config = ConfigDict(extra="forbid")


class Circle(MapCircle):
    model_config = ConfigDict(extra="forbid")


class MapAreaArgs(Args):
    """The map area a tool takes, as Browse filters on it: a box or a circle, never both (api/location_filter)."""

    bounds: Box | None = Field(
        None, description="The map box: a property matches when its point lies inside, as on Browse's map. Null = "
        "anywhere.")
    circle: Circle | None = Field(
        None, description="The map circle: a property matches when its point lies within radius_m metres of the "
        "centre, as Browse's centre + radius. Never with bounds. Null = anywhere.")

    @model_validator(mode="after")
    def _one_area(self) -> Self:
        one_area(self.bounds, self.circle)
        return self


class CityIndexRule(Args):
    index_name: str = Field(description="A city index slug, e.g. bezpecnost or celkove_hodnoceni.")
    # Exactly the operators curated_cities_matching evaluates (migration 436).
    op: Literal[">=", "<=", ">", "<", "==", "!="] = Field(description="How the city's index value compares.")
    value: float = Field(description="The value the city's index is compared with.")


class SetScope(Args):
    # frontend/src/lib/setScope.ts SetScope; membership is judged under the reader's own login (migration 629).
    set: Literal["in", "out"] | None = Field(
        None, description="'in' = a member of any element of the set, 'out' = of none (the id lists are then "
        "ignored, as on Browse); null = no whole-set side.")
    in_: list[int] = Field([], alias="in", description="Element ids the property must be in.")
    out: list[int] = Field([], description="Element ids the property must not be in; an id here wins over `in`.")

    # setScope.normalizeScope, so a scope selects what Browse makes of it: the 'out' side empties both lists, an id
    # on both lists is out, an element on the in side supersedes the 'in' side, and the lists are deduplicated.
    @model_validator(mode="after")
    def _browses_normal_form(self) -> Self:
        if self.set == "out":
            self.in_, self.out = [], []
            return self
        self.out = list(dict.fromkeys(self.out))
        self.in_ = [i for i in dict.fromkeys(self.in_) if i not in self.out]
        if self.in_:
            self.set = None
        return self


_COMPOSITES: dict[fr.FilterType, Any] = {
    fr.FilterType.DISTRICT_CHIP_LIST: list[Chip], fr.FilterType.CITY_INDEX_RULE_LIST: list[CityIndexRule],
    fr.FilterType.SET_SCOPE: SetScope,
}


def agenda_ids(agenda: fr.Agenda) -> tuple[str, ...]:
    """The agenda's filter ids, in registry order."""
    return tuple(f.id for f in fr.REGISTRY.values() if agenda in f.agendas)


def _vocabulary(f: fr.FilterDef) -> tuple[Any, ...]:
    return tuple((f.constraints or {}).get("enum") or (e.value for e in f.enum_values or ()))


def _annotation(f: fr.FilterDef) -> Any:
    vocabulary = _vocabulary(f)
    if f.type is fr.FilterType.STRING_LIST:
        return list[Literal[vocabulary]] if vocabulary else list[str]
    if f.type in (fr.FilterType.STRING, fr.FilterType.INT) and vocabulary:
        return Literal[vocabulary]
    # KeyError: LOCATION has no wire form (a tool takes `bounds` and `districts` instead).
    return _COMPOSITES.get(f.type) or _SCALARS[f.type]


def _description(f: fr.FilterDef, text: str) -> str:
    unit = "; ".join(part for part in (f.unit and f"Unit: {f.unit}", f.basis and f"basis: {f.basis}") if part)
    return f"{text} {unit}." if unit else text


def filter_model(
    name: str, ids: Iterable[str], consumer: Callable[..., Any] | None, *, required: Set[str] = frozenset(),
    descriptions: Mapping[str, str] = MappingProxyType({}),
) -> type[Args]:
    """Args whose fields are these registry filters, each defaulted as `consumer` defaults it; a mapping consumer
    (None, the filter compiler) takes the registry's own defaults."""
    params = inspect.signature(consumer).parameters if consumer is not None else None
    ids = tuple(ids)
    fields: dict[str, Any] = {}
    for fid in ids:
        f = fr.REGISTRY[fid]
        if params is not None and fid not in params:  # a filter the consumer takes no parameter for could never reach it
            raise KeyError(f"{getattr(consumer, '__name__', consumer)} takes no {fid}")
        default = params[fid].default if params is not None else f.default
        annotation = _annotation(f)
        if default is None or fid in required:
            annotation = annotation | None
        if f.type is fr.FilterType.STRING_LIST:
            annotation = Annotated[annotation, BeforeValidator(as_str_list)]
        # Only the registry's `min` is a domain floor; its `max`/`step` are slider axes (price ≤ 100 000 Kč for
        # a sale, plots ≤ 5 000 m²) and would refuse real values. An integer's one ceiling is its int4 column.
        bounds = {"ge": (f.constraints or {}).get("min")}
        if f.type is fr.FilterType.INT and not _vocabulary(f):
            bounds["le"] = INT4_MAX
        fields[fid] = (annotation, Field(... if fid in required else default,
                                         description=_description(f, descriptions.get(fid, f.description)),
                                         **bounds))
    base = _GatedRecency if {"max_age_days", "lifecycle"} <= set(ids) else Args
    return create_model(name, __base__=base, **fields)


def filter_values(model: BaseModel) -> dict[str, Any]:
    """The model's registry-filter fields by id, a composite as its wire JSON; subject and tool options are no
    registry id."""
    return model.model_dump(include={k for k in type(model).model_fields if k in fr.REGISTRY}, by_alias=True)
