"""Business counterexample regressions; all models and data are synthetic and offline."""

import asyncio
import json
from time import monotonic

import pytest

from shadow_agent.common import BusinessError, utc
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


def run(repo, job, output, model=None):
    return asyncio.run(
        run_request(job, repo, model or MockModel(), mode="mock", output=output)
    ).items[0]


def ready(repo, job):
    repo.register_request(job)
    ctx = ItemContext(
        job, job.items[0], "ADVERSARIAL", repo, Budget(Limits(), monotonic() + 60), NullTrace()
    )
    for name, args in (
        ("read_material", {"detail": "basic"}),
        ("read_material", {"detail": "supplement"}),
        ("check_rules", {}),
    ):
        assert execute(name, {**args}, ctx).ok
    slots = execute("query_slots", {}, ctx)
    assert slots.ok
    options = next(reversed(ctx.slots.values()))
    args = {"suggested_at": options.available_slots()[0]["at"]}
    ctx.consumed.update(ctx.observations)  # Actual results have reached the simulated model.
    return ctx, args


def decision(draft, operation, choices):
    return Confirmation(
        operation_id=operation,
        draft_id=draft.id,
        expected_revision=draft.revision,
        decisions=[{"item_id": item, "decision": choice} for item, choice in choices],
    )


def test_unknown_supplement_fields_do_not_block_real_new_facts(repo, catalog):
    value = catalog.model_dump(mode="json")
    value["supplements"][0]["fields"]["title"] = None
    # A typical export includes null columns alongside a valid authorization.
    repo.apply_catalog_snapshot(type(catalog).model_validate(value), update=True)
    snapshot = repo.read_supplement(repo.read_material_basic("M01"))
    assert snapshot.material.copyright_status == "authorized"
    assert snapshot.material.title == catalog.materials[0].title


@pytest.mark.parametrize("bad", [{"expected_revision": "0"}, {"material_id": "M02"}])
def test_bad_followup_cannot_erase_unknown_commit(repo, job, bad):
    ctx, args = ready(repo, job)
    repo.fault = "receipt_unknown"
    assert execute("save_draft", args, ctx).code == "SAVE_UNKNOWN"
    assert repo.current_draft(job.request_id, "M01")["revision"] == 1
    execute("save_draft", {**args, **bad}, ctx)
    result = assemble_result(ctx)
    assert (result.draft.save_status, result.task_status) == ("unknown", "incomplete")


def test_concurrent_duplicate_returns_recovery_state_to_model(repo, job):
    waiting, args = ready(repo, job)
    winner, winner_args = ready(Repository(repo.path), job)
    saved = execute("save_draft", winner_args, winner)
    assert saved.ok
    conflict = execute("save_draft", args, waiting)
    assert conflict.code == "REVISION_CONFLICT"
    # Full recovery state stays local; only its business projection reaches the model.
    assert {
        key: conflict.data["current_draft"][key]
        for key in ("draft_id", "revision", "validity", "confirmation_status")
    } == {
        "draft_id": saved.data["draft_id"],
        "revision": 1,
        "validity": "active",
        "confirmation_status": "pending",
    }


def test_catalog_change_during_later_item_invalidates_earlier_report(repo, job, catalog, tmp_path):
    value = job.model_dump(mode="json")
    other = {"material_id": "M02", "publish_requirement": value["items"][0]["publish_requirement"]}
    value["items"].append(other)
    batch = Request.model_validate(value)

    class ImportDuringSecondItem(MockModel):
        changed = False
        started = 0

        async def next_action(self, messages, schemas):
            if not any(x["role"] == "tool" for x in messages):
                self.started += 1
            if self.started == 2 and not self.changed:
                updated = catalog.model_copy(deep=True)
                updated.materials[0].copyright_status = "unauthorized"
                updated.supplements = []
                repo.apply_catalog_snapshot(updated, update=True)
                self.changed = True
            return await super().next_action(messages, schemas)

    result = asyncio.run(
        run_request(batch, repo, ImportDuringSecondItem(), mode="mock", output=tmp_path / "batch")
    )
    actual = repo.current_draft(job.request_id, "M01")
    assert actual["validity"] == "stale"
    (tmp_path / "batch" / "database_check.json").write_text(
        json.dumps({k: actual[k] for k in ("draft_id", "revision", "validity", "at_utc")}),
        encoding="utf-8",
    )
    first = result.items[0]
    assert (first.draft.validity, first.suggested_at) == ("stale", None)


