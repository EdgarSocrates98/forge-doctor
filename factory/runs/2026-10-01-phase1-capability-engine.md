# Program run: prompt_evo_capability1 — Phase 1, Capability Engine

## Shipped

- `core/capabilities.py` — `CapabilityStatus` (SUPPORTED/UNSUPPORTED/
  CONDITIONAL/UNKNOWN), `CapabilityContext`, `CapabilityResult`,
  `CapabilityRegistry` (`evaluate`/`supports`/`limitations`/
  `capabilities_for`/`explain`). Facts load from
  `knowledge/capabilities/*.json`; entries without a valid `source` are
  rejected and recorded in `registry.validation_issues`. Honesty: unknown
  platform/capability/version → UNKNOWN, never UNSUPPORTED.
- `ctx.capabilities` — the single documented path for checks.
- `knowledge/capabilities/` — glue, iceberg, dynamodb, neptune, graph
  packs (schema 2, sources, verified_at). Seeds: ICEBERG_MERGE_WRITE/
  UPDATE/DELETE, GRAPH_PROPERTY_MODEL/RDF_MODEL/BULK_LOAD,
  DYNAMODB_TRANSACTIONS/STREAMS/GLOBAL_TABLE_MREC/MRSC/STRONG_READ/GSI/LSI,
  NEPTUNE_GREMLIN/OPENCYPHER/SPARQL/BULK_LOADER/EXPLAIN/PROFILE/
  GLOBAL_DATABASE + analytics awareness.
- `cli/capabilities.py` — `capabilities list|explain` (+ `--json`,
  `--version`, `--variant`, `--attr`).
- Migrated check: ICE001 (iceberg format-version floor) now evaluates
  `ICEBERG_*` capabilities instead of a hardcoded `!= "2"`.
- `tests/unit/test_capabilities.py` — 29 tests: all statuses, version
  maps, unknown honesty, `when`/variant gating, conditions, pack
  validation (missing source, schema, duplicate, bad status/versions),
  determinism, bundled-pack `verify_pack`, ICE001 behavior preserved.

## Verification

- `pytest tests/unit/test_capabilities.py` — green (60 with iceberg tests)
- `pytest -x -q` — 748 passed
- `mypy src` — 95 files clean
- `ruff check src tests` — clean
- `ruff format --check src tests` — clean
- CLI smoke: `capabilities explain dynamodb_global_table
  DYNAMODB_TRANSACTIONS --variant MRSC` → unsupported + provenance.

## Notes

- `spark.json` from the prompt's suggested layout was intentionally
  skipped: no spark capability seeds were required and unsourced facts
  are rejected by design.
- Pack `schema.json` skipped — `list_packs()` scans every JSON in the
  domain and a schema file would fail `verify_pack`; schema version
  lives in each pack per existing convention.
