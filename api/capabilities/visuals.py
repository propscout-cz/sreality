"""assess_property_visuals: a market fact that costs money the first time it is asked (ADR-0007)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from api.capabilities.entry import Context, Effect, Entry, Tenancy
from api.capabilities.errors import ToolError, ToolErrorCode
from api.filter_args import Args
from api.llm_client import ProviderError, provider_for_model
from toolkit import photo_assessment as pa

NAME = "assess_property_visuals"
# ADR-0007's ten bounds what a run pays, so it counts bills: a cached answer costs nothing and takes no slot, and a reply
# refused by the schema took one.
MAX_PAID_PER_RUN = 10


class AssessArgs(Args):
    property_id: int = Field(ge=1, description="The property to judge; a merged-away id follows its survivor.")


def _refused(message: str) -> ToolError:
    return ToolError(ToolErrorCode.REFUSED, message)


def _unavailable(message: str) -> ToolError:
    return ToolError(ToolErrorCode.UNAVAILABLE, message)


def _pay(ctx: Context, photos: pa.PhotoSet, facets: pa.FacetFile) -> pa.Judgement:
    """One judgement, if this run may have one: refused before any money, every bill on the run's."""
    run = ctx.run
    if run is None:
        raise _refused(f"this property has no cached assessment, and {NAME} pays for a new one only inside an agent run, "
                       "which holds its budget and its limit")
    if run.counts[NAME] >= MAX_PAID_PER_RUN:
        raise _refused(f"this run has paid for {MAX_PAID_PER_RUN} photo assessments, the most one run may; a property "
                       "already assessed still answers, and the rest must be judged without their photos")
    model = pa.resolve_model(ctx.conn)
    try:
        price = run.llm.provider(provider_for_model(model)).price_for(model)
    except ProviderError as exc:
        raise _unavailable(f"the model for photo assessment is not available: {exc}") from exc
    if price is None:
        raise _unavailable(f"the photo assessment model {model!r} has no price, so its cost could not be capped")
    worst = pa.worst_case_usd(photos, facets, price)
    if worst > run.left_usd():
        raise _refused(f"this run's budget has ${max(run.left_usd(), 0):.2f} left and one assessment may cost "
                       f"${worst:.2f}; nothing was paid")
    run.counts[NAME] += 1
    try:
        judged = pa.judge(run.llm, photos, facets, model=model)
    except pa.PhotosUnreadable as exc:
        run.counts[NAME] -= 1
        raise _unavailable(f"{exc}; nothing was paid") from exc
    except ProviderError as exc:
        run.counts[NAME] -= 1
        raise _unavailable(f"the model did not answer: {exc}") from exc
    except pa.InvalidReply as exc:
        run.charge(exc.cost_usd, exc.llm_call_id)
        raise _refused(f"the model's reply was not a valid assessment and was not kept ({exc}); it was paid for and "
                       "used one of this run's assessments") from exc
    except Exception:
        # The provider may have billed and its ledger row failed (the INSERT follows the bill): the slot stays taken and
        # the bill is taken at its worst, so neither limit goes blind.
        run.charge(worst, None)
        raise
    run.charge(judged.cost_usd, judged.llm_call_id)
    return judged


def _assess(ctx: Context, args: AssessArgs) -> dict[str, Any]:
    photos = pa.photo_set(ctx.conn, args.property_id)
    if photos is None:
        raise ToolError(ToolErrorCode.NOT_FOUND, f"there is no property {args.property_id}")
    return pa.assess(ctx.conn, photos, asked=args.property_id, pay=lambda p, f: _pay(ctx, p, f))


ENTRIES = (
    Entry(
        name=NAME,
        description="A structured judgement of one property from its advert's photos and floor plan, the same for "
        "every agent: data.facets holds a score from 1 (poor) to 5 (excellent), or null when the pictures do not show "
        "it, and a one-line evidence note, per facet (view, neighbourhood as seen, shared spaces, kitchen, bathrooms, "
        "equipment, finish quality, lighting, styling, as the operator's facet file names them). Judged once per set "
        f"of photos: data.cache_hit means it cost nothing. A run may pay for at most {MAX_PAID_PER_RUN}, so ask only "
        "about a shortlist; a cached one takes no slot. status 'no_photos' or 'photos_pending' (see note) is an answer, "
        "with facets null and nothing paid; after 'photos_pending', ask again later. "
        "This is what the pictures show, not the condition the portal states, and no cohort statistic uses it.",
        args=AssessArgs, handler=_assess, tenancy=Tenancy.MARKET, effect=Effect.COSTLY,
    ),
)
