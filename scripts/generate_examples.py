"""Generate actual isolated application runs; never populate evidence from static examples."""

import argparse
import asyncio
from pathlib import Path

from shadow_agent.common import dumps, new_id
from shadow_agent.config import ModelSettings
from shadow_agent.contracts import Confirmation, Request, parse_time
from shadow_agent.models.deepseek import DeepSeekModel
from shadow_agent.models.mock import MockModel
from shadow_agent.service import run_request
from shadow_agent.storage import Repository, load_catalog

CASES = (
    "normal",
    "insufficient",
    "rule_risk",
    "blocked",
    "slot_conflict",
    "duplicate",
    "save_failure",
    "tool_failure",
    "changed_occupancy",
    "confirmation",
    "receipt_lost",
    "receipt_unknown",
)


def request_for(name: str, material: str = "M01") -> Request:
    return Request.model_validate(
        {
            "schema_version": "1.0",
            "request_id": f"REQ-EVIDENCE-{name}",
            "items": [
                {
                    "material_id": material,
                    "publish_requirement": {
                        "requested_at": "2026-11-03T18:00:00+08:00",
                        "timezone": "Asia/Shanghai",
                        "constraint": "hard",
                        "candidate_times": [
                            "2026-11-03T19:00:00+08:00",
                            "2026-11-03T20:00:00+08:00",
                        ],
                    },
                }
            ],
        }
    )


def write_json(path: Path, value) -> None:
    path.write_text(dumps(value), encoding="utf-8", newline="\n")


async def generate(args):
    model = DeepSeekModel(ModelSettings.load(args.env_file)) if args.mode == "live" else MockModel()
    runtime = Path("runtime/evidence") / new_id("batch")
    runtime.mkdir(parents=True, exist_ok=False)
    prefix = "live_" if args.mode == "live" else ""
    records = []
    try:
        for name in args.cases:
            catalog = load_catalog(Path("data"))
            if name not in ("slot_conflict", "changed_occupancy", "confirmation"):
                catalog.schedule.entries = []
            if name == "rule_risk":
                catalog.materials[0].caption = "示例最强功能，需人工核实"
            material = "M02" if name == "insufficient" else "M03" if name == "blocked" else "M01"
            job = request_for(name, material)
            if name == "slot_conflict":
                job.items[0].publish_requirement.candidate_times = job.items[
                    0
                ].publish_requirement.candidate_times[:1]
            fault = {
                "save_failure": "save_before",
                "tool_failure": "query_failure",
                "receipt_lost": "receipt_lost",
                "receipt_unknown": "receipt_unknown",
            }.get(name)
            repo = Repository(runtime / f"{name}.db", fault=fault)
            seeded = repo.apply_catalog_snapshot(catalog)
            target = args.output / (prefix + name)
            before = None
            confirmation = None
            changed_import = None
            if name in ("duplicate", "changed_occupancy", "confirmation"):
                before = await run_request(
                    job,
                    repo,
                    model,
                    mode=args.mode,
                    output=args.output / (prefix + name + "_before"),
                )
                draft = before.items[0].draft
                if not draft.id:
                    raise RuntimeError(f"{name}: prerequisite run did not save a draft")
                if name in ("changed_occupancy", "confirmation"):
                    confirmation = repo.confirm_draft(
                        Confirmation(
                            operation_id=f"CONFIRM-{name}",
                            draft_id=draft.id,
                            expected_revision=draft.revision,
                            decisions=[{"item_id": "change_time", "decision": "accept"}],
                        )
                    )
                if name == "changed_occupancy":
                    actual = repo.current_draft(job.request_id, material)
                    extra = catalog.schedule.entries[0].model_copy(deep=True)
                    extra.entry_id, extra.at = "S2", parse_time(actual["at_utc"])
                    catalog.schedule.entries.append(extra)
                    catalog.schedule.schedule_version += 1
                    changed_import = repo.apply_catalog_snapshot(catalog, update=True)
            result = await run_request(job, repo, model, mode=args.mode, output=target)
            states = repo.list_drafts()
            observed = {
                "draft_count": len(states),
                "drafts": [
                    {
                        k: x[k]
                        for k in (
                            "draft_id",
                            "request_id",
                            "material_id",
                            "revision",
                            "validity",
                            "at_utc",
                            "confirmation_status",
                        )
                    }
                    for x in states
                ],
                "history_count": sum(
                    len(repo.inspect_draft(x["draft_id"])["history"]) for x in states
                ),
            }
            write_json(target / "database_state.json", observed)
            write_json(target / "seed_result.json", seeded)
            catalog_path = target / "catalog"
            catalog_path.mkdir()
            for key, value in catalog.model_dump(mode="json").items():
                write_json(catalog_path / f"{key}.json", value)
            if confirmation:
                write_json(target / "confirmation_result.json", confirmation)
            if changed_import:
                write_json(target / "update_result.json", changed_import)
            item = result.items[0]
            record = {
                "case": name,
                "mode": args.mode,
                "task_status": result.task_status,
                "review_status": item.review_status,
                "save_status": item.draft.save_status,
                "draft_id": item.draft.id,
                "revision": item.draft.revision,
                "suggested_at": item.suggested_at,
                **observed,
            }
            records.append(record)
            print(dumps(record), flush=True)
            if (
                args.mode == "live"
                and item.error
                and item.error["code"]
                in ("MODEL_HTTP", "MODEL_TRANSPORT", "MODEL_REASONING_UNEXPECTED")
            ):
                raise RuntimeError(
                    "live model unavailable/configuration failed; actual failure evidence retained"
                )
            if name == "duplicate":
                assert observed["draft_count"] == 1
                assert item.draft.id == before.items[0].draft.id and item.draft.revision == 1
                assert item.draft.save_status == "reused"
            elif name == "changed_occupancy":
                assert item.draft.id == before.items[0].draft.id and item.draft.revision == 2
                assert item.suggested_at != before.items[0].suggested_at
                assert item.confirmation_status == "pending"
            elif name == "normal":
                assert result.task_status == "completed" and item.review_status == "passed"
            elif name in ("save_failure", "tool_failure"):
                assert result.task_status == "failed" and observed["draft_count"] == 0
            elif name == "insufficient":
                assert item.review_status == "needs_info" and observed["draft_count"] == 0
            elif name == "slot_conflict":
                assert item.suggested_at == "2026-11-03T19:00:00+08:00"
                assert item.confirmation_status == "pending"
        return records
    finally:
        if isinstance(model, DeepSeekModel):
            await model.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "live"), required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("examples"))
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    asyncio.run(generate(args))


if __name__ == "__main__":
    main()
