"""Independent nearest-node oracle, rule-refresh sequences and malformed HTTP boundaries."""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest
from test_agent import context

from shadow_agent.agent import validate_finish
from shadow_agent.common import BusinessError, utc
from shadow_agent.config import ModelSettings
from shadow_agent.contracts import Catalog, FinalProposal, Request
from shadow_agent.models.base import ModelTurn
from shadow_agent.models.deepseek import DeepSeekModel
from shadow_agent.models.mock import MockModel
from shadow_agent.result import assemble_result
from shadow_agent.service import run_request
from shadow_agent.tools.registry import execute


@pytest.mark.parametrize("explicit", [False, True], ids=["preset", "explicit"])
@pytest.mark.parametrize("mask", range(64), ids=lambda x: f"occupancy-{x:06b}")
def test_nearest_matches_independent_oracle_for_every_occupancy(repo, catalog, job, mask, explicit):
    origin = job.items[0].publish_requirement.requested_at
    nodes = [origin + timedelta(hours=x) for x in (-25, -2, -1, 1, 2, 25)]
    occupied = {node for index, node in enumerate(nodes) if mask & (1 << index)}
    value = catalog.model_dump(mode="json")
    value["materials"][0]["copyright_status"] = "authorized"
    value["schedule"]["allowed_slots"] = (
        []
        if explicit
        else [
            {"platform": "douyin", "account_id": "brand_main", "at": node.isoformat()}
            for node in reversed(nodes)
        ]
    )
    value["schedule"]["entries"] = [
        {
            "entry_id": f"O-{index}",
            "material_id": "OTHER",
            "platform": "douyin",
            "account_id": "brand_main",
            "at": node.isoformat(),
        }
        for index, node in enumerate(sorted(occupied | {origin}))
    ]
    repo.apply_catalog_snapshot(Catalog.model_validate(value), update=True)
    request = job.model_dump(mode="json")
    request["items"][0]["publish_requirement"]["candidate_times"] = (
        [node.isoformat() for node in reversed(nodes)] + [nodes[0].isoformat()]
        if explicit
        else None
    )
    request = Request.model_validate(request)
    snapshot = repo.read_material_basic("M01")
    options = repo.query_schedule_options(request.request_id, request.items[0], snapshot)
    assert not options.requested.slots[0]["available"]
    result = options.alternatives
    assert result is not None and result.catalog_revision == options.requested.catalog_revision
    # Oracle operates on sets of datetimes, independently of SQL/string scan order.
    free = set(nodes) - occupied
    before = [node for node in free if node < origin]
    after = [node for node in free if node > origin]
    expected = ([max(before)] if before else []) + ([min(after)] if after else [])
    assert [datetime.fromisoformat(x["at"]) for x in result.slots] == expected
    assert len(result.slots) <= 2
    assert all(x["available"] and not x["conflicts"] for x in result.slots)


def changed_rules_context(repo, catalog, job, status):
    value = catalog.model_dump(mode="json")
    matched = status != "passed"
    value["rules"] = [
        {
            "rule_id": "BRAND",
            "version": 1,
            "field": "caption",
            "operator": "contains",
            "value": "字幕" if matched else "不匹配的旧条件",
            "severity": status if matched else "blocked",
            "message": "旧规则",
        }
    ]
    original = Catalog.model_validate(value)
    repo.apply_catalog_snapshot(original, update=True)
    ctx = context(repo, job)
    assert execute("read_material", {"detail": "basic"}, ctx).ok
    assert execute("check_rules", {}, ctx).ok
    assert ctx.review.review_status == status
    old_hash = ctx.snapshot.facts_hash()
    original.rules[0].version = 2
    original.rules[0].value = "不匹配的新条件" if matched else "字幕"
    original.rules[0].message = "新规则"
    repo.apply_catalog_snapshot(original, update=True)
    assert execute("read_material", {"detail": "basic"}, ctx).ok
    assert ctx.snapshot.facts_hash() == old_hash
    return ctx


@pytest.mark.parametrize("status", ["passed", "blocked", "needs_info", "needs_review"])
def test_reloading_same_facts_invalidates_old_rule_review(repo, catalog, job, status):
    ctx = changed_rules_context(repo, catalog, job, status)
    assert assemble_result(ctx).review_status is None


def test_old_block_cannot_finish_after_rules_are_replaced(repo, catalog, job):
    ctx = changed_rules_context(repo, catalog, job, "blocked")
    with pytest.raises(BusinessError):
        validate_finish(ctx, FinalProposal(action="block", reason_summary="继续沿用旧规则"))


@pytest.mark.parametrize("status", ["passed", "blocked", "needs_info", "needs_review"])
def test_explicit_rule_recheck_obtains_new_verdict(repo, catalog, job, status):
    ctx = changed_rules_context(repo, catalog, job, status)
    assert execute("check_rules", {}, ctx).ok
    assert ctx.review.review_status == ("blocked" if status == "passed" else "passed")
    assert ctx.review.catalog_revision == ctx.snapshot.catalog_revision


