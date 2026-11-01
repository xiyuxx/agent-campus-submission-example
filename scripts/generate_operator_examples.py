"""Actual synthetic operator journey: guided inputs, unrelated updates and CSV snapshots."""

import argparse
import asyncio
import hashlib
import io
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from shadow_agent.common import dumps, new_id
from shadow_agent.contracts import Confirmation, PublishRequirement
from shadow_agent.handoff import export_handoff
from shadow_agent.models.mock import MockModel
from shadow_agent.operator_language import parse_operator_time
from shadow_agent.service import run_request, source_metadata
from shadow_agent.storage import Repository, load_catalog
from shadow_agent.work import ask_supplement, describe


async def generate(output):
    output.mkdir(parents=True, exist_ok=False)
    repo = Repository(
        Path("runtime") / new_id("operator-evidence") / "agent.db", replay_at="2026-11-01T00:00:00Z"
    )
    repo.apply_catalog_snapshot(load_catalog(Path("data")))
    records, actions = [], []

    def write(path, value):
        path.write_text(dumps(value) + "\n", encoding="utf-8", newline="\n")

    def row(material):
        return next(value for value in repo.task_worklist() if value["material_id"] == material)

    def prepare(material, at, candidate):
        return repo.prepare_task(
            material,
            PublishRequirement.model_validate(
                {
                    "requested_at": parse_operator_time(at),
                    "timezone": "local",
                    "constraint": "hard",
                    "candidate_times": [parse_operator_time(candidate)],
                }
            ),
        )

    async def perform(label, request):
        folder = output / label
        result = await run_request(request, repo, MockModel(), mode="mock", output=folder)
        item = result.items[0]
        with repo.transaction() as db:
            counts = {
                name: db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                for name in ("drafts", "draft_history", "confirmations")
            }
        write(
            folder / "database_check.json",
            {"counts": counts, "current": repo.current_draft(request.request_id, item.material_id)},
        )
        records.append(
            {
                "directory": label,
                "mode": "mock",
                "task_status": item.task_status,
                "save_status": item.draft.save_status,
                "counts": counts,
            }
        )
        return result

    m01 = prepare("M01", "2026-11-03T18:00:00+08:00", "2026-11-03T19:00:00+08:00")
    await perform("m01_check", m01)
    draft = row("M01")["draft"]
    decision = Confirmation(
        operation_id=new_id("FIXTURE-ACCEPT"),
        draft_id=draft["draft_id"],
        expected_revision=draft["revision"],
        decisions=[
            {"item_id": item["id"], "decision": "accept"} for item in draft["pending_items"]
        ],
    )
    repo.confirm_draft(decision)
    before = row("M01")
    first = export_handoff(repo, output / "before.csv")
    assert before["state"] == "可交接"

    m02 = prepare("M02", "2026-11-03T21:00:00+08:00", "2026-11-03T22:00:00+08:00")
    missing = await perform("m02_missing", m02)
    assert missing.items[0].review_status == "needs_info"
    prompts = []

    def answer(prompt):
        value = "1" if "版权" in prompt else "synthetic://operator/M02-cover.jpg"
        prompts.append({"question": prompt, "fixture_user_answer": value})
        return value

    with patch("shadow_agent.work.ask", side_effect=answer):
        fields = ask_supplement(repo, row("M02"))
    version = repo.read_material_basic("M02").material.material_version
    imported = repo.supplement_material("M02", fields, expected_material_version=version)
    after = row("M01")
    second = export_handoff(repo, output / "after-unrelated-update.json")
    assert after["state"] == "可交接" and after["draft"]["revision"] == before["draft"]["revision"]
    assert after["draft"]["confirmation_status"] == "confirmed"
    assert first["export_version"] == second["export_version"]
    actions.append(
        {
            "action": "synthetic_user_supplements_m02",
            "prompts": prompts,
            "fields": fields,
            "import_result": imported,
            "m01_before": before,
            "m01_after": after,
        }
    )
    await perform("m02_continue", m02)
    third = export_handoff(repo, output / "two-materials.csv")
    assert third["count"] == 2
    original = (output / "two-materials.csv").read_bytes()
    current = row("M02")
    repo.cancel_task(current["task_id"], expected_revision=current["revision"])
    fourth = export_handoff(repo, output / "after-cancel.csv")
    assert fourth["count"] == 1 and fourth["export_version"] != third["export_version"]
    assert (output / "two-materials.csv").read_bytes() == original
    repeated = await perform("m01_recheck_after_m02", m01)
    assert repeated.task_status == "completed"
    assert repeated.items[0].draft.save_status == "reused"
    assert repeated.items[0].draft.revision == before["draft"]["revision"]
    assert repeated.items[0].confirmation_status == "confirmed"
    transcript = io.StringIO()
    with redirect_stderr(transcript):
        describe(row("M01"))
        describe(row("M02"))
    (output / "operator-summary.txt").write_text(
        transcript.getvalue(), encoding="utf-8", newline="\n"
    )
    write(
        output / "journey.json",
        {
            "source": source_metadata(),
            "mode": "mock",
            "notice": "Fixture user actions, not candidate personal trial or publishing.",
            "actions": actions,
            "exports": [first, second, third, fourth],
        },
    )
    write(output / "index.json", {"runs": records})
    manifest = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    write(output / "manifest.json", manifest)
    print(
        dumps(
            {
                "runs": len(records),
                "unrelated_approval_preserved": True,
                "csv_exports": 3,
                "old_snapshot_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(generate(parser.parse_args().output))