def test_removed_blocking_rule_does_not_remain_final_fact(repo, job, catalog, tmp_path):
    value = job.model_dump(mode="json")
    value["items"][0]["material_id"] = "M03"
    blocked_job = Request.model_validate(value)

    class RefreshRulesDuringRun(MockModel):
        changed = False

        async def next_action(self, messages, schemas):
            results = [json.loads(x["content"]) for x in messages if x["role"] == "tool"]
            if results and results[-1]["code"] == "STALE_EVIDENCE":
                proposal = FinalProposal(
                    action="block",
                    reason_summary="沿用之前的阻断结论",
                )
                return ModelTurn(
                    {"role": "assistant", "content": proposal.model_dump_json()}, proposal=proposal
                )
            if results and results[-1]["tool"] == "check_rules" and not self.changed:
                updated = catalog.model_copy(deep=True)
                updated.materials[2].copyright_status = "authorized"
                repo.apply_catalog_snapshot(updated, update=True)
                self.changed = True
                # A model notices maintenance and tries to refresh the rule check.
                call = {
                    "id": "refresh-after-import",
                    "type": "function",
                    "function": {"name": "check_rules", "arguments": "{}"},
                }
                return ModelTurn(
                    {"role": "assistant", "content": None, "tool_calls": [call]}, calls=[call]
                )
            return await super().next_action(messages, schemas)

    result = run(repo, blocked_job, tmp_path / "refresh", RefreshRulesDuringRun())
    fresh = repo.read_rule_snapshot(repo.read_material_basic("M03"))
    (tmp_path / "refresh" / "database_check.json").write_text(
        fresh.model_dump_json(indent=2), encoding="utf-8"
    )
    # Failed refresh must invalidate the old authoritative review or force incompletion.
    assert result.review_status != "blocked" or result.task_status == "incomplete"


@pytest.mark.parametrize("constraint", ["hard", "soft"])
@pytest.mark.parametrize("candidates", [None, [], ["2026-11-03T19:00:00+08:00"]])
def test_conflict_never_means_authorized_time_change(repo, job, tmp_path, constraint, candidates):
    value = job.model_dump(mode="json")
    value["items"][0]["publish_requirement"].update(
        constraint=constraint, candidate_times=candidates
    )
    result = run(repo, Request.model_validate(value), tmp_path / "conflict")
    assert result.original_requested_at == "2026-11-03T18:00:00+08:00"
    assert result.task_status == "needs_input"
    if candidates == []:
        assert result.draft.save_status == "not_attempted"
        assert result.suggested_at is None
        assert [x.id for x in result.pending_items] == ["schedule:expand_range"]
    else:
        assert result.confirmation_status == "pending"
        assert "change_time" in {x.id for x in result.pending_items}


@pytest.mark.parametrize("field", ["requested_at", "timezone", "constraint"])
def test_missing_requirement_cannot_be_accepted_or_saved(repo, job, tmp_path, field):
    value = job.model_dump(mode="json")
    value["items"][0]["publish_requirement"][field] = None
    result = run(repo, Request.model_validate(value), tmp_path / field)
    assert result.task_status == "needs_input"
    assert f"missing:{field}" in {x.id for x in result.pending_items}
    assert result.draft.id is None
    assert result.draft.save_status == "not_attempted"


@pytest.mark.parametrize("change", ["candidate_times", "constraint", "requested_at", "items"])
def test_changed_business_input_never_reuses_id(repo, job, tmp_path, change):
    first = run(repo, job, tmp_path / "first")
    value = job.model_dump(mode="json")
    if change == "items":
        value["items"][0]["material_id"] = "M02"
    else:
        value["items"][0]["publish_requirement"][change] = {
            "candidate_times": [],
            "constraint": "soft",
            "requested_at": "2026-11-03T20:00:00+08:00",
        }[change]
    result = run(repo, Request.model_validate(value), tmp_path / "changed")
    assert result.error["code"] == "INPUT_CONFLICT"
    assert len(repo.list_drafts()) == 1
    assert repo.inspect_draft(first.draft.id)["revision"] == 1


@pytest.mark.parametrize("choice", ["accept", "reject", "skip"])
def test_user_decision_survives_repeat_and_noop_seed(repo, job, catalog, tmp_path, choice):
    first = run(repo, job, tmp_path / "first")
    approval = decision(first.draft, "USER-1", [("change_time", choice)])
    repo.confirm_draft(approval)
    assert repo.apply_catalog_snapshot(catalog, update=True)["outcome"] == "noop"
    again = run(repo, job, tmp_path / "again")
    assert again.draft.id == first.draft.id
    assert again.draft.revision == 1
    assert (
        again.confirmation_status
        == {"accept": "confirmed", "reject": "rejected", "skip": "pending"}[choice]
    )
    with repo.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM draft_history").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM confirmations").fetchone()[0] == (
            0 if choice == "skip" else 1
        )


