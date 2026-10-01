# Run: connected-data phase A2 — semantic accuracy hardening (spec 170)

Also incorporates the `prompt_evo_evolucao2.md` review corrections to
spec 169's evidence tagging (fact-level, not category-level).

## EvidenceKind retag (review feedback on 169)

- `_IcebergCheck` base OBSERVED_METADATA → **STATIC** (the model never
  reads real `metadata.json`; all evidence is code/SQL/IaC facts).
- `_ParquetCheck` base → **STATIC**; only on-disk file-stats checks
  (PARQ040/041/042) keep OBSERVED_METADATA.
- DERIVED now marks every multi-fact correlation/absence check:
  ICE001, ICE002, ICE008–010 (maintenance absence), ICE012, ICE013,
  ICE022, ICE023, ICE025; PARQ021 (codec correlation); STREAM013;
  CTM003/004/009/010/028 (cross-job event/name/calendar correlation).
- Rule applied: DERIVED when the finding *is* the combination of facts;
  STATIC/CONFIG when it attaches to one observed fact.

## Adversarial suite (`tests/unit/adversarial/`, 44 tests)

| domain | FP covered | FN covered | malformed/cross-file |
|---|---|---|---|
| sql | comment exclusion, bare strings | CTE not a table, aliased tables, quoted reserved id | truncated/garbage → `unparsed`, empty; **version contract**: `out` is reserved in sqlglot 28 — degrade-or-parse, never raise |
| streaming | `obj.readStream` attr | aliased `ss.readStream`, `queryName` kwarg, lambda foreachBatch | write-only file → sink-only record; cross-file halves (documented limitation); `option("checkpointLocation")` no-literal → dynamic+empty (documented ambiguity: index keeps only literal args) |
| stepfunctions | — | per-resource definition scoping regression, ghost StartAt → SFN002 fires | truncated JSON, `States: []`, glued-EOT heredoc → 0 machines (documented) |
| terraform | — | two same-type resources stay scoped | unbalanced braces graceful; block-comment resource never raises |
| airflow | `dag` name w/o airflow import | **FIXED**: `from airflow import DAG as Dag` (with/assign) + `dag as x` decorator were missed — `_FileWalk` now resolves airflow import aliases | syntax-error file graceful |
| controlm | random JSON shape | — | truncated/garbage JSON, `{}` job |
| parquet | `.parquet` string literal | `format("parquet").save()` | empty dir, zero-byte files |
| iceberg | `USING iceberg` in comment, bare word | MERGE no-partition finding | — |

## Real fix shipped

`_FileWalk` now collects `from airflow… import DAG as X` and
`dag as y` aliases before walking — ctor aliases feed `_dag_call`,
decorator aliases feed the `@dag` path. Verified: all three alias forms
detect, non-airflow `dag`/`Dag` names still no-FP.

## Documented limitations (not silently skipped)

1. Streaming var binding is per-file: cross-file read/write halves stay
   two records (`file` grouping).
2. `option("checkpointLocation")` no-literal is indistinguishable from a
   non-literal expression (index keeps literals only) → `dynamic=True`.
3. Terraform heredoc requires `\n` before the terminator.
4. Terraform block comments aren't stripped before the line scan.
5. sqlglot 28 reserved-word set: unquoted `out` (etc.) → unparsed;
   quoting (`"out"`) parses on all supported versions.

## Verification

- `pytest tests/unit/adversarial` — 44 passed
- `pytest -x -q` — 678 passed
- `mypy src` — 89 files clean · `ruff check` + `format --check` — clean
