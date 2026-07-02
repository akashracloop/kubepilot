"""Typer CLI entry point for KubePilot AI.

    kubepilot investigate <service> -n prod -q "..." [--wait] [-o table|json]
    kubepilot get <incident_id> [-o table|json]
    kubepilot list [--limit N] [-o table|json]

Commands drive the async ``client`` layer via ``asyncio.run`` and format output
through the pure helpers in ``render``. Errors print to stderr and exit non-zero.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Annotated

import typer
from rich.console import Console

from kubepilot_cli import client, render

app = typer.Typer(
    name="kubepilot",
    help="Run KubePilot AI incident investigations from the terminal or CI.",
    no_args_is_help=True,
    add_completion=False,
)

_stdout = Console()
_stderr = Console(stderr=True)

# Default seconds to wait for an investigation to reach a terminal status.
_WAIT_TIMEOUT = 600.0


class OutputFormat(StrEnum):
    table = "table"
    json = "json"


def _fail(message: str) -> None:
    """Print an error to stderr and exit non-zero."""
    _stderr.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=1)


@app.command()
def investigate(
    service: Annotated[str, typer.Argument(help="Service / workload to investigate.")],
    namespace: Annotated[str, typer.Option("--namespace", "-n", help="Kubernetes namespace.")],
    query: Annotated[
        str | None,
        typer.Option("--query", "-q", help="Investigation question."),
    ] = None,
    time_window: Annotated[
        int,
        typer.Option("--time-window", help="Look-back window in minutes."),
    ] = 30,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format."),
    ] = OutputFormat.table,
    wait: Annotated[
        bool,
        typer.Option("--wait/--no-wait", help="Poll until the investigation finishes."),
    ] = True,
) -> None:
    """Start an investigation for SERVICE; with --wait, render the final report."""
    question = query or f"why is {service} failing?"
    try:
        created = asyncio.run(client.create(question, namespace, service, time_window))
    except client.ApiError as exc:
        _fail(str(exc))
        return  # unreachable; keeps type-checkers happy

    incident_id = str(created.get("incident_id", ""))

    if not wait:
        if output is OutputFormat.json:
            _stdout.print_json(render.to_json(created))
        else:
            _stdout.print(incident_id)
        return

    try:
        detail = asyncio.run(client.wait_for(incident_id, timeout=_WAIT_TIMEOUT))
    except client.ApiError as exc:
        _fail(str(exc))
        return

    _emit_detail(detail, output)
    if detail.get("status") == "failed":
        _fail(detail.get("error") or f"Investigation {incident_id} failed.")


@app.command()
def get(
    incident_id: Annotated[str, typer.Argument(help="Investigation incident id.")],
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format."),
    ] = OutputFormat.table,
) -> None:
    """Fetch and render a single investigation."""
    try:
        detail = asyncio.run(client.get(incident_id))
    except client.ApiError as exc:
        _fail(str(exc))
        return
    _emit_detail(detail, output)
    if detail.get("status") == "failed":
        _fail(detail.get("error") or f"Investigation {incident_id} failed.")


@app.command(name="list")
def list_(
    limit: Annotated[int, typer.Option("--limit", help="Max investigations to return.")] = 20,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Output format."),
    ] = OutputFormat.table,
) -> None:
    """List recent investigations."""
    try:
        result = asyncio.run(client.list(limit=limit))
    except client.ApiError as exc:
        _fail(str(exc))
        return
    items = result.get("items", [])
    if output is OutputFormat.json:
        _stdout.print_json(render.to_json(result))
    else:
        _stdout.print(render.render_list(items))


def _emit_detail(detail: dict, output: OutputFormat) -> None:
    if output is OutputFormat.json:
        _stdout.print_json(render.to_json(detail))
    else:
        _stdout.print(render.render_report(detail))


if __name__ == "__main__":
    app()
