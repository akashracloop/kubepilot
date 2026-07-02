"""Versioned prompt registry (Phase 3).

Prompts are version-controlled ``.md`` files under ``prompts/``. Phase 1/2 used a
single flat file per agent (``rca_agent.md``); Phase 3 adds *versioned* variants so
we can roll a prompt forward, A/B two versions against the eval harness (W9), and
roll back without a code change — while recording which version produced each RCA
in ``InvestigationState.prompt_versions``.

Naming convention
-----------------
* ``{name}.md``          → the baseline, addressed as version ``v1``.
* ``{name}.v{N}.md``     → an explicit versioned variant (``v2``, ``v3``, …).

If both ``{name}.md`` and ``{name}.v1.md`` exist the explicit ``v1`` file wins; the
bare file is only an implicit ``v1`` alias when no explicit ``v1`` is present. This
keeps every existing flat prompt resolvable as ``v1`` with zero migration.

Resolution
----------
* ``resolve(name, version)`` returns ``(version, text)`` for an exact version.
* ``active_version(name)`` is the version served when a caller does not pin one.
  It defaults to the highest available version, but can be overridden per-name
  (via the ``active`` map / config) so an operator can pin or roll back.
* ``select_ab(name, key)`` deterministically splits traffic between the configured
  A/B pair for a name, hashed on a stable key (e.g. the incident id) so a given
  incident always lands on the same arm — the foundation the W9 A/B harness uses.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Matches "name.v3.md" → (name, 3). Bare "name.md" is handled separately as v1.
_VERSIONED_RE = re.compile(r"^(?P<name>.+)\.v(?P<num>\d+)$")


def _version_key(version: str) -> int:
    """Sort key for a ``vN`` string; raises on a malformed version."""
    if not version.startswith("v") or not version[1:].isdigit():
        raise ValueError(f"Malformed prompt version: {version!r} (expected 'vN')")
    return int(version[1:])


@dataclass(frozen=True)
class ABConfig:
    """An A/B assignment for one prompt name: split ``fraction`` of traffic to ``b``."""

    a: str
    b: str
    fraction: float = 0.5  # share routed to arm ``b`` (0.0 to 1.0)


@dataclass
class PromptRegistry:
    """Resolves versioned prompts from a directory of ``.md`` files."""

    prompts_dir: Path = _PROMPTS_DIR
    #: Per-name pinned active version, e.g. {"rca_agent": "v2"}. Overrides "latest".
    active: dict[str, str] = field(default_factory=dict)
    #: Per-name A/B split used by ``select_ab``.
    ab: dict[str, ABConfig] = field(default_factory=dict)

    # -- discovery ---------------------------------------------------------
    def versions(self, name: str) -> list[str]:
        """All available versions for ``name``, ascending (``["v1", "v2"]``)."""
        found: set[str] = set()
        for path in self.prompts_dir.glob(f"{name}.v*.md"):
            m = _VERSIONED_RE.match(path.stem)
            if m and m.group("name") == name:
                found.add(f"v{int(m.group('num'))}")
        # Bare file is an implicit v1 only if no explicit v1 file exists.
        if (self.prompts_dir / f"{name}.md").exists():
            found.add("v1")
        if not found:
            raise FileNotFoundError(f"No prompt versions for {name!r} in {self.prompts_dir}")
        return sorted(found, key=_version_key)

    def _path(self, name: str, version: str) -> Path:
        """Filesystem path for a specific version, honoring the bare-file v1 alias."""
        explicit = self.prompts_dir / f"{name}.{version}.md"
        if explicit.exists():
            return explicit
        if version == "v1":
            bare = self.prompts_dir / f"{name}.md"
            if bare.exists():
                return bare
        raise FileNotFoundError(f"Prompt not found: {name} {version}")

    # -- resolution --------------------------------------------------------
    def active_version(self, name: str) -> str:
        """Version served when the caller pins none: an explicit override, else latest."""
        if name in self.active:
            pinned = self.active[name]
            if pinned not in self.versions(name):
                raise FileNotFoundError(
                    f"Pinned active version {pinned!r} for {name!r} does not exist"
                )
            return pinned
        return self.versions(name)[-1]

    def resolve(self, name: str, version: str | None = None) -> tuple[str, str]:
        """Return ``(version, text)``. ``version=None`` uses :meth:`active_version`."""
        resolved = version or self.active_version(name)
        text = self._path(name, resolved).read_text(encoding="utf-8")
        return resolved, text

    def render(self, name: str, version: str | None = None) -> str:
        """Convenience: just the prompt text (drops the version)."""
        return self.resolve(name, version)[1]

    # -- A/B ---------------------------------------------------------------
    def select_ab(self, name: str, key: str) -> str:
        """Deterministically choose an A/B arm version for ``name`` given a stable ``key``.

        With no A/B configured for ``name`` this is just :meth:`active_version`. The
        hash is stable across processes (unlike ``hash()``), so the same incident id
        always routes to the same arm — required for coherent A/B measurement (W9).
        """
        cfg = self.ab.get(name)
        if cfg is None:
            return self.active_version(name)
        digest = hashlib.sha256(f"{name}:{key}".encode()).digest()
        # First 8 bytes → a fraction in [0, 1).
        bucket = int.from_bytes(digest[:8], "big") / float(1 << 64)
        return cfg.b if bucket < cfg.fraction else cfg.a


@lru_cache(maxsize=1)
def default_registry() -> PromptRegistry:
    """Process-wide default registry (no pins, no A/B) over the packaged prompts.

    The returned instance is shared and mutable: the api-gateway applies active-
    version pins / A/B config to it at startup (rollback is a config flip + restart).
    """
    return PromptRegistry()


def resolve_prompt(
    name: str, *, key: str | None = None, registry: PromptRegistry | None = None
) -> tuple[str, str]:
    """Resolve ``(version, text)`` for a prompt, honoring A/B when a ``key`` is given.

    ``key`` (e.g. the incident id) routes to a deterministic A/B arm via
    :meth:`PromptRegistry.select_ab`; without a key the active version is served.
    Deterministic: the same (name, key) always resolves the same version, so a
    node can re-resolve purely to *record* the version without risking divergence
    from the version the agent actually used.
    """
    reg = registry or default_registry()
    version = reg.select_ab(name, key) if key is not None else reg.active_version(name)
    return reg.resolve(name, version)
