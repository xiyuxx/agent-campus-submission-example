"""Operator CLI: expose decisions and outcomes while keeping protocol IDs in details."""

import asyncio
import json
import sys
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

from .common import BusinessError, dumps, new_id
from .config import Limits, ModelSettings
from .contracts import BusinessFacts, Confirmation, PublishRequirement, RequestResult
from .handoff import export_handoff
from .models.deepseek import DeepSeekModel
from .models.mock import MockModel
from .operator_language import (
    CONFIRM_LABELS,
    FIELD_LABELS,
    VALIDITY_LABELS,
    display_time,
    humanize,
    parse_operator_time,
    pending_message,
)
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
    work.add_argument("--at", help="发布时间，例如2026-11-03 22:00；默认使用电脑本地时区")
    work.add_argument("--timezone", help="高级选项：指定IANA时区；默认local为本机时区")
    work.add_argument("--constraint", choices=("hard", "soft"))
    work.add_argument("--candidate", action="append", help="授权查询的候选时刻，可重复传入")
    work.add_argument("--no-alternatives", action="store_true", help="不允许建议替代时间")
    work.add_argument("--decision", choices=("accept", "reject", "skip"))
    work.add_argument("--fields", type=Path, help="补充业务字段的 JSON 文件")
    work.add_argument("--file", type=Path, help="新的 .csv 或 .json 交接文件路径")
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
    if args.at is not None:
        zone = ZoneInfo(args.timezone) if args.timezone and args.timezone != "local" else None
        value["requested_at"] = parse_operator_time(args.at, zone=zone)
        value["timezone"] = args.timezone or "local"
    elif not value.get("timezone"):
        value["timezone"] = "local"
    if args.no_alternatives and args.candidate is not None:
        raise BusinessError("INVALID_INPUT", "候选列表与不建议改期不能同时选择")
    if args.no_alternatives:
        value["candidate_times"] = []
    elif args.candidate is not None:
        zone = ZoneInfo(value["timezone"]) if value["timezone"] != "local" else None
        value["candidate_times"] = [
            parse_operator_time(at.strip(), zone=zone) for at in args.candidate
        ]
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
    constraint = {"hard": "必须遵守，改期需确认", "soft": "优先采用，改期仍需确认"}
    print(
        f"要求：{display_time(requirement.get('requested_at'))} · "
        f"{constraint.get(requirement.get('constraint'), '时间要求待补充')}",
        file=sys.stderr,
    )
    draft = row["draft"]
    if draft:
        print(
            f"本地保存方案：{display_time(draft['at_utc'])} · "
            f"{VALIDITY_LABELS[draft['validity']]} · "
            f"{CONFIRM_LABELS[draft['confirmation_status']]}",
            file=sys.stderr,
        )
        if row["state"] == "修改未完成":
            print("以上为旧方案，新要求尚未完成保存；当前不能交接。", file=sys.stderr)
        if draft["validity"] != "active":
            print("该方案已失效，时间和风险需重新检查。", file=sys.stderr)
        for pending in draft["pending_items"]:
            print("待确认：" + pending_message(pending, draft), file=sys.stderr)
    else:
        print("尚无已保存草稿。", file=sys.stderr)
    last = row["last_result"]
    if last:
        for risk in last["risk_reasons"]:
            print("审核记录：" + humanize(risk["message"]), file=sys.stderr)
        for pending in last["pending_items"]:
            if pending["kind"] == "input":
                print("待补充：" + humanize(pending["message"]), file=sys.stderr)
        if last.get("error"):
            print("上次运行：" + humanize(last["error"]["message"]), file=sys.stderr)


def ask_supplement(repo, row):
    """Only explicit user answers become facts; the model never fills this form."""
    material = repo.read_material_basic(row["material_id"]).material
    missing = [
        risk["field"]
        for risk in (row["last_result"] or {}).get("risk_reasons", [])
        if risk["severity"] == "needs_info" and risk["field"] in BusinessFacts.model_fields
    ]
    if not missing:
        keys = list(BusinessFacts.model_fields)
        selection = ask(
            "选择要补充或修改的资料，序号用逗号分隔；回车返回\n"
            + " / ".join(f"{i}. {FIELD_LABELS[key]}" for i, key in enumerate(keys, 1))
        )
        if not selection:
            return {}
        try:
            indexes = [int(value.strip()) - 1 for value in selection.split(",")]
            if any(index < 0 or index >= len(keys) for index in indexes):
                raise ValueError
            missing = [keys[index] for index in indexes]
        except ValueError:
            raise ValueError("请选择有效资料序号") from None
    fields = {}
    ordered = list(dict.fromkeys(sorted(missing, key=lambda key: key != "type")))
    for key in ordered:
        options = {
            "copyright_status": {"1": "authorized", "2": "unauthorized"},
            "type": {"1": "video", "2": "image", "3": "text"},
        }.get(key)
        hint = {
            "copyright_status": "1 已获授权 / 2 未获授权",
            "type": "1 视频 / 2 图片 / 3 纯文字",
        }.get(key, "输入内容或文件链接")
        while True:
            value = ask(f"{FIELD_LABELS[key]}：{hint}；回车保留现状")
            if not value:
                break
            if options and value not in options:
                print("请选择有效选项。", file=sys.stderr)
                continue
            fields[key] = options[value] if options else value
            break
        if key == "type" and fields.get("type"):
            required = "caption" if fields["type"] == "text" else "media_ref"
            if getattr(material, required) is None and required not in ordered:
                ordered.append(required)
    return BusinessFacts.model_validate(fields).model_dump(exclude_unset=True)


def interactive(repo, args):
    args.interactive = True
    print(
        "发布前准备工作台 · 仅保存本地草稿\n"
        + (
            "离线 Mock：验证流程，不证明真实模型决策。" if args.mode == "mock" else "真实模型模式。"
        ),
        file=sys.stderr,
    )
    print("时间统一按电脑本地时区显示；建议改期始终需要确认。", file=sys.stderr)
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
            path = ask("输入新的交接文件路径（推荐 .csv，也支持 .json）；回车取消")
            if path:
                try:
                    saved = export_handoff(repo, Path(path))
                    print(
                        f"已导出 {saved['count']} 项 · 版本 {saved['export_version']}\n"
                        f"{saved['notice']}",
                        file=sys.stderr,
                    )
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
                ask(
                    "发布时间，例如2026-11-03 22:00；回车保留 "
                    f"{display_time(existing.get('requested_at'))}"
                )
                or None
            )
            selected_constraint = ask("是否必须在这个时间？1 是 / 2 可建议其他时间 / 回车保留")
            args.constraint = {"1": "hard", "是": "hard", "2": "soft", "否": "soft"}.get(
                selected_constraint, selected_constraint or None
            )
            candidates = ask(
                "可建议的其他时间：回车保留/使用账号预设，输入 不改期，或逗号分隔日期时间"
            )
            args.no_alternatives = candidates in ("不改期", "none")
            args.candidate = (
                candidates.split(",") if candidates and not args.no_alternatives else None
            )
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
            try:
                fields = ask_supplement(repo, selected[0])
            except (BusinessError, ValueError) as error:
                print(humanize(str(error)), file=sys.stderr)
                continue
            if not fields:
                continue
            args.fields = args.root / "inputs" / (new_id("supplement") + ".json")
            args.fields.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(args.fields, dumps(fields))
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
