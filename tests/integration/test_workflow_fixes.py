import asyncio
import json

import pytest

from shadow_agent.contracts import Confirmation, Request, parse_time
from shadow_agent.models.mock import MockModel
from shadow_agent.service import run_request


def run(repo, job, folder, model=None):
    return asyncio.run(run_request(job, repo, model or MockModel(), mode="mock", output=folder))


def approve(repo, result):
    draft = repo.inspect_draft(result.items[0].draft.id)
    decision = Confirmation(
        operation_id="APPROVE",
        draft_id=draft["draft_id"],
        expected_revision=draft["revision"],
        decisions=[{"item_id": x["id"], "decision": "accept"} for x in draft["pending_items"]],
    )
    repo.confirm_draft(decision)
    return decision


def test_confirmation_replay_refreshes_current_but_preserves_historical_receipt(
    repo, job, tmp_path
):
    first = run(repo, job, tmp_path / "first")
    decision = approve(repo, first)
    repo.business_clock = lambda: "2026-11-03T12:00:00Z"
    replay = repo.confirm_draft(decision)
    assert replay["stored_operation_result"]["confirmation_status"] == "confirmed"
    assert replay["current_draft"]["validity"] == "stale"
    assert replay["current_draft"]["confirmation_status"] == "pending"
    assert len(repo.inspect_draft(decision.draft_id)["events"]) == 3
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) FROM confirmations").fetchone()[0] == 1


def test_approved_future_plan_survives_original_deadline(repo, job, tmp_path):
    first = run(repo, job, tmp_path / "first")
    approve(repo, first)
    repo.business_clock = lambda: "2026-11-03T10:30:00Z"
    repeat = run(repo, job, tmp_path / "repeat")
    assert repeat.task_status == "completed"
    assert repeat.items[0].draft.save_status == "reused"
    assert repeat.items[0].draft.id == first.items[0].draft.id
    assert repeat.items[0].confirmation_status == "confirmed"
    assert repo.inspect_draft(first.items[0].draft.id)["revision"] == 1


def test_candidate_expires_model_requeries_and_new_time_remains_pending(repo, job, tmp_path):
    repo.business_clock = lambda: "2026-11-03T08:59:00Z"
    job.items[0].publish_requirement.reschedule_preference = "prefer_before"
    job.items[0].publish_requirement.candidate_times = [
        parse_time("2026-11-03T17:00:00+08:00"),
        parse_time("2026-11-03T19:00:00+08:00"),
    ]
    original = repo.save_draft

    def expire(*args, **kwargs):
        repo.business_clock = lambda: "2026-11-03T09:00:00Z"
        return original(*args, **kwargs)

    repo.save_draft = expire
    result = run(repo, job, tmp_path / "run")
    assert result.task_status == "needs_input"
    assert result.items[0].suggested_at == "2026-11-03T19:00:00+08:00"
    assert result.items[0].confirmation_status == "pending"
    events = [json.loads(x) for x in (tmp_path / "run/trace.jsonl").read_text("utf-8").splitlines()]
    tools = [e["result"] for e in events if e["event_type"] == "tool_result"]
    assert sum(x["tool"] == "query_slots" for x in tools) == 2
    assert any(x["code"] == "SLOT_EXPIRED" for x in tools)


@pytest.mark.parametrize(
    "at,zone",
    [
        ("9999-12-31T23:59:59-12:00", "Etc/GMT+12"),
        ("0001-01-01T00:00:00+14:00", "Etc/GMT-14"),
    ],
)
def test_extreme_time_is_input_validation_error(job, at, zone):
    raw = job.model_dump(mode="json")
    raw["items"][0]["publish_requirement"].update(requested_at=at, timezone=zone)
    with pytest.raises(ValueError):
        Request.model_validate(raw)