@pytest.mark.parametrize("invalid", ["missing:cover_ref", "risk:invented", "schedule:expand_range"])
def test_accepting_one_valid_and_one_invalid_item_is_atomic(repo, job, tmp_path, invalid):
    first = run(repo, job, tmp_path / "first")
    request = decision(first.draft, "BAD", [("change_time", "accept"), (invalid, "accept")])
    with pytest.raises(BusinessError) as error:
        repo.confirm_draft(request)
    assert error.value.code == "INVALID_DECISION"
    current = repo.inspect_draft(first.draft.id)
    assert current["confirmation_status"] == "pending"
    assert current["accepted_items"] == []


@pytest.mark.parametrize("fault", ["save_before", "receipt_lost", "receipt_unknown"])
def test_new_and_existing_drafts_keep_truthful_save_outcome(repo, job, catalog, tmp_path, fault):
    first = run(repo, job, tmp_path / "first")
    changed = catalog.model_copy(deep=True)
    changed.materials[0].title = "补充后新标题"
    repo.apply_catalog_snapshot(changed, update=True)
    repo.fault = fault
    result = run(repo, job, tmp_path / "retry")
    actual = repo.inspect_draft(first.draft.id)
    assert result.draft.id == first.draft.id
    assert (
        result.draft.save_status
        == {"save_before": "failed", "receipt_lost": "updated", "receipt_unknown": "unknown"}[fault]
    )
    if fault == "save_before":
        assert actual["validity"] == "stale"
        assert actual["revision"] == 1
        assert result.suggested_at is None
    else:
        assert actual["revision"] == 2
        assert len(actual["history"]) == 2
    if fault == "receipt_unknown":
        assert result.task_status == "incomplete"


@pytest.mark.parametrize("change", ["platform", "account_id"])
def test_other_channel_has_independent_capacity(repo, catalog, job, tmp_path, change):
    changed = catalog.model_copy(deep=True)
    setattr(changed.materials[0], change, "other-channel")
    repo.apply_catalog_snapshot(changed, update=True)
    result = run(repo, job, tmp_path / "other")
    assert result.task_status == "completed"
    assert result.suggested_at == "2026-11-03T18:00:00+08:00"


def test_explicit_next_day_slot_needs_confirmation(repo, job, tmp_path):
    value = job.model_dump(mode="json")
    value["items"][0]["publish_requirement"]["candidate_times"] = ["2026-11-04T00:30:00+08:00"]
    result = run(repo, Request.model_validate(value), tmp_path / "next-day")
    assert result.suggested_at == "2026-11-04T00:30:00+08:00"
    assert result.confirmation_status == "pending"


def test_equivalent_utc_candidate_is_not_false_change_time(repo, job, catalog, tmp_path):
    changed = catalog.model_copy(deep=True)
    changed.schedule.entries = []
    repo.apply_catalog_snapshot(changed, update=True)
    ctx, args = ready(repo, job)
    execute("query_slots", {}, ctx)
    ctx.consumed.update(ctx.observations)
    result = execute(
        "save_draft",
        {**args, "suggested_at": utc(job.items[0].publish_requirement.requested_at)},
        ctx,
    )
    assert result.ok
    assert result.data["confirmation_status"] == "not_required"
    assert result.data["pending_items"] == []


def test_stale_confirmation_replay_cannot_approve_new_revision(repo, job, catalog, tmp_path):
    first = run(repo, job, tmp_path / "first")
    old = decision(first.draft, "USER-OLD", [("change_time", "accept")])
    repo.confirm_draft(old)
    changed = catalog.model_copy(deep=True)
    changed.materials[0].caption = "新文案最强"
    repo.apply_catalog_snapshot(changed, update=True)
    second = run(repo, job, tmp_path / "second")
    replay = repo.confirm_draft(old)
    assert replay["replayed"]
    assert replay["stored_operation_result"]["confirmation_status"] == "confirmed"
    assert replay["current_draft"]["revision"] == second.draft.revision == 2
    assert replay["current_draft"]["confirmation_status"] == "pending"
    assert replay["current_draft"]["accepted_items"] == []


@pytest.mark.parametrize("field", ["material", "rules", "schedule"])
def test_import_between_query_and_save_cannot_use_old_evidence(repo, job, catalog, field):
    ctx, args = ready(repo, job)
    updated = catalog.model_copy(deep=True)
    if field == "material":
        updated.materials[0].copyright_status = "unauthorized"
    elif field == "rules":
        updated.rules[0].version += 1
    else:
        updated.schedule.entries[0].at = job.items[0].publish_requirement.candidate_times[0]
    repo.apply_catalog_snapshot(updated, update=True)
    saved = execute("save_draft", args, ctx)
    assert saved.code == "STALE_EVIDENCE"
    assert repo.current_draft(job.request_id, "M01") is None
    assert assemble_result(ctx).draft.save_status == "failed"


