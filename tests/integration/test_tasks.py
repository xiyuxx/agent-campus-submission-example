import json
import sqlite3
from importlib.resources import files

import pytest
from test_workflow_fixes import approve, run

from shadow_agent.common import BusinessError
from shadow_agent.contracts import Confirmation, PublishRequirement, parse_time
from shadow_agent.storage import Repository


def row(repo):
    return next(x for x in repo.task_worklist() if x["material_id"] == "M01")


def changed(job):
    value = job.items[0].publish_requirement.model_copy(deep=True)
    value.requested_at = parse_time("2026-11-03T20:00:00+08:00")
    return value


def test_worklist_and_task_identity_hide_request_ids(repo, job, tmp_path):
    assert row(repo)["state"] == "待处理"
    request = repo.prepare_task("M01", job.items[0].publish_requirement)
    repeated = repo.prepare_task("M01", job.items[0].publish_requirement)
    assert request == repeated
    assert row(repo)["state"] == "待检查"
    first = run(repo, request, tmp_path / "first")
    task_id = row(repo)["task_id"]
    assert row(repo)["state"] == "等你确认"
    approve(repo, first)
    assert row(repo)["state"] == "可交接"
    assert repo.handoff()["items"][0]["confirmation_status"] == "confirmed"
    repeat = run(repo, request, tmp_path / "repeat")
    assert repeat.items[0].draft.save_status == "reused"
    assert row(repo)["task_id"] == task_id


def test_edit_replaces_atomically_and_does_not_inherit_confirmation(repo, job, tmp_path):
    first = run(repo, job, tmp_path / "first")
    approve(repo, first)
    original = row(repo)
    requirement = changed(job)
    # A candidate deviation will need a fresh confirmation.
    requirement.requested_at = parse_time("2026-11-03T18:01:00+08:00")
    with repo.transaction(write=True) as db:
        db.execute(
            "INSERT INTO schedule SELECT 'BLOCK-NEW',platform,account_id,"
            "'2026-11-03T10:01:00.000000Z','OTHER',import_id FROM schedule LIMIT 1"
        )
    request = repo.prepare_task("M01", requirement, expected_revision=original["revision"])
    assert row(repo)["state"] == "修改未完成"
    assert not repo.handoff()["items"]
    second = run(repo, request, tmp_path / "second")
    assert second.items[0].confirmation_status == "pending"
    assert second.items[0].draft.id != first.items[0].draft.id
    assert repo.inspect_draft(first.items[0].draft.id)["validity"] == "stale"
    assert row(repo)["task_id"] == original["task_id"]
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) FROM publication_tasks").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM drafts WHERE validity='active'").fetchone()[0] == 1


@pytest.mark.parametrize("fault", ["save_before", "receipt_lost", "receipt_unknown"])
def test_change_failure_and_unknown_receipt_do_not_duplicate_or_lose_plan(
    repo, job, tmp_path, fault
):
    first = run(repo, job, tmp_path / "first")
    approve(repo, first)
    original = row(repo)
    request = repo.prepare_task("M01", changed(job))
    repo.fault = fault
    second = run(repo, request, tmp_path / "second")
    current = row(repo)
    if fault == "save_before":
        assert second.task_status == "failed"
        assert current["state"] == "修改未完成"
        assert current["draft"]["draft_id"] == original["draft"]["draft_id"]
        assert current["draft"]["validity"] == "active"
        assert current["draft"]["confirmation_status"] == "confirmed"
    elif fault == "receipt_unknown":
        assert second.task_status == "incomplete"
        assert current["state"] == "保存未完成"
    else:
        assert second.items[0].draft.save_status == "saved"
        assert current["state"] == "可交接"
    repo.fault = None
    recovered = run(repo, request, tmp_path / "recover")
    assert recovered.items[0].draft.save_status in ("saved", "reused")
    assert row(repo)["state"] == "可交接"
    with repo.transaction() as db:
        assert db.execute("SELECT count(*) FROM drafts WHERE validity='active'").fetchone()[0] == 1


def test_cancel_confirmed_plan_releases_capacity_and_requires_explicit_reopen(repo, job, tmp_path):
    first = run(repo, job, tmp_path / "first")
    approve(repo, first)
    task = row(repo)
    repo.cancel_task(task["task_id"], expected_revision=task["revision"])
    assert row(repo)["state"] == "已取消"
    assert repo.handoff()["items"] == []
    assert repo.inspect_draft(first.items[0].draft.id)["validity"] == "stale"
    repeat = run(repo, job, tmp_path / "repeat")
    assert repeat.items[0].error["code"] == "TASK_CANCELLED"
    with pytest.raises(BusinessError, match="恢复"):
        repo.prepare_task("M01", changed(job))
    request = repo.prepare_task("M01", changed(job), reopen=True)
    run(repo, request, tmp_path / "reopen")
    assert row(repo)["state"] == "可交接"


def test_old_confirmation_and_old_run_cannot_override_new_requirements(repo, job, tmp_path):
    first = run(repo, job, tmp_path / "first")
    old = row(repo)
    repo.prepare_task("M01", changed(job))
    with pytest.raises(BusinessError) as caught:
        repo.confirm_draft(
            Confirmation(
                operation_id="OLD",
                draft_id=first.items[0].draft.id,
                expected_revision=1,
                decisions=[{"item_id": "change_time", "decision": "accept"}],
            )
        )
    assert caught.value.code == "TASK_SUPERSEDED"
    repeat = run(repo, job, tmp_path / "repeat")
    assert repeat.items[0].error["code"] == "TASK_SUPERSEDED"
    with pytest.raises(BusinessError) as caught:
        repo.prepare_task("M01", changed(job), expected_revision=old["revision"])
    assert caught.value.code == "REVISION_CONFLICT"


