# kubepilot-orch

LangGraph-based agent orchestrator: state machine, agents, LLM provider abstraction, prompt registry.

Entry points live in `src/kubepilot_orch/`:

- `state.py` — `InvestigationState` (Pydantic) + schema versioning + migration loader
- `llm/` — provider abstraction (Anthropic, OpenAI, Bedrock, Azure, Ollama, vLLM) + role-based router
- `agents/` — Supervisor, Kubernetes, Metrics, Logs, RCA, Recommendation (Phase 1)
- `graph.py` — LangGraph wiring (added in W6)
- `config.py` — Pydantic settings loaded from env / values.yaml
- `smoke_test.py` — `make smoke-test` entry point
