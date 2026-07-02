"""Real-apply dispatch for the write tools (Phase 4 gap fix).

No live cluster: the kubernetes client is replaced with fakes so we can assert
each tool maps to the correct API verb + request body, and that ``dry_run`` flows
through as server-side ``dryRun=All``. The end-to-end real mutation is exercised
in the kind sandbox (remediation-e2e.yml).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from mcp_k8s_write import apply as apply_mod
from mcp_k8s_write import server as server_mod


class _FakeApi:
    """Records the last call as (method, args, kwargs)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        def _rec(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return None

        return _rec


@pytest.fixture
def apps(monkeypatch: pytest.MonkeyPatch) -> _FakeApi:
    fake = _FakeApi()
    monkeypatch.setattr(apply_mod, "get_apps_v1", lambda: fake)
    return fake


@pytest.fixture
def core(monkeypatch: pytest.MonkeyPatch) -> _FakeApi:
    fake = _FakeApi()
    monkeypatch.setattr(apply_mod, "get_core_v1", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_scale_patches_deployment_scale(apps: _FakeApi) -> None:
    out = await apply_mod.apply_tool(
        "scale", "prod", "deployment/checkout", {"replicas": 5}, dry_run=False
    )
    assert out == {"applied": True, "note": "scaled checkout to 5 replicas"}
    method, args, kwargs = apps.calls[-1]
    assert method == "patch_namespaced_deployment_scale"
    assert args[0] == "checkout" and args[1] == "prod"
    assert args[2] == {"spec": {"replicas": 5}}
    assert kwargs["dry_run"] is None


@pytest.mark.asyncio
async def test_dry_run_flows_as_server_side_dryrun(apps: _FakeApi) -> None:
    out = await apply_mod.apply_tool(
        "scale", "prod", "deployment/checkout", {"replicas": 2}, dry_run=True
    )
    assert out["applied"] is False
    method, args, kwargs = apps.calls[-1]
    assert method == "patch_namespaced_deployment_scale"
    assert args[2] == {"spec": {"replicas": 2}}
    # dry_run flows through as the server-side ["All"] selector.
    assert kwargs["dry_run"] == ["All"]


@pytest.mark.asyncio
async def test_patch_image_sets_container_image(apps: _FakeApi) -> None:
    await apply_mod.apply_tool(
        "patch_image",
        "prod",
        "deployment/api",
        {"container": "api", "image": "api:v2"},
        dry_run=False,
    )
    method, args, _ = apps.calls[-1]
    assert method == "patch_namespaced_deployment"
    assert args[2] == {
        "spec": {"template": {"spec": {"containers": [{"name": "api", "image": "api:v2"}]}}}
    }


@pytest.mark.asyncio
async def test_rollout_restart_stamps_annotation(apps: _FakeApi) -> None:
    await apply_mod.apply_tool("rollout_restart", "prod", "deployment/api", {}, dry_run=False)
    method, args, _ = apps.calls[-1]
    assert method == "patch_namespaced_deployment"
    ann = args[2]["spec"]["template"]["metadata"]["annotations"]
    assert apply_mod._RESTART_ANNOTATION in ann


@pytest.mark.asyncio
async def test_restart_pod_deletes_pod(core: _FakeApi) -> None:
    await apply_mod.apply_tool("restart_pod", "prod", "checkout-abc123", {}, dry_run=False)
    method, args, _ = core.calls[-1]
    assert method == "delete_namespaced_pod"
    assert args[0] == "checkout-abc123" and args[1] == "prod"


@pytest.mark.asyncio
async def test_cordon_and_uncordon_toggle_unschedulable(core: _FakeApi) -> None:
    await apply_mod.apply_tool("cordon", None, "node-1", {}, dry_run=False)
    assert core.calls[-1][0] == "patch_node"
    assert core.calls[-1][1][1] == {"spec": {"unschedulable": True}}
    await apply_mod.apply_tool("uncordon", None, "node-1", {}, dry_run=False)
    assert core.calls[-1][1][1] == {"spec": {"unschedulable": False}}


@pytest.mark.asyncio
async def test_edit_configmap_patches_data(core: _FakeApi) -> None:
    await apply_mod.apply_tool(
        "edit_configmap", "prod", "cm/app", {"data": {"LEVEL": "info"}}, dry_run=False
    )
    method, args, _ = core.calls[-1]
    assert method == "patch_namespaced_config_map"
    assert args[2] == {"data": {"LEVEL": "info"}}


@pytest.mark.asyncio
async def test_rollout_undo_targets_prior_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    dep = SimpleNamespace(
        metadata=SimpleNamespace(
            annotations={"deployment.kubernetes.io/revision": "3"}, uid="dep-uid"
        ),
        spec=SimpleNamespace(selector=SimpleNamespace(match_labels={"app": "checkout"})),
    )
    template = SimpleNamespace(
        metadata=SimpleNamespace(labels={"app": "checkout", "pod-template-hash": "old"})
    )

    def _rs(rev: str, uid: str) -> SimpleNamespace:
        return SimpleNamespace(
            metadata=SimpleNamespace(
                annotations={"deployment.kubernetes.io/revision": rev},
                owner_references=[SimpleNamespace(uid=uid)],
            ),
            spec=SimpleNamespace(template=template if rev == "2" else SimpleNamespace()),
        )

    class _Apps:
        def __init__(self) -> None:
            self.patched: dict[str, Any] | None = None
            self.api_client = SimpleNamespace(
                sanitize_for_serialization=lambda t: {"sanitized": True}
            )

        def read_namespaced_deployment(self, name: str, ns: str) -> Any:
            return dep

        def list_namespaced_replica_set(self, ns: str, label_selector: str | None = None) -> Any:
            return SimpleNamespace(items=[_rs("2", "dep-uid"), _rs("3", "dep-uid")])

        def patch_namespaced_deployment(
            self, name: str, ns: str, body: Any, dry_run: Any = None
        ) -> Any:
            self.patched = {"name": name, "body": body, "dry_run": dry_run}

    fake = _Apps()
    monkeypatch.setattr(apply_mod, "get_apps_v1", lambda: fake)

    out = await apply_mod.apply_tool(
        "rollout_undo", "prod", "deployment/checkout", {}, dry_run=False
    )
    assert out == {"applied": True, "note": "rolled back checkout to revision 2"}
    assert fake.patched is not None
    assert fake.patched["body"] == {"spec": {"template": {"sanitized": True}}}
    # pod-template-hash stripped from the copied template before the patch.
    assert "pod-template-hash" not in template.metadata.labels


@pytest.mark.asyncio
async def test_rollout_undo_without_prior_revision_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    dep = SimpleNamespace(
        metadata=SimpleNamespace(
            annotations={"deployment.kubernetes.io/revision": "1"}, uid="dep-uid"
        ),
        spec=SimpleNamespace(selector=SimpleNamespace(match_labels={"app": "x"})),
    )

    class _Apps:
        api_client = SimpleNamespace(sanitize_for_serialization=lambda t: {})

        def read_namespaced_deployment(self, name: str, ns: str) -> Any:
            return dep

        def list_namespaced_replica_set(self, ns: str, label_selector: str | None = None) -> Any:
            return SimpleNamespace(items=[])

    monkeypatch.setattr(apply_mod, "get_apps_v1", lambda: _Apps())
    with pytest.raises(apply_mod.ApplyError):
        await apply_mod.apply_tool("rollout_undo", "prod", "deployment/x", {}, dry_run=False)


async def _server_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=server_mod.app), base_url="http://write")


