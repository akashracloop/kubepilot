# KubePilot AI — Golden RCA Eval Harness

Phase-1 evaluation suite for the autonomous incident investigator. It measures
whether the multi-agent graph reaches the **correct root cause, with adequate
confidence, citing the right evidence** across a hand-authored set of Kubernetes
failure scenarios (PHASE_1_PLAN §7).

```
eval/
├── datasets/
│   └── golden_rca_scenarios.jsonl   # 22 hand-authored scenarios (one JSON/line)
├── harness/
│   ├── loader.py                    # .jsonl → typed Scenario/Expected models
│   ├── runner.py                    # scenario + LLM → InvestigationState
│   ├── scorer.py                    # the §7.2 score formula
│   ├── report.py                    # per-scenario table + aggregate baseline
│   ├── run_eval.py                  # `python -m eval.harness.run_eval` (live)
│   └── test_harness.py              # deterministic CI self-test (no LLM)
├── conftest.py                      # makes `eval.harness.*` importable under pytest
└── README.md
```

## Two ways to run

### 1. Live accuracy path — `make eval`

Runs **all** scenarios through the full investigation graph against a **real
LLM** and prints the score report. Exits non-zero if the aggregate baseline
drops below **70%**.

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # or OPENAI_API_KEY=sk-...
make eval
# equivalently: uv run python -m eval.harness.run_eval
```

Provider selection (BYOK, in priority order):

| Env var             | Provider  | Default model (override with `KUBEPILOT_EVAL_MODEL`) |
|---------------------|-----------|------------------------------------------------------|
| `ANTHROPIC_API_KEY` | Anthropic | `claude-sonnet-4-6`                                  |
| `OPENAI_API_KEY`    | OpenAI    | `gpt-4o`                                             |

With no key set, `run_eval` exits with a message explaining this is the live
path and points you at the self-test below.

The MCP servers are **not** contacted over the network: each scenario ships a
canned `fixture` that is served in-process through an `httpx.MockTransport`, so
the eval is hermetic and needs no cluster, Prometheus, or Loki.

### 2. Deterministic self-test — `uv run pytest eval`

Validates the **harness itself** (scorer math + runner/transport wiring) with a
scripted LLM. Never calls a real model, so it is safe to run on every commit.

```bash
uv run pytest eval               # or: make eval-test
```

`eval/` is not under the configured `testpaths` (`services/*/tests`), so it is
only collected when you pass the explicit `eval` path.

## Score formula (§7.2)

Each scenario is graded on three equally-weighted, binary components:

```
score = ( category_correct          # rca.root_cause_category == expected (case-insensitive)
        + confidence_within_tol      # rca.confidence >= min_confidence - 0.05
        + evidence_present ) / 3     # every must_mention_evidence substring appears
                                     #   in the RCA text or collected evidence
```

So a per-scenario score is one of `0.0, 0.33, 0.67, 1.0`. The **aggregate** is
the mean across all scenarios.

### Baseline gate

The v0.1.0 release gate is **aggregate ≥ 70%**. `make eval` exits non-zero below
that threshold so CI can block a regression. (Improving the baseline toward ≥85%
is a later-phase goal.)

## Dataset format

```json
{
  "id": "java-spring-oom-001",
  "query": "why is payment-service failing?",
  "namespace": "prod",
  "service": "payment-service",
  "fixture": {
    "mcp-k8s":  { "list_pods": [...], "get_events": [...] },
    "mcp-prom": { "query_range": {...} },
    "mcp-loki": { "search_exceptions": {...} }
  },
  "expected": {
    "root_cause_category": "OOMKilled",
    "min_confidence": 0.7,
    "must_mention_evidence": ["memory", "restart", "137"]
  }
}
```

`fixture` maps each MCP server name to a `{tool_name: canned_result}` dict. The
runner serves those results for `POST /mcp/invoke` and derives `GET /mcp/tools`
descriptors from the staged tool names.

### Coverage (22 scenarios)

CrashLoopBackOff (Java OOM, Python segfault, Node uncaught exception, Go panic,
Go fatal, Python OOM) · ImagePullBackOff (auth, typo, registry down) ·
ConfigMap/Secret errors · Pending (insufficient resources, NodeSelector, taint) ·
Service selector mismatch · recent-deploy 5xx spike · DNS failure ·
PVC full / node disk pressure · NetworkPolicy block · Readiness/Liveness probe
failures.
