"""Slack Bolt app (Socket Mode) for KubePilot AI.

Registers an ``app_mention`` handler and a ``/kubepilot`` slash command. Both
parse the message, acknowledge, post an "investigating…" note, call the API
gateway, wait for the result, and post a Block Kit result card back.

The Bolt app is constructed inside :func:`build_app` (not at import time) so the
pure modules — config / parse / blocks / api_client — can be imported for tests
without requiring ``slack_bolt`` to be installed.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from kubepilot_slack.api_client import InvestigationApiClient
from kubepilot_slack.blocks import result_card
from kubepilot_slack.config import Settings, load_settings
from kubepilot_slack.parse import parse_request

if TYPE_CHECKING:
    from slack_bolt.app.async_app import AsyncApp

log = structlog.get_logger(__name__)


def build_app(settings: Settings | None = None) -> AsyncApp:
    """Construct the AsyncApp with handlers wired to the API gateway."""
    from slack_bolt.app.async_app import AsyncApp

    settings = settings or load_settings()
    app = AsyncApp(token=settings.slack_bot_token)
    client = InvestigationApiClient(api_url=settings.api_url, api_key=settings.api_key)

    async def run_investigation(text: str, say: Any) -> None:
        parsed = parse_request(text, default_namespace=settings.default_namespace)
        target = parsed.service or parsed.query or "the workload"
        await say(f":mag: Investigating `{target}` in `{parsed.namespace}`…")

        try:
            incident_id = await client.start_investigation(
                query=parsed.query,
                namespace=parsed.namespace,
                service=parsed.service,
            )
            detail = await client.wait_for(incident_id, timeout=settings.wait_timeout_seconds)
        except TimeoutError:
            log.warning("investigation_timeout", query=parsed.query)
            await say(
                ":hourglass: The investigation is taking longer than expected. Try again shortly."
            )
            return
        except Exception:  # surface any failure to the user, keep the bot alive
            log.exception("investigation_failed", query=parsed.query)
            await say(":warning: Sorry, I couldn't complete that investigation.")
            return

        await say(blocks=result_card(detail), text="KubePilot investigation result")

    @app.event("app_mention")
    async def handle_mention(event: dict[str, Any], say: Any) -> None:
        await run_investigation(event.get("text", ""), say)

    @app.command("/kubepilot")
    async def handle_command(ack: Any, command: dict[str, Any], say: Any) -> None:
        await ack()
        await run_investigation(command.get("text", ""), say)

    return app


def main() -> None:
    """Entrypoint — start the Socket Mode handler (blocks forever)."""
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    settings = load_settings()
    app = build_app(settings)
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    log.info("slack_bot_starting", api_url=settings.api_url)
    asyncio.run(handler.start_async())


if __name__ == "__main__":
    main()
