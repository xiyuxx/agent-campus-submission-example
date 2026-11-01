"""Operator CLI: expose decisions and outcomes while keeping protocol IDs in details."""

import asyncio
import json
import sys
from pathlib import Path
from time import monotonic

from .common import BusinessError, dumps, new_id
from .config import Limits, ModelSettings
from .contracts import BusinessFacts, Confirmation, PublishRequirement, RequestResult
from .models.deepseek import DeepSeekModel
from .models.mock import MockModel
from .result import EXIT_CODES, exit_code, render_summary
from .service import run_request
from .trace import atomic_write


def add_work_parser(commands):
    work = commands.add_parser("work", help="发布前准备工作台：检查、补资料、确认与交接")
    work.add_argument("--db", type=Path, required=True)
    work.add_argument("--root", type=Path, default=Path("runtime/work"))
    work.add_argument("--mode", choices=("mock", "live"), default="mock")
    work.add_argument("--env-file", type=Path)
    work.add_argument("--as-of", help="显式业务时间回放；实际执行时间不变")
    work.add_argument(
        "--action",
        choices=(
            "list",
            "check",
            "edit",
            "supplement",
            "confirm",
            "cancel",
            "reopen",
            "export",
            "detail",
        ),
    )
    work.add_argument("--material", action="append", default=[], help="素材 ID；批量检查最多三项")
    work.add_argument("--at", help="用户明确指定的发布时间，ISO8601 含偏移")
    work.add_argument("--timezone", help="IANA 时区")
    work.add_argument("--constraint", choices=("hard", "soft"))
    work.add_argument("--candidate", action="append", help="授权查询的候选时刻，可重复传入")
    work.add_argument("--no-alternatives", action="store_true", help="不允许建议替代时间")
    work.add_argument("--decision", choices=("accept", "reject", "skip"))
    work.add_argument("--fields", type=Path, help="补充业务字段的 JSON 文件")
    work.add_argument("--file", type=Path, help="导出路径；必须为新文件")
    migrate = commands.add_parser("migrate", help="将已有数据库原子迁移到当前版本")
    migrate.add_argument("--db", type=Path, required=True)


def select_rows(repo, materials):
    if not materials or len(materials) > 3 or len(set(materials)) != len(materials):
        raise BusinessError("INVALID_INPUT", "请选择一至三项不同素材")
    rows = {row["material_id"]: row for row in repo.task_worklist()}
    if any(material not in rows for material in materials):
        raise BusinessError("MATERIAL_NOT_FOUND", "所选素材不在当前资料目录中")
    return [rows[material] for material in materials]


def requirement_for(row, args):
    value = dict(row["requirement"] or {})
    for name, option in (
        ("requested_at", "at"),
        ("timezone", "timezone"),
        ("constraint", "constraint"),
    ):
        setting = getattr(args, option, None)
        if setting is not None:
            value[name] = setting
    if args.no_alternatives and args.candidate is not None:
        raise BusinessError("INVALID_INPUT", "候选列表与不建议改期不能同时选择")
    if args.no_alternatives:
        value["candidate_times"] = []
    elif args.candidate is not None:
        value["candidate_times"] = args.candidate
    return PublishRequirement.model_validate(value)


async def check_rows(repo, rows, args, *, reopen=False):
    # Validate the entire user selection before making any requirement changes.
    requirements = [requirement_for(row, args) for row in rows]
    model = DeepSeekModel(ModelSettings.load(args.env_file)) if args.mode == "live" else MockModel()
    results = []
    deadline = monotonic() + 540
    try:
        for row, requirement in zip(rows, requirements, strict=True):
            remaining = deadline - monotonic()
            if remaining <= 0:
                results.append(
                    {
                        "material_id": row["material_id"],
                        "task_status": "incomplete",
                        "error": {"code": "BUDGET_EXHAUSTED", "message": "批量执行时间耗尽"},
                    }
                )
                continue
            try:
                request = repo.prepare_task(
                    row["material_id"],
                    requirement,
                    expected_revision=row["revision"],
                    reopen=reopen,
                )
            except BusinessError as error:
                results.append(
                    {
                        "material_id": row["material_id"],
                        "task_status": "needs_input",
                        "error": error.as_dict(),
                    }
                )
                continue
            output = args.root / "runs" / new_id("run")
            result = await run_request(
                request,
                repo,
                model,
                mode=args.mode,
                output=output,
                limits=Limits(request_seconds=min(540, remaining)),
            )
            if getattr(args, "interactive", False):
                for current in repo.task_worklist():
                    if current["material_id"] == row["material_id"]:
                        describe(current)
            else:
                print(render_summary(result), file=sys.stderr)
            results.append(
                {
                    "material_id": row["material_id"],
                    "output": str(output),
                    "result": result.model_dump(mode="json"),
                }
            )
    finally:
        if isinstance(model, DeepSeekModel):
            await model.aclose()
    return {"mode": args.mode, "runs": results, "worklist": repo.task_worklist()}


