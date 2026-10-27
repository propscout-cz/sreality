"""Tests for image_storage helpers (key generation, env-var validation)."""

from __future__ import annotations

import pytest

from scraper.image_storage import LISTING_PHOTO_KEY_RE, R2_ENV_VARS, image_key, is_configured


class _StreamResp:
    """Minimal stand-in for a streamed requests.Response (context manager)."""

    def __init__(self, content: bytes, headers: dict | None = None, status: int = 200) -> None:
        self._content = content
        self.headers = headers or {}
        self.status_code = status

    def __enter__(self) -> "_StreamResp":
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]


def _patch_get(monkeypatch, captured: list, resp: _StreamResp) -> None:
    import scraper.image_storage as image_storage

    class _Session:
        # scraper.egress sends every direct fetch as a non-following streamed request.
        def request(self, method, url, *, headers, timeout, stream, allow_redirects):
            assert (method, stream, allow_redirects) == ("GET", True, False)
            captured.append(url)
            return resp

    monkeypatch.setattr(image_storage, "_session", lambda: _Session())


def test_the_image_session_wears_the_crawlers_browser_identity():
    """iDNES serves galleries through its own site's redirector, whose WAF throttled
    and then blocked python-requests' bare identity (429 → 403, 2026-10-05): the
    fetch carries portal_base's desktop-Chrome UA, its client hints and an <img>
    Accept, through one session per process."""
    import scraper.image_storage as image_storage

    image_storage._SESSION = None
    headers = image_storage._session().headers
    assert headers["User-Agent"].startswith("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    assert "Chrome/" in headers["User-Agent"]
    assert headers["Accept"].startswith("image/avif,image/webp")
    assert headers["sec-ch-ua-mobile"] == "?0"
    assert image_storage._session() is image_storage._session()
    image_storage._SESSION = None


def test_image_key_is_namespaced_and_row_unique():
    assert image_key(2836292428, 17503887) == "img/2836292428/17503887.jpg"
    assert image_key(123, 4) == "img/123/4.jpg"


def test_image_key_cannot_collide_with_either_legacy_shape():
    # A new listing whose id equals an old listing's sreality_id minted the old listing's
    # `{prefix}/{seq:04d}.jpg` keys. `img/` rules that out whatever the numbers are, and the
    # image id keeps two rows of one listing apart (two NULL sequences both became 0000).
    assert not image_key(13251404, 1).endswith("13251404/0001.jpg")
    assert image_key(31419, 529986) != image_key(31419, 529987)


@pytest.mark.parametrize(
    "key", ["img/443628/226547358.jpg", "2872083276/0001.jpg", "-4671/0009.jpg"]
)
def test_listing_photo_whitelist_accepts_every_shape_the_bucket_holds(key):
    assert LISTING_PHOTO_KEY_RE.match(key)


def test_listing_photo_whitelist_accepts_what_the_writer_mints():
    # A shape change that forgot the whitelist would 404 every newly stored photo.
    assert LISTING_PHOTO_KEY_RE.match(image_key(443628, 226547358))


@pytest.mark.parametrize(
    "key",
    [
        "custom-attachments/building/1/a.jpg",    # the private uploads sharing the bucket
        "thumb/w1080/img/1/2.webp",               # copies are reached through ?w=, never directly
        "backups/x/2026-10-25/a.jpg",
        "img/custom-attachments/1.jpg",           # the namespace is not a free prefix
        "img/443628/226547358.pdf",
        "img/443628.jpg",
        "img/-1/2.jpg",
        "x/img/443628/226547358.jpg",
        "2872083276/001.jpg",
        "2872083276/0001.jpg\n",                  # `\Z`, not `$`
        "img/443628/226547358.jpg\n",
        "img/\u0661/2.jpg",                       # `[0-9]`, not the unicode-aware `\d`
    ],
)
def test_listing_photo_whitelist_rejects_everything_else(key):
    assert not LISTING_PHOTO_KEY_RE.match(key)


def test_is_configured_requires_all_vars(monkeypatch):
    for name in R2_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert is_configured() is False

    for name in R2_ENV_VARS[:-1]:
        monkeypatch.setenv(name, "x")
    assert is_configured() is False

    monkeypatch.setenv(R2_ENV_VARS[-1], "x")
    assert is_configured() is True


def test_from_env_raises_when_missing(monkeypatch):
    from scraper.image_storage import R2Client

    for name in R2_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="R2_ACCOUNT_ID"):
        R2Client.from_env()


