# Roadmap

v0.1 is a small, excellent foundation — not the whole diagram.

## v0.2 — reach — partially shipped
- Shipped: `explain <CHECK_ID>`, example plugin + `docs/plugins.md`,
  Docker, Glue and CI categories (47 checks total).
- Remaining: Databricks, Airflow categories; `pipx inject` integration
  test in CI once published.

## v0.3 — UX & tooling — shipped
- Baselines: `scan --save-baseline` / `--baseline` (`NEW` markers, `is_new`
  in JSON, exit code counts only new findings).
- `--format html` self-contained reports, `--output`, `--no-color`.
- `forge-doctor init` scaffolding, `forge-doctor info` stats.
- `--watch` re-scan loop, shell completion, Rich tables/panels throughout.

## v0.4 — engine v2 & integrations — shipped
- **Finding Model v2**: `confidence`, `fingerprint`, `tags`, `docs_uri`,
  `evidence`, `source`, `fixable`, `column`/`end_line`/`end_column` on
  `CheckResult` + serialized in JSON/SARIF.
- **Knowledge packs** (`forge_doctor/knowledge/`): Glue versions +
  compatibility matrix (through Glue 6.0), Spark runtime map, Python
  compatibility — version facts evolve without engine changes.
- **SARIF 2.1.0** (`--format sarif`) for GitHub Code Scanning; composite
  action in `action.yml` uploads it automatically.
- **`--format agent`**: compact `{id, sev, loc, fp}` bundle for LLM/agent
  consumers; `explain <ID> --json` for on-demand rule metadata.
- **`forge-doctor diff`**: report-vs-report, file-vs-ref, and
  `diff base...head` git ranges via temporary worktrees.
- **`forge-doctor compatibility`**: detected environment + migration-risk
  matrix (`--from`/`--to`).
- **`forge-doctor workspace`**: monorepo project discovery.
- **Profiles**: `default|strict|security|spark-performance|glue-migration|production`.
- **Pre-commit**: `.pre-commit-hooks.yaml` + `--files` filtering.
- **Plugin trust model**: `--no-plugins`, `[tool.forge-doctor.plugins].allow`,
  findings stamped with their plugin `source`.
- **Spark AST v2**: alias/symbol tracking (`import ... as`, `df = spark.read...`,
  chained receivers) — findings carry receiver confidence; SPARK006 now pairs
  `unpersist()` per variable; SPARK008-011 added.
- Release workflow → PyPI Trusted Publishing (OIDC) + PEP 740 attestations
  (disabled until the PyPI project exists).

## v0.7 — engine v3, data intelligence & ecosystem — shipped

Engine:

- **Fingerprint v3** — semantic `check_id|file|symbol|anchor` identity;
  findings survive line moves and message edits. Baseline format 2.
- **Contract v3** — `schema_version: "3.0"` JSON/agent output,
  project-relative POSIX paths, opt-in `--show-root`.
- **Single-scan `--emit FMT[:PATH]`** — repeatable multi-target rendering;
  `jsonl` format; `--stats` (per-check timings + cache hit rate).
- **Semantic index** — one `ast.parse` per file feeds Spark, Glue, lineage
  and `trace`; imports, call sites with literal args, assignment chains,
  enclosing symbols.
- **Incremental cache** — sha256-keyed per-file facts in `.forge-doctor/`;
  `--cache/--no-cache`, `forge-doctor cache` group, `--watch` via
  `watchfiles` (polling fallback).
- **Policy-as-code** — `[tool.forge-doctor.policy]` rule overrides,
  expiring/scoped/owned suppressions, `suppressions` audit, POLICY001/002.
- **Plugin SDK v2** — `PluginIdentity`/`PluginDescriptor`, api_version
  gating, `plugins list|validate|doctor`; `cli/` modularized into a package.

Data intelligence:

- **`diagnose`** — offline log fingerprinting via `knowledge/errors/` packs
  (spark, glue, databricks, iceberg, lakeformation, python).
