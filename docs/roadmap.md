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
