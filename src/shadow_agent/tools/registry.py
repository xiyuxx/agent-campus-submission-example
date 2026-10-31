"""Whitelist tool calls; errors are real observations and also consume budget."""

import json
import sqlite3

from pydantic import ValidationError

from ..common import BusinessError, dumps, new_id, utc
from ..config import ResourceLimits
from ..context import ItemContext
from ..contracts import FACT_FIELDS, MaterialArgs, ReadArgs, SaveArgs, SlotArgs, ToolResult
from ..model_view import tool_view

ARGUMENTS = {
    "read_material": ReadArgs,
    "check_rules": MaterialArgs,
    "query_slots": SlotArgs,
    "save_draft": SaveArgs,
}
DESCRIPTIONS = {
    "read_material": "读取当前素材 basic；先 basic 后可选 supplement。补充仅填未知，不改身份。",
    "check_rules": "检查本轮已读取的有效素材，返回规则证据；读资料变化后必须重新检查。",
    "query_slots": "requested 查原要求；nearest 找前后两侧各一个最近空闲预设节点，允许跨日。",
    "save_draft": "保存当前素材的建议时间；程序绑定真实依据和版本，改期待确认，绝不发布。",
}


def schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": DESCRIPTIONS[name],
                "parameters": contract.model_json_schema(),
            },
        }
        for name, contract in ARGUMENTS.items()
    ]


def retry_key(name, args, ctx) -> str:
    value = args.model_dump(
        mode="json",
        exclude={"reason_summary"},
    )
    if isinstance(args, SaveArgs):
        value["suggested_at"] = utc(args.suggested_at)
    value["basis"] = (
        (ctx.snapshot.catalog_revision, ctx.snapshot.facts_hash()) if ctx.snapshot else None
    )
    return name + ":" + json.dumps(value, sort_keys=True)


