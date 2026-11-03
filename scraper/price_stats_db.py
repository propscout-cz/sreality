"""Database I/O for price-stats datasets (psycopg, transaction pooler).

Dataset reads, the towns and keys a run fetches (`ruian_translation`, #2362), run
bookkeeping, per-month observation upserts, and the derived-metric recompute that
feeds the analysis tab + map choropleth, all keyed by RÚIAN town code. Pure SQL
only — the CAGR/yield math lives in `scraper.price_stats_metrics`.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from scraper.db import (  # noqa: F401 (re-export connect)
    connect,
    database_url,
    refresh_matview,
)
from scraper.price_stats_metrics import compute_city_metrics

LOG = logging.getLogger(__name__)

_DATASET_COLS = (
    "id, slug, name, description, category_main_cb, building_condition, "
    "building_type, ownership, usable_area_from, usable_area_to, distance, "
    "is_active, start_ym, end_ym, obec_ids"
)

# The key each town is fetched under: a key matched from ads feeds the fetch only when its town has no other row.
FETCH_KEYS_SQL = """
select distinct on (ruian_code) ruian_code as obec_id, source_key
  from ruian_translation
 where source = 'sreality_municipality' and ruian_level = 'obec'
 order by ruian_code, matched_by = 'ad_bound_obec', source_key::bigint
"""

# Active sreality ads confirm the rows they agree with and add an id no row holds once its ads name one town; a
# stored row stands, so an ad naming another town changes nothing and is reported (#2362 §4-5).
CONFIRM_TRANSLATION_SQL = """
with ads as (
  select l.raw_json #>> '{locality,municipality_id}' source_key, ll.obec_kod code, count(*) ads
    from listings l join listing_location ll on ll.listing_id = l.id
   where l.source = 'sreality' and l.is_active and ll.obec_kod is not null
     and l.raw_json #>> '{locality,municipality_id}' ~ '^[0-9]+$'
   group by 1, 2
), stored as (
  select source_key, ruian_code from ruian_translation
   where source = 'sreality_municipality' and ruian_level = 'obec'
), confirmed as (
  update ruian_translation t set last_confirmed_at = now()
    from ads a
   where t.source = 'sreality_municipality' and t.ruian_level = 'obec'
     and t.source_key = a.source_key and t.ruian_code = a.code
  returning 1
), added as (
  insert into ruian_translation
  select 'sreality_municipality', a.source_key, 'obec', min(a.code), 'ad_bound_obec', now()
    from ads a
   where not exists (select 1 from stored s where s.source_key = a.source_key)
   group by a.source_key having count(*) = 1
  returning source_key, ruian_code
)
select (select count(*) from confirmed) confirmed,
       (select jsonb_agg(added) from added) added,
       (select jsonb_agg(d) from (
          select a.source_key, s.ruian_code stored, a.code, a.ads
            from ads a left join stored s using (source_key)
           where s.ruian_code <> a.code
              or (s.source_key is null and exists
                   (select 1 from ads b where b.source_key = a.source_key and b.code <> a.code))) d) disputed
"""

COVERAGE_REMAINING_SQL = f"""
SELECT count(*)
  FROM ({FETCH_KEYS_SQL}) k
 WHERE (%(ids)s::bigint[] IS NULL OR k.obec_id = ANY(%(ids)s::bigint[]))
   AND NOT EXISTS (SELECT 1 FROM price_stat_observations o
                    WHERE o.dataset_id = %(did)s AND o.obec_id = k.obec_id)
   AND NOT EXISTS (SELECT 1 FROM price_stat_locality_no_data nd
                    WHERE nd.dataset_id = %(did)s AND nd.obec_id = k.obec_id)
"""

LOCALITIES_ORDERED_SQL = f"""
SELECT k.obec_id, k.source_key, coalesce(n.name, k.obec_id::text) AS name,
       coalesce(o.mf, nd.checked_at) AS last_fetched_at
  FROM ({FETCH_KEYS_SQL}) k
  LEFT JOIN ruian_place_names_public n ON n.level = 'obec' AND n.code = k.obec_id
  LEFT JOIN (
    SELECT obec_id, max(fetched_at) AS mf
      FROM price_stat_observations
     WHERE dataset_id = %(did)s
     GROUP BY obec_id
  ) o ON o.obec_id = k.obec_id
  LEFT JOIN price_stat_locality_no_data nd ON nd.dataset_id = %(did)s AND nd.obec_id = k.obec_id
 WHERE (%(obec_ids)s::bigint[] IS NULL OR k.obec_id = ANY(%(obec_ids)s::bigint[]))
   AND (nd.checked_at IS NULL OR nd.checked_at < now() - make_interval(days => %(ttl)s))
 ORDER BY coalesce(o.mf, nd.checked_at) ASC NULLS FIRST, name
