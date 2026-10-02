---
id: 183-runtime-evidence
title: Runtime Evidence — offline adapters for exported artifacts
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_runtime_evidence.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_next_step.md` phase 2. `EvidenceKind.RUNTIME` becomes a real
layer: user-exported artifacts parsed offline into a normalized model.
No AWS calls, no target-code execution; adapters treat input as
untrusted.

# Acceptance Criteria
- `core/runtime_evidence.py`: `RuntimeEvidenceModel` (executions, events,
  metrics, errors, timings, throughput, lag, retries, resource_usage,
  state, identifiers), `ExecutionMetric`/`ExecutionError`/
  `ExecutionTiming`/`ExecutionThroughput`, `RuntimeEvidenceAdapter`
  protocol, `identity_keys()` for demonstrable joins only.
- `analyzers/runtime_evidence.py`: adapters for Spark event log,
  Structured Streaming progress, Glue logs, Athena query stats, Lambda
  REPORT, Step Functions history, Neptune explain/profile; ordered
  first-match dispatch + forced `--adapter`.
- CLI: `runtime inspect|diagnose <artifact>`, `streaming progress <file>`.
- Tests: per-adapter positive + malformed + unknown-input + determinism.
