---
id: 225
title: Platform Ontology (canonical vocabulary)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k ontology -x -q
---

# Consolidation 1/5 - Platform Ontology

Roadmap order: executes after spec 211 (ecosystem contracts) and before
roadmap-4 wave 1 (spec 212). Numbered 225 because 212–224 are taken;
sequence position is what matters.

## Context

Entity kinds, relationship kinds, evidence planes, and capability
families are defined across `platform_graph.py` (`EntityKind`,
`RelKind`), `incremental.py` (evidence domains), and `capabilities.py`
(capability registry) — the vocabulary is real but scattered, and
adapters can drift from it silently. The ontology is the single
canonical vocabulary every adapter, contract, and doc derives from.

## Acceptance Criteria

- `core/ontology.py` — the canonical vocabulary module: entity kinds,
  relationship kinds, evidence planes (`static`, `config`,
  `observed_metadata`, `runtime`, `derived`), evidence domains, and
  capability families, each with a one-line definition. `EntityKind` /
  `RelKind` continue to be the runtime enums; the ontology module is
  the documented registry they conform to (no duplication of meaning —
  ontology validates/annotates, enums execute).
- `forge-doctor-data ontology` — prints the vocabulary deterministically
  (kinds, rels, planes, domains, families with definitions);
  `--format json` emits the same as a stable, documented shape.
- Ontology conformance: a check (run inside `scan` or via
  `ontology validate`) that every entity kind/relationship kind
  produced by registered adapters belongs to the vocabulary — unknown
  kinds surface as diagnostics, never silently accepted.
- `docs/ontology.md` — generated or verified from the module (a test
  asserts the doc table matches the vocabulary; no hand-drifted doc).
- Contracts hook: `platform-graph` schema notes entity/rel kinds are
  ontology-bound; ontology terms are stable public identifiers.

## Constraints

- Vocabulary changes are additive by default; renaming a kind is a
  breaking change requiring schema/contract version bumps.
- Deterministic ordering everywhere; offline; no new runtime planes.
- The ontology documents what exists — it does not invent entity kinds
  for platforms not yet implemented (warehouse kinds arrive with 212).

## Open Questions

- Whether `ontology validate` should be a scan-time check (new check
  id) or a standalone diagnostic command — decide at implementation
  based on how violations should surface (finding vs report).
- Whether external plugins may extend the vocabulary — likely yes via
  namespaced kinds; left open until the plugin SDK contract for it is
  designed.
