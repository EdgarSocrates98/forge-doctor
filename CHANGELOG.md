# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (roadmap-2: production readiness)

- **Forge Lab** — `forge-doctor lab list|run|report|metrics`:
  reproducible scenario suites under `labs/` with `expected.json`
  ground truth plus per-domain precision/recall/FPR/parser-coverage
  metrics (`allowed_findings` + `labs/_defaults.json` noise budgets).
- **Golden repositories** — `forge-doctor golden list|run|update`:
  `golden/<name>/repo` + `expected/` snapshots pin full engine output
  (findings, graph, root causes, remediations, migrations) for
  regression gating. 8-repo seed corpus.
- **Performance benchmark** — `forge-doctor bench run [--files N]`
  measures cold/warm scan time, AST parse count, graph build, pack load,
  and peak memory; `--budget` enforces portable ratio/count budgets.
- **Workspace intelligence** — `forge-doctor workspace inspect` builds a
  `WorkspaceModel`: marker-based repo discovery, per-repo platform
  graphs merged by canonical entity id, and cross-repo `DEFINES` /
  `IMPLEMENTS` / `INVOKES` links from `repo:workspace:<name>` entities.
- **Semantic diff** — `forge-doctor diff <base>...<head> --semantic`:
  entity-level `added`/`removed`/`modified`/`touched` changes, blast
  radius via dependency-aware traversal, and a deterministic
  low/medium/high risk classification with reasons — CI-gateable PR
  intelligence without an LLM.
- Terraform typed entities propagate version attrs (`glue_version`,
  `runtime`, `engine_version`, `release_label`, `format_version`,
  `spark_version`); `add_entity` merges duplicate-id entities instead of
  discarding later producers' attrs.
- **Organization policy packs** — declarative `forbid`/`require` rules
  in `.forge-doctor/policy/*.yml|*.json` (or `policy_packs` config):
  file-pattern rules, terraform attr checks (`equals`/`matches`/
  `present`), required files, required file content. Findings carry org
  rule ids; invalid packs surface as `POLICY010`. New commands:
  `forge-doctor policy list|eval|validate`.
- **Public API** — `forge_doctor.api` is the stable SDK surface
  (`scan`, `platform_graph`, `capabilities_evaluate`, `what_if`,
  `migrate_plans`, `version`, `SCHEMA_VERSION`, re-exported
  `ScanReport`/`ScanOptions`/`DataPlatformGraph`); `SCHEMA_VERSION` is
  now the single constant behind every `schema_version` JSON key.
  `forge-doctor schema contracts [name]` publishes JSON Schemas for the
  public artifacts; `docs/api.md` records the semver/stability rules.
- **Docs completeness** — README usage now covers every command group
  (domain `inspect` CLIs, `lab`/`golden`/`bench`, `policy`, `what-if`,
  `remediate`, `root-cause`, `runtime`, `contract`/`architecture`,
  `capabilities`, `schema contracts`, semantic `diff`); the checks table
  covers all 28 categories; stale `schema_version` in the JSON example
  fixed. New `docs/getting-started.md` onboarding path; CONTRIBUTING
  documents knowledge packs, lab scenarios, golden repos, and policy
  packs. `test_docs.py` enforces docs coverage deterministically —
  every command, check id, and category must be documented, and doc
  links must resolve.
- **v1.0 release hardening** — `SCAN_SCHEMA_VERSION` (`"3.0"`) exported
  beside `SCHEMA_VERSION` (`"1.0"`): the scan JSON report and the newer
  artifact family are two independent contracts, now documented as such.
  Published JSON Schemas corrected to match real output
  (`scan-report`: tool/project/summary objects; `golden-snapshot`: row
  arrays + graph object) and enforced by contract tests. CI smoke
  strengthened: contract-shape asserts plus `schema contracts`,
  `explain`, `checks` on the installed wheel. New `docs/release.md`
  v1.0 pre-flight checklist.
- **Plugin SDK & ecosystem** — `forge_doctor.sdk` is the stable
  author-facing surface (Check/CheckBase/CheckResult/PluginDescriptor/
  ProjectContext/api versions, `__all__` pinned); `plugins init`
  scaffolds a complete plugin package; `plugins lock`/`verify` pin and
  audit installed-plugin integrity via content digests; `plugins
  install` wraps `pipx inject`/`pip` with post-install validation;
  `[tool.forge-doctor.plugins] mode = "strict"` adds default-deny trust
  (only `trusted` may load — `allow` identities no longer suffice).
- **Enterprise governance** — policy packs gain layering (`extends:` a
  named or path-referenced pack; child rules override the parent's on
  id collision; cycles/missing refs surface as `POLICY010`) and
  `require_approval`, under which suppressions missing `approved_by`
  emit `POLICY011` findings. `suppressions` records/report show the
  approver. `forge-doctor policy report` summarizes compliance (packs,
  violations by rule, suppression audit; `--format json`). `--baseline`
  /`--save-baseline` accept bare names (`main`) that resolve to
  `.forge-doctor/baselines/<name>.json` for per-branch baselines.
  `scan --evidence-out <dir>` writes a dated audit bundle
  (`report.json` + `suppressions.json` + `packs.json`).
- **Fleet intelligence** — `forge-doctor fleet` merges a manifest of
  repositories (`fleet.yml`/`fleet.json` paths, or `root:`/directory
  workspace discovery) into one estate graph via the same
  DEFINES/IMPLEMENTS/INVOKES merge as `workspace`. Queries:
  `fleet query <m> runtimes` (entities by domain + versioned attrs),
  `capability <id>` (per-entity registry buckets), `dependents <glob>`
  (dependency-aware blast radius incl. cross-repo links), `findings
  <check-id|glob>` (per-repo scan). `fleet report` = estate census +
  findings roll-up; text + JSON. See `docs/fleet.md`.
- **Historical intelligence** — `scan --record [--keep N]` appends a
  compact snapshot to `.forge-doctor/history/<utc>.json` (summary
  counts, finding fingerprints, entity census, capability states, ARCH
  drift ids). `forge-doctor history` lists snapshots; `history diff
  <a> <b>`/`--last` reports new/resolved findings (fingerprint join
  key), entity adds/removals, capability transitions, drift changes;
  `history trend` shows per-category counts over the series + a debt
  trajectory. History commands never re-scan; snapshots are only
  written on `--record`.
- **CLI UX** — top-level `--help` groups the 60+ commands into panels
  (Scan & findings / Platform intelligence / Estate & change / Quality
  gates / Setup & integrations); scan options grouped into Scope &
  filters / Output / Gates & baselines / Runtime / History & evidence
  with a runnable `Examples:` epilog; bare `forge-doctor` prints help;
  groups with a bare-invoke default advertise it ("Bare: …"); category
  section headers render proper names (AWS, CI, IaC, DynamoDB…).
- **Change intelligence** — `diff --semantic` now reports capability
  transitions (capability registry evaluated against each ref's
  observed per-domain versions, e.g. `LAKEFORMATION_FGAC: conditional
  -> supported` on a Glue 4.0 -> 5.0 bump) and migration requirements:
  version moves in entity attrs (`*_version`/`runtime`/`dbr`) are
  matched against the domain `compatibility` knowledge packs to list
  required changes and HIGH-severity blockers per changed entity.
  `diff --semantic -f json` emits `capabilities` +
  `migration_requirements`; absent or uncovered versions report
  `unknown`, never fabricated steps. Exit-code semantics unchanged.
- **Incremental analysis** — `forge-doctor scan --incremental` reuses
  per-check results from the previous scan for checks whose declared
  evidence domains are untouched by the file changes detected since
  then. `core/incremental.py` defines the evidence-domain vocabulary
  (`terraform`, `spark`, `airflow`, `env`, `host`, …), a per-module
  declared-domain map (`MODULE_DOMAINS`, overridable per check via
  `Check.evidence_domains`), file→domain classification, mtime+size
  change detection, and a versioned `ResultStore` in the user cache
  dir. Cross-domain/unbounded checks (platform rules, contracts,
  migrations, what-if) always rerun, preserving the full-scan output
  contract — verified by tests asserting identical result sets between
  incremental and full scans. `--stats` reports
  `incremental: N file(s) changed, X checks rerun, Y reused`; watch
  mode inherits incremental when enabled.
