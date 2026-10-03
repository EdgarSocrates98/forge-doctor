# Run: Program C — Experiment / Validation Engine (spec 209)

- **Initial HEAD**: `f1ad77d`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/209-experiment-engine.md`

## Scope

Forge Lab validates detection; experiments validate *remediation
hypotheses*: copy a scenario fixture, apply a named deterministic
transform, rescan hermetically, compare before/after.

## Files changed

- `src/forge_doctor_data/core/experiments.py` — **new** (~270 lines):
  `Hypothesis` (named transform over a fixture tree),
  `ExperimentResult` (resolved/introduced by fingerprint, severity
  deltas, file counts, verdict + reasons), `run_experiment`
  (copy → transform → hermetic rescan → diff; temp copy always
  removed), `HYPOTHESES` registry.
- `src/forge_doctor_data/cli/lab.py` — `lab experiment <scenario>
  --hypothesis <name> [--json]`; accepts a scenario name under
  `--labs` or a direct fixture directory.
- `tests/unit/test_experiments.py` — **new**, 12 tests: improved
  (STREAM002 resolved by add-checkpoint), neutral (no-op and already-
  satisfied), regressed (temp hypothesis removing a checkpoint),
  unknown-hypothesis error, fixture immutability, determinism, JSON
  shape, CLI paths.
- `CHANGELOG.md` — Added entry.

## Hypotheses shipped

| name | transform |
|---|---|
| `bump-glue-version` | `glue_version = "4.0"` → `"5.0"` in `*.tf` |
| `partition-data` | `.repartition(1)`→8 / `.coalesce(1)`→8 in `*.py` |
| `add-checkpoint` | `.option("checkpointLocation", ...)` after `.writeStream.format(...)` |
| `increase-trigger-interval` | `processingTime` → `'30 seconds'` in `*.py` |

## Design decisions

- Verdict rules: any new warning/error → `regressed`; else resolved
  warning/errors → `improved`; else `neutral`. Info/pass fingerprint
  shifts are reported as reasons but never decide the verdict.
- Hypothesis transforms are pure text edits — no runtime measurement,
  honest about "cost proxy" limits per spec.
- Bug caught during testing: inserting the checkpoint option between
  `.writeStream` and `.format()` broke Delta sink detection; the
  transform now anchors after `format(...)`.

## Tests / gates

- `pytest tests/unit/test_experiments.py`: **12 passed**
- `pytest tests/unit/ -k experiment -x -q`: 12 passed (spec verification)
- `ruff`/`mypy` on touched files: clean
- Manual dogfood: `lab experiment repartition1-write --hypothesis
  partition-data` → IMPROVED (SPARK003 resolved)

## Known limitations

- Verdict granularity is finding-fingerprint level; capability/graph
  deltas aren't diffed (ground-truth categories could join later).
- Hypotheses are fixed named transforms, not parameterized
  (`bump-glue-version` is hard-coded 4.0→5.0).
- `expected.json` `hypothesis:` suite key deferred per open question.

## Open questions

None added. Spec remains in `active/` pending human review.
