"""Fetch + ingest the MF "Cenová mapa nájemného" XLSX.

Downloads the current file from the MF infografika page (or a local
`--source-path`), parses it, and appends a new `rent_map_revisions` row —
unless the file's sha256 already exists, in which case it's a no-op. The MF
data updates 4×/year, so the monthly job (scraper/jobs.py `rent_map`) mostly
no-ops. Every run then re-renders the map's `rent_map` overlay.

    python -m scripts.fetch_rent_map                     # fetch + ingest live
    python -m scripts.fetch_rent_map --source-path f.xlsx
    python -m scripts.fetch_rent_map --dry-run           # parse, then a dry render of the stored revision's
                                                         # overlay (needs SUPABASE_DB_URL); no write
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from api.rent_map import fetch_latest_xlsx, ingest_bytes
from scraper.db import connect
from scripts import ingest_map_overlays
from toolkit.rent_map import (
    MF_INFOGRAPHIC_URL,
    parse_rent_map_xlsx,
    sha256_bytes,
    source_date_from_filename,
)

LOG = logging.getLogger("fetch_rent_map")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=MF_INFOGRAPHIC_URL)
    parser.add_argument("--source-path", default=None,
                        help="ingest a local .xlsx instead of fetching")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.source_path:
        path = Path(args.source_path)
        data = path.read_bytes()
        filename = path.name
        LOG.info("rent map: read local file %s (%d bytes)", filename, len(data))
    else:
        data, filename = fetch_latest_xlsx(args.source_url)
        LOG.info("rent map: fetched %s (%d bytes)", filename, len(data))

    if args.dry_run:
        parsed = parse_rent_map_xlsx(
            data, source_date=source_date_from_filename(filename)
        )
        LOG.info(
            "DRY RUN sha256=%s source_date=%s territories=%d adjustments=%d",
            sha256_bytes(data)[:12], parsed.source_date,
            len({v.ruian_code for v in parsed.values}), len(parsed.adjustments),
        )
        return ingest_map_overlays.main(["--sections", "rent_map", "--dry-run"])

    with connect() as conn:
        result = ingest_bytes(
            conn, data,
            source_filename=filename,
            uploaded_by=os.environ.get("GITHUB_ACTOR") or "fetch_rent_map",
        )
        if result["ingested"]:
            LOG.info("INGESTED revision=%s source_date=%s territories=%d",
                     result["source_revision"], result["source_date"],
                     result["territory_count"])
        else:
            LOG.info("NO-OP: sha256=%s already ingested",
                     result["file_sha256"][:12])
    # fetch_rent_map.yml's last step, folded (#2025): a no-op month re-renders too, and a refused render exits 1.
    return ingest_map_overlays.main(["--sections", "rent_map"])


if __name__ == "__main__":
    sys.exit(main())
