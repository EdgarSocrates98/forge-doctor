# Run: Program H — Formal Digital Twin (spec 227)

- **Initial HEAD**: `2b93965`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/227-formal-digital-twin.md` (authored
  this session)

## Scope

The `DataPlatformGraph` + scan report + a deterministic invariant
suite — the trusted snapshot decision/optimization intelligence stand
on. Validation reports; it never mutates.

## Files changed

- `src/forge_doctor/core/twin.py` — **new**: `TwinViolation`,
  `AttrGap`, `TwinReport`, `validate_twin` (invariants I1–I5 +
  ontology producer-domain conformance folded in), `build_twin`,
  `twin_snapshot`.
- `src/forge_doctor/cli/twin.py` — **new**: `twin inspect <path>`
  (text/json, exit 1 on hard violations) + `twin export <path>`
  (deterministic snapshot; stdout or `-o`).
- `src/forge_doctor/cli/__init__.py` — module + panel registration
  (Estate & change).
- `tests/unit/test_twin.py` — **new**, 12 tests.
- `README.md`, `CHANGELOG.md` — entries.

## Design decisions

- **Hard vs informational split** (spec's mechanism): I1–I3 +
  ontology violations are hard (exit 1); I5 attr gaps are reported as
  informational counts — adapters legitimately omit file/line for
  entities inferred without a source site.
- **I4 resolves relative paths against root** and requires existence
  *under* the resolved root — a finding pointing outside the scanned
  tree or at a missing file is a violation.
- **`twin export` shape** = `graph.to_dict()` + `invariants_ok` +
  `summary` header, so snapshots satisfy the existing
  `platform-graph` contract as a superset (additionalProperties).

## Tests / gates

- `pytest -k twin`: **12 passed** — every invariant detects a planted
  violation (dangling endpoint via graph surgery, malformed id via
  stand-in entity, bogus evidence plane, out-of-root finding file)
  and passes on clean fixtures; snapshot byte-deterministic.
- ruff/mypy on touched files: clean.
- Dogfood `twin inspect` + `twin export` on a tmp fixture: invariants
  hold; snapshot validates against `platform-graph` contract.

## Known limitations

- `twin inspect` runs a full scan + graph build (no incremental path
  yet) — on large repos it's scan-priced.
- I3 can only fire on deserialized/foreign graphs — in-memory edges
  are enum-typed at construction (correct; the check guards contract
  boundaries, not the constructor).

## Open questions

- Whether `scan --record` history snapshots should store twin digests
  for drift comparison — cheap follow-up, deferred per spec.
- `twin-snapshot` as a named contract artifact — deferred until a
  second consumer exists (same rule as spec 211).
- Spec remains in `active/` pending human review.
