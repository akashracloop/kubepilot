"""API gateway settings — env-driven, pydantic-settings."""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    url: str = "postgresql://kubepilot:kubepilot@localhost:5432/kubepilot"


class MCPEndpoints(BaseModel):
    k8s: str = "http://localhost:8081"
    prom: str = "http://localhost:8082"
    loki: str = "http://localhost:8083"
    # Phase 2, optional: empty string means the server isn't deployed, so the
    # Tracing / Deployment specialist branches are omitted from the graph.
    tempo: str = ""
    ci: str = ""


class AuthSettings(BaseModel):
    api_key: str | None = None  # if None, auth is disabled (dev only)
    api_key_header: str = "X-API-Key"


class ApiSettings(BaseSettings):
    """Top-level API gateway settings."""

    model_config = SettingsConfigDict(
        env_prefix="KUBEPILOT_API_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "dev"
    log_level: str = "INFO"

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    mcp: MCPEndpoints = Field(default_factory=MCPEndpoints)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    # Storage backend — "postgres" in prod, "memory" in tests / dev without DB.
    storage: str = "postgres"

    # LangGraph checkpointer backend — "postgres" (resumable, survives restarts)
    # or "memory" (dev / no-DB). See kubepilot_orch.checkpointing.
    checkpointer: str = "postgres"


def load_settings() -> ApiSettings:
    return ApiSettings()
