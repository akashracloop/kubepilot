"""Confidence calibration (Phase 3 W7).

The RCA agent states a raw confidence. Whether a stated "0.85" is *right* ~85% of
the time is an empirical question answered by eval history. This module learns a
monotonic map raw-confidence → empirical-accuracy via **isotonic regression**
(Pool Adjacent Violators — no sklearn dependency, so it runs air-gapped) and
exposes the **Expected Calibration Error (ECE)** + a reliability curve for the
AgentOps plot.

At finalize the graph stamps ``state.calibrated_confidence`` from a fitted
calibrator (when one is wired in); otherwise the critic's interim adjustment (W2)
stands. The gate (W8) is ECE < 10% on the held-out eval set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CalibrationSample:
    """One labelled outcome: the model's stated confidence and whether it was right."""

    confidence: float
    correct: bool


@dataclass(frozen=True)
class ReliabilityBin:
    """One bin of the reliability curve (for the AgentOps calibration plot)."""

    lo: float
    hi: float
    count: int
    mean_confidence: float
    accuracy: float

    @property
    def gap(self) -> float:
        """|accuracy - confidence| — the miscalibration contributed by this bin."""
        return abs(self.accuracy - self.mean_confidence)


def reliability_curve(
    samples: list[CalibrationSample], *, n_bins: int = 10
) -> list[ReliabilityBin]:
    """Bin samples by confidence over [0, 1]; return non-empty bins low→high."""
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    buckets: list[list[CalibrationSample]] = [[] for _ in range(n_bins)]
    for s in samples:
        # Clamp then bucket; confidence == 1.0 lands in the last bin.
        c = min(max(s.confidence, 0.0), 1.0)
        idx = min(int(c * n_bins), n_bins - 1)
        buckets[idx].append(s)

    curve: list[ReliabilityBin] = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            continue
        n = len(bucket)
        mean_conf = sum(s.confidence for s in bucket) / n
        acc = sum(1 for s in bucket if s.correct) / n
        curve.append(
            ReliabilityBin(
                lo=i / n_bins,
                hi=(i + 1) / n_bins,
                count=n,
                mean_confidence=mean_conf,
                accuracy=acc,
            )
        )
    return curve


def expected_calibration_error(samples: list[CalibrationSample], *, n_bins: int = 10) -> float:
    """ECE: weighted mean |accuracy - confidence| across confidence bins. 0 = perfect."""
    if not samples:
        return 0.0
    total = len(samples)
    return sum(b.gap * b.count / total for b in reliability_curve(samples, n_bins=n_bins))


def _isotonic_pav(ys: list[float], ws: list[float]) -> list[float]:
    """Weighted Pool-Adjacent-Violators → non-decreasing fit aligned with inputs.

    ``ys`` must already be ordered by ascending x. Returns a fitted value per input.
    """
    block_y: list[float] = []
    block_w: list[float] = []
    block_len: list[int] = []
    for y, w in zip(ys, ws, strict=True):
        cy, cw, clen = y, w, 1
        # Merge with previous blocks while they violate monotonicity.
        while block_y and block_y[-1] > cy:
            py, pw, pl = block_y.pop(), block_w.pop(), block_len.pop()
            cw_new = pw + cw
            cy = (py * pw + cy * cw) / cw_new
            cw = cw_new
            clen += pl
        block_y.append(cy)
        block_w.append(cw)
        block_len.append(clen)

    out: list[float] = []
    for y, length in zip(block_y, block_len, strict=True):
        out.extend([y] * length)
    return out


class IsotonicCalibrator:
    """Monotonic raw→calibrated map fit by isotonic regression on eval history.

    Fit on ``CalibrationSample``s; ``calibrate`` maps a new raw confidence to the
    empirical accuracy learned for that region, linearly interpolating between the
    fitted breakpoints and clamping outside the observed range.
    """

    def __init__(self) -> None:
        # Parallel, ascending-by-x breakpoint arrays. Empty until fit().
        self._xs: list[float] = []
        self._ys: list[float] = []

    @property
    def is_fitted(self) -> bool:
        return bool(self._xs)

    def fit(self, samples: list[CalibrationSample]) -> IsotonicCalibrator:
        if not samples:
            self._xs, self._ys = [], []
            return self
        ordered = sorted(samples, key=lambda s: s.confidence)
        xs = [min(max(s.confidence, 0.0), 1.0) for s in ordered]
        ys = [1.0 if s.correct else 0.0 for s in ordered]
        ws = [1.0] * len(ordered)
        fitted = _isotonic_pav(ys, ws)
        # Collapse duplicate x's (average their fitted y) so the map is a function.
        self._xs, self._ys = _collapse(xs, fitted)
        return self

    def calibrate(self, confidence: float) -> float:
        """Map a raw confidence to its calibrated value. Identity if unfitted."""
        if not self._xs:
            return confidence
        x = min(max(confidence, 0.0), 1.0)
        if x <= self._xs[0]:
            return self._ys[0]
        if x >= self._xs[-1]:
            return self._ys[-1]
        # Linear interpolation between the two bracketing breakpoints.
        lo = 0
        hi = len(self._xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self._xs[mid] <= x:
                lo = mid
            else:
                hi = mid
        x0, x1 = self._xs[lo], self._xs[hi]
        y0, y1 = self._ys[lo], self._ys[hi]
        if x1 == x0:
            return y0
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form for persistence / shipping a trained calibrator."""
        return {"kind": "isotonic", "xs": list(self._xs), "ys": list(self._ys)}

    @classmethod
    def from_dict(cls, blob: dict[str, Any]) -> IsotonicCalibrator:
        cal = cls()
        cal._xs = [float(x) for x in blob.get("xs", [])]
        cal._ys = [float(y) for y in blob.get("ys", [])]
        return cal


def _collapse(xs: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
    """Average ys for equal xs, preserving ascending order (xs already sorted)."""
    out_x: list[float] = []
    out_y: list[float] = []
    i = 0
    n = len(xs)
    while i < n:
        j = i
        acc = 0.0
        while j < n and xs[j] == xs[i]:
            acc += ys[j]
            j += 1
        out_x.append(xs[i])
        out_y.append(acc / (j - i))
        i = j
    return out_x, out_y


@dataclass
class CalibrationReport:
    """Calibration metrics for a set of samples (ECE + curve)."""

    n: int
    ece: float
    curve: list[ReliabilityBin] = field(default_factory=list)

    def within(self, max_ece: float) -> bool:
        return self.ece <= max_ece


def calibration_report(samples: list[CalibrationSample], *, n_bins: int = 10) -> CalibrationReport:
    return CalibrationReport(
        n=len(samples),
        ece=expected_calibration_error(samples, n_bins=n_bins),
        curve=reliability_curve(samples, n_bins=n_bins),
    )
