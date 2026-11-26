"""Parse ČSÚ DataStat JSON-stat population exports, by municipality code.

Download the source JSON from the Czech Statistical Office DataStat portal:

    https://data.csu.gov.cz/datastat/data/VYBER/OBY02AT02

(dataset "Počet obyvatel v obcích k 1. 1." — population in municipalities as
of 1 January). Save it as ``data/csu_population.json``;
``scripts/load_obec_population.py`` loads it into ``obec_population`` by the
municipality code, which IS the RÚIAN obec code. A town is matched by its code,
never by its name (1,635 of 6,258 town names are shared).

This replaced the Wikidata SPARQL fetcher (deleted 2026-10-25)
as the population source: the operator now downloads the official ČSÚ file
directly instead of relying on a third-party mirror.

File shape (JSON-stat 2.0)
--------------------------
A flat ``value`` array indexed by the cross product of the dimensions named
in ``id`` — here ``IndicatorType × UZ25 × CasR``:

* ``IndicatorType`` — a single population-count indicator (``2406P``).
* ``UZ25`` ("Kraje a obce") — mixes 14 NUTS3 *kraj* codes (``CZ0xx``) with
  ~6 000 municipality codes. ``dimension.UZ25.category.child`` maps each kraj
  code to its municipality codes, which is how we tell municipalities apart
  from the kraj-level aggregates, which we drop.
* ``CasR`` — the year axis; we take the most recent year present.

JSON-stat is row-major over ``size``, so a cell's flat index is
``sum(coord_i * stride_i)`` where ``stride_i`` is the product of the sizes of
the dimensions after ``i``. ``value`` may be a dense list or a sparse
``{index: value}`` object; both are handled.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

LOG = logging.getLogger("csu_population")

# ČSÚ DataStat download URL for the population-in-municipalities dataset.
# Surfaced in the loader's argparse help and the workflow docs so the operator
# knows where the file comes from.
DATASTAT_URL = "https://data.csu.gov.cz/datastat/data/VYBER/OBY02AT02"


def _strides(sizes: list[int]) -> list[int]:
    """Row-major strides for a JSON-stat ``size`` vector."""
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    return strides


def _value_at(values: Any, flat: int) -> Any:
    """Read one cell from a dense list or sparse dict ``value`` container."""
    if isinstance(values, dict):
        return values.get(str(flat))
    if 0 <= flat < len(values):
        return values[flat]
    return None


def parse_population_by_code(doc: dict[str, Any]) -> dict[int, tuple[int, int]]:
    """Return ``{obec_code: (population, as_of_year)}`` for the latest year.

    Keys are the integer ČSÚ/RÚIAN municipality codes (the UZ25 child codes),
    which ARE the RÚIAN obec codes. Kraj-level aggregates are dropped.
    """
    dims: list[str] = doc["id"]
    sizes: list[int] = doc["size"]
    values = doc["value"]
    role = doc.get("role", {})
    geo_dim = (role.get("geo") or ["UZ25"])[0]
    time_dim = (role.get("time") or ["CasR"])[0]
    dim_pos = {name: i for i, name in enumerate(dims)}
    if geo_dim not in dim_pos or time_dim not in dim_pos:
        raise ValueError(f"JSON-stat missing geo/time dim (id={dims})")
    strides = _strides(sizes)
    geo_cat = doc["dimension"][geo_dim]["category"]
    geo_index: dict[str, int] = geo_cat["index"]
    child: dict[str, list[str]] = geo_cat.get("child", {})
    if not child:
        raise ValueError("JSON-stat geo dimension has no `child` map")
    muni_codes = {c for codes in child.values() for c in codes}

    time_cat = doc["dimension"][time_dim]["category"]
    time_index: dict[str, int] = time_cat["index"]
    latest_year_code = max(time_index, key=lambda y: int(y))
    latest_year = int(latest_year_code)
    time_pos = time_index[latest_year_code]

    base_coord = [0] * len(dims)
    base_coord[dim_pos[time_dim]] = time_pos

    out: dict[int, tuple[int, int]] = {}
    for code, gpos in geo_index.items():
        if code not in muni_codes:
            continue  # kraj-level aggregate
        try:
            code_int = int(code)
        except ValueError:
            continue
        coord = list(base_coord)
        coord[dim_pos[geo_dim]] = gpos
        raw = _value_at(values, sum(c * s for c, s in zip(coord, strides)))
        if raw is None:
            continue
        try:
            pop = int(round(float(raw)))
        except (TypeError, ValueError):
            continue
        if pop <= 0:
            continue
        out[code_int] = (pop, latest_year)
    LOG.info("Parsed population for %d obce (latest year %d)", len(out), latest_year)
    return out


def load_population_by_code(path: Path) -> dict[int, tuple[int, int]]:
    return parse_population_by_code(json.loads(Path(path).read_text(encoding="utf-8")))
