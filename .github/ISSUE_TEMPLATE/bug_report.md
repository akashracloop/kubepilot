---
name: Bug report
about: Something isn't working as expected
title: "[bug] "
labels: bug
---

**What happened**
<!-- What you did, what you expected, what actually happened. -->

**Reproduction**
<!-- Steps, or the investigation query + namespace/service. -->

**Environment**
- KubePilot version / image tag or commit:
- Install method: [ ] minikube  [ ] Helm (profile: …)  [ ] local dev
- LLM provider + model (from your `llm.roles`, **redact keys**):

**Diagnostics**
<!-- Attach where possible: -->
- `GET /ready` output:
- Relevant `kubepilot-ai-api-gateway` pod logs (redact secrets):
- Investigation `state` / `error` field if applicable:

**Note:** never paste API keys, tokens, or secret values.
