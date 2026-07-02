# API gateway image — also hosts the LangGraph orchestrator in-process.
# Build from the REPO ROOT (uv workspace context):
#   docker build -f services/api-gateway/Dockerfile -t kubepilot-api:dev .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

# libpq5: runtime dep of psycopg, used by the LangGraph Postgres checkpointer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Whole workspace so uv can resolve kubepilot-api + its kubepilot-orch dep.
COPY pyproject.toml uv.lock ./
COPY services ./services
RUN uv sync --frozen --no-dev --package kubepilot-api

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8080
USER 10000
CMD ["uvicorn", "--factory", "kubepilot_api.main:build_app", "--host", "0.0.0.0", "--port", "8080"]