- **Safe-fix intelligence** — `forge-doctor fix <path>` produces
  safety-classified fix proposals (`core/fixes.py`): `safe`
  deterministic text transforms (declare `requires-python`, append
  ignore patterns to `.gitignore`, create a default `.gitignore`),
  `review-required` proposals applied only with `--apply --class
  review`, and `manual-only` guidance rows for IaC/IAM/LF-class
  findings which have no apply path at all. Dry-run prints unified
  diffs; `--apply` re-reads targets and aborts stale sources; `--json`
  emits an audit record. No git ops, no commits — version control is
  the rollback.
- **Experiment engine** — `forge-doctor lab experiment <scenario>
  --hypothesis <name>` applies a named deterministic transform
  (`bump-glue-version`, `partition-data`, `add-checkpoint`,
  `increase-trigger-interval`) to a temporary copy of a lab fixture,
  rescans it hermetically, and reports a before/after comparison:
  findings resolved/introduced by fingerprint, severity deltas, file
  counts, and a verdict (`improved` | `regressed` | `neutral`) with
  reasons. `--json` emits both sides plus deltas. The original
  fixture is never touched; hypotheses are pure file edits — no live
  runtime measurement, no subprocesses.
- **Knowledge supply chain** — the `knowledge` group gains
  `new <domain> [--kind]` (pack scaffold with provenance + positive
  examples), `diff <a> <b>` (semantic entry diff — added/removed/
  changed rows keyed by section:id, meta fields excluded),
  `test` (conformance: structure, `re:` compile, examples match a
  pattern, capability assertions re-evaluate per declared version via
  the real registry), and `publish <domain> [--bump]` (freshness
  checklist + optional pack_version/verified_at rewrite; dry-run only,
  distribution stays manual). Entries relying only on `re:` patterns
  without an `examples` fixture report as warnings.
- **Ecosystem contracts** — the published contract set grows to the full
  interop surface: `evidence`, `finding`, `capability-report`,
  `platform-graph`, `remediation-plan`, and `handoff-bundle` join the
  existing `scan-report`/`policy-pack`/`lab-expected`/`golden-snapshot`
  schemas (`forge-doctor schema contracts <name>`). New commands:
  `forge-doctor export <path> --format handoff` emits a portable JSON
  bundle (`{contract, contract_version, schema_version, tool, project,
  summary, results, graph, capabilities, plans}`) with stable keys and
  deterministic ordering for downstream Forge tools, and
  `forge-doctor contracts list|verify <bundle>` validates an artifact
  file or stdin against the published schemas via a dependency-free
  subset validator (`core/contract_check.py`). `plan_to_dict()` moves to
  `core/remediation.py` so `remediate --json` and handoff bundles share
  one serializer. `docs/contracts.md` is the interop spec.
- **Platform ontology** — `core/ontology.py` is the canonical vocabulary
  registry: every entity kind, relationship kind, evidence plane,
  evidence domain, producer domain, and capability family carries a
  documented definition. `forge-doctor ontology` prints it
  (`-f json` for the stable machine shape); `forge-doctor ontology
  validate <path>` conformance-checks a project's graph — free-text
  producer domains outside the vocabulary are diagnostics. The
  `platform-graph` contract now pins `kind`/`evidence_kind` to the
  vocabulary enums, so `contracts verify` enforces ontology conformance.
  `docs/ontology.md` tables are test-verified against the module.
- **Capability graph** — every evaluated capability now carries its
  deciding evidence: `CapabilityResult` gains `entry_id`,
  `matched_when`, and `missing_evidence` (for `unknown` — the versions
  or when-attrs that would decide). `forge-doctor capabilities graph
  <path>` renders the provenance subgraph: `capability` entities
  `EVIDENCED_BY` their deciding `knowledge_pack` entities plus the
  platform entities that supplied the evaluated version; `--json`
  emits it in `platform-graph` contract shape. `capabilities list
  --json --provenance` adds `{status, provenance}` rows opt-in
  (default shape unchanged); `capabilities explain` reports
  `entry_id`/`matched_when`/`missing_evidence`. Ontology gains
  `capability`/`knowledge_pack` kinds, `EVIDENCED_BY` rel, and the
  `knowledge` producer domain (additive vocabulary).
- **Formal digital twin** — `core/twin.py` assembles the platform graph
  plus a deterministic invariant suite (I1 dangling endpoints, I2
  canonical id shape, I3 evidence-plane binding, I4 finding-file
  resolution under root, I5 attr-completeness as informational gaps).
  `forge-doctor twin inspect <path>` prints the summary + invariant
  report and exits 1 on hard violations; `twin export` emits the
  deterministic snapshot artifact (`entities`/`relationships` +
  `invariants_ok`/`summary` header — satisfies the `platform-graph`
  contract). Validation reports; it never mutates the graph.
- **Decision intelligence** — `forge-doctor advise <path>` merges
  existing signals into one ranked, fully-cited action list
  (`core/decisions.py`): severity, confidence, cluster membership
  (+runtime-confirmed boost), fix safety class, remediation plan
  presence, policy violations, and graph blast radius. The score is the
  literal sum of documented breakdown terms; every row cites
  fingerprints and entity ids; ties break deterministically on
  fingerprint. `-f json` emits a stable shape; `--top N` truncates.
  Advisory only — it points at `fix`/`remediate`, never applies.
- **Optimization intelligence** — `forge-doctor optimize <path>`
  enumerates the optimizations the platform is eligible for
  (`core/optimize.py`): finding-backed candidates reuse the experiment
  vocabulary (`partition-data`, `add-checkpoint`,
  `increase-trigger-interval`), and observed platform versions older
  than a compatibility pack's declared targets produce upgrade
  candidates (`glue-version-upgrade`). Every candidate cites its
  evidence, gives a static cost proxy (never live numbers), a
  confidence label from the evidence planes present, and the exact
  `lab experiment`/`what-if` command that validates it. Dedupe per
  (optimization, file); no evidence, no candidates.
- **Warehouse project model** (spec 212, roadmap-4 wave 1a) —
  `analyzers/warehouse_model.py` is the vendor-neutral semantic core
  the vendor adapters populate: Terraform `snowflake_*` /
  `google_bigquery_*` / `aws_redshift*` resources and warehouse-dialect
  SQL DDL normalize to compute, database/schema, table, view, query,
  and workload-management facts. Graph adapter emits new ontology
  kinds (`warehouse`, `warehouse_compute`, `view`, `schema`) with
  `CONTAINS`/`READS_FROM`/`WRITES_TO` edges; the capability registry
  gains the `warehouse` family (`SQL_QUERY` supported; vendor-specific
  capabilities honest `unknown` until packs land). Generic `WARE###`
  checks: `WARE001` surface census, `WARE010` unprofiled table,
  `WARE020` view→unknown base, `WARE030` compute without WLM. New lab
  scenario `labs/warehouse/redshift-cluster`.
- **Snowflake adapter** (spec 213, roadmap-4 wave 1b) —
  `analyzers/snowflake_model.py` populates a vendor detail model from
  Snowflake DDL (`CREATE WAREHOUSE|DATABASE|SCHEMA|TABLE|STAGE|PIPE|
  STREAM|TASK`, plus embedded `COPY INTO`), Terraform `snowflake_*`
  resources, and observed `snowflake/` / `.forge-doctor/evidence/` /
  `information_schema*` JSON/CSV exports (unknown shapes recorded, not
  dropped). Facts merge into `WarehouseProjectModel` (deduped against
  Terraform rows) and the graph adapter emits Snowflake stages, streams,
  pipes, tasks, and principals with `CONTAINS`/`READS_FROM`/`WRITES_TO`
  edges. Query-history exports become `EvidenceKind.RUNTIME` rows.
  Checks `SNOW001`–`SNOW005` cover missing `auto_suspend`, asymmetric
  `auto_resume`, unclustered large tables, public/insecure `COPY INTO`
  stages, and `SELECT *` in persisted DDL. New command
  `forge-doctor snowflake inspect`, capability pack `snowflake.json`
  (time travel, zero-copy clone, Snowpipe, streams/tasks, clustering,
  result caching, multi-cluster warehouses), and labs
  `labs/snowflake/no-auto-suspend` + adversarial `generic-sql`.
