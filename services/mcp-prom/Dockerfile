# mcp-prom image. Build from the REPO ROOT (uv workspace context):
#   docker build -f services/mcp-prom/Dockerfile -t kubepilot-mcp-prom:dev .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
COPY services ./services
RUN uv sync --frozen --no-dev --package kubepilot-mcp-prom

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
USER 10002
CMD ["uvicorn", "mcp_prom.server:app", "--host", "0.0.0.0", "--port", "8080"]
