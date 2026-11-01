import asyncio
import json

import pytest

from shadow_agent.common import BusinessError, utc
from shadow_agent.contracts import Confirmation, parse_time
from shadow_agent.models.mock import MockModel
from shadow_agent.service import run_request
from shadow_agent.storage import Repository


def ready(repo, job):
    repo.register_request(job)
    snapshot = repo.read_supplement(repo.read_material_basic("M01"))
    review = repo.read_rule_snapshot(snapshot)
    slots = repo.query_schedule_options(job.request_id, job.items[0], snapshot)
    return snapshot, review, slots


def persist(repo, job, snapshot, review, slots, at=None):
    at = at or slots.available_slots()[0]["at"]
    part = next(p for p in slots.evidence() if any(s["at"] == at for s in p.slots))
    return repo.save_draft(
        job, job.items[0], snapshot, review, part, at, 0, evidence_ids=["TEST"], run_id="TEST"
    )


@pytest.mark.parametrize(
    "kind,missing",
    [
        (None, "type"),
        ("video", "title"),
        ("video", "media_ref"),
        ("image", "media_ref"),
        ("text", "caption"),
    ],
)
def test_minimum_fields_are_auditable_and_block_saving(repo, job, kind, missing):
    snapshot = repo.read_supplement(repo.read_material_basic("M01"))
    snapshot.material.type = kind
    setattr(snapshot.material, missing, None)
    review = repo.read_rule_snapshot(snapshot)
    assert review.review_status == "needs_info"
    assert any(
        m["field"] == missing and m["rule_id"].startswith("SYS-MINIMUM-") for m in review.matches
    )
    assert any(r.rule_id.startswith("SYS-MINIMUM-") for r in review.rules)


@pytest.mark.parametrize(
    "kind,field",
    [
        ("video", "caption"),
        ("text", "media_ref"),
        ("image", "source_asset_id"),
        ("video", "license_ref"),
    ],
)
def test_type_specific_optional_fields_do_not_block(repo, kind, field):
    snapshot = repo.read_supplement(repo.read_material_basic("M01"))
    snapshot.material.type = kind
    setattr(snapshot.material, field, None)
    review = repo.read_rule_snapshot(snapshot)
    assert review.review_status == "passed"


def test_empty_material_never_completes(catalog, job, tmp_path):
    for field in ("type", "title", "media_ref", "caption", "source_asset_id"):
        setattr(catalog.materials[0], field, None)
    catalog.materials[0].copyright_status = "authorized"
    catalog.supplements = []
    repo = Repository(tmp_path / "empty.db")
    repo.apply_catalog_snapshot(catalog)
    result = asyncio.run(run_request(job, repo, MockModel(), mode="mock", output=tmp_path / "run"))
    assert result.items[0].review_status == "needs_info"
    assert result.task_status == "needs_input"
    assert repo.list_drafts() == []


@pytest.mark.parametrize("at", ["2000-01-01T18:00:00+08:00", "2026-11-01T08:00:00+08:00"])
def test_expired_or_equal_hard_request_needs_input(repo, job, tmp_path, at):
    repo.business_clock = lambda: "2026-11-01T00:00:00Z"
    job.items[0].publish_requirement.requested_at = parse_time(at)
    result = asyncio.run(run_request(job, repo, MockModel(), mode="mock", output=tmp_path / "run"))
    assert result.task_status == "needs_input"
    assert result.items[0].error["code"] == "REQUEST_EXPIRED"
    assert result.items[0].original_requested_at == at
    assert result.items[0].suggested_at is None
    assert repo.list_drafts() == []


def test_candidate_expiry_is_not_occupancy(repo, job):
    repo.business_clock = lambda: "2026-11-03T09:00:00Z"
    job.items[0].publish_requirement.candidate_times = [parse_time("2026-11-03T08:00:00Z")]
    snapshot, _, slots = ready(repo, job)
    assert slots.alternatives.slots == []
    expired = slots.alternatives.checked_slots[0]
    assert not expired["available"] and expired["conflicts"] == []
    assert expired["unavailable_reason"] == "expired"
    assert snapshot.material.material_id == "M01"


