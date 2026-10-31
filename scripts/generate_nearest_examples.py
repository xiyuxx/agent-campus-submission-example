"""Generate actual v2 runs in new directories; real API calls require --live."""

import argparse
import asyncio
import json
from pathlib import Path

from shadow_agent.common import dumps, utc
from shadow_agent.config import ModelSettings
from shadow_agent.contracts import Catalog, Request, parse_time
from shadow_agent.models.deepseek import DeepSeekModel
from shadow_agent.models.mock import MockModel
from shadow_agent.service import run_request
from shadow_agent.storage import Repository, load_catalog


def fixture(root, name):
    value = load_catalog(root / "data").model_dump(mode="json")
    value["materials"][0]["copyright_status"] = "authorized"
    nodes = ["2026-11-03T17:00:00+08:00", "2026-11-03T18:00:00+08:00", "2026-11-03T19:00:00+08:00"]
    occupied = []
    if name == "two_sides":
        nodes += ["2026-11-03T17:30:00+08:00", "2026-11-03T18:30:00+08:00"]
        occupied = [
            "2026-11-03T17:30:00+08:00",
            "2026-11-03T18:00:00+08:00",
            "2026-11-03T18:30:00+08:00",
        ]
    elif name == "cross_day":
        nodes = ["2026-11-02T23:00:00+08:00", "2026-11-04T09:00:00+08:00"]
        occupied = ["2026-11-03T18:00:00+08:00"]
    elif name == "insufficient":
        value["materials"][0]["copyright_status"] = None
        value["supplements"] = []
    value["schedule"]["allowed_slots"] = [
        {"platform": "douyin", "account_id": "brand_main", "at": at} for at in nodes
    ]
    value["schedule"]["entries"] = [
        {
            "entry_id": f"OCCUPIED-{i}",
            "material_id": "OTHER",
            "platform": "douyin",
            "account_id": "brand_main",
            "at": at,
        }
        for i, at in enumerate(occupied)
    ]
    request = Request.model_validate(
        {
            "request_id": f"REQ-NEAREST-V2-{name}",
            "items": [
                {
                    "material_id": "M01",
                    "publish_requirement": {
                        "requested_at": "2026-11-03T18:00:00+08:00",
                        "timezone": "Asia/Shanghai",
                        "constraint": "hard",
                    },
                }
            ],
        }
    )
    return Catalog.model_validate(value), request


def inspect_run(directory, repo, result, name):
    item = result.items[0]
    events = [
        json.loads(line)
        for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    queries = [
        e["result"]["data"]
        for e in events
        if e["event_type"] == "tool_result"
        and e["result"]["tool"] == "query_slots"
        and e["result"]["ok"]
    ]
    if name in ("two_sides", "cross_day"):
        nearest = next(q for q in queries if q["query"] == "alternatives")
        assert len(nearest["slots"]) == 2
        assert {s["direction"] for s in nearest["slots"]} == {"before", "after"}
        assert utc(parse_time(item.suggested_at)) in {s["at"] for s in nearest["slots"]}
        assert item.confirmation_status == "pending"
    elif name == "insufficient":
        assert item.review_status == "needs_info" and item.draft.id is None
    elif name == "save_failure":
        assert item.draft.save_status == "failed" and item.task_status == "failed"
        assert repo.current_draft(result.request_id, "M01") is None
    elif name == "duplicate":
        assert item.draft.save_status == "reused" and item.draft.revision == 1
    else:
        assert item.task_status == "completed" and item.suggested_at == "2026-11-03T18:00:00+08:00"
    for e in events:
        for call in e.get("calls", []):
            args = json.loads(call["function"]["arguments"])
            assert not {
                "material_id",
                "request_id",
                "expected_revision",
                "review_observation_id",
                "slot_observation_id",
            }.intersection(args)
    return {
        "case": directory.name,
        "mode": result.mode,
        "task_status": item.task_status,
        "suggested_at": item.suggested_at,
        "save_status": item.draft.save_status,
        "files": [
            str((directory / name).name)
            for name in (
                "input.json",
                "output.json",
                "trace.jsonl",
                "run.json",
                "summary.md",
                "catalog.json",
            )
        ],
    }


async def generate(args):
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists():
        raise ValueError("output must be a new directory")
    settings = ModelSettings.load(args.env_file) if args.live else None
    output.mkdir(parents=True)
    runtime = root / "runtime" / output.name
    runtime.mkdir(parents=True, exist_ok=False)
    records = []
    for name in ("normal", "two_sides", "cross_day", "insufficient", "save_failure"):
        catalog, request = fixture(root, name)
        repo = Repository(
            runtime / f"{name}.db", fault="save_before" if name == "save_failure" else None
        )
        repo.apply_catalog_snapshot(catalog)
        folder = output / name
        result = await run_request(request, repo, MockModel(), mode="mock", output=folder)
        (folder / "catalog.json").write_text(
            catalog.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        records.append(inspect_run(folder, repo, result, name))
        if name == "normal":
            folder = output / "duplicate"
            duplicate = await run_request(request, repo, MockModel(), mode="mock", output=folder)
            assert duplicate.items[0].draft.id == result.items[0].draft.id
            (folder / "catalog.json").write_text(
                catalog.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
            )
            records.append(inspect_run(folder, repo, duplicate, "duplicate"))
    if args.live:
        model = DeepSeekModel(settings)
        try:
            for name in ("normal", "two_sides"):
                catalog, request = fixture(root, name)
                repo = Repository(runtime / f"live-{name}.db")
                repo.apply_catalog_snapshot(catalog)
                folder = output / f"live_{name}"
                result = await run_request(request, repo, model, mode="live", output=folder)
                (folder / "catalog.json").write_text(
                    catalog.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
                )
                records.append(inspect_run(folder, repo, result, name))
        finally:
            await model.aclose()
    (output / "index.json").write_text(
        dumps({"protocol_version": "2.0", "runs": records}) + "\n", encoding="utf-8", newline="\n"
    )
    print(dumps({"output": str(output), "runs": records}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path)
    asyncio.run(generate(parser.parse_args()))
