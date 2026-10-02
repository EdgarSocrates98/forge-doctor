---
id: 238
title: Reliability & SLA Intelligence — delivery semantics, objectives, failure domains
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "reliability or sla or freshness or delivery" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program P — Phase 4: Reliability & SLA Intelligence (prompt_evo_step8 §Phase 4 + §22-25,60,68)

Models the platform's reliability behavior — architecture/runtime
reliability evidence, never incident management.

## Acceptance Criteria

- `core/reliability.py` — new module:
  - `ReliabilityModel`: retries, idempotency, checkpointing,
    deduplication, timeout, DLQ, backup, restore, replication,
    failover, health_checks, recovery — each evidence-tagged
    (declared/observed/absent/unknown).
  - `ServiceObjective`: metric (availability, latency, freshness,
    throughput, error_rate, RPO, RTO), target, window, scope, source,
    criticality.
  - `FreshnessPath`: source_event_time, ingestion_time,
    transformation_time, serving_time, total_lag.
  - End-to-end latency via graph path (source → ingestion →
    transformation → warehouse → serving); only compatible metrics
    summed; incomplete timestamps → PARTIAL.
  - RPO/RTO comparison required (contract/policy) vs implemented/
    observed.
  - `FailureDomain`: cloud, region, AZ, cluster, account/project/
    subscription, workspace; concentration only with topology
    evidence.
  - `DeliverySemantics`: AT_MOST_ONCE, AT_LEAST_ONCE,
    EFFECTIVELY_ONCE, EXACTLY_ONCE_CLAIMED, UNKNOWN — never assert
    exactly-once without full-path evidence.
  - Reliability path per critical workload: producer → transport →
    compute → storage → consumer; evaluate retry, idempotency,
    checkpoint, DLQ, recovery.
- REL findings REL001–REL010 (retry without idempotency, stateful
  stream without checkpoint, SLA freshness mismatch, observed latency
  exceeds objective, RPO mismatch, RTO path incomplete, missing DLQ on
  retrying event path, failover topology unresolved, duplicated
  delivery without dedup evidence, backup/restore evidence
  incomplete). Statuses include PARTIAL/risk wording per sample.
- `knowledge/reliability/` packs — delivery/retry/checkpoint
  semantics per engine.
- Tests: delivery-semantics composition (source+engine+checkpoint+
  sink), exactly-once never claimed without evidence, freshness
  partial sums, REL findings, determinism, serialization.

## Constraints

- Graph-path traversal only over evidenced edges — never fabricate.