- **BigQuery adapter** (spec 214, roadmap-4 wave 1c) —
  `analyzers/bigquery_model.py` populates a vendor detail model from
  BigQuery DDL (`CREATE SCHEMA|TABLE|VIEW|MATERIALIZED VIEW|EXTERNAL
  TABLE|RESERVATION` with `PARTITION BY`/`CLUSTER BY`/`OPTIONS(...)`),
  Terraform `google_bigquery_*`/`google_biglake_*` resources (nested
  `time_partitioning`/`clustering`/`access.view` blocks mined from the
  resource body), and observed `INFORMATION_SCHEMA` exports (tables,
  partitions, jobs-by-project — claimed only with a positive BigQuery
  field signal in shared evidence dirs). `CLUSTER BY` alone is not a
  marker (Snowflake shares it). Checks `BQ001`–`BQ005`: unpartitioned
  large tables, partition-filter-less queries (authored=warning,
  observed jobs=error), `SELECT *` cost risk, public datasets /
  undocumented authorized views, and materialized views over mutable
  bases without `max_staleness`. `BigQueryJobsAdapter` ingests
  `INFORMATION_SCHEMA.JOBS` exports as runtime evidence (bytes
  processed/billed, slot-ms). New command `forge-doctor bigquery
  inspect`, capability pack `bigquery.json` (partitioning, clustering,
  BI Engine, slots vs on-demand, time travel, BigLake, DML quotas),
  labs `labs/bigquery/unpartitioned` + adversarial `plain-sql`.
- **Redshift adapter** (spec 215, roadmap-4 wave 1d) —
  `analyzers/redshift_model.py` populates a vendor detail model from
  Redshift DDL (`CREATE TABLE ... DISTSTYLE|DISTKEY|SORTKEY|ENCODE`,
  `CREATE EXTERNAL SCHEMA|TABLE` Spectrum, materialized views,
  datashares), Terraform `aws_redshift*` resources (provisioned
  clusters, Serverless workgroups/namespaces, parameter/subnet groups —
  `parameter{}` blocks mined for WLM/`auto_analyze`), and observed
  `SVV_*`/`STL_*` exports (claimed only with a positive field signal in
  shared dirs). `VACUUM`/`ANALYZE` alone never attribute — Postgres
  shares them. Checks `RS001`–`RS005`: large EVEN/ALL-distributed
  tables under joins, unsorted tables behind range predicates, ATO
  disabled with observed skew, public/unencrypted clusters, and manual
  maintenance scripts on ATO-eligible compute. STL/SVV-derived findings
  carry `evidence_kind=observed_metadata`. `RedshiftQueryLogAdapter`
  ingests STL_QUERY exports as runtime evidence (exec + WLM queue
  times). New command `forge-doctor redshift inspect`, capability pack
  `redshift.json` (Spectrum, datashares, RA3 managed storage,
  Serverless RPU, concurrency scaling, auto MVs, dist/sort keys), labs
  `labs/redshift/public-cluster` + `skewed-even` + adversarial
  `postgres`.
- **dbt adapter** (spec 216, roadmap-4 wave 2a) —
  `analyzers/dbt_model.py` builds a `DbtProjectModel` from
  `dbt_project.yml` (required gate), `profiles.yml` (key names only —
  env-var secrets never surfaced), `schema.yml` properties files
  (sources + freshness, model/column tests, exposures), model `.sql`
  (`config(materialized=...)`, `unique_key`, `is_incremental()`,
  `ref()`/`source()`), seeds, snapshots, singular tests, macros, and the
  observed artifacts `target/manifest.json` + `run_results.json`. dbt is
  never executed — artifacts are read only. Graph integration adds
  `dbt_model` entities with `READS_FROM` (`ref`/`source`) and
  `WRITES_TO` (materialized output) edges, linking to warehouse
  entities by tail-name match when adapters 213–215 already claimed
  them. Checks `DBT001`–`DBT005`: models without tests, incremental
  models without `unique_key`, sources without freshness, declared-but-
  unused sources, and low documentation coverage. New command
  `forge-doctor dbt inspect`, labs `labs/dbt/basic-project` +
  adversarial `plain-dir`.

### Fixed

- **Lab/golden host-state leak** — scans now support `ScanOptions.hermetic`,
  hiding host state (env vars, `~/.aws`, PATH tools, ancestor git repos,
  the PATH interpreter) so lab and golden results depend only on project
  files. Fixes CI-only `AWS002` precision regressions when the runner has
  no AWS region configured; `ctx.home`/`ctx.which` route host reads, and
  hermetic git scoping still honors a scenario's own `git init`.
- **Published JSON Schemas contradicted real output** — `scan-report`
  declared `tool`/`project` as strings (actually objects) and
  `golden-snapshot` declared a single object with a `tool` key
  (actually row arrays + a bare entities/relationships object). Both
  corrected and now enforced by tests validating renderer output and
  every committed snapshot against the published schemas.
- **Delta false positive** — generic SQL DML (`MERGE`/`UPDATE`/`DELETE`/
  `OPTIMIZE`/`VACUUM`) is no longer attributed to Delta without any
  project-level delta signal; self-evident syntax (`USING DELTA`,
  `table_changes(`) still counts.
- **Airflow bare operator calls** — `SomeOperator(task_id=...)` as a
  bare expression inside `with DAG(...)` (context-manager binding) is
  now collected as a task; previously only `var = Operator(...)` assigns
  were seen, so orchestrator targets and edges were missed.
- **Migration planner crash** — `databricks-runtime-upgrade` iterated
  DBR dict keys then indexed them (`TypeError`); now iterates values
  correctly (caught by the golden corpus).

A full platform evolution: semantic fingerprints, a real plugin SDK,
policy-as-code, incremental analysis, runtime diagnosis, data intelligence,
and the integration surface (MCP/LSP/SBOM/intelligence graph) - followed
by a hardening cycle on the trust boundary, cache, and integrations.

### Fixed (hardening)

- **Plugin trust boundary** — `trusted`/identity `allow` entries gate
  *before* `ep.load()`; untrusted plugins never execute code.
  `checks.enabled`/`disabled` filter post-load. `FORGE_DOCTOR_NO_PLUGINS`
  is an env kill-switch.
- **Cache moved out of the repo** — scan cache now lives in the platform
  user cache (`%LOCALAPPDATA%`/`~/Library/Caches`/`$XDG_CACHE_HOME`,
  `FORGE_DOCTOR_CACHE_DIR` override), keyed by repo + tool + schema
  version. Auto-disabled in CI unless `--cache`. Dependency-aware
  invalidation: editing a producer module re-analyzes its importers.
- **Spark cross-file propagation** — producer functions bound across
  modules now reach a fixpoint and resolve qualified names; a Polars or
  plain-Python `df` name no longer counts as Spark evidence.
- **Baseline validation** — missing/corrupt/foreign-fingerprint-version
  baselines fail loudly (exit 2) instead of silently marking all findings
  NEW.
- **Avro nullable** — `default` no longer marks a field nullable (spec:
  only `null` unions/literals do).
- **MCP hardening** — `--root DIR` sandbox confines tool paths;
  `initialize` negotiates protocolVersion; `server/discover` alias;
  notifications get no response.
- **LSP** — workspace root (not file parent), percent/drive URI decoding,
  unsaved-buffer overlay, debounced changes, stale diagnostics cleared.
- **SBOM v2** — all locked packages (transitives included) become
  components with a real `dependencies` graph; `serialNumber` is derived
  from project name+version (path-independent); no hardcoded vendor.
- **OpenLineage** — `lineage --format openlineage` emits spec-shaped
  RunEvents (eventType/eventTime/producer/schemaURL/run.runId).
- **Action** — `install` input; the repo dogfoods its own checkout.
- **`__version__`** — single-sourced from installed dist metadata.

### Changed (hardening)

- **ScanService** (`core/service.py`) — one pipeline for CLI, MCP, and
  LSP: plugins, profile, policy/suppressions, baseline, cache.

### Changed (UX)

- **Console scan output** — categories render worst-first (errors, then
  warnings, then info-only), with per-category severity badges
  (`Sql  4 warning, 1 info`); wrapped messages/evidence/recommendations now
  align under the finding indent instead of wrapping to column 0;
  a trailing tip points at `explain <ID>` for the worst finding.
