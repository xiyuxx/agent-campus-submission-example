"""Native bounded loop; decisions belong to the model, verification to the program."""

import asyncio

from .common import BusinessError, dumps
from .config import ResourceLimits
from .context import ItemContext
from .contracts import FinalProposal
from .model_view import plan_view, tool_view
from .models.base import validate_turn
from .models.prompts import SYSTEM
from .tools.registry import execute, schemas


def validate_finish(ctx: ItemContext, proposal: FinalProposal) -> None:
    if ctx.save_status == "unknown":
        ctx.forced_status = "incomplete"
        return
    if ctx.technical_error:
        if proposal.action != "finish":
            raise BusinessError(
                "INVALID_FINISH", "技术失败不能伪装成资料待确认，请 finish 如实停止"
            )
        ctx.forced_status = "failed"
        return
    state = ctx.repo.runtime_state(ctx.request.request_id, ctx.material_id)
    current = state["draft"]
    if ctx.snapshot and state["catalog_revision"] != ctx.snapshot.catalog_revision:
        raise BusinessError("STALE_EVIDENCE", "资料已更新，结束前需要重新读取、检查与查询")
    if ctx.save_receipt and any(
        not current or current[key] != value for key, value in ctx.save_receipt.items()
    ):
        ctx.refresh_draft()
        raise BusinessError("PLAN_CHANGED", "当前方案已变化，请根据新方案重新评估和核实保存")
    if current and current["validity"] == "rejected":
        required = "needs_input"
    elif ctx.review and ctx.review.review_status == "blocked":
        required = "block"
    elif ctx.item.publish_requirement.missing():
        required = "needs_input"
    elif not ctx.snapshot and any(
        x.code == "MATERIAL_NOT_FOUND" for x in ctx.observations.values()
    ):
        required = "needs_input"
    elif not ctx.review:
        raise BusinessError("INVALID_FINISH", "缺少本轮真实规则检查，不能结束为成功")
    elif ctx.review.review_status == "needs_info":
        required = "needs_input"
    elif (
        ctx.save_status in ("saved", "updated", "reused")
        and current
        and current["validity"] == "active"
        and current["catalog_revision"] == ctx.snapshot.catalog_revision
    ):
        if ctx.save_observation_id not in ctx.consumed:
            raise BusinessError("INVALID_FINISH", "必须先消费保存回执")
        required = "needs_input" if current["pending_items"] else "finish"
    else:
        requested = [x for x in ctx.slots.values() if x.query == "requested"]
        alternatives = [x for x in ctx.slots.values() if x.query == "alternatives"]
        if (
            requested
            and not any(x["available"] for x in requested[-1].slots)
            and alternatives
            and not any(x["available"] for x in alternatives[-1].slots)
        ):
            required = "needs_input"
        else:
            raise BusinessError("INVALID_FINISH", "需要真实查询；有可用方案时先保存并消费回执")
    if proposal.action != required:
        raise BusinessError("INVALID_FINISH", f"可信状态要求 action={required}")


async def run_item(ctx: ItemContext, model) -> None:
    ctx.refresh_draft()
    start = {
        "publish_requirement": ctx.item.publish_requirement.model_dump(mode="json"),
    }
    messages = [
        {"role": "system", "content": SYSTEM + "\n" + dumps(FinalProposal.model_json_schema())},
        {"role": "system", "content": "预算状态待更新"},
        {"role": "user", "content": dumps(start)},
    ]
    retries = 0
    resources = ResourceLimits()

    def feedback(error):
        if ctx.protocol_feedbacks >= ctx.budget.limits.protocol_feedbacks:
            ctx.forced_status = "incomplete"
            ctx.technical_error = {"code": "PROTOCOL_LIMIT", "message": "协议/结束纠错次数耗尽"}
            return
        ctx.protocol_feedbacks += 1
        messages.append({"role": "system", "content": dumps(error.as_dict())})

    while not ctx.forced_status:
        try:
            messages[1]["content"] = dumps(
                {
                    "budget": ctx.budget.summary(),
                    "current_plan": plan_view(
                        ctx.draft_state, ctx.item.publish_requirement.timezone
                    ),
                }
            )
            if (
                len(dumps({"messages": messages, "tools": schemas()}).encode())
                > resources.request_bytes
            ):
                raise BusinessError("CONTEXT_LIMIT", "完整请求上下文超限，未发送模型请求")
            ctx.budget.model_attempt()
            ctx.turn_revision = ctx.expected_revision
            try:
                turn = await asyncio.wait_for(
                    model.next_action(messages, schemas()), timeout=min(30, ctx.budget.remaining())
                )
            except TimeoutError as exc:
                raise BusinessError("MODEL_TIMEOUT", "模型整轮请求超时", retryable=True) from exc
            retries = 0
            ctx.budget.ensure()
            validate_turn(turn, messages)
            ctx.provider_records.append(turn.metadata)
            ctx.consumed.update(ctx.observations)
            messages.append(turn.assistant_message)
            ctx.trace.emit(
                "model_action",
                material_id=ctx.material_id,
                budget=ctx.budget,
                calls=turn.calls,
                proposal=turn.proposal.model_dump() if turn.proposal else None,
                provider=turn.metadata,
                delivered_observation_ids=sorted(ctx.consumed),
            )
            if turn.calls:
                for call in turn.calls:
                    function = call["function"]
                    result = execute(
                        function["name"], function["arguments"], ctx, call_id=call["id"]
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": dumps(
                                tool_view(result, ctx.item.publish_requirement.timezone)
                            ),
                        }
                    )
                continue
            if turn.proposal is None:
                raise BusinessError("MODEL_PROTOCOL", "模型既未调用工具也未提供结束请求")
            try:
                validate_finish(ctx, turn.proposal)
                ctx.budget.ensure()
            except BusinessError as error:
                if error.code == "BUDGET_EXHAUSTED":
                    raise
                ctx.trace.emit(
                    "error", material_id=ctx.material_id, budget=ctx.budget, error=error.as_dict()
                )
                feedback(error)
                continue
            return
        except BusinessError as error:
            ctx.trace.emit(
                "error", material_id=ctx.material_id, budget=ctx.budget, error=error.as_dict()
            )
            if (
                error.code
                in (
                    "BUDGET_EXHAUSTED",
                    "CONTEXT_LIMIT",
                    "MODEL_RESPONSE_LIMIT",
                    "MODEL_REASONING_UNEXPECTED",
                    "MODEL_REFUSAL",
                )
                or ctx.budget.remaining() <= 0
            ):
                ctx.forced_status = "incomplete"
                ctx.technical_error = error.as_dict()
            elif error.code == "MODEL_PROTOCOL":
                feedback(error)
            elif error.retryable and retries < 1:
                retries += 1
                if ctx.budget.remaining() <= 0.5:
                    ctx.forced_status = "incomplete"
                    ctx.technical_error = error.as_dict()
                else:
                    await asyncio.sleep(0.5)
            else:
                ctx.forced_status = "failed"
                ctx.technical_error = error.as_dict()
        except asyncio.CancelledError:
            ctx.forced_status = "incomplete"
            ctx.technical_error = {"code": "INTERRUPTED", "message": "已取消；保留已完成部分"}
            return
