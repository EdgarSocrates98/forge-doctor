# Roadmap-2 Phase 1 run — Forge Lab

- Spec: `factory/specs/active/192-forge-lab.md`
- Commit message: `feat(lab): add Forge Lab scenario suites with ground truth`

## Implemented

- `core/lab.py` — `GroundTruth`/`load_ground_truth` (JSON,
  stdlib-only), `discover_scenarios`, `run_scenario`, `run_lab`.
  Four ground-truth categories:
  - `expected_findings` — `CHECK_ID` or `CHECK_ID@file-fragment`;
  - `forbidden_findings` — presence → `forbidden_hit` failure;
  - `expected_graph_edges` — `kind|src->dst` exact ids;
  - `expected_capabilities` — `[platform:]CAP[@ver][;attr=v…]=status`;
  - `expected_root_causes` — cluster-id prefixes; optional `runtime/`
    artifact dir is ingested for `cluster_findings`.
  Detected-but-undeclared findings land in `extra` (informational FP
  surface). `ScenarioReport.passed` = no misses + no forbidden hits +
  no errors.
- `cli/lab.py` — `lab list`, `lab run [scenario|dir]`, `lab report`,
  `--labs`/`--json`; exit 1 on failure.
- `labs/` seed corpus — 10 verified scenarios, one per domain dir:
  airflow/dag-catchup (AIR013+AIR025), cross-domain/lf-emr-iceberg
  (ICE008+LF010 + governs/writes edges), dynamodb/streams-lambda
  (DDB001+LAM002 + produces/triggers edges), glue/glue4-job
  (GLUE003 + glue iceberg capability), iceberg/merge-churn
  (ICE001/ICE008/ICE022 + format_version capability gating),
  lakeformation/cross-account-link (LF001+LF012 + depends_on edge),
  neptune/unbounded-traversal (GRAPH021+NEP001), spark/repartition1-write
  (SPARK001+SPARK003), streaming/kafka-backlog (KFK003/KFK006 +
  RC_STREAM_COMMITS from 3 runtime progress artifacts),
  terraform/plaintext-msk (KFK001 + defines edge).

## Fix discovered by the lab

- `delta_model` attributed generic SQL DML (MERGE/UPDATE/DELETE/
  OPTIMIZE/VACUUM) to Delta with zero delta evidence → DELTA001 fired
  on the Iceberg-only scenario. Fixed: generic SQL ops now require a
  project-level delta signal (`_DELTA_SIGNAL_RE`: `USING DELTA`,
  `DeltaTable`, `format("delta")`, `spark.databricks.delta`,
  `io.delta`, `delta.tables`, `table_changes(`); `create_using_delta`
  and `cdf_read` are self-evidencing and stay ungated.

## Ground-truth format note

Capability expectations carry `;attr=v` context pairs
(`iceberg:ICEBERG_MERGE_WRITE;format_version=1=unsupported`) since
pack entries gate on attributes; expected status splits at the last
`=` so attribute pairs can precede it.

## Verification

- `pytest tests/unit/test_lab.py tests/unit/adversarial/test_lab.py` →
  21 passed
- `forge_doctor_data lab run` → 10/10 scenarios pass (text + JSON verified)
- `pytest -q` → 1217 passed, 0 failed
- `mypy src` → clean (143 files); `ruff check` + `ruff format` clean

## Open questions

- Metrics (precision/recall roll-ups) land in R2-P2; the report already
  carries the raw sets needed.
