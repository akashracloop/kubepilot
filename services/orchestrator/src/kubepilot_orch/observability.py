"""AgentOps tracing setup (OpenTelemetry-GenAI → Phoenix / any OTLP endpoint).

Phase 1 keeps this dependency-light and fail-open:

- Tracing is enabled only when an OTLP endpoint is configured (the Helm chart
  sets ``KUBEPILOT_OTEL_EXPORTER_ENDPOINT`` when AgentOps/Phoenix is on).
- The OTel + OpenInference packages are an OPTIONAL install (the ``observability``
  extra). If they aren't present, ``setup_tracing`` logs and no-ops rather than
  crashing — a minimal/air-gapped install without the extra still runs.

When enabled, OpenInference's LangChain instrumentor auto-emits spans for every
LLM call (prompt, completion, tool calls, token usage) to Phoenix, which is
OTel-GenAI compatible. Per-investigation token totals are additionally recorded
on the investigation state (see finalize) as a lightweight cost ledger.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def setup_tracing(service_name: str, endpoint: str | None) -> bool:
    """Wire OTLP tracing + LangChain auto-instrumentation. Returns True if enabled.

    Safe to call unconditionally at startup: returns False (no-op) when no
    endpoint is configured or the optional observability packages are absent.
    """
    if not endpoint:
        log.info("tracing_disabled", reason="no_otel_endpoint")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning(
            "tracing_unavailable",
            reason="opentelemetry not installed — install the 'observability' extra",
            endpoint=endpoint,
        )
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    # Auto-instrument LangChain (LLM spans) if OpenInference is present.
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().instrument()
        log.info("tracing_enabled", endpoint=endpoint, langchain_instrumented=True)
    except ImportError:
        log.info("tracing_enabled", endpoint=endpoint, langchain_instrumented=False)

    return True
