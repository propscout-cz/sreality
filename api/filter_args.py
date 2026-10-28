"""Argument models generated from toolkit/filter_registry, so each filter is declared once for every caller."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Set
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, create_model

from toolkit import filter_registry as fr

_SCALARS: dict[fr.FilterType, type] = {
    fr.FilterType.INT: int, fr.FilterType.FLOAT: float, fr.FilterType.BOOL: bool, fr.FilterType.STRING: str,
}


def as_str_list(v: Any) -> Any:
    """A bare string is a one-item list (callers still send `furnished="ano"`); None and lists pass."""
    return [v] if isinstance(v, str) else v


class Args(BaseModel):
    """Every argument model: an unknown key is refused, never dropped."""

    model_config = ConfigDict(extra="forbid")


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


def filter_model(
    name: str, ids: Iterable[str], consumer: Callable[..., Any], *, required: Set[str] = frozenset(),
) -> type[Args]:
    """Args whose fields are these registry filters, each defaulted as `consumer` defaults it.

    A filter `consumer` takes no parameter for raises KeyError: it could never reach it.
    """
    params = inspect.signature(consumer).parameters
    fields: dict[str, Any] = {}
    for fid in ids:
        f = fr.REGISTRY[fid]
        if fid not in params:
            raise KeyError(f"{getattr(consumer, '__name__', consumer)} takes no {fid}")
        default = params[fid].default
        annotation = _annotation(f)
        if default is None or fid in required:
            annotation = annotation | None
        if f.type is fr.FilterType.STRING_LIST:
            annotation = Annotated[annotation, BeforeValidator(as_str_list)]
        # Only the registry's `min` is a domain floor; its `max`/`step` are slider axes (price ≤ 100 000 Kč for
        # a sale, plots ≤ 5 000 m²) and would refuse real values.
        floor = (f.constraints or {}).get("min")
        fields[fid] = (annotation, Field(... if fid in required else default, ge=floor, description=f.description))
    return create_model(name, __base__=Args, **fields)


def filter_values(model: BaseModel) -> dict[str, Any]:
    """The model's registry-filter fields by id; subject and tool options are no registry id."""
    return {k: getattr(model, k) for k in type(model).model_fields if k in fr.REGISTRY}
