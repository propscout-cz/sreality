"""One singleton-lease module: lease compare-and-set text lives in scraper/job_lease.py and in no new file.
Three hand-written leases (LEGACY's S1-S3) each re-solve take, renew, fence and release their own way; this census
fails CI on a fourth. It matches write shapes: SQL that sets or compares a holder, the bare `expires_at` /
`heartbeat_at` staleness tests, a session advisory lock in Python. A work-queue claim (`FOR UPDATE SKIP LOCKED`), a
transaction advisory lock, and a reader, view, COMMENT or log line naming job_lease's columns do not match.
Blind spot: a lease spelled otherwise passes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest

from tests.test_derived_artifacts_stamping import _drop_sql_comment_lines
from tests.test_measure_registry_census import strip_python_comments

REPO = Path(__file__).resolve().parents[1]
SOURCE_TREES = ("scraper", "toolkit", "api", "location_data", "autodedup", "scripts")
THE_MODULE = "scraper/job_lease.py"

_LEASE_TEXT = (
    re.compile(r"\b(set|where|and|or)\s+(\w+\.)?(lease_)?holder\s*=(?!=)", re.IGNORECASE),
    re.compile(r"\b(lease_)?holder\s*=\s*(%\(|\$\d)", re.IGNORECASE),
    re.compile(r"\bexpires_at\s*<=?\s*(now|clock_timestamp)\s*\(\s*\)", re.IGNORECASE),
    re.compile(r"\bheartbeat_at\s*<", re.IGNORECASE),
)
# Python only: there a session lock strands on the transaction pooler; a migration's top-level one waits out a rebuild.
_SESSION_ADVISORY_LOCK = re.compile(r"\bpg_(try_)?advisory_(un)?lock(_shared|_all)?\s*\(", re.IGNORECASE)


class Ledgered(NamedTuple):
    reason: str
    owner: str


LEGACY: dict[str, Ledgered] = {
    "scripts/recompute_property_stats.py": Ledgered(
        "S1 property_maintenance_lease (mig 279): the cutover row beside the job_lease row (rule 20)",
        "the follow-up to #2107 drops it once gate #2110 passes"),
    "scripts/resolve_brokers.py": Ledgered(
        "S2 broker_resolution_lock (mig 192): the broker lane and the daily full resolve",
        "#2027 runs the sweep and keeps this lease; moving it onto job_lease is an operator ruling"),
    "autodedup/incremental_sql.py": Ledgered(
        "S3 autodedup.rt_lease (mig 539): the SQL behind autodedup/rt_lease.py (lane pass, rt_seed, live apply)",
        "the merge coordinator (it owns autodedup); it moves only on its and the operator's go"),
}


def _source_files(root: Path) -> list[Path]:
    files = [p for tree in SOURCE_TREES if (root / tree).is_dir() for p in sorted((root / tree).rglob("*.py"))]
    if (root / "migrations").is_dir():
        files += sorted((root / "migrations").glob("*.sql"))
    return files


def lease_hits(root: Path) -> dict[str, list[str]]:
    """{repo-relative path: ['<line>: <text>', ...]} for every line of lease text, comments stripped."""
    hits: dict[str, list[str]] = {}
    for path in _source_files(root):
        raw = path.read_text(encoding="utf-8")
        if path.suffix == ".sql":
            text, patterns = _drop_sql_comment_lines(raw), _LEASE_TEXT
        else:
            text, patterns = strip_python_comments(raw), (*_LEASE_TEXT, _SESSION_ADVISORY_LOCK)
        found = [f"{n}: {line.strip()}" for n, line in enumerate(text.splitlines(), 1)
                 if any(p.search(line) for p in patterns)]
        if found:
            hits[path.relative_to(root).as_posix()] = found
    return hits


def violations(hits: dict[str, list[str]], legacy: dict[str, Ledgered]) -> dict[str, list[str]]:
    return {path: lines for path, lines in hits.items() if path != THE_MODULE and path not in legacy}


def test_no_singleton_lease_outside_job_lease():
    """RED by: a new lease CAS (or a session advisory lock in Python) in any file but job_lease.py."""
    found = violations(lease_hits(REPO), LEGACY)
    assert not found, (
        "singleton-lease text outside scraper/job_lease.py. A one-holder-at-a-time lease goes through "
        "job_lease (`held` for a lane, `claim`/`renew`/`release` for a scheduled job); a work queue claims "
        "rows with FOR UPDATE SKIP LOCKED and a one-transaction guard takes pg_advisory_xact_lock, and neither "
        "matches here. LEGACY holds the three older copies and only shrinks; a match that is no lease narrows "
        "_LEASE_TEXT instead: "
        f"{found}"
    )


def test_every_legacy_entry_still_holds_lease_text_and_names_an_owner():
    """RED by: a ledgered lease moved onto job_lease (or deleted) while its LEGACY entry stays."""
    hits = lease_hits(REPO)
    assert THE_MODULE not in LEGACY
    for path, entry in LEGACY.items():
        assert entry.reason.strip() and entry.owner.strip(), path
        assert path in hits, f"{path} holds no lease text any more: delete its LEGACY entry"


def test_census_passes_job_lease_and_fails_a_copy_of_it(tmp_path: Path):
    """RED by: patterns that stop matching job_lease's own CAS, or an exemption wider than its one path."""
    source = (REPO / THE_MODULE).read_text(encoding="utf-8")
    for rel in (THE_MODULE, "scraper/my_lease.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(source, encoding="utf-8")
    hits = lease_hits(tmp_path)
    assert len(hits[THE_MODULE]) >= 5
    assert list(violations(hits, LEGACY)) == ["scraper/my_lease.py"]


@pytest.mark.parametrize("rel, text", [
    ("scraper/set_holder.py", 'SQL = "UPDATE x_lease SET holder = NULL"\n'),
    ("api/set_lease_holder.py", 'SQL = "UPDATE scheduled_jobs SET lease_holder = NULL"\n'),
    ("toolkit/and_alias.py", 'SQL = "UPDATE x_lease SET owner = 1 WHERE id = 1 AND j.holder = :h"\n'),
    ("toolkit/or_holder.py", 'SQL = "UPDATE x_lease SET owner = 1 WHERE id = 1 OR holder = :h"\n'),
    ("scraper/named_bind.py", 'SQL = "UPDATE x_lease SET owner = 1, holder = %(h)s"\n'),
    ("scraper/dollar_bind.py", 'SQL = "UPDATE x_lease SET owner = 1, lease_holder = $1"\n'),
    ("toolkit/now.py", 'SQL = "UPDATE x_lease SET owner = 1 WHERE expires_at < now()"\n'),
    ("scripts/heartbeat.py", 'SQL = "UPDATE x_lock SET owner = 1 WHERE heartbeat_at < %(stale)s"\n'),
    ("location_data/try_lock.py", 'SQL = "SELECT pg_try_advisory_lock(hashtext(\'x\'))"\n'),
    ("autodedup/lock.py", 'SQL = "select pg_advisory_lock(42)"\n'),
    ("migrations/998_clock.sql", "update x_lease set owner = p_owner where expires_at < clock_timestamp();\n"),
    ("migrations/999_where.sql", "update x_lease set owner = null where lease_holder = p_holder;\n"),
])
def test_a_synthetic_lease_fails_the_census(tmp_path: Path, rel: str, text: str):
    """RED by: a pattern arm that no longer bites; each case matches through one arm alone."""
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(text, encoding="utf-8")
    hits = lease_hits(tmp_path)
    assert rel in violations(hits, LEGACY)
    assert rel not in violations(hits, {rel: Ledgered("reason", "owner")})


def test_queue_claims_xact_locks_readers_and_logs_are_not_leases(tmp_path: Path):
    """RED by: a pattern widened onto work-queue claims, transaction advisory locks, readers, logs or prose."""
    (tmp_path / "scraper").mkdir()
    (tmp_path / "scraper" / "queue.py").write_text(
        "# a lease would say holder = %(h)s and expires_at < now(); never pg_advisory_lock(\n"
        'CLAIM = "SELECT id FROM q WHERE claimed_at IS NULL ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 10"\n'
        'GUARD = "SELECT pg_try_advisory_xact_lock(hashtext(\'x\'))"\n'
        'SHARED = "SELECT pg_advisory_xact_lock_shared(hashtext(\'x\'))"\n'
        'CACHE = "SELECT body FROM parsed_url_cache WHERE expires_at > now()"\n'
        'STALE = "SELECT job_name FROM scheduled_jobs WHERE lease_holder IS NOT NULL AND lease_expires_at < now()"\n'
        'LOG.info("JOB start job=%s holder=%s", lease.job_name, lease.holder)\n'
        'MINE = lease is not None and holder == "x"\n',
        encoding="utf-8")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "999_rebuild.sql").write_text(
        "-- the old lease said holder = %(h)s where expires_at < now()\n"
        "select pg_advisory_lock(hashtext('rebuild_browse_list'));\n"
        "perform pg_advisory_xact_lock(hashtext('rebuild_browse_list'));\n"
        "create table x_jobs (lease_holder text, lease_expires_at timestamptz);\n"
        "comment on table x_jobs is '(lease_holder, lease_expires_at) claimed by scraper/job_lease.py';\n"
        "create view x_jobs_v as select job_name, lease_holder, lease_expires_at from x_jobs;\n",
        encoding="utf-8")
    assert lease_hits(tmp_path) == {}
