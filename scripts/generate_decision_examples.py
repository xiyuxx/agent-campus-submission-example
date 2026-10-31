"""Generate fresh synthetic evidence; --live explicitly enables DeepSeek calls."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from shadow_agent.common import dumps, utc
from shadow_agent.config import ModelSettings
from shadow_agent.contracts import Request, Slot, parse_time
from shadow_agent.models.deepseek import DeepSeekModel
from shadow_agent.models.mock import MockModel
from shadow_agent.service import run_request
from shadow_agent.storage import Repository, load_catalog


class RacingRepository(Repository):
    """Controlled occupancy change immediately before the first genuine save attempt."""

    injected = False

    def save_draft(self, request, item, snapshot, review, slot, suggested_at, *args, **kwargs):
        if not self.injected:
            with self.transaction(write=True) as connection:
                connection.execute(
                    "INSERT INTO schedule VALUES (?,?,?,?,?,?)",
                    (
                        "CONTROLLED-RACE",
                        snapshot.material.platform,
                        snapshot.material.account_id,
                        suggested_at,
                        "OTHER",
                        snapshot.import_id,
                    ),
                )
            self.injected = True
        return super().save_draft(
            request, item, snapshot, review, slot, suggested_at, *args, **kwargs
        )


def fixture(root, case):
    catalog = load_catalog(root / "data")
    request = Request.model_validate(json.loads((root / "data/request.json").read_text("utf-8")))
    request.items = request.items[:1]
    item = request.items[0]
    item.publish_requirement.candidate_times = None
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
    if case == "normal":
        catalog.schedule.entries = []
    elif case == "insufficient":
        item.material_id = "M02"
    elif case == "blocked":
        catalog.materials[0].copyright_status = "unauthorized"
    elif case == "prefer_before":
        item.publish_requirement.reschedule_preference = "prefer_before"
    else:
        item.publish_requirement.reschedule_preference = "prefer_after"
    return catalog, request


def verify(folder, repo, result, case):
    item = result.items[0]
    expected = {
        "normal": ("completed", "saved"),
        "duplicate": ("completed", "reused"),
        "insufficient": ("needs_input", "not_attempted"),
        "blocked": ("blocked", "not_attempted"),
        "save_failure": ("failed", "failed"),
        "prefer_before": ("needs_input", "saved"),
        "save_race": ("needs_input", "saved"),
    }
    assert (item.task_status, item.draft.save_status) == expected[case], item.model_dump()
    if case in ("normal", "duplicate", "prefer_before", "save_race"):
        expected_hour = {"normal": 18, "duplicate": 18, "prefer_before": 17, "save_race": 20}[case]
        assert parse_time(item.suggested_at).hour == expected_hour
        assert item.confirmation_status == ("not_required" if expected_hour == 18 else "pending")
    trace = [json.loads(x) for x in (folder / "trace.jsonl").read_text("utf-8").splitlines()]
    actions = [x for x in trace if x["event_type"] == "model_action"]
    for action in actions:
        assert all(c["function"]["name"] in action["offered_tools"] for c in action["calls"])
    saves = [
        x for x in trace if x["event_type"] == "tool_result" and x["result"]["tool"] == "save_draft"
    ]
    if case == "save_race":
        conflict = next(x["result"] for x in saves if x["result"]["code"] == "SLOT_CONFLICT")
        assert any(conflict["observation_id"] in x["delivered_observation_ids"] for x in actions)
        queries = [
            x
            for x in trace
            if x["event_type"] == "tool_result" and x["result"]["tool"] == "query_slots"
        ]
        assert len(queries) == 2 and len(saves) == 2
    with repo.transaction() as connection:
        counts = {
            t: connection.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("drafts", "draft_history")
        }
    assert counts["drafts"] == (1 if item.draft.id else 0)
    if item.draft.id:
        assert counts["draft_history"] == 1
    (folder / "database_check.json").write_text(
        dumps(
            {
                "counts": counts,
                "current": repo.current_draft(result.request_id, item.material_id),
                "selected_at_utc": utc(parse_time(item.suggested_at))
                if item.suggested_at
                else None,
            }
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "directory": folder.name,
        "mode": result.mode,
        "case": case,
        "task_status": item.task_status,
        "save_status": item.draft.save_status,
        "suggested_at": item.suggested_at,
        "model_actions": len(actions),
    }


async def generate(args):
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists():
        raise ValueError("output must be a new directory")
    runtime = root / "runtime" / output.name
    runtime.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True)
    model = DeepSeekModel(ModelSettings.load(args.env_file)) if args.live else None
    records = []
    try:
        for mode, cases in (
            (
                "mock",
                ("normal", "insufficient", "blocked", "save_failure", "prefer_before", "save_race"),
            ),
            ("live", ("normal", "save_race") if args.live else ()),
        ):
            for case in cases:
                catalog, request = fixture(root, case)
                repository = RacingRepository if case == "save_race" else Repository
                fault = {
                    "save_race": "controlled_occupancy_before_first_save",
                    "save_failure": "save_before",
                }.get(case)
                repo = repository(runtime / f"{mode}-{case}.db", fault=fault)
                repo.apply_catalog_snapshot(catalog)
                folder = output / f"{mode}_{case}"
                result = await run_request(
                    request,
                    repo,
                    model if mode == "live" else MockModel(),
                    mode=mode,
                    output=folder,
                )
                (folder / "catalog.json").write_text(
                    catalog.model_dump_json(indent=2), encoding="utf-8", newline="\n"
                )
                records.append(verify(folder, repo, result, case))
                if mode == "mock" and case == "normal":
                    duplicate_folder = output / "mock_duplicate"
                    duplicate = await run_request(
                        request, repo, MockModel(), mode="mock", output=duplicate_folder
                    )
                    assert duplicate.items[0].draft.id == result.items[0].draft.id
                    assert duplicate.items[0].draft.revision == result.items[0].draft.revision
                    records.append(verify(duplicate_folder, repo, duplicate, "duplicate"))
                print(dumps(records[-1]), flush=True)
    finally:
        if model:
            await model.aclose()
        (output / "index.json").write_text(
            dumps({"runs": records}) + "\n", encoding="utf-8", newline="\n"
        )
        manifest = {
            p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob("*"))
            if p.is_file()
        }
        (output / "manifest.json").write_text(
            dumps(manifest) + "\n", encoding="utf-8", newline="\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path)
    asyncio.run(generate(parser.parse_args()))