- **`spark eventlog|plan|logs`** — runtime diagnosis: executor loss, skew,
  spill, GC pressure, cartesian products, BNLJ, global sorts.
- **`lineage`** — static reads/writes graph; text/json/dot/mermaid +
  OpenLineage-shaped JSON.
- **`schema diff`** — avsc/JSON Schema/DDL/dbt diffs classified
  compatible | potentially breaking | breaking, over files or git ranges.
- **`migrate glue`** — version-pin, DynamicFrame and dependency signals on
  top of the knowledge-pack risk matrix.
- **IaC checks** — IAC001-004 over Terraform/CloudFormation via `hcl_lite`.
- **`trace`** — why a finding fired: evidence, symbol, receiver, assignment
  chain, imports.

Ecosystem:

- **`workspace scan|diff`** — monorepo orchestration with per-project
  aggregation.
- **`knowledge list|info|verify`** — pack provenance, schema_version 2,
  staleness warnings.
- **`sbom`** — CycloneDX 1.5 of deps + plugins + packs.
- **`mcp`** — zero-dependency JSON-RPC stdio server (6 tools, resources).
- **`lsp`** — optional `pygls` server mapping findings to diagnostics.
- **`graph`** — Project Intelligence Graph (repo/job/dataset/infra/
  orchestrator) in json/dot/mermaid.
- **`doctor`** — environment self-check.

## v0.8–v1.0 — platform intelligence & hardening — shipped (in review)

Platform intelligence (the `DataPlatformGraph` + cross-domain engine):

- Canonical entity/relationship graph across all domain models;
  `platform graph|blast-radius|findings`.
- Cross-domain rule engine (PLAT rules), offline runtime evidence
  (`runtime inspect|diagnose`), finding promotion + root-cause
  clustering (`root-cause`), deterministic remediation (`remediate`).
- Architecture contracts + drift (`contract validate`,
  `architecture drift`); what-if evaluation + named migration plans
  (`what-if`, `migrate plan`).
- Deep-intelligence packs: Lake Formation, EMR, Databricks, Delta,
  Athena, Lambda, Step Functions, Kafka, Kinesis, Flink, streaming
  delivery semantics.

Reliability & trust:

- **Forge Lab** (`lab run|metrics`) — scenario suites with ground truth;
  precision/recall per domain.
- **Golden repos** (`golden run|update`) — snapshot regression over
  realistic fixture corpora.
- **Bench** (`bench run`) — cold/warm scan and model timings.
- **Workspace intelligence** (`workspace inspect`) — cross-repo
  DEFINES/IMPLEMENTS/INVOKES links.
- **Semantic diff** (`diff --semantic`) — entity diff + blast radius +
  risk class for PR review.
- **Policy packs** (`policy list|eval|validate`) — org-declared
  forbid/require rules.
- **Public API** (`forge_doctor.api`) — stable SDK surface + JSON Schema
  contracts (`schema contracts`); see `docs/api.md`.
- **v1.0 readiness** — `docs/release.md` checklist; tagging remains a
  human decision.

## Roadmap-3 — extensible operational platform (in progress)

- **P1 Plugin SDK & ecosystem** — shipped: `forge_doctor.sdk` stable
  surface, `plugins init|lock|verify|install`, strict trust mode.
- **P2 Enterprise governance** — shipped: pack `extends` layering,
  `require_approval` + `POLICY011`, `approved_by` on suppressions,
  `policy report`, named per-branch baselines, `scan --evidence-out`.
- **P3 Fleet intelligence** — shipped: `fleet inspect|query|report`
  over a manifest of repos; shared `merge_repos` workspace merge.
- **P4 Historical intelligence** — shipped: `scan --record` snapshots,
  `history` / `history diff` / `history trend`.
- **P5 Change intelligence** — shipped: `diff --semantic` adds
  capability transitions (registry evaluated per ref's observed
  versions) and migration requirements extracted from the
  `compatibility` knowledge packs; `-f json` emits
  `capabilities` + `migration_requirements`.
