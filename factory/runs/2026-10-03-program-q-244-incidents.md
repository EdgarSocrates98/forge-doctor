# Program Q — Wave 4: Causal / Dependency-Aware Incident Intelligence (spec 244)

## Scope

Explainable chains of failure/degradation over recorded history —
extends `core.diagnosis` (reuses `PromotionLevel`) with temporal
evidence, runtime trends and change correlation; does not duplicate
the root-cause engine (`cluster_findings` still owns static chains).

## What landed

- `core/incident.py`
  - `IncidentEpisode` — id, start/end, symptoms, affected_entities,
    regressions, correlated_changes, candidate_causes, propagations,
    downstream_effects, owners, unknowns, recoveries.
  - `CauseCategory` — 11 categories (configuration, data_shape,
    capacity, dependency, schema, orchestration, network,
    storage_layout, query_plan, runtime_version, unknown).
  - `CandidateCause` — entity, change, evidence_path, confirmed/expected
    links (`N/5`), temporal/runtime/graph flags, confidence,
    limitations. Every candidate answers *why related* + *what's missing*.
  - `SymptomPropagation` — directed BFS over `RelKind` edges
    (`a --KIND--> b` hops); `path_found=false` reported honestly.
  - `build_incidents` — co-occurring episodes (overlapping or within
    window) merge into one incident; timestamp-less episodes stay
    standalone. Unknowns are explicit (no timestamps / no graph /
    no correlated changes / not persistent).
  - `structural_causes` — twin-drift + capability-gap causes, always
    POSSIBLE with limitations (structural, not change).
  - `RecoveryEvent` for resolved incidents.
  - CONFIRMED requires all 5 evidence links AND a persistent breach —
    "correlated with" otherwise.
- `cli/incident.py` — `incident inspect` (grouped windows) and
  `incident explain <id>` (symptoms, propagation hops, causes with
  per-link path, owners routing, unknowns).
- `core/lab.py` — truth keys `expected_incidents` (count),
  `expected_candidate_causes` (`<category>=<level>`),
  `expected_propagations` (`<upstream>-><symptom>`); JOB-kind series
  now also built so lab subjects can map to real graph entities.
- Lab: `labs/incidents/glue-partition-etl` — TF partition change on a
  Glue job → job-duration regression; propagation over the DEFINES
  edge (`infrastructure_resource:aws:aws_glue_job.etl -> etl`),
  candidate `storage_layout=strongly_supported`.
- `docs/checks.md` — Incident Intelligence section.

## Language discipline

`confirmed` reserved for the full deterministic chain (5 links +
persistent breach). `strongly_supported` at 3–4 links. Structural
causes are always `possible`.

## Validation

- `pytest tests/unit/ -k "incident or causal or propagat" -x -q` —
  16 passed
- `tests/unit/test_incident.py` — 15 tests (grouping, merge/split,
  confirmed/weak chains, evidence-path text, propagation found/not
  found, structural causes, downstream effects, owners)
- lab run on `labs/incidents` — 1/1 PASS
- `ruff check src tests`, `ruff format --check`, `mypy src` — clean
  (236 files)
- Full suite: **2079 passed** in 211.10s
