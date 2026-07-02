# RCA quality: critique, runtime libraries, calibration, prompt A/B

Phase 3 is where **quality and evaluation dominate**. Four features make the
root-cause analysis more trustworthy and more measurable. All are read-only.

## Multi-agent critique

After the RCA agent produces a report, a **critic agent** reviews it
adversarially — it is prompted to *refute*: find missing signals, alternative
causes it didn't rule out, symptoms mistaken for causes, and unsupported leaps. It
emits a `Critique { agreement, concerns, adjusted_confidence, escalate_to_human }`.

- The critic's `adjusted_confidence` seeds `state.calibrated_confidence` (an
  interim until the empirical calibrator, below, is fitted).
- Deterministic policy forces `escalate_to_human` when agreement < 0.5 or the
  adjusted confidence < 0.4 — a low-agreement finding is routed to a human instead
  of asserted.
- The critic's concerns are fed into the recommendation agent so remediation
  weighs the open questions.

Enabled via `critic_enabled` (on by default in Phase 3). The graph inserts the
critic node between RCA and recommendation: `rca → critic → recommendation`.

The **debate-uplift eval** (`eval/harness/debate_eval.py`) measures the critic's
value on a held-out set: on ambiguous over-confident cases the critic tempers
confidence toward the ideal and escalates; on clear cases it must not regress.

## Runtime-specific RCA libraries

Per-runtime failure knowledge (JVM, Node.js, Python, Go) lives as **markdown**
under `rca/runtimes/` — not as branching code. The Logs agent tags evidence with
`detail.runtime`; the RCA agent normalizes that tag (jvm/kotlin→java, golang→go,
nodejs→node, cpython→python) and injects the matching library into its prompt.

Adding a runtime is drop-in a `{key}.md` file. A Java OOM gets JVM heap/metaspace/
GC guidance; a Go incident gets goroutine-leak/panic guidance — with no
cross-contamination. Unrecognized/`generic` runtimes get no injection (the RCA
stays runtime-agnostic).

## Confidence calibration

A stated "0.85" should be right ~85% of the time. The calibrator
(`calibration/calibrator.py`) learns a monotonic map raw-confidence →
empirical-accuracy from eval history via **isotonic regression** (Pool Adjacent
Violators — no sklearn, runs air-gapped), and reports the **Expected Calibration
Error (ECE)** + a reliability curve for the AgentOps plot.

At finalize, a fitted calibrator (shipped via `calibrator_path`) maps the raw RCA
confidence to `calibrated_confidence`, overriding the critic's interim value. Gate:
ECE < 10% on the eval set.

## Prompt versioning + A/B + rollback

Every prompt is a version-controlled file. The registry
(`agents/prompt_registry.py`) resolves `{name}.md` as `v1` and `{name}.vN.md` as
explicit versions; the reasoning agents record which version produced each
investigation in `state.prompt_versions`.

- **A/B**: `eval/harness/prompt_ab.py` scores each pinned version over the golden
  set; a challenger is promoted only if it beats current beyond a noise margin
  **and** doesn't regress category accuracy. A worse prompt is rejected.
- **Rollback (<5 min)**: pin `prompt_active_versions` (e.g. `{"rca_agent": "v1"}`)
  in gateway config and restart. Config-only, no code change.

## Continuous eval, drift, and the release gate

- `eval/harness/drift.py` compares a run's metrics (mean score, category accuracy,
  ECE) to a committed baseline (`eval/baselines/golden.json`) and flags regressions.
- `eval/harness/eval_gate.py` + `.github/workflows/eval-gate.yml` **block a
  release** on an accuracy/score regression >5% or an ECE increase >5%.
- Deterministic self-tests keep PR CI green; the live gate runs on release tags
  when an API key is present.
