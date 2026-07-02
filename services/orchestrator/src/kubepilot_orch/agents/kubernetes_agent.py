"""Kubernetes specialist sub-agent.

Thin shell over ``_runner.run_agent`` — see that module for the shared
tool-loop + structured-summary pattern. The system prompt and user-task
formatting are the only k8s-specific bits.
"""

from __future__ import annotations

from kubepilot_orch.agents._runner import (
    DEFAULT_MAX_ITERATIONS,
    AgentSpec,
    run_agent,
)
from kubepilot_orch.agents.prompts import load_prompt
from kubepilot_orch.llm.router import LLMRouter
from kubepilot_orch.mcp.client import MCPClient
from kubepilot_orch.state import AgentOutput

AGENT_NAME = "kubernetes"
MAX_ITERATIONS = DEFAULT_MAX_ITERATIONS  # re-exported so existing tests keep working


async def run(
    *,
    query: str,
    namespace: str,
    service: str | None,
    llm: LLMRouter,
    mcp_k8s: MCPClient,
) -> AgentOutput:
    return await run_agent(
        AgentSpec(
            name=AGENT_NAME,
            system_prompt=load_prompt("kubernetes_agent"),
            user_task=_user_task(query, namespace, service),
            mcp=mcp_k8s,
            llm=llm,
        )
    )


def _user_task(query: str, namespace: str, service: str | None) -> str:
    parts = [f"Investigation query: {query}", f"Namespace: {namespace}"]
    if service:
        parts.append(f"Target service: {service}")
    parts.append("Assess Kubernetes-level health. Use the tools available.")
    return "\n".join(parts)
