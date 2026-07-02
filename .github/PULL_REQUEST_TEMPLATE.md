<!-- Thanks for contributing! See CONTRIBUTING.md. Keep PRs focused and small. -->

## What & why

<!-- What does this change and why? Link the issue: Closes #123 -->

## How verified

<!-- Commands run + results. e.g. `make check`, `make eval-test`, a live/minikube run. -->

## Checklist

- [ ] `make check` is green (lint + typecheck + unit tests)
- [ ] Tests added/updated (new behavior has a test; bug fixes have a regression test)
- [ ] Docs updated in the same PR when behavior/config changed (`docs/`, `README.md`)
- [ ] No new config keys invented — they match `services/*/src/*/config.py`
- [ ] **Read-only preserved** — no cluster-write path added (Phase 4 only)
- [ ] If `InvestigationState` shape changed: additive-only **or** a migration + a new
      `tests/fixtures/checkpoints/vN_sample.json` + the fixture-replay test (CONTRIBUTING §5)
