# Long-Term Incident Memory (pgvector)

> Phase 2. KubePilot remembers past incidents. When an investigation concludes it
> is embedded and stored; before the RCA agent reasons on the *next* incident,
> similar past incidents are retrieved and injected as context. This is the
> long-term memory described in
> [ARCHITECTURE.md §7.2](../reference/architecture.md#72-long-term-memory-phase-2) — the
> short-term LangGraph checkpointer (§7.1) is unchanged.
>
> Memory is **corroborating context, never an override.** Retrieved incidents
> help the RCA agent recognize a recurring pattern faster and more confidently;
> they never replace the current signals. If memory and the live evidence
> disagree, the live evidence wins.

---

## 1. How it works

Two touch-points bracket the RCA step:

```text
specialists (k8s ∥ metrics ∥ logs ∥ tracing ∥ deployment)
        │  evidence
        ▼
   memory RETRIEVE  ──►  state.memory_context = [PastIncident, ...]   (before RCA)
        ▼
      RCA agent  (weighs memory_context alongside live evidence)
        ▼
      finalize
        │
        └──► memory INDEX  (embed this concluded incident for future recall)
```

- **Retrieve-before-RCA.** The `memory_agent` node
  (`agents/memory_agent.py`) is **not** an LLM agent. It builds a retrieval
  query from the current investigation — the question, the target service, and
  the top evidence summaries — asks the retriever for the *k* most similar past
  incidents (default `k=3`), and populates `state.memory_context`. The RCA
  prompt gains a "Similar past incidents" section.
- **Embed-on-finalize.** When an investigation concludes with an RCA,
  `index_incident` embeds a compact summary (the query + service + root cause +
  category) and writes it to the store so a future investigation can recall it.
  Indexing happens off the reasoning hot path.
- **Hybrid retrieval.** Retrieval is dense-vector similarity **plus** lightweight
  metadata boosts: candidates are over-fetched by cosine similarity, then
  re-ranked with a same-service boost (default `+0.10`) and a same-category
  boost (default `+0.05`) before the top *k* are returned
  (`memory/retriever.py`). The metadata boost is the "hybrid" signal on top of
  dense similarity; a full BM25 term index over metadata is a later refinement.

Retrieval is namespace-scoped: the store filters candidates to the current
investigation's namespace before ranking, so an investigation only recalls
incidents from its own namespace.

---

## 2. The two embedders (BYOK vs offline)

Embedding mirrors the LLM-provider philosophy — a cloud BYOK option and a fully
offline default (`memory/embedder.py`):

| Embedder | When it's used | Notes |
|---|---|---|
| **`HashEmbedder`** (default) | Dev, air-gapped installs with no embedding endpoint, and all unit tests | Deterministic, dependency-free, offline. A normalized signed token-frequency vector (`dim=256`): summaries that share tokens land near each other — enough for near-duplicate incident recall. Not semantic. |
| **`OpenAIEmbedder`** (BYOK) | When an OpenAI key is configured | Real cloud embeddings via `text-embedding-3-small` (`dim=1536`). |

The gateway picks the embedder automatically: if an OpenAI API key is present
(`orch_settings.llm.openai_api_key`) it uses `OpenAIEmbedder`, otherwise it falls
back to `HashEmbedder` (`services/api-gateway/src/kubepilot_api/main.py` →
`_build_memory`). The store dimension follows the embedder's `dim`, so the two
stay consistent.

> **Air-gapped:** the hash embedder needs no network and no model, so memory
> works out-of-the-box in a disconnected cluster. Because it is not semantic,
> recall is near-duplicate rather than conceptual — which is exactly what the
> recurring-incident use case needs.

---

## 3. The two stores (dev vs prod)

| Store | When it's used | Backing |
|---|---|---|
| **`InMemoryMemoryStore`** | Dev / tests (`storage != "postgres"`) | List-backed cosine search. Non-persistent — lost on restart. |
| **`PgVectorMemoryStore`** | Prod (`storage == "postgres"`) | pgvector-backed. |

The prod store uses the **bundled** `pgvector/pgvector:pg16` Postgres image — the
same database the LangGraph checkpointer already runs on, so **no extra
component** is required (pgvector ships enabled). On first use it idempotently
runs `CREATE EXTENSION IF NOT EXISTS vector` and creates the
`incident_embeddings` table:

```sql
CREATE TABLE IF NOT EXISTS incident_embeddings (
    incident_id         UUID PRIMARY KEY,
    summary             TEXT NOT NULL,
    embedding           vector(<dim>) NOT NULL,
    root_cause_category TEXT,
    namespace           TEXT,
    service             TEXT,
    outcome             TEXT,
    occurred_at         TIMESTAMPTZ
);
```

Nearest-neighbour search uses pgvector's cosine-distance operator `<=>`
(similarity = `1 - distance`), ordered and limited server-side. `psycopg` is
imported lazily, so a dev machine without libpq can still run the in-memory path.

---

## 4. Enabling / disabling memory

Memory is controlled by a single setting, **on by default**:

| Setting | Env var | Default | Meaning |
|---|---|---|---|
| `memory_enabled` | `KUBEPILOT_API_MEMORY_ENABLED` | `true` | When on, retrieve-before-RCA + index-on-finalize are wired in. When off, the gateway passes no retriever into the graph and the memory node is a no-op. |

When enabled with `storage=postgres` you get the persistent pgvector store; with
any other storage backend you get the non-persistent in-process store (useful in
dev without a DB).

**Opt out for air-gapped or privacy-sensitive installs.** Set the gateway's
`KUBEPILOT_API_MEMORY_ENABLED=false` to run KubePilot with no long-term memory at
all. Investigations still complete exactly as in Phase 1 — they just don't recall
or record past incidents.

```bash
# Local dev — disable memory on the gateway
export KUBEPILOT_API_MEMORY_ENABLED=false
```

In-cluster, inject that env var through the gateway's `extraEnv` values block
(there is no dedicated chart flag yet):

```yaml
apiGateway:
  extraEnv:
    KUBEPILOT_API_MEMORY_ENABLED: "false"
```

---

## 5. Relationship to the RCA agent

The memory node runs **after** the specialists and **before** RCA, so retrieval
does not block the parallel evidence-gathering fan-out. The RCA prompt consumes
`state.memory_context` as a distinct, clearly-labeled input — the agent is
instructed to treat prior incidents as a hypothesis to check against the current
evidence, not as ground truth. This is the deliberate design guarantee: **memory
speeds up recognition of known patterns; it never overrides what the current
cluster signals say.**

## Next steps

- [Tracing & deployment specialists](tracing-and-ci.md) — the other Phase 2 signals
- [LLM providers](../configuration/llm-providers.md) — the BYOK model that the OpenAI embedder shares
- [Architecture §7](../reference/architecture.md#7-memory-architecture) — the memory design in context
