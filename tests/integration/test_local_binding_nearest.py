"""New business contract: local identities and nearest free node on each side."""

import asyncio
import json

import httpx
import pytest
from test_agent import context, run

from shadow_agent.agent import validate_finish
from shadow_agent.common import BusinessError, utc
from shadow_agent.config import ModelSettings
from shadow_agent.contracts import FinalProposal, ScheduleEntry, Slot, parse_time
from shadow_agent.model_view import tool_view
from shadow_agent.models.deepseek import DeepSeekModel
from shadow_agent.models.mock import MockModel
from shadow_agent.result import assemble_result
from shadow_agent.service import run_request
from shadow_agent.tools.registry import execute, schemas


def prepare(repo, catalog, job, nodes, occupied):
    updated = catalog.model_copy(deep=True)
    updated.materials[0].copyright_status = "authorized"
    updated.schedule.allowed_slots = [
        Slot(platform="douyin", account_id="brand_main", at=parse_time(at)) for at in nodes
    ]
    updated.schedule.entries = [
        ScheduleEntry(
            entry_id=f"PRIVATE-OCCUPANCY-{i}",
            material_id="OTHER-MATERIAL",
            platform="douyin",
            account_id="brand_main",
            at=parse_time(at),
        )
        for i, at in enumerate(occupied)
    ]
    repo.apply_catalog_snapshot(updated, update=True)
    job.items[0].publish_requirement.candidate_times = None
    return context(repo, job)


ORIGINAL = "2026-11-03T18:00:00+08:00"


@pytest.mark.parametrize(
    "nodes,occupied,expected",
    [
        (
            ["17:00", "17:30", "18:00", "18:30", "19:00", "20:00"],
            ["17:30", "18:00", "18:30"],
            ["17:00", "19:00"],
        ),
        (["17:00", "19:00"], ["17:00"], ["19:00"]),
        (["17:00", "19:00"], ["19:00"], ["17:00"]),
        (["17:00", "19:00"], ["17:00", "19:00"], []),
        (["17:00", "17:30", "18:30", "19:00"], [], ["17:30", "18:30"]),
    ],
)
def test_nearest_one_on_each_side(repo, catalog, job, nodes, occupied, expected):
    def stamp(text):
        return f"2026-11-03T{text}:00+08:00"

    ctx = prepare(repo, catalog, job, list(map(stamp, nodes)), list(map(stamp, occupied)))
    snapshot = repo.read_material_basic(ctx.material_id)
    result = repo.query_occupancy(job.request_id, ctx.item, snapshot, "alternatives")
    assert [x["at"] for x in result.slots] == [utc(parse_time(stamp(x))) for x in expected]
    assert all(x["available"] for x in result.slots)
    assert len(result.slots) <= 2
    assert len({x["direction"] for x in result.slots}) == len(result.slots)


def test_nodes_can_be_on_previous_and_following_day(repo, catalog, job):
    nodes = ["2026-11-02T23:00:00+08:00", "2026-11-04T09:00:00+08:00"]
    ctx = prepare(repo, catalog, job, nodes, [ORIGINAL])
    snapshot = repo.read_material_basic(ctx.material_id)
    result = repo.query_occupancy(job.request_id, ctx.item, snapshot, "alternatives")
    assert [x["at"] for x in result.slots] == [utc(parse_time(x)) for x in nodes]
    _, output = run(repo, job)
    assert output.draft.save_status == "saved"
    assert output.confirmation_status == "pending"
    assert output.original_requested_at == ORIGINAL


def ready(ctx, query="nearest"):
    for name, args in (
        ("read_material", {"detail": "basic"}),
        ("check_rules", {}),
        ("query_slots", {"query": "requested"}),
    ):
        assert execute(name, args, ctx).ok
    if query == "nearest":
        ctx.consumed.update(ctx.observations)
        assert execute("query_slots", {"query": "nearest"}, ctx).ok


def test_nearest_requires_observed_original_conflict(repo, catalog, job):
    ctx = prepare(repo, catalog, job, ["2026-11-03T19:00:00+08:00"], [])
    assert execute("read_material", {"detail": "basic"}, ctx).ok
    assert execute("query_slots", {"query": "nearest"}, ctx).code == "PRECONDITION_FAILED"
    assert execute("query_slots", {"query": "requested"}, ctx).ok
    ctx.consumed.update(ctx.observations)
    assert execute("query_slots", {"query": "nearest"}, ctx).code == "PRECONDITION_FAILED"


def test_save_requires_delivered_evidence_and_hides_internal_references(repo, catalog, job):
    ctx = prepare(repo, catalog, job, ["2026-11-03T19:00:00+08:00"], [ORIGINAL])
    ready(ctx)
    args = {"suggested_at": "2026-11-03T19:00:00+08:00"}
    assert execute("save_draft", args, ctx).code == "PRECONDITION_FAILED"
    ctx.consumed.update(ctx.observations)
    saved = execute("save_draft", args, ctx)
    assert saved.ok
    view = tool_view(saved, "Asia/Shanghai")
    assert view["data"]["confirmation_status"] == "pending"
    assert saved.data["draft_id"] not in json.dumps(view)
    payload = repo.inspect_draft(saved.data["draft_id"])["payload"]
    assert payload["slot_observations"]
    assert payload["evidence_ids"]


