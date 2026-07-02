"""Pytest bootstrap for the eval suite.

``eval/`` is not under ``services/*/tests`` (the configured ``testpaths``), so it
is only collected when pytest is invoked with an explicit ``eval`` path. This
conftest guarantees the repo root is importable so ``import eval.harness.*``
resolves regardless of pytest's import mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
