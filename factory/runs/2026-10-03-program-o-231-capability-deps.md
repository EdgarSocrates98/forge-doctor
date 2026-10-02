# Run: Program O wave 2 — Capability dependency semantics (spec 231)

- **Commit**: `3786da0`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/231-capability-dependency-semantics.md`

## Scope

Additive schema-v2 dependency/lifecycle fields on capability packs,
transitive dependency evaluation with blocked-state propagation, and
dependency edges in the capability subgraph.

## Files changed

- `src/forge_doctor/core/capabilities.py` — `_Entry` gained
  `requires`, `requires_any`, `alternatives`, `incompatible_with`,
  `specializes`, `introduced_in`, `deprecated_in`, `removed_in`,
  `replacement`; public `dependencies()` accessor.
- `src/forge_doctor/core/capability_deps.py` — **new**: DFS evaluator
  producing `DependencyEvaluation` (status, lifecycle, readiness,
  blocked_path, alternatives, incompatibles, cycles, missing),
  `CapabilityReadiness` (READY/PARTIAL/BLOCKED/UNKNOWN), cycle
  detection, `requires_any` group satisfaction.
- `src/forge_doctor/core/capability_graph.py` — `DEPENDS_ON` edges for
  `requires`/`alternatives`/`specializes`/`replacement`.
- `src/forge_doctor/cli/capabilities.py` — `capabilities explain`
  prints lifecycle, readiness, blocked path, alternatives.
- Knowledge packs — dependency declarations added to `dynamodb`
  (LSI→GSI etc.), `trino`, `search`.
- `tests/unit/test_capability_deps.py` — **new**; capability-graph
  edge test updated for non-`EVIDENCED_BY` edges.

## Found & fixed en route

- Blocked-state propagation bug: a transitively blocked dependency
  did not block its parent — `_resolve` now propagates
  `subtree_blocked` up the chain.
- Blocked-path reconstruction duplicated the leaf and dropped
  intermediate ancestors — simplified to root + ancestors + leaf.

## Validation

- `pytest tests/unit/ -k capabilit` — 65 passed.
- ruff + mypy clean (typing tightened in wave 5 commit `ebf5945`).
