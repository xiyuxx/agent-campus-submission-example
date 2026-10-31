"""One deterministic lookup supplies options; the model still chooses and saves a draft."""

import asyncio
import json

import pytest
from test_agent import context
from test_local_binding_nearest import ORIGINAL, prepare, ready

from shadow_agent.agent import validate_finish
from shadow_agent.common import BusinessError, utc
from shadow_agent.contracts import FinalProposal, SlotArgs, parse_time
from shadow_agent.model_view import tool_view
from shadow_agent.models.mock import MockModel
from shadow_agent.result import assemble_result
from shadow_agent.service import run_request
from shadow_agent.tools.registry import execute, schemas


def test_free_original_does_not_scan_alternatives(repo, catalog, job, monkeypatch):
    ctx = prepare(repo, catalog, job, ["2026-11-03T19:00:00+08:00"], [])
    assert execute("read_material", {"detail": "basic"}, ctx).ok

    def forbidden_scan(*args):
        raise AssertionError("free original must not scan alternative nodes")

    monkeypatch.setattr(repo, "_candidate_times", forbidden_scan)
    result = execute("query_slots", {}, ctx)
    view = tool_view(result, "Asia/Shanghai")
    assert result.ok and result.code == "SLOTS_QUERIED"
    assert view["data"]["requested_slot"]["at"] == ORIGINAL
    assert view["data"]["requested_slot"]["available"]
    assert not view["data"]["alternatives_searched"]
    assert view["data"]["alternatives"] == []
    assert len(ctx.slots) == 1
    assert repo.current_draft(job.request_id, "M01") is None


def test_conflict_returns_both_directions_in_one_observation(repo, catalog, job):
    nodes = ["2026-11-03T17:00:00+08:00", "2026-11-03T19:00:00+08:00"]
    ctx = prepare(repo, catalog, job, nodes, [ORIGINAL])
    assert execute("read_material", {"detail": "basic"}, ctx).ok
    result = execute("query_slots", {}, ctx)
    view = tool_view(result, "Asia/Shanghai")
    assert result.ok and result.code == "SLOT_CONFLICT"
    assert not view["data"]["requested_slot"]["available"]
    assert view["data"]["alternatives_searched"]
    assert [x["at"] for x in view["data"]["alternatives"]] == nodes
    assert [x["direction"] for x in view["data"]["alternatives"]] == ["before", "after"]
    assert len(ctx.slots) == 1
    assert repo.current_draft(job.request_id, "M01") is None


def test_original_and_alternatives_share_one_sqlite_snapshot(repo, job, monkeypatch):
    snapshot = repo.read_material_basic("M01")
    connections = []
    original = repo._query_occupancy

    def record(connection, *args):
        assert connection.in_transaction
        connections.append(connection)
        return original(connection, *args)

    monkeypatch.setattr(repo, "_query_occupancy", record)
    options = repo.query_schedule_options(job.request_id, job.items[0], snapshot)
    assert len(connections) == 2 and connections[0] is connections[1]
    assert options.requested.catalog_revision == options.alternatives.catalog_revision


@pytest.mark.parametrize("old_query", ["requested", "nearest"])
def test_query_mode_is_no_longer_a_model_parameter(repo, job, old_query):
    assert set(SlotArgs.model_json_schema()["properties"]) == {"reason_summary"}
    schema = next(x["function"] for x in schemas() if x["function"]["name"] == "query_slots")
    assert "query" not in schema["parameters"]["properties"]
    ctx = context(repo, job)
    assert execute("query_slots", {"query": old_query}, ctx).code == "INVALID_ARGUMENTS"
    assert not ctx.slots


def test_partial_lookup_failure_does_not_deliver_a_successful_package(repo, job, monkeypatch):
    ctx = context(repo, job)
    assert execute("read_material", {"detail": "basic"}, ctx).ok
    original = repo._query_occupancy

    def failing(connection, request_id, item, snapshot, query):
        if query == "alternatives":
            raise BusinessError("QUERY_FAILED", "synthetic alternative lookup failure")
        return original(connection, request_id, item, snapshot, query)

    monkeypatch.setattr(repo, "_query_occupancy", failing)
    result = execute("query_slots", {}, ctx)
    assert not result.ok and result.code == "QUERY_FAILED"
    assert not ctx.slots
    assert assemble_result(ctx).draft.save_status == "not_attempted"


def test_new_query_must_be_delivered_even_if_old_query_was_consumed(repo, catalog, job):
    ctx = prepare(repo, catalog, job, ["2026-11-03T19:00:00+08:00"], [ORIGINAL])
    ready(ctx)
    ctx.consumed.update(ctx.observations)
    assert execute("query_slots", {}, ctx).ok
    saved = execute("save_draft", {"suggested_at": "2026-11-03T19:00:00+08:00"}, ctx)
    assert saved.code == "PRECONDITION_FAILED"
    assert repo.current_draft(job.request_id, "M01") is None


