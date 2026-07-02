"""Long-term incident memory (Phase 2).

Concluded investigations are embedded and stored; before the RCA agent reasons,
similar past incidents are retrieved and injected into
``InvestigationState.memory_context``.

Layers:
- ``embedder``  — text -> vector (BYOK cloud embeddings, or a deterministic hash
  embedder for dev / air-gapped / tests).
- ``store``     — vector + metadata persistence (pgvector in prod; in-memory for
  dev / tests).
- ``retriever`` — hybrid retrieval (vector similarity + metadata boost) returning
  ``PastIncident`` objects.
"""

from __future__ import annotations

from kubepilot_orch.memory.embedder import Embedder, HashEmbedder, OpenAIEmbedder
from kubepilot_orch.memory.retriever import MemoryRetriever
from kubepilot_orch.memory.store import (
    InMemoryMemoryStore,
    MemoryStore,
    StoredIncident,
)

__all__ = [
    "Embedder",
    "HashEmbedder",
    "InMemoryMemoryStore",
    "MemoryRetriever",
    "MemoryStore",
    "OpenAIEmbedder",
    "StoredIncident",
]
