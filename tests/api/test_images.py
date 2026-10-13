"""Tests for GET /images/{key} — the public presigned-R2 redirect.

The redirect is unauthenticated (like /health), redirects a listing-image key
to a presigned R2 URL, and refuses any key that isn't the listing-image shape
so it can never presign the operator-private `custom-attachments/` uploads that
share the bucket. `?w=1080` redirects to the phone-size copy, making it from the
full-size photo on its first request and falling back to the full-size photo on any
failure.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image, ImageCms

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import main as api_main
from api.routes import images as images_route
from scraper import image_storage


def _jpeg(width: int, height: int, **save: object) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 40, 40)).save(out, "JPEG", **save)
    return out.getvalue()


class _FakeR2:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.objects: dict[str, bytes] = {}
        self.heads: list[str] = []
        self.uploads: list[tuple[str, str]] = []
        self.fail_upload = False

    def presigned_get(
        self, key: str, expires_in: int = 0, anchor_seconds: int = 0
    ) -> str:
        self.calls.append((key, expires_in, anchor_seconds))
        return f"https://example.r2.cloudflarestorage.com/bucket/{key}?sig=abc"

    def object_size(self, key: str) -> int | None:
        self.heads.append(key)
        data = self.objects.get(key)
        return None if data is None else len(data)

    def download_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def upload_bytes(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        if self.fail_upload:
            raise RuntimeError("R2 PUT failed")
        self.uploads.append((key, content_type))
        self.objects[key] = data


@pytest.fixture()
def fake_r2():
    return _FakeR2()


@pytest.fixture()
def client(monkeypatch, fake_r2):
    monkeypatch.setattr(image_storage, "is_configured", lambda: True)
    monkeypatch.setattr(image_storage.R2Client, "from_env", classmethod(lambda cls, **_kw: fake_r2))
    images_route._client = None  # reset the module-level lazy singleton
    yield TestClient(api_main.app)
    images_route._client = None


def test_valid_key_redirects_to_presigned_url(client):
    res = client.get("/images/2872083276/0001.jpg", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"].endswith("2872083276/0001.jpg?sig=abc")
    assert "max-age" in res.headers.get("cache-control", "")


def test_negative_id_key_allowed(client):
    # Non-sreality portals use synthetic negative native ids.
    res = client.get("/images/-4671/0009.jpg", follow_redirects=False)
    assert res.status_code == 302


def test_public_even_when_token_set(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret-token-xyz")
    res = client.get("/images/2872083276/0001.jpg", follow_redirects=False)
    assert res.status_code == 302


@pytest.mark.parametrize(
    "key",
    [
        "custom-attachments/building/1/abc.pdf",  # operator-private uploads
        "2872083276/0001.png",                    # wrong extension
        "../etc/passwd",                          # traversal
        "2872083276",                             # no sequence
        "foo/0001.jpg",                           # non-numeric id
    ],
)
def test_non_image_keys_rejected(client, key):
    res = client.get(f"/images/{key}", follow_redirects=False)
    assert res.status_code == 404


def test_presign_is_anchored_by_default(client, fake_r2):
    """The redirect target has to be stable across the hourly re-mint, or the browser
    re-downloads every photo it already holds."""
    client.get("/images/2872083276/0001.jpg", follow_redirects=False)
    _key, expires_in, anchor = fake_r2.calls[-1]
    assert anchor == images_route._PRESIGN_ANCHOR_DEFAULT
    assert expires_in == images_route._PRESIGN_TTL
    # The redirect itself must NOT be cached longer just because the target is stable —
    # that short TTL is what makes a credential rotation self-heal within the hour.
    assert images_route._REDIRECT_MAX_AGE == 3600


def test_anchor_kill_switch_reverts_to_per_request_signing(client, fake_r2, monkeypatch):
    monkeypatch.setenv("IMAGE_PRESIGN_ANCHOR_SECONDS", "0")
    client.get("/images/2872083276/0001.jpg", follow_redirects=False)
    assert fake_r2.calls[-1][2] == 0


def test_anchor_override_is_capped_below_the_presign_ttl(client, fake_r2, monkeypatch):
    """An anchor at or past the TTL would hand out already-expired URLs."""
    monkeypatch.setenv("IMAGE_PRESIGN_ANCHOR_SECONDS", "9999999")
    client.get("/images/2872083276/0001.jpg", follow_redirects=False)
    assert fake_r2.calls[-1][2] == images_route._PRESIGN_TTL // 2


def test_garbage_anchor_override_falls_back_to_the_default(client, fake_r2, monkeypatch):
    monkeypatch.setenv("IMAGE_PRESIGN_ANCHOR_SECONDS", "not-a-number")
    client.get("/images/2872083276/0001.jpg", follow_redirects=False)
    assert fake_r2.calls[-1][2] == images_route._PRESIGN_ANCHOR_DEFAULT


def test_unconfigured_storage_returns_503(client, monkeypatch):
    monkeypatch.setattr(image_storage, "is_configured", lambda: False)
    images_route._client = None
    res = client.get("/images/2872083276/0001.jpg", follow_redirects=False)
    assert res.status_code == 503



MASTER = "2872083276/0001.jpg"
COPY = "thumb/w1080/2872083276/0001.webp"


def test_without_w_the_hot_path_never_touches_r2(client, fake_r2):
    """The full-size redirect stays a pure presign: no HEAD, no GET, no resize."""
    res = client.get(f"/images/{MASTER}", follow_redirects=False)
    assert res.status_code == 302
    assert fake_r2.heads == [] and fake_r2.uploads == []


def test_existing_copy_redirects_to_the_copy(client, fake_r2):
    fake_r2.objects[COPY] = b"webp-bytes"
    res = client.get(f"/images/{MASTER}?v=2&w=1080", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"].endswith(f"{COPY}?sig=abc")
    assert res.headers["cache-control"] == "public, max-age=3600"
    assert fake_r2.uploads == []
    # Signed exactly as the full-size photo is: same TTL, same day anchor.
    assert fake_r2.calls[-1] == (
        COPY, images_route._PRESIGN_TTL, images_route._PRESIGN_ANCHOR_DEFAULT
    )


def test_missing_copy_is_made_uploaded_and_redirected(client, fake_r2):
    fake_r2.objects[MASTER] = _jpeg(1800, 1200)
    res = client.get(f"/images/{MASTER}?v=2&w=1080", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"].endswith(f"{COPY}?sig=abc")
    assert res.headers["cache-control"] == "public, max-age=3600"
    assert fake_r2.uploads == [(COPY, "image/webp")]
    with Image.open(io.BytesIO(fake_r2.objects[COPY])) as copy:
        assert (copy.format, copy.size) == ("WEBP", (1080, 720))


def test_negative_id_copy_key(client, fake_r2):
    fake_r2.objects["-4671/0009.jpg"] = _jpeg(1200, 900)
    res = client.get("/images/-4671/0009.jpg?w=1080", follow_redirects=False)
    assert res.headers["location"].endswith("thumb/w1080/-4671/0009.webp?sig=abc")


@pytest.mark.parametrize("w", ["480", "1800", "abc", "", "1080.0"])
def test_any_other_width_is_404(client, fake_r2, w):
    res = client.get(f"/images/{MASTER}?w={w}", follow_redirects=False)
    assert res.status_code == 404
    assert fake_r2.heads == []


def test_copy_route_still_refuses_private_keys(client):
    res = client.get("/images/custom-attachments/building/1/abc.pdf?w=1080", follow_redirects=False)
    assert res.status_code == 404


def test_resize_failure_falls_back_to_full_size_briefly(client, fake_r2):
    fake_r2.objects[MASTER] = b"not an image"
    res = client.get(f"/images/{MASTER}?w=1080", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"].endswith(f"{MASTER}?sig=abc")
    assert res.headers["cache-control"] == "public, max-age=300"
    assert fake_r2.uploads == []


def test_upload_failure_falls_back_to_full_size_briefly(client, fake_r2):
    fake_r2.objects[MASTER] = _jpeg(1800, 1200)
    fake_r2.fail_upload = True
    res = client.get(f"/images/{MASTER}?w=1080", follow_redirects=False)
    assert res.headers["location"].endswith(f"{MASTER}?sig=abc")
    assert res.headers["cache-control"] == "public, max-age=300"


def test_no_free_slot_falls_back_instead_of_queueing(client, fake_r2, monkeypatch):
    """A burst of first views waits a bounded time for a slot, then gets the full size."""
    fake_r2.objects[MASTER] = _jpeg(1800, 1200)
    monkeypatch.setattr(images_route, "_COPY_SLOTS", asyncio.Semaphore(0))
    monkeypatch.setattr(images_route, "_COPY_WAIT_SECONDS", 0.01)
    res = client.get(f"/images/{MASTER}?w=1080", follow_redirects=False)
    assert res.headers["location"].endswith(f"{MASTER}?sig=abc")
    assert res.headers["cache-control"] == "public, max-age=300"
    assert fake_r2.uploads == []


def test_copy_is_never_upscaled():
    with Image.open(io.BytesIO(images_route.make_copy(_jpeg(800, 600), 1080))) as copy:
        assert copy.size == (800, 600)


def test_copy_applies_exif_rotation():
    exif = Image.Exif()
    exif[0x0112] = 6  # stored landscape, shown portrait
    data = _jpeg(2000, 1500, exif=exif.tobytes())
    with Image.open(io.BytesIO(images_route.make_copy(data, 1080))) as copy:
        assert copy.size == (1080, 1440)
        assert copy.getexif().get(0x0112) is None


def test_copy_keeps_an_rgb_colour_profile():
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    data = _jpeg(1600, 1200, icc_profile=icc)
    with Image.open(io.BytesIO(images_route.make_copy(data, 1080))) as copy:
        assert copy.info.get("icc_profile") == icc