- P6–P10 (specs 207–211): continuous incremental
  analysis, safe-fix (`--dry-run`/`--apply` boundaries), experiment
  engine, knowledge supply-chain lifecycle, Forge ecosystem contracts.

## Consolidation programs (specs 225–229, run before roadmap-4)

The official sequence inserts a consolidation layer between P6–P10 and
coverage expansion — numbered 225–229 because 212–224 are already
taken:

- **Platform Ontology** (225): canonical vocabulary — entity kinds,
  relationship kinds, evidence planes, domains, capability families —
  with conformance validation so adapters can't drift silently.
- **Capability Graph** (226): provenance wiring from each evaluated
  capability to the entities/models/evidence that produced its status.
- **Formal Digital Twin** (227): the `DataPlatformGraph` plus a
  deterministic invariant suite — the trusted snapshot that decision
  and optimization intelligence stand on.
- **Decision Intelligence** (228): `advise` — ranked, fully-cited
  actions merging root causes, remediation plans, fix safety, policy,
  and blast radius.
- **Optimization Intelligence** (229): `optimize` — ranked improvement
  candidates with static cost-proxy estimates and an explicit
  `lab experiment`/`what-if` validation path.

## Roadmap-4 — data-platform coverage expansion (specs authored)

Generic model first, then vendor adapters (invariant: vendors are
implementations of neutral abstractions):

- **Wave 1 Warehouses** (212–215): `WarehouseProjectModel` core, then
  Snowflake, BigQuery, Redshift adapters + capability packs.
- **Wave 2 Transformation/semantic** (216–217): dbt project model +
  lineage; data-contract linting + schema-evolution detection.
- **Wave 3 Federated & serving** (218–220): Trino catalogs/connectors;
  `AnalyticalEngineModel` for ClickHouse/Pinot/Druid; `SearchPlatformModel`
  for OpenSearch/Elasticsearch.
- **Wave 4 Catalog/governance** (221): DataHub/OpenMetadata adapters +
  declared-vs-actual drift checks; Glue/Unity catalog coverage signals.
- **Wave 5 Data quality** (222): Deequ/GX/Soda/dbt-test suites as
  evidence; prod-without-tests and defined-not-run gates.
- **Wave 6 Multi-cloud abstractions** (223): `ObjectStorage`/
  `ComputeEngine`/`Catalog`/`Stream`/`OperationalStore`/`Warehouse`
  across AWS/Azure/GCP — additive view over existing entities.
- **Wave 7 Cross-platform migration** (224): `migrate plan --from X
  --to Y` capability-parity plans + `what-if` on abstractions.

## Next

The engine is stable — new work lands as **data-intelligence packs** on top
of the semantic models (invariant V11), not as engine changes.

- SQL first-class — shipped: `[sql]` extra (sqlglot), `SqlIndex` over
  `*.sql` files and `*.sql("...")` call literals, SQL000–SQL003. Remaining:
  SQL tables → lineage/graph edges, engine-dialect migration analysis.
- Iceberg pack (spec 113): format-version, partition evolution,
  snapshots/retention, `rewrite_data_files`, delete modes, catalog config,
  Glue/Athena/EMR compat — via `IcebergProjectModel`.
- Lake Formation pack (spec 114): FGAC, cross-account, resource links,
  credential vending — incl. `diagnose` correlation.
- Bigger Spark performance pack (spec 115): dropDuplicates on full frames,
  broadcast hints, repartition-before-write, `spark.sql.shuffle.partitions`,
  AQE off, repeated actions across files, `count()` for logging only.
- Databricks / Airflow / dbt / EMR categories (AST-first where possible).
- Security adapters instead of homegrown scanners: OSV-Scanner/pip-audit
  (`forge-doctor security --engine osv`), `zizmor` for workflows.
- Safe autofix — only after baselines prove stable: deterministic transforms,
  always `--dry-run` first.
- Tree-sitter for Scala/Java/Shell later; Python stays on `ast`.

## Non-goals for the foreseeable future
- No health score until a defensible weighting exists.
- No `fix` subcommand until diagnostics are proven stable.
- No network/cloud calls in `scan`.