def test_free_original_cannot_be_bypassed_by_choosing_an_unreturned_alternative(repo, catalog, job):
    ctx = prepare(repo, catalog, job, ["2026-11-03T19:00:00+08:00"], [])
    ready(ctx)
    ctx.consumed.update(ctx.observations)
    saved = execute("save_draft", {"suggested_at": "2026-11-03T19:00:00+08:00"}, ctx)
    assert saved.code == "PRECONDITION_FAILED"


def test_original_becoming_free_replaces_old_alternative_options(repo, catalog, job):
    ctx = prepare(repo, catalog, job, ["2026-11-03T19:00:00+08:00"], [ORIGINAL])
    ready(ctx)
    ctx.consumed.update(ctx.observations)
    with repo.transaction(write=True) as connection:
        connection.execute("DELETE FROM schedule")
    assert execute("query_slots", {}, ctx).ok
    ctx.consumed.update(ctx.observations)
    assert (
        execute("save_draft", {"suggested_at": "2026-11-03T19:00:00+08:00"}, ctx).code
        == "PRECONDITION_FAILED"
    )
    assert execute("save_draft", {"suggested_at": ORIGINAL}, ctx).ok


def test_candidate_taken_after_lookup_is_rechecked_at_save(repo, catalog, job):
    candidate = "2026-11-03T19:00:00+08:00"
    ctx = prepare(repo, catalog, job, [candidate], [ORIGINAL])
    ready(ctx)
    ctx.consumed.update(ctx.observations)
    with repo.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO schedule VALUES (?,?,?,?,?,?)",
            (
                "NEW",
                "douyin",
                "brand_main",
                utc(parse_time(candidate)),
                "OTHER",
                ctx.snapshot.import_id,
            ),
        )
    result = execute("save_draft", {"suggested_at": candidate}, ctx)
    assert result.code == "SLOT_CONFLICT"
    assert repo.current_draft(job.request_id, "M01") is None


def test_empty_candidates_allow_needs_input_but_not_premature_success(repo, catalog, job):
    ctx = prepare(repo, catalog, job, [], [ORIGINAL])
    ready(ctx)
    validate_finish(ctx, FinalProposal(action="needs_input", reason_summary="没有合法候选"))
    with pytest.raises(BusinessError):
        validate_finish(ctx, FinalProposal(action="finish", reason_summary="伪称完成"))
    result = assemble_result(ctx)
    assert result.task_status == "needs_input"
    assert [x.id for x in result.pending_items] == ["schedule:expand_range"]
    assert result.draft.save_status == "not_attempted"


@pytest.mark.parametrize(
    "scenario",
    ["normal", "conflict", "empty", "insufficient", "query_failure", "save_failure", "duplicate"],
)
def test_automatic_options_service_scenario(repo, catalog, job, tmp_path, scenario):
    updated = catalog.model_copy(deep=True)
    updated.materials[0].copyright_status = "authorized"
    if scenario == "normal":
        updated.schedule.entries = []
    if scenario == "empty":
        updated.schedule.allowed_slots = []
    repo.apply_catalog_snapshot(updated, update=True)
    job.items[0].publish_requirement.candidate_times = None
    if scenario == "insufficient":
        job.items[0].material_id = "M02"
    repo.fault = {"query_failure": "query_failure", "save_failure": "save_before"}.get(scenario)
    output = tmp_path / "automatic-options"
    first = None
    if scenario == "duplicate":
        first = asyncio.run(
            run_request(job, repo, MockModel(), mode="mock", output=output / "first")
        )
        output = output / "repeat"
    result = asyncio.run(run_request(job, repo, MockModel(), mode="mock", output=output))
    item = result.items[0]
    events = [
        json.loads(x) for x in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    queries = [
        x["result"]
        for x in events
        if x["event_type"] == "tool_result" and x["result"]["tool"] == "query_slots"
    ]
    assert len(queries) == (0 if scenario == "insufficient" else 1)
    current = repo.current_draft(job.request_id, job.items[0].material_id)
    if scenario in ("normal", "conflict", "duplicate"):
        assert item.draft.save_status == ("reused" if first else "saved")
        assert item.task_status == ("completed" if scenario == "normal" else "needs_input")
        assert item.suggested_at == (
            ORIGINAL if scenario == "normal" else "2026-11-03T19:00:00+08:00"
        )
        if first:
            assert item.draft.id == first.items[0].draft.id
            assert item.draft.revision == 1
        evidence = repo.inspect_draft(item.draft.id)["payload"]["slot_observations"]
        assert [x["query"] for x in evidence] == (
            ["requested"] if scenario == "normal" else ["requested", "alternatives"]
        )
    elif scenario in ("empty", "insufficient"):
        assert item.task_status == "needs_input" and current is None
    else:
        assert item.task_status == "failed" and current is None
        assert item.error["code"] == (
            "QUERY_FAILED" if scenario == "query_failure" else "SAVE_FAILED"
        )
    with repo.transaction() as connection:
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("drafts", "draft_history")
        }
    assert counts == {"drafts": int(current is not None), "draft_history": int(current is not None)}
    (output / "database_check.json").write_text(
        json.dumps(
            {"scenario": scenario, "counts": counts, "draft": current}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
