"""JSON automation and terminal review share the same application/storage services."""

import argparse
import asyncio
import json
import sqlite3
import sys
from importlib.resources import files
from pathlib import Path

from pydantic import ValidationError

from .common import BusinessError, dumps, new_id
from .config import Limits, ModelSettings
from .contracts import Confirmation, Request
from .models.deepseek import DeepSeekModel
from .models.mock import MockModel
from .result import exit_code, render_summary
from .service import run_request
from .storage import Repository, load_catalog


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="素材审核与发布排期 Agent；仅保存本地草稿")
    commands = root.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="完整导入四类合成资料")
    seed.add_argument("--data", type=Path, default=Path("data"))
    seed.add_argument("--db", type=Path, required=True)
    seed.add_argument("--update", action="store_true")
    seed.add_argument("--dry-run", action="store_true", help="只查看变更摘要，不写数据库")
    run = commands.add_parser("run", help="运行有界工具循环")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--mode", choices=("mock", "live"), required=True)
    run.add_argument("--env-file", type=Path)
    run.add_argument("--as-of", help="显式业务时间回放，ISO8601含时区；不修改实际执行时间")
    run.add_argument("--max-model-attempts", type=int, default=16)
    run.add_argument("--max-tool-attempts", type=int, default=16)
    run.add_argument("--item-seconds", type=float, default=180)
    run.add_argument("--request-seconds", type=float, default=540)
    run.add_argument(
        "--fault",
        choices=(
            "read_failure",
            "query_failure",
            "save_before",
            "save_once",
            "receipt_lost",
            "receipt_unknown",
        ),
        help="显式测试故障；会记录在 run.json 中，模型不能启用",
    )
    confirm = commands.add_parser("confirm", help="记录明确用户决定，不调用模型")
    confirm.add_argument("--input", type=Path, required=True)
    confirm.add_argument("--db", type=Path, required=True)
    confirm.add_argument("--as-of", help="显式业务时间回放")
    inspect = commands.add_parser("inspect-draft", help="查看数据库当前状态和历史")
    inspect.add_argument("--id", required=True)
    inspect.add_argument("--db", type=Path, required=True)
    review = commands.add_parser("review", help="交互处理当前草稿的未决确认项")
    review.add_argument("--db", type=Path, required=True)
    review.add_argument("--as-of", help="显式业务时间回放")
    demo = commands.add_parser("demo", help="隔离目录的一键演示，默认 Mock")
    demo.add_argument("--mode", choices=("mock", "live"), default="mock")
    demo.add_argument("--root", type=Path, default=Path("runtime/demo"))
    demo.add_argument("--env-file", type=Path)
    demo.add_argument(
        "--as-of",
        default="2026-11-01T00:00:00Z",
        help="演示夹具回放参考时间，默认2026-11-01；实际日志时间不变",
    )
    return root


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


async def _run(args, request: Request, *, repo=None, output=None):
    model = DeepSeekModel(ModelSettings.load(args.env_file)) if args.mode == "live" else MockModel()
    try:
        limits = Limits(
            getattr(args, "max_model_attempts", 16),
            getattr(args, "max_tool_attempts", 16),
            getattr(args, "item_seconds", 180),
            getattr(args, "request_seconds", 540),
        )
        result = await run_request(
            request,
            repo
            or Repository(
                args.db, fault=getattr(args, "fault", None), replay_at=getattr(args, "as_of", None)
            ),
            model,
            mode=args.mode,
            output=output or args.output,
            limits=limits,
        )
        print(render_summary(result), file=sys.stderr)
        print(result.model_dump_json(indent=2))
        return exit_code(result)
    finally:
        if isinstance(model, DeepSeekModel):
            await model.aclose()


