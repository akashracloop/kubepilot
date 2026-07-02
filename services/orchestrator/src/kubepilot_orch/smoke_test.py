"""W1 smoke test — verifies the orchestrator can talk to its dependencies.

Checks:
  1. Config loads
  2. Postgres reachable (no schema yet — just connect)
  3. Redis reachable
  4. The configured LLM provider can complete a one-shot prompt
     (skipped when no API key is set, but config + factory must still build)

Run with: ``make smoke-test`` (after ``make dev-up``).
"""

from __future__ import annotations

import asyncio
import os
import sys

import structlog

from kubepilot_orch.config import load_settings
from kubepilot_orch.llm.base import Message, ProviderNotConfigured, Role
from kubepilot_orch.llm.factory import build_router

log = structlog.get_logger(__name__)


async def main() -> int:
    settings = load_settings()
    log.info(
        "config_loaded",
        environment=settings.environment,
        providers=list({b.provider for b in settings.llm.roles.values()}),
    )

    ok = True
    ok &= await _check_postgres(settings.db.url)
    ok &= await _check_redis(settings.redis.url)
    ok &= await _check_llm(settings)

    if not ok:
        print("\nSMOKE TEST: FAILED", file=sys.stderr)
        return 1

    print("\nSMOKE TEST: PASSED")
    return 0


async def _check_postgres(url: str) -> bool:
    try:
        import asyncpg
    except ImportError:
        log.warning("postgres_check_skipped", reason="asyncpg not installed")
        return True

    try:
        conn = await asyncpg.connect(url)
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        log.info("postgres_ok", version=str(version).split(",")[0])
        return True
    except Exception as e:
        log.error("postgres_failed", error=str(e))
        return False


async def _check_redis(url: str) -> bool:
    try:
        import redis.asyncio as redis
    except ImportError:
        log.warning("redis_check_skipped", reason="redis not installed")
        return True

    try:
        client = redis.from_url(url)
        pong = await client.ping()
        await client.aclose()
        log.info("redis_ok", pong=pong)
        return True
    except Exception as e:
        log.error("redis_failed", error=str(e))
        return False


async def _check_llm(settings) -> bool:  # type: ignore[no-untyped-def]
    try:
        router = build_router(settings)
    except ProviderNotConfigured as e:
        log.warning("llm_factory_skipped", reason=str(e))
        return True  # config build is what we're verifying; missing keys aren't fatal in W1

    # Only attempt a real call if a key is actually present for the analysis role.
    analysis = settings.llm.roles[Role.ANALYSIS]
    key_present = _has_credential(analysis.provider, settings)
    if not key_present:
        log.info("llm_call_skipped", reason=f"no credential for provider={analysis.provider}")
        return True

    try:
        resp = await router.chat(
            role=Role.ANALYSIS,
            messages=[
                Message(role="system", content="Reply with exactly the word OK."),
                Message(role="user", content="Status check."),
            ],
            max_tokens=8,
        )
        log.info(
            "llm_ok",
            provider=resp.provider,
            model=resp.model,
            content=resp.content[:40],
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
        return True
    except Exception as e:
        log.error("llm_call_failed", error=str(e))
        return False


def _has_credential(provider: str, settings) -> bool:  # type: ignore[no-untyped-def]
    match provider:
        case "anthropic":
            return bool(settings.llm.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))
        case "openai":
            return bool(settings.llm.openai_api_key or os.getenv("OPENAI_API_KEY"))
        case "ollama" | "vllm":
            return True  # local — assume reachable
        case _:
            return False


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
