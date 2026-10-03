# Run: Program G — Capability Graph (spec 226)

- **Initial HEAD**: `99cad2c`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/226-capability-graph.md` (authored
  this session)

## Scope

Wire every evaluated capability to the evidence that produced its
status — deciding pack entry, matched when-clause, version-source
entities, and named missing evidence for `unknown`.

## Files changed

- `src/forge_doctor_data/core/capabilities.py` — `CapabilityResult` gains
  `entry_id`, `matched_when`, `missing_evidence`; `evaluate` populates
  them (unknowns name the unmet `when` attrs or uncovered known
  versions).
- `src/forge_doctor_data/core/capability_graph.py` — **new**:
  `capability_subgraph(graph)` builds capability + knowledge_pack
  entities linked `EVIDENCED_BY` (deciding pack) and `EVIDENCED_BY`
  version-source entities (attr `provides=version`).
- `src/forge_doctor_data/core/platform_graph.py` + `core/ontology.py` +
  `docs/ontology.md` — additive vocabulary: `EntityKind.CAPABILITY`,
  `EntityKind.KNOWLEDGE_PACK`, `RelKind.EVIDENCED_BY`, producer domain
  `knowledge`.
- `src/forge_doctor_data/cli/capabilities.py` — `capabilities graph <path>`
  (text + `--json` platform-graph shape), `list --json --provenance`,
  `explain` reports the new fields; JSON output moved off
  `console.print` to `typer.echo` (Rich line-wrap corrupted payloads).
- `tests/unit/test_capability_graph.py` — **new**, 14 tests.

## Design decisions

- **`list --json` default shape kept flat** (`cap → status`) — the
  capability-report contract stays untouched; provenance is opt-in via
  `--provenance`. This resolves the spec's "additive, non-breaking"
  requirement literally.
- **Edge vocabulary**: `EVIDENCED_BY` added to `RelKind` per spec 226
  open question (additive ontology change); `capability` +
  `knowledge_pack` entity kinds added so the subgraph reuses
  `DataPlatformGraph` and emits valid `platform-graph` JSON.
- **Version evidence edges**: capabilities link to the entities whose
  version attrs supplied the evaluation context (dominant version per
  `platform_versions`) — that's what actually decided gated statuses.

## Tests / gates

- `pytest -k "capability_graph or capgraph"`: **14 passed**; with
  ontology+capabilities: **56 passed**
- ruff/mypy on touched files: clean
- Dogfood on this repo: 78 nodes / 79 edges; glue capabilities cite
  `compute_job:glue:etl`/`orders-etl` as version evidence; EMR/Lambda
  unknowns name uncovered versions.

## Known limitations

- Provenance is pack-level — a capability cannot yet cite which repo
  file set a *context attribute* other than version (conditions are
  evaluated but only the entry is cited).
- `capabilities graph` scopes to platforms observed in the project's
  graph; it does not render the full registry (intentional — evidence
  wiring needs observed context).

## Open questions

- Whether conditional statuses should enumerate their unmet conditions
  as graph attrs — partially done (`missing_evidence.N`); full
  condition serialization deferred.
- Spec remains in `active/` pending human review.
