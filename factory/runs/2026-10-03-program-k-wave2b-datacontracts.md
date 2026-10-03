# Run: Program K wave 2b — Data contracts (spec 217)

- **Initial HEAD**: `a7c3d67` (dbt adapter)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/217-data-contracts.md`

## Scope

Contract lint + schema-evolution detection. `DataContractModel` parses
datacontract-cli and ODCS contract files into a minimal normalized
subset; `DCTR001`–`DCTR003` lint presence/SLA/type-drift; `DCTR004`
surfaces breaking schema changes inside `diff --semantic`.

## Files changed

- `src/forge_doctor_data/analyzers/datacontract_model.py` — **new**:
  `DataContractModel` (contracts with id/format/owner/objects+fields/
  sla/quality/servers/unsupported sections), `DetectedSchema`
  cross-domain schema map, `norm_family` type normalization.
- `src/forge_doctor_data/checks/datacontract.py` — **new**: DCTR000 census,
  DCTR001 missing schema, DCTR002 prod server without SLA, DCTR003
  field type drift (evidence kind = detected schema's plane).
- `src/forge_doctor_data/analyzers/platform_graph_builder.py` —
  `_contracts` adapter: `data_contract` entities, `GOVERNS` edges to
  tail-matched relations (or `table:datacontract:` placeholders), and
  `field.<name>` attrs merged onto the governed relation for diff
  granularity.
- `src/forge_doctor_data/core/semantic_diff.py` — `EntityChange.breaking`,
  `_type_relation` lattice (same/widened/narrowed/changed),
  `_field_breaks`, classify rules: breaking field changes and
  removed contracted relations are HIGH risk; `READS_FROM`/`WRITES_TO`
  added to impact sets so dbt lineage reaches blast radius.
- `src/forge_doctor_data/cli/diff.py` — "Breaking contract changes"
  (DCTR004) section in text output + `contract_changes` array in JSON.
- `src/forge_doctor_data/core/platform_graph.py`,
  `src/forge_doctor_data/core/ontology.py`, `docs/ontology.md` —
  `data_contract` entity kind + `datacontract` producer domain.
- `src/forge_doctor_data/checks/__init__.py`,
  `src/forge_doctor_data/core/incremental.py` — registrations
  (`SQL | TERRAFORM | CONFIG | FILES`).
- `docs/checks.md`, `CHANGELOG.md`.
- `labs/datacontract/drifted-prod/` (DCTR002+DCTR003),
  `labs/datacontract/no-schema/` (DCTR001), `labs/datacontract/
  not-a-contract/` (adversarial).
- `tests/unit/test_datacontract.py` — **new**, 19 tests.

## Design decisions

- **Marker-or-filename gate**: `datacontract.yml` / `*.datacontract.*` /
  `*.odcs.*` names OR a `dataContractSpecification`/`kind:
  DataContract` content key. Generic YAML never attributes
  (adversarial lab pins silence).
- **Minimal subset**: both dialects normalize to the same rows; unknown
  top-level keys (`terms`, `examples`, `links`, `definitions`, ...)
  land in `unsupported` — parsed-but-unchecked, per the constraint.
- **DCTR003 evidence plane follows the detected schema's source** —
  static (CREATE TABLE), config (TF BigQuery schema), or
  observed_metadata (Snowflake/BQ column exports). Tail-name matching
  links contract `schema.name` to detected relations (documented
  heuristic).
- **Field-level evolution without field entities**: declared field
  families land as `field.<name>` attrs on the governed relation;
  `diff_graphs` diffs them per-field, and `_type_relation` (a small
  widening lattice over numeric/text/param families) separates
  breaking (removed/narrowed/changed) from additive/widened edits.
- **DCTR004 is a diff-time surface**, not a ctx check — the spec places
  it "in `diff --semantic`"; it emits `check_id: DCTR004` rows under
  `contract_changes` in JSON and a labeled section in text.
- **Impact sets now include `READS_FROM`/`WRITES_TO`** — readers and
  writers of a changed relation are dependents for PR-review blast
  radius; this also fixes a latent gap where dbt lineage edges never
  participated in impact traversal. Existing diff tests unaffected.

## Found & fixed en route

- `norm_family` searched the whole type string — `STRUCT<a INT>`
  matched `int` before `struct`. Family now matches the base token
  (pre-`(`/`<`) only.
- `TfBlock` fields are `kind`/`labels`/`attrs` (not `type`/`name`) —
  resource type is `labels[0]` when `kind == "resource"`.
- Mypy: `doc.get("info")` doesn't narrow inside a ternary — assign to
  `info_raw` first. `**dict` splat into `_e()` hits positional params —
  construct `Entity` directly for attr merges.
- `zip()` needs `strict=` under B905.

## Tests / gates

- `pytest tests/unit/ -k datacontract`: **19 passed**
- `pytest tests/unit/test_semantic_diff.py`: 26 passed (impact-set
  change verified non-breaking)
- `lab run`: **23/23 PASS** (incl. all three contract labs)
- ruff/mypy on touched files: clean

## Known limitations

- Field matching is name-exact + family-normalized; nested fields
  (struct members) normalize to `struct` without member-level drift.
- The widening lattice is intentionally small (numeric chain, text
  bounds, date→timestamp); unrecognized type pairs classify as
  `changed` (breaking candidate) rather than guessing.
- Semantic-layer diff (`semantic_models` in dbt yml) remains the open
  question deferred from spec 216.
- Spec remains in `active/` pending human review.