"""


def load_active_datasets(conn: psycopg.Connection) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {_DATASET_COLS} FROM price_stat_datasets "
            "WHERE is_active ORDER BY id"
        )
        return cur.fetchall()


def get_dataset(conn: psycopg.Connection, dataset_id: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {_DATASET_COLS} FROM price_stat_datasets WHERE id = %s",
            (dataset_id,),
        )
        return cur.fetchone()


def coverage_remaining(
    conn: psycopg.Connection, dataset_id: int, obec_ids: list[int] | None
) -> int:
    """Towns (selected, or all) the dataset hasn't scraped yet — those with
    NEITHER an observation NOR a no-data marker. > 0 means a coverage pass is
    still incomplete; the workflow chains one more run only when a run shrank
    this count (periodic refresh is the weekly schedule's job)."""
    with conn.cursor() as cur:
        cur.execute(COVERAGE_REMAINING_SQL, {"did": dataset_id, "ids": obec_ids})
        return cur.fetchone()[0]


def localities_ordered(
    conn: psycopg.Connection,
    dataset_id: int,
    obec_ids: list[int] | None = None,
    *,
    no_data_ttl_days: int = 60,
) -> list[dict[str, Any]]:
    """Towns for a dataset run with the key each is fetched under, STALEST first.

    Ordered by each town's most-recent observation for THIS dataset
    (never-scraped first, then oldest). A time-budgeted / partial run thus
    always advances the towns that need it most, so full coverage is reached
    across runs (and then refreshes oldest-first) rather than re-doing town 1
    every run. `obec_ids` None = every translated town. Each row carries its
    `last_fetched_at` — the last fetch, else the last empty check, NULL only for a
    town never scraped — which orders the queue across datasets. The name is the
    mirror's; a code the mirror no longer serves keeps being fetched.

    Towns the scraper recently checked and found EMPTY (a
    price_stat_locality_no_data marker within `no_data_ttl_days`) are skipped,
    so the budget isn't spent re-scanning the dataless tail every run; they fall
    back in for re-verification once the marker ages past the TTL.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(LOCALITIES_ORDERED_SQL, {"did": dataset_id, "obec_ids": obec_ids, "ttl": no_data_ttl_days})
        return cur.fetchall()


def confirm_translation(conn: psycopg.Connection) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    """Run CONFIRM_TRANSLATION_SQL: (rows confirmed, rows added, disputed (key, town) pairs)."""
    with conn.cursor() as cur:
        cur.execute(CONFIRM_TRANSLATION_SQL)
        confirmed, added, disputed = cur.fetchone()
    return confirmed, added or [], disputed or []


def record_no_data(conn: psycopg.Connection, dataset_id: int, obec_id: int) -> None:
    """Mark a (dataset, town) as checked-and-empty (no prodej/pronájem data)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO price_stat_locality_no_data (dataset_id, obec_id, checked_at)
            VALUES (%s, %s, now())
            ON CONFLICT (dataset_id, obec_id)
            DO UPDATE SET checked_at = excluded.checked_at
            """,
            (dataset_id, obec_id),
        )


def clear_no_data(conn: psycopg.Connection, dataset_id: int, obec_id: int) -> None:
    """Drop the empty-marker for a town that now yields data."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM price_stat_locality_no_data WHERE dataset_id = %s AND obec_id = %s",
            (dataset_id, obec_id),
        )


