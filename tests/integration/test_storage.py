from pathlib import Path

import pytest

from shadow_agent.common import BusinessError, utc
from shadow_agent.contracts import Confirmation, Request, SlotObservation
from shadow_agent.storage import Repository


def save(repo, job, *, expected=0, at="2026-11-03T11:00:00.000000Z", query="alternatives"):
    repo.register_request(job)
    snapshot = repo.read_supplement(repo.read_material_basic("M01"))
    review = repo.read_rule_snapshot(snapshot)
    slots = repo.query_occupancy(job.request_id, job.items[0], snapshot, query)
    return repo.save_draft(
        job,
        job.items[0],
        snapshot,
        review,
        slots,
        at,
        expected,
        evidence_ids=["TEST-OBS"],
        run_id="TEST-RUN",
    )


def decide(draft, *, operation="C1", choice="accept", revision=None):
    return Confirmation(
        operation_id=operation,
        draft_id=draft["draft_id"],
        expected_revision=revision or draft["revision"],
        decisions=[{"item_id": "change_time", "decision": choice}],
    )


def count(repo, table):
    with repo.transaction() as connection:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_migration_matches_design():
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs/15-database-schema.sql").read_bytes() == (
        root / "src/shadow_agent/migrations/001_initial.sql"
    ).read_bytes()


def test_seed_noop_and_retains_confirmations(repo, catalog, job):
    saved = save(repo, job)["draft"]
    repo.confirm_draft(decide(saved))
    assert repo.apply_catalog_snapshot(catalog, update=True)["outcome"] == "noop"
    repeated = save(Repository(repo.path), job, expected=1)
    assert repeated["save_status"] == "reused"
    assert repeated["draft"]["confirmation_status"] == "confirmed"
    assert count(repo, "drafts") == count(repo, "draft_history") == 1


def test_import_replaces_occupancy_stales_and_preserves_history(repo, catalog, job):
    old = save(repo, job)["draft"]
    repo.confirm_draft(decide(old))
    updated = catalog.model_copy(deep=True)
    updated.schedule.entries[0].at = job.items[0].publish_requirement.candidate_times[0]
    assert repo.apply_catalog_snapshot(updated, update=True)["catalog_revision"] == 2
    current = repo.current_draft(job.request_id, "M01")
    assert current["validity"] == "stale"
    assert count(repo, "confirmations") == 1
    snapshot = repo.read_supplement(repo.read_material_basic("M01"))
    requested = repo.query_occupancy(job.request_id, job.items[0], snapshot, "requested")
    assert requested.slots[0]["available"]
    new = repo.save_draft(
        job,
        job.items[0],
        snapshot,
        repo.read_rule_snapshot(snapshot),
        requested,
        requested.slots[0]["at"],
        1,
        evidence_ids=[],
        run_id="T2",
    )
    assert new["save_status"] == "updated"
    assert new["draft"]["draft_id"] == old["draft_id"]
    assert new["draft"]["revision"] == 2
    assert new["draft"]["confirmation_status"] == "not_required"
    assert count(repo, "draft_history") == 2


def test_request_conflict_does_not_overwrite(repo, job):
    repo.register_request(job)
    changed = job.model_copy(deep=True)
    changed.items[0].publish_requirement.candidate_times = []
    with pytest.raises(BusinessError, match="原始要求不同"):
        repo.register_request(changed)
    assert count(repo, "requests") == 1


def test_reject_terminal_and_operation_replay(repo, job, catalog):
    saved = save(repo, job)["draft"]
    decision = decide(saved, choice="reject")
    assert repo.confirm_draft(decision)["current_draft"]["validity"] == "rejected"
    assert repo.confirm_draft(decision)["replayed"]
    with pytest.raises(BusinessError) as error:
        save(repo, job, expected=1)
    assert error.value.code == "DRAFT_REJECTED"
    changed = catalog.model_copy(deep=True)
    changed.schedule.entries = []
    repo.apply_catalog_snapshot(changed, update=True)
    assert repo.current_draft(job.request_id, "M01")["validity"] == "rejected"
    assert count(repo, "draft_history") == 1


def test_explicit_new_publication_does_not_exclude_other_request(repo, job):
    first = save(repo, job)["draft"]
    second = job.model_copy(update={"request_id": "REQ-2"}, deep=True)
    second.items[0].new_publication = True
    repo.register_request(second)
    snapshot = repo.read_material_basic("M01")
    own = repo.query_occupancy(job.request_id, job.items[0], snapshot, "alternatives")
    assert own.slots[0]["available"]
    with pytest.raises(BusinessError) as caught:
        repo.query_occupancy(second.request_id, second.items[0], snapshot, "alternatives")
    assert caught.value.code == "DUPLICATE_PUBLICATION"
    assert caught.value.details["related_drafts"][0]["draft_id"] == first["draft_id"]


def test_before_commit_failure_rolls_back_all(repo, job):
    repo.fault = "save_before"
    with pytest.raises(BusinessError, match="已回滚"):
        save(repo, job)
    assert count(repo, "drafts") == count(repo, "draft_history") == count(repo, "draft_events") == 0


