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


class KeyPolicy(BaseModel):
    """What an API key is allowed to do (light multi-tenancy, Phase 2)."""

    role: str = "investigator"  # "viewer" (read-only) | "investigator" (can trigger)
    namespaces: list[str] = Field(default_factory=list)  # empty = all namespaces


class AuthSettings(BaseModel):
    api_key: str | None = None  # legacy single key (investigator, all namespaces)
    api_key_header: str = "X-API-Key"
    # Optional per-key policies. Set via KUBEPILOT_API_AUTH__KEYS as JSON, e.g.
    # {"<secret>": {"role": "viewer", "namespaces": ["prod"]}}. Takes precedence
    # over api_key when a presented key matches here.
    keys: dict[str, KeyPolicy] = Field(default_factory=dict)


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

    # CORS: origins allowed to call the API from a browser (the Web UI SPA).
    # Default allows any origin — fine since auth is a header token (no cookies).
    # Set to specific origins (e.g. https://kubepilot.example.com) in production.
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # Storage backend — "postgres" in prod, "memory" in tests / dev without DB.
    storage: str = "postgres"

    # LangGraph checkpointer backend — "postgres" (resumable, survives restarts)
    # or "memory" (dev / no-DB). See kubepilot_orch.checkpointing.
    checkpointer: str = "postgres"

    # Phase 2 long-term memory (pgvector). When enabled, similar past incidents are
    # retrieved before RCA and concluded incidents are indexed. Uses a pgvector
    # store when storage=postgres, else an in-process store (dev, non-persistent).
    memory_enabled: bool = True

    # Phase 3 critic: an adversarial review between RCA and recommendation that
    # produces an agreement score, a critic-adjusted confidence, and an
    # escalate-to-human flag. On by default for Phase 3.
    critic_enabled: bool = True

    # Phase 3 cluster knowledge graph: a pre-RCA node that injects owner/dependency/
    # SLO context. Off by default until the graph is populated by ingestion; an empty
    # graph would just add a no-op node.
    knowledge_enabled: bool = False

    # Phase 3 confidence calibrator: path to a JSON file holding a trained isotonic
    # calibrator (IsotonicCalibrator.to_dict()). When set + readable, finalize maps
    # the raw RCA confidence to an empirically-calibrated value. None → no calibration.
    calibrator_path: str | None = None

    # Phase 3 prompt versioning: pin a prompt to a specific version, e.g.
    # {"rca_agent": "v2"}. This is the rollback lever — set it (env/values) and
    # restart to roll a prompt back in <5 min. Empty → each prompt serves its latest.
    prompt_active_versions: dict[str, str] = Field(default_factory=dict)


def load_settings() -> ApiSettings:
    return ApiSettings()
