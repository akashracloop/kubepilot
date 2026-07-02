"""GET /calibration — expose the confidence-calibration map for the plot (A5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from kubepilot_api.config import ApiSettings
from kubepilot_api.main import build_app
from kubepilot_api.repository import InMemoryInvestigationRepository
from kubepilot_orch.calibration import CalibrationSample, IsotonicCalibrator


class _FakeGraph:
    async def astream(self, initial: dict[str, Any], *, stream_mode=None, config=None):  # type: ignore[no-untyped-def]
        yield ("values", {**initial, "current_step": "completed"})


def _app(calibrator_path: str | None) -> Any:
    settings = ApiSettings(storage="memory")
    settings.calibrator_path = calibrator_path
    return build_app(
        settings=settings, repo=InMemoryInvestigationRepository(), compiled_graph=_FakeGraph()
    )


@pytest_asyncio.fixture
async def client_factory():  # type: ignore[no-untyped-def]
    clients: list[httpx.AsyncClient] = []

    async def make(calibrator_path: str | None) -> httpx.AsyncClient:
        c = httpx.AsyncClient(
            transport=ASGITransport(app=_app(calibrator_path)), base_url="http://test"
        )
        clients.append(c)
        return c

    yield make
    for c in clients:
        await c.aclose()


async def test_calibration_empty_when_unconfigured(client_factory) -> None:  # type: ignore[no-untyped-def]
    client = await client_factory(None)
    r = await client.get("/calibration")
    assert r.status_code == 200
    assert r.json() == {"fitted": False, "curve": []}


async def test_calibration_returns_curve_when_fitted(client_factory, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cal = IsotonicCalibrator().fit(
        [CalibrationSample(confidence=0.9, correct=i % 2 == 0) for i in range(20)]
    )
    path = tmp_path / "cal.json"
    path.write_text(json.dumps(cal.to_dict()), encoding="utf-8")

    client = await client_factory(str(path))
    body = (await client.get("/calibration")).json()
    assert body["fitted"] is True
    assert body["curve"]
    assert set(body["curve"][0]) == {"raw", "calibrated"}


def test_calibrator_curve_shape() -> None:
    cal = IsotonicCalibrator().fit(
        [
            CalibrationSample(confidence=0.2, correct=False),
            CalibrationSample(confidence=0.8, correct=True),
        ]
    )
    curve = cal.curve()
    assert all(set(p) == {"raw", "calibrated"} for p in curve)
    assert [p["raw"] for p in curve] == sorted(p["raw"] for p in curve)


def test_unfitted_curve_is_empty() -> None:
    assert IsotonicCalibrator().curve() == []


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
