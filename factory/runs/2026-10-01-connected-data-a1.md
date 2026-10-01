# Run: connected-data phase A1 — evidence classification (spec 169)

Prompt: `prompt_evo_neptune_graph_dynamo.md` — 7-phase program
(A hardening → B platform graph → C capabilities → D graph → E dynamodb
→ F neptune → G connected). Specs 169–180 written to inbox; 169 staged
and implemented.

## What shipped

- `EvidenceKind` enum in `core/models.py`: `STATIC`, `CONFIG`,
  `OBSERVED_METADATA`, `RUNTIME` (reserved), `DERIVED` — plus `parse()`.
- `CheckResult.evidence_kind` field; deliberately excluded from the
  fingerprint material (verified by test).
- `CheckBase.evidence_kind` class attr, default `STATIC`, plus a
  `result(evidence_kind=...)` per-finding override.
- Category tagging:
  - STATIC (default): spark, sql, streaming, airflow, glue — untouched.
  - CONFIG: terraform, iac, stepfunctions, controlm via category bases;
    aws, ci, dependencies, docker, python_env, repository via per-class
    `category`-line augmentation (these files have no shared base).
  - OBSERVED_METADATA: iceberg, parquet (bases), git_checks (per-class).
  - DERIVED: STREAM013 SharedCheckpoint (correlates queries),
    ICE013 RuntimeCompatibility (metadata × runtime pin × pack).
- Surfacing: `output/json_renderer.py` emits `evidence_kind`;
  `explain` prints a `kind` row and includes it in `--json`.
- docs/checks.md gained a global "Evidence kinds" section; CHANGELOG
  bullet added.

## Decisions / noted quirks

- Default `STATIC` rather than `None`: every built-in except the config/
  metadata domains reads code, and untagged plugins must stay `None`
  (absence ≠ static claim).
- Files without a shared check base were tagged per class via the
  `category` line — if a base is introduced later the attr can move up.
- `runtime` has no producer yet; reserved per spec.

## Verification

- `pytest tests/unit/test_evidence_kind.py` — 7 passed
- `pytest -x -q` — 634 passed
- `mypy src` — 89 files clean
- `ruff check` + `ruff format --check` — clean

## Amendment (prompt_evo_evolucao2.md review)

Reviewer corrected the tagging to fact-level: Iceberg/Parquet bases are
STATIC (neither model reads real table metadata); OBSERVED_METADATA only
on on-disk stats checks (PARQ040-042, git index); DERIVED expanded to all
correlation/absence checks (ICE001/002/008-010/012/013/022/023/025,
PARQ021, CTM003/004/009/010/028, STREAM013). Details in the
semantic-hardening run record.
