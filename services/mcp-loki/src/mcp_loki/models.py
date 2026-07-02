"""Pydantic models for Loki tools."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LogLine(BaseModel):
    timestamp: datetime
    line: str
    stream_labels: dict[str, str] = Field(default_factory=dict)


class LogQueryResult(BaseModel):
    query: str
    total_lines: int = 0
    truncated: bool = False
    lines: list[LogLine] = Field(default_factory=list)


class ExceptionMatch(BaseModel):
    """A log line classified as an exception/stack trace."""

    timestamp: datetime
    line: str
    runtime: str  # "java" | "python" | "node" | "go" | "dotnet" | "ruby" | "generic"
    exception_class: str | None = None
    stream_labels: dict[str, str] = Field(default_factory=dict)


class ExceptionsView(BaseModel):
    query: str
    total: int = 0
    by_runtime: dict[str, int] = Field(default_factory=dict)
    matches: list[ExceptionMatch] = Field(default_factory=list)
