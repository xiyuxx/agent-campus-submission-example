"""Business outcomes and authorization boundaries for model-driven decisions."""

import json

import pytest
from test_agent import context, run

from shadow_agent.common import BusinessError, utc
from shadow_agent.contracts import Slot, parse_time
from shadow_agent.model_view import tool_view
from shadow_agent.models.mock import MockModel
from shadow_agent.policy import check_rules
from shadow_agent.tools.registry import execute, schemas


def names(ctx):
    return {x["function"]["name"] for x in schemas(ctx)}


def test_optional_missing_is_not_a_blocking_requirement(repo, job, catalog):
    catalog.materials[0].copyright_status = "authorized"
    repo.apply_catalog_snapshot(catalog, update=True)
    ctx = context(repo, job)
    assert execute("read_material", {"detail": "basic"}, ctx).ok
    result = execute("check_rules", {}, ctx)
    data = tool_view(result, "Asia/Shanghai")["data"]
    assert data["review_status"] == "passed"
    assert data["blocking_missing_fields"] == []
    assert "license_ref" in data["other_missing_fields"]
    assert data["supplement_available"] and not data["supplemented"]


def test_blocking_field_and_available_source_are_explicit(repo, job):
    ctx = context(repo, job)
    execute("read_material", {"detail": "basic"}, ctx)
    result = execute("check_rules", {}, ctx)
    assert tool_view(result, None)["data"]["blocking_missing_fields"] == ["copyright_status"]
    execute("read_material", {"detail": "supplement"}, ctx)
    result = execute("check_rules", {}, ctx)
    assert result.data["blocking_missing_fields"] == []
    assert result.data["supplemented"]


def test_offered_actions_do_not_authorize_undelivered_results(repo, job):
    ctx = context(repo, job)
    assert names(ctx) == {"read_material"}
    execute("read_material", {"detail": "basic"}, ctx)
    execute("read_material", {"detail": "supplement"}, ctx)
    execute("check_rules", {}, ctx)
    assert "query_slots" in names(ctx) and "save_draft" not in names(ctx)
    # The next request may offer query_slots, but no response has consumed review yet.
    assert execute("query_slots", {}, ctx, allowed_tools=names(ctx)).code == "ACTION_NOT_AVAILABLE"
    ctx.consumed.update(ctx.observations)
    assert execute("query_slots", {}, ctx, allowed_tools=names(ctx)).ok
    assert "save_draft" in names(ctx)
    saved = execute(
        "save_draft",
        {"suggested_at": "2026-11-03T19:00:00+08:00"},
        ctx,
        allowed_tools=names(ctx),
    )
    assert saved.code == "ACTION_NOT_AVAILABLE"
    assert repo.current_draft(job.request_id, "M01") is None


def test_blocked_and_unknown_do_not_offer_scheduling(repo, job, catalog):
    catalog.materials[0].copyright_status = "unauthorized"
    repo.apply_catalog_snapshot(catalog, update=True)
    ctx = context(repo, job)
    execute("read_material", {"detail": "basic"}, ctx)
    execute("check_rules", {}, ctx)
    ctx.consumed.update(ctx.observations)
    assert not {"query_slots", "save_draft"} & names(ctx)
    ctx.save_status = "unknown"
    assert names(ctx) == set()


def test_default_preference_keeps_legacy_canonical_input(repo, job):
    legacy = job.canonical()
    assert "reschedule_preference" not in legacy["items"][0]["publish_requirement"]
    repo.register_request(job)
    same = job.model_copy(deep=True)
    same.items[0].publish_requirement.reschedule_preference = "nearest"
    assert repo.register_request(same).canonical() == legacy
    same.items[0].publish_requirement.reschedule_preference = "prefer_before"
    with pytest.raises(BusinessError) as error:
        repo.register_request(same)
    assert error.value.code == "INPUT_CONFLICT"


def schedule(repo, catalog, job, *, original_free=False):
    catalog.materials[0].copyright_status = "authorized"
    catalog.schedule.allowed_slots = [
        Slot(platform="douyin", account_id="brand_main", at=parse_time(t))
        for t in (
            "2026-11-03T17:00:00+08:00",
            "2026-11-03T19:00:00+08:00",
            "2026-11-03T20:00:00+08:00",
            "2026-11-03T21:00:00+08:00",
        )
    ]
    if original_free:
        catalog.schedule.entries = []
    repo.apply_catalog_snapshot(catalog, update=True)
    job.items[0].publish_requirement.candidate_times = None


@pytest.mark.parametrize(
    "preference,expected",
    [
        ("nearest", "19:00"),
        ("prefer_before", "17:00"),
        ("prefer_after", "19:00"),
    ],
)
def test_preference_changes_recommendation_but_still_requires_confirmation(
    repo,
    job,
    catalog,
    preference,
    expected,
):
    schedule(repo, catalog, job)
    job.items[0].publish_requirement.reschedule_preference = preference
    _, result = run(repo, job)
    assert expected in result.suggested_at
    assert result.confirmation_status == "pending"
    assert result.constraint == "hard" and result.original_requested_at.endswith("18:00:00+08:00")
    assert len(repo.list_drafts()) == 1
    _, repeated = run(repo, job)
    assert repeated.draft.save_status == "reused"
    assert repeated.draft.id == result.draft.id and repeated.draft.revision == result.draft.revision


