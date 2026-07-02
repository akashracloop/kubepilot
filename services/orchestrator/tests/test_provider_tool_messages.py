"""Regression: assistant tool_calls must be rendered so a following role="tool"
message is valid (OpenAI rejects a tool message otherwise). This bug only shows
up against a real provider's message conversion, not the ScriptedLLM path.
"""

from __future__ import annotations

from kubepilot_orch.llm._common import to_lc_messages
from kubepilot_orch.llm.base import Message, ToolCall


def _convo() -> list[Message]:
    return [
        Message(role="system", content="you are a k8s agent"),
        Message(role="user", content="inspect prod"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call_1", name="list_pods", arguments={"namespace": "prod"})],
        ),
        Message(role="tool", tool_call_id="call_1", name="list_pods", content='{"pods": []}'),
    ]


def test_common_conversion_carries_tool_calls() -> None:
    lc = to_lc_messages(_convo())
    ai = lc[2]  # the assistant turn
    assert ai.tool_calls, "assistant message lost its tool_calls"
    assert ai.tool_calls[0]["name"] == "list_pods"
    assert ai.tool_calls[0]["id"] == "call_1"
    # the tool message references the same id
    assert lc[3].tool_call_id == "call_1"


def test_openai_and_ollama_conversion_carry_tool_calls() -> None:
    from kubepilot_orch.llm.anthropic import _to_lc_message
    from kubepilot_orch.llm.ollama import _to_lc as ollama_to_lc
    from kubepilot_orch.llm.openai import _to_lc as openai_to_lc

    assistant = _convo()[2]
    for conv in (openai_to_lc, ollama_to_lc, _to_lc_message):
        ai = conv(assistant)
        assert ai.tool_calls and ai.tool_calls[0]["name"] == "list_pods"


def test_plain_assistant_message_has_no_tool_calls() -> None:
    from kubepilot_orch.llm.openai import _to_lc as openai_to_lc

    ai = openai_to_lc(Message(role="assistant", content="done"))
    assert not ai.tool_calls
