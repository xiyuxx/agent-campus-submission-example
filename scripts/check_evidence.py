"""Read-only validation of selected application evidence, including current live paths."""

import argparse
import hashlib
import json
from pathlib import Path

from shadow_agent.common import dumps
from shadow_agent.contracts import Request, RequestResult, ToolResult
from shadow_agent.result import exit_code
from shadow_agent.service import source_metadata


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def events_at(root):
    return [json.loads(line) for line in (root / "trace.jsonl").read_text("utf-8").splitlines()]


def analyze(root: Path) -> dict:
    metadata = load(root / "run.json")
    request = Request.model_validate(load(root / "input.json"))
    result = RequestResult.model_validate(load(root / "output.json"))
    assert result.request_id == request.request_id == metadata["request_id"]
    assert result.mode == metadata["mode"]
    assert result.run_id == metadata["run_id"]
    assert metadata["state"] == "finished" and not metadata["evidence_error"]
    assert metadata["exit_code"] == exit_code(result)
    assert metadata["source_sha256"] == source_metadata()["source_sha256"], root
    for name in metadata["files"]:
        path = (root / name).resolve()
        assert path.is_relative_to(root.resolve()) and path.is_file(), (root, name)
    observations, calls, paired, finals = {}, set(), set(), {}
    last_step = 0
    for event in events_at(root):
        assert event["run_id"] == result.run_id and event["mode"] == result.mode
        assert event["step"] > last_step
        last_step = event["step"]
        material = event["material_id"]
        if event["event_type"] == "model_action":
            for observation in event["delivered_observation_ids"]:
                assert observations[observation] == material
            for call in event["calls"]:
                key = (material, call["id"])
                assert key not in calls
                calls.add(key)
        elif event["event_type"] == "tool_result":
            key = (material, event["tool_call_id"])
            assert key in calls and key not in paired
            paired.add(key)
            observation = ToolResult.model_validate(event["result"]).observation_id
            assert observation not in observations
            observations[observation] = material
        elif event["event_type"] == "final":
            finals[material] = event["result"]
    assert calls == paired
    for item in result.items:
        assert all(observations[x] == item.material_id for x in item.evidence_ids)
        assert finals[item.material_id] == item.model_dump(mode="json")
    return {
        "directory": root.as_posix(),
        "run_id": result.run_id,
        "mode": result.mode,
        "task_status": result.task_status,
        "source_sha256": metadata["source_sha256"],
        "base_commit": metadata["base_commit"],
        "model_attempts": metadata["model_attempts"],
        "tool_attempts": metadata["tool_attempts"],
        "exit_code": metadata["exit_code"],
        "file_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in metadata["files"]
        },
    }


def transition(events, observation):
    following = next(
        event
        for event in events
        if event["step"] > observation["step"]
        and event["event_type"] == "model_action"
        and observation["result"]["observation_id"] in event["delivered_observation_ids"]
    )
    return {
        "observation_step": observation["step"],
        "observation": observation["result"],
        "next_model_step": following["step"],
        "next_calls": [
            {"tool": x["function"]["name"], "arguments": json.loads(x["function"]["arguments"])}
            for x in following["calls"]
        ],
    }


def live_paths(root: Path) -> dict:
    paths = {}
    for name in ("live_normal", "live_save_race"):
        folder = root / name
        metadata = load(folder / "run.json")
        assert metadata["mode"] == "live" and metadata["provider_responses"]
        events = events_at(folder)
        queried = next(
            e
            for e in events
            if e["event_type"] == "tool_result" and e["result"]["tool"] == "query_slots"
        )
        paths[name] = transition(events, queried)
    normal, conflict = paths["live_normal"], paths["live_save_race"]
    assert normal["observation"]["data"]["requested"]["slots"][0]["available"]
    assert not conflict["observation"]["data"]["requested"]["slots"][0]["available"]
    assert normal["next_calls"][0]["tool"] == conflict["next_calls"][0]["tool"] == "save_draft"
    assert (
        normal["next_calls"][0]["arguments"]["suggested_at"]
        != (conflict["next_calls"][0]["arguments"]["suggested_at"])
    )
    events = events_at(root / "live_save_race")
    failed = next(
        e
        for e in events
        if e["event_type"] == "tool_result"
        and e["result"]["tool"] == "save_draft"
        and not e["result"]["ok"]
    )
    paths["save_conflict_recovery"] = transition(events, failed)
    assert paths["save_conflict_recovery"]["next_calls"][0]["tool"] == "query_slots"
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--live-directory", type=Path)
    parser.add_argument(
        "--output", type=Path, help="Write a new validation report; never overwrite"
    )
    args = parser.parse_args()
    # Historical test-suite run.json files have different contracts. Select an explicit
    # application evidence tree rather than silently skipping malformed application runs.
    folders = sorted(p.parent for p in args.directory.rglob("run.json"))
    assert folders, "No application runs in selected directory"
    records = [analyze(folder) for folder in folders]
    paths = None
    if args.live_directory:
        for name in ("live_normal", "live_save_race"):
            records.append(analyze(args.live_directory / name))
        paths = live_paths(args.live_directory)
    report = {"kind": "read-only-evidence-validation", "actual_runs": records, "live_paths": paths}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(dumps(report) + "\n")
    print(f"Validated {len(records)} application runs; live paths checked: {paths is not None}.")


if __name__ == "__main__":
    main()
