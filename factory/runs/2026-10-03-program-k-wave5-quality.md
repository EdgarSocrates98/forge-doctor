# Run: Program K wave 5 — Data quality evidence (spec 222)

- **Initial HEAD**: `5961cd1` (metadata catalogs)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/222-data-quality.md`

## Scope

Expectation suites as *evidence of intent*: Deequ, Great Expectations,
SodaCL, dbt tests; coverage map table→checks; gate/checkpoint wiring;
observed run exports. Suites parsed, never executed.

## Files changed

- `src/forge_doctor_data/analyzers/quality_model.py` — **new**:
  `DataQualityModel` (`QualitySuite`/`QualityExpectation`/
  `QualityGate`/`ObservedQualityRun`), GX suite + checkpoint +
  validation-export scanners, SodaCL `checks for` scanner, Deequ
  code-bound scanner, dbt reuse via `DbtProjectModel`, CI/operator
  invocation gates, suite wiring pass.
- `src/forge_doctor_data/checks/quality.py` — **new**: DQ000 census,
  DQ001 prod table w/o expectations (gated on practice existing,
  medium confidence), DQ002 unwired suite (capped 10 + summary),
  DQ003 stale suite, DQ004 dropped-column expectation (silent without
  detected `field.*` schema).
- `src/forge_doctor_data/cli/quality.py` — **new**: `quality inspect`
  prints suites (engine/table/check count/wired), coverage map,
  gates, observed runs.
- `src/forge_doctor_data/checks/__init__.py`,
  `src/forge_doctor_data/cli/__init__.py`,
  `src/forge_doctor_data/core/incremental.py` (`PYTHON | CONFIG | FILES`),
  `src/forge_doctor_data/core/ontology.py` + `docs/ontology.md` (`quality`
  producer domain) — registrations.
- `docs/checks.md`, `README.md`, `CHANGELOG.md`.
- `labs/quality/{gx-unwired,contract-drift,soda-covered,adversarial}/`.
- `tests/unit/test_quality.py` — **new**, 18 tests.

## Design decisions

- **DQ001 gated on practice** — per the spec's open-question default:
  a prod-signaled table with zero checks only flags when ≥1 suite
  exists (gap within a practice, not absence of one).
- **Wiring is per-engine** — GX wires via checkpoint `validations`
  refs, `uncommitted/validations/` observed runs, or
  `GreatExpectationsOperator`/`run_checkpoint` invocations; Soda/dbt
  engine-wide invocations (`soda scan`, `dbt test|build`) run every
  suite in scope; Deequ is always wired (code-bound).
- **SodaCL gate is key-shape, not filename** — `checks for <dataset>:`
  top-level keys; adversarial CI `checks:` maps never attribute.
- **GX table resolution** — `meta.data_asset_name` / `data_asset_name`
  / `dataset` / `table` keys first, then the suite-name head segment
  (`orders.warning` → `orders`).
- **DQ004 needs a detected schema** — `field.*` attrs exist only where
  spec-217 contracts merge them; unknown schemas stay silent rather
  than guess.
- **Prod signal reuse** — `prod`/`production` in identifier segments
  or `env`/`environment`/`fabric` attrs == PROD (same convention as
  META004).

## Found & fixed en route

- `_SODA_CHECKS_RE` expected a trailing colon — the YAML parser
  strips it, so dict keys are `checks for X`; regex relaxed and key
  normalized before matching.
- `CHECKS` must hold instances (`tuple[Check, ...]`), not classes.
- `Entity.attrs` is `tuple[tuple[str,str], ...]`, not a dict — wrap
  with `dict()` for lookups.

## Validation

- `pytest tests/unit/test_quality.py` — 18 passed.
- Labs: `gx-unwired` fires DQ001/002/003, `contract-drift` fires
  DQ004 only, `soda-covered` fires DQ001 only, `adversarial` silent.
- `forge-doctor-data quality inspect` renders suites/coverage/gates on both
  positive labs.
- ruff + mypy clean on touched files.

## Open items / boundaries

- Deequ table targets are unresolvable offline (code-bound DataFrames)
  — suites record `table=""` and count toward practice, not coverage.
- GX `run_checkpoint` operator gates wire all GX suites (a real
  checkpoint name may be narrower — errs toward wired).
- No live execution — results arrive only as exported artifacts.
