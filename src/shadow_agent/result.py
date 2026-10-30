"""Assemble the public result from observations and committed database state."""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from .common import BusinessError
from .context import ItemContext
from .contracts import DraftResult, ItemResult, PendingItem, RequestResult


def assemble_result(ctx: ItemContext) -> ItemResult:
    requirement = ctx.item.publish_requirement
    try:
        current = ctx.repo.current_draft(ctx.request.request_id, ctx.material_id)
    except (BusinessError, sqlite3.Error) as error:
        public = ctx.repo.database_error(error) if isinstance(error, sqlite3.Error) else error
        ctx.technical_error = public.as_dict()
        ctx.forced_status = "incomplete" if ctx.save_status != "not_attempted" else "failed"
        current = None
    pending = [
        PendingItem(id=f"missing:{x}", kind="input", message=f"请在新请求中补充 {x}")
        for x in requirement.missing()
    ]
    review = ctx.review
    if review and review.review_status == "needs_info":
        fields = sorted({x["field"] for x in review.matches if x["severity"] == "needs_info"})
        pending += [
            PendingItem(id=f"missing:{x}", kind="input", message=f"请补充素材 {x} 并显式导入")
            for x in fields
        ]
    if any(x.code == "MATERIAL_NOT_FOUND" for x in ctx.observations.values()) and not ctx.snapshot:
        pending.append(
            PendingItem(id="missing:material_id", kind="input", message="请导入该素材资料")
        )
    suggested = None
    confirmation = "not_required"
    draft = DraftResult(save_status=ctx.save_status, error=ctx.save_error)
    if current:
        draft.id, draft.revision, draft.validity = (
            current["draft_id"],
            current["revision"],
            current["validity"],
        )
        confirmation = current["confirmation_status"]
        if current["validity"] == "active":
            value = datetime.fromisoformat(current["at_utc"])
            suggested = (
                value.astimezone(ZoneInfo(requirement.timezone)).isoformat()
                if requirement.timezone
                else current["at_utc"]
            )
            pending += [PendingItem.model_validate(x) for x in current["pending_items"]]
        elif current["validity"] == "rejected":
            pending.append(
                PendingItem(
                    id="request:new_request",
                    kind="input",
                    message="该方案已拒绝，请以新 request_id 发起独立请求",
                )
            )
    alternatives = [x for x in ctx.slots.values() if x.query == "alternatives"]
    if (
        not suggested
        and alternatives
        and not any(s["available"] for s in alternatives[-1].slots)
        and review
        and review.review_status in ("passed", "needs_review")
        and not requirement.missing()
        and not (current and current["validity"] == "rejected")
    ):
        pending.append(
            PendingItem(
                id="schedule:expand_range",
                kind="input",
                message="合法候选为空或全部占用，请在新请求中调整原要求或候选范围",
            )
        )
    if ctx.forced_status:
        status = ctx.forced_status
    elif ctx.save_status == "unknown":
        status = "incomplete"
    elif review and review.review_status == "blocked":
        status = "blocked"
    elif pending:
        status = "needs_input"
    elif suggested and ctx.save_status in ("saved", "updated", "reused"):
        status = "completed"
    else:
        status = "incomplete"
    return ItemResult(
        material_id=ctx.material_id,
        material_version=ctx.snapshot.material.material_version if ctx.snapshot else None,
        review_status=review.review_status if review else None,
        risk_reasons=review.matches if review else [],
        original_requested_at=requirement.requested_at.isoformat()
        if requirement.requested_at
        else None,
        constraint=requirement.constraint,
        suggested_at=suggested,
        confirmation_status=confirmation,
        pending_items=pending,
        draft=draft,
        task_status=status,
        evidence_ids=list(ctx.observations),
        error=ctx.technical_error,
    )


def aggregate(items: list[ItemResult]) -> str:
    order = {"completed": 0, "needs_input": 1, "blocked": 2, "incomplete": 3, "failed": 4}
    return max((x.task_status for x in items), key=order.__getitem__)


EXIT_CODES = {"completed": 0, "needs_input": 3, "blocked": 4, "incomplete": 5, "failed": 1}


def exit_code(result: RequestResult) -> int:
    if any(
        x.error and x.error["code"] in ("INPUT_CONFLICT", "SCHEMA_VERSION") for x in result.items
    ):
        return 2
    return EXIT_CODES[result.task_status]


def render_summary(result: RequestResult) -> str:
    lines = [
        f"# 素材审核与排期结果：{result.task_status}",
        "",
        f"模式：{result.mode}；请求：{result.request_id}；运行：{result.run_id}",
        "",
        "仅保存本地草稿；未接入真实发布，未预约真实平台时段。",
        "",
    ]
    for item in result.items:
        lines += [
            f"## {item.material_id}",
            "",
            f"- 规则预检：{item.review_status or '尚未完成'}",
            f"- 原要求：{item.original_requested_at or '未知'}；约束：{item.constraint or '未知'}",
            f"- 当前建议：{item.suggested_at or '无有效建议'}",
            f"- 确认状态：{item.confirmation_status}",
            f"- 草稿保存：{item.draft.save_status}；ID：{item.draft.id or '无'}；"
            f"修订：{item.draft.revision}；有效性：{item.draft.validity}",
            f"- 本项状态：{item.task_status}",
        ]
        lines += [f"- 风险：{r['rule_id']} / {r['message']}" for r in item.risk_reasons]
        lines += [f"- 待办（{x.kind}）：{x.id} / {x.message}" for x in item.pending_items]
        if item.error or item.draft.error:
            error = item.error or item.draft.error
            lines.append(f"- 错误：{error['code']} / {error['message']}")
        lines.append("")
    return "\n".join(lines)
