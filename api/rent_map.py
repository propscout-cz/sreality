"""Ingest the MF rent-map XLSX into rent_map_* (write path).

Parsing lives in `toolkit.rent_map`; the write lives here (out of the read-only
toolkit). Shared by `scripts.fetch_rent_map` and the `/admin/rent-map` upload
endpoint so both ingest identically. The reference rent itself is the SQL
measure `mf_reference()` (migration 565), which reads the refreshed cells; the
map's rent choropleth is the `rent_map` reference overlay
(`scripts.ingest_map_overlays`), re-rendered in the ingest's own transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

import logging
import re
from datetime import date
from typing import TYPE_CHECKING, Any

import requests

from scraper import contact
from scraper import db as scraper_db
from scripts.ingest_map_overlays import Refused as OverlayRefused, render_sql_section
from toolkit.rent_map import (
    MF_INFOGRAPHIC_URL,
    ParsedRentMap,
    parse_rent_map_xlsx,
    sha256_bytes,
    source_date_from_filename,
)

if TYPE_CHECKING:
    import psycopg

LOG = logging.getLogger("rent_map")

OVERLAY_SECTION = "rent_map"
_MF_BASE = "https://mf.gov.cz"
# Mozilla-compatible, as mf.gov.cz has always been sent, naming us like any honest crawler.
_UA = f"Mozilla/5.0 (compatible; {contact.USER_AGENT})"


def find_latest_xlsx_url(page_html: str) -> str | None:
    """The MF page lists the current file + historical ones; the current is
    always the newest date, so pick the max-dated Cenova-mapa .xlsx href."""
    hrefs = re.findall(
        r"""/assets/attachments/[^"'<> ]*?Cenova-mapa[^"'<> ]*?\.xlsx""",
        page_html,
        re.IGNORECASE,
    )
    if not hrefs:
        return None

    def date_key(href: str) -> str:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", href)
        return m.group(1) if m else ""

    best = max(hrefs, key=date_key)
    return best if best.startswith("http") else _MF_BASE + best


def fetch_latest_xlsx(
    source_url: str = MF_INFOGRAPHIC_URL, *, timeout: int = 60,
) -> tuple[bytes, str]:
    """Download the current MF rent-map XLSX. Returns (bytes, filename)."""
    headers = {"User-Agent": _UA}
    page = requests.get(source_url, timeout=timeout, headers=headers)
    page.raise_for_status()
    url = find_latest_xlsx_url(page.text)
    if not url:
        raise ValueError("rent map: no XLSX link found on MF page")
    resp = requests.get(url, timeout=timeout, headers=headers)
    resp.raise_for_status()
    return resp.content, url.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class RevisionOutcome:
    """What an ingest did: the new revision (None = file already known) and, when the map
    overlay could not be re-rendered under its guards, why — the revision still stands."""
    revision: int | None
    overlay_refused: str | None


