"""Corrupt real evidence and ensure the review command rejects misleading records."""

import asyncio
import json
import runpy
from pathlib import Path

import pytest

from shadow_agent.models.mock import MockModel
from shadow_agent.service import run_request

analyze = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_evidence.py"))[
    "analyze"
]


@pytest.mark.parametrize("corruption", ["future_observation", "final_result", "source", "missing"])
def test_evidence_check_rejects_corruption(repo, job, tmp_path, corruption):
    folder = tmp_path / "evidence"
    asyncio.run(run_request(job, repo, MockModel(), mode="mock", output=folder))
    analyze(folder)
    if corruption == "future_observation":
        path = folder / "trace.jsonl"
        events = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        observation = next(e for e in events if e["event_type"] == "tool_result")
        events[0]["delivered_observation_ids"] = [observation["result"]["observation_id"]]
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    elif corruption == "final_result":
        path = folder / "output.json"
        output = json.loads(path.read_text("utf-8"))
        output["items"][0]["risk_reasons"] = []
        output["items"][0]["original_requested_at"] = "2026-11-03T19:00:00+08:00"
        path.write_text(json.dumps(output), encoding="utf-8")
    elif corruption == "source":
        path = folder / "run.json"
        metadata = json.loads(path.read_text("utf-8"))
        metadata["source_sha256"] = "0" * 64
        path.write_text(json.dumps(metadata), encoding="utf-8")
    else:
        (folder / "summary.md").unlink()
    with pytest.raises((AssertionError, KeyError)):
        analyze(folder)
