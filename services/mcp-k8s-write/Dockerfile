# mcp-k8s-write image. Build from the REPO ROOT (uv workspace context):
#   docker build -f services/mcp-k8s-write/Dockerfile -t kubepilot-mcp-k8s-write:dev .
# Deployed ONLY when remediation.enabled=true (Phase 4). Dry-run by default.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
COPY services ./services
RUN uv sync --frozen --no-dev --package kubepilot-mcp-k8s-write

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
USER 10002
CMD ["uvicorn", "mcp_k8s_write.server:app", "--host", "0.0.0.0", "--port", "8080"]
