"""Render a human-readable eval report: per-scenario table + aggregate baseline.

No external table dependency — plain fixed-width columns so the output is stable
in CI logs.
"""

from __future__ import annotations

from eval.harness.scorer import AggregateScore, ScoreBreakdown

_TICK = "PASS"
_CROSS = "FAIL"


def _yn(ok: bool) -> str:
    return _TICK if ok else _CROSS


def _row(b: ScoreBreakdown) -> str:
    cat = _yn(b.category_match)
    conf = (
        f"{_yn(b.confidence_ok)} ({b.actual_confidence:.2f}/{b.min_confidence:.2f})"
        if b.actual_confidence is not None
        else f"{_CROSS} (—/{b.min_confidence:.2f})"
    )
    evid = f"{_yn(b.evidence_ok)} {b.evidence_hit_count}/{len(b.evidence_hits)}"
    return f"{b.scenario_id:<34}  {cat:<4}  {conf:<18}  {evid:<10}  {b.score:.2f}"


def render_table(breakdowns: list[ScoreBreakdown]) -> str:
    header = f"{'scenario':<34}  {'cat':<4}  {'confidence':<18}  {'evidence':<10}  score"
    sep = "-" * len(header)
    lines = [header, sep]
    lines.extend(_row(b) for b in breakdowns)
    return "\n".join(lines)


def render_report(agg: AggregateScore) -> str:
    """Full report: table + aggregate summary + gate verdict."""
    table = render_table(agg.breakdowns)
    pct = agg.mean_score * 100.0
    target_pct = agg.baseline_target * 100.0
    gate = "PASS" if agg.passes_gate else "FAIL"
    summary = [
        "",
        "=" * 72,
        f"Scenarios graded      : {agg.count}",
        f"Category accuracy     : {agg.category_accuracy * 100:5.1f}%",
        f"Perfect (score=1.0)   : {agg.perfect_count}/{agg.count}",
        f"Aggregate baseline    : {pct:5.1f}%   (gate ≥ {target_pct:.0f}%)",
        f"Release gate          : {gate}",
        "=" * 72,
    ]
    return table + "\n" + "\n".join(summary)
