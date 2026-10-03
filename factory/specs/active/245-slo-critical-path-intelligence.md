---
id: 245
title: End-to-End SLO & Critical Path Intelligence
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "slo or critical_path or budget" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program Q — Phase 5: SLO & Critical Path Intelligence (prompt_evo_step9 §PHASE 5, §63, §94)

Whole-path analysis over DataPlatformGraph — extends the phase-238
local reliability/SLA surface.

## Acceptance Criteria

- `CriticalPath` (source, destination, entities, executions,
  latency_segments, total_latency, freshness, reliability, bottleneck,
  unknown_segments) discovered via graph edges only where semantics
  permit (PRODUCES/CONSUMES/READS/WRITES/TRIGGERS/INVOKES/DEPENDS_ON) —
  no arbitrary paths.
- End-to-end latency from comparable timestamps only (event time →
  ingestion → transformation → serving); incompatible metrics never
  summed.
- `SLOBudget` (objective, total_budget, consumed, remaining,
  violating_segments, evidence); freshness decomposition per segment.
- Bottleneck = largest observed contributor; missing segments reported
  as unknown, never inferred.
- Availability-path and RPO/RTO path checks when topology evidence
  exists.
- Findings SLO001–SLO006 (e2e freshness violation, latency budget
  exhausted, unknown critical segment, RPO mismatch, RTO mismatch,
  critical dependency without failover evidence).
- CLI: `reliability path`, `reliability slo`.
- Lab: `labs/slo/` Kafka→Spark→Iceberg→Serving freshness budget.

## Constraints

- Show which segment consumed the budget; "5 known / 2 unknown"
  coverage always listed.