@pytest.mark.asyncio
async def test_invoke_applies_when_gate_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the apply gate ON, a non-preview invoke performs the real mutation."""
    monkeypatch.setenv("KUBEPILOT_WRITE_APPLY_ENABLED", "true")

    async def _fake_apply(tool, ns, target, args, *, dry_run):  # type: ignore[no-untyped-def]
        return {"applied": True, "note": f"scaled {target}"}

    monkeypatch.setattr(server_mod, "apply_tool", _fake_apply)
    async with await _server_client() as c:
        resp = await c.post(
            "/mcp/invoke",
            json={
                "tool": "scale",
                "arguments": {"namespace": "prod", "target": "deployment/checkout", "replicas": 3},
            },
        )
    result = resp.json()["result"]
    assert result["applied"] is True
    assert result["dry_run"] is False


@pytest.mark.asyncio
async def test_invoke_apply_failure_surfaces_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """An apply error is never a silent success — the server returns 502."""
    monkeypatch.setenv("KUBEPILOT_WRITE_APPLY_ENABLED", "true")

    async def _boom(tool, ns, target, args, *, dry_run):  # type: ignore[no-untyped-def]
        raise apply_mod.ApplyError("apiserver 409 Conflict")

    monkeypatch.setattr(server_mod, "apply_tool", _boom)
    async with await _server_client() as c:
        resp = await c.post(
            "/mcp/invoke",
            json={
                "tool": "scale",
                "arguments": {"namespace": "prod", "target": "d/x", "replicas": 1},
            },
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_gate_closed_stays_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the gate OFF, even a real-apply request applies nothing."""
    monkeypatch.setenv("KUBEPILOT_WRITE_APPLY_ENABLED", "false")
    async with await _server_client() as c:
        resp = await c.post(
            "/mcp/invoke",
            json={
                "tool": "scale",
                "arguments": {"namespace": "prod", "target": "d/x", "replicas": 1},
            },
        )
    result = resp.json()["result"]
    assert result["applied"] is False
    assert result["dry_run"] is True
    assert any("false" in w for w in result["warnings"])
