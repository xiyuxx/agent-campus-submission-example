"""External JSON shape checks precede execution and use the shared recovery budget."""

import asyncio
import json

import httpx
import pytest
from test_agent import context
from test_retest_edge_cases import response_with

from shadow_agent.agent import run_item
from shadow_agent.common import BusinessError
from shadow_agent.config import ModelSettings
from shadow_agent.models.deepseek import DeepSeekModel
from shadow_agent.result import assemble_result

CALL = {
    "id": "valid-first",
    "type": "function",
    "function": {"name": "read_material", "arguments": '{"detail":"basic"}'},
}
FINAL = '{"action":"finish","reason_summary":"合法结束对象"}'


@pytest.mark.parametrize(
    "body",
    [
        None,
        1,
        {"choices": None},
        {"choices": {}},
        {"choices": [None]},
        {"choices": [[]]},
        response_with({"role": "assistant", "content": {}, "tool_calls": [CALL]}),
        response_with({"role": "assistant", "content": FINAL, "tool_calls": {}}, "stop"),
        response_with({"role": "assistant", "content": FINAL, "tool_calls": ""}, "stop"),
        response_with({"role": "assistant", "tool_calls": [CALL, {**CALL, "function": 1}]}),
        response_with(
            {"role": "assistant", "tool_calls": [{**CALL, "function": {"name": "read_material"}}]}
        ),
        response_with(
            {
                "role": "assistant",
                "tool_calls": [{**CALL, "function": {"name": "read_material", "arguments": {}}}],
            }
        ),
    ],
)
def test_bad_containers_and_argument_types_are_protocol_errors(body):
    async def scenario():
        model = DeepSeekModel(
            ModelSettings("synthetic-test-credential"),
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
        )
        try:
            with pytest.raises(BusinessError) as error:
                await model.next_action([], [])
            assert error.value.code == "MODEL_PROTOCOL"
        finally:
            await model.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_message", [None, {"role": "assistant", "tool_calls": [CALL, None]}])
def test_persistent_malformed_response_is_bounded_and_executes_nothing(repo, job, bad_message):
    ctx = context(repo, job)
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json=response_with(bad_message))

    async def scenario():
        model = DeepSeekModel(
            ModelSettings("synthetic-test-credential"), transport=httpx.MockTransport(handler)
        )
        try:
            await run_item(ctx, model)
        finally:
            await model.aclose()

    asyncio.run(scenario())
    result = assemble_result(ctx)
    assert attempts == ctx.budget.models == 3
    assert ctx.protocol_feedbacks == 2
    assert ctx.budget.tools == 0 and not ctx.observations
    assert result.task_status == "incomplete"
    assert result.error["code"] == "PROTOCOL_LIMIT"
    assert repo.current_draft(job.request_id, "M01") is None


def test_nullable_content_and_final_json_remain_valid():
    async def scenario():
        for message, finish in (
            ({"role": "assistant", "content": None, "tool_calls": [CALL]}, "tool_calls"),
            ({"role": "assistant", "content": FINAL, "tool_calls": None}, "stop"),
        ):
            model = DeepSeekModel(
                ModelSettings("synthetic-test-credential"),
                transport=httpx.MockTransport(
                    lambda request, message=message, finish=finish: httpx.Response(
                        200, json=response_with(message, finish)
                    )
                ),
            )
            try:
                turn = await model.next_action([], [])
                if finish == "tool_calls":
                    assert turn.calls == [CALL]
                    assert turn.assistant_message["content"] is None
                else:
                    assert turn.proposal.action == "finish"
                    assert json.loads(turn.assistant_message["content"])["action"] == "finish"
            finally:
                await model.aclose()

    asyncio.run(scenario())


def test_internal_attribute_error_is_not_reclassified_as_provider_protocol(monkeypatch):
    def broken_internal_validator(*args):
        raise AttributeError("synthetic internal bug")

    monkeypatch.setattr("shadow_agent.models.deepseek.validate_turn", broken_internal_validator)

    async def scenario():
        model = DeepSeekModel(
            ModelSettings("synthetic-test-credential"),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json=response_with({"role": "assistant", "tool_calls": [CALL]})
                )
            ),
        )
        try:
            with pytest.raises(AttributeError, match="synthetic internal bug"):
                await model.next_action([], [])
        finally:
            await model.aclose()

    asyncio.run(scenario())
