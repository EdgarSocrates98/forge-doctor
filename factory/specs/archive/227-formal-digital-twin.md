---
id: 227
title: Formal Digital Twin (validated platform snapshot)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k twin -x -q
---

# Consolidation 3/5 - Formal Digital Twin

Roadmap order: after spec 226, before roadmap-4 wave 1. Numbered 227
because 212–224 are taken.

## Context

The `DataPlatformGraph` + analyzer models already act as a digital twin
of the platform, but informally — nothing states the twin's invariants
or checks them. The formal twin is the graph plus a deterministic
invariant suite: what must hold for the snapshot to be trusted as a
basis for decision and optimization intelligence (228/229).

## Acceptance Criteria

- `core/twin.py` — twin assembly + invariant validation over a built
  `DataPlatformGraph` and `ScanReport`. Invariants (all deterministic,
  all reported — never auto-repaired):
  - no dangling relationship endpoints;
  - every entity id matches `kind:domain:identifier` with `kind` in
    the ontology vocabulary;
  - every relationship `kind` is ontology-bound and its
    `evidence_kind` is a declared evidence plane when set;
  - every finding with a `file` resolves to a scanned file, and
    graph-linked findings reference existing entities;
  - entity attr completeness report — entities missing `name`/
    `file`/`line` counted per domain (informational, not failure).
- `forge-doctor twin inspect <path>` — twin summary (entity/rel counts
  by kind/domain/plane) + invariant report; `--format json` stable
  shape. Exit non-zero only on hard invariant violations (dangling
  edges, malformed ids), not on informational gaps.
- `forge-doctor twin export <path>` — the twin snapshot artifact
  (entities + relationships + invariant report header) for downstream
  tools; deterministic serialization.
- Tests: each invariant detects a planted violation and passes on the
  committed lab/golden fixtures; twin output is byte-deterministic.

## Constraints

- Validation reports; it never mutates the graph.
- Invariants encode semantics we already guarantee — do not add rules
  the current adapters cannot satisfy (informational vs hard split
  exists precisely for this).
- Twin is per-project; workspace merge already handled by 196.

## Open Questions

- Whether the twin snapshot becomes a named contract artifact
  (`twin-snapshot` schema) — likely yes once a second consumer exists;
  defer until then per the same rule as spec 211's shared package.
- Retention: should `scan --record` snapshots store twin digests for
  history diffs — cheap to add, decide at implementation.
