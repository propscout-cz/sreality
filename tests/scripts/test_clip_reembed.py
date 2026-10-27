"""The re-embed proof (#2032 AC2) with fakes: a frozen sample read in one READ ONLY transaction, compared vector by
vector (L2 in float64) and tag by tag, against the stored tags too, and the loaded weights' identity.

Fakes: a connection that serves the sample and refuses any write, R2 whose GETs fail a set number of times, a tagger
whose vectors and tags come from a table, and the hub cache as a real snapshot/blob tree in tmp_path.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import logging
import math
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scraper import image_storage, stop
from scraper.clip_tagger import TagResult
from scripts import clip_reembed as cr
from scripts import clip_tag_backfill as ctb
from scripts import retag_from_embeddings as rte

_STAMP = dt.datetime(2026, 10, 21, 9, 30, tzinfo=dt.UTC)


def _base(image_id: int) -> list[float]:
    """512 values on the 1e-6 grid, as _vec_str leaves them, distinct per image."""
    return [round(((image_id * (k + 3)) % 2001 - 1000) / 25_000, 6) for k in range(512)]


def _text(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


def _tags(values: list[float]) -> TagResult:
    fine = "kitchen" if values[0] >= values[1] else "bathroom"
    return TagResult(fine, fine, 0.9, 0.1)


class _Row:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return list(self._values)


class _Tagger:
    model_id = "openai/clip-vit-base-patch32"
    revision = cr.PIN

    def __init__(self, fresh: dict[int, list[float]], *, commit: str = cr.PIN) -> None:
        self.fresh = fresh
        self._model = SimpleNamespace(config=SimpleNamespace(_commit_hash=commit))
        self._processor = SimpleNamespace(image_processor=type("CLIPImageProcessor", (), {})())

    def embed(self, images: list[int], batch_size: int = 32) -> list[_Row]:
        return [_Row(self.fresh[i]) for i in images]

    def tags_from_emb(self, emb: list[Any]) -> list[TagResult]:
        return [_tags(r.tolist() if hasattr(r, "tolist") else list(r)) for r in emb]


class _Cur:
    def __init__(self, conn: "_Conn") -> None:
        self._conn = conn
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        head = sql.split()[0].upper()
        if head in ("INSERT", "UPDATE", "DELETE"):
            raise AssertionError(f"the verify wrote: {' '.join(sql.split())[:60]}")
        self._conn.statements.append((self._conn.in_txn, " ".join(sql.split())))
        self._rows = list(self._conn.rows) if sql == cr._READ_SQL else []

    def executemany(self, sql: str, params: Any) -> None:
        self.execute(sql)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _Conn:
    """The sample's rows, keyed by image id; `statements` holds (inside a transaction, the SQL)."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.statements: list[tuple[bool, str]] = []
        self.in_txn = False

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        self.in_txn = True
        try:
            yield
        finally:
            self.in_txn = False

    def cursor(self) -> _Cur:
        return _Cur(self)


class _R2:
    """GETs return the image id as bytes; `fails[id]` GETs of that image raise first. `head` answers object_size:
    the size, None for an object that does not exist ("gone"), or an error ("raises")."""

    def __init__(self, fails: dict[int, int] | None = None, head: str = "exists") -> None:
        self.fails = dict(fails or {})
        self.head = head
        self.calls: list[int] = []

    def object_size(self, key: str) -> int | None:
        if self.head == "raises":
            raise RuntimeError("R2 down")
        return None if self.head == "gone" else 9

    def download_bytes(self, key: str) -> bytes:
        image_id = int(key.rsplit("/", 1)[1].split(".")[0])
        self.calls.append(image_id)
        if self.fails.get(image_id, 0) > 0:
            self.fails[image_id] -= 1
            raise RuntimeError("R2 blip")
        return str(image_id).encode()


