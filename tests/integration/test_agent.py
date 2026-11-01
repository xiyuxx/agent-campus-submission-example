import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import monotonic

import pytest

from shadow_agent.agent import run_item
from shadow_agent.common import BusinessError, new_id
from shadow_agent.config import Budget, Limits
from shadow_agent.context import ItemContext
from shadow_agent.contracts import Confirmation, FinalProposal, Request
from shadow_agent.models.base import ModelTurn
from shadow_agent.models.mock import MockModel
from shadow_agent.result import assemble_result
from shadow_agent.service import run_request
from shadow_agent.storage import Repository
from shadow_agent.tools.registry import execute
from shadow_agent.trace import NullTrace


def context(repo, job, limits=None):
    repo.register_request(job)
    repo.revalidate_current_draft(job.request_id, job.items[0].material_id)
    return ItemContext(
        job, job.items[0], "TEST", repo, Budget(limits or Limits(), monotonic() + 60), NullTrace()
    )


def run(repo, job, model=None, limits=None):
    ctx = context(repo, job, limits)
    asyncio.run(run_item(ctx, model or MockModel()))
    return ctx, assemble_result(ctx)


def test_unknown_out_of_scope_and_forged_observation(repo, job):
    ctx = context(repo, job)
    assert execute("publish", {}, ctx).code == "UNKNOWN_TOOL"
    assert (
        execute("read_material", {"material_id": "M02", "detail": "basic"}, ctx).code
        == "INVALID_ARGUMENTS"
    )
    assert execute("read_material", {"detail": "supplement"}, ctx).code == "PRECONDITION_FAILED"
    for name, args in [
        ("read_material", {"detail": "basic"}),
        ("read_material", {"detail": "supplement"}),
        ("check_rules", {}),
    ]:
        assert execute(name, {**args}, ctx).ok
    saved = execute(
        "save_draft",
        {
            "suggested_at": "2026-11-03T19:00:00+08:00",
            "review_observation_id": "FAKE",
            "slot_observation_id": "FAKE",
        },
        ctx,
    )
    assert not saved.ok
    assert ctx.budget.tools == 7
    assert repo.current_draft(job.request_id, "M01") is None


def test_mock_conflict_consumes_save_receipt(repo, job):
    ctx, result = run(repo, job)
    assert result.task_status == "needs_input"
    assert result.suggested_at == "2026-11-03T19:00:00+08:00"
    assert result.original_requested_at == "2026-11-03T18:00:00+08:00"
    assert ctx.save_observation_id in ctx.consumed
    assert [x.id for x in result.pending_items] == ["change_time"]


def test_saved_on_last_round_is_still_incomplete(repo, job):
    ctx, result = run(repo, job, limits=Limits(model_attempts=6))
    assert result.draft.save_status == "saved"
    assert result.draft.id
    assert result.task_status == "incomplete"
    assert ctx.budget.models == 6


def test_tool_budget_refuses_unexecuted_calls(repo, job):
    ctx, result = run(repo, job, limits=Limits(tool_attempts=2))
    assert result.task_status == "incomplete"
    assert ctx.budget.tools == 2
    assert result.draft.save_status == "not_attempted"


class PrematureFinish:
    async def next_action(self, messages, schemas):
        proposal = FinalProposal(action="finish", reason_summary="不执行工具就完成")
        return ModelTurn(
            {"role": "assistant", "content": proposal.model_dump_json()}, proposal=proposal
        )


def test_premature_success_cannot_bypass_tools(repo, job):
    ctx, result = run(repo, job, PrematureFinish(), Limits(model_attempts=3))
    assert ctx.budget.models == 3
    assert result.task_status == "incomplete"
    assert result.review_status is None
    assert result.draft.id is None


@pytest.mark.parametrize("fault", ["read_failure", "query_failure", "save_before"])
def test_technical_failures_are_not_business_confirmation(repo, job, fault):
    repo.fault = fault
    _, result = run(repo, job)
    assert result.task_status == "failed"
    assert result.draft.id is None
    if fault != "read_failure":
        assert result.review_status == "passed"
    if fault == "save_before":
        assert result.draft.save_status == "failed"


def test_retry_and_unknown(repo, job):
    repo.fault = "save_once"
    _, result = run(repo, job)
    assert result.draft.save_status == "saved"
    assert result.task_status == "needs_input"
    second = job.model_copy(update={"request_id": "OTHER"}, deep=True)
    second.items[0].publish_requirement.requested_at = second.items[
        0
    ].publish_requirement.candidate_times[0]
    second.items[0].publish_requirement.candidate_times = []
    repo.fault = "receipt_unknown"
    # Use an independent catalog database to avoid the previous valid draft's capacity.
    _, unknown = run(repo, second)
    assert unknown.task_status == "needs_input"  # existing other-request draft blocks this slot
    assert unknown.draft.save_status == "not_attempted"


def test_unknown_receipt_is_incomplete(repo, job):
    repo.fault = "receipt_unknown"
    _, result = run(repo, job)
    assert result.task_status == "incomplete"
    assert result.draft.save_status == "unknown"


def test_empty_candidates_and_missing_requirements(repo, job):
    job.items[0].publish_requirement.candidate_times = []
    ctx, result = run(repo, job)
    assert result.task_status == "needs_input"
    assert result.suggested_at is None
    assert result.draft.save_status == "not_attempted"
    assert [x.id for x in result.pending_items] == ["schedule:expand_range"]
    missing = job.model_copy(update={"request_id": "MISSING"}, deep=True)
    missing.items[0].publish_requirement.timezone = None
    ctx, result = run(repo, missing)
    assert result.task_status == "needs_input"
    assert not any(x.tool in ("query_slots", "save_draft") for x in ctx.observations.values())


