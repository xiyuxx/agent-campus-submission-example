from dataclasses import dataclass, field
from typing import Protocol

from ..common import BusinessError
from ..config import ResourceLimits
from ..contracts import FinalProposal


@dataclass
class ModelTurn:
    assistant_message: dict
    calls: list[dict] = field(default_factory=list)
    proposal: FinalProposal | None = None
    metadata: dict = field(default_factory=dict)


class Model(Protocol):
    async def next_action(self, messages: list[dict], schemas: list[dict]) -> ModelTurn: ...


def validate_turn(turn: ModelTurn, messages: list[dict]) -> None:
    if bool(turn.calls) == (turn.proposal is not None):
        raise BusinessError("MODEL_PROTOCOL", "工具调用与结束请求必须二选一")
    if turn.assistant_message.get("role") != "assistant":
        raise BusinessError("MODEL_PROTOCOL", "响应角色必须为 assistant")
    if len(turn.calls) > ResourceLimits().calls_per_response:
        raise BusinessError("MODEL_PROTOCOL", "单轮工具数量超限，整组不执行")
    if turn.calls:
        if turn.assistant_message.get("tool_calls") != turn.calls:
            raise BusinessError("MODEL_PROTOCOL", "助手消息与待执行工具组不一致")
        seen = {call["id"] for message in messages for call in message.get("tool_calls", [])}
        for call in turn.calls:
            if (
                not isinstance(call, dict)
                or not isinstance(call.get("id"), str)
                or not call["id"]
                or call["id"] in seen
                or call.get("type") != "function"
                or not isinstance(call.get("function"), dict)
                or not isinstance(call["function"].get("name"), str)
                or not isinstance(call["function"].get("arguments"), str)
            ):
                raise BusinessError("MODEL_PROTOCOL", "工具调用标识/结构错误，整组未执行")
            seen.add(call["id"])