- **`checks`** prints the total + category count footer;
  **`iceberg inspect`** risks sort by severity (not lexically) and wrap
  cleanly.
- **`inspect` findings block** — the severity-sorted risk section is now
  one shared renderer (`cli/common.render_findings`) used by iceberg,
  parquet, terraform, stepfunctions, streaming, airflow and controlm —
  consistent ordering, `:line` elided when unknown, messages aligned
  under the finding indent.

### Added (0.8.0 — data intelligence)

- **DataPlatformGraph (core)** — `core/platform_graph.py`: canonical
  `Entity`/`Relationship` model (15 entity kinds, 10 relationship kinds)
  with `kind:domain:identifier` stable ids, `EvidenceKind`-tagged edges,
  cycle-safe reachability, dedup, and deterministic `to_dict()`.
- **Graph identity hardening** — canonical table identity no longer
  depends on the producing model: SQL/streaming table refs whose first
  qualifier is a catalog configured with an iceberg impl resolve to the
  same `table:iceberg:` node the iceberg adapter mints. `StreamingQuery`
  gains `source_identifier`/`sink_identifier` (literal `toTable`/`start`/
  `table`/`load`/`option` targets); unidentified endpoints mint
  `dataset:<fmt>:<fmt>` markers instead of fake table nodes.
  `platform blast-radius` now traverses edges in their *impact*
  direction (DEPENDS_ON/READS/CONSUMES/STORED_IN inbound; INVOKES/
  DEFINES/GOVERNS/TRIGGERS outbound; WRITES/PRODUCES both ways).
- **Platform graph population** — `analyzers/platform_graph_builder.py`
  fuses all seven domain models (airflow/control-m/stepfunctions/
  streaming/sql/iceberg/parquet/terraform) into the canonical graph via
  unidirectional adapters; edges carry the EvidenceKind of their source
  fact. Cross-domain joins are deterministic identity only — a Terraform
  `aws_sfn_state_machine` block and its embedded ASL definition converge
  on one `workflow:stepfunctions:` node. `forge-doctor platform graph`
  (census + `--json`) and `platform blast-radius <entity>` (reachability).
- **Evidence classification** — every finding now carries
  `evidence_kind` (`static`/`config`/`observed_metadata`/`runtime`/
  `derived`): the source plane its fact came from. Exposed in JSON
  output and `explain`; excluded from fingerprints. Built-in check
  categories are tagged; untagged plugin findings omit the field.
- **Streaming Intelligence (stage 1)** — `StreamingProjectModel`
  reconstructs Spark Structured Streaming queries from the AST index:
  stream-variable lineage (`df = spark.readStream…` → `agg = df.…` →
  `q = agg.writeStream…` merge into one logical query), source/sink
  format classification (kafka/kinesis/files/delta/iceberg/rate), output
  mode, trigger, checkpoint (literal vs dynamically-composed),
  watermark, stateful ops, `foreachBatch` sinks. New `streaming`
  category: STREAM001 anchor, STREAM002 missing checkpoint, STREAM003
  temp checkpoint path, STREAM013 shared checkpoint, STREAM014 dynamic
  checkpoint, STREAM020 stateful-without-watermark (pack-driven op
  table), STREAM070 foreachBatch. New `forge-doctor streaming inspect` +
  `knowledge/streaming/spark/stateful_ops.json`.
- **Step Functions Doctor (stage 1)** — `StepFunctionsModel` parses ASL
  offline (stdlib JSON only): `*.asl.json`/`*.states.json`/`*.sfn.json`,
  any `*.json` carrying `StartAt`+`States`, Terraform
  `aws_sfn_state_machine` definitions (quoted or heredoc), and CFN
  `AWS::StepFunctions::StateMachine` `DefinitionString`. States expose
  type/next/end/choices+default/retry/catch/resource-arn/timeout/
  heartbeat/map-mode; Map `Iterator`/`ItemProcessor` bodies model as
  nested graphs. New `stepfunctions` category: SFN000 anchor, SFN002
  unreachable state, SFN003 dead-end path, SFN005 Choice without
  Default, SFN010 sync/callback task without TimeoutSeconds, SFN020
  Distributed Map inside an EXPRESS machine. New
  `forge-doctor stepfunctions inspect` +
  `knowledge/stepfunctions/integrations.json` (service-integration arn
  map with sync/callback support flags).
- **Parquet Doctor (stage 1)** — `ParquetProjectModel` fuses AST-index
  evidence (`read.parquet`/`write.parquet`/`format("parquet")`/
  `option("compression",…)`/`spark.sql.parquet.*`) with on-disk
  `*.parquet` stats (count, total, median, p95 — `stat` sizes only).
  New `parquet` category: PARQ000 anchor, PARQ010 repartition-before-write,
  PARQ020 uncompressed write, PARQ021 mixed codecs, PARQ040/041/042
  dataset-level small-file/excessive-count/size-skew (thresholds from
  `knowledge/parquet/format.json`). New `forge-doctor parquet inspect`.
- **Terraform Doctor (stage 1)** — `TerraformProjectModel` extends
  `hcl_lite` to a full block-level model: `terraform` internals
  (`required_version`, `required_providers`, `backend`), providers with
  aliases, modules (local/registry/git), resources, data, variables,
  outputs, `moved`/`import`/`check` blocks, and a reference graph between
  addresses (`aws_iam_role.glue`, `module.vpc`, `var.env`). New `terraform`
  category: TF000 anchor, TF001 missing required_version, TF002
  unconstrained provider, TF003 unbounded constraint, TF020 local module,
  TF021 registry module unpinned, TF022 mutable git ref, TF130 local
  backend. New `forge-doctor terraform inspect` + `knowledge/terraform/`
  packs (language feature floors, provider source map).
- **Airflow Doctor (stage 1)** — `AirflowProjectModel`: index-flagged
  `airflow*` files get one targeted AST pass — `with DAG(...)`/`@dag`/
  `DAG()` definitions, `*Operator`/`*Sensor`/`@task` tasks, `>>`/`<<`/
  `set_*`/`chain`/`cross_downstream` edges, TaskFlow wiring by call,
  provider imports, and parse-time external calls. New `airflow` category:
  AIR000 anchor, AIR002 duplicate dag_id, AIR003 empty DAG, AIR004 orphan
  task, AIR013 dynamic start_date, AIR021/AIR025 parse-time external +
  `Variable.get` calls, AIR040/AIR042 sensor poke-mode/timeout, AIR100
  retries without delay, AIR130 undeclared provider. New
  `forge-doctor airflow inspect` + `knowledge/airflow/` and
  `errors/airflow` packs (feeds `diagnose`).
- **Control-M Doctor (stage 1)** — `ControlMModel` parses Automation API
  JSON definitions (folders, jobs, events, calendars, site standards,
  `Defaults` inheritance) plus `ctm` CLI and `automation-api` references —
  stdlib-only, fully offline. New `controlm` category: CTM000 anchor,
  CTM002 no execution target, CTM003 duplicate names, CTM004 never-firing
  job, CTM009/CTM010 event produce/consume mismatches, CTM028 undefined
  calendar, CTM051 missing required metadata, CTM070 credential literals
  (names only — values never emitted). New `forge-doctor controlm inspect`
  command group + `knowledge/controlm/` and `knowledge/errors/controlm`
  packs (the latter feeds `diagnose`).
- **Iceberg Doctor** — `IcebergProjectModel` fuses evidence from the AST
  index (catalog configs, `USING iceberg`, `writeTo` chains), `SqlIndex`
  (CREATE USING/TBLPROPERTIES/MERGE/CALL maintenance), `spark-defaults.conf`/
  `*.properties`, and IaC (Glue version pins, S3 Tables). New `iceberg`
  category: ICE000 anchor, ICE001 format-version vs ops, ICE002 unpartitioned
  MERGE, ICE008-010 maintenance gaps, ICE012 catalog conflicts, ICE013 Glue
  runtime compat. Stage 2 adds MERGE reconstruction (`merge_detail`/`merge_on`
  evidence — target, source, target-side ON columns), partition-transform
  specs (`PARTITIONED BY` parsed to `partition.<col>` evidence), write-API
  classification (`write_api` v2/legacy/legacy_catalog), catalog-impl typing,
  and small-file write patterns: ICE020 legacy writer API, ICE021 insertInto
  on a cataloged table, ICE022 row-level ops without
  `IcebergSparkSessionExtensions`, ICE023 MERGE ON-cols missing partition
  columns, ICE024 repartition/coalesce-before-write, ICE025 feature-vs-format
  floors (`merge-on-read` needs v2, deletion-vectors/row-lineage need v3 —
  driven by `knowledge/iceberg/spec.json`). New `forge-doctor iceberg
  merge|files` subcommands, plus the existing `iceberg inspect|maintenance|
  compatibility` group and `knowledge/iceberg/` packs.