def confirm_row(repo, row, decision):
    draft = row["draft"]
    if not draft or draft["validity"] != "active" or row["state"] not in ("等你确认", "可交接"):
        raise BusinessError("DRAFT_INACTIVE", "当前方案需要先重新检查，不能确认旧方案")
    if not draft["pending_items"]:
        return {"reused": True, "message": "当前方案无需进一步确认", "current_draft": draft}
    if decision is None:
        raise BusinessError("INVALID_INPUT", "请选择接受、拒绝或暂不处理")
    return repo.confirm_draft(
        Confirmation(
            operation_id=new_id("CONFIRM"),
            draft_id=draft["draft_id"],
            expected_revision=draft["revision"],
            decisions=[{"item_id": p["id"], "decision": decision} for p in draft["pending_items"]],
        ),
        actor_source="review",
    )


def export_handoff(repo, path):
    if path is None or path.exists():
        raise BusinessError("INVALID_INPUT", "请提供新的交接文件路径，已有文件不能覆盖")
    result = repo.handoff()
    # Exclusive creation also protects against a competing writer after the initial check.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return {"file": str(path), "count": len(result["items"]), "notice": result["notice"]}


def work_exit_code(result):
    if "continued" in result:
        return work_exit_code(result["continued"])
    codes = []
    for run in result.get("runs", []):
        if "result" in run:
            codes.append(exit_code(RequestResult.model_validate(run["result"])))
        else:
            codes.append(EXIT_CODES[run["task_status"]])
    return next((code for code in (1, 2, 5, 4, 3) if code in codes), 0)


def dispatch(repo, args):
    if args.action == "list":
        return {"items": repo.task_worklist(), "notice": "仅本地草稿；没有真实发布能力"}
    if args.action == "export":
        return export_handoff(repo, args.file)
    rows = select_rows(repo, args.material)
    if args.action in ("check", "edit", "reopen"):
        for row in rows:
            requirement_for(row, args)
        if args.action == "edit" and not any(
            (
                args.at,
                args.timezone,
                args.constraint,
                args.candidate is not None,
                args.no_alternatives,
            )
        ):
            raise BusinessError("INVALID_INPUT", "请提供要修改的时间要求")
        return asyncio.run(check_rows(repo, rows, args, reopen=args.action == "reopen"))
    if len(rows) != 1:
        raise BusinessError("INVALID_INPUT", "此操作一次只处理一项素材")
    row = rows[0]
    if args.action == "detail":
        return row
    if args.action == "supplement":
        if args.fields is None:
            raise BusinessError("INVALID_INPUT", "请提供业务字段 JSON 文件")
        if args.mode == "live":
            ModelSettings.load(args.env_file)
        fields = json.loads(args.fields.read_text("utf-8-sig"))
        version = repo.read_material_basic(row["material_id"]).material.material_version
        saved = repo.supplement_material(
            row["material_id"], fields, expected_material_version=version
        )
        refreshed = select_rows(repo, args.material)
        return {"supplement": saved, "continued": asyncio.run(check_rows(repo, refreshed, args))}
    if not row["task_id"]:
        raise BusinessError("TASK_NOT_FOUND", "请先开始检查，建立发布任务")
    if args.action == "cancel":
        return repo.cancel_task(row["task_id"], expected_revision=row["revision"])
    return confirm_row(repo, row, args.decision)


def ask(prompt):
    print(prompt, file=sys.stderr)
    try:
        return input().strip()
    except EOFError:
        return ""


def describe(row):
    print(
        f"\n{row['title'] or row['material_id']} · {row['platform']}/{row['account_id']}"
        f" · {row['state']}",
        file=sys.stderr,
    )
    requirement = row["requirement"] or {}
    print(
        f"要求：{requirement.get('requested_at') or '待补充'} · "
        f"{requirement.get('timezone') or '时区待补充'} · "
        f"{requirement.get('constraint') or '约束待补充'}",
        file=sys.stderr,
    )
    draft = row["draft"]
    if draft:
        print(
            f"本地保存方案：{draft['at_utc']} · {draft['validity']} · "
            f"确认状态 {draft['confirmation_status']}",
            file=sys.stderr,
        )
        if row["state"] == "修改未完成":
            print("以上为旧方案，新要求尚未完成保存；当前不能交接。", file=sys.stderr)
        if draft["validity"] != "active":
            print("该方案已失效，时间和风险需重新检查。", file=sys.stderr)
        for pending in draft["pending_items"]:
            print("待确认：" + pending["message"], file=sys.stderr)
    else:
        print("尚无已保存草稿。", file=sys.stderr)
    last = row["last_result"]
    if last:
        for risk in last["risk_reasons"]:
            print("审核记录：" + risk["message"], file=sys.stderr)
        for pending in last["pending_items"]:
            if pending["kind"] == "input":
                print("待补充：" + pending["message"], file=sys.stderr)
        if last.get("error"):
            print("上次运行：" + last["error"]["message"], file=sys.stderr)


