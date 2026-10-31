"""Rule verdicts qualify only for their exact catalog and effective facts."""

import pytest
from test_agent import context, run

from shadow_agent.agent import validate_finish
from shadow_agent.common import BusinessError
from shadow_agent.contracts import FinalProposal
from shadow_agent.result import assemble_result
from shadow_agent.tools.registry import execute


def test_identical_basic_reload_keeps_current_review(repo, job):
    ctx = context(repo, job)
    assert execute("read_material", {"detail": "basic"}, ctx).ok
    assert execute("check_rules", {}, ctx).ok
    review, review_id = ctx.review, ctx.review_id
    assert execute("read_material", {"detail": "basic"}, ctx).ok
    assert ctx.review is review
    assert ctx.review_id == review_id


def test_basic_reload_after_supplement_invalidates_review(repo, job):
    ctx = context(repo, job)
    for name, args in (
        ("read_material", {"detail": "basic"}),
        ("read_material", {"detail": "supplement"}),
        ("check_rules", {}),
    ):
        assert execute(name, args, ctx).ok
    old_id = ctx.review_id
    revision = ctx.snapshot.catalog_revision
    assert execute("read_material", {"detail": "basic"}, ctx).ok
    assert ctx.snapshot.catalog_revision == revision
    assert ctx.review is None and ctx.review_id is None
    assert old_id in ctx.observations  # History survives invalidation.


@pytest.mark.parametrize("mismatch", ["catalog", "facts"])
@pytest.mark.parametrize("save_status", ["saved", "unknown"])
def test_residual_stale_review_cannot_affect_finish_or_result(repo, job, mismatch, save_status):
    ctx, result = run(repo, job)
    assert result.draft.save_status == "saved"
    if mismatch == "catalog":
        ctx.review = ctx.review.model_copy(update={"catalog_revision": 999})
    else:
        ctx.review = ctx.review.model_copy(update={"material_sha256": "obsolete"})
    ctx.save_status = save_status
    if save_status == "saved":
        with pytest.raises(BusinessError) as error:
            validate_finish(ctx, FinalProposal(action="needs_input", reason_summary="旧审核"))
        assert error.value.code == "STALE_EVIDENCE"
    result = assemble_result(ctx)
    assert result.review_status is None
    assert result.risk_reasons == []
    assert result.task_status == "incomplete"
    assert result.error["code"] == "STALE_EVIDENCE"
    assert result.draft.save_status == save_status
    assert repo.current_draft(job.request_id, "M01")["revision"] == 1


def test_catalog_refresh_still_rejects_stale_save_then_allows_recheck(repo, catalog, job):
    ctx, result = run(repo, job)
    assert result.draft.save_status == "saved"
    newer = catalog.model_copy(deep=True)
    newer.rules[0].version += 1
    repo.apply_catalog_snapshot(newer, update=True)
    stale = execute("save_draft", {"suggested_at": result.suggested_at}, ctx)
    assert stale.code == "STALE_EVIDENCE"
    # A fresh run must obtain and consume new observations, rather than reusing the old verdict.
    fresh, result = run(repo, job)
    assert fresh.review.catalog_revision == fresh.snapshot.catalog_revision
    assert result.draft.save_status in ("updated", "reused")
    assert result.task_status == "needs_input"
