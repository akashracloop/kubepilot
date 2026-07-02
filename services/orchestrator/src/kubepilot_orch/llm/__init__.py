"""LLM provider abstraction.

Every agent calls into ``LLMRouter`` which picks a provider+model per *role*
(routing, analysis, summarization). This lets a cheap model handle routing
and a strong model handle RCA, configured in values.yaml.
"""

from kubepilot_orch.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolSchema,
)
from kubepilot_orch.llm.router import LLMRouter

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LLMRouter",
    "Message",
    "Role",
    "ToolCall",
    "ToolSchema",
]