- **SQL first-class** — optional `[sql]` extra (sqlglot): `SqlIndex` parses
  every `*.sql` file and every `*.sql("...")` literal found by the semantic
  index into statement facts (reads/writes/projection/joins/dialect).
  New `sql` category: SQL000 surface anchor, SQL001 `SELECT *`, SQL002
  cartesian/comma joins, SQL003 non-sargable predicates. Unparseable
  statements are counted, never fatal; without the extra the category is
  absent and `explain SQL###` points at `forge-doctor[sql]`.
- **Lake Formation pack + diagnose correlation** — `knowledge/lakeformation/`
  covers credential vending (GetTemporaryCredentialsForTableV2), FGAC,
  resource links, RAM/cross-account, hybrid access, IAMAllowedPrincipals,
  LF-tags, DATA_LOCATION_ACCESS, and registered-location requirements; each
  entry carries signature patterns + causes + fix hints. `diagnose` now
  accepts `--path` and, when a vending-family signature matches and repo
  evidence shows Glue >=5.x + LF/FGAC config + a write op, appends the
  vending/write-path conflict correlation line. Error signatures gained
  optional `fixes`/`families` fields. New `lakeformation` category: LF000
  anchor, LF001 resource link without RAM share, LF002 IAMAllowedPrincipals
  alongside FGAC tags (hybrid-access ambiguity).
- **Capability Engine** (`core/capabilities.py`) — a versioned platform
  capability registry so checks stop hardcoding service facts.
  `CapabilityStatus` is a four-state answer (SUPPORTED / UNSUPPORTED /
  CONDITIONAL / UNKNOWN): uncovered versions and platforms resolve to
  UNKNOWN — absence of proof is never proof of absence. Facts live in
  `knowledge/capabilities/*.json` (schema 2, per-entry `source`,
  `when`-gated variants, `versions` maps, attribute `conditions`); packs
  missing sources are rejected at load and surfaced via
  `registry.validation_issues`. Seeds cover Glue↔Iceberg row-level ops,
  Iceberg format-version gating, DynamoDB (transactions, streams, GSI/LSI,
  global-table MREC/MRSC semantics), Neptune (Gremlin/openCypher/SPARQL,
  bulk loader, explain/profile, global database, paradigm-vs-language
  incompatibility), and paradigm-level graph capabilities. Checks reach it
  via `ctx.capabilities`; CLI: `forge-doctor capabilities list|explain`
  (`--json`, `--version`, `--variant`, `--attr`). ICE001 now consults the
  registry for its format-version floor — first migrated check.
- **Graph Intelligence** (`analyzers/graph_model.py` +
  `analyzers/graph_queries.py`) — paradigm-aware graph model: property
  graph (Gremlin/openCypher) is never conflated with RDF (SPARQL);
  paradigms land as `property_graph`/`rdf`/`unknown` from evidence.
  Static shape extractors cover `.cypher`/`.cql`/`.sparql`/`.rq` files,
  `.gremlin` scripts, Neptune bulk-load CSV headers, RDF data files, and
  Python call sites (`g.V()` chains via the dotted-call index, query
  strings handed to client methods) — nothing is executed, unparsed or
  dynamic queries degrade to `parsed=False` honestly. Traversals record
  start selectivity, steps, directions, hop counts/bounds, filters and
  position, projection size, writes, vertex/edge labels, properties, and
  edge endpoints. New `graph` category: GRAPH001 anchor, GRAPH002
  disconnected components, GRAPH003 orphan vertex, GRAPH004 edge with
  undefined endpoint type, GRAPH005 direction inconsistency, GRAPH006
  redundant relationship modeling, GRAPH007 generic edge label,
  GRAPH008/009 INFO-gated property fan-out and supernode candidates,
  GRAPH010 relational-shape artifacts, GRAPH020–025 traversal-shape
  family (unselective start, unbounded result, unbounded variable
  length, high fan-out, late filtering, repeated pattern), plus local
  extensions GRAPH026 (full-graph starts) and GRAPH030 (mixed paradigms).
  `knowledge/graph/` packs (property-graph, rdf, modeling, algorithms;
  schema 2 + sources). CLI: `forge-doctor graph inspect|schema|
  traversals`; the prior project-intelligence dump stays reachable as
  `forge-doctor graph <path>` (unchanged) and `graph project`.
- **DynamoDB Intelligence** (`analyzers/dynamodb_model.py`) — tables
  from Terraform `aws_dynamodb_table` (nested gsi/lsi/replica/ttl/pitr/
  encryption blocks), CloudFormation `AWS::DynamoDB::*` (incl.
  `MultiRegionConsistency` MRSC detection), and boto3 bindings in code;
  access operations extracted at the AST level so keyword presence is
  exact (`KeyConditionExpression`, `FilterExpression`,
  `ProjectionExpression`, `ConsistentRead`, `IndexName`, `TableName`),
  with Key-dict literals and `PREFIX#{id}` f-string patterns decoded.
  Streams carry view type + consumers (event-source mappings) +
  idempotency signals; global tables carry mode (mrec default / mrsc)
  + regions; single-table entity prefixes are reconstructed from
  `PREFIX#` key conventions. New `dynamodb` category: DDB001 anchor,
  DDB002-010 access-pattern family (scan on latency path, unfiltered
  scan, poor-cardinality/hot/constant PKs, time-only SK, GSI duplicates
  /hot keys/count-vs-usage), DDBSTR001-005 streams family (no consumer,
  no idempotency, duplicate consumers, recovery window, replicated
  global events), DDBGT001/002/005 multi-region signals and DDBGT003 —
  transactions on MRSC resolve UNSUPPORTED via the capability registry
  (ERROR). All static-risk framing; single-table structure reported,
  never recommended. `knowledge/dynamodb/` packs (indexes,
  transactions, limits, modeling, streams, global-tables; schema 2 +
  sources). CLI: `forge-doctor dynamodb inspect|access-patterns|
  indexes|streams|global-tables|capacity`.
- **Neptune Intelligence** (`analyzers/neptune_model.py`,
  `neptune_queries.py`, `neptune_explain.py`) — `NeptuneProjectModel`
  separates Neptune Database from Neptune Analytics; clusters, instances,
  subnet/parameter groups, global clusters from Terraform +
  CloudFormation; endpoints, `neptunedata`/`neptune-graph` bindings,
  `start_loader_job` calls, IAM-auth hints from code. Query shapes reuse
  the Graph Intelligence extractors (no parsers duplicated). New
  `neptune` category: NEP001 anchor, NEP010 language↔paradigm
  incompatibility via the capability registry (DERIVED + source),
  NEP020-024 traversal checks per language, NEP030-033 ingestion checks,
  NEP040-045 infra/topology checks, NEPGT001-003 global-database checks,
  NEPA001-002 analytics checks, NEPCD001 stream-fed mutation
  idempotency. `neptune explain <file>` classifies exported
  explain/profile artifacts STATIC/OBSERVED_METADATA/RUNTIME and flags
  large intermediates, broad starts, late filters — offline only.
  `knowledge/neptune/` packs (products, engines, ingestion, features,
  query-languages, bulk-loader, global-database, explain, analytics,
  compatibility; schema 2 + sources). CLI: `forge-doctor neptune
  inspect|schema|queries|ingest|explain|analyze-explain|compatibility`;
  `forge-doctor data-model inspect` reports the access-style breakdown
  as facts only. Platform graph gained DynamoDB adapters (table
  PRODUCES stream, stream TRIGGERS lambda, code READS/WRITES table) and
  Neptune adapters (`graph:neptune:<cluster>`, loader READS S3 / WRITES
  graph, lambda WRITES via handler-module join) — `blast-radius` spans
  Terraform → DynamoDB → stream → Lambda → Neptune.