def interactive(repo, args):
    args.interactive = True
    print(
        "发布前准备工作台 · 仅保存本地草稿\n"
        + (
            "离线 Mock：验证流程，不证明真实模型决策。" if args.mode == "mock" else "真实模型模式。"
        ),
        file=sys.stderr,
    )
    while True:
        rows = repo.task_worklist()
        for number, row in enumerate(rows, 1):
            print(
                f"{number}. {row['title'] or row['material_id']} · "
                f"{row['account_id']} · {row['state']}",
                file=sys.stderr,
            )
        selection = ask("选择素材序号（批量检查可用逗号，最多三项）；e 导出交接清单；回车退出")
        if not selection:
            return {"closed": True}
        if selection == "e":
            path = ask("输入新的交接 JSON 文件路径；回车取消")
            if path:
                try:
                    print(dumps(export_handoff(repo, Path(path))), file=sys.stderr)
                except (BusinessError, OSError) as error:
                    print(str(error), file=sys.stderr)
            continue
        try:
            indexes = [int(x) - 1 for x in selection.split(",")]
            if not 1 <= len(indexes) <= 3 or len(set(indexes)) != len(indexes):
                raise ValueError
            if any(index < 0 or index >= len(rows) for index in indexes):
                raise ValueError
            selected = [rows[index] for index in indexes]
        except ValueError:
            print("请选择一至三个不同的有效序号。", file=sys.stderr)
            continue
        for row in selected:
            describe(row)
        action = ask(
            "1 检查/继续  2 修改时间  3 补资料并继续  4 确认方案  "
            "5 取消任务  6 恢复/重新安排  7 技术详情  回车返回"
        )
        if not action:
            continue
        if len(selected) > 1 and action != "1":
            print("此操作请只选择一项。", file=sys.stderr)
            continue
        args.material = [row["material_id"] for row in selected]
        args.at = args.timezone = args.constraint = args.candidate = None
        args.no_alternatives = False
        if action in ("2", "6") or (
            action == "1" and len(selected) == 1 and not selected[0]["requirement"]
        ):
            existing = selected[0]["requirement"] or {}
            args.at = (
                ask(f"发布时间，含时区偏移（回车保留 {existing.get('requested_at') or '未知'}）")
                or None
            )
            args.timezone = (
                ask(f"IANA 时区（回车保留 {existing.get('timezone') or '未知'}）") or None
            )
            args.constraint = (
                ask(f"hard/soft（回车保留 {existing.get('constraint') or '未知'}）") or None
            )
            candidates = ask(
                "替代范围：回车保留/使用账号预设，none 不建议改期，或逗号分隔 ISO 时间"
            )
            args.no_alternatives = candidates == "none"
            args.candidate = candidates.split(",") if candidates and candidates != "none" else None
        mapping = {
            "1": "check",
            "2": "edit",
            "3": "supplement",
            "4": "confirm",
            "5": "cancel",
            "6": "reopen",
            "7": "detail",
        }
        args.action = mapping.get(action)
        if args.action is None:
            continue
        if action == "3":
            fields = ask('输入补充字段 JSON（如 {"copyright_status":"authorized"}）；回车取消')
            if not fields:
                continue
            try:
                BusinessFacts.model_validate_json(fields)
            except ValueError:
                print("业务字段格式不正确。", file=sys.stderr)
                continue
            args.fields = args.root / "inputs" / (new_id("supplement") + ".json")
            args.fields.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(args.fields, fields)
        if action == "4":
            decision = ask("1 接受上述全部待确认事项 / 2 拒绝整份方案 / 回车暂不处理")
            args.decision = {"1": "accept", "2": "reject"}.get(decision, "skip")
        if action in ("5", "6") and ask("输入 确认 执行；回车取消") != "确认":
            continue
        try:
            result = dispatch(repo, args)
            if args.action == "detail":
                print(dumps(result), file=sys.stderr)
            elif args.action not in ("check", "edit", "supplement", "reopen"):
                print("操作完成；已更新工作清单。", file=sys.stderr)
        except (BusinessError, ValueError, OSError) as error:
            print(str(error), file=sys.stderr)