def test_download_image_appends_transform(monkeypatch):
    """Bare CDN URLs 401 without the render-transform; the downloader adds it."""
    import scraper.image_storage as image_storage

    captured: list[str] = []
    _patch_get(monkeypatch, captured, _StreamResp(b"jpegbytes"))
    assert image_storage.download_image("https://d18-a.sdn.cz/x/y.jpeg") == b"jpegbytes"
    assert captured == ["https://d18-a.sdn.cz/x/y.jpeg?fl=res,1800,1800,1|shr,,20|jpg,80"]


def test_download_image_normalises_a_legacy_chain(monkeypatch):
    """The 868k-row legacy cohort carries the 749 CROP chain. Passing it through
    would keep re-downloading the cropped 4:3 frame, so it is REWRITTEN onto the
    master template before the request goes out."""
    import scraper.image_storage as image_storage

    captured: list[str] = []
    _patch_get(monkeypatch, captured, _StreamResp(b""))
    image_storage.download_image(
        "https://d18-a.sdn.cz/x/y.jpeg?fl=res,749,562,3|shr,,20|jpg,90"
    )
    assert captured == ["https://d18-a.sdn.cz/x/y.jpeg?fl=res,1800,1800,1|shr,,20|jpg,80"]


def test_download_image_rejects_oversize_content_length(monkeypatch):
    """A Content-Length over the cap is rejected before the body is read."""
    import scraper.image_storage as image_storage
    from scraper import media

    captured: list[str] = []
    headers = {"Content-Length": str(media.MAX_IMAGE_BYTES + 1)}
    _patch_get(monkeypatch, captured, _StreamResp(b"irrelevant", headers=headers))
    with pytest.raises(image_storage.NotAnImageError):
        image_storage.download_image("https://www.bazos.cz/img/1/1/1.jpg")


def test_download_image_rejects_oversize_body(monkeypatch):
    """A body that streams past the cap (no/short Content-Length) is rejected."""
    import scraper.image_storage as image_storage
    from scraper import media

    captured: list[str] = []
    oversize = b"\x00" * (media.MAX_IMAGE_BYTES + 1)
    _patch_get(monkeypatch, captured, _StreamResp(oversize))
    with pytest.raises(image_storage.NotAnImageError):
        image_storage.download_image("https://www.bazos.cz/img/1/1/1.jpg")


def test_with_transform_completes_rot_prefix_chain():
    """sreality ships some URLs with a prefix chain '?fl=rot,<deg>,0|' (trailing
    pipe). The CDN 400s it as-is AND with the pipe stripped; only the completed
    chain returns bytes. The rot op must be preserved — completing without it
    returns 200 but stores the photo unrotated (curl-verified)."""
    from scraper.image_storage import IMAGE_TRANSFORM_OPS, with_transform

    url = "https://d18-a.sdn.cz/d_18/c_img_a/x.jpeg?fl=rot,180,0|"
    assert with_transform(url) == (
        "https://d18-a.sdn.cz/d_18/c_img_a/x.jpeg?fl=rot,180,0|" + IMAGE_TRANSFORM_OPS
    )


def test_with_transform_completes_rot_chain_without_trailing_pipe():
    """A rot prefix without the trailing pipe gets exactly one pipe separator."""
    from scraper.image_storage import IMAGE_TRANSFORM_OPS, with_transform

    url = "https://d18-a.sdn.cz/d_18/c_img_a/x.jpeg?fl=rot,90,0"
    assert with_transform(url) == url + "|" + IMAGE_TRANSFORM_OPS


def test_with_transform_keeps_rot_when_normalising_a_legacy_chain():
    """A stored chain carrying BOTH a per-photo fact and the legacy serving ops:
    the rot survives in order, the crop ops are replaced."""
    from scraper.image_storage import IMAGE_TRANSFORM_OPS, with_transform

    url = "https://d18-a.sdn.cz/x/y.jpeg?fl=rot,90,0|res,749,562,3|shr,,20|jpg,90"
    assert with_transform(url) == (
        "https://d18-a.sdn.cz/x/y.jpeg?fl=rot,90,0|" + IMAGE_TRANSFORM_OPS
    )


def test_with_transform_normalises_legacy_complete_chain():
    """THE inversion: the legacy 749 chain is mode-3 CROPPED (4:3), so passing it
    through would keep serving a cropped photo. It is rewritten, not preserved."""
    from scraper.image_storage import IMAGE_TRANSFORM_OPS, with_transform

    legacy = "https://d18-a.sdn.cz/x/y.jpeg?fl=res,749,562,3|shr,,20|jpg,90"
    assert with_transform(legacy) == "https://d18-a.sdn.cz/x/y.jpeg?fl=" + IMAGE_TRANSFORM_OPS