- **Cross-Domain Rule Engine** (`core/crossdomain.py` +
  `checks/platform_rules.py`) — PLAT### findings derived from multiple
  semantic models, canonical-graph edges, and the capability registry.
  Each rule declares the entity/relationship/capability prerequisites it
  needs and fires only when all are present (no fuzzy joins); findings
  list their contributing facts. First rules: PLAT001 retrying
  orchestration task + append-only sink, PLAT002 runtime/config
  capability incompatibility, PLAT003 continuous writer + maintenance
  gap, PLAT004 duplicate orchestration ownership, PLAT005 IaC runtime vs
  source assumptions, PLAT006 table-format/consumer mismatch, PLAT007
  microbatch side-effect idempotency risk. `AirflowTask`/`AirflowDag`
  gained `target`/`default_retries`; the Airflow adapter now emits
  task→compute-job INVOKES edges for orchestrating operators
  (GlueJobOperator, LambdaInvoke*, StepFunction*, EMR, Databricks).
  CLI: `forge-doctor platform findings` (also runs inside `scan` under
  the `platform` category).
- **Runtime Evidence layer** — `core/runtime_evidence.py` normalizes
  exported artifacts into `RuntimeEvidenceModel` (executions, metrics,
  errors, timings, throughput, lag, retries, resource usage, state,
  identifiers); `analyzers/runtime_evidence.py` ships seven offline
  adapters (Spark event log, Structured Streaming progress, Athena
  stats, Lambda REPORT, Step Functions history, Glue logs, Neptune
  explain/profile) behind ordered first-match dispatch. Identity joins
  use demonstrable keys only (ARN/job/query/execution id). CLI:
  `runtime inspect|diagnose <artifact>` and `streaming progress <file>`.
- **Finding promotion + root-cause clustering** — `core/diagnosis.py`
  correlates scan findings with runtime evidence. `FindingPromotion`
  keeps `base_fingerprint` correlation (originals never mutate) and a
  deterministic `promotion_id`; levels CONFIRMED (requires exact
  identity join), STRONGLY_SUPPORTED (targeted rule, domain-only), and
  POSSIBLE (shared-domain errors — corroboration, never confirmation).
  `FindingCluster` evaluates deterministic causal chains — micro-batch →
  commit amplification → small files → consumer overhead, and
  join/shuffle key → skew → spill → long stage — emitting root causes,
  symptoms, related findings, affected entities, evidence, and causal
  edges. CLI: `forge-doctor root-cause . --runtime artifact.json`
  (`--json` supported).
- **Deterministic remediation planning** — `core/remediation.py` maps
  findings and root-cause clusters to ordered `RemediationPlan`s from
  the new `knowledge/remediation/` packs (spark, parquet, iceberg,
  streaming, platform families + the RC_* chains; schema-2 provenance).
  Plans are advisory only — what/where/why/how-to-validate; nothing is
  patched, committed, applied, or deployed. CLI:
  `forge-doctor remediate . [--root-cause <id>]` (`--json`).
- **Architecture contract + drift** — `core/contract.py` parses a
  versioned `platform-contract.yml` (pipelines with compute platform/
  version, storage format, orchestration, SLA, semantics, ownership,
  approved capabilities; governance.allowed_dependencies). `pyyaml` when
  installed, else a strict minimal parser for the documented shape.
  `detect_drift` compares desired vs declared/implemented/runtime planes
  and emits ARCH001-008 drift (platform mismatch, version drift, format
  drift, undeclared dependency, SLA violation via identity join,
  idempotency-without-evidence, multi-owner resources, features outside
  approved capabilities). ARCH### also run as scan checks when a
  contract exists. CLI: `contract validate <file>` and
  `architecture drift . [--runtime artifact]`.
- **Lake Formation deep intelligence** — `analyzers/lakeformation_model.py`
  builds a `LakeFormationProjectModel` (principals, admins, grants,
  databases/tables/columns, data locations, resource links, LF tags,
  data-cells filters, RAM shares, IAM `lakeformation:` policy actions,
  boto3 `grant_permissions`/`register_resource`/`create_database`
  TargetDatabase call-sites) with nested-block HCL extraction and
  producer/consumer cross-account resolution. Checks LF010-LF018 cover
  missing data-lake settings, `IAMAllowedPrincipals` defaults, dangling
  resource links, external grants without RAM, unregistered data
  locations, LF-TBAC coverage, unused filters, hybrid overlap, and
  grant-option escalation. FGAC/FTA capability facts live in
  `knowledge/capabilities/lakeformation.json`. Platform graph gains
  principal → GOVERNS edges and resource-link DEPENDS_ON edges. CLI:
  `lakeformation inspect|permissions|graph|cross-account|compatibility|findings`.