class _Sample:
    """The 100 frozen ids with matching stored rows; edit `stored`, `fresh` or `tags` to break one image."""

    def __init__(self) -> None:
        self.fresh = {i: _base(i) for i in cr.SAMPLE_IDS}
        self.stored = {i: list(v) for i, v in self.fresh.items()}
        self.tags = {i: _tags(v) for i, v in self.fresh.items()}
        self.r2 = _R2()
        self.commit = cr.PIN

    def rows(self) -> list[tuple[Any, ...]]:
        out = []
        for i in cr.SAMPLE_IDS:
            listing = i // 1000
            t = self.tags[i]
            out.append((i, listing, f"img/{listing}/{i}.jpg", _STAMP, cr.PIN, _text(self.stored[i]),
                        t.fine_tag, t.logical_tag, t.confidence, t.render_score))
        return out

    def verify(self, *argv: str) -> tuple[int, _Conn, _Tagger]:
        conn = _Conn(self.rows())
        tagger = _Tagger(self.fresh, commit=self.commit)
        args = ctb._parser().parse_args(["--verify-reembed", *argv])
        code = cr.verify(conn, args, r2_factory=lambda: self.r2, tagger_factory=lambda threads: tagger)
        return code, conn, tagger


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """R2 configured; the decoder returns the image id; the hub cache is a snapshot of the pin with the blob."""
    for name in image_storage.R2_ENV_VARS:
        monkeypatch.setenv(name, "x")
    real = ctb._decode
    monkeypatch.setattr(ctb, "_decode", lambda data: int(data) if data.isdigit() else real(data))
    monkeypatch.setattr(rte, "_as_tensor", lambda vectors: vectors)
    monkeypatch.setattr(cr, "_torch_facts", lambda: {"cuda": None, "isa": "AVX2"})
    monkeypatch.setattr(cr, "_importable", lambda name: False)
    repo = tmp_path / "models--openai--clip-vit-base-patch32"
    snap = repo / "snapshots" / cr.PIN
    (repo / "blobs").mkdir(parents=True)
    snap.mkdir(parents=True)
    (repo / "blobs" / cr.WEIGHTS_OID).write_bytes(b"weights")
    (repo / "blobs" / "c0nf1g").write_bytes(b"{}")
    (snap / "pytorch_model.bin").symlink_to(Path("..") / ".." / "blobs" / cr.WEIGHTS_OID)
    (snap / "config.json").symlink_to(Path("..") / ".." / "blobs" / "c0nf1g")
    monkeypatch.setattr(cr, "_cache_path", lambda model, filename, revision: str(snap / filename))
    yield snap


def _summary(caplog: pytest.LogCaptureFixture) -> str:
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("CLIP_VERIFY ")]
    return line


def _field(line: str, name: str) -> str:
    match = re.search(rf"(?:^| ){name}=(\S+)", line)
    assert match, (name, line)
    return match.group(1)


def _failures(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("CLIP_VERIFY_FAIL ")]


