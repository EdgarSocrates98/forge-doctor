---
id: 226
title: Capability Graph (provenance-wired capabilities)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "capability_graph or capgraph" -x -q
---

# Consolidation 2/5 - Capability Graph

Roadmap order: after spec 225, before roadmap-4 wave 1. Numbered 226
because 212–224 are taken.

## Context

`CapabilityRegistry.evaluate` reports `supported|unsupported|
conditional|unknown` per platform and version, but the answer is flat —
a downstream consumer cannot see *which evidence* produced a status.
The capability graph wires each evaluated capability to the entities,
models, and evidence planes that informed it, making capability
reporting explainable end to end.

## Acceptance Criteria

- Capability evaluation gains provenance: each capability row records
  the deterministic evidence behind its status — contributing entity
  ids (graph ids), producing models/domains, and matched rule ids —
  populated from the same evidence the evaluator already reads (no
  fabricated provenance; `unknown` rows state the missing evidence).
- `forge-doctor capabilities graph <path>` — renders the capability →
  evidence subgraph: capability nodes linked to platform/entity nodes
  with edge kinds drawn from the ontology vocabulary; deterministic
  ordering; `--format json` emits the subgraph (stable shape added to
  `schema contracts` if it becomes a public artifact).
- `capabilities list --json` rows gain a `provenance` field (additive,
  non-breaking).
- Handoff bundles automatically carry provenance via the existing
  `capabilities` section — no bundle shape change.
- Tests: every evaluated capability reports honest provenance;
  `unknown` capabilities name the evidence that would decide them;
  ordering is deterministic across runs.

## Constraints

- Provenance is derived, never asserted — a capability may not claim
  support from evidence the evaluator did not actually read.
- No vendor-specific capability logic here; adapters own their packs.
- Offline; static cost proxies only; no runtime probing.

## Open Questions

- Edge vocabulary for capability→entity links (`DERIVES`,
  `EVIDENCED_BY`) — pick from/extend `RelKind` during implementation;
  new rel kinds are additive vocabulary changes per spec 225.
- Whether conditional statuses should enumerate their conditions in
  provenance — likely yes; confirm against real pack data.