@pytest.mark.parametrize(
    "key",
    [
        "material_id",
        "expected_revision",
        "review_observation_id",
        "slot_observation_id",
        "confirmation_status",
    ],
)
def test_model_cannot_supply_local_binding_fields(repo, job, key):
    ctx = context(repo, job)
    assert (
        execute("save_draft", {"suggested_at": ORIGINAL, key: "forged"}, ctx).code
        == "INVALID_ARGUMENTS"
    )
    assert repo.current_draft(job.request_id, ctx.material_id) is None


def test_save_only_uses_latest_returned_nearest_options(repo, catalog, job):
    nodes = ["2026-11-03T19:00:00+08:00", "2026-11-03T20:00:00+08:00"]
    ctx = prepare(repo, catalog, job, nodes, [ORIGINAL])
    ready(ctx)
    ctx.consumed.update(ctx.observations)
    with repo.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO schedule VALUES (?,?,?,?,?,?)",
            (
                "OTHER",
                "douyin",
                "brand_main",
                utc(parse_time(nodes[0])),
                "EXTERNAL",
                ctx.snapshot.import_id,
            ),
        )
    assert execute("query_slots", {"query": "nearest"}, ctx).ok
    ctx.consumed.update(ctx.observations)
    rejected = execute("save_draft", {"suggested_at": nodes[0]}, ctx)
    assert rejected.code == "PRECONDITION_FAILED"
    assert repo.current_draft(job.request_id, "M01") is None


def test_old_receipt_cannot_finish_a_new_revision(repo, catalog, job):
    ctx = prepare(repo, catalog, job, ["2026-11-03T19:00:00+08:00"], [])
    ready(ctx, "requested")
    ctx.consumed.update(ctx.observations)
    saved = execute("save_draft", {"suggested_at": ORIGINAL}, ctx)
    assert saved.ok
    ctx.consumed.update(ctx.observations)
    slot = repo.query_occupancy(job.request_id, ctx.item, ctx.snapshot, "alternatives")
    repo.save_draft(
        job,
        ctx.item,
        ctx.snapshot,
        ctx.review,
        slot,
        slot.slots[0]["at"],
        1,
        evidence_ids=[],
        run_id="CONCURRENT",
    )
    with pytest.raises(BusinessError, match="当前方案已变化"):
        validate_finish(ctx, FinalProposal(action="needs_input", reason_summary="结束"))
    assert assemble_result(ctx).task_status == "incomplete"


def test_earlier_nearest_recommendation_still_requires_confirmation(repo, catalog, job):
    prepare(
        repo, catalog, job, ["2026-11-03T17:30:00+08:00", "2026-11-03T20:00:00+08:00"], [ORIGINAL]
    )
    _, result = run(repo, job)
    assert result.suggested_at == "2026-11-03T17:30:00+08:00"
    assert result.original_requested_at == ORIGINAL
    assert result.confirmation_status == "pending"
    assert result.draft.save_status == "saved"
    assert len(repo.list_drafts()) == 1


def test_concurrent_revision_recovers_without_model_guessing_ids(repo, job, tmp_path):
    class ConcurrentSave(MockModel):
        changed = False

        async def next_action(self, messages, schemas):
            turn = await super().next_action(messages, schemas)
            if (
                turn.calls
                and turn.calls[0]["function"]["name"] == "save_draft"
                and not self.changed
            ):
                self.changed = True
                snapshot = repo.read_supplement(repo.read_material_basic("M01"))
                review = repo.read_rule_snapshot(snapshot)
                slot = repo.query_occupancy(job.request_id, job.items[0], snapshot, "alternatives")
                repo.save_draft(
                    job,
                    job.items[0],
                    snapshot,
                    review,
                    slot,
                    slot.slots[0]["at"],
                    0,
                    evidence_ids=[],
                    run_id="CONCURRENT",
                )
            return turn

    result = asyncio.run(
        run_request(job, repo, ConcurrentSave(), mode="mock", output=tmp_path / "concurrent")
    )
    assert result.items[0].draft.save_status == "reused"
    assert result.items[0].draft.revision == 1
    assert len(repo.list_drafts()) == 1
    assert result.task_status == "needs_input"


def test_wire_business_messages_and_schemas_have_no_internal_identity(repo, job, tmp_path):
    requests = []
    forbidden = {
        "material_id",
        "request_id",
        "run_id",
        "draft_id",
        "revision",
        "expected_revision",
        "observation_id",
        "evidence_ids",
        "source_id",
        "rule_id",
        "import_id",
        "catalog_revision",
    }

    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        check(body["tools"])
        for message in body["messages"]:
            if message["role"] in ("user", "tool"):
                check(json.loads(message["content"]))
            if message.get("tool_calls"):
                for call in message["tool_calls"]:
                    check(json.loads(call["function"]["arguments"]))
        encoded = json.dumps(body)
        assert job.request_id not in encoded
        assert '"M01"' not in encoded
        assert "synthetic://" not in encoded
        assert "OBS-" not in encoded
        turn = await MockModel().next_action(body["messages"], body["tools"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": turn.assistant_message,
                        "finish_reason": "tool_calls" if turn.calls else "stop",
                    }
                ]
            },
        )

    async def scenario():
        model = DeepSeekModel(
            ModelSettings("synthetic-test-credential"), transport=httpx.MockTransport(handler)
        )
        try:
            return await run_request(job, repo, model, mode="mock", output=tmp_path / "wire")
        finally:
            await model.aclose()

    output = asyncio.run(scenario())
    assert output.items[0].draft.save_status == "saved"
    assert output.request_id == job.request_id
    assert output.items[0].material_id == "M01"
    assert len(requests) == 8
    check(schemas())
