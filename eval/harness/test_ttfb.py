"""Deterministic self-test for the TTFB latency gate (Phase 1/2 gap fix).

No live LLM: exercises the median/p95 math and the <5s gate decision from
synthetic samples. The live measurement (measure_ttfb) is the keyed path.
"""

from __future__ import annotations

from eval.harness.ttfb import TtfbSample, render_ttfb, summarize_ttfb


def _samples(values: list[float]) -> list[TtfbSample]:
    return [TtfbSample(scenario_id=f"s{i}", ttfb_s=v) for i, v in enumerate(values)]


def test_median_and_p95_over_samples() -> None:
    report = summarize_ttfb(_samples([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert report.median_s == 3.0
    assert report.max_s == 5.0
    assert report.p95_s == 5.0


def test_gate_passes_when_median_under_threshold() -> None:
    # Median 2.0s < 5s, even with one slow outlier.
    report = summarize_ttfb(_samples([1.0, 2.0, 2.0, 12.0]), threshold_s=5.0)
    assert report.median_s == 2.0
    assert report.passes_gate is True


def test_gate_fails_when_median_exceeds_threshold() -> None:
    report = summarize_ttfb(_samples([6.0, 7.0, 8.0]), threshold_s=5.0)
    assert report.passes_gate is False
    assert "FAIL" in render_ttfb(report)


def test_empty_run_does_not_fail_gate() -> None:
    report = summarize_ttfb([], threshold_s=5.0)
    assert report.passes_gate is True
    assert report.median_s == 0.0
