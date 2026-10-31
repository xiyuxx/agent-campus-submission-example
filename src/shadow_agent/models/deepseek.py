import json

import httpx
from pydantic import ValidationError

from ..common import BusinessError
from ..config import ModelSettings, ResourceLimits
from ..contracts import FinalProposal
from .base import ModelTurn, validate_turn


class DeepSeekModel:
    def __init__(self, settings: ModelSettings, *, transport=None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=5),
            transport=transport,
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )

    async def aclose(self):
        await self.client.aclose()

    async def next_action(self, messages: list[dict], schemas: list[dict]) -> ModelTurn:
        resources = ResourceLimits()
        try:
            async with self.client.stream(
                "POST",
                self.settings.base_url + "/chat/completions",
                json={
                    "model": self.settings.model,
                    "messages": messages,
                    "tools": schemas,
                    "thinking": {"type": "disabled"},
                    "max_tokens": resources.max_output_tokens,
                    "temperature": 0,
                },
            ) as response:
                if response.status_code >= 400:
                    raise BusinessError(
                        "MODEL_HTTP",
                        f"模型 HTTP 状态 {response.status_code}",
                        retryable=response.status_code == 429 or response.status_code >= 500,
                    )
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > resources.response_bytes:
                        raise BusinessError(
                            "MODEL_RESPONSE_LIMIT", "模型响应体超限，未执行任何工具"
                        )
                    chunks.append(chunk)
                raw = b"".join(chunks)
        except httpx.TransportError as exc:
            raise BusinessError("MODEL_TRANSPORT", "模型传输失败", retryable=True) from exc
        try:
            body = json.loads(raw)
            if not isinstance(body, dict) or not isinstance(body.get("choices"), list):
                raise ValueError("expected response object and choices array")
            if len(body["choices"]) != 1 or not isinstance(body["choices"][0], dict):
                raise ValueError("expected one choice")
            message = body["choices"][0]["message"]
            if not isinstance(message, dict):
                raise ValueError("expected message object")
            finish = body["choices"][0].get("finish_reason")
            if finish == "content_filter":
                raise BusinessError("MODEL_REFUSAL", "模型未能处理该内容；未执行工具")
            if message.get("reasoning_content"):
                raise BusinessError(
                    "MODEL_REASONING_UNEXPECTED", "非 thinking 配置收到意外字段，停止并核查配置"
                )
            if message.get("role") != "assistant":
                raise ValueError("invalid role")
            if message.get("content") is not None and not isinstance(message["content"], str):
                raise ValueError("expected string or null content")
            metadata = {
                "requested_model": self.settings.model,
                "returned_model": body.get("model"),
                "response_id": body.get("id"),
                "usage": body.get("usage"),
            }
            # Deliberately drop reasoning_content and the full provider response.
            assistant = {"role": "assistant", "content": message.get("content")}
            calls = message.get("tool_calls")
            if calls is None:
                calls = []
            if not isinstance(calls, list):
                raise ValueError("expected tool calls array")
            if calls:
                if finish != "tool_calls":
                    raise ValueError("inconsistent finish reason")
                if len(calls) > resources.calls_per_response:
                    raise ValueError("too many calls")
                if message.get("content"):
                    try:
                        FinalProposal.model_validate_json(message["content"])
                    except ValidationError:
                        pass
                    else:
                        raise ValueError("mixed calls and final")
                if any(
                    not isinstance(x, dict)
                    or x.get("type") != "function"
                    or not isinstance(x.get("id"), str)
                    or not x["id"]
                    or not isinstance(x.get("function"), dict)
                    or not isinstance(x["function"].get("name"), str)
                    or not isinstance(x["function"].get("arguments"), str)
                    for x in calls
                ) or len({x["id"] for x in calls}) != len(calls):
                    raise ValueError("invalid tool calls")
                assistant["tool_calls"] = [
                    {
                        "id": x["id"],
                        "type": "function",
                        "function": {
                            "name": x["function"]["name"],
                            "arguments": x["function"]["arguments"],
                        },
                    }
                    for x in calls
                ]
                assistant["content"] = None
                turn = ModelTurn(assistant, calls=assistant["tool_calls"], metadata=metadata)
                validate_turn(turn, messages)
                return turn
            content = message.get("content") or ""
            if finish != "stop":
                raise ValueError("incomplete response")
            proposal = FinalProposal.model_validate_json(content)
            return ModelTurn(
                {"role": "assistant", "content": proposal.model_dump_json()},
                proposal=proposal,
                metadata=metadata,
            )
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            ValidationError,
            json.JSONDecodeError,
        ) as exc:
            raise BusinessError("MODEL_PROTOCOL", "模型响应不符合工具/结束协议") from exc
