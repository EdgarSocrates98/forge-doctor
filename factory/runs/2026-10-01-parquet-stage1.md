# Run: 142-parquet-model (Parquet stage 1)

- Spec: `factory/specs/active/142-parquet-model.md` (staged, grill: completed)
- Agent: devin
- Source prompt: `prompt_evo_parquet_delta.md` — 8-subcycle Storage &
  Table Intelligence program (Parquet model → physical → pushdown → Delta
  model → Delta MERGE → Delta maintenance → runtime compat → cross-format).

## Implemented

- `ParquetProjectModel` (`analyzers/parquet_model.py`) — two planes:
  code evidence (writers/readers/configs/write_patterns from the AST
  index: `read.parquet`, `write.parquet`, `format("parquet")`,
  `option("compression",…)`, `spark.conf.set("spark.sql.parquet.*")`) and
  on-disk `*.parquet` files (`stat` sizes only → count/total/median/p95).
- 7 checks (`category = "parquet"`): PARQ000 anchor, PARQ010
  repartition/coalesce-before-write, PARQ020 compression none/uncompressed,
  PARQ021 inconsistent codecs, PARQ040 median size below floor, PARQ041
  excessive file count, PARQ042 p95/median skew — all thresholds from
  `knowledge/parquet/format.json`.
- `forge-doctor parquet inspect` — dataset stats, compression values,
  writer/reader counts, severity-sorted risks. Verified live.
- Inbox specs for sub-cycles 2–8: 143 physical (metadata snapshot/opt-in
  extra), 144 pushdown cross-SQL, 145 Delta model+protocol, 146 Delta
  MERGE, 147 Delta maintenance/layout, 148 Delta runtime compat
  (Glue/Databricks), 149 `storage health|migrate`.

## Latent quirks found

- `_call_name` needed a final `.rsplit(".")` — inside-out dotted
  `write.option(df.repartition)` yields terminal `option` only after the
  last-dot cut. Fixed in both parquet_model and iceberg_model; this also
  unbroke `option("compression")` detection inside chained writes.
- `.pytest_tmp` is cleaned by the suite — e2e fixtures belong elsewhere.

## Verification

- `pytest`: 594 passed (+23)
- `ruff check`, `ruff format --check`: clean
- `mypy src`: clean (83 files)
- E2E: `parquet inspect` on fixture renders dataset stats, `none`
  compression, PARQ020/010/040 risks.

## Gate

Spec left in `active/` — awaiting human review + `archive --accepted`.
