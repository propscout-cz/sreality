"""Argument models generated from toolkit/filter_registry, so each filter is declared once for every caller."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, Set
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, create_model, model_validator

from toolkit import filter_registry as fr

_SCALARS: dict[fr.FilterType, type] = {
    fr.FilterType.INT: int, fr.FilterType.FLOAT: float, fr.FilterType.BOOL: bool, fr.FilterType.STRING: str,
}
_INT4_MAX = 2**31 - 1


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


def agenda_ids(agenda: fr.Agenda) -> tuple[str, ...]:
    """The agenda's filter ids, in registry order."""
    return tuple(f.id for f in fr.REGISTRY.values() if agenda in f.agendas)


def _annotation(f: fr.FilterDef) -> Any:
    vocabulary = tuple((f.constraints or {}).get("enum") or (e.value for e in f.enum_values or ()))
    if f.type is fr.FilterType.STRING_LIST:
        return list[Literal[vocabulary]] if vocabulary else list[str]
    if f.type is fr.FilterType.STRING and vocabulary:
        return Literal[vocabulary]
    return _SCALARS[f.type]  # KeyError: a composite filter (place, chips, rules, set scope) has no scalar form


def _description(f: fr.FilterDef, text: str) -> str:
    unit = "; ".join(part for part in (f.unit and f"Unit: {f.unit}", f.basis and f"basis: {f.basis}") if part)
    return f"{text} {unit}." if unit else text


def filter_model(
    name: str, ids: Iterable[str], consumer: Callable[..., Any], *, required: Set[str] = frozenset(),
    descriptions: Mapping[str, str] = MappingProxyType({}),
) -> type[Args]:
    """Args whose fields are these registry filters, each defaulted as `consumer` defaults it."""
    params = inspect.signature(consumer).parameters
    ids = tuple(ids)
    fields: dict[str, Any] = {}
    for fid in ids:
        f = fr.REGISTRY[fid]
        if fid not in params:  # a filter the consumer takes no parameter for could never reach it
            raise KeyError(f"{getattr(consumer, '__name__', consumer)} takes no {fid}")
        default = params[fid].default
        annotation = _annotation(f)
        if default is None or fid in required:
            annotation = annotation | None
        if f.type is fr.FilterType.STRING_LIST:
            annotation = Annotated[annotation, BeforeValidator(as_str_list)]
        # Only the registry's `min` is a domain floor; its `max`/`step` are slider axes (price ≤ 100 000 Kč for
        # a sale, plots ≤ 5 000 m²) and would refuse real values. An integer's one ceiling is its int4 column.
        bounds = {"ge": (f.constraints or {}).get("min")}
        if f.type is fr.FilterType.INT:
            bounds["le"] = _INT4_MAX
        fields[fid] = (annotation, Field(... if fid in required else default,
                                         description=_description(f, descriptions.get(fid, f.description)),
                                         **bounds))
    base = _GatedRecency if {"max_age_days", "lifecycle"} <= set(ids) else Args
    return create_model(name, __base__=base, **fields)


def filter_values(model: BaseModel) -> dict[str, Any]:
    """The model's registry-filter fields by id; subject and tool options are no registry id."""
    return {k: getattr(model, k) for k in type(model).model_fields if k in fr.REGISTRY}
