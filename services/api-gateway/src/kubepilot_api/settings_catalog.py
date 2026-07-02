"""The catalog of UI-editable settings + override application (Phase: UI config).

A curated set of runtime-editable settings across four domains — features, LLM
routing, remediation, prompts/thresholds. Overrides are stored as a flat
``{key: value}`` dict (see ``settings_store``); this module both *describes* the
editable surface (for the Settings UI to render generically) and *applies* a
persisted override set onto ``ApiSettings`` + ``OrchestratorSettings`` before the
graph is (re)built, so a change takes effect on the next investigation.

Secrets (API keys) and infra (MCP URLs, DB) are deliberately **not** editable
here — they stay env/Helm-managed and are surfaced read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kubepilot_api.config import ApiSettings

PROVIDERS = ["anthropic", "openai", "bedrock", "azure", "ollama", "vllm"]
LLM_ROLES = ["routing", "analysis", "summarization", "critique"]
SELFHEAL_PATTERNS = ["imagepull_revert", "crashloop_restart"]


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    kind: str  # bool | string | select | csv | json
    label: str
    help: str = ""
    options: list[str] | None = None
    restart_required: bool = False


# The editable surface, grouped. `llm.role.*` fields are generated below.
CATALOG: list[Setting] = [
    # --- Features ---
    Setting(
        "features.critic_enabled",
        "features",
        "bool",
        "Adversarial critic",
        "A critic agent refutes the RCA and can escalate low-agreement findings.",
    ),
    Setting(
        "features.memory_enabled",
        "features",
        "bool",
        "Long-term memory",
        "Retrieve similar past incidents before RCA; index concluded ones.",
    ),
    Setting(
        "features.knowledge_enabled",
        "features",
        "bool",
        "Cluster knowledge graph",
        "Inject owner / dependency / SLO context into the RCA.",
    ),
    Setting(
        "features.timeline_llm_labels",
        "features",
        "bool",
        "LLM timeline labels",
        "Polish incident-timeline labels with an extra LLM call (ordering stays deterministic).",
    ),
    # --- LLM routing ---
    Setting(
        "llm.default_provider",
        "llm",
        "select",
        "Default provider",
        "Fallback provider when a role has no explicit binding.",
        options=PROVIDERS,
    ),
    # --- Remediation ---
    Setting(
        "remediation.enabled",
        "remediation",
        "bool",
        "Remediation enabled",
        "Propose an executable plan and interrupt for HITL approval. Writes stay dry-run "
        "unless the write server's apply flag is on.",
    ),
    Setting(
        "remediation.signal_query",
        "remediation",
        "string",
        "Validation PromQL",
        "Error-rate metric compared before/after a write; a regression auto-rolls-back. "
        "Empty → restarts-only validation.",
    ),
    Setting(
        "remediation.selfheal_patterns",
        "remediation",
        "csv",
        "Self-heal patterns",
        "Comma-separated opt-in autonomous patterns (still fully gated).",
        options=SELFHEAL_PATTERNS,
    ),
    # --- Prompts & thresholds ---
    Setting(
        "prompts.active_versions",
        "prompts",
        "json",
        "Prompt version pins",
        'JSON map pinning a prompt to a version, e.g. {"rca_agent": "v1"}. The rollback lever.',
    ),
]

# Per-role provider/model bindings (generated so the UI can render them).
for _role in LLM_ROLES:
    CATALOG.append(
        Setting(
            f"llm.role.{_role}.provider",
            "llm",
            "select",
            f"{_role.capitalize()} · provider",
            options=PROVIDERS,
        )
    )
    CATALOG.append(
        Setting(f"llm.role.{_role}.model", "llm", "string", f"{_role.capitalize()} · model")
    )

_BY_KEY = {s.key: s for s in CATALOG}

# catalog key → ApiSettings attribute (simple scalar fields).
_API_FIELDS: dict[str, str] = {
    "features.critic_enabled": "critic_enabled",
    "features.memory_enabled": "memory_enabled",
    "features.knowledge_enabled": "knowledge_enabled",
    "features.timeline_llm_labels": "timeline_llm_labels",
    "remediation.enabled": "remediation_enabled",
    "remediation.signal_query": "remediation_signal_query",
    "remediation.selfheal_patterns": "remediation_selfheal_patterns",
}


def is_editable(key: str) -> bool:
    return key in _BY_KEY


class SettingsValidationError(ValueError):
    """A submitted override value is invalid."""


def validate(overrides: dict[str, Any]) -> None:
    """Reject unknown keys and type/enum-invalid values (fail-closed)."""
    for key, value in overrides.items():
        setting = _BY_KEY.get(key)
        if setting is None:
            raise SettingsValidationError(f"unknown setting: {key!r}")
        if setting.kind == "bool" and not isinstance(value, bool):
            raise SettingsValidationError(f"{key} must be a boolean")
        if setting.kind in ("string", "csv", "select") and not isinstance(value, str):
            raise SettingsValidationError(f"{key} must be a string")
        if setting.kind == "select" and setting.options and value not in setting.options:
            raise SettingsValidationError(f"{key} must be one of {setting.options}")
        if setting.kind == "json" and not isinstance(value, dict):
            raise SettingsValidationError(f"{key} must be a JSON object")


def _base_value(key: str, settings: ApiSettings, orch: Any) -> Any:
    """The current value from env/config (before overrides), for a catalog key."""
    if key in _API_FIELDS:
        return getattr(settings, _API_FIELDS[key])
    if key == "prompts.active_versions":
        return dict(settings.prompt_active_versions)
    if key == "llm.default_provider":
        return orch.llm.default_provider
    if key.startswith("llm.role."):
        _, _, role, attr = key.split(".", 3)
        binding = orch.llm.roles.get(_role_enum(orch, role))
        return getattr(binding, attr) if binding else ""
    return None


def _role_enum(orch: Any, role: str) -> Any:
    for r in orch.llm.roles:
        if str(r) == role or getattr(r, "value", None) == role:
            return r
    return role


def effective(settings: ApiSettings, orch: Any, overrides: dict[str, Any]) -> dict[str, Any]:
    """The effective value of every catalog key: override if present, else base."""
    out: dict[str, Any] = {}
    for setting in CATALOG:
        out[setting.key] = overrides.get(setting.key, _base_value(setting.key, settings, orch))
    return out


def describe(settings: ApiSettings, orch: Any, overrides: dict[str, Any]) -> dict[str, Any]:
    """Grouped settings + metadata for the Settings UI, with effective values."""
    eff = effective(settings, orch, overrides)
    groups: dict[str, list[dict[str, Any]]] = {}
    for s in CATALOG:
        groups.setdefault(s.group, []).append(
            {
                "key": s.key,
                "kind": s.kind,
                "label": s.label,
                "help": s.help,
                "options": s.options,
                "restart_required": s.restart_required,
                "value": eff[s.key],
                "overridden": s.key in overrides,
            }
        )
    return {"groups": groups}


def apply_overrides(
    settings: ApiSettings, orch: Any, overrides: dict[str, Any]
) -> tuple[ApiSettings, Any]:
    """Return (settings, orch) copies with the overrides applied — used to (re)build the graph."""
    from kubepilot_orch.config import LLMRoleBinding

    api_updates: dict[str, Any] = {}
    for key, value in overrides.items():
        if key in _API_FIELDS:
            api_updates[_API_FIELDS[key]] = value
        elif key == "prompts.active_versions" and isinstance(value, dict):
            api_updates["prompt_active_versions"] = value
    new_settings = settings.model_copy(update=api_updates) if api_updates else settings

    new_orch = orch
    llm_touch = any(k == "llm.default_provider" or k.startswith("llm.role.") for k in overrides)
    if llm_touch:
        new_orch = orch.model_copy(deep=True)
        if "llm.default_provider" in overrides:
            new_orch.llm.default_provider = overrides["llm.default_provider"]
        for key, value in overrides.items():
            if not key.startswith("llm.role."):
                continue
            _, _, role, attr = key.split(".", 3)
            r = _role_enum(new_orch, role)
            current = new_orch.llm.roles.get(r)
            if current is None:
                continue
            data = {"provider": current.provider, "model": current.model, attr: value}
            new_orch.llm.roles[r] = LLMRoleBinding(**data)
    return new_settings, new_orch


# Read-only infra/secret facts surfaced (never editable here).
def readonly_facts(settings: ApiSettings, orch: Any) -> list[dict[str, Any]]:
    def has(v: Any) -> str:
        return "configured" if v else "not set"

    return [
        {"label": "Storage backend", "value": settings.storage},
        {"label": "Checkpointer", "value": settings.checkpointer},
        {"label": "MCP · kubernetes", "value": settings.mcp.k8s},
        {"label": "MCP · prometheus", "value": settings.mcp.prom},
        {"label": "MCP · loki", "value": settings.mcp.loki},
        {"label": "Anthropic API key", "value": has(orch.llm.anthropic_api_key)},
        {"label": "OpenAI API key", "value": has(orch.llm.openai_api_key)},
        {"label": "Write apply", "value": "server-side (KUBEPILOT_WRITE_APPLY_ENABLED)"},
    ]
