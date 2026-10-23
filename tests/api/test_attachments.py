"""A property's files (migration 660): what may be stored, how it is stored, listed, read back (the
original, or an image's display copy) and removed. Hermetic: the tenant connection and R2 are
faked; RLS, the note key and the carriers are the live suites' job (tests/test_collection_shares_live.py,
tests/test_property_carriers_live.py, tests/test_property_split_live.py)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient
from PIL import Image  # noqa: E402

from api import attachments as at  # noqa: E402
from api import curation  # noqa: E402
from api import main as api_main  # noqa: E402
from api import tenant_pool  # noqa: E402

_WHEN = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
_PDF = b"%PDF-1.4 plan"
_OWNER = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _jpeg(width: int = 2400, height: int = 1600) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(out, "JPEG")
    return out.getvalue()


class _R2:
    def __init__(self) -> None:
        self.put: list[tuple[str, bytes, str]] = []
        self.objects: dict[str, bytes] = {}

    def upload_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.put.append((key, data, content_type))
        self.objects[key] = data

    def download_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def object_size(self, key: str) -> int | None:
        return len(self.objects[key]) if key in self.objects else None


class _Cur:
    def __init__(self, db: "_Conn") -> None:
        self.db, self.rowcount, self._rows = db, 0, []

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.db.ran.append((sql, params))
        if sql.startswith("SELECT 1 FROM property_notes"):
            self._rows = [(1,)] if self.db.note_ok else []
        elif sql.startswith("SELECT count(*) FROM property_attachments"):
            self._rows = [(self.db.count,)]
        elif sql.startswith("INSERT INTO property_attachments"):
            pid, note_id, _acc, _key, filename, mime, size, _sha = params
            self._rows = [] if self.db.duplicate else [(41, pid, note_id, filename, mime, size, _WHEN, None)]
        elif sql.startswith("SELECT storage_key"):
            self._rows = [self.db.stored] if self.db.stored else []
        elif sql.startswith("DELETE"):
            self.rowcount = self.db.deletes
        elif "FROM property_attachments" in sql:
            self._rows = self.db.files
        else:
            self._rows = self.db.notes

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class _Conn:
    def __init__(self) -> None:
        self.ran: list[tuple[str, Any]] = []
        self.note_ok = True       # the note is this property's and the caller's to change
        self.count = 0            # files already on the note (or outside notes)
        self.duplicate = False    # the account holds these bytes on the property already
        self.stored: tuple | None = None
        self.deletes = 1
        self.notes: list[tuple] = []
        self.files: list[tuple] = []

    def cursor(self) -> _Cur:
        return _Cur(self)

    @contextlib.contextmanager
    def transaction(self):
        yield

    def sql(self, prefix: str) -> list[tuple[str, Any]]:
        return [(s, p) for s, p in self.ran if s.startswith(prefix)]


@pytest.fixture()
def r2(monkeypatch):
    fake = _R2()
    monkeypatch.setattr(at.image_storage, "is_configured", lambda: True)
    monkeypatch.setattr(at, "_client", fake)
    return fake


@pytest.fixture()
def conn(monkeypatch):
    db = _Conn()
    monkeypatch.setattr(at, "resolve_active_property_id", lambda _c, pid: pid)
    monkeypatch.setattr(curation, "resolve_active_property_id", lambda _c, pid: pid)
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: db
    api_main.app.dependency_overrides[tenant_pool.require_acting_account_id] = lambda: _OWNER
    yield db
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(api_main.app)


def _upload(client, name: str, data: bytes = _PDF, mime: str = "application/pdf",
            note_id: int | None = None):
    form = {"note_id": str(note_id)} if note_id is not None else None
    return client.post("/properties/5/attachments", files={"file": (name, data, mime)}, data=form)


def test_an_upload_with_no_note_lands_under_the_property_in_the_acting_accounts_name(
        client, conn, r2):
    res = _upload(client, "Plán bytu.pdf")
    assert res.status_code == 200
    assert res.json() == {"id": 41, "property_id": 5, "note_id": None, "filename": "Plán bytu.pdf",
                          "mime_type": "application/pdf", "byte_size": len(_PDF),
                          "created_at": _WHEN.isoformat(), "owner_email": None}
    [(key, data, mime)] = r2.put
    assert key.startswith("custom-attachments/property/5/") and key.endswith(".pdf")
    assert (data, mime) == (_PDF, "application/pdf")
    [(sql, params)] = conn.sql("INSERT")
    assert params == (5, None, str(_OWNER), key, "Plán bytu.pdf", "application/pdf", len(_PDF),
                      hashlib.sha256(_PDF).hexdigest())
    assert "ON CONFLICT (account_id, property_id, sha256_hex) DO NOTHING" in sql
    [(_s, cap)] = conn.sql("SELECT count(*)")
    assert "note_id IS NULL" in _s and cap == (5,)
    assert conn.sql("SELECT 1 FROM property_notes") == []


def test_an_upload_onto_a_note_checks_the_note_is_the_propertys_and_writable(client, conn, r2):
    assert _upload(client, "plan.pdf", note_id=7).json()["note_id"] == 7
    [(sql, check)] = conn.sql("SELECT 1 FROM property_notes")
    assert check == (7, 5) and sql.endswith("FOR UPDATE"), "the note's UPDATE policy, not visibility"
    assert conn.sql("INSERT")[0][1][1] == 7


@pytest.mark.parametrize("note_id,seed", [
    (7, {"count": at.MAX_FILES_PER_NOTE}),
    (None, {"count": at.MAX_FILES_WITHOUT_NOTE}),
    (None, {"duplicate": True}),    # the account holds these bytes on the property already
])
def test_a_full_note_or_property_or_the_same_bytes_twice_is_a_409_before_storage(
        client, conn, r2, note_id, seed):
    for k, v in seed.items():
        setattr(conn, k, v)
    assert _upload(client, "plan.pdf", note_id=note_id).status_code == 409
    assert r2.put == []


@pytest.mark.parametrize("name,data,mime", [
    ("drawing.svg", b"<svg/>", "image/svg+xml"),    # active content
    ("page.html", b"<p>", "text/plain"),            # a named extension decides, not the type
    ("tool.exe", b"MZ", "application/octet-stream"),
    ("noext", b"<p>", "text/html"),
    ("IMG_0001.heic", b"\x00\x00\x00\x18ftypheic", "image/heic"),   # left the allowlist
    ("photo.jpg", b"not really a jpeg", "image/jpeg"),               # an image by name only
])
def test_an_active_unknown_or_unreadable_type_is_refused_before_storage(
        client, conn, r2, name, data, mime):
    assert _upload(client, name, data, mime).status_code == 415
    assert r2.put == []


def test_an_image_is_typed_by_its_own_bytes(client, conn, r2):
    png = io.BytesIO()
    Image.new("RGB", (4, 4)).save(png, "PNG")
    assert _upload(client, "scan.jpg", png.getvalue(), "image/jpeg").json()["mime_type"] == (
        "image/png")
    assert r2.put[0][0].endswith(".png")


def test_an_empty_or_oversized_file_is_refused(client, conn, r2, monkeypatch):
    assert _upload(client, "plan.pdf", b"").status_code == 400
    monkeypatch.setattr(at, "MAX_BYTES", 4)
    assert _upload(client, "plan.pdf", b"12345").status_code == 413
    assert r2.put == []


def test_the_list_reads_the_propertys_files_optionally_one_notes(client, conn):
    conn.files = [(41, 5, 7, "plan.pdf", "application/pdf", 10, _WHEN, "b@x.cz"),
                  (42, 5, None, "IMG.jpg", "image/jpeg", 20, _WHEN, None)]
    data = client.get("/properties/5/attachments").json()["data"]
    assert [(f["note_id"], f["owner_email"]) for f in data] == [(7, "b@x.cz"), (None, None)]
    client.get("/properties/5/attachments", params={"note_id": 7})
    assert conn.ran[-1][1] == (5, 7, 7)
    # Moje fotky reads mine as the notes do: owner_email is null iff the acting identity added it.
    assert "account_id IN (SELECT acting_account_ids()) THEN NULL" in conn.ran[-1][0]


def test_a_read_serves_the_original_typed_as_stored_never_sniffed(client, conn, r2):
    conn.stored = ("custom-attachments/property/5/a.pdf", "application/pdf", "Smlouva č. 1.pdf")
    r2.objects["custom-attachments/property/5/a.pdf"] = _PDF
    res = client.get("/properties/5/attachments/41")
    assert res.status_code == 200 and res.content == _PDF
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["cache-control"] == "private, max-age=86400, immutable"
    assert res.headers["content-disposition"] == (
        "attachment; filename=\"Smlouva . 1.pdf\"; "
        "filename*=UTF-8''Smlouva%20%C4%8D.%201.pdf")


def test_a_display_copy_is_made_once_then_served_and_the_original_is_untouched(client, conn, r2):
    key = "custom-attachments/property/5/a.jpg"
    original = _jpeg()
    conn.stored = (key, "image/jpeg", "IMG_0001.jpg")
    r2.objects[key] = original
    first = client.get("/properties/5/attachments/41", params={"w": 1080})
    assert first.status_code == 200 and first.headers["content-type"] == "image/webp"
    assert first.headers["cache-control"].startswith("private")
    with Image.open(io.BytesIO(first.content)) as copy:
        assert copy.size == (1080, 720)
    assert [k for k, _d, _m in r2.put] == ["thumb/w1080/custom-attachments/property/5/a.webp"]
    second = client.get("/properties/5/attachments/41", params={"w": 1080})
    assert second.content == first.content and len(r2.put) == 1, "made once, then read back"
    assert r2.objects[key] == original
    assert client.get("/properties/5/attachments/41").content == original


@pytest.mark.parametrize("stored,w", [
    (("custom-attachments/property/5/a.pdf", "application/pdf", "plan.pdf"), 1080),
    (("custom-attachments/property/5/a.jpg", "image/jpeg", "a.jpg"), 640),   # one size only
])
def test_no_display_copy_for_a_file_or_a_size_it_has_not(client, conn, r2, stored, w):
    conn.stored = stored
    r2.objects[stored[0]] = _PDF
    assert client.get("/properties/5/attachments/41", params={"w": w}).status_code == 404
    assert r2.put == []


def test_a_copy_that_cannot_be_made_serves_the_original(client, conn, r2):
    conn.stored = ("custom-attachments/property/5/a.jpg", "image/jpeg", "a.jpg")
    r2.objects["custom-attachments/property/5/a.jpg"] = b"corrupt"
    res = client.get("/properties/5/attachments/41", params={"w": 1080})
    assert res.status_code == 200 and res.content == b"corrupt"
    assert res.headers["content-type"] == "image/jpeg"


def test_another_accounts_file_or_note_is_a_404(client, conn, r2):
    """RLS hides what the caller may not read or change (a note: its UPDATE policy, before any
    byte reaches R2); the fake answers as it would."""
    conn.deletes = 0
    assert client.get("/properties/5/attachments/41").status_code == 404
    assert client.delete("/properties/5/attachments/41").status_code == 404
    conn.note_ok = False
    assert _upload(client, "plan.pdf", note_id=8).status_code == 404
    assert r2.put == []


def test_a_delete_removes_the_row_scoped_to_the_property_and_keeps_the_bytes(client, conn, r2):
    assert client.delete("/properties/5/attachments/41").json() == {"deleted": True}
    [(sql, params)] = conn.sql("DELETE")
    assert params == (41, 5) and "property_attachments" in sql


def test_the_notes_list_carries_each_notes_files(conn):
    conn.notes = [
        (8, 5, "druhá", False, _WHEN, None, None, None, None),
        (7, 5, "první", False, _WHEN, None, None, None, None),
    ]
    conn.files = [(41, 5, 7, "plan.pdf", "application/pdf", 10, _WHEN, None)]
    data = curation.list_notes(conn, 5)["data"]
    assert [(n["id"], [f["id"] for f in n["attachments"]]) for n in data] == [(8, []), (7, [41])]


def test_the_routes_need_a_signed_in_user(client):
    assert _upload(client, "plan.pdf").status_code == 401
    assert client.get("/properties/5/attachments/41").status_code == 401


@pytest.mark.parametrize("raw,clean", [
    ("C:\\Users\\me\\plan.pdf", "plan.pdf"),
    ("../../etc/passwd", "passwd"),
    ("a\x00b\nc.txt", "abc.txt"),
    ("", "attachment"),
    ("x" * 300 + ".pdf", "x" * 251 + ".pdf"),
])
def test_a_file_name_is_cleaned_to_a_display_name(raw, clean):
    assert at.clean_filename(raw) == clean


def test_the_allowlist_holds_nothing_active_and_no_heic():
    assert not {"text/html", "image/svg+xml", "application/javascript", "text/xml",
                "image/heic"} & set(at.ALLOWED)
    assert all(ext.startswith(".") for ext in at.BY_EXTENSION)
    assert at.BUILDING_TYPES <= set(at.ALLOWED)
