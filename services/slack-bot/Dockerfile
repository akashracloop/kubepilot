# slack-bot image. Build from the REPO ROOT (uv workspace context):
#   docker build -f services/slack-bot/Dockerfile -t kubepilot-slack:dev .
# Long-running worker — connects to Slack over Socket Mode, so no port is exposed.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
COPY services ./services
RUN uv sync --frozen --no-dev --package kubepilot-slack

ENV PATH="/app/.venv/bin:${PATH}"
USER 10002
CMD ["python", "-m", "kubepilot_slack.app"]
