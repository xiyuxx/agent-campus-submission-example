"""Fresh review-remediation runs; --live explicitly enables eight real-model samples."""

import argparse
import asyncio
import hashlib
from pathlib import Path

from shadow_agent.common import dumps, new_id
from shadow_agent.config import ModelSettings
from shadow_agent.contracts import Request, parse_time
from shadow_agent.models.deepseek import DeepSeekModel
from shadow_agent.models.mock import MockModel
from shadow_agent.service import run_request
from shadow_agent.storage import Repository, load_catalog

MOCK_CASES = (
    "normal",
    "missing",
    "past",
    "insufficient",
    "rule_risk",
    "blocked",
    "save_failure",
    "save_race",
    "receipt_unknown",
    "receipt_lost",
    "supplement",
)
LIVE_CASES = MOCK_CASES[:8]


class RacingRepository(Repository):
    injected = False

    def save_draft(self, request, item, snapshot, review, slots, at, *args, **kwargs):
        if not self.injected:
            with self.transaction(write=True) as db:
                db.execute(
                    "INSERT INTO schedule VALUES (?,?,?,?,?,?)",
                    (
                        "CONTROLLED-RACE",
                        snapshot.material.platform,
                        snapshot.material.account_id,
                        at,
                        "OTHER",
                        snapshot.import_id,
                    ),
                )
            self.injected = True
        return super().save_draft(request, item, snapshot, review, slots, at, *args, **kwargs)


def fixture(case):
    catalog = load_catalog(Path("data"))
    request = Request.model_validate_json(Path("data/request.json").read_text("utf-8"))
    request.request_id = "REVIEW-" + case
    request.items = request.items[:1]
    request.items[0].publish_requirement.reschedule_preference = "prefer_after"
    request.items[0].publish_requirement.candidate_times = [
        parse_time("2026-11-03T19:00:00+08:00"),
        parse_time("2026-11-03T20:00:00+08:00"),
    ]
    catalog.materials[0].copyright_status = "authorized"
    if case == "normal":
        catalog.schedule.entries = []
    elif case == "missing":
        for field in ("title", "type", "media_ref", "caption", "source_asset_id"):
            setattr(catalog.materials[0], field, None)
        catalog.supplements = []
    elif case == "past":
        request.items[0].publish_requirement.requested_at = parse_time("2000-01-01T18:00:00+08:00")
    elif case == "insufficient":
        request.items[0].material_id = "M02"
    elif case == "rule_risk":
        catalog.materials[0].caption = "示例最强效果，需要核实"
    elif case == "blocked":
        catalog.materials[0].copyright_status = "unauthorized"
    elif case == "supplement":
        catalog.materials[0].title = None
        catalog.materials[0].media_ref = None
        catalog.supplements[0].fields.update(title="补充标题", media_ref="synthetic://fixed.mp4")
    return catalog, request


async def generate(args):
    args.output.mkdir(parents=True, exist_ok=False)
    runtime = Path("runtime") / new_id("review")
    runtime.mkdir(parents=True)
    live = DeepSeekModel(ModelSettings.load(args.env_file)) if args.live else None
    records = []

    async def perform(case, mode, request, repo, catalog):
        folder = args.output / f"{mode}_{case}"
        result = await run_request(
            request, repo, live if mode == "live" else MockModel(), mode=mode, output=folder
        )
        item = result.items[0]
        with repo.transaction() as db:
            counts = {
                t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("drafts", "draft_history", "confirmations")
            }
        snapshot = {
            "counts": counts,
            "current": repo.current_draft(request.request_id, item.material_id),
        }
        (folder / "database_check.json").write_text(
            dumps(snapshot) + "\n", encoding="utf-8", newline="\n"
        )
        (folder / "catalog.json").write_text(
            catalog.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        records.append(
            {
                "case": case,
                "directory": folder.name,
                "mode": mode,
                "task_status": result.task_status,
                "save_status": item.draft.save_status,
                "review_status": item.review_status,
                "draft_id": item.draft.id,
                "proposed_plan": item.proposed_plan.model_dump() if item.proposed_plan else None,
                "counts": counts,
            }
        )
        print(dumps(records[-1]), flush=True)
        expected = {
            "normal": ("completed", "saved"),
            "duplicate": ("completed", "reused"),
            "cross_request": ("needs_input", "not_attempted"),
            "explicit_repeat": ("needs_input", "saved"),
            "explicit_repeat_again": ("needs_input", "reused"),
            "missing": ("needs_input", "not_attempted"),
            "past": ("needs_input", "not_attempted"),
            "insufficient": ("needs_input", "not_attempted"),
            "blocked": ("blocked", "not_attempted"),
            "save_failure": ("failed", "failed"),
            "receipt_unknown": ("incomplete", "unknown"),
        }.get(case, ("needs_input", "saved"))
        assert (result.task_status, item.draft.save_status) == expected, records[-1]
        if case == "save_failure":
            assert item.proposed_plan and item.proposed_plan.persistence_status == "not_saved"
            assert counts["drafts"] == 0
        if case == "cross_request":
            assert item.related_drafts and item.draft.id is None and counts["drafts"] == 1
        if case == "missing":
            assert item.review_status == "needs_info" and counts["drafts"] == 0
        if case == "save_race":
            assert item.suggested_at == "2026-11-03T20:00:00+08:00" and counts["drafts"] == 1

    try:
        for mode, cases in (("mock", MOCK_CASES), ("live", LIVE_CASES if args.live else ())):
            for case in cases:
                catalog, request = fixture(case)
                cls = RacingRepository if case == "save_race" else Repository
                fault = {
                    "save_failure": "save_before",
                    "save_race": "controlled_occupancy_before_save",
                    "receipt_lost": "receipt_lost",
                    "receipt_unknown": "receipt_unknown",
                }.get(case)
                repo = cls(runtime / f"{mode}_{case}.db", fault=fault, replay_at=args.as_of)
                repo.apply_catalog_snapshot(catalog)
                await perform(case, mode, request, repo, catalog)
                if case == "normal" and mode == "mock":
                    await perform("duplicate", mode, request, repo, catalog)
                    other = request.model_copy(
                        deep=True, update={"request_id": "ACCIDENTAL-NEW-ID"}
                    )
                    await perform("cross_request", mode, other, repo, catalog)
                    other = request.model_copy(deep=True, update={"request_id": "EXPLICIT-SECOND"})
                    other.items[0].new_publication = True
                    await perform("explicit_repeat", mode, other, repo, catalog)
                    await perform("explicit_repeat_again", mode, other, repo, catalog)
    finally:
        if live:
            await live.aclose()
        (args.output / "index.json").write_text(
            dumps({"runs": records}) + "\n", encoding="utf-8", newline="\n"
        )
        manifest = {
            p.relative_to(args.output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(args.output.rglob("*"))
            if p.is_file()
        }
        (args.output / "manifest.json").write_text(
            dumps(manifest) + "\n", encoding="utf-8", newline="\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--as-of", help="Explicit historical business-time replay")
    asyncio.run(generate(parser.parse_args()))
