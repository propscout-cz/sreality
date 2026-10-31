"""assess_property_visuals: a structured judgement of one property's photos and floor plan, for agents (ADR-0007).

The facets and the prompt are one file the operator edits (`skills/photo-facets.md`), whose hash is the prompt version.
A judgement is cached per photo set and prompt version in `photo_set_assessments`, shared by every agent: what goes in is
an ad's public photos and that file, never anything of an account's. Write-allowed toolkit exception (LLM-backed cache).
Not rule 14's parked condition scoring, and no other surface reads this cache.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

from scraper import image_storage
from toolkit.property_identity import resolve_active_property_id
from toolkit.vision_images import COMPARISON_MAX_EDGE, DOCUMENT_MAX_EDGE, image_block

if TYPE_CHECKING:
    import psycopg

    from api.llm_client import LLMClient
    from api.providers import ModelPrice

CALLED_FOR = "assess_property_visuals"
TOOL_NAME = "record_photo_assessment"
FACETS_PATH = Path(__file__).resolve().parents[1] / "skills" / "photo-facets.md"
# Opus until the comparison (scripts/photo_assessment_compare.py) says the cheaper model holds (MVP § 2). A swap is this
# setting; judgements already cached stay, since they are judgements of the same photos under the same prompt.
DEFAULT_MODEL = "claude-opus-5-5"
MODEL_SETTING = "llm_photo_assessment_model"
# Production, 2026-11-01, a 1-in-500 sample of live ads: stored photos p50 13, p90 28, p95 33; 25 % have a floor plan.
MAX_PHOTOS = 30
MAX_PLANS = 2
# Nine one-line notes are ~1,500 tokens. A reply's limit covers the model's thinking too, so this leaves it room; a
# reply cut off here is paid for and thrown away, while a higher limit only reserves more of the run's budget.
MAX_REPLY_TOKENS = 8_000
# Anthropic bills an image at about width * height / 750 tokens (a square is the worst shape for a long-edge limit).
PHOTO_TOKENS_MAX = COMPARISON_MAX_EDGE ** 2 // 750 + 1
PLAN_TOKENS_MAX = DOCUMENT_MAX_EDGE ** 2 // 750 + 1
EVIDENCE_MAX_CHARS = 300

_FACET_HEADING = re.compile(r"^### (.*)$", re.M)
_FACET_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
_REPLY_RULE = (f"\n\n---\nAnswer only by calling {TOOL_NAME}, once, with every facet above and nothing else. Its score "
               "is a whole number from 1 to 5 or null; its evidence is one line.")
_PLAN_TAG = "floor_plan"


class FacetFileError(ValueError):
    """A facet file no assessment can come from, in words its editor can act on."""


class PhotosUnreadable(RuntimeError):
    """A stored photo could not be read, so nothing was sent and nothing was paid."""


class InvalidReply(RuntimeError):
    """A reply the provider billed that is no assessment: never cached."""

    def __init__(self, reason: str, *, cost_usd: float, llm_call_id: int) -> None:
        super().__init__(reason)
        self.cost_usd, self.llm_call_id = cost_usd, llm_call_id


@dataclass(frozen=True)
class FacetFile:
    """The file as the operator wrote it: `system` is its text, `version` the hash of all the model is given."""

    text: str
    keys: tuple[str, ...]

    @property
    def system(self) -> str:
        return self.text + _REPLY_RULE

    @property
    def version(self) -> str:
        given = self.system + json.dumps(tool_schema(self), sort_keys=True)
        return "facets-" + hashlib.sha256(given.encode()).hexdigest()[:16]


def load_facets(path: Path | None = None) -> FacetFile:
    """The facet file, read each call so nothing outlives an edit; a heading that is no key is refused, never skipped."""
    text = (path or FACETS_PATH).read_text(encoding="utf-8")
    keys = _FACET_HEADING.findall(text)
    if not keys:
        raise FacetFileError("the facet file has no facet: each is a heading '### key'")
    for key in keys:
        if not _FACET_KEY.fullmatch(key):
            raise FacetFileError(f"'### {key}' is no facet key: lowercase letters, digits and _, starting with a letter")
        if keys.count(key) > 1:
            raise FacetFileError(f"facet {key!r} appears twice")
    return FacetFile(text=text, keys=tuple(keys))


def tool_schema(facets: FacetFile) -> dict[str, Any]:
    """The one output shape: every facet of the file, each exactly a score and a one-line note."""
    facet = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "score": {"type": ["integer", "null"], "minimum": 1, "maximum": 5,
                      "description": "1 poor to 5 excellent; null when the pictures do not show this facet."},
            "evidence": {"type": "string", "minLength": 1, "maxLength": EVIDENCE_MAX_CHARS, "pattern": r"^[^\r\n]+$",
                         "description": "One line naming what the pictures show."},
        },
        "required": ["score", "evidence"],
    }
    return {
        "name": TOOL_NAME,
        "description": "Record the assessment of this property's pictures. Call exactly once.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"facets": {"type": "object", "additionalProperties": False,
                                      "properties": {key: facet for key in facets.keys},
                                      "required": list(facets.keys)}},
            "required": ["facets"],
        },
    }


def schema_problem(reply_input: Any, facets: FacetFile) -> str | None:
    """Why this reply is no assessment, or None."""
    errors = sorted(Draft202012Validator(tool_schema(facets)["input_schema"]).iter_errors(reply_input), key=str)
    if not errors:
        return None
    return f"{'/'.join(map(str, errors[0].absolute_path)) or 'the reply'}: {errors[0].validator} failed"


@dataclass(frozen=True)
class Photo:
    image_id: int
    storage_path: str
    rendition: str | None
    width: int | None
    height: int | None

    def identity(self, role: str) -> list[Any]:
        # The re-master lane overwrites bytes under a stable key (migration 496): the rendition and size say which bytes.
        return [self.image_id, self.storage_path, self.rendition, self.width, self.height, role]


@dataclass(frozen=True)
class PhotoSet:
    """What one judgement looks at: the first MAX_PHOTOS of one ad's stored pictures in the ad's order, and its first
    MAX_PLANS floor plans. A property's ad is its canonical one, or else the first with a stored picture (live, then
    newest). The hash is these pictures, their order and their role alone, so a photo that arrives, a re-mastered
    file, a floor-plan tag or another canonical ad is a new set, and one beyond the cap changes nothing the model sees."""

    property_id: int
    listing_id: int | None
    stored: int
    pending: int
    photos: tuple[Photo, ...]
    plans: tuple[Photo, ...]

    @property
    def hash(self) -> str:
        identity = [p.identity("photo") for p in self.photos] + [p.identity("plan") for p in self.plans]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    @property
    def usable(self) -> bool:
        return bool(self.photos or self.plans)


_ADS_SQL = """
SELECT l.id, count(i.storage_path),
       count(i.id) FILTER (WHERE i.storage_path IS NULL AND i.unavailable_reason IS NULL)
