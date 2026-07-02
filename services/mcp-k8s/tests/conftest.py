"""Shared test fixtures.

Tests must never reach a real cluster. Two layers of insulation:

1. ``_block_kube_config_load`` (autouse) replaces the kubeconfig loader with
   a no-op so importing tool modules can't trigger a kube-config read.
2. ``core_v1`` / ``apps_v1`` patch the underlying ``kubernetes.client.*Api``
   classes so any handler that constructs one gets the mock — regardless of
   how it imported ``get_core_v1``.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _block_kube_config_load() -> Iterator[None]:
    with patch("mcp_k8s.client._load_config", lambda: None):
        yield


@pytest.fixture
def core_v1() -> Iterator[MagicMock]:
    fake = MagicMock()
    # Patch the class so every CoreV1Api() construction returns our fake.
    with patch("kubernetes.client.CoreV1Api", return_value=fake):
        yield fake


@pytest.fixture
def apps_v1() -> Iterator[MagicMock]:
    fake = MagicMock()
    with patch("kubernetes.client.AppsV1Api", return_value=fake):
        yield fake