def test_partial_confirmation_and_same_revision_reuse(repo, job, catalog):
    changed = catalog.model_copy(deep=True)
    changed.materials[0].caption = "示例最强功能"
    repo.apply_catalog_snapshot(changed, update=True)
    _, result = run(repo, job)
    assert result.review_status == "needs_review"
    assert len(result.pending_items) == 2
    first = Confirmation(
        operation_id="C1",
        draft_id=result.draft.id,
        expected_revision=1,
        decisions=[{"item_id": "change_time", "decision": "accept"}],
    )
    assert repo.confirm_draft(first)["current_draft"]["confirmation_status"] == "pending"
    second = Confirmation(
        operation_id="C2",
        draft_id=result.draft.id,
        expected_revision=1,
        decisions=[{"item_id": "risk:R-CAPTION-REVIEW", "decision": "accept"}],
    )
    assert repo.confirm_draft(second)["current_draft"]["confirmation_status"] == "confirmed"
    _, repeated = run(repo, job)
    assert repeated.draft.save_status == "reused"
    assert repeated.draft.revision == 1
    assert repeated.review_status == "needs_review"
    assert repeated.task_status == "completed"
    assert not repeated.pending_items


def test_changed_occupancy_revises_and_invalidates_old_confirmation(repo, job, catalog):
    job.items[0].publish_requirement.candidate_times.append(
        job.items[0].publish_requirement.requested_at.replace(hour=20)
    )
    _, first = run(repo, job)
    repo.confirm_draft(
        Confirmation(
            operation_id="C1",
            draft_id=first.draft.id,
            expected_revision=1,
            decisions=[{"item_id": "change_time", "decision": "accept"}],
        )
    )
    changed = catalog.model_copy(deep=True)
    extra = changed.schedule.entries[0].model_copy(update={"entry_id": "S2"}, deep=True)
    extra.at = job.items[0].publish_requirement.candidate_times[0]
    changed.schedule.entries.append(extra)
    repo.apply_catalog_snapshot(changed, update=True)
    _, second = run(repo, job)
    assert second.draft.id == first.draft.id
    assert second.draft.revision == 2
    assert second.suggested_at == "2026-11-03T20:00:00+08:00"
    assert second.confirmation_status == "pending"


def test_timeout_has_bounded_calls(repo, job):
    class SlowModel:
        async def next_action(self, messages, schemas):
            await asyncio.sleep(1)

    ctx, result = run(repo, job, SlowModel(), Limits(item_seconds=0.02))
    assert result.task_status == "incomplete"
    assert ctx.budget.models == 1
    assert ctx.budget.tools == 0


def test_cross_process_duplicate_and_output_no_overwrite(repo, job, tmp_path):
    input_path = tmp_path / "request.json"
    input_path.write_text(job.model_dump_json(), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "shadow_agent",
        "run",
        "--as-of",
        "2020-01-01T00:00:00Z",
        "--input",
        str(input_path),
        "--db",
        str(repo.path),
        "--mode",
        "mock",
        "--output",
    ]
    first = subprocess.run(
        [*command, str(tmp_path / "one")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    second = subprocess.run(
        [*command, str(tmp_path / "two")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert first.returncode == second.returncode == 3
    a, b = json.loads(first.stdout), json.loads(second.stdout)
    assert a["items"][0]["draft"]["id"] == b["items"][0]["draft"]["id"]
    assert b["items"][0]["draft"]["save_status"] == "reused"
    previous = (tmp_path / "one/output.json").read_bytes()
    duplicate = subprocess.run([*command, str(tmp_path / "one")], capture_output=True, timeout=30)
    assert duplicate.returncode == 2
    assert (tmp_path / "one/output.json").read_bytes() == previous


def test_two_writers_recheck_capacity(repo, job):
    barrier = Barrier(2)

    def worker(request_id):
        repository = Repository(repo.path)
        own = job.model_copy(update={"request_id": request_id}, deep=True)
        own.items[
            0
        ].new_publication = True  # Explicit independent publications still compete for capacity.
        repository.register_request(own)
        snapshot = repository.read_supplement(repository.read_material_basic("M01"))
        review = repository.read_rule_snapshot(snapshot)
        slots = repository.query_occupancy(request_id, own.items[0], snapshot, "alternatives")
        barrier.wait(timeout=5)
        try:
            return repository.save_draft(
                own,
                own.items[0],
                snapshot,
                review,
                slots,
                slots.slots[0]["at"],
                0,
                evidence_ids=[],
                run_id=new_id("TEST"),
            )["save_status"]
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, ["A", "B"]))
    assert sorted(results) == ["SLOT_CONFLICT", "saved"]


def test_batch_keeps_success_when_later_items_are_blocked(repo, tmp_path):
    job = Request.model_validate_json(
        (__import__("pathlib").Path(__file__).resolve().parents[2] / "data/request.json").read_text(
            encoding="utf-8"
        )
    )
    result = asyncio.run(
        run_request(job, repo, MockModel(), mode="mock", output=tmp_path / "batch")
    )
    assert result.task_status == "blocked"
    assert result.items[0].draft.save_status == "saved"
    assert result.items[1].review_status == "needs_info"
    assert result.items[2].review_status == "blocked"