FROM listings l
JOIN properties p ON p.id = l.property_id
LEFT JOIN images i ON i.listing_id = l.id
WHERE l.property_id = %(property_id)s
GROUP BY l.id, l.is_active, p.repr_listing_ref_id
ORDER BY l.id = p.repr_listing_ref_id DESC NULLS LAST, l.is_active DESC NULLS LAST, l.id DESC
"""

_IMAGES_SQL = """
SELECT i.id, i.storage_path, i.rendition, i.stored_width, i.stored_height, tag.logical_tag
FROM images i
LEFT JOIN LATERAL (SELECT t.logical_tag FROM image_clip_tags t WHERE t.image_id = i.id
                   ORDER BY t.tagged_at DESC LIMIT 1) tag ON true
WHERE i.listing_id = %(listing_id)s AND i.storage_path IS NOT NULL
ORDER BY i.sequence ASC NULLS LAST, i.id ASC
"""

_LOOKUP_SQL = """
SELECT facets, model, created_at FROM photo_set_assessments
WHERE photo_set_hash = %(hash)s AND prompt_version = %(version)s
"""

# The first judgement stands: two runs racing on one set both pay, and both get the one every later agent sees.
_STORE_SQL = """
INSERT INTO photo_set_assessments (photo_set_hash, prompt_version, facets, model, photos_used, plans_used, cost_usd)
VALUES (%(hash)s, %(version)s, %(facets)s, %(model)s, %(photos_used)s, %(plans_used)s, %(cost_usd)s)
ON CONFLICT (photo_set_hash, prompt_version) DO NOTHING
"""

_MODEL_SQL = "SELECT value FROM app_settings WHERE key = %(key)s"


def select_photos(rows: Sequence[tuple[Any, ...]]) -> tuple[tuple[Photo, ...], tuple[Photo, ...]]:
    """(photos, floor plans) from an ad's stored images in its order. A plan not yet tagged travels as a photo."""
    photos = [Photo(*row[:5]) for row in rows if row[5] != _PLAN_TAG]
    plans = [Photo(*row[:5]) for row in rows if row[5] == _PLAN_TAG]
    return tuple(photos[:MAX_PHOTOS]), tuple(plans[:MAX_PLANS])