def review_drafts(repo: Repository) -> dict:
    states = [x for x in repo.list_drafts() if x["validity"] == "active" and x["pending_items"]]
    if not states:
        print("当前没有可确认的待办。stale 需要重评估，已确认方案不提供撤回入口。", file=sys.stderr)
        return {"handled": 0}
    handled = 0
    for draft in states:
        payload = draft["payload"]
        material = payload["snapshot"]["material"]
        original = payload["original_requirement"]
        print(
            f"\n{material.get('title') or draft['material_id']} · "
            f"{draft['platform']} / {draft['account_id']}\n"
            f"草稿 {draft['draft_id']} 第 {draft['revision']} 版；时区 {original['timezone']}\n"
            f"原要求 {original['requested_at']}（{original['constraint']}）；"
            f"建议 {draft['at_utc']}\n"
            "仅本地草稿。拒绝任一事项会拒绝整份方案；默认暂不处理。",
            file=sys.stderr,
        )
        decisions = []
        for item in draft["pending_items"]:
            print(
                f"{item['id']}：{item['message']}\n0 暂不处理 / 1 接受 / 2 拒绝整份方案",
                file=sys.stderr,
            )
            try:
                choice = input().strip()
            except EOFError:
                choice = "0"
            if choice not in ("", "0", "1", "2"):
                choice = "0"
            if choice == "2":
                decisions = [{"item_id": item["id"], "decision": "reject"}]
                break
            decisions.append(
                {"item_id": item["id"], "decision": "accept" if choice == "1" else "skip"}
            )
        decision = Confirmation(
            operation_id=new_id("CONFIRM"),
            draft_id=draft["draft_id"],
            expected_revision=draft["revision"],
            decisions=decisions,
        )
        result = repo.confirm_draft(decision, actor_source="review")
        print(
            dumps(
                {
                    "draft_id": draft["draft_id"],
                    "skipped": result.get("skipped", False),
                    "confirmation_status": result["current_draft"]["confirmation_status"],
                }
            ),
            file=sys.stderr,
        )
        handled += int(not result.get("skipped", False))
    return {"handled": handled}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "seed":
            catalog = load_catalog(args.data)
            repo = Repository(args.db)
            preview = repo.preview_catalog_snapshot(catalog)
            print("完整目录变更预览：" + dumps(preview), file=sys.stderr)
            result = (
                preview
                if args.dry_run
                else repo.apply_catalog_snapshot(catalog, update=args.update)
            )
        elif args.command == "run":
            return asyncio.run(_run(args, Request.model_validate(read_json(args.input))))
        elif args.command == "demo":
            resources = files("shadow_agent").joinpath("fixtures")
            repo = Repository(args.root / "agent.db", replay_at=args.as_of)
            repo.apply_catalog_snapshot(load_catalog(Path(str(resources))), update=True)
            request = Request.model_validate_json(
                resources.joinpath("request.json").read_text(encoding="utf-8")
            )
            return asyncio.run(
                _run(args, request, repo=repo, output=args.root / "runs" / new_id("run"))
            )
        elif args.command == "confirm":
            result = Repository(args.db, replay_at=args.as_of).confirm_draft(
                Confirmation.model_validate(read_json(args.input))
            )
        elif args.command == "inspect-draft":
            result = Repository(args.db).inspect_draft(args.id)
        else:
            result = review_drafts(Repository(args.db, replay_at=args.as_of))
        print(dumps(result))
        return 0
    except BusinessError as error:
        print(dumps({"error": error.as_dict()}))
        print(error.message, file=sys.stderr)
        return (
            2
            if error.code
            in (
                "INVALID_CATALOG",
                "INPUT_CONFLICT",
                "CATALOG_EXISTS",
                "MISSING_API_KEY",
                "INVALID_CONFIG",
                "INVALID_LIMITS",
                "SCHEMA_VERSION",
            )
            else 1
        )
    except (ValidationError, ValueError, OSError):
        message = "输入/配置格式错误或文件不可读；已有运行目录不能覆盖"
        print(dumps({"error": {"code": "INVALID_INPUT", "message": message}}))
        print(message, file=sys.stderr)
        return 2
    except sqlite3.Error as error:
        public = Repository.database_error(error)
        print(dumps({"error": public.as_dict()}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
