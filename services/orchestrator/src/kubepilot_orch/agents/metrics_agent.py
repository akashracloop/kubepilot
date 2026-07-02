"""Metrics specialist sub-agent — Prometheus signals via mcp-prom."""

from __future__ import annotations

from kubepilot_orch.agents._runner import AgentSpec, run_agent
from kubepilot_orch.agents.prompts import load_prompt
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.mcp.client import MCPClient
from kubepilot_orch.state import AgentOutput

AGENT_NAME = "metrics"


async def run(
    *,
    query: str,
    namespace: str,
    service: str | None,
    time_window_minutes: int = 15,
    llm: LLMRouter,
    mcp_prom: MCPClient,
) -> AgentOutput:
    return await run_agent(
        AgentSpec(
            name=AGENT_NAME,
            system_prompt=load_prompt("metrics_agent"),
            user_task=_user_task(query, namespace, service, time_window_minutes),
            mcp=mcp_prom,
            llm=llm,
        )
    )


def _user_task(query: str, namespace: str, service: str | None, window: int) -> str:
    parts = [
        f"Investigation query: {query}",
        f"Namespace: {namespace}",
    ]
    if service:
        parts.append(f"Target service: {service}")
    parts.append(f"Time window: last {window} minutes")
    parts.append(
        "Look for resource saturation, error-rate changes, latency shifts, and firing alerts. "
        "Use the Prometheus tools available."
    )
    return "\n".join(parts)
