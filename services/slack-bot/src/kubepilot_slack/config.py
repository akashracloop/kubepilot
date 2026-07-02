"""Slack bot settings — env-driven, pydantic-settings.

All settings are read from ``KUBEPILOT_SLACK_*`` environment variables (or a
local ``.env``). Socket Mode is the default transport, so both a bot token
(``xoxb-…``) and an app-level token (``xapp-…``) are needed to connect.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level Slack bot settings."""

    model_config = SettingsConfigDict(
        env_prefix="KUBEPILOT_SLACK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Slack credentials.
    slack_bot_token: str = ""  # xoxb-… — used for Web API calls (posting cards)
    slack_app_token: str = ""  # xapp-… — app-level token for Socket Mode

    # KubePilot API gateway.
    api_url: str = "http://localhost:8080"
    api_key: str | None = None  # sent as the X-API-Key header when set

    # Default namespace when the user does not specify one in their message.
    default_namespace: str = "prod"

    # How long to wait for an investigation to finish before giving up (seconds).
    wait_timeout_seconds: float = 300.0


def load_settings() -> Settings:
    return Settings()