def test_a_matching_sample_passes(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = _Sample().verify()
    line = _summary(caplog)
    assert code == 0, line
    assert _field(line, "pass") == "true" and _field(line, "why") == "ok"
    assert _field(line, "n") == "100" and _field(line, "ids_md5") == cr.IDS_MD5
    assert _field(line, "exact_rows") == "100" and _field(line, "tags_equal") == "100"
    assert _field(line, "stored_tags_equal") == "100" and _field(line, "missing") == "0"
    assert _field(line, "commit") == cr.PIN[:12] and _field(line, "weights") == cr.WEIGHTS_OID[:12]
    assert _field(line, "processor") == "CLIPImageProcessor"


def test_the_verify_writes_nothing() -> None:
    code, conn, _tagger = _Sample().verify()
    assert code == 0
    assert conn.statements, "the sample was read"
    assert all(in_txn for in_txn, _sql in conn.statements), "every read is inside the one transaction"
    assert conn.statements[0][1] == "SET TRANSACTION READ ONLY"
    assert not [s for _t, s in conn.statements if s.split()[0].upper() in ("INSERT", "UPDATE", "DELETE")]


def test_the_sample_is_frozen() -> None:
    ids = cr.SAMPLE_IDS
    assert len(ids) == 100 and list(ids) == sorted(set(ids))
    # Postgres's md5(string_agg(id::text, ',' order by id)) over the draw in the module's comment.
    assert hashlib.md5(",".join(map(str, ids)).encode()).hexdigest() == cr.IDS_MD5
    assert cr.IDS_MD5 == "a037de1cf09ec23fc5baa610de55508a"


def test_the_frozen_taxonomy_digest_is_todays_file() -> None:
    """True at the draw: the sample was tagged under this taxonomy. A later taxonomy edit turns P2b `skipped` by
    design and this goes red; then delete this test, never re-pin the digest (the sample's tags are the old file's)."""
    assert cr._taxonomy_digest(cr.load_taxonomy()) == cr.TAXONOMY_SHA256


def _off(values: list[float], n: int, by: float) -> list[float]:
    return [round(v + by, 6) if k < n else v for k, v in enumerate(values)]


@pytest.mark.parametrize(("case", "n", "by", "passes"), [
    ("1e-6 off in 13 values: the measured floor", 13, 1e-6, True),
    ("one value 2e-5 off", 1, 2e-5, False),
    ("1e-6 off in 128 values: L2 1.13e-5", 128, 1e-6, False),
])
def test_the_vector_rule_passes_the_measured_floor_and_fails_past_it(
        caplog: pytest.LogCaptureFixture, case: str, n: int, by: float, passes: bool) -> None:
    sample = _Sample()
    image = cr.SAMPLE_IDS[17]
    sample.stored[image] = _off(sample.stored[image], n, by)
    l2, _max_abs, _cos = cr._metrics(sample.stored[image], sample.fresh[image])
    assert (l2 <= cr.L2_MAX) is passes, (case, l2)
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = sample.verify()
    line = _summary(caplog)
    assert code == (0 if passes else 1), (case, line)
    assert _field(line, "why") == ("ok" if passes else "vector"), case
    assert math.isclose(float(_field(line, "max_l2")), l2, rel_tol=1e-2)  # printed to 3 digits
    assert _field(line, "exact_rows") == "99"
    if not passes:
        assert [f.split()[1:3] for f in _failures(caplog)] == [["vector", f"image={image}"]]


def test_min_cos64_is_the_normalized_cosine_not_a_raw_dot() -> None:
    raw = _base(42)
    norm = math.sqrt(sum(v * v for v in raw))
    stored = [v * (1 + 1e-6) / norm for v in raw]
    l2, max_abs, cos = cr._metrics(stored, list(stored))
    assert l2 == 0.0 and max_abs == 0.0
    assert abs(cos - 1.0) < 1e-12, cos


def test_a_download_shortfall_is_its_own_result(caplog: pytest.LogCaptureFixture) -> None:
    sample = _Sample()
    twice, always = cr.SAMPLE_IDS[3], cr.SAMPLE_IDS[61]
    sample.r2 = _R2({twice: 2})
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = sample.verify()
    assert code == 0 and sample.r2.calls.count(twice) == 3, "two failed rounds, then the third GET"
    caplog.clear()
    sample.r2 = _R2({twice: 2, always: 3})
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = sample.verify()
    line = _summary(caplog)
    assert code == 1
    assert (_field(line, "pass"), _field(line, "why"), _field(line, "missing")) == ("false", "download", "1")
    assert sample.r2.calls.count(always) == cr.ROUNDS
    assert _field(line, "exact_rows") == "99", "the 99 that downloaded are compared, and match"


@pytest.mark.parametrize(("head", "why"), [("gone", "sample"), ("raises", "download")])
def test_a_sample_object_gone_from_r2_fails_the_sample_not_the_download(caplog: pytest.LogCaptureFixture, head: str,
                                                                       why: str) -> None:
    """A deleted object never downloads, so re-requesting cannot help: it is a broken frozen row (P0). Only R2 saying
    so counts; a HEAD that fails too stays an inconclusive shortfall."""
    sample = _Sample()
    lost = cr.SAMPLE_IDS[61]
    sample.r2 = _R2({lost: cr.ROUNDS}, head=head)
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = sample.verify()
    line = _summary(caplog)
    assert code == 1 and (_field(line, "why"), _field(line, "missing")) == (why, "1")
    assert [f.split()[1:3] for f in _failures(caplog)] == [[why, f"image={lost}"]]


def test_a_value_failure_outranks_a_download_shortfall(caplog: pytest.LogCaptureFixture) -> None:
    sample = _Sample()
    sample.r2 = _R2({cr.SAMPLE_IDS[0]: 3})
    sample.stored[cr.SAMPLE_IDS[1]] = _off(sample.stored[cr.SAMPLE_IDS[1]], 1, 2e-5)
    with caplog.at_level(logging.INFO):
        assert sample.verify()[0] == 1
    assert _field(_summary(caplog), "why") == "vector"


def test_a_tag_flip_fails_the_check(caplog: pytest.LogCaptureFixture) -> None:
    """A near tie: the vectors pass P1 (L2 1.4e-6), but the winning anchor differs between them."""
    sample = _Sample()
    image = cr.SAMPLE_IDS[40]
    fresh = sample.fresh[image]
    fresh[0], fresh[1] = 0.100001, 0.100000
    sample.stored[image] = [0.100000, 0.100001, *fresh[2:]]
    sample.tags[image] = _tags(fresh)
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = sample.verify()
    line = _summary(caplog)
    assert code == 1 and _field(line, "why") == "tags" and _field(line, "tags_equal") == "99"
    (failure,) = _failures(caplog)
    assert failure.split()[1:3] == ["tags", f"image={image}"]
    assert "fresh=kitchen:0.9" in failure and "stored=bathroom:0.9" in failure


@pytest.mark.parametrize("what", ["fine_tag", "confidence"])
def test_a_text_side_difference_fails_against_the_stored_tags(caplog: pytest.LogCaptureFixture, what: str) -> None:
    sample = _Sample()
    image = cr.SAMPLE_IDS[88]
    t = sample.tags[image]
    sample.tags[image] = (TagResult("bedroom", t.logical_tag, t.confidence, t.render_score) if what == "fine_tag"
                          else TagResult(t.fine_tag, t.logical_tag, round(t.confidence + 2e-4, 4), t.render_score))
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = sample.verify()
    line = _summary(caplog)
    assert code == 1 and _field(line, "why") == "stored_tags" and _field(line, "stored_tags_equal") == "99"
    assert _field(line, "tags_equal") == "100", "P2a compares the runner with itself and cannot see it"
    assert [f.split()[1:3] for f in _failures(caplog)] == [["stored_tags", f"image={image}"]]


def test_stored_tags_within_their_rounding_pass(caplog: pytest.LogCaptureFixture) -> None:
    sample = _Sample()
    t = sample.tags[cr.SAMPLE_IDS[5]]
    sample.tags[cr.SAMPLE_IDS[5]] = TagResult(t.fine_tag, t.logical_tag, round(t.confidence + 1e-4, 4), 0.1001)
    with caplog.at_level(logging.INFO):
        assert sample.verify()[0] == 0, _summary(caplog)


def test_a_changed_taxonomy_skips_the_stored_tag_rule(caplog: pytest.LogCaptureFixture,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr, "TAXONOMY_SHA256", "0" * 64)
    sample = _Sample()
    t = sample.tags[cr.SAMPLE_IDS[88]]
    sample.tags[cr.SAMPLE_IDS[88]] = TagResult("bedroom", t.logical_tag, t.confidence, t.render_score)
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = sample.verify()
    line = _summary(caplog)
    assert code == 0 and _field(line, "stored_tags_equal") == "skipped", line


def test_the_loaded_revision_is_the_models_own_commit_hash(caplog: pytest.LogCaptureFixture) -> None:
    sample = _Sample()
    sample.commit = "f" * 40
    with caplog.at_level(logging.INFO):
        code, _conn, tagger = sample.verify()
    line = _summary(caplog)
    assert tagger.revision == cr.PIN, "the file's string still equals the pin"
    assert code == 1 and _field(line, "why") == "identity" and _field(line, "commit") == "f" * 12


def test_a_wrong_weights_blob_fails_the_identity(caplog: pytest.LogCaptureFixture, _env: Path) -> None:
    other = _env.parent.parent / "blobs" / ("b" * 64)
    other.write_bytes(b"other weights")
    (_env / "pytorch_model.bin").unlink()
    (_env / "pytorch_model.bin").symlink_to(Path("..") / ".." / "blobs" / other.name)
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = _Sample().verify()
    line = _summary(caplog)
    assert code == 1 and _field(line, "why") == "identity" and _field(line, "weights") == "b" * 12


def test_a_config_outside_the_pins_snapshot_fails_the_identity(caplog: pytest.LogCaptureFixture,
                                                               monkeypatch: pytest.MonkeyPatch,
                                                               _env: Path) -> None:
    elsewhere = _env.parent / ("e" * 40)
    elsewhere.mkdir()
    (elsewhere / "config.json").write_text("{}")
    real = cr._cache_path
    monkeypatch.setattr(cr, "_cache_path", lambda model, filename, revision: (
        str(elsewhere / filename) if filename == "config.json" else real(model, filename, revision)))
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = _Sample().verify()
    assert code == 1 and _field(_summary(caplog), "why") == "identity"
    assert any(f.split()[1] == "identity" and "config.json" in f for f in _failures(caplog))


@pytest.mark.parametrize(("missing", "weights"), [("config.json", cr.WEIGHTS_OID[:12]), ("pytorch_model.bin", "none")])
def test_a_file_not_cached_at_the_pin_fails_the_identity(caplog: pytest.LogCaptureFixture,
                                                         monkeypatch: pytest.MonkeyPatch, missing: str,
                                                         weights: str) -> None:
    """The lookup's reachable miss (try_to_load_from_cache returns no path): one CLIP_VERIFY line, never a crash."""
    real = cr._cache_path
    monkeypatch.setattr(cr, "_cache_path", lambda model, filename, revision: (
        None if filename == missing else real(model, filename, revision)))
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = _Sample().verify()
    line = _summary(caplog)
    assert code == 1 and _field(line, "why") == "identity" and _field(line, "weights") == weights


@pytest.mark.parametrize("fact", ["cuda", "torchvision", "processor"])
def test_the_runtime_stack_is_the_one_production_tagged_with(caplog: pytest.LogCaptureFixture,
                                                             monkeypatch: pytest.MonkeyPatch, fact: str) -> None:
    if fact == "cuda":
        monkeypatch.setattr(cr, "_torch_facts", lambda: {"cuda": "12.4", "isa": "AVX2"})
    elif fact == "torchvision":
        monkeypatch.setattr(cr, "_importable", lambda name: name == "torchvision")
    sample = _Sample()
    with caplog.at_level(logging.INFO):
        conn = _Conn(sample.rows())
        tagger = _Tagger(sample.fresh)
        if fact == "processor":
            tagger._processor = SimpleNamespace(image_processor=type("CLIPImageProcessorFast", (), {})())
        args = ctb._parser().parse_args(["--verify-reembed"])
        code = cr.verify(conn, args, r2_factory=lambda: sample.r2, tagger_factory=lambda threads: tagger)
    assert code == 1 and _field(_summary(caplog), "why") == "identity"


def test_a_sample_row_out_of_its_window_fails_the_sample(caplog: pytest.LogCaptureFixture) -> None:
    sample = _Sample()
    rows = sample.rows()
    rows[9] = (*rows[9][:3], dt.datetime(2026, 10, 27, 7, 0, tzinfo=dt.UTC), *rows[9][4:])
    conn = _Conn(rows[:50] + rows[51:])
    args = ctb._parser().parse_args(["--verify-reembed"])
    with caplog.at_level(logging.INFO):
        code = cr.verify(conn, args, r2_factory=lambda: sample.r2,
                         tagger_factory=lambda threads: _Tagger(sample.fresh))
    line = _summary(caplog)
    assert code == 1 and _field(line, "why") == "sample" and _field(line, "n") == "99"
    assert _field(line, "ids_md5") != cr.IDS_MD5
    assert {f.split()[2].split("=")[0] if "ids_md5" in f else f.split()[2]
            for f in _failures(caplog) if f.split()[1] == "sample"} == {
        "ids_md5", f"image={cr.SAMPLE_IDS[9]}", f"image={cr.SAMPLE_IDS[50]}"}


def test_a_sample_row_without_its_vector_fails_the_sample_and_is_not_compared(
        caplog: pytest.LogCaptureFixture) -> None:
    sample = _Sample()
    rows = sample.rows()
    rows[30] = (*rows[30][:5], None, *rows[30][6:])
    args = ctb._parser().parse_args(["--verify-reembed"])
    with caplog.at_level(logging.INFO):
        code = cr.verify(_Conn(rows), args, r2_factory=lambda: sample.r2,
                         tagger_factory=lambda threads: _Tagger(sample.fresh))
    line = _summary(caplog)
    assert code == 1 and _field(line, "why") == "sample" and _field(line, "exact_rows") == "99"
    assert cr.SAMPLE_IDS[30] not in sample.r2.calls, "nothing to compare it with: not downloaded"


def test_the_ids_read_must_hash_to_the_frozen_fingerprint(caplog: pytest.LogCaptureFixture,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr, "IDS_MD5", "0" * 32)
    with caplog.at_level(logging.INFO):
        code, _conn, _tagger = _Sample().verify()
    assert code == 1 and _field(_summary(caplog), "why") == "sample"
    assert any(f.split()[1] == "sample" and "ids_md5=" in f for f in _failures(caplog))


def test_missing_r2_fails_the_check(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in image_storage.R2_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    sample = _Sample()
    with caplog.at_level(logging.INFO):
        code, conn, _tagger = sample.verify()
    line = _summary(caplog)
    assert code == 1 and _field(line, "pass") == "false" and _field(line, "why") == "r2"
    assert conn.statements == [] and sample.r2.calls == []


def test_the_verify_line_fits_the_log_tail(caplog: pytest.LogCaptureFixture) -> None:
    sample = _Sample()
    for image in cr.SAMPLE_IDS:
        sample.stored[image] = _off(sample.stored[image], 1, 2e-5)
    with caplog.at_level(logging.INFO):
        assert sample.verify()[0] == 1
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("CLIP_VERIFY")]
    assert len(lines) <= 1 + cr.MAX_FAILURE_LINES, len(lines)
    assert lines[-1] == _summary(caplog), "the summary is the last line"
    tail = "\n".join(lines)
    assert re.search(r"CLIP_VERIFY [^\n]*", tail).group(0) == _summary(caplog), "the gate's regex finds the summary"


def test_the_flag_dispatches_to_the_verify_and_nothing_else_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg

    seen: list[Any] = []
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    monkeypatch.setattr(sys, "argv", ["clip_tag_backfill", "--verify-reembed"])
    monkeypatch.setattr(stop, "install", lambda: None)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: contextlib.nullcontext("conn"))
    monkeypatch.setattr(ctb, "run", lambda *a, **k: pytest.fail("the tag job ran"))
    monkeypatch.setattr(cr, "verify", lambda conn, args, **kw: seen.append((conn, args.verify_reembed)) or 1)
    assert ctb.main() == 1
    assert seen == [("conn", True)]
