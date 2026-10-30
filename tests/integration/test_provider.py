import asyncio
import json

import httpx
import pytest

from shadow_agent.config import ModelSettings
from shadow_agent.models.deepseek import DeepSeekModel
from shadow_agent.models.mock import MockModel
from shadow_agent.service import run_request


def test_provider_roundtrip_has_real_tool_call_ids_and_drops_reasoning(repo, job, tmp_path):
    requests = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["thinking"] == {"type": "disabled"}
        turn = await MockModel().next_action(body["messages"], body["tools"])
        return httpx.Response(
            200,
            json={
                "id": "engineering-provider-response",
                "model": "synthetic-provider",
                "choices": [
                    {
                        "finish_reason": "tool_calls" if turn.calls else "stop",
                        "message": {**turn.assistant_message, "reasoning_content": None},
                    }
                ],
            },
        )

    async def scenario():
        model = DeepSeekModel(
            ModelSettings("synthetic-test-credential"), transport=httpx.MockTransport(handler)
        )
        try:
            return await run_request(job, repo, model, mode="mock", output=tmp_path / "provider")
        finally:
            await model.aclose()

    result = asyncio.run(scenario())
    assert result.items[0].draft.save_status == "saved"
    assert len(requests) == 8
    trace = (tmp_path / "provider/trace.jsonl").read_text(encoding="utf-8")
    assert "DO-NOT-STORE" not in trace
    assert "synthetic-test-credential" not in trace
    history = requests[-1]["messages"]
    call_ids = {call["id"] for msg in history for call in msg.get("tool_calls", [])}
    result_ids = {msg["tool_call_id"] for msg in history if msg["role"] == "tool"}
    assert call_ids == result_ids


@pytest.mark.parametrize("statuses,expected_count", [([429], 2), ([429, 429], 2), ([401], 1)])
def test_provider_retry_limits(repo, job, tmp_path, statuses, expected_count):
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        if count <= len(statuses):
            return httpx.Response(statuses[count - 1])
        return httpx.Response(
            200,
            json={
                "model": "synthetic",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "action": "finish",
                                    "reason_summary": "无依据成功",
                                    "evidence_ids": [],
                                }
                            ),
                        },
                    }
                ],
            },
        )

    async def scenario():
        from shadow_agent.config import Limits

        model = DeepSeekModel(
            ModelSettings("synthetic-test-credential"), transport=httpx.MockTransport(handler)
        )
        try:
            return await run_request(
                job,
                repo,
                model,
                mode="mock",
                output=tmp_path / "retry",
                limits=Limits(model_attempts=2),
            )
        finally:
            await model.aclose()

    result = asyncio.run(scenario())
    assert count == expected_count
    assert result.task_status in ("incomplete", "failed")