def test_expiry_between_query_and_save_is_rejected(repo, job):
    snapshot, review, slots = ready(repo, job)
    repo.business_clock = lambda: "2026-11-03T10:30:00Z"
    with pytest.raises(BusinessError, match="已经到期") as caught:
        persist(repo, job, snapshot, review, slots)
    assert caught.value.code == "REQUEST_EXPIRED"
    assert repo.list_drafts() == []


def test_confirmation_expiry_stales_without_accepting(repo, job):
    snapshot, review, slots = ready(repo, job)
    draft = persist(repo, job, snapshot, review, slots)["draft"]
    repo.business_clock = lambda: "2026-11-03T11:00:00Z"
    decision = Confirmation(
        operation_id="expired",
        draft_id=draft["draft_id"],
        expected_revision=1,
        decisions=[{"item_id": "change_time", "decision": "accept"}],
    )
    with pytest.raises(BusinessError):
        repo.confirm_draft(decision)
    current = repo.current_draft(job.request_id, "M01")
    assert current["validity"] == "stale"
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) FROM confirmations").fetchone()[0] == 0


def test_replay_is_explicit_and_does_not_forge_audit_time(repo, job, tmp_path):
    repo.business_clock = lambda: "2099-01-01T00:00:00Z"
    repo.replay_at = utc(parse_time("2026-11-01T00:00:00Z"))
    result = asyncio.run(run_request(job, repo, MockModel(), mode="mock", output=tmp_path / "run"))
    assert result.items[0].draft.save_status == "saved"
    metadata = json.loads((tmp_path / "run/run.json").read_text("utf-8"))
    assert metadata["clock_mode"] == "replay"
    assert metadata["reference_time"] == repo.replay_at
    assert metadata["started_at"] != repo.replay_at


def test_missing_minimum_cannot_bypass_storage(catalog, job, tmp_path):
    catalog.materials[0].media_ref = None
    repo = Repository(tmp_path / "missing.db")
    repo.apply_catalog_snapshot(catalog)
    snapshot, review, slots = ready(repo, job)
    assert review.review_status == "needs_info"
    with pytest.raises(BusinessError) as caught:
        persist(repo, job, snapshot, review, slots)
    assert caught.value.code == "PRECONDITION_FAILED"
    assert repo.list_drafts() == []


def test_minimum_can_be_supplemented_and_reaudited(catalog, job, tmp_path):
    catalog.materials[0].title = None
    catalog.materials[0].media_ref = None
    catalog.supplements[0].fields.update(title="补充标题", media_ref="synthetic://fixed.mp4")
    repo = Repository(tmp_path / "supplement.db")
    repo.apply_catalog_snapshot(catalog)
    result = asyncio.run(run_request(job, repo, MockModel(), mode="mock", output=tmp_path / "run"))
    assert result.items[0].review_status == "passed"
    assert result.items[0].draft.save_status == "saved"
    events = [json.loads(x) for x in (tmp_path / "run/trace.jsonl").read_text("utf-8").splitlines()]
    reviews = [
        x["result"]["data"]["review_status"]
        for x in events
        if x["event_type"] == "tool_result" and x["result"]["tool"] == "check_rules"
    ]
    assert reviews == ["needs_info", "passed"]


def test_selected_candidate_expires_before_save(repo, job):
    repo.business_clock = lambda: "2026-11-03T08:59:00Z"
    job.items[0].publish_requirement.candidate_times = [parse_time("2026-11-03T09:00:00Z")]
    snapshot, review, slots = ready(repo, job)
    repo.business_clock = lambda: "2026-11-03T09:00:00Z"
    with pytest.raises(BusinessError) as caught:
        persist(repo, job, snapshot, review, slots)
    assert caught.value.code == "SLOT_EXPIRED"
    assert repo.list_drafts() == []


def test_system_rule_prefix_cannot_be_imported(catalog):
    catalog.rules[0].rule_id = "SYS-MINIMUM-TYPE"
    with pytest.raises(ValueError, match="保留前缀"):
        type(catalog).model_validate(catalog.model_dump())
