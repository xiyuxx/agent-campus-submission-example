import asyncio
import json

import httpx
import pytest
from test_agent import context, run

from shadow_agent.config import ModelSettings
from shadow_agent.models.deepseek import DeepSeekModel
from shadow_agent.models.mock import MockModel
from shadow_agent.service import run_request
from shadow_agent.tools.registry import execute
from shadow_agent.trace import TraceSink


def test_oversized_observation_is_explicit_and_not_usable(repo, job, catalog):
    changed = catalog.model_copy(deep=True)
    changed.materials[0].caption = "x" * 40000
    repo.apply_catalog_snapshot(changed, update=True)
    ctx, result = run(repo, job)
    assert result.task_status == "incomplete"
    assert result.review_status is None
    assert ctx.snapshot is None
    assert list(ctx.observations.values())[0].code == "OBSERVATION_TOO_LARGE"


def test_oversized_arguments_are_not_executed(repo, job):
    ctx = context(repo, job)
    result = execute(
        "read_material",
        json.dumps({"material_id": "M01", "detail": "basic", "reason_summary": "x" * 9000}),
        ctx,
    )
    assert result.code == "INVALID_ARGUMENTS"
    assert not result.data["executed"]
    assert ctx.snapshot is None


def test_oversized_context_is_not_sent(repo, job):
    job.items[0].publish_requirement.candidate_times *= 15000
    ctx, result = run(repo, job)
    assert result.task_status == "incomplete"
    assert result.error["code"] == "CONTEXT_LIMIT"
    assert ctx.budget.models == ctx.budget.tools == 0


@pytest.mark.parametrize(
    "variant", ["truncated", "duplicate", "mixed", "too_many", "reasoning", "huge"]
)
def test_invalid_provider_turn_never_partially_executes(repo, job, tmp_path, variant):
    calls = [
        {
            "id": "same",
            "type": "function",
            "function": {
                "name": "read_material",
                "arguments": '{"material_id":"M01","detail":"basic"}',
            },
        }
    ]
    if variant == "duplicate":
        calls *= 2
    if variant == "too_many":
        calls = [{**calls[0], "id": str(i)} for i in range(17)]
    message = {"role": "assistant", "content": None, "tool_calls": calls}
    finish = "length" if variant == "truncated" else "tool_calls"
    if variant == "mixed":
        message["content"] = '{"action":"finish","reason_summary":"bad"}'
    if variant == "reasoning":
        message["reasoning_content"] = "DO-NOT-STORE"

    async def handler(request):
        if variant == "huge":
            return httpx.Response(200, content=b" " * 270000)
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": finish}]}
        )

    async def scenario():
        model = DeepSeekModel(
            ModelSettings("synthetic-test-credential"), transport=httpx.MockTransport(handler)
        )
        try:
            return await run_request(job, repo, model, mode="mock", output=tmp_path / variant)
        finally:
            await model.aclose()

    result = asyncio.run(scenario())
    assert result.task_status == "incomplete"
    assert result.items[0].draft.id is None
    assert result.items[0].review_status is None
    assert "DO-NOT-STORE" not in (tmp_path / variant / "trace.jsonl").read_text(encoding="utf-8")


def test_trace_failure_after_commit_preserves_saved_state(repo, job, tmp_path, monkeypatch):
    emit = TraceSink.emit

    def failing(self, event_type, **data):
        if event_type == "tool_result" and data["result"]["tool"] == "save_draft":
            raise OSError("synthetic trace failure")
        return emit(self, event_type, **data)

    monkeypatch.setattr(TraceSink, "emit", failing)
    result = asyncio.run(
        run_request(job, repo, MockModel(), mode="mock", output=tmp_path / "trace-fail")
    )
    assert result.task_status == "failed"
    assert result.items[0].draft.save_status == "saved"
    assert repo.current_draft(job.request_id, "M01")["revision"] == 1
    assert result.items[0].error["code"] == "EVIDENCE_ERROR"


def test_trace_preflight_stops_before_save(repo, job, tmp_path, monkeypatch):
    def failing(self):
        raise OSError("synthetic preflight failure")

    monkeypatch.setattr(TraceSink, "check_writable", failing)
    result = asyncio.run(
        run_request(job, repo, MockModel(), mode="mock", output=tmp_path / "preflight")
    )
    assert result.task_status == "failed"
    assert repo.current_draft(job.request_id, "M01") is None


def test_cancellation_returns_incomplete(repo, job, tmp_path):
    class CancelModel:
        async def next_action(self, messages, schemas):
            raise asyncio.CancelledError

    result = asyncio.run(
        run_request(job, repo, CancelModel(), mode="mock", output=tmp_path / "cancel")
    )
    assert result.task_status == "incomplete"
    assert result.items[0].error["code"] == "INTERRUPTED"


def test_output_failure_updates_exit_code_without_repeating_save(repo, job, tmp_path, monkeypatch):
    from shadow_agent import service

    original = service.atomic_write

    def failing(path, content):
        if path.name == "summary.md":
            raise OSError("synthetic output failure")
        original(path, content)

    monkeypatch.setattr(service, "atomic_write", failing)
    result = asyncio.run(
        service.run_request(job, repo, MockModel(), mode="mock", output=tmp_path / "fail")
    )
    assert result.task_status == "failed"
    assert result.items[0].draft.save_status == "saved"
    metadata = json.loads((tmp_path / "fail/run.json").read_text(encoding="utf-8"))
    assert metadata["exit_code"] == 1
    assert metadata["evidence_error"]
    assert len(repo.inspect_draft(result.items[0].draft.id)["history"]) == 1


def test_refreshing_observation_ids_does_not_reset_retry_limit(repo, job, monkeypatch):
    from shadow_agent.common import BusinessError

    ctx = context(repo, job)
    for name, args in [
        ("read_material", {"detail": "basic"}),
        ("read_material", {"detail": "supplement"}),
        ("check_rules", {}),
    ]:
        execute(name, {**args}, ctx)
    attempts = 0

    def busy(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise BusinessError("DATABASE_BUSY", "synthetic busy", retryable=True)

    monkeypatch.setattr(repo, "save_draft", busy)
    assert execute("query_slots", {"query": "requested"}, ctx).ok
    ctx.consumed.update(ctx.observations)
    for index in range(3):
        execute("query_slots", {"query": "nearest"}, ctx)
        ctx.consumed.update(ctx.observations)  # Simulate delivering the actual observations.
        result = execute(
            "save_draft",
            {"suggested_at": "2026-11-03T19:00:00+08:00", "reason_summary": f"retry {index}"},
            ctx,
        )
    assert attempts == 2
    assert result.code == "RETRY_LIMIT"


def test_late_model_response_cannot_complete_after_deadline(repo, job):
    from shadow_agent.agent import run_item
    from shadow_agent.contracts import FinalProposal
    from shadow_agent.models.base import ModelTurn
    from shadow_agent.result import assemble_result

    job.items[0].publish_requirement.timezone = None
    ctx = context(repo, job)

    class LateModel:
        async def next_action(self, messages, schemas):
            ctx.budget.clock = lambda: ctx.budget.deadline + 1
            proposal = FinalProposal(action="needs_input", reason_summary="缺时区")
            return ModelTurn(
                {"role": "assistant", "content": proposal.model_dump_json()}, proposal=proposal
            )

    asyncio.run(run_item(ctx, LateModel()))
    assert assemble_result(ctx).task_status == "incomplete"