def execute(
    name: str, raw_arguments, context: ItemContext, *, call_id: str | None = None
) -> ToolResult:
    ctx = context
    observation_id = new_id("OBS")
    attempted = False
    executed = False
    previous = (ctx.snapshot, ctx.review, ctx.review_id, dict(ctx.slots))
    try:
        if ctx.forced_status:
            raise BusinessError("NOT_EXECUTED", "本项已终止，此调用未执行")
        if (
            ctx.technical_error
            and not ctx.technical_error.get("retryable", False)
            and ctx.technical_error["code"]
            in ("READ_FAILED", "QUERY_FAILED", "SAVE_FAILED", "DATABASE_ERROR")
        ):
            ctx.forced_status = "failed"
            raise BusinessError("NOT_EXECUTED", "此前不可继续的技术失败尚未解决，禁止后续副作用")
        ctx.budget.tool_attempt()
        attempted = True
        if name == "save_draft" and ctx.save_status == "unknown":
            raise BusinessError("SAVE_UNKNOWN", "此前提交尚未核实，禁止盲目重试保存")
        if name not in ARGUMENTS:
            raise BusinessError("UNKNOWN_TOOL", "未知工具，禁止执行")
        if isinstance(raw_arguments, str):
            if len(raw_arguments.encode()) > ResourceLimits().arguments_bytes:
                raise BusinessError("INVALID_ARGUMENTS", "工具参数超出 8 KiB；没有执行")
            raw_arguments = json.loads(raw_arguments)
        args = ARGUMENTS[name].model_validate(raw_arguments)
        key = retry_key(name, args, ctx)
        if ctx.retry_counts.get(key, 0) > 1:
            raise BusinessError("RETRY_LIMIT", "该临时失败最多额外重试一次")
        ctx.budget.ensure()
        ctx.repo.busy_ms = min(2000, int(ctx.budget.remaining() * 1000))
        if name == "read_material":
            if args.detail == "basic":
                executed = True
                snapshot = ctx.repo.read_material_basic(ctx.material_id)
            else:
                if not ctx.snapshot:
                    raise BusinessError("PRECONDITION_FAILED", "supplement 之前必须先读取 basic")
                executed = True
                snapshot = ctx.repo.read_supplement(ctx.snapshot)
            if (
                not ctx.snapshot
                or ctx.snapshot.catalog_revision != snapshot.catalog_revision
                or ctx.snapshot.facts_hash() != snapshot.facts_hash()
            ):
                ctx.review, ctx.review_id = None, None
            if not ctx.snapshot or ctx.snapshot.catalog_revision != snapshot.catalog_revision:
                ctx.slots.clear()
            ctx.snapshot = snapshot
            data = snapshot.model_dump(mode="json")
            data["detail"] = args.detail
            data["missing_fields"] = [
                x for x in FACT_FIELDS if getattr(snapshot.material, x) is None
            ]
            code = "MATERIAL_READ"
        elif name == "check_rules":
            if not ctx.snapshot:
                raise BusinessError("PRECONDITION_FAILED", "检查规则前必须读取 basic")
            executed = True
            ctx.review = ctx.repo.read_rule_snapshot(ctx.snapshot)
            ctx.review_id = observation_id
            data, code = ctx.review.model_dump(mode="json"), "RULES_CHECKED"
        elif name == "query_slots":
            if not ctx.snapshot:
                raise BusinessError("PRECONDITION_FAILED", "查询前必须读取可信平台账号")
            if args.query == "nearest":
                original = [(key, x) for key, x in ctx.slots.items() if x.query == "requested"]
                if not original or original[-1][0] not in ctx.consumed:
                    raise BusinessError("PRECONDITION_FAILED", "先查询原时间并消费其冲突结果")
                if any(x["available"] for x in original[-1][1].slots):
                    raise BusinessError("PRECONDITION_FAILED", "原时间可用，应优先保留原要求")
            executed = True
            slots = ctx.repo.query_occupancy(
                ctx.request.request_id,
                ctx.item,
                ctx.snapshot,
                "requested" if args.query == "requested" else "alternatives",
            )
            ctx.slots[observation_id] = slots
            data = slots.model_dump()
            code = (
                "SLOT_CONFLICT" if any(not x["available"] for x in slots.slots) else "SLOTS_QUERIED"
            )
        else:
            if not ctx.review or ctx.review_id not in ctx.consumed:
                raise BusinessError("PRECONDITION_FAILED", "需要已回传的当前规则检查结果")
            suggested = utc(args.suggested_at)
            query = (
                (
                    "requested"
                    if suggested == utc(ctx.item.publish_requirement.requested_at)
                    else "alternatives"
                )
                if ctx.item.publish_requirement.requested_at
                else None
            )
            candidates = [(key, value) for key, value in ctx.slots.items() if value.query == query]
            if not candidates:
                raise BusinessError("PRECONDITION_FAILED", "需要本轮真实时段查询结果")
            slot_id, slot = candidates[-1]
            if slot_id not in ctx.consumed or suggested not in {
                x["at"] for x in slot.slots if x["available"]
            }:
                raise BusinessError("PRECONDITION_FAILED", "建议必须来自最新已回传的可用时段结果")
            ctx.trace.check_writable()
            executed = True
            ctx.repo.revalidate_current_draft(
                ctx.request.request_id, ctx.material_id, run_id=ctx.run_id
            )
            ctx.budget.ensure()
            saved = ctx.repo.save_draft(
                ctx.request,
                ctx.item,
                ctx.snapshot,
                ctx.review,
                slot,
                suggested,
                ctx.turn_revision,
                evidence_ids=[ctx.review_id, *ctx.slots],
                run_id=ctx.run_id,
                slot_evidence=list(ctx.slots.values()),
            )
            ctx.save_status, ctx.save_error = saved["save_status"], None
            ctx.save_observation_id = observation_id
            draft = saved["draft"]
            ctx.draft_state = draft
            ctx.expected_revision = draft["revision"]
            ctx.save_receipt = {
                key: draft[key]
                for key in ("draft_id", "revision", "catalog_revision", "semantic_sha256")
            }
            data = {
                "save_status": saved["save_status"],
                "draft_id": draft["draft_id"],
                "revision": draft["revision"],
                "validity": draft["validity"],
                "suggested_at": draft["at_utc"],
                "confirmation_status": draft["confirmation_status"],
                "pending_items": [dict(x) for x in draft["pending_items"][:20]],
                "pending_total_count": len(draft["pending_items"]),
                "pending_summary_truncated": len(draft["pending_items"]) > 20,
                "receipt_recovered": saved.get("receipt_recovered", False),
            }
            code = "DRAFT_SAVED"
        result = ToolResult(
            observation_id=observation_id,
            tool=name,
            ok=True,
            code=code,
            catalog_revision=ctx.snapshot.catalog_revision if ctx.snapshot else None,
            data={**data, "executed": executed},
        )
        if (
            len(dumps(tool_view(result, ctx.item.publish_requirement.timezone)).encode())
            > ResourceLimits().tool_result_bytes
        ):
            if name != "save_draft":
                ctx.snapshot, ctx.review, ctx.review_id, ctx.slots = previous
            ctx.forced_status = "incomplete"
            raise BusinessError("OBSERVATION_TOO_LARGE", "观察超过 32 KiB，没有伪装成完整成功结果")
        ctx.technical_error = None
    except (ValidationError, json.JSONDecodeError, TypeError):
        result = ToolResult(
            observation_id=observation_id,
            tool=name,
            ok=False,
            code="INVALID_ARGUMENTS",
            data={"message": "工具参数不符合 Schema；没有执行业务动作", "executed": executed},
        )
        if name == "save_draft" and ctx.save_status != "unknown":
            ctx.save_status = "failed"
            ctx.save_error = {"code": result.code, "message": result.data["message"]}
    except (BusinessError, sqlite3.Error) as exc:
        error = ctx.repo.database_error(exc) if isinstance(exc, sqlite3.Error) else exc
        if error.retryable and attempted:
            key = retry_key(name, args, ctx)
            ctx.retry_counts[key] = ctx.retry_counts.get(key, 0) + 1
        result = ToolResult(
            observation_id=observation_id,
            tool=name,
            ok=False,
            code=error.code,
            retryable=error.retryable,
            data={"message": error.message, "executed": executed},
        )
        if error.code == "REVISION_CONFLICT":
            ctx.refresh_draft()
            result.data["current_draft"] = ctx.draft_state
        if (
            name == "save_draft"
            and attempted
            and ctx.save_status != "unknown"
            and not (
                error.code == "OBSERVATION_TOO_LARGE"
                and ctx.save_status in ("saved", "updated", "reused")
            )
        ):
            ctx.save_status = (
                "unknown" if error.code in ("SAVE_UNKNOWN", "COMMIT_UNKNOWN") else "failed"
            )
            ctx.save_error = error.as_dict()
            ctx.save_observation_id = observation_id
        if error.code in (
            "READ_FAILED",
            "QUERY_FAILED",
            "SAVE_FAILED",
            "DATABASE_ERROR",
            "DATABASE_BUSY",
            "SAVE_UNKNOWN",
            "COMMIT_UNKNOWN",
            "RETRY_LIMIT",
            "OBSERVATION_TOO_LARGE",
        ):
            ctx.technical_error = error.as_dict()
        if error.code == "BUDGET_EXHAUSTED":
            ctx.forced_status = "incomplete"
    except OSError:
        if name == "save_draft" and attempted and ctx.save_status == "not_attempted":
            ctx.save_status = "failed"
            ctx.save_error = {"code": "EVIDENCE_ERROR", "message": "证据预检失败，保存未执行"}
        raise
    ctx.observations[observation_id] = result
    if call_id is not None:
        ctx.observation_calls[observation_id] = call_id
    ctx.trace.emit(
        "tool_result",
        material_id=ctx.material_id,
        budget=ctx.budget,
        attempted=attempted,
        tool_call_id=call_id,
        result=result.model_dump(mode="json"),
    )
    return result