def response_with(message, finish="tool_calls"):
    return {"choices": [{"message": message, "finish_reason": finish}]}


def bad_shape(value, label):
    return pytest.param(value, id=label)


@pytest.mark.parametrize(
    "body",
    [
        bad_shape(response_with(None), "message-null"),
        bad_shape(response_with([]), "message-array"),
        bad_shape(response_with("warming-up"), "message-string"),
        bad_shape(response_with({"role": "assistant", "tool_calls": [None]}), "call-null"),
        bad_shape(response_with({"role": "assistant", "tool_calls": [0]}), "call-number"),
        bad_shape(
            response_with(
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "C", "type": "function", "function": None}],
                }
            ),
            "function-null",
        ),
        bad_shape(
            response_with(
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "C", "type": "function", "function": []}],
                }
            ),
            "function-array",
        ),
        pytest.param([], id="root-array-handled"),
        pytest.param({"choices": []}, id="choices-empty-handled"),
        pytest.param(response_with({}), id="missing-role-handled"),
    ],
)
def test_invalid_provider_shapes_are_classified_as_protocol_errors(body):
    async def handler(request):
        return httpx.Response(200, json=body)

    async def scenario():
        model = DeepSeekModel(
            ModelSettings("synthetic-test-credential"), transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(BusinessError) as error:
                await model.next_action([], [])
            assert error.value.code == "MODEL_PROTOCOL"
        finally:
            await model.aclose()

    asyncio.run(scenario())


def test_requested_time_is_independent_of_default_nodes(repo, catalog, job):
    value = catalog.model_dump(mode="json")
    value["schedule"]["allowed_slots"] = []
    value["schedule"]["entries"] = []
    repo.apply_catalog_snapshot(Catalog.model_validate(value), update=True)
    snapshot = repo.read_material_basic("M01")
    result = repo.query_occupancy(job.request_id, job.items[0], snapshot, "requested")
    assert result.slots[0]["at"] == utc(job.items[0].publish_requirement.requested_at)
    assert result.slots[0]["available"]


def test_running_agent_cannot_finish_with_old_rule_after_refresh(repo, catalog, job, tmp_path):
    value = job.model_dump(mode="json")
    value["items"][0]["material_id"] = "M03"
    request = Request.model_validate(value)

    class OldVerdictAfterRefresh(MockModel):
        changed = False

        async def next_action(self, messages, schemas):
            results = [json.loads(x["content"]) for x in messages if x["role"] == "tool"]
            if results and results[-1]["tool"] == "check_rules" and not self.changed:
                updated = catalog.model_copy(deep=True)
                updated.rules = [x for x in updated.rules if x.rule_id != "R-COPYRIGHT-DENIED"]
                repo.apply_catalog_snapshot(updated, update=True)
                self.changed = True
                call = {
                    "id": "refresh-basic",
                    "type": "function",
                    "function": {"name": "read_material", "arguments": '{"detail":"basic"}'},
                }
                return ModelTurn(
                    {"role": "assistant", "content": None, "tool_calls": [call]}, calls=[call]
                )
            if self.changed:
                proposal = FinalProposal(action="block", reason_summary="沿用旧审核结论")
                return ModelTurn(
                    {"role": "assistant", "content": proposal.model_dump_json()}, proposal=proposal
                )
            return await super().next_action(messages, schemas)

    output = tmp_path / "rule-refresh"
    result = asyncio.run(
        run_request(request, repo, OldVerdictAfterRefresh(), mode="mock", output=output)
    )
    fresh = repo.read_rule_snapshot(repo.read_material_basic("M03"))
    (output / "database_check.json").write_text(fresh.model_dump_json(indent=2), encoding="utf-8")
    assert fresh.review_status == "passed"
    assert result.items[0].review_status is None or result.task_status == "incomplete"


def test_one_malformed_provider_response_does_not_abort_recoverable_run(repo, job, tmp_path):
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, json=response_with(None))
        body = json.loads(request.content)
        turn = await MockModel().next_action(body["messages"], body["tools"])
        return httpx.Response(
            200, json=response_with(turn.assistant_message, "tool_calls" if turn.calls else "stop")
        )

    async def scenario():
        model = DeepSeekModel(
            ModelSettings("synthetic-test-credential"), transport=httpx.MockTransport(handler)
        )
        try:
            return await run_request(
                job, repo, model, mode="mock", output=tmp_path / "provider-recovery"
            )
        finally:
            await model.aclose()

    result = asyncio.run(scenario())
    assert result.items[0].draft.save_status == "saved"
    assert result.task_status == "needs_input"
    assert attempts > 1
