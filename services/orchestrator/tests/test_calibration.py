"""Confidence calibration — isotonic fit, ECE, and finalize wiring (Phase 3 W7)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

import httpx
import pytest
from kubepilot_orch.calibration import (
    CalibrationSample,
    IsotonicCalibrator,
    calibration_report,
    expected_calibration_error,
    reliability_curve,
)
from kubepilot_orch.graph import AgentDeps, build_graph
from kubepilot_orch.state import AgentOutput, Evidence, RCAReport, Recommendation
from kubepilot_orch.testing import (
    ScriptedLLM,
    build_mcp_client,
    build_router,
    llm_text,
    llm_tool_call,
)


def _samples(pairs: list[tuple[float, bool]]) -> list[CalibrationSample]:
    return [CalibrationSample(confidence=c, correct=ok) for c, ok in pairs]


def test_ece_zero_for_perfectly_calibrated() -> None:
    # In the [0.9,1.0) bin, 9/10 correct and mean confidence ~0.9 → gap ~0.
    samples = _samples([(0.9, i < 9) for i in range(10)])
    assert expected_calibration_error(samples, n_bins=10) < 0.02


def test_ece_high_for_overconfident() -> None:
    # Stated 0.95 but only 40% correct → large calibration error.
    samples = _samples([(0.95, i < 4) for i in range(10)])
    assert expected_calibration_error(samples, n_bins=10) > 0.4


def test_isotonic_is_monotonic_nondecreasing() -> None:
    # Higher stated confidence → not-lower empirical accuracy after fit.
    samples = _samples(
        [(0.1, False), (0.2, False), (0.3, True), (0.6, False), (0.8, True), (0.9, True)]
    )
    cal = IsotonicCalibrator().fit(samples)
    xs = [0.0, 0.15, 0.3, 0.5, 0.7, 0.9, 1.0]
    ys = [cal.calibrate(x) for x in xs]
    assert all(b >= a - 1e-9 for a, b in pairwise(ys))
    assert all(0.0 <= y <= 1.0 for y in ys)


def test_isotonic_maps_overconfidence_down() -> None:
    # Confidences cluster at 0.9 but only half are right → calibrated ≈ 0.5.
    samples = _samples([(0.9, i % 2 == 0) for i in range(20)])
    cal = IsotonicCalibrator().fit(samples)
    assert cal.calibrate(0.9) == pytest.approx(0.5, abs=0.05)


def test_calibrator_reduces_ece_on_miscalibrated_data() -> None:
    # Systematically overconfident: stated 0.8, only 50% correct.
    samples = _samples([(0.8, i % 2 == 0) for i in range(40)])
    raw_ece = expected_calibration_error(samples)
    cal = IsotonicCalibrator().fit(samples)
    calibrated = _samples([(cal.calibrate(s.confidence), s.correct) for s in samples])
    assert expected_calibration_error(calibrated) < raw_ece
    assert expected_calibration_error(calibrated) < 0.10  # meets the <10% gate


def test_unfitted_calibrator_is_identity() -> None:
    cal = IsotonicCalibrator()
    assert not cal.is_fitted
    assert cal.calibrate(0.73) == pytest.approx(0.73)
    assert IsotonicCalibrator().fit([]).is_fitted is False


def test_calibrator_roundtrips_through_dict() -> None:
    samples = _samples([(0.2, False), (0.5, True), (0.9, True)])
    cal = IsotonicCalibrator().fit(samples)
    restored = IsotonicCalibrator.from_dict(cal.to_dict())
    for x in (0.1, 0.5, 0.95):
        assert restored.calibrate(x) == pytest.approx(cal.calibrate(x))


def test_reliability_curve_bins_and_report() -> None:
    samples = _samples([(0.15, False), (0.25, True), (0.95, True)])
    curve = reliability_curve(samples, n_bins=10)
    assert [b.count for b in curve] == [1, 1, 1]
    report = calibration_report(samples)
    assert report.n == 3
    assert 0.0 <= report.ece <= 1.0
    assert report.within(1.0)


# ----------------------------------------------------------------------------
# Graph-level: a fitted calibrator stamps calibrated_confidence at finalize.
# ----------------------------------------------------------------------------


def _mcp_handler(tool: str) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(
                200,
                json={
                    "tools": [{"name": tool, "description": tool, "parameters": {"type": "object"}}]
                },
            )
        body = json.loads(request.content.decode())
        return httpx.Response(200, json={"tool": body["tool"], "result": {"ok": True}})

    return handler


def _now() -> datetime:
    return datetime(2026, 7, 2, 10, 8, tzinfo=UTC)


def _spec(name: str, tool: str) -> ScriptedLLM:
    out = AgentOutput(
        agent_name=name,
        succeeded=True,
        evidence=[Evidence(source_agent=name, kind="obs", summary="ok", collected_at=_now())],
    )
    return ScriptedLLM(
        name=name,
        responses=[
            llm_tool_call(tool, {}, call_id=f"{name}-1"),
            llm_text("done"),
            llm_text(out.model_dump_json()),
        ],
    )


@pytest.mark.asyncio
async def test_finalize_applies_fitted_calibrator() -> None:
    # Calibrator learns that a stated 0.9 is only right ~50% of the time.
    calibrator = IsotonicCalibrator().fit(_samples([(0.9, i % 2 == 0) for i in range(20)]))

    rca = ScriptedLLM(
        name="rca",
        responses=[
            llm_text(
                RCAReport(
                    root_cause="overconfident finding",
                    root_cause_category="OOMKilled",
                    confidence=0.9,
                    evidence_refs=[0],
                    reasoning="r",
                    recommendations=["x"],
                ).model_dump_json()
            )
        ],
    )
    rec = ScriptedLLM(
        name="rec",
        responses=[
            llm_text(
                json.dumps(
                    {"recommendations": [Recommendation(title="x", rationale="y").model_dump()]}
                )
            )
        ],
    )
    by_keyword = [
        ("Kubernetes specialist", _spec("kubernetes", "list_pods")),
        ("metrics specialist", _spec("metrics", "query_range")),
        ("logs specialist", _spec("logs", "search_exceptions")),
        ("Root-Cause Analysis", rca),
        ("Recommendation agent", rec),
    ]

    class Dispatcher:
        name = "dispatcher"

        async def chat(self, messages: list[Any], **kwargs: Any) -> Any:
            sys = next((m.content for m in messages if m.role == "system"), "")
            for kw, llm in by_keyword:
                if kw in sys:
                    return await llm.chat(messages, **kwargs)
            raise AssertionError(f"no scripted llm for {sys[:60]!r}")

    deps = AgentDeps(
        llm=build_router(Dispatcher()),  # type: ignore[arg-type]
        mcp_k8s=build_mcp_client(_mcp_handler("list_pods"), server_name="k8s"),
        mcp_prom=build_mcp_client(_mcp_handler("query_range"), server_name="prom"),
        mcp_loki=build_mcp_client(_mcp_handler("search_exceptions"), server_name="loki"),
        calibrator=calibrator,
    )
    try:
        graph = build_graph(deps)
        final = await graph.ainvoke(
            {
                "incident_id": uuid.uuid4(),
                "query": "why is svc failing?",
                "namespace": "prod",
                "service": "svc",
                "started_at": _now(),
            }
        )
    finally:
        for c in (deps.mcp_k8s, deps.mcp_prom, deps.mcp_loki):
            await c.aclose()

    # Raw confidence preserved; calibrated value tempered toward empirical ~0.5.
    assert final["confidence"] == pytest.approx(0.9)
    assert final["calibrated_confidence"] == pytest.approx(0.5, abs=0.06)