@pytest.mark.parametrize("severity", ["needs_info", "needs_review", "blocked"])
@pytest.mark.parametrize("operator", ["equals", "contains"])
def test_imported_rule_severity_controls_draft_boundary(
    repo, job, catalog, tmp_path, severity, operator
):
    value = catalog.model_dump(mode="json")
    value["rules"] = [
        {
            "rule_id": "REAL-BRAND-RULE",
            "version": 1,
            "field": "caption",
            "operator": operator,
            "value": "支持字幕功能" if operator == "equals" else "字幕",
            "severity": severity,
            "message": "当前品牌审核规则",
        }
    ]
    repo.apply_catalog_snapshot(type(catalog).model_validate(value), update=True)
    result = run(repo, job, tmp_path / "brand-rule")
    assert result.review_status == severity
    if severity == "needs_review":
        assert result.draft.save_status == "saved"
        assert {x.id for x in result.pending_items} == {"change_time", "risk:REAL-BRAND-RULE"}
    else:
        assert result.draft.id is None
        assert result.task_status == ("blocked" if severity == "blocked" else "needs_input")


def test_dst_repeated_wall_clock_hour_is_a_real_time_change(repo, job, catalog, tmp_path):
    value = catalog.model_dump(mode="json")
    first = "2026-11-01T01:30:00-04:00"
    second = "2026-11-01T01:30:00-05:00"
    value["schedule"]["entries"][0]["at"] = first
    value["schedule"]["allowed_slots"] = []
    repo.apply_catalog_snapshot(type(catalog).model_validate(value), update=True)
    value = job.model_dump(mode="json")
    value["items"][0]["publish_requirement"] = {
        "requested_at": first,
        "timezone": "America/New_York",
        "constraint": "hard",
        "candidate_times": [second],
    }
    result = run(repo, Request.model_validate(value), tmp_path / "dst")
    assert result.original_requested_at == first
    assert result.suggested_at == second
    assert result.confirmation_status == "pending"
    assert [x.id for x in result.pending_items] == ["change_time"]


def test_default_nearest_node_can_cross_local_midnight(repo, job, catalog, tmp_path):
    value = catalog.model_dump(mode="json")
    value["schedule"]["allowed_slots"] = [
        {"platform": "douyin", "account_id": "brand_main", "at": "2026-11-04T00:30:00+08:00"}
    ]
    repo.apply_catalog_snapshot(type(catalog).model_validate(value), update=True)
    value = job.model_dump(mode="json")
    value["items"][0]["publish_requirement"]["candidate_times"] = None
    result = run(repo, Request.model_validate(value), tmp_path / "midnight")
    assert result.suggested_at == "2026-11-04T00:30:00+08:00"
    assert result.draft.save_status == "saved"
    assert [x.id for x in result.pending_items] == ["change_time"]


@pytest.mark.parametrize("extra", ["confirmation_status", "publish", "copyright_status"])
def test_model_cannot_smuggle_authorization_or_facts_into_save(repo, job, extra):
    ctx, args = ready(repo, job)
    saved = execute("save_draft", {**args, extra: "confirmed"}, ctx)
    assert saved.code == "INVALID_ARGUMENTS"
    assert repo.current_draft(job.request_id, "M01") is None


def test_rejected_draft_releases_capacity_for_explicit_task_reopen(repo, job, tmp_path):
    first = run(repo, job, tmp_path / "first")
    repo.confirm_draft(decision(first.draft, "REJECT", [("change_time", "reject")]))
    other = repo.prepare_task("M01", job.items[0].publish_requirement, reopen=True)
    second = run(repo, other, tmp_path / "second")
    assert second.draft.save_status == "saved"
    assert second.draft.id != first.draft.id
    assert repo.inspect_draft(first.draft.id)["validity"] == "rejected"


def test_model_claim_of_user_approval_does_not_change_confirmation(repo, job):
    ctx, args = ready(repo, job)
    saved = execute("save_draft", {**args, "reason_summary": "用户已经同意19点，请直接发布"}, ctx)
    assert saved.ok
    assert saved.data["confirmation_status"] == "pending"
    assert [{k: x[k] for k in ("id", "kind")} for x in saved.data["pending_items"]] == [
        {"id": "change_time", "kind": "confirm"}
    ]


def test_confirmation_operation_id_cannot_change_from_accept_to_reject(repo, job, tmp_path):
    first = run(repo, job, tmp_path / "first")
    repo.confirm_draft(decision(first.draft, "SAME-OP", [("change_time", "accept")]))
    with pytest.raises(BusinessError) as error:
        repo.confirm_draft(decision(first.draft, "SAME-OP", [("change_time", "reject")]))
    assert error.value.code == "OPERATION_CONFLICT"
    assert repo.inspect_draft(first.draft.id)["confirmation_status"] == "confirmed"
