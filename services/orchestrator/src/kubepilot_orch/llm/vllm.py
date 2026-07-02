"""vLLM provider — OpenAI-compatible local endpoint.

vLLM exposes the OpenAI Chat Completions API, so we reuse OpenAIProvider with
a base_url override. Tool-calling support depends on the served model's
chat template.
"""

from __future__ import annotations

from kubepilot_orch.llm.openai import OpenAIProvider


class VLLMProvider(OpenAIProvider):
    name = "vllm"

    def __init__(self, base_url: str = "http://localhost:8000/v1", api_key: str = "dummy") -> None:
        super().__init__(api_key=api_key, base_url=base_url)


__all__ = ["VLLMProvider"]
