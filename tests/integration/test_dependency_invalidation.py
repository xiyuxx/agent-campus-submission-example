import pytest
from test_storage import count, decide, save

from shadow_agent.contracts import ScheduleEntry, parse_time


def confirmed(repo, job):
    draft = save(repo, job)["draft"]
    repo.confirm_draft(decide(draft))
    return draft


def test_other_material_update_preserves_approval_and_reuses_old_evidence(repo, job, catalog):
    original = confirmed(repo, job)
    catalog.materials[1].title = "另一项素材更新"
    catalog.materials[1].material_version += 1
    before = repo.path.read_bytes()
    preview = repo.preview_catalog_snapshot(catalog)
    assert repo.path.read_bytes() == before
    applied = repo.apply_catalog_snapshot(catalog, update=True)
    assert preview["changes"]["staled_drafts"] == applied["changes"]["staled_drafts"] == []
    current = repo.current_draft(job.request_id, "M01")
    assert current["validity"] == "active" and current["confirmation_status"] == "confirmed"
    assert current["catalog_revision"] == original["catalog_revision"]
    assert save(repo, job, expected=1)["save_status"] == "reused"
    assert count(repo, "draft_history") == count(repo, "confirmations") == 1


def test_supplementing_m02_does_not_revoke_m01(repo, job):
    original = confirmed(repo, job)
    version = repo.read_material_basic("M02").material.material_version
    change = repo.supplement_material(
        "M02",
        {"copyright_status": "authorized", "cover_ref": "synthetic://cover/M02"},
        expected_material_version=version,
    )
    assert change["changes"]["staled_drafts"] == []
    state = next(row for row in repo.task_worklist() if row["material_id"] == "M01")
    assert state["state"] == "可交接"
    assert state["draft"]["draft_id"] == original["draft_id"]
    assert state["draft"]["revision"] == 1


@pytest.mark.parametrize("change", ["material", "rules", "removed", "conflict"])
def test_affected_dependency_invalidates_and_preview_matches_import(repo, job, catalog, change):
    draft = confirmed(repo, job)
    if change == "material":
        catalog.materials[0].caption = "素材发生实际变化"
    elif change == "rules":
        catalog.rules[0].version += 1
    elif change == "removed":
        catalog.materials = [m for m in catalog.materials if m.material_id != "M01"]
        catalog.supplements = [s for s in catalog.supplements if s.material_id != "M01"]
    else:
        catalog.schedule.entries[0].at = job.items[0].publish_requirement.candidate_times[0]
    preview = repo.preview_catalog_snapshot(catalog)
    applied = repo.apply_catalog_snapshot(catalog, update=True)
    assert preview["changes"]["staled_drafts"] == [draft["draft_id"]]
    assert preview["changes"]["invalidation_reasons"] == applied["changes"]["invalidation_reasons"]
    assert repo.current_draft(job.request_id, "M01")["validity"] == "stale"
    assert count(repo, "confirmations") == 1


def test_other_account_and_other_slot_changes_preserve_current_plan(repo, job, catalog):
    confirmed(repo, job)
    catalog.schedule.entries.append(
        ScheduleEntry(
            entry_id="OTHER-ACCOUNT",
            material_id="OTHER",
            platform="douyin",
            account_id="other",
            at=job.items[0].publish_requirement.candidate_times[0],
        )
    )
    catalog.schedule.entries[0].at = parse_time("2026-11-03T20:00:00+08:00")
    # Assignments are validated at the catalog boundary, as in real imports.
    catalog = type(catalog).model_validate(catalog.model_dump(mode="json"))
    result = repo.apply_catalog_snapshot(catalog, update=True)
    assert result["changes"]["staled_drafts"] == []
    assert repo.current_draft(job.request_id, "M01")["confirmation_status"] == "confirmed"


@pytest.mark.parametrize("explicit", [True, False])
def test_only_default_alternative_depends_on_account_candidate_config(repo, job, catalog, explicit):
    if not explicit:
        job.items[0].publish_requirement.candidate_times = None
    draft = confirmed(repo, job)
    catalog.schedule.allowed_slots = []
    result = repo.apply_catalog_snapshot(catalog, update=True)
    if explicit:
        assert result["changes"]["staled_drafts"] == []
    else:
        assert result["changes"]["invalidation_reasons"][draft["draft_id"]] == "CANDIDATE_REMOVED"