def test_with_transform_drops_an_unrecognised_op():
    """The kept-ops list is an ALLOWLIST because the CDN is one. Carrying an op we
    don't recognise in front of the template builds a chain the CDN 400s, and
    `_classify_image_failure` parks a 400 terminally (source_unavailable, never
    retried) — so an unknown op is dropped, costing at worst one odd-looking photo."""
    from scraper.image_storage import IMAGE_TRANSFORM_OPS, with_transform

    url = "https://d18-a.sdn.cz/x/y.jpeg?fl=crp,1,2|res,749,562,3|shr,,20|jpg,90"
    assert with_transform(url) == "https://d18-a.sdn.cz/x/y.jpeg?fl=" + IMAGE_TRANSFORM_OPS


def test_with_transform_is_idempotent():
    """Running it over its own output must be byte-identical — the URL is what the
    browser and the HTTP cache key on."""
    from scraper.image_storage import with_transform

    once = with_transform("https://d18-a.sdn.cz/x/y.jpeg")
    assert with_transform(once) == once
    rot = with_transform("https://d18-a.sdn.cz/x/y.jpeg?fl=rot,180,0|")
    assert with_transform(rot) == rot


def test_with_transform_never_percent_encodes_the_chain():
    """The CDN allowlist matches the template LITERALLY: %2C / %7C are rejected."""
    from scraper.image_storage import with_transform

    out = with_transform("https://d18-a.sdn.cz/x/y.jpeg?fl=rot,180,0|")
    assert "%2C" not in out and "%7C" not in out
    assert "," in out and "|" in out


def test_with_transform_keeps_other_query_params_and_fragment():
    from scraper.image_storage import IMAGE_TRANSFORM_OPS, with_transform

    url = "https://d18-a.sdn.cz/x/y.jpeg?v=3&fl=res,749,562,3#frag"
    assert with_transform(url) == (
        "https://d18-a.sdn.cz/x/y.jpeg?v=3&fl=" + IMAGE_TRANSFORM_OPS + "#frag"
    )


def test_rendition_for_splits_sreality_from_native():
    from scraper.image_storage import (
        RENDITION_NATIVE,
        RENDITION_SREALITY_MASTER,
        rendition_for,
    )

    assert rendition_for("https://d18-a.sdn.cz/x/y.jpeg") == RENDITION_SREALITY_MASTER
    assert rendition_for("https://www.bazos.cz/img/1/1/1.jpg") == RENDITION_NATIVE


def _tiny_jpeg(width: int = 7, height: int = 5) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), (1, 2, 3)).save(buf, format="JPEG")
    return buf.getvalue()


def test_image_dimensions_measures_real_bytes():
    pytest.importorskip("PIL", reason="Pillow is a scraper-runtime dependency")
    from scraper.image_storage import image_dimensions

    assert image_dimensions(_tiny_jpeg(7, 5)) == (7, 5)


def test_image_dimensions_returns_none_on_garbage():
    """Measuring must never fail a download — undecodable bytes store unmeasured."""
    from scraper.image_storage import image_dimensions

    assert image_dimensions(b"not an image at all") is None
    assert image_dimensions(b"") is None


def test_download_image_leaves_non_sreality_url_untouched(monkeypatch):
    """The render-transform is sreality-CDN-only; bazos (and other portals')
    image URLs must download verbatim (they 404 on the sreality query)."""
    import scraper.image_storage as image_storage

    captured: list[str] = []
    _patch_get(monkeypatch, captured, _StreamResp(b"bazosbytes"))
    bazos = "https://www.bazos.cz/img/1/123/456.jpg"
    assert image_storage.download_image(bazos) == b"bazosbytes"
    assert captured == [bazos]  # no transform appended


class _Hop:
    """A non-streaming first hop through the proxy."""

    def __init__(self, status, headers=None, content=b""):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.is_redirect = status in (301, 302, 303, 307, 308) and "Location" in self.headers

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            resp = requests.Response()
            resp.status_code = self.status_code
            raise requests.HTTPError(response=resp)


def _patch_session(monkeypatch, calls: list, hop: _Hop, resp: _StreamResp):
    import scraper.image_storage as image_storage

    class _Session:
        def get(self, url, timeout=15.0, stream=False, allow_redirects=True, proxies=None):
            calls.append((url, allow_redirects, proxies))
            return hop

        def request(self, method, url, *, headers, timeout, stream, allow_redirects):
            calls.append((url, allow_redirects, None))
            return resp

    monkeypatch.setattr(image_storage, "_session", lambda: _Session())


