"""Shared tool-use loop + structured-summary runner for sub-agents.

Every Phase-1 specialist agent (Kubernetes, Metrics, Logs) follows the same shape:

  1. Fetch tool descriptors from one MCP server.
  2. Run a bounded tool-use loop: LLM emits tool_calls → invoke them → feed results back.
   Loop ends when the LLM produces text without tool_calls OR we hit MAX_ITERATIONS.
  3. Make one final call with response_schema=AgentOutput to get the structured summary.

This module factors that loop out so concrete agents are just a thin shell over
``run_agent(...)`` with their own name, prompt, and MCP client.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import ValidationError

from kubepilot_orch.llm.base import LLMResponse, Message, Role, ToolSchema
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.mcp.client import MCPClient, MCPError
from kubepilot_orch.state import AgentOutput, Evidence, Severity

log = structlog.get_logger(__name__)

DEFAULT_MAX_ITERATIONS = 8
DEFAULT_TOOL_RESULT_CHARS = 8000
SUMMARY_INSTRUCTION = (
    "Investigation complete. Produce the final structured AgentOutput per the schema. "
    "Include every distinct symptom you observed as a separate evidence item. "
    "Do not invent data not present in the tool results."
)


@dataclass
class AgentSpec:
    """Per-agent configuration consumed by ``run_agent``."""

    name: str  # "kubernetes" | "metrics" | "logs" | ...
    system_prompt: str
    user_task: str
    mcp: MCPClient
    llm: LLMRouter
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_tool_result_chars: int = DEFAULT_TOOL_RESULT_CHARS


async def run_agent(spec: AgentSpec) -> AgentOutput:
    """Execute one specialist agent and return its structured AgentOutput.

    Never raises on tool failures — those are recorded as evidence (kind="tool_error",
    severity=warning). Only LLM-provider-level failures will propagate.
    """
    started = datetime.now(UTC)
    tokens_total = 0
    tool_calls_made = 0

    tool_descriptors = await spec.mcp.list_tools()
    tool_schemas = [
        ToolSchema(name=t.name, description=t.description, parameters=t.parameters)
        for t in tool_descriptors
    ]

    messages: list[Message] = [
        Message(role="system", content=spec.system_prompt),
        Message(role="user", content=spec.user_task),
    ]
    tool_evidence: list[Evidence] = []

    # ---- Phase 1: tool-use loop --------------------------------------------
    for iteration in range(spec.max_iterations):
        resp = await spec.llm.chat(
            role=Role.ANALYSIS,
            messages=messages,
            tools=tool_schemas,
            temperature=0.0,
        )
        tokens_total += resp.output_tokens + resp.input_tokens

        if not resp.tool_calls:
            messages.append(Message(role="assistant", content=resp.content or ""))
            log.info(
                "agent_loop_finished",
                agent=spec.name,
                iteration=iteration,
                reason="no_more_tools",
            )
            break

        messages.append(Message(role="assistant", content=resp.content or _tool_call_marker(resp)))

        for tc in resp.tool_calls:
            tool_calls_made += 1
            try:
                result = await spec.mcp.invoke(tc.name, tc.arguments)
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=_truncate_tool_result(result, spec.max_tool_result_chars),
                    )
                )
            except MCPError as e:
                tool_evidence.append(
                    Evidence(
                        source_agent=spec.name,
                        kind="tool_error",
                        summary=f"{tc.name} failed: HTTP {e.status}",
                        detail={"tool": tc.name, "arguments": tc.arguments, "error": str(e)},
                        severity=Severity.WARNING,
                        collected_at=datetime.now(UTC),
                    )
                )
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=json.dumps({"error": str(e)}),
                    )
                )
    else:
        log.warning("agent_hit_max_iterations", agent=spec.name, max_iterations=spec.max_iterations)

    # ---- Phase 2: structured summary ---------------------------------------
    messages.append(Message(role="user", content=SUMMARY_INSTRUCTION))
    summary_resp = await spec.llm.chat(
        role=Role.ANALYSIS,
        messages=messages,
        response_schema=AgentOutput,
        temperature=0.0,
    )
    tokens_total += summary_resp.output_tokens + summary_resp.input_tokens

    try:
        output = AgentOutput.model_validate_json(summary_resp.content)
    except (ValidationError, ValueError) as e:
        log.error(
            "agent_summary_invalid",
            agent=spec.name,
            error=str(e),
            content=summary_resp.content[:500],
        )
        output = AgentOutput(
            agent_name=spec.name,
            succeeded=False,
            evidence=tool_evidence,
            notes=f"Failed to produce structured summary: {e}",
        )

    output.agent_name = spec.name
    output.evidence = _normalize_evidence(output.evidence, spec.name) + tool_evidence
    output.tokens_used = tokens_total
    output.latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    if not output.evidence:
        log.warning("agent_no_evidence", agent=spec.name, tool_calls=tool_calls_made)
    return output


def _normalize_evidence(evidence: list[Evidence], agent_name: str) -> list[Evidence]:
    """Stamp source_agent + collected_at on evidence items the LLM produced without them."""
    out: list[Evidence] = []
    now = datetime.now(UTC)
    for e in evidence:
        if not e.source_agent:
            e = e.model_copy(update={"source_agent": agent_name})
        if e.collected_at is None:  # type: ignore[unreachable]
            e = e.model_copy(update={"collected_at": now})
        out.append(e)
    return out


def _tool_call_marker(resp: LLMResponse) -> str:
    """Stable placeholder when the LLM emitted tool_calls with no text content."""
    names = ", ".join(tc.name for tc in resp.tool_calls)
    return f"(calling tools: {names})"


def _truncate_tool_result(result: Any, max_chars: int) -> str:
    body = json.dumps(result, default=str)
    if len(body) > max_chars:
        body = body[:max_chars] + "...[truncated]"
    return body


# Reduce httpx INFO chatter inside agent loops.
logging.getLogger("httpx").setLevel(logging.WARNING)
