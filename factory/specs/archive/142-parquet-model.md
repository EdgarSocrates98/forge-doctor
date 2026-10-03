---
id: 142-parquet-model
title: Parquet stage 1 - ParquetProjectModel, PARQ0## checks, `parquet` CLI
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_parquet.py tests/unit/test_parquet_model.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_parquet_delta.md` sub-cycle 1 of 8: Parquet is the physical
file layer beneath Delta (and beside Iceberg). Model covers code-level
evidence (writers/readers/configs/options) plus on-disk `*.parquet` files
(sizes only - no footer parsing yet; that's stage 2 via optional extra).

# Acceptance Criteria
- `analyzers/parquet_model.py` `ParquetProjectModel`: evidence kinds
  `writer`/`reader` (`read.parquet`, `write.parquet`, `format("parquet")`,
  `.load`/`.save` with parquet format), `config` (`spark.sql.parquet.*`,
  `spark.sql.files.*`, `option("compression",...)` args), `write_pattern`
  (repartition/coalesce in a file that writes parquet), `file` (on-disk
  .parquet with byte size). Derived dataset stats: count/total/median/p95/
  min/max.
- `checks/parquet.py` (`category = "parquet"`): PARQ000 anchor, PARQ010
  repartition/coalesce-before-parquet-write (INFO), PARQ020 compression
  `none`/`uncompressed` evidence (WARNING), PARQ021 inconsistent codec
  config across files (INFO), PARQ040 dataset median file size below pack
  threshold (INFO), PARQ041 file count above pack threshold (INFO),
  PARQ042 size distribution skew (p95/median ratio over pack ratio, INFO).
- `cli/parquet.py`: `forge-doctor-data parquet inspect [path]` - writers/
  readers/compression/dataset stats/risks (reviewer's output shape).
- `knowledge/parquet/format.json` - codecs list, thresholds
  (small_file_median_mb, excessive_files, size_skew_ratio), schema 2
  + sources.
- Tests per check + CLI; docs checks.md PARQ rows + CHANGELOG.

# Constraints
- File stats from os.stat only - never open/parse file bytes in stage 1.
- Thresholds live in the pack, not the checks.
