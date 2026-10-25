"""One attachment store: the files a user adds to a property (migration 660), and a building run's.

A property's files run on the caller's tenant connection, so RLS scopes every read and write; a row's
account is the acting account the route resolved, its property the merge survivor, its note optional
(the note must be the property's and the caller's to change). A building run's files keep their own
table and service-role routes, under the same upload rules. The bytes go to R2 under the private
`custom-attachments/` prefix and come back only through the API: the bucket is private and sends no
CORS header. An original is stored as uploaded (EXIF intact); an image's display copy is made on its
first request under `thumb/w1080/` and served the same way, never presigned. A delete removes the row
only: R2 objects are never deleted (a split's note copy once shared them; an orphan sweep is owed).
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import threading
import unicodedata
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Collection, NamedTuple
from urllib.parse import quote

from fastapi import HTTPException, Response, UploadFile

from api.shares import party_label
from scraper import image_storage
from toolkit.property_identity import resolve_active_property_id

if TYPE_CHECKING:
    import psycopg

LOG = logging.getLogger(__name__)

# MIME type -> the extension its R2 key carries. Nothing active (HTML, SVG, script): the SPA turns
# these bytes into same-origin blob URLs. No HEIC: no browser draws it and the phone hands over
# JPEG when the picker asks for it first. Mirrored for the file picker in
# frontend/src/lib/attachments.ts (tests/test_attachment_rules_parity.py).
ALLOWED: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/zip": ".zip",
}
BY_EXTENSION: dict[str, str] = {ext: mime for mime, ext in ALLOWED.items()} | {
    ".jpeg": "image/jpeg",
}
# What an image's header says it is (Pillow's format name); only these are drawn and copied.
_IMAGE_FORMATS = {"JPEG": "image/jpeg", "MPO": "image/jpeg", "PNG": "image/png",
                  "WEBP": "image/webp", "GIF": "image/gif"}
# The building flow's vision model reads these.
BUILDING_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_NOTE = 20  # a note's, and a building run's
# A property's files with no note are capped too, so no page can fill the bucket.
MAX_FILES_WITHOUT_NOTE = 100
# Making a display copy decodes a whole photo; the API is one process.
_COPY_SLOTS = threading.BoundedSemaphore(4)

# owner_email: null iff the acting identity added it (curation's one share-aware field); another
# author's file is readable only on a note they marked shared (migration 660).
_PROJECTION = (
    "id, property_id, note_id, filename, mime_type, byte_size, created_at, "
    "CASE WHEN account_id IN (SELECT acting_account_ids()) THEN NULL "
    f"ELSE {party_label('account_id')} END AS owner_email"
)
_BUILDING_PROJECTION = (
    "id, building_run_id, storage_key, filename, mime_type, byte_size, width_px, height_px, "
    "sha256_hex, uploaded_by, created_at"
)

_client: image_storage.R2Client | None = None


class Upload(NamedTuple):
    data: bytes
    filename: str
    mime: str
    sha256: str
    size: tuple[int, int] | None  # an image's (width, height)


def storage() -> image_storage.R2Client:
    """The R2 client, or a 503: a silent no-op would let a user believe a file was kept."""
    global _client
    if not image_storage.is_configured():
        raise HTTPException(503, "attachment storage is not configured")
    if _client is None:
        _client = image_storage.R2Client.from_env()
    return _client


def clean_filename(raw: str | None) -> str:
    """The name as shown and offered on download: no path, no control characters, ≤255 chars."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(
        ch for ch in unicodedata.normalize("NFC", name) if unicodedata.category(ch)[0] != "C"
    ).strip()
    if len(name) > 255:
        stem, ext = os.path.splitext(name)
        name = stem[: 255 - len(ext)] + ext if len(ext) < 255 else name[:255]
    return name or "attachment"


def resolve_mime(filename: str, declared: str | None) -> str | None:
    """A named extension decides, allowed or not; only a file without one falls back to the type
    the browser declared (which is itself guessed from the name, and often empty)."""
    ext = os.path.splitext(filename)[1].lower()
    if ext:
        return BY_EXTENSION.get(ext)
    mime = (declared or "").split(";")[0].strip().lower()
    return mime if mime in ALLOWED else None


