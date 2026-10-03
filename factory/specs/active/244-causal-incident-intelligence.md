---
id: 244
title: Causal / Dependency-Aware Incident Intelligence
agent: claude
risk: high
verification:
  - python -m pytest tests/unit/ -k "incident or causal or propagat" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program Q — Phase 4: Incident Intelligence (prompt_evo_step9 §PHASE 4, §62, §69–71, §76–77, §83–84, §93)

Explainable chains of failure/degradation — not generic incident
management. Extends `core/diagnosis.py` with temporal evidence, runtime
trends and change correlation; never duplicates root-cause engine.

## Acceptance Criteria

- `IncidentEpisode` (id, start, end, symptoms, affected_entities,
  regressions, correlated_changes, candidate_causes,
  downstream_effects, unknowns).
- `CandidateCause` (entity, change, evidence_path, temporal_match,
  runtime_match, graph_match, confidence, limitations) — cause
  categories CONFIGURATION/DATA_SHAPE/CAPACITY/DEPENDENCY/SCHEMA/
  ORCHESTRATION/NETWORK/STORAGE_LAYOUT/QUERY_PLAN/RUNTIME_VERSION/
  UNKNOWN.
- `SymptomPropagation` — upstream issue → intermediate effect →
  downstream symptom, over DataPlatformGraph edge semantics
  (cross-engine chains like Spark small-files → Iceberg manifest count →
  Trino splits → query latency).
- Evidence-path output: "4/5 expected links confirmed" + explicit
  unknowns; every candidate cause answers "why is this related?".
- Twin states (declared/implemented/observed deltas) participate;
  capability gaps surface as structural causes; resolved incidents can
  record recovery events; existing Severity reused.
- Ownership routing shown in output (no notifications).
- CLI: `incident inspect`, `incident explain`.
- Lab: `labs/incidents/` cross-engine propagation scenario.

## Constraints

- "confirmed cause" reserved for deterministic full-chain evidence;
  otherwise "strong correlation".
