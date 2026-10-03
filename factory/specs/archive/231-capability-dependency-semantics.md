---
id: 231
title: Capability Dependency Semantics — functional deps, lifecycle, readiness
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k capabilit -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program M — Capability Dependency Semantics (prompt_evo_step7 §Phase 2)

Depends on 226 (capability provenance graph) and 230 (ontology). The
capability engine today answers "does platform P support X"; it does not
model that X *requires* Y, is *replaced by* Z, or was *removed in* v5.

## Context

`core/capabilities.py` loads `knowledge/capabilities/*.json`
(schema_version 2) into `_Entry` facts with when/versions/conditions/
limitations and evaluates tri-state-plus answers with provenance.
`core/capability_graph.py` emits EVIDENCED_BY edges to packs. Dependency
semantics must live in the versioned packs, not hardcoded Python.

## Acceptance Criteria

- Pack schema extended (additive, schema_version 2 compatible):
  `requires`, `requires_any`, `alternatives`, `incompatible_with`,
  `introduced_in`, `deprecated_in`, `removed_in`, `replacement`
  (conditions/limitations already exist).
- `CapabilityRel` edge kinds: REQUIRES, REQUIRES_ANY, ALTERNATIVE_TO,
  INCOMPATIBLE_WITH, REPLACED_BY, SPECIALIZES.
- Dependency evaluation: transitive requires — if a required capability
  is UNSUPPORTED, dependents resolve UNSUPPORTED with an explainable
  path (X requires Y, Y requires Z, Z unsupported on platform/version).
  REQUIRES_ANY stays satisfiable while one alternative exists.
- Cycle detection — deterministic, reported, never infinite recursion.
- Lifecycle semantics: introduced_in/deprecated_in/removed_in/
  replacement → CLI-visible available|deprecated|removed|unknown.
- `forge-doctor-data capabilities explain <capability>` — Capability/Status/
  Because-chain/Replacement output shape.
- `CapabilityReadiness` — READY, PARTIAL, BLOCKED, UNKNOWN.
- Workload→capability mapping: WorkloadIntent → RequiredCapabilities
  (e.g. VECTOR_SEARCH requires vector index + similarity search +
  serving latency).
- Dependency semantics data-driven via packs; pack validation covers
  new fields; real dependency entries added where supported by docs.
- Capability graph emits the new edge kinds with provenance.

## Constraints

- Unknown ≠ unsupported: missing dependency facts degrade to
  UNKNOWN/PARTIAL, never silently unsupported.
- Backward compatible: existing packs without the new fields still load;
  existing CapabilityResult fields unchanged.
- Deterministic traversal ordering.

## Test requirements (§2.10)

transitive requires, requires_any, cycle detection, deprecated feature,
replacement chain, missing evidence, conditional capability,
deterministic traversal.
