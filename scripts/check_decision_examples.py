"""Read-only checks of evidence hashes, tool delivery, outcomes and database snapshots."""

import argparse
import hashlib
import json
from pathlib import Path

from shadow_agent.common import utc
from shadow_agent.contracts import RequestResult, ToolResult, parse_time


def check(root):
    manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    for name, expected in manifest.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("manifest path leaves evidence directory")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
    index = json.loads((root / "index.json").read_text("utf-8"))
    for record in index["runs"]:
        folder = root / record["directory"]
        result = RequestResult.model_validate_json((folder / "output.json").read_text("utf-8"))
        assert result.mode == record["mode"]
        item = result.items[0]
        assert (item.task_status, item.draft.save_status) == (
            record["task_status"],
            record["save_status"],
        )
        events = [json.loads(x) for x in (folder / "trace.jsonl").read_text("utf-8").splitlines()]
        calls, delivered, successful_saves = set(), set(), set()
        for event in events:
            if event["event_type"] == "model_action":
                delivered.update(event["delivered_observation_ids"])
                for call in event["calls"]:
                    assert call["id"] not in calls
                    calls.add(call["id"])
                    assert call["function"]["name"] in event["offered_tools"]
                    args = json.loads(call["function"]["arguments"])
                    assert (
                        not {
                            "request_id",
                            "material_id",
                            "expected_revision",
                            "draft_id",
                            "review_observation_id",
                            "slot_observation_id",
                        }
                        & args.keys()
                    )
            elif event["event_type"] == "tool_result":
                observation = ToolResult.model_validate(event["result"])
                assert event["tool_call_id"] in calls
                if observation.tool == "save_draft" and observation.ok:
                    successful_saves.add(observation.observation_id)
        assert successful_saves <= delivered
        db = json.loads((folder / "database_check.json").read_text("utf-8"))
        if "counts" in record:
            assert db["counts"] == record["counts"]
        else:
            assert db["counts"]["drafts"] == (1 if item.draft.id else 0)
        if item.draft.id:
            assert db["current"]["draft_id"] == item.draft.id
            assert db["current"]["revision"] == item.draft.revision
            if item.suggested_at:
                assert db["current"]["at_utc"] == utc(parse_time(item.suggested_at))
    print(json.dumps({"verified_runs": len(index["runs"]), "verified_files": len(manifest)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    check(parser.parse_args().directory)
