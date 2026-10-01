---
id: 169-evidence-classification
title: Connected Data phase A1 - global evidence-kind classification on findings
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest tests/unit/test_evidence_kind.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase A (Semantic Accuracy
Hardening), deliverable 1. Before adding Neptune/graph/DynamoDB domains,
findings need a typed evidence classification so downstream consumers
(graphs, capability engine, reports) know how each fact was obtained.
`CheckResult.evidence` is a free-text snippet and `Confidence` measures
detector certainty — neither says *where the fact came from*.

# Acceptance Criteria
- `core/models.py`: `EvidenceKind` enum — `STATIC` (parsed source code /
  AST), `CONFIG` (declarative config/manifest/IaC files),
  `OBSERVED_METADATA` (real metadata artifacts: parquet footers, iceberg
  metadata.json, git index state), `RUNTIME` (runtime artifacts such as
  progress logs — reserved, no producer yet), `DERIVED` (inferred by
  combining multiple facts). `parse()` classmethod matching `Severity`.
- `CheckResult.evidence_kind: EvidenceKind | None` field; excluded from
  fingerprint material (identity stays stable).
- `CheckBase.evidence_kind` class attr (default `STATIC`) + `result()`
  kwarg override; wire to `CheckResult`.
- Tag every built-in category base: STATIC — spark, sql, streaming,
  airflow; CONFIG — terraform, iac, stepfunctions, controlm,
  dependencies, ci, docker, python_env, repository, aws; OBSERVED_METADATA
  — iceberg, parquet, git_checks; DERIVED — only individual checks that
  combine multi-signal inference (e.g. streaming shared-checkpoint).
- `output/json_renderer.py` emits `evidence_kind` when set; `explain`
  shows it alongside confidence.
- Tests: enum parse, default/override passthrough, per-category tags,
  JSON payload, fingerprint unchanged vs a result without the field.
- docs/checks.md evidence-tier section + CHANGELOG entry.

# Constraints
- No behavior change to severities, fingerprints, or SARIF schema beyond
  optionally surfacing the kind in SARIF properties.
- Untagged third-party plugin results keep `evidence_kind=None` — absence
  must not break renderers.
- Tagging is per category base class where one exists; per check
  otherwise. When a check's evidence is genuinely mixed, pick the
  dominant source and note it in the run record.