def insert_revision(
    conn: "psycopg.Connection",
    parsed: ParsedRentMap,
    *,
    source_filename: str,
    file_sha256: str,
    source_date: date | None,
    uploaded_by: str | None,
) -> "RevisionOutcome":
    """Insert one revision + its values/adjustments, re-render the `rent_map` map
    overlay and refresh `rent_map_cells`, in one transaction.

    Returns the new source_revision, or None when this exact file (by
    sha256) was already ingested — re-fetching an unchanged file is a no-op.
    """
    territory_count = len({v.ruian_code for v in parsed.values})
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "select source_revision from rent_map_revisions "
            "where file_sha256 = %s",
            (file_sha256,),
        )
        existing = cur.fetchone()
        if existing is not None:
            LOG.info("rent map sha256 %s already ingested (rev %s)",
                     file_sha256[:12], existing[0])
            return RevisionOutcome(None, None)

        cur.execute(
            """
            insert into rent_map_revisions
              (source_date, source_filename, file_sha256, row_count, uploaded_by)
            values (%s, %s, %s, %s, %s)
            returning source_revision
            """,
            (source_date, source_filename, file_sha256, territory_count,
             uploaded_by),
        )
        (revision,) = cur.fetchone()

        with cur.copy(
            "copy rent_map_values (source_revision, ruian_code, level, kraj, "
            "ku_name, obec_name, vk, ref_rent_per_m2, "
            "ref_rent_novostavba_per_m2, data_coverage) from stdin"
        ) as copy:
            for v in parsed.values:
                copy.write_row((
                    revision, v.ruian_code, v.level, v.kraj, v.ku_name,
                    v.obec_name, v.vk, v.ref_rent_per_m2,
                    v.ref_rent_novostavba_per_m2, v.data_coverage,
                ))

        with cur.copy(
            "copy rent_map_adjustments (source_revision, vk, is_novostavba, "
            "attribute, czk_per_m2) from stdin"
        ) as copy:
            for a in parsed.adjustments:
                copy.write_row((
                    revision, a.vk, a.is_novostavba, a.attribute, a.czk_per_m2,
                ))

        # The map's rent choropleth is the `rent_map` reference overlay, re-rendered
        # from the rows just written in a savepoint of this transaction (a rolled-back
        # ingest leaves the previous tiles). A refused guard (datum, shrink) writes
        # nothing and must not block the rent data: the revision lands, the map keeps
        # its previous render, and fetch_rent_map.yml's re-render step fails loudly on
        # the same refusal. Any other failure rolls the whole ingest back, as a failed
        # matview refresh did.
        overlay_refused: str | None = None
        try:
            tiles = render_sql_section(conn, OVERLAY_SECTION)
        except OverlayRefused as exc:
            overlay_refused = str(exc)
            LOG.error("rent map: revision %d kept, but the map overlay was NOT "
                      "re-rendered and still shows the previous one: %s", revision, exc)
        else:
            LOG.info("rent map: overlay %s re-rendered (%d tiles)", OVERLAY_SECTION, tiles)

        # rent_map_cells is mf_reference()'s only input (migration 565), read by
        # every Browse and listing read. It publishes through the ONE chokepoint
        # (migration 578): CONCURRENTLY, so no reader ever blocks on an ingest.
        # Joining the ingest's own transaction (a savepoint here) keeps the
        # invariant: a rolled-back ingest leaves no refresh and no stamp claiming
        # freshness the matview does not have. The registry says host =
        # 'api-request' — this artifact's freshness depends on someone loading a page.
        scraper_db.refresh_matview(conn, "rent_map_cells")

    LOG.info("rent map: ingested revision %d (%d territories, %d adjustments)",
             revision, territory_count, len(parsed.adjustments))
    return RevisionOutcome(revision, overlay_refused)


def ingest_bytes(
    conn: "psycopg.Connection",
    data: bytes,
    *,
    source_filename: str,
    uploaded_by: str | None,
) -> dict[str, Any]:
    """Parse + ingest an XLSX byte blob. Returns a small status dict."""
    parsed = parse_rent_map_xlsx(
        data, source_date=source_date_from_filename(source_filename)
    )
    file_sha256 = sha256_bytes(data)
    outcome = insert_revision(
        conn, parsed,
        source_filename=source_filename,
        file_sha256=file_sha256,
        source_date=parsed.source_date,
        uploaded_by=uploaded_by,
    )
    revision = outcome.revision
    return {
        "ingested": revision is not None,
        "source_revision": revision,
        "overlay_refused": outcome.overlay_refused,
        "source_date": parsed.source_date.isoformat() if parsed.source_date else None,
        "source_filename": source_filename,
        "file_sha256": file_sha256,
        "territory_count": len({v.ruian_code for v in parsed.values}),
        "adjustment_count": len(parsed.adjustments),
    }


def current_revision(conn: "psycopg.Connection") -> dict[str, Any] | None:
    """Summary of the latest ingested revision, or None if none yet."""
    with conn.cursor() as cur:
        cur.execute(
            "select source_revision, source_date, source_filename, row_count, "
            "uploaded_by, uploaded_at from rent_map_revisions "
            "order by source_revision desc limit 1"
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "source_revision": row[0],
        "source_date": row[1].isoformat() if row[1] else None,
        "source_filename": row[2],
        "row_count": row[3],
        "uploaded_by": row[4],
        "uploaded_at": row[5].isoformat() if row[5] else None,
    }


def list_revisions(
    conn: "psycopg.Connection", *, limit: int = 50
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "select source_revision, source_date, source_filename, row_count, "
            "uploaded_by, uploaded_at from rent_map_revisions "
            "order by source_revision desc limit %s",
            (limit,),
        )
        rows = cur.fetchall()
    return [
        {
            "source_revision": r[0],
            "source_date": r[1].isoformat() if r[1] else None,
            "source_filename": r[2],
            "row_count": r[3],
            "uploaded_by": r[4],
            "uploaded_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]