def photo_set(conn: "psycopg.Connection", property_id: int) -> PhotoSet | None:
    """The property's photo set (a merged-away id follows its survivor), or None for no such property."""
    survivor = resolve_active_property_id(conn, property_id)
    if survivor is None:
        return None
    with conn.cursor() as cur:
        cur.execute(_ADS_SQL, {"property_id": survivor})
        ads = cur.fetchall()
        chosen = next((ad for ad in ads if ad[1]), None)
        if chosen is None:
            return PhotoSet(property_id=survivor, listing_id=ads[0][0] if ads else None, stored=0,
                            pending=sum(ad[2] for ad in ads), photos=(), plans=())
        cur.execute(_IMAGES_SQL, {"listing_id": chosen[0]})
        photos, plans = select_photos(cur.fetchall())
    return PhotoSet(property_id=survivor, listing_id=chosen[0], stored=chosen[1], pending=chosen[2], photos=photos,
                    plans=plans)


def resolve_model(conn: "psycopg.Connection") -> str:
    with conn.cursor() as cur:
        cur.execute(_MODEL_SQL, {"key": MODEL_SETTING})
        row = cur.fetchone()
    return row[0] if row and isinstance(row[0], str) else DEFAULT_MODEL


def worst_case_usd(photos: PhotoSet, facets: FacetFile, price: "ModelPrice") -> float:
    """Every prompt token at the dearer of the input and cache-write rates, and a reply at its limit."""
    prompt = len(facets.system.encode()) + len(json.dumps(tool_schema(facets)).encode())
    tokens = prompt + len(photos.photos) * PHOTO_TOKENS_MAX + len(photos.plans) * PLAN_TOKENS_MAX
    return (tokens * max(price.input_per_mtok, price.cache_write_per_mtok)
            + MAX_REPLY_TOKENS * price.output_per_mtok) / 1e6


def _image_blocks(r2: Any, photos: Sequence[Photo], max_edge: int) -> list[dict[str, Any]]:
    return [image_block(r2, photo.storage_path, max_edge) for photo in photos]


def _content(photos: PhotoSet, r2: Any | None) -> list[dict[str, Any]]:
    """Photos at the comparison tier: finish, light and styling need no fine detail, and 768 px costs a quarter of 1568's
    tokens. A floor plan at the document tier, for its labels and thin lines."""
    if r2 is None:
        if not image_storage.is_configured():
            raise PhotosUnreadable("the image bucket is not configured here")
        r2 = image_storage.R2Client.from_env()
    try:
        content = [{"type": "text", "text": f"The property's photos ({len(photos.photos)}):"},
                   *_image_blocks(r2, photos.photos, COMPARISON_MAX_EDGE)]
        if photos.plans:
            content += [{"type": "text", "text": f"Its floor plan ({len(photos.plans)}):"},
                        *_image_blocks(r2, photos.plans, DOCUMENT_MAX_EDGE)]
    except Exception as exc:
        raise PhotosUnreadable(f"a stored photo could not be read: {type(exc).__name__}") from exc
    return [*content, {"type": "text", "text": f"Assess this property with {TOOL_NAME}."}]


