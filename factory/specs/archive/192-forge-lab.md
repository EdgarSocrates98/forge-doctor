---
id: 192
title: Forge Lab - Reproducible Scenario Suites with Ground Truth
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/test_lab.py tests/unit/adversarial/test_lab.py -x -q
  - python -m forge_doctor_data lab run
  - python -m forge_doctor_data lab run --json
  - python -m pytest -q
---

# Roadmap-2 Phase 1 - Forge Lab

## Context

The first roadmap built detection surface. Roadmap-2 turns Forge Doctor Data
into a measurable product: prove precision/recall, block regressions,
and keep honest evidence. Phase 1 builds the lab harness itself plus a
seed corpus — later phases add metrics, golden repos, and perf budgets.

## Acceptance Criteria

- `core/lab.py`:
  - `GroundTruth` + `load_ground_truth` parsing `expected.json`
    (missing → empty truth, invalid → reportable error, non-dict/
    non-list tolerated).
  - `discover_scenarios(root)` — dirs containing `expected.json`,
    sorted.
  - `run_scenario(dir)` → `ScenarioReport` comparing engine output to
    truth across four categories: findings (`ID` or `ID@file-frag`),
    graph edges (`kind|src->dst`), capabilities
    (`[platform:]CAP[@version][;attr=v...]=status`), root causes
    (cluster-id prefixes, fed by optional `runtime/` artifacts).
  - `forbidden_findings` produce `forbidden_hit` failures;
    unexpected-but-present findings surface as `extra` (informational).
  - `run_lab(root, scenario=None)` → `LabReport` with pass/fail counts.
- `cli/lab.py`: `lab list`, `lab run [scenario|--labs dir|dir-path]`,
  `lab report`; `--json` everywhere; exit 1 on failure.
- `labs/` seed corpus: at least one verified scenario per domain dir
  (airflow, cross-domain, dynamodb, glue, iceberg, lakeformation,
  neptune, spark, streaming, terraform) — ground truth authored from
  *verified* engine output, never wishful declarations.
- Tests + adversarial tests; docs + run record.

## Constraints

- Deterministic, offline, stdlib-only (JSON ground truth — no YAML dep).
- No invention of expected findings — all expectations verified live.
- No architecture changes to existing checks; lab consumes them.

## Review Notes

- Capability ground truth needs attribute context
  (`iceberg:ICEBERG_MERGE_WRITE;format_version=1=unsupported`) because
  pack entries gate on attributes, not just versions.
- The lab immediately caught a real FP: DELTA001 fired on an
  Iceberg-only project. Fix folded into this commit: generic SQL DML
  ops in `delta_model` now require a project-level delta signal
  (`USING DELTA`, `DeltaTable`, `format("delta")`, `io.delta`, …);
  self-evident ops (`USING DELTA`, `table_changes(`) stay ungated.
