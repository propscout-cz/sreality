"""Throwaway, never merged, read only: rows whose obec or část changed since W7's pre-load backup."""
import collections
import csv
import gzip
import io
import os

import psycopg

from scraper.image_storage import R2Client

PRAHA = 554782
raw = R2Client.from_env().download_bytes("backups/w7-registry/2026-11-02/listing_location.csv.gz")
before = {}
for row in csv.DictReader(io.StringIO(gzip.decompress(raw).decode())):
    before[int(row["listing_id"])] = (row["obec_kod"] or None, row["cast_obce_kod"] or None)
print("backup rows", len(before))

SQL = """select ll.listing_id, ll.obec_kod, ll.cast_obce_kod, l.is_active, l.source
           from listing_location ll join listings l on l.id = ll.listing_id"""
tally = collections.Counter()
by_source = collections.Counter()
samples = []
with psycopg.connect(os.environ["SUPABASE_DB_SESSION_URL"], autocommit=True) as conn, conn.cursor(name="c") as cur:
    cur.execute(SQL)
    for lid, obec, part, active, source in cur:
        old = before.get(lid)
        if old is None:
            continue
        o = str(obec) if obec is not None else None
        p = str(part) if part is not None else None
        if (o, p) == old:
            tally["same"] += 1
            continue
        praha = PRAHA in {obec, int(old[0]) if old[0] else None}
        kind = ("obec" if o != old[0] else "cast_lost" if p is None else "cast_gained" if old[1] is None else "cast_moved")
        key = ("praha" if praha else "non_praha", "active" if active else "inactive", kind)
        tally[key] += 1
        if not praha:
            by_source[source, kind] += 1
            if len(samples) < 30:
                samples.append((lid, source, old, (o, p)))
for key, n in sorted(tally.items(), key=str):
    print("CHANGE", key, n)
for key, n in sorted(by_source.items()):
    print("NON_PRAHA", key, n)
for sample in samples:
    print("SAMPLE", sample)
