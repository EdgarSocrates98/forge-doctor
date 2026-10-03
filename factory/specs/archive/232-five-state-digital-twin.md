---
id: 232
title: Five-State Digital Twin — reconciliation, drift, temporal diff
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k twin -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program N — Five-State Digital Twin (prompt_evo_step7 §Phase 3)

Depends on 227 (formal digital twin). The twin today is a trusted
snapshot; this evolves it to five formal states with reconciliation.

## Context

`core/twin.py` holds the current snapshot/invariants; `core/history.py`
records snapshots; `core/whatif.py` produces hypothetical reports.
Phase 3 formalizes state-tagged facts and state-vs-state drift.

## Acceptance Criteria

- `TwinState` enum — DESIRED, DECLARED, IMPLEMENTED, OBSERVED,
  HYPOTHETICAL. Sources per state: DESIRED = platform-contract/org
  policy/data contracts; DECLARED = Terraform/CloudFormation/dbt bundle
  /Snowflake config/BigQuery IaC; IMPLEMENTED = source code/SQL/dbt/
  orchestrators; OBSERVED = runtime evidence/metadata exports/query
  history/system tables; HYPOTHETICAL = what-if/migration plan/
  experiment transforms.
- `TwinFact` — entity, property, value, state, evidence_kind, source,
  confidence.
- `TwinReconciliation` — expected, actual, states_compared, difference,
  confidence, affected_entities.
- `DriftType` — CONFIG_DRIFT, IMPLEMENTATION_DRIFT, RUNTIME_DRIFT,
  CAPABILITY_DRIFT, OWNERSHIP_DRIFT, SCHEMA_DRIFT, PERFORMANCE_DRIFT,
  SECURITY_DRIFT. Example shapes: desired Glue 6 vs code assuming
  Glue 5 → IMPLEMENTATION_DRIFT; contract customer_id string vs
  implemented bigint → SCHEMA_DRIFT.
- Temporal twin: `TwinSnapshot` (timestamp, state summaries, graph,
  capabilities, findings, runtime, drift) integrated with history.py.
- CLI: `forge-doctor-data twin diff <snapshot-a> <snapshot-b>` — entity
  added/removed, relationship changed, capability changed, drift
  introduced/resolved; `forge-doctor-data twin explain <entity>` — shows the
  entity across all five states.
- What-if produces a HypotheticalState without mutating the current
  twin.

## Constraints

- No state is inferred without evidence; missing states are absent, not
  fabricated. OBSERVED only from real artifacts (RUNTIME /
  OBSERVED_METADATA evidence kinds).
- Public twin API stays additive; snapshot schema versioned.
- Deterministic ordering in reconciliation and diff output.

## Test requirements (§3.11)

state conflict, state agreement, missing observed evidence,
hypothetical state, temporal diff, ownership drift, schema drift,
determinism.
