"""Deterministic engineering substitute. It consumes observations; never live evidence."""

import json
from datetime import datetime

from ..common import new_id
from ..contracts import FinalProposal
from .base import ModelTurn


class MockModel:
    async def next_action(self, messages: list[dict], schemas: list[dict]) -> ModelTurn:
        context = json.loads(next(x["content"] for x in messages if x["role"] == "user"))
        state = json.loads(messages[1]["content"])
        results = [json.loads(x["content"]) for x in messages if x["role"] == "tool"]

        def call(name, **kwargs):
            tool = {
                "id": new_id("mock-call"),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(kwargs),
                },
            }
            return ModelTurn(
                {"role": "assistant", "content": None, "tool_calls": [tool]}, calls=[tool]
            )

        def finish(action, reason):
            proposal = FinalProposal(
                action=action,
                reason_summary=reason,
            )
            return ModelTurn(
                {"role": "assistant", "content": proposal.model_dump_json()}, proposal=proposal
            )

        if messages[-1]["role"] == "system":
            feedback = json.loads(messages[-1]["content"])
            if feedback.get("code") in ("PLAN_CHANGED", "STALE_EVIDENCE"):
                return call("read_material", detail="basic")
        if not results:
            return call("read_material", detail="basic")
        last = results[-1]
        if not last["ok"]:
            if last["code"] == "STALE_EVIDENCE":
                return call("read_material", detail="basic")
            if last["retryable"] or last["code"] == "REVISION_CONFLICT":
                retry = next(
                    x
                    for x in reversed(messages)
                    if x["role"] == "assistant" and x.get("tool_calls")
                )
                function = retry["tool_calls"][0]["function"]
                args = json.loads(function["arguments"])
                return call(function["name"], **args)
            if last["code"] in ("DRAFT_REJECTED", "MATERIAL_NOT_FOUND", "DATA_CONFLICT"):
                return finish("needs_input", "需要用户补充资料或发起独立请求")
            return finish("finish", "工具失败，停止并保留已完成检查")
        if last["tool"] == "read_material":
            return call("check_rules")
        review = next(
            (r for r in reversed(results) if r["tool"] == "check_rules" and r["ok"]), None
        )
        if review["data"]["review_status"] == "blocked":
            return finish("block", "规则明确阻断")
        if review["data"]["review_status"] == "needs_info":
            supplemented = any(
                r["tool"] == "read_material" and r["ok"] and r["data"].get("detail") == "supplement"
                for r in results
            )
            if not supplemented:
                return call("read_material", detail="supplement")
            return finish("needs_input", "补查后仍缺必要资料")
        requirement = context["publish_requirement"]
        if any(requirement.get(k) is None for k in ("requested_at", "timezone", "constraint")):
            return finish("needs_input", "原时间要求缺项")
        if (state.get("current_plan") or {}).get("validity") == "rejected":
            return finish("needs_input", "原方案已拒绝，需要新请求")
        if last["tool"] == "check_rules":
            return call("query_slots", query="requested")
        if last["tool"] == "query_slots":
            slots = last["data"]["slots"]
            available = [x for x in slots if x["available"]]
            if not available:
                if last["data"]["query"] == "requested":
                    return call("query_slots", query="nearest")
                return finish("needs_input", "合法候选为空或全部占用，请调整范围")
            original = datetime.fromisoformat(requirement["requested_at"])
            chosen = min(
                available,
                key=lambda x: (
                    abs((datetime.fromisoformat(x["at"]) - original).total_seconds()),
                    datetime.fromisoformat(x["at"]) < original,
                ),
            )
            return call(
                "save_draft",
                suggested_at=chosen["at"],
            )
        if last["tool"] == "save_draft":
            action = "needs_input" if last["data"]["pending_items"] else "finish"
            return finish(action, "草稿已核实，按待确认事项结束")
        return finish("finish", "工程替身停止")
