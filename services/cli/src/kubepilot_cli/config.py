"""CLI settings — env + ``~/.kubepilot/config.toml``.

Precedence (highest first): explicit init args > ``KUBEPILOT_*`` env vars >
``.env`` > ``~/.kubepilot/config.toml``. The TOML file uses bare field names
(``api_url`` / ``api_key``); env vars use the ``KUBEPILOT_`` prefix.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

# Location of the optional user config file. Kept module-level so tests can
# monkeypatch it to point at a fixture.
CONFIG_PATH = Path.home() / ".kubepilot" / "config.toml"


class Settings(BaseSettings):
    """Resolved CLI configuration."""

    model_config = SettingsConfigDict(
        env_prefix="KUBEPILOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        toml_file=CONFIG_PATH,
        extra="ignore",
    )

    api_url: str = "http://localhost:8080"
    # Sent as the ``X-API-Key`` header when set; omitted otherwise (dev).
    api_key: str | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Insert the TOML source below env/dotenv so env vars override the file.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


def load_config() -> Settings:
    """Load settings from env and ``~/.kubepilot/config.toml`` (if present)."""
    return Settings()