def sniff_image(data: bytes) -> tuple[str, int, int] | None:
    """(MIME type, width, height) when the bytes are an image we draw, read from its header only."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            mime = _IMAGE_FORMATS.get(image.format or "")
            return (mime, *image.size) if mime else None
    except Exception:  # noqa: BLE001 - bytes Pillow cannot read are no image
        return None


def read_upload(file: UploadFile, allowed: Collection[str] = ALLOWED) -> Upload:
    """The upload's bytes and what they are: an image by its own header, anything else by its name."""
    data = file.file.read(MAX_BYTES + 1)
    if not data:
        raise HTTPException(400, "the file is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"the file is over {MAX_BYTES // (1024 * 1024)} MB")
    filename = clean_filename(file.filename)
    image = sniff_image(data)
    mime = image[0] if image else resolve_mime(filename, file.content_type)
    if mime is None or mime not in allowed or (image is None and mime.startswith("image/")):
        raise HTTPException(415, f"this file type cannot be attached: {filename}")
    return Upload(data, filename, mime, hashlib.sha256(data).hexdigest(),
                  (image[1], image[2]) if image else None)


def file_response(data: bytes, mime: str, filename: str) -> Response:
    """The bytes typed as stored (an allowlisted, inactive type) and never sniffed. A row never
    changes its bytes (each upload gets a fresh key), so the caller's browser may keep them;
    `private`, never a shared cache: these are a user's files behind a JWT."""
    fallback = filename.encode("ascii", "ignore").decode().replace('"', "").replace("\\", "")
    return Response(content=data, media_type=mime, headers={
        "Cache-Control": "private, max-age=86400, immutable",
        "Content-Disposition": (f"attachment; filename=\"{fallback or 'attachment'}\"; "
                                f"filename*=UTF-8''{quote(filename)}"),
        "X-Content-Type-Options": "nosniff",
    })


# --- a property's files ------------------------------------------------------------------


def add_attachment(
    conn: "psycopg.Connection", property_id: int, file: UploadFile, *,
    account_id: str, note_id: int | None = None,
) -> dict[str, Any]:
    upload = read_upload(file)
    client = storage()
    with conn.transaction(), conn.cursor() as cur:
        pid = resolve_active_property_id(conn, property_id)
        if pid is None:
            raise HTTPException(404, "property not found")
        if note_id is not None:
            # FOR UPDATE applies the note's UPDATE policy, so a note the caller can read but not
            # change matches nothing; the lock serialises uploads to the note, so its cap holds.
            cur.execute("SELECT 1 FROM property_notes WHERE id = %s AND property_id = %s FOR UPDATE",
                        (note_id, pid))
            if cur.fetchone() is None:
                raise HTTPException(404, "note not found")
            cur.execute("SELECT count(*) FROM property_attachments WHERE note_id = %s", (note_id,))
            cap, why = MAX_FILES_PER_NOTE, "a note holds"
        else:
            cur.execute("SELECT count(*) FROM property_attachments "
                        "WHERE property_id = %s AND note_id IS NULL", (pid,))
            cap, why = MAX_FILES_WITHOUT_NOTE, "a property holds, outside notes,"
        if cur.fetchone()[0] >= cap:
            raise HTTPException(409, f"{why} at most {cap} files")
        # The row first: the same bytes twice is a 409 before any byte reaches R2, and a failed
        # upload rolls the row back.
        key = f"custom-attachments/property/{pid}/{uuid.uuid4().hex}{ALLOWED[upload.mime]}"
        cur.execute(
            "INSERT INTO property_attachments (property_id, note_id, account_id, storage_key, "
            "  filename, mime_type, byte_size, sha256_hex) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (account_id, property_id, sha256_hex) DO NOTHING "
            f"RETURNING {_PROJECTION}",
            (pid, note_id, account_id, key, upload.filename, upload.mime, len(upload.data),
             upload.sha256),
        )
        created = cur.fetchone()
        if created is None:
            raise HTTPException(409, f"{upload.filename} is already among this property's files")
        client.upload_bytes(key, upload.data, upload.mime)
    return _to_attachment(created)


def list_attachments(
    conn: "psycopg.Connection", property_id: int, note_id: int | None = None,
) -> dict[str, Any]:
    """The property's files the caller may read, oldest first; a merged-away id reads its
    survivor's."""
    pid = resolve_active_property_id(conn, property_id) or property_id
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_PROJECTION} FROM property_attachments "
            "WHERE property_id = %s AND (%s::bigint IS NULL OR note_id = %s) "
            "ORDER BY created_at, id",
            (pid, note_id, note_id),
        )
        return {"data": [_to_attachment(r) for r in cur.fetchall()]}


def attachments_by_note(
    conn: "psycopg.Connection", note_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in note_ids}
    if not note_ids:
        return out
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_PROJECTION} FROM property_attachments "
            "WHERE note_id = ANY(%s) ORDER BY created_at, id",
            (note_ids,),
        )
        for row in cur.fetchall():
            out[int(row[2])].append(_to_attachment(row))
    return out


def read_attachment(
    conn: "psycopg.Connection", property_id: int, attachment_id: int, width: int | None = None,
) -> tuple[bytes, str, str]:
    """(bytes, MIME type, file name): the original, or with `width` an image's display copy."""
    if width is not None and width not in image_storage.COPY_WIDTHS:
        raise HTTPException(404, "no such size")
    pid = resolve_active_property_id(conn, property_id) or property_id
    with conn.cursor() as cur:
        cur.execute("SELECT storage_key, mime_type, filename FROM property_attachments "
                    "WHERE id = %s AND property_id = %s", (attachment_id, pid))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "attachment not found")
    key, mime, filename = row
    if width is None:
        return storage().download_bytes(key), mime, filename
    if mime not in _IMAGE_FORMATS.values():
        raise HTTPException(404, "this file has no display copy")
    copy = display_copy(key, width)
    if copy is None:
        return storage().download_bytes(key), mime, filename
    return copy, "image/webp", f"{os.path.splitext(filename)[0]}.webp"