- **EMR + Databricks + Delta deep intelligence** — first-class models
  (`analyzers/emr_model.py`, `databricks_model.py`, `delta_model.py`)
  built from Terraform/CloudFormation/boto3/code evidence, offline only.
  EMR splits EC2 clusters (release label, fleets, spot/on-demand,
  autoscaling/dynamic allocation, roles, bootstrap, logging, security
  config, step failure actions), EMR Serverless applications (release,
  engine, capacity caps, auto-stop), and EMR on EKS virtual clusters.
  Databricks covers jobs (task counts, job vs existing clusters,
  notebook/pipeline tasks), clusters (DBR version, autoscale, spot,
  serverless), SQL warehouses, pipelines, Unity Catalog objects
  (catalog/schema/external location/storage credential/volume), asset
  bundles, and sdk/dbutils/notebook evidence. Delta captures tables,
  ops (MERGE/UPDATE/DELETE/OPTIMIZE/VACUUM/RESTORE/CLUSTER BY),
  table features (deletion vectors, CDF, liquid clustering, column
  mapping, schema evolution, identity columns), reader/writer
  protocol floors, read/write/streaming counts, and CDF consumers.
  Checks: EMR000-007, DBX000-006, DELTA000-004. Cross-domain rules:
  PLAT008 (EMR Iceberg writes under Lake Formation without an
  LF-integrated security configuration) and PLAT009 (Databricks
  runtime below a detected Delta feature's protocol floor) —
  capability-gated, fact-attributed. The platform graph gains
  `compute_job:emr|databricks`, UC catalog/location/principal, and
  `table:delta` entities with per-op WRITES edges. New packs:
  `knowledge/capabilities/{emr,databricks,delta}.json`,
  `knowledge/{emr/releases,databricks/runtime,delta/features}.json`.
  CLI: `emr|databricks|delta inspect|findings`, `delta features`.
- **Athena + Lambda + Step Functions deep intelligence** —
  `analyzers/athena_model.py` (workgroups with engine version,
  enforced result location, bytes-scanned cutoff, encryption;
  catalogs, databases, named/prepared queries; CTAS/UNLOAD/PREPARE
  SQL ops; Iceberg DDL; boto3 `athena` call-sites) and
  `analyzers/lambda_model.py` (functions with runtime/arch/memory/
  timeout/ephemeral storage/reserved+provisioned concurrency/layers/
  VPC/DLQ/tracing; event sources incl. stream/sqs/s3/sns/schedule
  kinds with TF-ref + ARN resolution; invoke-config destinations;
  layer versions; boto3 `lambda` calls; idempotency-library imports).
  `StepFunctionsModel` deepened: per-machine `QueryLanguage`
  (JSONPath/JSONata), per-state payload keys, retry `MaxAttempts` +
  `ErrorEquals` sets, Lambda `target` extraction
  (`Parameters.FunctionName`), and Distributed Map
  `MaxConcurrency`/`ToleratedFailurePercentage`. Checks: ATH000-005,
  LAM000-005, SFN030-032. Cross-domain: PLAT010 (SFN + client-side
  Athena poller → `.sync` candidate) and PLAT011 (Distributed Map
  `MaxConcurrency` > the invoked Lambda's reserved concurrency).
  Platform graph gains `compute_job:athena`, `query:athena`,
  `catalog:athena`, stream→lambda TRIGGERS edges, and
  function→destination INVOKES edges. Packs:
  `capabilities/{athena,lambda}.json`, `athena/engines.json`,
  `lambda/runtimes.json`, `stepfunctions/query-languages.json`.
  CLI: `athena|lambda inspect|findings`; `stepfunctions inspect`
  shows query language, retry attempts, map detail, payload keys.
- **Streaming runtime + Kafka/Kinesis/Flink deep intelligence** —
  `analyzers/kafka_model.py` (MSK provisioned/serverless clusters with
  encryption-in-transit, client auth, broker counts; topics with
  partitions/replication/config keys; SS `subscribe`/`startingOffsets`/
  `maxOffsetsPerTrigger`/`failOnDataLoss`/`kafka.group.id` options;
  `KafkaConsumer`/`KafkaProducer`/`SchemaRegistryClient` call sites;
  consumer groups; schema-registry and TLS/SASL presence),
  `analyzers/kinesis_model.py` (streams with shards/stream_mode/
  retention/encryption; EFO consumers; Firehose; managed-Flink
  KinesisAnalyticsV2 apps; SS kinesis options; boto3 `kinesis` calls
  with StreamName/Consumer literals), `analyzers/flink_model.py`
  (StreamExecutionEnvironment jobs; sources; keyed state; windows;
  timers; checkpointing + `CheckpointingMode`; savepoints; parallelism;
  sinks; managed apps). `core/delivery.py` derives delivery semantics
  (`at-most-once`/`at-least-once`/`effectively-once`/
  `exactly-once-claim`/`unknown`) from source+checkpoint+engine+sink+
  idempotency — a checkpoint alone never claims exactly-once.
  `analyzers/streaming_runtime.py` diagnoses progress batch series:
  SRATE001 rate imbalance, SSTATE002 state growth, SWM003 watermark
  lag, SCKPT004 commit instability, SKFK005 source offset backlog,
  SDUR006 slow batches. Checks: KFK000-006, KIN000-003, FLK000-004,
  STREAM080 (derived semantics per query). Runtime adapters:
  `flink_checkpoints` (checkpoint history + `CheckpointFailed` errors)
  and `stream_metrics` (`MillisBehindLatest`/records-lag exports).
  Platform graph: `stream:kafka:*`, `stream:kinesis:*`,
  `stream:firehose:*`, `compute_job:flink:*`, `principal:kafka:group:*`,
  `principal:kinesis:*` + EFO→stream CONSUMES edges. Packs:
  `streaming/delivery`, `kafka/config`, `kinesis/config`,
  `flink/config`, `capabilities/{kafka,kinesis,flink}`. CLI:
  `kafka|kinesis|flink inspect|findings`, `streaming diagnose`,
  `streaming semantics`.
- **What-if + migration planning** — `core/whatif.py`
  (`WhatIfChange`/`evaluate_change`): `--change target=value`
  (`glue-version`, `iceberg-format-version`, `databricks-runtime`,
  `lambda-runtime`, `emr-release`) evaluates affected graph entities,
  capability status transitions (lost caps = blockers, gained =
  enablers), domain-pack compatibility notes, contract version-pin
  conflicts, and honest `unknown` entries where packs lack facts.
  `core/migration.py` (`MigrationPlan`, `plan_migrations`): named
  advisory paths — `glue-4-to-5`, `iceberg-v1-to-v2`,
  `databricks-runtime-upgrade`, `parquet-to-delta`,
  `parquet-to-iceberg`, `streaming-modernize`,
  `lambda-runtime-upgrade` — each carrying affected entities,
  blockers, warnings, required changes, validation steps, and
  rollback considerations. Plans are generated, never executed;
  insufficient pack facts surface as UNKNOWN. CLI: `what-if
  --change ... [--assume fact]`, `migrate plan`.

### Added (0.7.0)

- **Fingerprint v3** — semantic identity `check_id|file|symbol|anchor` from
  the AST: findings survive line moves and message rewording. Baseline format
  2 with strict `fingerprint_version` validation — an incompatible baseline
  fails loudly instead of flipping every finding to NEW.
- **Contract v3** — JSON/agent output carries
  `{tool:{name,version}, schema_version:"3.0", project:{name}}`; absolute
  project root is opt-in via `--show-root`. `jsonl` format emits one finding
  per line.
- **Single-scan `--emit FMT[:PATH]`** — repeatable; one analysis renders to
  many targets (stdout + SARIF + HTML in one pass). The composite action now
  produces text + SARIF from a single scan.
- **Plugin SDK v2** — `PluginIdentity(distribution, version, api_version,
  entry_point)` stamped on loaded checks; allowlist matches check id,
  distribution, or entry point (never class names). `plugins list|validate|
  doctor` inspect health.
- **Policy-as-code** — `[tool.forge-doctor.policy] extends/rules` and
  `[[tool.forge-doctor.suppressions]]` (scoped, owned, expiring). Expired
  suppressions reactivate findings and emit `POLICY001`; `suppressions`
  audits ACTIVE/EXPIRED/UNUSED with match counts.
- **Semantic index** — one `ast.parse` per file powers Spark and Glue
  checks; cross-file producer propagation (`from reader import load_orders`).
- **Incremental cache** — the user cache dir (never the scanned repo)
  persists per-file sha256 → analyzer facts with dependency provenance;
  `--cache/--no-cache`, `forge-doctor cache` stats + `cache clean`; off by
  default in CI. `--watch` uses `watchfiles` when the extra is installed and
  snapshot polling otherwise. `--stats` prints per-check timings and cache
  hit rate.
- **`forge-doctor trace ID FILE:LINE`** — explains one finding: evidence,
  enclosing symbol, receiver classification, assignment chain, imports;
  `--json` for agents.
- **`forge-doctor diagnose FILE|-`** — deterministic log fingerprinting via
  `knowledge/errors/` packs (spark, glue, iceberg, lakeformation, databricks,
  python); substring + `re:` regex patterns, occurrence counts, causes.
- **Spark runtime doctor** — `spark eventlog` (executor loss, task skew,
  shuffle spill, GC pressure, single-task stages, retries, scheduler delay),
  `spark plan` (cartesian products, BNLJ, single-partition exchanges, global
  sorts, join-strategy mix), `spark logs` (error packs + runtime patterns).
- **Static lineage** — `forge-doctor lineage` detects `spark.read.*`,
  `read.format().load()`, `spark.sql` FROM/JOIN/INSERT, `saveAsTable`,
  `insertInto`, `writeTo`, `write.<fmt>()`, Glue `from_catalog`; renders
  text/json/dot/mermaid plus an OpenLineage-shaped `--format openlineage`.
- **`forge-doctor schema diff`** — Avro, JSON Schema, SQL DDL, dbt
  `schema.yml` (optional `pyyaml`); classifies added/dropped/type/nullability
  changes and rename candidates; works on files or `base...head` git ranges.
- **IaC checks (IAC001–004)** — dependency-free Terraform/HCL-lite and
  CloudFormation (JSON + mined YAML) parsing: Glue version aging, worker
  sanity, CFN Glue/Lambda/EMR runtimes. IaC pins feed `compatibility`.
- **`forge-doctor migrate glue`** — combines knowledge-pack version deltas
  with real project signals (code pins, DynamicFrame usage, dependencies,
  IaC) grouped Runtime/Code/Dependencies/Infrastructure.
- **Workspace orchestration** — `workspace` (discovery), `workspace scan`
  (aggregate, per-project prefix, text/json/sarif), `workspace diff
  base...head` (per-subproject added/fixed counts).
- **Knowledge provenance** — packs carry `schema_version: 2` +
  `pack_version`/`verified_at`/`sources`; `knowledge list|info|verify`
  (stale > 90d flagged).
- **`forge-doctor sbom`** — CycloneDX 1.5: project deps, plugins, knowledge
  packs, the tool itself.
- **`forge-doctor mcp`** — zero-dependency JSON-RPC stdio server:
  `initialize`, `tools/list`+`tools/call` (scan_project, explain_rule,
  check_compatibility, get_lineage, diagnose_log, diff_findings),
  `resources/list`+`resources/read` (`forge-doctor://rules/ID`,
  `forge-doctor://knowledge/DOMAIN/NAME`), `ping`, notifications tolerated.
- **`forge-doctor lsp`** — optional `pygls`-based stdio server mapping
  findings to `publishDiagnostics` (severity, `source: forge-doctor`, check
  id code) on open/change/save; mapping logic is unit-tested without pygls.
- **`forge-doctor graph`** — Project Intelligence Graph: repo, job, dataset,
  IaC, orchestrator nodes with contains/reads/writes/deploys/triggers edges;
  json/dot/mermaid.
- **`forge-doctor doctor`** — environment health: git, config validity,
  plugin status, cache dir writability, knowledge pack freshness.
- **Exact-duplicate dedup** in the runner; per-check timing instrumentation.

### Changed

- `forge_doctor/cli.py` is now the `forge_doctor/cli/` package
  (`app`/`common`/`scan`/`diff`/`workspace`/`compatibility`/`plugins`/`misc`);
  the `forge_doctor.cli:app` entry point is unchanged.
- `workspace`/`spark`/`cache`/`plugins`/`knowledge`/`schema`/`migrate` are
  command groups; category scans keep the same flags.
- Poetry >= 2.2 required; extras: `watch` (watchfiles), `lsp` (pygls +
  lsprotocol), `schemas` (pyyaml).
- CI: quality matrix on 3.11–3.13 preserved; added cross-platform smoke
  (Ubuntu/Windows/macOS wheel install + scan) and a dogfood job that runs
  the composite action on this repo.

## [0.3.0] - 2026-09-30

### Added

- **Finding Model v2**: `CheckResult` gains `confidence`, `fingerprint`
  (auto-derived stable identity), `evidence` (triggering source line),
  `tags`, `docs_uri`, `source` (plugin distribution), `fixable`, and
  `column`/`end_line`/`end_column`. All serialize in JSON when set.
- `--format sarif` — SARIF 2.1.0 for GitHub Code Scanning: rules with
  tags, locations with evidence snippets, `partialFingerprints`, `fixes`.
- `--format agent` — compact `{id, sev, loc, fp}` bundle for agent
  consumers; `explain <ID> --json` exposes rule metadata on demand.
- `forge-doctor diff` — compares findings by fingerprint across two saved
  reports, a report vs a git ref, or a `base...head` range (scanned in a
  temporary detached worktree); exit 1 when new findings exist.
- `forge-doctor compatibility` — detects Glue/Spark/Python/Java/Iceberg
  environment and prints migration risks (`--from`/`--to`).
- `forge-doctor workspace` — discovers nested `pyproject.toml` projects.
- **Knowledge packs** under `forge_doctor/knowledge/`: Glue version status
  through **Glue 6.0** (Spark 4.1.1, Python 3.13, Java 17, Iceberg 1.11.0),
  Glue runtime map, Glue/Python compatibility — shipped inside the wheel.
- **Profiles** (`--profile`): `default`, `strict`, `security`,
  `spark-performance`, `glue-migration`, `production`.
- `--new-only` (requires `--baseline`), `--files` filtering (pre-commit),
  `--no-plugins`, and `[tool.forge-doctor.plugins].allow` trust list.
- Spark AST v2: alias and symbol tracking (`import ... as`,
  `df = spark.read...`, chained-call receivers) — Spark findings now carry
  receiver `confidence`; SPARK006 pairs `unpersist()` per variable.
- New Spark checks: SPARK008 `.rdd` access, SPARK009 Cartesian joins,
  SPARK010 global sort, SPARK011 `withColumn` in loops.
- `action.yml` composite GitHub Action (scan + SARIF upload);
  `.pre-commit-hooks.yaml` for pre-commit consumers.
- Release workflow: PyPI Trusted Publishing (OIDC) + PEP 740 attestations
  (disabled until the PyPI project exists).

### Fixed

- `--output` now writes json/sarif/agent payloads to a file (previously
  always printed to stdout).
- Chained-call receivers (`orders.filter().collect()`) resolve to their
  root DataFrame — `confidence` now reaches `high` as intended.
- `--check` with only unknown categories exits 2 with the valid list;
  mixed valid/unknown warns but runs. `--fail-on` validated up front.
- CI002 grades refs honestly: full SHA quiet, version tag INFO, floating
  ref/no-@ WARNING (`security` profile escalates tags to warnings).
- `.tokensave/` removed from git index; `DataDoctorConfig` fully renamed
  to `ForgeDoctorConfig` (old name kept as deprecated alias).
- GLUE002 no longer recommends "4.0/5.x" — knowledge pack drives status
  and the `compatibility` command provides migration guidance.

## [0.2.0] - 2026-09-29

### Added

- `forge-doctor explain <CHECK_ID>` — renders a rule's why/when-OK/fix from
  code-level attributes (`CheckBase.why`, `.when_ok`, `.fix`) populated for
  all built-in checks; works for plugin checks too.
- New categories: **docker** (DOCKER001-004 — unpinned `FROM`, missing
  `USER`, secret-looking `ENV`/`ARG` names), **glue** (GLUE001-004 — AST:
  awsglue usage, EOL runtimes, `getResolvedOptions`, DynamicFrame mixing),
  **ci** (CI001-004 — unpinned actions, python-version, test/lint steps).
- `examples/plugin/` — minimal installable plugin (`forge-doctor-example`)
  + `docs/plugins.md` SDK guide.
- Console output degrades non-UTF glyphs to ASCII on legacy encodings
  (Windows cp1252).
- `forge-doctor init` — scaffolds a PEP 621/Poetry project (`--name`,
  `--force`); never overwrites existing files unless forced.
- `forge-doctor info` — instant project stats: files, lines, top types,
  git state, detected tooling (no checks run).
- Baselines: `--save-baseline PATH` records a scan, `--baseline PATH`
  diffs against it — findings render a `NEW` marker, JSON gains
  `is_new`/`baseline`, and `--fail-on` only counts **new** findings so
  pre-existing debt does not break CI.
- `--format html` — self-contained shareable report (`--output PATH`,
  defaults to `forge-doctor-report.html`).
- `--output/-o` — also writes text reports to a file.
- `--watch/-w` — re-scans whenever project files change (polling, Ctrl+C
  exits with the last scan's code).
- `--no-color` flag (the `NO_COLOR` env var was already honored by Rich).
- Console redesign: header panel (project/version/check count),
  spinner while scanning, grouped categories, color-coded summary panel
  with baseline diff, Rich tables for `checks`/`plugins`/`explain`.
- Shell completion via `forge-doctor --install-completion`.

### Fixed

- `git ls-files` paths are now scoped/stripped to the scanned root
  (subdirectory scans reported repo-root-relative paths).
- subprocess probes use `errors="replace"` — non-ASCII output no longer
  crashes the git checks on cp1252 consoles.
- `file` fields render POSIX separators in JSON and console output —
  the contract is now identical across platforms.
- DEP005 only runs `poetry check --lock` on Poetry-managed projects.
- `--format` is validated before the scan runs.
- Console no longer duplicates a file location equal to the rule title.
- `.pytest_tmp` added to default traversal exclusions; `aws --version`
  probe timeout raised to 15s.

### Changed

- Renamed the project `data-doctor` → `forge-doctor` (module, CLI, PyPI name,
  `[tool.forge-doctor]` config section, `forge_doctor.checks` entry-point
  group) to avoid the `vision-data-doctor` CLI collision and pair with the
  future Spark Forge ecosystem.
- DEP005 now runs `poetry check --lock` when Poetry is on PATH (definitive
  PASS/WARNING) and falls back to the mtime heuristic (INFO) otherwise.
- DEP006 poetry probe timeout raised to 30s — the pipx shim cold-starts
  slowly on Windows and previously produced a false "failed" INFO.

## [0.1.0] - 2026-09-28

### Added

- `forge-doctor scan` engine: layered architecture (CLI → runner → checks →
  analyzers → context → renderers) with stable check ids.
- 6 check categories, 35 rules: repository (REP), python (PY),
  dependencies/poetry (DEP), git (GIT), spark AST analysis (SPARK), local AWS
  config (AWS).
- Rich console output, stable `--format json` contract, `--quiet`,
  `--ignore`, `--check`, `--fail-on`, `--verbose`.
- `[tool.forge-doctor]` configuration with `exclude` globs and `ignore` lists.
- Plugin discovery via the `forge_doctor.checks` entry-point group.
- `plugins`, `checks`, `version` commands; `--version` flag.
- Exit codes: 0 clean, 1 errors, 2 internal.
- CI (lint/format/type/test/build matrix) and manual release workflow.