def test_a_proxied_redirector_is_resolved_through_the_proxy_and_the_file_fetched_directly(monkeypatch):
    """iDNES links fresh photos through its own site (`/file/thumbnail/{id}`, a 301 to the
    static CDN) and that site blocks our datacenter address: the one hop goes through the
    portal's residential proxy, the bytes come straight from the CDN."""
    import scraper.image_storage as image_storage

    calls: list = []
    static = "https://sta-reality2.1gr.cz/sta/compile/thumbs/d/8/9/abc.jpg"
    _patch_session(monkeypatch, calls, _Hop(301, {"Location": static}), _StreamResp(b"jpegbytes"))
    proxies = {"http": "http://proxy.example", "https": "http://proxy.example"}
    url = "https://reality.idnes.cz/file/thumbnail/6ac5?profile=front_detail_article_big_fit"
    assert image_storage.download_image(url, proxies=proxies) == b"jpegbytes"
    assert calls == [(url, False, proxies), (static, False, None)]


def test_a_proxied_site_that_serves_the_bytes_itself_is_read_through_the_proxy(monkeypatch):
    import scraper.image_storage as image_storage

    calls: list = []
    _patch_session(monkeypatch, calls, _Hop(200, {"Content-Type": "image/jpeg"}, b"raw"), _StreamResp(b""))
    assert image_storage.download_image("https://www.portal.cz/img/1.jpg", proxies={"https": "http://p"}) == b"raw"
    assert len(calls) == 1


def test_a_proxied_hop_raises_its_status_like_any_fetch(monkeypatch):
    import pytest
    import requests

    import scraper.image_storage as image_storage

    calls: list = []
    _patch_session(monkeypatch, calls, _Hop(403), _StreamResp(b""))
    with pytest.raises(requests.HTTPError):
        image_storage.download_image("https://www.portal.cz/img/1.jpg", proxies={"https": "http://p"})


def test_the_image_session_is_the_egress_one() -> None:
    """Image URLs are page-supplied and their bytes reach the open /images proxy: every
    direct connection is resolved, checked and pinned (scraper.egress)."""
    import scraper.image_storage as image_storage
    from scraper import egress

    image_storage._SESSION = None
    session = image_storage._session()
    assert isinstance(session.get_adapter("https://cdn.example/x.jpg"), egress._PinnedAdapter)
    assert session.trust_env is False
    image_storage._SESSION = None


def test_an_image_url_on_a_private_address_is_never_fetched(monkeypatch) -> None:
    import scraper.image_storage as image_storage
    from scraper import egress

    calls: list = []
    _patch_get(monkeypatch, calls, _StreamResp(b"secret"))
    with pytest.raises(egress.EgressRefused):
        image_storage.download_image("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(egress.EgressRefused):
        image_storage.download_image("http://169.254.169.254/x.jpg", proxies={"http": "http://p"})
    assert calls == []


def test_a_redirect_to_a_private_address_is_refused_at_the_hop(monkeypatch) -> None:
    import scraper.image_storage as image_storage
    from scraper import egress

    calls: list = []
    _patch_get(monkeypatch, calls, _StreamResp(b"", headers={"Location": "http://10.0.0.7/x.jpg"}, status=302))
    with pytest.raises(egress.EgressRefused):
        image_storage.download_image("https://cdn.example/x.jpg")
    assert calls == ["https://cdn.example/x.jpg"]


@pytest.mark.parametrize("status, gone, dead", [(404, True, False), (410, True, False),
                                               (400, False, True), (415, False, True),
                                               (403, False, False), (503, False, False)])
def test_an_error_status_raises_like_raise_for_status(monkeypatch, status, gone, dead) -> None:
    """The HTTPError carries a response with the status: the image phase reads it to tell an
    expired URL (gone) and a dead one (permanent) from a throttle or an outage (transient)."""
    import requests

    import scraper.image_storage as image_storage
    from scraper.main import _is_dead_url_image_error, _is_gone_image_error

    _patch_get(monkeypatch, [], _StreamResp(b"", status=status))
    with pytest.raises(requests.HTTPError) as exc:
        image_storage.download_image("https://cdn.example/x.jpg")
    assert exc.value.response is not None and exc.value.response.status_code == status
    assert _is_gone_image_error(exc.value) is gone
    assert _is_dead_url_image_error(exc.value) is dead
