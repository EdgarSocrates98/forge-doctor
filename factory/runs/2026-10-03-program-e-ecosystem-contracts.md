# Run: Program E — Forge Ecosystem Contracts (spec 211)

- **Initial HEAD**: `752964d`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/211-forge-ecosystem-contracts.md`

## Scope

Publish the full interop contract set, emit a portable handoff bundle
for downstream Forge tools, and ship a deterministic offline validator
for those contracts. Schemas describe existing output; no new runtime
behavior was invented for contracts.

## Files changed

- `src/forge_doctor_data/core/schemas.py` — six new contract schemas:
  `evidence`, `finding`, `capability-report`, `platform-graph`,
  `remediation-plan`, `handoff-bundle` (draft 2020-12, `$id`-keyed).
- `src/forge_doctor_data/core/handoff.py` — **new**: `build_handoff(report,
  ctx)` assembling `{contract, contract_version, schema_version, tool,
  project, summary, results, graph, capabilities, plans}` with stable
  keys and deterministic ordering (results sorted by
  `(check_id, fingerprint)`, plans by id).
- `src/forge_doctor_data/core/contract_check.py` — **new**: dependency-free
  subset validator (`type`/`required`/`properties`/`items`/`enum`/
  `const`/`oneOf`/`additionalProperties` incl. schema-valued/
  `pattern`) + `verify_contract(instance, name)`.
- `src/forge_doctor_data/core/remediation.py` — `plan_to_dict()` shared
  serializer (extracted from `cli/remediate.py` so `remediate --json`
  and handoff bundles emit identical plan rows).
- `src/forge_doctor_data/cli/export.py` — **new**: `export <path> --format
  handoff [-o file]` (stdout default).
- `src/forge_doctor_data/cli/misc.py` — new `contracts` group:
  `contracts list` + `contracts verify <bundle> [--contract]` where
  `<bundle>` is a file or `-`/omitted for stdin.
- `src/forge_doctor_data/cli/__init__.py` — `contracts` panel membership +
  `export` already in "Setup & integrations".
- `docs/contracts.md` — **new**: interop spec (guarantees, versioning
  rules, bundle shape, verify usage, boundaries).
- `tests/unit/test_contracts.py` — **new**, 17 tests.
- `tests/unit/test_api.py` — registry assertion updated to the 10-name
  contract set.
- `CHANGELOG.md` — Added entry.

## Design decisions

- **No `jsonschema` dependency** — the validator implements only the
  subset the published schemas use; keeps the offline/stdlib posture
  and is itself deterministic (sorted violations).
- **`contracts verify` is the canonical gate** per the spec
  (stdin/file); `schema contracts` remains the schema-dump surface.
- **Bundle honesty** — empty sections serialize as empty
  collections/objects rather than being omitted; absence of evidence
  is represented, not hidden.
- **`schema_version` vs `contract_version`** — bundle carries both:
  report-level schema version plus the bundle's own contract version.

## Tests / gates

- `pytest tests/unit/ -k contract -x -q`: **41 passed**
- `pytest tests/unit/test_contracts.py tests/unit/test_api.py`:
  **27 passed**
- `ruff check`/`format --check`, `mypy` on touched files: clean
- Dogfood: `contracts list` → 10 contracts;
  `export . --format handoff -o bundle.json` → `contracts verify`
  passes on file **and** stdin; malformed bundle (missing keys) and
  bad enum value both rejected with exit 1 and path-addressed errors.

## Known limitations

- The subset validator is not a general JSON Schema engine — it covers
  the keywords the published contracts use; adding a schema feature
  outside that set requires extending `contract_check.py`.
- `capabilities` in the bundle reflect evaluated capability status at
  scan time; no historical deltas.

## Open questions

- Whether downstream tools want a `contracts verify --json`
  machine-readable error stream is left open — current output is
  human-readable rows + exit code.
- Spec remains in `active/` pending human review.