def test_handoff_rechecks_expiry_and_material_change(repo, job, tmp_path):
    first = run(repo, job, tmp_path / "first")
    approve(repo, first)
    assert len(repo.handoff()["items"]) == 1
    repo.business_clock = lambda: "2026-11-03T11:00:00Z"
    assert repo.handoff()["items"] == []
    assert row(repo)["state"] == "需重新检查"


def test_supplement_updates_version_rechecks_and_preserves_task_identity(repo, job, tmp_path):
    first = run(repo, job, tmp_path / "first")
    approve(repo, first)
    before = row(repo)
    version = repo.read_material_basic("M01").material.material_version
    repo.supplement_material("M01", {"title": "用户修改标题"}, expected_material_version=version)
    assert row(repo)["task_id"] == before["task_id"]
    assert repo.handoff()["items"] == []
    second = run(repo, job, tmp_path / "second")
    assert second.items[0].confirmation_status == "pending"
    assert second.items[0].draft.revision == 2
    with pytest.raises(BusinessError):
        repo.supplement_material("M01", {"title": "过期覆盖"}, expected_material_version=version)
    with pytest.raises(ValueError):
        repo.supplement_material(
            "M01", {"platform": "other"}, expected_material_version=version + 1
        )


def make_v1(path, catalog, job):
    """Produce an actual schema-v1 DB, independent of the new initializer."""
    repo = Repository(path)
    repo.apply_catalog_snapshot(catalog)
    # Current SQL tables/data are compatible; remove only the new task extension.
    with repo.transaction(write=True) as db:
        for table in ("task_events", "task_requests", "publication_tasks"):
            db.execute(f"DROP TABLE {table}")
        db.execute("DROP INDEX ux_drafts_active_material")
        db.execute("PRAGMA user_version=1")
    return repo


def test_migration_is_atomic_idempotent_and_preserves_original_schema(repo, catalog, job, tmp_path):
    old = make_v1(tmp_path / "legacy.db", catalog, job)
    old.initialize()
    old.initialize()
    assert old.task_worklist()[0]["state"] == "待处理"
    with old.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_migration_backfills_real_drafts_and_confirms(repo, job, tmp_path):
    first = run(repo, job, tmp_path / "first")
    approve(repo, first)
    with repo.transaction(write=True) as db:
        for table in ("task_events", "task_requests", "publication_tasks"):
            db.execute(f"DROP TABLE {table}")
        db.execute("DROP INDEX ux_drafts_active_material")
        db.execute("PRAGMA user_version=1")
    repo.initialize()
    assert row(repo)["state"] == "可交接"
    assert row(repo)["draft"]["draft_id"] == first.items[0].draft.id
    assert row(repo)["draft"]["confirmation_status"] == "confirmed"


def test_migration_refuses_ambiguous_active_drafts_without_partial_changes(tmp_path):
    path = tmp_path / "ambiguous.db"
    db = sqlite3.connect(path, autocommit=True)
    schema = files("shadow_agent").joinpath("migrations/001_initial.sql").read_text("utf-8")
    db.executescript(schema)
    # Deliberately incomplete legacy data is enough for preflight; no source rows are modified.
    db.execute(
        "INSERT INTO drafts VALUES ('A','R1','M',1,'active','p','a','2030',1,?, 't','t')",
        ("a" * 64,),
    )
    db.execute(
        "INSERT INTO drafts VALUES ('B','R2','M',1,'active','p','a','2031',1,?, 't','t')",
        ("a" * 64,),
    )
    db.execute("PRAGMA user_version=1")
    db.close()
    with pytest.raises(BusinessError) as caught:
        Repository(path).initialize()
    assert caught.value.code == "MIGRATION_CONFLICT"
    assert caught.value.details["duplicates"][0]["draft_ids"] == "A,B"
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not db.execute(
        "SELECT name FROM sqlite_schema WHERE name='publication_tasks'"
    ).fetchone()
    assert db.execute("SELECT count(*) FROM drafts").fetchone()[0] == 2
    db.close()


def test_missing_requirements_stay_in_task_until_user_completes_them(repo, tmp_path):
    request = repo.prepare_task("M01", PublishRequirement())
    assert run(repo, request, tmp_path / "first").task_status == "needs_input"
    assert row(repo)["state"] == "待检查"
    assert row(repo)["last_result"]["task_status"] == "needs_input"
    assert row(repo)["draft"] is None


def test_task_record_failure_keeps_trace_and_public_failure_consistent(repo, job, tmp_path):
    def fail_record(*args):
        raise BusinessError("DATABASE_ERROR", "任务运行结果记录失败")

    repo.record_task_result = fail_record
    result = run(repo, job, tmp_path / "record-failure")
    assert result.task_status == "failed"
    assert result.items[0].draft.save_status == "saved"
    events = [
        json.loads(line)
        for line in (tmp_path / "record-failure/trace.jsonl").read_text("utf-8").splitlines()
    ]
    final = [event for event in events if event["event_type"] == "final"][-1]
    assert final["result"] == result.items[0].model_dump(mode="json")
    assert final["reason"] == "task_record_failed"
