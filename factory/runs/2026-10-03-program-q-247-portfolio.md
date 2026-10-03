# Program Q — Wave 7 Run Record: Spec 247 Platform Portfolio

Date: 2026-10-03 · Spec: `factory/specs/active/247-platform-portfolio-intelligence.md`

## Delivered

- `src/forge_doctor_data/core/portfolio.py`
  - `PlatformPortfolio` — platforms, workloads, logical_datasets,
    physical_representations, teams, environments, cost_drivers,
    reliability_signals, lifecycle map, criticality set, duplications,
    complexity. Every row deterministically sorted; no scores.
  - `TechnologyInstance` (platform, version, lifecycle_status, owner,
    workloads, criticality, replacement) — lifecycle comes from
    capability-pack `deprecated_in`/`removed_in`/`replacement` facts and
    domain runtime packs (`knowledge/<platform>/runtimes.json` `eol` /
    `deprecated` lists; e.g. a lambda on `python3.8` reports EOL).
  - `PortfolioDuplicationSignal` — `DATASET_ACROSS_PLATFORMS`,
    `WORKLOAD_ACROSS_ORCHESTRATORS`, `CROSS_CLOUD_COPY`; classification
    is always OPPORTUNITY, never an error.
  - `PortfolioComplexitySignal` — engines / orchestrators /
    governance_planes / copies / cross_cloud_edges / owners as counts.
  - §7.2 answers: `answer_engines_per_workload`,
    `answer_datasets_across_platforms`, `answer_critical_platforms`,
    `answer_deprecated`, `answer_cross_cloud_concentration`,
    `answer_team_cross_platform_deps`.
  - `RecurringPattern` + `recurring_patterns()` — a check id firing in
    >=2 repos is a candidate shared-capability signal.
  - `ValidatedOptimizationEvidence` + `record_/load_optimization_evidence`
    — `.forge-doctor-data/optimization-evidence.jsonl`, scope field is
    literally "project-local"; nothing generalizes globally.
- `fleet portfolio` — portfolio facts + §7.2 answers (text/JSON).
- `fleet regressions` — per-repo recorded history -> fingerprint series
  -> shared regression dimensions across >=2 repos (stored samples carry
  fingerprints only — job series cannot be rebuilt, by design).
- MCP: `get_execution_baseline`, `get_regressions`,
  `get_runtime_correlations`, `get_incident_explanation`,
  `get_critical_path`, `get_capacity_signals`, `get_portfolio_summary`
  (13 tools total).
- Evidence Bundle v2: `scan --evidence-out DIR --evidence-compact`
  writes `summary.json` (id/severity/location/message rows + roll-ups)
  for token-constrained consumers; default bundle unchanged.
- `platform_graph_builder`: datacontract `owner` now lands on governed
  entities (same bridge pattern as the spec-245 SLA attrs).
- Lab harness: `fleet_manifest` + `expected_duplications` truth keys;
  `labs/portfolio/multi-repo-duplication` — S3+GCS `raw-orders`
  (cross_cloud_copy), Kinesis+Kafka `events` (dataset_across_platforms),
  Airflow+ADF `etl` (workload_across_orchestrators). PASS.
- `_ORCHESTRATOR_DOMAINS` extended with adf / composer / stepfunctions
  (all genuine workflow orchestrators).

## Honest-position notes

- `cross_cloud_edges` counts only relationship endpoints spanning
  clouds; same-named datasets on two clouds report as CROSS_CLOUD_COPY
  duplication instead (they share no edge).
- Platform lifecycle is UNKNOWN when neither capability nor runtime
  packs carry facts — "active" is never assumed.
- Fleet regression aggregation keys on recorded fingerprints; a repo
  with no `.forge-doctor-data/execution-history/` simply doesn't appear.

## Validation

- `pytest tests/unit/ -k "portfolio or fleet"` — 32 passed.
- `lab run` — 66/66 scenarios PASS.
- `ruff check`, `ruff format --check`, `mypy` (271 files) — clean.
- Full suite: **2131 passed** in 172.04s.
