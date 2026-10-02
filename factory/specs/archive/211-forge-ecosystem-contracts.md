---
id: 211
title: Forge Ecosystem Contracts
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k contracts -x -q
---

# Roadmap-3 Phase 10 - Forge Ecosystem Contracts

## Context

The Forge family (Doctor now, Spark Forge / API Forge later) needs
common contracts so tools can hand off: evidence, findings,
capabilities, graph, remediation plans, context economy. Doctor is the
deterministic evidence engine; its outputs must be portable.

## Acceptance Criteria

- `core/schemas.py` extended to the full interop set: `evidence`,
  `finding` (single-result), `capability-report`, `platform-graph`,
  `remediation-plan`, `handoff-bundle` — all in `schema contracts`.
- `forge-doctor export --format handoff` — a portable JSON bundle for
  downstream Forge tools: `{tool, schema_version, project, results,
  graph, capabilities, plans}` with stable keys; deterministic ordering.
- `contracts verify <bundle>` — validates a handoff bundle file against
  the published schemas (stdin/file); used by other tools' tests.
- `docs/contracts.md` — the interop spec: what each artifact guarantees
  (id stability, determinism, evidence kinds), versioning rules,
  example bundle.
- Tests: handoff bundle validates against its own schema; verify
  rejects malformed bundles; schema registry lists all contracts.

## Constraints

- Schemas describe what we already emit — no new runtime behavior
  invented for the contract's sake.
- Backward compatible: existing consumers unaffected.

## Open questions

- Whether contracts live in a shared `forge-contracts` package for
  cross-repo reuse — deferred until a second Forge tool exists to pin
  the requirement.