def sweep_stale_runs(conn: psycopg.Connection, *, older_than_min: int = 90) -> int:
    """Mark price_stat_runs left 'running' (a hard-killed / timed-out job that
    couldn't finish_run) as failed, so the UI banner doesn't poll forever."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE price_stat_runs SET status = 'failed', finished_at = now(), "
            "error = COALESCE(error, 'run did not finish (timed out or killed)') "
            "WHERE status = 'running' "
            "AND started_at < now() - make_interval(mins => %s)",
            (older_than_min,),
        )
        return cur.rowcount


def start_run(
    conn: psycopg.Connection, dataset_id: int, *, cities_total: int = 0
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO price_stat_runs (dataset_id, cities_total) "
            "VALUES (%s, %s) RETURNING id",
            (dataset_id, cities_total),
        )
        return cur.fetchone()[0]


def update_run_progress(
    conn: psycopg.Connection, run_id: int, *, cities_done: int, observations: int
) -> None:
    """Incremental progress for the live Datasets-page banner (every city)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE price_stat_runs SET cities_done = %s, observations = %s "
            "WHERE id = %s",
            (cities_done, observations, run_id),
        )


def finish_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    status: str,
    localities: int = 0,
    observations: int = 0,
    error: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE price_stat_runs SET status = %s, localities = %s, "
            "observations = %s, error = %s, finished_at = now() WHERE id = %s",
            (status, localities, observations, error, run_id),
        )


def upsert_observations(
    conn: psycopg.Connection,
    *,
    dataset_id: int,
    obec_id: int,
    category_type_cb: int,
    months: list[dict[str, Any]],
    run_id: int | None,
) -> int:
    """Latest-wins upsert of one (dataset, town, category) monthly series."""
    if not months:
        return 0
    rows = [
        (
            dataset_id, obec_id, category_type_cb, m["year"], m["month"], m.get("price"),
            m.get("active_count"), m.get("new_count"), m.get("deleted_count"), run_id,
        )
        for m in months
    ]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO price_stat_observations (
                dataset_id, obec_id, category_type_cb, year, month, price,
                active_count, new_count, deleted_count, run_id, fetched_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (dataset_id, obec_id, category_type_cb, year, month) DO UPDATE SET
                price = EXCLUDED.price,
                active_count = EXCLUDED.active_count,
                new_count = EXCLUDED.new_count,
                deleted_count = EXCLUDED.deleted_count,
                run_id = EXCLUDED.run_id,
                fetched_at = now()
            """,
            rows,
        )
    return len(rows)


def recompute_metrics(
    conn: psycopg.Connection, dataset_id: int, *, window_years: int = 5
) -> int:
    """Recompute price_stat_city_metrics for a dataset from its observations."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT obec_id, category_type_cb, year, month, price, active_count "
            "FROM price_stat_observations WHERE dataset_id = %s "
            "ORDER BY obec_id, year, month",
            (dataset_id,),
        )
        obs = cur.fetchall()

    grouped: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for r in obs:
        g = grouped.setdefault(r["obec_id"], {"sale": [], "rent": []})
        g["sale" if r["category_type_cb"] == 1 else "rent"].append(r)

    rows: list[tuple[Any, ...]] = []
    for obec_id, g in grouped.items():
        m = compute_city_metrics(g["sale"], g["rent"], window_years=window_years)
        rows.append(
            (
                dataset_id, obec_id, m["window_years"],
                m["sale_latest_price"], m["sale_latest_ym"], m["sale_cagr_pct"],
                m["sale_months"], m["sale_min_active"], m["rent_latest_price"],
                m["rent_latest_ym"], m["rent_cagr_pct"], m["rent_months"],
                m["rent_min_active"], m["gross_yield_pct"],
            )
        )

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "DELETE FROM price_stat_city_metrics WHERE dataset_id = %s",
            (dataset_id,),
        )
        if rows:
            cur.executemany(
                """
                INSERT INTO price_stat_city_metrics (
                    dataset_id, obec_id, window_years,
                    sale_latest_price, sale_latest_ym, sale_cagr_pct,
                    sale_months, sale_min_active, rent_latest_price,
                    rent_latest_ym, rent_cagr_pct, rent_months, rent_min_active,
                    gross_yield_pct, computed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                rows,
            )
    return len(rows)


def refresh_choropleth(conn: psycopg.Connection) -> None:
    """Publish the map matview through the ONE chokepoint (migration 578):
    CONCURRENTLY with a first-populate fallback, stamped in-DB."""
    refresh_matview(conn, "price_stat_choropleth")
