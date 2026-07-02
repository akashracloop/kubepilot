# Contributing to KubePilot AI

Thanks for helping build KubePilot AI — an open-source Agentic SRE platform for Kubernetes. This guide covers dev setup, code style, tests, the state-schema discipline, and how we keep scope tight across phases.

By contributing you agree your work is licensed under [Apache 2.0](LICENSE).

---

## 1. Ground rules

- **Read the context first.** [IDEA.md](IDEA.md) (product), [docs/reference/architecture.md](docs/reference/architecture.md) (engineering), and the [phase plans](docs/reference/) + [roadmap](docs/reference/roadmap.md) (Phases 1–3 are implemented; Phase 4 is writes).
- **Respect the locked product decisions.** Read-only **through Phase 3**, self-hosted via Helm, Grafana LGTM + a pluggable observability adapter, BYOK multi-provider + local models, workload-agnostic. PRs that violate these will be asked to change.
- **Be kind.** See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## 2. Development setup

This is a `uv` workspace (monorepo). Prerequisites: [uv](https://docs.astral.sh/uv/), Docker, and — for end-to-end work — [kind](https://kind.sigs.k8s.io/), `kubectl`, `helm`.

```bash
make install       # uv sync --all-packages (creates .venv, fetches Python 3.12)
make dev-up        # local Postgres (pgvector/pgvector:pg16) + Redis via docker-compose
make smoke-test    # verify config + DB + Redis + LLM wiring
```

Full details in [docs/install.md](docs/getting-started/install.md). Common `make` targets:

| Target | What it does |
|---|---|
| `make install` | Install all workspace deps |
| `make dev-up` / `dev-down` / `dev-reset` | Start / stop / wipe local Postgres + Redis |
| `make smoke-test` | Validate LLM provider + DB connectivity |
| `make kind-up` / `kind-down` | Local kind cluster + Prometheus/Loki |
| `make lint` | ruff check + `ruff format --check` |
| `make format` | Auto-format (`ruff format` + `ruff check --fix`) |
| `make typecheck` | mypy (strict) over `services/` |
| `make test` | Unit tests (excludes `integration` + `live_llm`) |
| `make test-integration` | Integration tests (needs `make dev-up`) |
| `make check` | lint + typecheck + tests — **run this before pushing** |

---

## 3. Code style

Style is enforced by tooling; the config in [`pyproject.toml`](pyproject.toml) is the source of truth. Don't hand-tune formatting — run `make format`.

- **ruff** — line length **100**, target **py312**. Lint rule sets: `E/W` (pycodestyle), `F` (pyflakes), `I` (isort), `B` (bugbear), `UP` (pyupgrade), `SIM` (simplify), `RUF`, `ASYNC`, and `S` (bandit/security). `E501` is deferred to the formatter; `S101` (assert) is allowed in tests.
- **mypy** — `strict = true`. All new code must type-check: `disallow_untyped_defs`, `no_implicit_optional`, `check_untyped_defs`. Tests relax `disallow_untyped_defs`.
- **Prompts** live in `prompts/*.md`, version-controlled — never inline agent prompts in code.
- **Structured outputs** — agents return Pydantic-validated models, not free-form dicts.

`make lint && make typecheck` must be clean before review.

---

## 4. Testing

pytest config is in `pyproject.toml` (`asyncio_mode = "auto"`, `--strict-markers`, `--strict-config`). Test paths: `services/*/tests`.

**Markers** (declared in `pyproject.toml` — use them, `--strict-markers` will reject typos):

| Marker | Meaning | Runs in default `make test`? |
|---|---|---|
| `integration` | Requires running services (Postgres, Redis, MCP servers) | No — use `make test-integration` after `make dev-up` |
| `slow` | Long-running | Opt-in |
| `live_llm` | Requires a real LLM API key; **not run in CI by default** | No |

```bash
make test               # fast unit tests, LLM mocked
make test-integration   # real Postgres/Redis
uv run pytest -m slow    # opt into slow tests
```

Coverage target: **70% line coverage** on `orchestrator` and the MCP servers (Web UI is manually tested). New behavior needs a test; bug fixes should add a regression test. The eval harness (`eval/`) has its own deterministic self-tests (`make eval-test`) plus a live golden run (`make eval`).

---

## 5. State-schema versioning discipline (orchestrator)

**If your PR touches the LangGraph `InvestigationState` shape, this section is mandatory.** LangGraph serializes state into Postgres at every node transition, so a careless field change breaks in-flight investigations, replay of past incidents, and rolling deploys. The full rationale and reference implementation are in [ARCHITECTURE.md §3.2.1](docs/reference/architecture.md#321-state-schema--versioning). The rules:

1. **State is a Pydantic `BaseModel`** (not `TypedDict`) with an embedded `schema_version: int`.
2. **Additive-only between minor bumps.** New fields **must** have a default. **Never rename, never remove, never change a field's type.** ~95% of changes are additive and need zero migration work.
3. **Major (breaking) bumps** require a registered `migrate_vN_to_vN+1` function in the `MIGRATIONS` map, chained by the checkpoint loader.
4. **Update the fixture-replay test.** Add a checkpoint blob for the new version under `tests/fixtures/checkpoints/`; CI asserts every historical fixture still loads under current code. **A state-shape change will not be merged without this.**
5. A major version bump must ship **migration + new fixture + integration test in the same PR**.

If you find yourself needing major bumps often, the schema design needs rethinking — not more migrations.

---

## 6. Pull requests

- **Branch** off `main`; keep PRs focused and reasonably small.
- **Green CI** — lint, typecheck, unit tests, and the eval subset must pass.
- **Describe the change**: what, why, and how you verified it. Link the issue.
- **Docs** — update relevant docs (`docs/`, `README.md`) in the same PR when behavior or config changes. **Never invent config keys** — match the settings shapes in `services/*/src/*/config.py`.
- **Tests** — include them. New config key? Document it. New state field? Update the fixture set (§5).
- A maintainer reviews; address feedback by pushing follow-up commits.

### Commit conventions

Use short, imperative subject lines, ideally [Conventional Commits](https://www.conventionalcommits.org/) style:

```
feat(orchestrator): add readiness-probe RCA scenario
fix(mcp-loki): handle empty LogQL result set
docs(install): clarify air-gapped Helm profile
test(state): add v2 checkpoint fixture
```

Scopes track the components: `orchestrator`, `api-gateway`, `mcp-k8s`, `mcp-prom`, `mcp-loki`, `web-ui`, `charts`, `docs`, `eval`.

### DCO / sign-off

Sign-off is **optional but appreciated**. Add it with `git commit -s` (appends a `Signed-off-by` line asserting you have the right to contribute the change under Apache 2.0).

---

## 7. Phase discipline

**Phases 1–3 are implemented and read-only.** The single hardest rule is the **read/write bright line**: KubePilot writes nothing to the cluster until Phase 4. RBAC grants only `get/list/watch`, `mcp-k8s` exposes only read tools (`mcp-k8s/tests/test_rbac.py` renders the chart and asserts no write verbs), and a guardrail blocks any destructive recommendation. The following are **out of scope until Phase 4** and PRs adding them will be deferred to issues:

- **Any** cluster writes / remediation execution / auto-rollback / self-healing (Phase 4 — read-only is architectural, not optional).
- The `k8s-write-mcp` server, HITL approval flow, execution policy engine, blast-radius estimation (Phase 4).

Still deferred (open an issue to discuss): full **OIDC/Keycloak** auth (a documented opt-in seam today), multi-cluster federation, and a SaaS control plane.

Have a great idea for Phase 4+? **Open an issue** so it's captured — don't smuggle a write path into a read-only PR. See the [roadmap](docs/reference/roadmap.md) for where things land.

---

## 8. Reporting bugs & requesting features

Open a GitHub issue. For bugs include: what you did, what you expected, what happened, `/ready` output, relevant pod logs, and your (redacted) `llm.roles` config. For features, note which phase you think it belongs to.

Thanks for contributing.
</content>
