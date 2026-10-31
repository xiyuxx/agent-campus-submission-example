"""Check actual v2 evidence without calling a model or replaying business writes."""

import argparse
import hashlib
import json
from pathlib import Path

from shadow_agent.common import now, utc
from shadow_agent.contracts import FinalProposal, RequestResult, ToolResult, parse_time
from shadow_agent.model_view import tool_view
from shadow_agent.storage import Repository
from shadow_agent.tools.registry import schemas


def check(root, runtime=None):
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert index["protocol_version"] == "2.0"
    forbidden = {
        "material_id",
        "request_id",
        "run_id",
        "draft_id",
        "revision",
        "expected_revision",
        "review_observation_id",
        "slot_observation_id",
        "observation_id",
        "evidence_ids",
        "account_id",
        "rule_id",
        "source_id",
        "catalog_revision",
        "import_id",
    }

    def business(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value), forbidden.intersection(value)
            for v in value.values():
                business(v)
        elif isinstance(value, list):
            for v in value:
                business(v)

    business(schemas())
    report = []
    for entry in index["runs"]:
        folder = root / entry["case"]
        run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        output = RequestResult.model_validate_json(
            (folder / "output.json").read_text(encoding="utf-8")
        )
        assert run["run_id"] == output.run_id and run["prompt_version"] == "2.0"
        assert output.mode == entry["mode"]
        calls, observations, results = set(), set(), set()
        saved = None
        actions = 0
        for line in (folder / "trace.jsonl").read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            assert event["run_id"] == output.run_id
            if event["event_type"] == "model_action":
                actions += 1
                assert set(event["delivered_observation_ids"]).issubset(observations)
                if output.mode == "live":
                    assert event["provider"]["response_id"]
                    assert event["provider"]["usage"]["total_tokens"] > 0
                for call in event["calls"]:
                    assert call["id"] not in calls
                    calls.add(call["id"])
                    business(json.loads(call["function"]["arguments"]))
                if event["proposal"]:
                    FinalProposal.model_validate(event["proposal"])
                    business(event["proposal"])
                    if output.items[0].draft.save_status in ("saved", "reused", "updated"):
                        assert saved in event["delivered_observation_ids"]
            elif event["event_type"] == "tool_result":
                result = ToolResult.model_validate(event["result"])
                assert event["tool_call_id"] in calls and event["tool_call_id"] not in results
                results.add(event["tool_call_id"])
                observations.add(result.observation_id)
                business(tool_view(result, "Asia/Shanghai"))
                if result.tool == "save_draft" and result.ok:
                    saved = result.observation_id
                if (
                    result.tool == "query_slots"
                    and result.ok
                    and result.data["query"] == "alternatives"
                ):
                    slots = result.data["slots"]
                    assert len(slots) <= 2
                    assert len({x["direction"] for x in slots}) == len(slots)
                    assert all(x["available"] for x in slots)
        assert calls == results
        assert actions == run["model_attempts"]
        assert len(results) == run["tool_attempts"]
        if runtime is not None:
            database = ("normal" if entry["case"] == "duplicate" else entry["case"]).replace(
                "live_", "live-", 1
            )
            repo = Repository(runtime / f"{database}.db")
            item = output.items[0]
            current = repo.current_draft(output.request_id, item.material_id)
            assert (current["draft_id"] if current else None) == item.draft.id
            if current:
                assert current["revision"] == item.draft.revision
                assert current["at_utc"] == utc(parse_time(item.suggested_at))
            with repo.transaction() as connection:
                count = connection.execute("SELECT count(*) FROM drafts").fetchone()[0]
            (folder / "database_check.json").write_text(
                json.dumps(
                    {
                        "checked_at": now(),
                        "draft_count": count,
                        "draft_id": current["draft_id"] if current else None,
                        "revision": current["revision"] if current else None,
                        "validity": current["validity"] if current else None,
                        "at_utc": current["at_utc"] if current else None,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
        report.append(
            {
                "case": entry["case"],
                "mode": output.mode,
                "status": output.task_status,
                "model_attempts": actions,
                "tool_attempts": len(results),
            }
        )
    files = [p for p in root.rglob("*") if p.is_file() and p.name != "checks.json"]
    document = {
        "kind": "evidence-validation-not-a-new-model-run",
        "runs_checked": report,
        "file_sha256": {
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)
        },
        "scope": (
            "Typed outputs, local call/observation links, receipt delivery, "
            "projected fields and nearest result shape; raw HTTP bodies are not archived."
        ),
        "database_cross_check": runtime is not None,
    }
    (root / "checks.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({"checked_runs": len(report), "checked_files": len(files), "result": "PASS"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--runtime", type=Path, help="Optional local database cross-check")
    args = parser.parse_args()
    check(args.path, args.runtime)