@pytest.mark.parametrize(
    "fault,expected", [("receipt_lost", "saved"), ("receipt_unknown", "SAVE_UNKNOWN")]
)
def test_receipt_loss(repo, job, fault, expected):
    repo.fault = fault
    if expected == "saved":
        assert save(repo, job)["receipt_recovered"]
    else:
        with pytest.raises(BusinessError) as error:
            save(repo, job)
        assert error.value.code == expected
    assert count(repo, "draft_history") == 1


def test_bad_revision_and_forged_evidence_are_rejected(repo, job):
    with pytest.raises(BusinessError) as error:
        save(repo, job, expected=2)
    assert error.value.code == "REVISION_CONFLICT"
    repo.register_request(job)
    snapshot = repo.read_supplement(repo.read_material_basic("M01"))
    review = repo.read_rule_snapshot(snapshot)
    slots = SlotObservation(
        catalog_revision=1,
        query="alternatives",
        source="explicit",
        slots=[{"at": "2026-11-03T12:00:00.000000Z", "available": True}],
    )
    with pytest.raises(BusinessError, match="扩大候选"):
        repo.save_draft(
            job,
            job.items[0],
            snapshot,
            review,
            slots,
            slots.slots[0]["at"],
            0,
            evidence_ids=[],
            run_id="FAKE",
        )


def test_save_rechecks_schedule_after_query(repo, job):
    repo.register_request(job)
    snapshot = repo.read_supplement(repo.read_material_basic("M01"))
    slots = repo.query_occupancy(job.request_id, job.items[0], snapshot, "alternatives")
    with repo.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO schedule VALUES ('S2','douyin','brand_main',?,'OUTSIDE',?)",
            (slots.slots[0]["at"], snapshot.import_id),
        )
    with pytest.raises(BusinessError) as error:
        repo.save_draft(
            job,
            job.items[0],
            snapshot,
            repo.read_rule_snapshot(snapshot),
            slots,
            slots.slots[0]["at"],
            0,
            evidence_ids=[],
            run_id="TEST",
        )
    assert error.value.code == "SLOT_CONFLICT"


def test_supplement_conflict_merges_nothing(repo, catalog):
    changed = catalog.model_copy(deep=True)
    changed.supplements[0].fields = {"caption": "冲突文案", "copyright_status": "authorized"}
    repo.apply_catalog_snapshot(changed, update=True)
    basic = repo.read_material_basic("M01")
    with pytest.raises(BusinessError) as error:
        repo.read_supplement(basic)
    assert error.value.code == "DATA_CONFLICT"
    assert basic.material.copyright_status is None


def test_stopping_directory_rows_preserves_foreign_keys(repo, catalog, job):
    saved = save(repo, job)["draft"]
    changed = catalog.model_copy(deep=True)
    changed.materials = [x for x in changed.materials if x.material_id != "M01"]
    changed.supplements = []
    repo.apply_catalog_snapshot(changed, update=True)
    with pytest.raises(BusinessError, match="未找到"):
        repo.read_material_basic("M01")
    assert repo.inspect_draft(saved["draft_id"])["validity"] == "stale"
    with repo.transaction() as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_skip_does_not_write_and_replay_keeps_historical_result(repo, job, catalog):
    saved = save(repo, job)["draft"]
    event_count = count(repo, "draft_events")
    assert repo.confirm_draft(decide(saved, choice="skip"))["skipped"]
    assert count(repo, "confirmations") == 0
    assert count(repo, "draft_events") == event_count
    accepted = decide(saved)
    repo.confirm_draft(accepted)
    changed = catalog.model_copy(deep=True)
    changed.schedule.entries[0].at = job.items[0].publish_requirement.candidate_times[0]
    repo.apply_catalog_snapshot(changed, update=True)
    replay = repo.confirm_draft(accepted)
    assert replay["stored_operation_result"]["confirmation_status"] == "confirmed"
    assert replay["current_draft"]["validity"] == "stale"


def test_explicit_cross_day_and_empty_candidates(repo, job):
    item = job.items[0].model_copy(deep=True)
    item.publish_requirement.candidate_times = []
    snapshot = repo.read_material_basic("M01")
    assert repo.query_occupancy(job.request_id, item, snapshot, "alternatives").slots == []
    item.publish_requirement.candidate_times = None
    default = repo.query_occupancy(job.request_id, item, snapshot, "alternatives")
    assert len(default.slots) == 1
    assert default.slots[0]["at"] == "2026-11-03T11:00:00.000000Z"
    assert default.source == "default"
    next_day = Request.model_validate(
        {
            "request_id": "N",
            "items": [
                {
                    "material_id": "M01",
                    "publish_requirement": {
                        "requested_at": "2026-11-03T18:00:00+08:00",
                        "timezone": "Asia/Shanghai",
                        "constraint": "soft",
                        "candidate_times": ["2026-11-04T01:00:00+08:00"],
                    },
                }
            ],
        }
    )
    result = repo.query_occupancy("N", next_day.items[0], snapshot, "alternatives")
    assert result.slots[0]["at"] == utc(next_day.items[0].publish_requirement.candidate_times[0])