@dataclass(frozen=True)
class Judgement:
    facets: dict[str, Any]
    model: str
    cost_usd: float
    llm_call_id: int


def judge(llm: "LLMClient", photos: PhotoSet, facets: FacetFile, *, model: str, r2: Any | None = None) -> Judgement:
    """One paid judgement of `photos` on `model`, checked against the schema and cached by no one; InvalidReply if the
    reply is no assessment. The tool cannot be forced (a 400 on Opus and Sonnet 5.5), so a reply that skips it lands
    there too. File and schema form a stable prefix for the provider's prompt cache; the pictures follow it."""
    content = _content(photos, r2)
    response = llm.call(called_for=CALLED_FOR, messages=[{"role": "user", "content": content}], system=facets.system,
                        tools=[tool_schema(facets)], model=model, max_tokens=MAX_REPLY_TOKENS)

    def refused(reason: str) -> InvalidReply:
        return InvalidReply(reason, cost_usd=response.cost_usd, llm_call_id=response.llm_call_id)

    stop = response.completion.stop_reason
    if stop != "tool_use":
        raise refused(f"the model stopped on {stop} without an assessment")
    calls = [c for c in response.tool_calls if c["name"] == TOOL_NAME]
    if len(calls) != 1:
        raise refused(f"the model called {TOOL_NAME} {len(calls)} times, not once")
    problem = schema_problem(calls[0]["input"], facets)
    if problem:
        raise refused(f"the reply failed the facet schema at {problem}")
    return Judgement(facets=calls[0]["input"]["facets"], model=response.model, cost_usd=response.cost_usd,
                     llm_call_id=response.llm_call_id)


def _lookup(conn: "psycopg.Connection", photo_set_hash: str, version: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(_LOOKUP_SQL, {"hash": photo_set_hash, "version": version})
        row = cur.fetchone()
    return None if row is None else {"facets": row[0], "model": row[1], "created_at": row[2].isoformat()}


def _store(conn: "psycopg.Connection", photos: PhotoSet, facets: FacetFile, judged: Judgement) -> None:
    from psycopg.types.json import Jsonb

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_STORE_SQL, {"hash": photos.hash, "version": facets.version, "facets": Jsonb(judged.facets),
                                 "model": judged.model, "photos_used": len(photos.photos),
                                 "plans_used": len(photos.plans), "cost_usd": judged.cost_usd})


def assess(conn: "psycopg.Connection", photos: PhotoSet, *,
           pay: Callable[[PhotoSet, FacetFile], Judgement]) -> dict[str, Any]:
    """The tool's envelope for `photos`: no usable picture is a result with no call, a cached judgement is served, and
    only a miss asks `pay`, the caller's bound on what a judgement may cost."""
    from toolkit import _now_iso

    facets = load_facets()
    data: dict[str, Any] = {
        "property_id": photos.property_id, "listing_id": photos.listing_id, "status": "no_photos", "facets": None,
        "photos_stored": photos.stored, "photos_pending": photos.pending, "photos_used": len(photos.photos),
        "plans_used": len(photos.plans), "photo_set_hash": None, "prompt_version": facets.version, "model": None,
        "assessed_at": None, "cache_hit": False, "cost_usd": 0.0,
    }
    if not photos.usable:
        data["note"] = ("This property has no stored photo yet, so nothing was assessed and nothing was paid"
                        + (f"; {photos.pending} are still downloading." if photos.pending else "."))
    else:
        row = _lookup(conn, photos.hash, facets.version)
        data["cache_hit"] = row is not None
        if row is None:
            judged = pay(photos, facets)
            _store(conn, photos, facets, judged)
            row = _lookup(conn, photos.hash, facets.version)
            data["cost_usd"] = judged.cost_usd
        data.update(status="assessed", facets=row["facets"], model=row["model"], assessed_at=row["created_at"],
                    photo_set_hash=photos.hash)
    return {
        "data": data,
        "metadata": {
            "tool": CALLED_FOR,
            "filters_used": {"property_id": photos.property_id},
            "result_count": 1 if data["status"] == "assessed" else 0,
            "queried_at": _now_iso(),
            "data_freshness": data["assessed_at"],
        },
    }
