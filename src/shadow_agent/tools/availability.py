"""Legal model actions; the database remains authoritative at commit time."""

from ..policy import review_is_current


def read_details(ctx, *, include_pending: bool = False) -> list[str]:
    details = ["basic"]
    delivered = ctx.consumed | (set(ctx.observations) if include_pending else set())
    if (
        ctx.snapshot
        and not ctx.snapshot.supplemented
        and ctx.supplement_available
        and ctx.review
        and ctx.review_id in delivered
        and ctx.review.review_status in ("needs_info", "needs_review")
        and review_is_current(ctx.snapshot, ctx.review, ctx.snapshot.catalog_revision)
        and any(getattr(ctx.snapshot.material, x["field"]) is None for x in ctx.review.matches)
    ):
        details.append("supplement")
    return details


def available_tools(ctx, *, include_pending: bool = False) -> set[str]:
    # Pending observations are in the next request, but become usable for execution
    # only after that request has produced a valid response.
    delivered = ctx.consumed | (set(ctx.observations) if include_pending else set())
    if ctx.forced_status or ctx.save_status == "unknown" or ctx.input_issue:
        return set()
    if ctx.technical_error and not ctx.technical_error.get("retryable", False):
        return set()
    allowed = {"read_material"}
    if not ctx.snapshot:
        return allowed
    allowed.add("check_rules")
    if (
        not ctx.review
        or ctx.review_id not in delivered
        or not review_is_current(ctx.snapshot, ctx.review, ctx.snapshot.catalog_revision)
        or ctx.review.review_status not in ("passed", "needs_review")
        or ctx.item.publish_requirement.missing()
        or (ctx.draft_state and ctx.draft_state["validity"] == "rejected")
    ):
        return allowed
    allowed.add("query_slots")
    if ctx.slots and not ctx.schedule_refresh_required:
        slot_id, options = next(reversed(ctx.slots.items()))
        if slot_id in delivered and options.available_slots():
            allowed.add("save_draft")
    return allowed
