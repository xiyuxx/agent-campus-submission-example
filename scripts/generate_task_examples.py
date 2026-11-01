"""Actual synthetic task journeys; confirmations are explicit fixture user actions."""

import argparse
import asyncio
import hashlib
from pathlib import Path

from shadow_agent.common import dumps, new_id
from shadow_agent.contracts import Confirmation, PublishRequirement, parse_time
from shadow_agent.models.mock import MockModel
from shadow_agent.service import run_request, source_metadata
from shadow_agent.storage import Repository, load_catalog


async def generate(output):
    output.mkdir(parents=True, exist_ok=False)
    runtime = Path("runtime") / new_id("task-evidence")
    log = []
    runs = []

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps(value) + "\n", encoding="utf-8", newline="\n")

    def checkpoint(label, repo, action, result):
        state = {
            "fixture_action": action,
            "actual_result": result,
            "worklist": repo.task_worklist(),
            "handoff": repo.handoff(),
        }
        write(output / (label + ".json"), state)
        log.append({"checkpoint": label + ".json", "action": action})

    def make_repo(name, *, missing=False):
        repo = Repository(runtime / (name + ".db"), replay_at="2026-11-01T00:00:00Z")
        catalog = load_catalog(Path("data"))
        if missing:
            catalog.materials[0].title = None
            catalog.supplements = []
        repo.apply_catalog_snapshot(catalog)
        return repo

    requirement = PublishRequirement.model_validate(
        dict(
            requested_at="2026-11-03T18:00:00+08:00",
            timezone="Asia/Shanghai",
            constraint="hard",
            candidate_times=["2026-11-03T19:00:00+08:00"],
        )
    )

    async def perform(label, repo, request):
        folder = output / label
        result = await run_request(request, repo, MockModel(), mode="mock", output=folder)
        with repo.transaction() as db:
            counts = {
                table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("publication_tasks", "drafts", "draft_history", "confirmations")
            }
        write(
            folder / "database_check.json",
            {"counts": counts, "current": repo.current_draft(request.request_id, "M01")},
        )
        item = result.items[0]
        runs.append(
            {
                "directory": label,
                "mode": "mock",
                "task_status": item.task_status,
                "save_status": item.draft.save_status,
                "counts": counts,
            }
        )
        checkpoint(
            label + "-state",
            repo,
            {"operation": "check", "request": request.model_dump(mode="json")},
            result.model_dump(mode="json"),
        )
        return result

    def task_row(repo):
        return next(row for row in repo.task_worklist() if row["material_id"] == "M01")

    def approve(repo, label):
        row = task_row(repo)
        draft = row["draft"]
        decision = Confirmation(
            operation_id=new_id("FIXTURE-CONFIRM"),
            draft_id=draft["draft_id"],
            expected_revision=draft["revision"],
            decisions=[{"item_id": p["id"], "decision": "accept"} for p in draft["pending_items"]],
        )
        result = repo.confirm_draft(decision)
        checkpoint(
            label,
            repo,
            {"operation": "synthetic_user_accept", "decision": decision.model_dump(mode="json")},
            result,
        )
        return decision

    repo = make_repo("journey")
    request = repo.prepare_task("M01", requirement)
    first = await perform("check_conflict", repo, request)
    assert first.items[0].confirmation_status == "pending" and not repo.handoff()["items"]
    decision = approve(repo, "confirm_and_handoff")
    assert len(repo.handoff()["items"]) == 1
    # Original deadline passes, approved future suggestion remains usable.
    repo.replay_at = "2026-11-03T10:30:00.000000Z"
    repeat = await perform("approved_future_reuse", repo, request)
    assert repeat.task_status == "completed" and repeat.items[0].draft.save_status == "reused"
    repo.replay_at = "2026-11-03T11:00:00.000000Z"
    replay = repo.confirm_draft(decision)
    checkpoint(
        "expired_confirmation_replay",
        repo,
        {"operation": "replay_same_confirmation", "decision": decision.model_dump(mode="json")},
        replay,
    )
    assert replay["stored_operation_result"]["confirmation_status"] == "confirmed"
    assert replay["current_draft"]["validity"] == "stale" and not repo.handoff()["items"]
    revised = requirement.model_copy(deep=True)
    revised.requested_at = parse_time("2026-11-03T20:00:00+08:00")
    next_request = repo.prepare_task("M01", revised)
    await perform("user_changes_time", repo, next_request)
    row = task_row(repo)
    cancelled = repo.cancel_task(row["task_id"], expected_revision=row["revision"])
    checkpoint("cancel_confirmed_or_ready", repo, {"operation": "synthetic_user_cancel"}, cancelled)
    assert not repo.handoff()["items"]

    repo = make_repo("missing", missing=True)
    request = repo.prepare_task("M01", requirement)
    missing = await perform("information_missing", repo, request)
    assert missing.items[0].review_status == "needs_info" and not repo.handoff()["items"]
    fields = {"title": "用户补齐标题", "copyright_status": "authorized"}
    version = repo.read_material_basic("M01").material.material_version
    updated = repo.supplement_material("M01", fields, expected_material_version=version)
    checkpoint(
        "user_supplements",
        repo,
        {"operation": "synthetic_user_supplement", "fields": fields},
        updated,
    )
    completed = await perform("supplement_and_continue", repo, request)
    assert completed.items[0].draft.save_status == "saved"

    repo = make_repo("save-failure")
    request = repo.prepare_task("M01", requirement)
    await perform("before_failed_edit", repo, request)
    approve(repo, "before_failed_edit_confirm")
    old_id = task_row(repo)["draft"]["draft_id"]
    request = repo.prepare_task("M01", revised)
    repo.fault = "save_before"
    failed = await perform("edit_save_failure", repo, request)
    assert failed.task_status == "failed"
    assert task_row(repo)["state"] == "修改未完成"
    assert task_row(repo)["draft"]["draft_id"] == old_id
    assert task_row(repo)["draft"]["validity"] == "active"
    repo.fault = None
    await perform("retry_failed_edit", repo, request)
    assert task_row(repo)["state"] == "可交接"
    write(
        output / "action-log.json",
        {
            "source": source_metadata(),
            "notice": "Synthetic fixture user actions, not a record of candidate personal trial.",
            "actions": log,
        },
    )
    write(output / "index.json", {"runs": runs})
    manifest = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    write(output / "manifest.json", manifest)
    print(dumps({"runs": len(runs), "checkpoints": len(log)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(generate(parser.parse_args().output))
