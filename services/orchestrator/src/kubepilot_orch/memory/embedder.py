"""Text embedders for long-term memory.

Two implementations, mirroring the LLM-provider philosophy (BYOK + local):

- ``OpenAIEmbedder`` — real cloud embeddings (BYOK).
- ``HashEmbedder``   — deterministic, dependency-free hashing embedder. Similar
  summaries (shared tokens) land near each other, which is enough for retrieving
  near-duplicate incidents. Used for dev, air-gapped installs without an
  embedding endpoint, and all unit tests (no network, fully reproducible).
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    """Turns text into a fixed-dimension vector."""

    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class HashEmbedder:
    """Deterministic hashing embedder — a normalized token-frequency vector.

    Not semantic, but stable and offline: two summaries sharing many tokens have
    high cosine similarity, which is what the retriever needs for near-duplicate
    incident recall in dev/tests.
    """

    name = "hash"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0  # signed hashing reduces collisions
            vec[idx] += sign
        return _l2_normalize(vec)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class OpenAIEmbedder:
    """Cloud embeddings via OpenAI (BYOK). ``dim`` matches the chosen model."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        dim: int = 1536,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        from langchain_openai import OpenAIEmbeddings  # lazy: keeps import light

        client = OpenAIEmbeddings(api_key=self._api_key, model=self._model)  # type: ignore[arg-type]
        return await client.aembed_documents(texts)
