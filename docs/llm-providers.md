# Configuring LLM Providers

> KubePilot AI is **provider-agnostic** and **BYOK** (bring your own key). All LLM calls flow through one abstraction; swapping Claude → GPT-4o → a local Llama is a config change, not a code change ([ARCHITECTURE.md §3.6](./ARCHITECTURE.md#36-llm-provider-layer)).

This guide covers all six supported providers, the per-role routing model, and complete `values.yaml` examples for cloud and air-gapped deployments.

---

## 1. Supported providers

| Provider | Kind | Config key (`provider:`) | Typical use |
|---|---|---|---|
| **Anthropic** | Cloud, BYOK | `anthropic` | Default; strong RCA reasoning |
| **OpenAI** | Cloud, BYOK | `openai` | GPT-4o / GPT-4o-mini |
| **Amazon Bedrock** | Cloud, BYOK (AWS-native) | `bedrock` | Claude/Llama via AWS, IAM-scoped |
| **Azure OpenAI** | Cloud, BYOK (enterprise) | `azure` | Azure-hosted GPT-4o |
| **Ollama** | Local | `ollama` | Laptop / small clusters, air-gapped |
| **vLLM** | Local | `vllm` | High-throughput GPU inference, air-gapped |

Anthropic, OpenAI, Ollama, and vLLM are fully implemented. Bedrock and Azure OpenAI are landing now — configuration is identical to what is documented here.

Only providers **actually referenced by a role binding** are instantiated at startup. An air-gapped install that routes every role to Ollama needs no Anthropic or OpenAI key configured — the factory never loads them (`services/orchestrator/src/kubepilot_orch/llm/factory.py`).

---

## 2. Role-based routing

Every LLM call declares a **role**. The router (`llm/router.py`) maps each role to a `{provider, model}` binding, so you can run a cheap model for cheap work and a strong model where accuracy matters.

The three roles (`kubepilot_orch.llm.base.Role`):

| Role | Where it's used | Recommendation |
|---|---|---|
| `routing` | Supervisor decides which sub-agent to invoke next | **Cheap + fast.** Short prompts, structured decisions. Haiku / gpt-4o-mini / an 8B local model. |
| `analysis` | RCA agent correlates evidence and reasons about root cause | **Strongest model you have.** This is where accuracy is won or lost. Sonnet/Opus / gpt-4o / a 14B+ local model. |
| `summarization` | Condensing evidence and drafting the final report | **Cheap.** A capable small model is fine. |

This is why routing is deliberately split from analysis: the supervisor makes many small routing calls, and paying analysis-model prices for them is wasteful. Conversely, under-powering the analysis role is the single biggest cause of weak RCA (see [troubleshooting.md](./troubleshooting.md#empty-or-low-confidence-rca)).

The settings shape lives in `services/orchestrator/src/kubepilot_orch/config.py`:

```python
class LLMRoleBinding(BaseModel):
    provider: str   # "anthropic" | "openai" | "ollama" | "vllm" | "bedrock" | "azure"
    model: str

class LLMSettings(BaseModel):
    default_provider: str = "anthropic"
    roles: dict[Role, LLMRoleBinding]
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    vllm_base_url: str = "http://localhost:8000/v1"
    bedrock_region: str | None = None
    azure_api_key: str | None = None
    azure_endpoint: str | None = None
```

---

## 3. Configuration surfaces

There are two ways to set provider config; they resolve to the same settings object.

### 3.1 Environment variables (local dev)

The orchestrator uses the `KUBEPILOT_` prefix with `__` as the nested delimiter (pydantic-settings). Credentials and endpoints map directly to the `LLMSettings` fields:

| Setting field | Env var |
|---|---|
| `llm.default_provider` | `KUBEPILOT_LLM__DEFAULT_PROVIDER` |
| `llm.anthropic_api_key` | `KUBEPILOT_LLM__ANTHROPIC_API_KEY` |
| `llm.openai_api_key` | `KUBEPILOT_LLM__OPENAI_API_KEY` |
| `llm.ollama_base_url` | `KUBEPILOT_LLM__OLLAMA_BASE_URL` |
| `llm.vllm_base_url` | `KUBEPILOT_LLM__VLLM_BASE_URL` |
| `llm.bedrock_region` | `KUBEPILOT_LLM__BEDROCK_REGION` |
| `llm.azure_api_key` | `KUBEPILOT_LLM__AZURE_API_KEY` |
| `llm.azure_endpoint` | `KUBEPILOT_LLM__AZURE_ENDPOINT` |
| `llm.roles` | `KUBEPILOT_LLM__ROLES` (JSON string — see below) |

Because `roles` is a nested dict, override it as a JSON value:

```bash
export KUBEPILOT_LLM__DEFAULT_PROVIDER=anthropic
export KUBEPILOT_LLM__ANTHROPIC_API_KEY=sk-ant-...
export KUBEPILOT_LLM__ROLES='{
  "routing":       {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
  "analysis":      {"provider": "anthropic", "model": "claude-sonnet-4-6"},
  "summarization": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}
}'
```

If you leave `KUBEPILOT_LLM__ROLES` unset, the code defaults (Anthropic haiku/sonnet/haiku) apply — so a single `KUBEPILOT_LLM__ANTHROPIC_API_KEY` is enough to get running.

> The smoke test also honors the bare `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` env vars when deciding whether to attempt a real LLM call, but the services themselves read the `KUBEPILOT_LLM__*` form. Prefer the prefixed form.

### 3.2 Helm `values.yaml` (cluster)

In the chart, the same config lives under `llm:` (camelCase keys map to the settings fields), with credentials sourced from a Kubernetes Secret named by `llm.secretName`:

```yaml
llm:
  defaultProvider: anthropic
  roles:
    routing:       { provider: anthropic, model: claude-haiku-4-5-20251001 }
    analysis:      { provider: anthropic, model: claude-sonnet-4-6 }
    summarization: { provider: anthropic, model: claude-haiku-4-5-20251001 }
  secretName: kubepilot-llm-credentials
  ollama:
    baseUrl: http://ollama.kubepilot-system.svc.cluster.local:11434
```

The referenced Secret carries the credential fields, keyed to match the settings (`anthropic_api_key`, `openai_api_key`, `azure_api_key`, …):

```bash
kubectl -n kubepilot-system create secret generic kubepilot-llm-credentials \
  --from-literal=anthropic_api_key=sk-ant-...
```

---

## 4. Per-provider requirements

### Anthropic
- **Needs:** `anthropic_api_key`.
- **Models:** e.g. `claude-sonnet-4-6` (analysis), `claude-haiku-4-5-20251001` (routing/summarization).

```bash
kubectl ... --from-literal=anthropic_api_key=sk-ant-...
```

### OpenAI
- **Needs:** `openai_api_key`.
- **Models:** `gpt-4o` (analysis), `gpt-4o-mini` (routing/summarization).

```bash
kubectl ... --from-literal=openai_api_key=sk-...
```

### Amazon Bedrock
- **Needs:** `bedrock_region`, plus AWS credentials resolved by the standard AWS SDK chain (IRSA / instance role / env). No API key field — auth is IAM.
- **Models:** Bedrock model IDs (Claude or Llama), e.g. `anthropic.claude-3-5-sonnet-20241022-v2:0`.

```yaml
llm:
  roles:
    analysis: { provider: bedrock, model: anthropic.claude-3-5-sonnet-20241022-v2:0 }
```
```bash
kubectl ... --from-literal=bedrock_region=us-east-1
```

### Azure OpenAI
- **Needs:** `azure_api_key` and `azure_endpoint`. The `model` value is your **deployment name**.
- **Models:** whatever you named your Azure deployment (e.g. `gpt-4o`).

```yaml
llm:
  roles:
    analysis: { provider: azure, model: my-gpt4o-deployment }
```
```bash
kubectl ... \
  --from-literal=azure_api_key=... \
  --from-literal=azure_endpoint=https://my-resource.openai.azure.com
```

### Ollama (local)
- **Needs:** a reachable Ollama server URL (`ollama_base_url`, default `http://localhost:11434`; in-cluster via `llm.ollama.baseUrl`). No API key.
- **Models:** e.g. `llama3.1:8b`, `qwen2.5:14b`. Pull them on the Ollama host first (`ollama pull qwen2.5:14b`).

### vLLM (local)
- **Needs:** an OpenAI-compatible vLLM endpoint (`vllm_base_url`, default `http://localhost:8000/v1`). No API key.
- **Models:** the model name served by your vLLM instance.
- GPU scheduling is left to the operator (see [ARCHITECTURE.md §13, decision 5](./ARCHITECTURE.md#13-resolved-architecture-decisions)) — document your NodeSelector/resource requests for the vLLM pod yourself.

---

## 5. Full example — cloud (Anthropic default)

```yaml
llm:
  defaultProvider: anthropic
  roles:
    routing:       { provider: anthropic, model: claude-haiku-4-5-20251001 }
    analysis:      { provider: anthropic, model: claude-sonnet-4-6 }
    summarization: { provider: anthropic, model: claude-haiku-4-5-20251001 }
  secretName: kubepilot-llm-credentials
```

**Mixed providers** are fine — nothing requires a single vendor. For example, route cheap work to OpenAI-mini and analysis to Claude:

```yaml
llm:
  defaultProvider: anthropic
  roles:
    routing:       { provider: openai,    model: gpt-4o-mini }
    analysis:      { provider: anthropic, model: claude-sonnet-4-6 }
    summarization: { provider: openai,    model: gpt-4o-mini }
  secretName: kubepilot-llm-credentials   # must hold BOTH openai_api_key and anthropic_api_key
```

The factory loads exactly the providers named across your role bindings, so the Secret must contain a credential for each provider you reference.

---

## 6. Air-gapped — Ollama / vLLM

No calls leave the cluster. Route every role to a local model; use a larger model for the analysis role because RCA quality degrades fastest there on small models.

```yaml
llm:
  defaultProvider: ollama
  roles:
    routing:       { provider: ollama, model: llama3.1:8b }
    analysis:      { provider: vllm,   model: qwen2.5:14b }
    summarization: { provider: ollama, model: llama3.1:8b }
  ollama:
    baseUrl: http://ollama.kubepilot-system.svc.cluster.local:11434
```

For local dev the equivalent env config:

```bash
export KUBEPILOT_LLM__DEFAULT_PROVIDER=ollama
export KUBEPILOT_LLM__OLLAMA_BASE_URL=http://localhost:11434
export KUBEPILOT_LLM__VLLM_BASE_URL=http://localhost:8000/v1
export KUBEPILOT_LLM__ROLES='{
  "routing":       {"provider": "ollama", "model": "llama3.1:8b"},
  "analysis":      {"provider": "vllm",   "model": "qwen2.5:14b"},
  "summarization": {"provider": "ollama", "model": "llama3.1:8b"}
}'
```

**Minimum model size.** Use **14B+ models for the analysis role**. Smaller models produce shallow, low-confidence RCA. If air-gapped RCA looks weak, the model is almost always the cause — bump the analysis model before anything else ([troubleshooting.md](./troubleshooting.md#air-gapped-model-quality)).

The `prod-air-gapped` Helm profile enables the bundled Ollama subchart and Phoenix (self-hosted AgentOps), so no cloud LLM **or** cloud observability endpoint is required.

---

## 7. Verifying provider config

```bash
# Local: the smoke test builds the router and (if a credential is present for the
# analysis role) makes a one-shot call.
make smoke-test

# Cluster: gateway readiness includes an LLM-provider check.
curl -s localhost:8080/ready
```

If a selected provider has no configured credential, you'll see a `ProviderNotConfigured` error — see [troubleshooting.md](./troubleshooting.md#provider-not-configured).
</content>
