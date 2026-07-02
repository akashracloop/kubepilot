"""Orchestrator settings — pydantic-settings, loaded from env / values.yaml.

Hierarchy:
- env vars (highest priority): ``KUBEPILOT_LLM__DEFAULT_PROVIDER`` etc.
- ``.env`` file
- defaults in code (lowest)

Provider API keys are loaded from env vars or k8s Secrets in production.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from kubepilot_orch.llm.base import Role


class LLMRoleBinding(BaseModel):
    provider: str  # "anthropic" | "openai" | "ollama" | "vllm" | "bedrock" | "azure"
    model: str


class LLMSettings(BaseModel):
    default_provider: str = "anthropic"
    roles: dict[Role, LLMRoleBinding] = Field(
        default_factory=lambda: {
            Role.ROUTING: LLMRoleBinding(provider="anthropic", model="claude-haiku-4-5-20251001"),
            Role.ANALYSIS: LLMRoleBinding(provider="anthropic", model="claude-sonnet-4-6"),
            Role.SUMMARIZATION: LLMRoleBinding(
                provider="anthropic", model="claude-haiku-4-5-20251001"
            ),
            # Phase 3 critic: an independent refutation of the RCA needs a strong
            # reasoner, so it defaults to the same analysis-tier model as RCA.
            Role.CRITIQUE: LLMRoleBinding(provider="anthropic", model="claude-sonnet-4-6"),
        }
    )

    # Provider credentials / endpoints (only the ones referenced in `roles` need to be set)
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    vllm_base_url: str = "http://localhost:8000/v1"
    bedrock_region: str | None = None
    azure_api_key: str | None = None
    azure_endpoint: str | None = None


class DatabaseSettings(BaseModel):
    url: str = "postgresql://kubepilot:kubepilot@localhost:5432/kubepilot"
    pool_size: int = 10


class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379/0"


class OrchestratorSettings(BaseSettings):
    """Top-level orchestrator settings."""

    model_config = SettingsConfigDict(
        env_prefix="KUBEPILOT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "dev"
    log_level: str = "INFO"

    llm: LLMSettings = Field(default_factory=LLMSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)


def load_settings() -> OrchestratorSettings:
    return OrchestratorSettings()
