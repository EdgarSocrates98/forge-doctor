# Run: Program O wave 3 — Five-state digital twin (spec 232)

- **Commit**: `f91ff19`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/232-five-state-digital-twin.md`

## Scope

State-tagged facts across five planes — DESIRED (contract), DECLARED
(Terraform/config), IMPLEMENTED (graph entities), OBSERVED (runtime
evidence), HYPOTHETICAL (what-if reports) — plus reconciliation,
snapshot diffing, and history recording.

## Files changed

- `src/forge_doctor/core/twin_states.py` — **new**: `TwinState`,
  `TwinFact`, `DriftType` (`OWNERSHIP_DRIFT`, `IMPLEMENTATION_DRIFT`,
  `MISSING_DESIRED`, `UNDECLARED_IMPLEMENTATION`),
  `collect_twin_facts`, `reconcile`, `drift_summary`,
  `twin_state_snapshot`, `diff_twin_snapshots`,
  `record_twin_snapshot`, `hypothetical_facts` (WhatIfReport → facts).
- `src/forge_doctor/cli/twin.py` — `twin facts`, `twin reconcile`,
  `twin diff <a> <b>`, `twin explain`, `twin record <name>`.
- `tests/unit/test_twin_states.py` — **new**.

## Found & fixed en route

- No `Entity.evidence_kind` exists — evidence plane is derived from
  the fact's state, not the entity.
- DECLARED classification keys off `source=terraform` attrs and `.tf`
  provenance, not the evidence domain (first attempt misclassified).
- `dict`-typed snapshot rows needed typed row helpers
  (`_snap_rows`/`_snap_dicts`) for mypy strict (landed in wave 5).

## Validation

- `pytest tests/unit/ -k twin` — green.
- Reconcile smoke test: contract-vs-TF ownership → `OWNERSHIP_DRIFT`,
  version mismatch → `IMPLEMENTATION_DRIFT`, agreement → no drift.