def test_preference_never_bypasses_free_original(repo, job, catalog):
    schedule(repo, catalog, job, original_free=True)
    job.items[0].publish_requirement.reschedule_preference = "prefer_before"
    _, result = run(repo, job)
    assert result.suggested_at.endswith("18:00:00+08:00")
    assert result.confirmation_status == "not_required"


class OccupyBeforeSave(MockModel):
    def __init__(self, repo, *, every_time=False):
        self.repo, self.every_time, self.saves = repo, every_time, 0

    async def next_action(self, messages, schemas):
        turn = await super().next_action(messages, schemas)
        for call in turn.calls:
            if call["function"]["name"] == "save_draft":
                self.saves += 1
                if self.saves == 1 or self.every_time:
                    at = json.loads(call["function"]["arguments"])["suggested_at"]
                    with self.repo.transaction(write=True) as connection:
                        connection.execute(
                            "INSERT INTO schedule VALUES (?,?,?,?,?,?)",
                            (
                                f"RACE-{self.saves}",
                                "douyin",
                                "brand_main",
                                utc(parse_time(at)),
                                "OTHER",
                                self.repo.read_material_basic("M01").import_id,
                            ),
                        )
        return turn


def test_model_consumes_save_conflict_and_saves_a_fresh_candidate(repo, job, catalog):
    schedule(repo, catalog, job)
    job.items[0].publish_requirement.reschedule_preference = "prefer_after"
    ctx, result = run(repo, job, OccupyBeforeSave(repo))
    assert result.suggested_at.endswith("20:00:00+08:00")
    assert result.draft.save_status == "saved" and result.confirmation_status == "pending"
    assert ctx.schedule_requeries == 1 and not ctx.schedule_refresh_required
    conflict = next(x for x in ctx.observations.values() if x.tool == "save_draft" and not x.ok)
    assert conflict.observation_id in ctx.consumed
    assert tool_view(conflict, None)["data"]["recovery_action"] == "query_slots"
    assert len(repo.list_drafts()) == 1


def test_persistent_races_end_incomplete_with_no_draft(repo, job, catalog):
    schedule(repo, catalog, job)
    job.items[0].publish_requirement.reschedule_preference = "prefer_after"
    ctx, result = run(repo, job, OccupyBeforeSave(repo, every_time=True))
    assert ctx.schedule_requeries == 2
    assert result.task_status == "incomplete" and result.error["code"] == "RECOVERY_LIMIT"
    assert repo.list_drafts() == []


def test_conflict_invalidates_old_query_even_if_time_becomes_free_again(repo, job, catalog):
    schedule(repo, catalog, job)
    ctx = context(repo, job)
    for name, args in (
        ("read_material", {"detail": "basic"}),
        ("check_rules", {}),
        ("query_slots", {}),
    ):
        assert execute(name, args, ctx).ok
    ctx.consumed.update(ctx.observations)
    args = {"suggested_at": "2026-11-03T19:00:00+08:00"}
    with repo.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO schedule VALUES (?,?,?,?,?,?)",
            (
                "TAKEN",
                "douyin",
                "brand_main",
                utc(parse_time(args["suggested_at"])),
                "OTHER",
                ctx.snapshot.import_id,
            ),
        )
    assert execute("save_draft", args, ctx).code == "SLOT_CONFLICT"
    assert "save_draft" not in names(ctx)
    with repo.transaction(write=True) as connection:
        connection.execute("DELETE FROM schedule WHERE entry_id='TAKEN'")
    assert execute("save_draft", args, ctx).code == "PRECONDITION_FAILED"
    assert repo.list_drafts() == []


def test_auditor_runs_outside_read_transaction_and_is_bound_to_input(repo):
    class RecordingAudit:
        def evaluate(self, snapshot, rules):
            # A catalog update can commit here; an enclosing read transaction would block commit.
            with repo.transaction(write=True) as connection:
                connection.execute("UPDATE drafts SET updated_at=updated_at")
            return check_rules(snapshot, rules)

    repo.auditor = RecordingAudit()
    snapshot = repo.read_material_basic("M01")
    assert repo.read_rule_snapshot(snapshot).material_sha256 == snapshot.facts_hash()

    class WrongAudit:
        def evaluate(self, snapshot, rules):
            report = check_rules(snapshot, rules)
            report.material_sha256 = "wrong-material"
            return report

    repo.auditor = WrongAudit()
    with pytest.raises(BusinessError, match="不匹配"):
        repo.read_rule_snapshot(snapshot)