def display_copy(key: str, width: int) -> bytes | None:
    """The stored copy, made from the original on its first request; None when it cannot be made
    (the original is then served)."""
    from api.routes.images import make_copy

    client = storage()
    copy = image_storage.copy_key(key, width)
    try:
        if client.object_size(copy) is not None:
            return client.download_bytes(copy)
        with _COPY_SLOTS:
            made = make_copy(client.download_bytes(key), width)
        client.upload_bytes(copy, made, "image/webp")
        return made
    except Exception as exc:  # noqa: BLE001 - the original is always servable
        LOG.warning("attachment copy %s unavailable, serving the original: %r", copy, exc)
        return None


def delete_attachment(
    conn: "psycopg.Connection", property_id: int, attachment_id: int,
) -> dict[str, Any]:
    """Removes the row; the R2 object stays (a split's note copy may share it)."""
    pid = resolve_active_property_id(conn, property_id) or property_id
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM property_attachments WHERE id = %s AND property_id = %s",
                    (attachment_id, pid))
        if cur.rowcount == 0:
            raise HTTPException(404, "attachment not found")
    return {"deleted": True}


def _to_attachment(row: tuple[Any, ...]) -> dict[str, Any]:
    created = row[6]
    return {
        "id": int(row[0]),
        "property_id": int(row[1]),
        "note_id": None if row[2] is None else int(row[2]),
        "filename": row[3],
        "mime_type": row[4],
        "byte_size": int(row[5]),
        "created_at": created.isoformat() if isinstance(created, datetime) else created,
        "owner_email": row[7],
    }


# --- a building run's files (service role; the caller has checked the run is editable) -----


def insert_building_attachment(
    conn: "psycopg.Connection", *, building_run_id: int, file: UploadFile,
    uploaded_by: str | None = None,
) -> dict[str, Any]:
    upload = read_upload(file, BUILDING_TYPES)
    client = storage()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), count(*) FILTER (WHERE sha256_hex = %s) "
                    "FROM building_run_attachments WHERE building_run_id = %s",
                    (upload.sha256, building_run_id))
        total, same = cur.fetchone()
    if same:
        raise HTTPException(409, f"{upload.filename} is already attached to this building")
    if total >= MAX_FILES_PER_NOTE:
        raise HTTPException(409, f"a building holds at most {MAX_FILES_PER_NOTE} files")
    key = f"custom-attachments/building/{building_run_id}/{uuid.uuid4().hex}{ALLOWED[upload.mime]}"
    client.upload_bytes(key, upload.data, upload.mime)
    width, height = upload.size or (None, None)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO building_run_attachments (building_run_id, storage_key, filename, "
            "  mime_type, byte_size, width_px, height_px, sha256_hex, uploaded_by) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_BUILDING_PROJECTION}",
            (building_run_id, key, upload.filename, upload.mime, len(upload.data), width, height,
             upload.sha256, uploaded_by),
        )
        row = cur.fetchone()
        conn.commit()
    return _building_row(row)


def list_building_attachments(
    conn: "psycopg.Connection", building_run_id: int,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_BUILDING_PROJECTION} FROM building_run_attachments "
                    "WHERE building_run_id = %s ORDER BY created_at, id", (building_run_id,))
        return [_building_row(r) for r in cur.fetchall()]


def fetch_building_attachment(
    conn: "psycopg.Connection", attachment_id: int,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_BUILDING_PROJECTION} FROM building_run_attachments WHERE id = %s",
                    (attachment_id,))
        row = cur.fetchone()
    return _building_row(row) if row else None


def delete_building_attachment(
    conn: "psycopg.Connection", building_run_id: int, attachment_id: int,
) -> None:
    """Removes the row; the R2 object stays, as for a property's files."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM building_run_attachments WHERE id = %s AND building_run_id = %s",
                    (attachment_id, building_run_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "attachment not found")
        conn.commit()


def building_attachment_bytes(
    conn: "psycopg.Connection", attachment_id: int, building_run_id: int | None = None,
) -> tuple[bytes, str, str]:
    """(bytes, MIME type, file name) for read_floor_plan and the preview proxy, which names its
    run (another run's attachment is a 404)."""
    row = fetch_building_attachment(conn, attachment_id)
    if row is None or building_run_id not in (None, row["building_run_id"]):
        raise HTTPException(404, "attachment not found")
    return storage().download_bytes(row["storage_key"]), row["mime_type"], row["filename"]


def _building_row(row: Any) -> dict[str, Any]:
    keys = [k.strip() for k in _BUILDING_PROJECTION.split(",")]
    out = dict(zip(keys, row))
    out["created_at"] = out["created_at"].isoformat() if out["created_at"] is not None else None
    return out
