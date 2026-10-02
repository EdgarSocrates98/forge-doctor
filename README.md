<p align="center">
  <img src="docs/assets/logo.png" alt="Forge Doctor" width="440">
</p>

# Forge Doctor

Deterministic diagnostics for data engineering projects. One command inspects
your repository, Python environment, packaging, dependencies, git hygiene,
PySpark code, and local AWS setup — no daemons, no network calls, no AI.

## Why it exists

Data projects drift: a `collect()` slips into a hot path, `requirements.txt`
and `poetry.lock` start coexisting, a `.env` gets committed, the Python in the
venv no longer matches `requires-python`. These failures are cheap to catch and
expensive to debug. Forge Doctor is a fast, offline, deterministic sensor that
surfaces them with file:line precision — and explains when each pattern is
actually fine.

## Installation

```bash
pipx install forge-doctor
```

New here? Walk through [docs/getting-started.md](docs/getting-started.md) —
install to a gated CI scan in five steps.

Or from source:

```bash
git clone https://github.com/EdgarSocrates98/forge-doctor
cd forge-doctor
pipx install .
```

## Usage

```bash
forge-doctor scan .                 # full scan
forge-doctor scan . --check spark   # one category
forge-doctor scan . --ignore SPARK001
forge-doctor scan . --format json   # stable machine-readable contract
forge-doctor scan . --format html   # self-contained shareable report
forge-doctor scan . --format sarif  # GitHub Code Scanning
forge-doctor scan . --format agent  # compact bundle for agent consumers
forge-doctor scan . --quiet         # only problems
forge-doctor scan . --fail-on warning
forge-doctor scan . --profile security   # severity profile
forge-doctor scan . --files a.py b.py    # only these files (pre-commit)
forge-doctor scan . --no-plugins         # skip external plugins
forge-doctor scan . --watch         # re-scan on every file change
forge-doctor scan . --save-baseline .fd-baseline.json
forge-doctor scan . --baseline .fd-baseline.json   # only NEW findings fail
forge-doctor scan . --baseline .fd.json --new-only # just the new ones
forge-doctor scan . --save-baseline main       # named: .forge-doctor/baselines/main.json
forge-doctor scan . --baseline main --new-only # per-branch baselines
forge-doctor scan . --evidence-out audit/      # dated audit bundle (report+suppressions+packs)
forge-doctor scan . --record --keep 30         # append a history snapshot, prune to 30
forge-doctor history .              # recorded snapshots
forge-doctor history diff --last    # new/resolved findings, entity+capability+drift deltas
forge-doctor history trend          # finding counts over the series + debt trajectory
forge-doctor diff old.json new.json     # diff two saved reports
forge-doctor diff HEAD~1...HEAD         # diff across git refs
forge-doctor compatibility --to 6.0     # Glue env + migration risks
forge-doctor migrate glue --from 3.0 --to 4.0  # migration intelligence
forge-doctor workspace .                # discover projects in a monorepo
forge-doctor workspace scan --path .    # scan every nested project
forge-doctor workspace inspect .        # cross-repo graph links (defines/implements/invokes)
forge-doctor fleet inspect fleet.yml    # manifest-driven estate graph over many repos
forge-doctor fleet query fleet.yml runtimes|dependents|findings|capability
forge-doctor fleet report fleet.yml     # estate census + findings roll-up
forge-doctor diff base...head --semantic  # entity diff + blast radius + capability/migration intel
forge-doctor remediate .              # deterministic fix plans per finding/root cause
forge-doctor fix .                    # preview/apply safety-classified text fixes
forge-doctor root-cause .             # correlate findings into causal clusters
forge-doctor runtime inspect .        # offline runtime evidence (exported artifacts)
forge-doctor architecture drift .     # drift vs .forge-doctor/contract.yml
forge-doctor contract validate .      # verify a platform contract file
forge-doctor capabilities list .      # platform capability registry report
forge-doctor capabilities graph .     # capability→evidence provenance subgraph
forge-doctor twin inspect .           # digital twin: graph + invariant report
forge-doctor twin export .            # deterministic twin snapshot artifact
forge-doctor advise .                 # ranked, cited actions (decision intelligence)
forge-doctor optimize .               # ranked optimization candidates + validation commands
forge-doctor what-if . --change glue-version=5.1   # hypothetical-change evaluation
forge-doctor migrate plan .           # named deterministic migration plans
forge-doctor policy list .            # organization policy packs
forge-doctor policy report .          # compliance: violations + suppression audit
forge-doctor lab run                  # Forge Lab scenario suites + ground truth
forge-doctor golden run               # golden-repo snapshot regression
forge-doctor bench run .              # performance & scale benchmark
forge-doctor schema contracts         # JSON Schemas for output artifacts
forge-doctor ontology                 # canonical vocabulary (entity/rel kinds, planes)
forge-doctor ontology validate .      # graph conformance vs the vocabulary
forge-doctor snowflake inspect .      # Snowflake warehouse model + vendor objects
forge-doctor bigquery inspect .       # BigQuery datasets, partitions, slots, jobs
forge-doctor redshift inspect .       # Redshift clusters, dist/sort keys, WLM, datashares
forge-doctor dbt inspect .            # dbt models, sources, tests, ref/source lineage
forge-doctor trino inspect .          # Trino catalogs, connectors, coordinator config, 3-part refs
forge-doctor analytical inspect .     # ClickHouse/Pinot/Druid tables, engines, ingestion, indexes
forge-doctor search inspect .         # OpenSearch/Elasticsearch templates, policies, domains
forge-doctor export . -f handoff      # portable bundle for downstream Forge tools
forge-doctor contracts verify b.json  # validate a bundle against published contracts
forge-doctor info                   # project stats, no checks
forge-doctor init --name my-etl     # scaffold a new project
forge-doctor repo                   # category shortcuts
forge-doctor plugins                # built-in + external checks
forge-doctor plugins init forge-doctor-x   # scaffold a plugin package
forge-doctor plugins lock && forge-doctor plugins verify  # integrity pinning
forge-doctor plugins install forge-doctor-x --dry-run     # pipx/pip wrapper
forge-doctor checks                 # every rule id and title
forge-doctor explain SPARK001       # why / when it's OK / how to fix
forge-doctor explain SPARK001 --json    # rule metadata for agents
forge-doctor trace SPARK001 src/job.py:42   # why this finding fired
forge-doctor diagnose driver.log      # fingerprint log errors (offline)
forge-doctor spark eventlog dir/      # executor loss, skew, spill, GC
forge-doctor lineage                # static reads/writes graph
forge-doctor schema diff old.avsc new.avsc  # breaking/compatible changes
forge-doctor graph                  # project intelligence graph
forge-doctor platform graph .       # canonical platform graph census (+ --json)
forge-doctor platform blast-radius <entity> .  # semantic impact traversal
forge-doctor suppressions           # audit governed exceptions
forge-doctor iceberg inspect .      # Iceberg model summary (ops/maintenance/risks)
forge-doctor iceberg merge .        # MERGE posture: source, ON cols, partition pruning
forge-doctor iceberg files .        # write APIs + small-file risk
forge-doctor iceberg compatibility  # runtime x Iceberg compat from the packs
forge-doctor controlm inspect .     # Control-M workflows-as-code (folders/jobs/events/risks)
forge-doctor airflow inspect .      # Airflow DAGs/tasks/sensors/providers + parse-time risks
forge-doctor terraform inspect .    # providers/modules/backends/reference graph
forge-doctor parquet inspect .      # Parquet dataset stats + codec/write risks
forge-doctor stepfunctions inspect .# ASL machines: states, integrations, graph risks
forge-doctor streaming inspect .    # streaming queries: source→sink, checkpoint, watermark
forge-doctor streaming diagnose .   # progress-metrics diagnostics (rate/state/watermark)
forge-doctor streaming semantics .  # derived delivery semantics per query
forge-doctor dynamodb inspect .     # DynamoDB tables + access patterns
forge-doctor neptune inspect .      # Neptune clusters + query shapes
forge-doctor lakeformation inspect .# governance, cross-account shares, tag usage
forge-doctor emr inspect .          # EMR EC2/Serverless/EKS deep model
forge-doctor databricks inspect .   # jobs/clusters/DBR/UC + bundles
forge-doctor delta inspect .        # Delta tables, ops, protocol features
forge-doctor athena inspect .       # workgroups, queries, result locations
forge-doctor lambda inspect .       # functions, triggers, runtimes, data-plane calls
forge-doctor kafka inspect .        # MSK clusters/topics/consumer groups
forge-doctor kinesis inspect .      # streams, EFO, Firehose
forge-doctor flink inspect .        # Flink jobs, state, checkpoints, savepoints
forge-doctor data-model inspect .   # cross-domain access-style inspection
forge-doctor iac                    # IaC checks (Terraform/CloudFormation)
forge-doctor cache                  # incremental-analysis stats
forge-doctor sbom                   # CycloneDX 1.5 of the project
forge-doctor knowledge verify       # pack freshness/provenance
forge-doctor doctor                 # environment self-check
forge-doctor mcp                    # JSON-RPC stdio server for agents
forge-doctor lsp                    # editor diagnostics (pip install .[lsp])
forge-doctor version
```

One scan can emit several reports — `--emit` is repeatable and takes
`FMT` (stdout) or `FMT:PATH`:

```bash
forge-doctor scan . --emit text --emit sarif:report.sarif --emit html:report.html
```

## Example output

```text
╭─ Forge Doctor ───────────────────────╮
│ Project  /home/me/etl                │
│ Version  0.7.0                       │
│  Checks  51                          │
╰──────────────────────────────────────╯

Python
  ✓ PY001 Python version
  ⚠ PY002 requires-python pyproject.toml
      not declared
      → Declare requires-python in pyproject.toml

Spark
  ⚠ SPARK001 collect() src/jobs/customer.py:182 NEW
      collect() moves all rows to the driver; verify the volume is bounded.
      [confidence: high]
      > df_final.collect()

╭─────────────── Summary ───────────────╮
│ Passed 17   Info 3   Warnings 4   Errors 0   │
│ 4 warning(s)                                 │
│ baseline: +1 new, -2 fixed, 33 pre-existing  │
╰──────────────────────────────────────────────╯
```

## Baselines

Save a scan once, then compare every later run against it — new findings get
a `NEW` marker and `--fail-on` only counts those, so pre-existing debt never
breaks CI:

```bash
forge-doctor scan . --save-baseline .forge-doctor-baseline.json
forge-doctor scan . --baseline .forge-doctor-baseline.json --fail-on warning
```

## HTML reports

```bash
forge-doctor scan . --format html --output report.html
```

Produces a single self-contained HTML file — same content as the terminal
report, shareable with the team. `--no-color` (or `NO_COLOR=1`) disables ANSI
colors; `--output` also works for plain text reports.

## Checks

| Category | IDs | Covers |
|---|---|---|
| repository | REP001–REP008 | pyproject/README/LICENSE/gitignore/tests/src layout/conflicting manifests/CI |
| python | PY001–PY007 | interpreter vs `requires-python`, venv, pytest/ruff/type-checker config |
| dependencies | DEP001–DEP006 | lock file, unrestricted deps, dev tools at runtime, duplicates |
| git | GIT001–GIT003 | repo initialized, tracked `.env`, tracked caches |
| spark | SPARK001–SPARK011 | `collect()`, `toPandas()`, `repartition(1)`, Python UDFs, actions in loops, cache w/o same-var unpersist, `.rdd`, Cartesian joins, global sort, `withColumn` in loops |
| aws | AWS001–AWS004 | CLI present, region configured, credentials detected (values never shown) |
| docker | DOCKER001–DOCKER004 | unpinned `FROM`, missing `USER`, secret-looking `ENV`/`ARG` names |
| glue | GLUE001–GLUE004 | awsglue usage, EOL runtimes, job params, DynamicFrame/DataFrame mixing |
| ci | CI001–CI004 | unpinned actions, missing python-version, missing test/lint steps |
| sql | SQL000–SQL003 | `SELECT *`, cartesian/comma joins, non-sargable predicates — needs the `[sql]` extra |
| iceberg | ICE000–ICE002, ICE008–ICE010, ICE012–ICE013, ICE020–ICE025 | IcebergProjectModel: format-version vs ops, maintenance gaps, catalog conflicts, Glue runtime compat, MERGE/write API risks |
| controlm | CTM000–CTM004, CTM009–CTM010, CTM028, CTM051, CTM070 | ControlMModel: Automation API defs — events produced/consumed, calendars, site standards, execution targets, credentials (names only) |
| airflow | AIR000–AIR004, AIR013, AIR021, AIR025, AIR040, AIR042, AIR100, AIR130 | AirflowModel: DAGs/tasks/edges/TaskFlow, parse-time calls, dynamic start_date, sensors (poke/deferrable/timeout), retries, providers vs pyproject |
| terraform | TF000–TF003, TF020–TF022, TF130 | TerraformProjectModel: required_version/providers, module pinning, local backend, reference graph |
| parquet | PARQ000, PARQ010, PARQ020–PARQ021, PARQ040–PARQ042 | ParquetProjectModel: write APIs, compression, small-file/dataset stats |
| stepfunctions | SFN000, SFN002–SFN003, SFN005, SFN010, SFN020 | StepFunctionsModel: ASL states/graphs, unreachable/dead-end states, sync timeouts, Distributed Map on Express |
| streaming | STREAM001–STREAM003, STREAM013–STREAM014, STREAM020, STREAM070, STREAM080 | StreamingProjectModel: Spark SS queries, checkpoint/watermark/stateful evidence, foreachBatch, derived delivery semantics |
| iac | IAC rules | CloudFormation + cross-IaC checks |
| dynamodb | DDB, DDBGT, DDBSTR rules | DynamoDBProjectModel: tables, indexes, streams, access patterns |
| neptune | NEP, NEPA, NEPCD, NEPGT rules | NeptuneProjectModel: clusters, query shapes, explain ingest |
| lakeformation | LF rules | LF permissions, cross-account shares, tag governance |
| platforms | EMR, DBX, DELTA rules | deep EMR (EC2/Serverless/EKS), Databricks jobs/UC, Delta protocol/features |
| serverless | ATH, LAM, SFN rules | Athena workgroups, Lambda functions/triggers, ASL machines |
| streaming-bus | KFK, KIN, FLK, STREAM rules | Kafka/MSK, Kinesis/EFO/Firehose, Flink state/checkpoints |
| graph | GRAPH rules | graph-model checks (queries, bulk loads, call sites) |
| platform | PLAT rules | cross-domain correlation (e.g. PLAT008–PLAT011) |
| architecture | ARCH rules | contract conformance + drift detection |
| policy | POLICY rules | suppressions audit, org policy-pack violations |

Full per-rule documentation (including *when it's OK*) lives in
[docs/checks.md](docs/checks.md).

## JSON output

```bash
forge-doctor scan . --format json
```

```json
{
  "tool": {"name": "forge-doctor", "version": "0.7.0"},
  "schema_version": "3.0",
  "version": "0.7.0",
  "project": {"name": "etl"},
  "summary": {"passed": 17, "info": 3, "warnings": 4, "errors": 0},
  "results": [
    {"check_id": "SPARK001", "severity": "warning", "category": "spark",
     "file": "src/jobs/customer.py", "line": 182, "message": "...", "recommendation": "...",
     "fingerprint": "7998e28bb13c13ac", "confidence": "high",
     "evidence": "df_final.collect()", "tags": ["performance"],
     "is_new": true}
  ],
  "baseline": {"new": 1, "fixed": 2, "existing": 33}
}
```

The JSON contract is stable and meant for CI, GitHub, and agent consumers —
`forge-doctor schema contracts` emits the JSON Schemas for every public
artifact, `forge-doctor contracts verify` validates artifacts against
them, and [docs/api.md](docs/api.md) +
[docs/contracts.md](docs/contracts.md) record the versioning and
interop rules.
Every finding carries a stable `fingerprint`; optional fields (`confidence`,
`evidence`, `evidence_kind`, `tags`, `docs_uri`, `source`, `fixable`,
`column`, `end_line`, `end_column`) appear only when set. `is_new`/`baseline`
appear only when `--baseline` is in use. `evidence_kind` classifies the fact's
source plane (`static`/`config`/`observed_metadata`/`runtime`/`derived`).

## SARIF & GitHub Code Scanning

```bash
forge-doctor scan . --format sarif -o forge-doctor.sarif
```

SARIF 2.1.0 with rules, locations, snippets, `partialFingerprints` and
`fixes` — upload via `github/codeql-action/upload-sarif` or use the
composite action in [action.yml](action.yml), which installs the CLI,
scans, and uploads SARIF in one step.

## Pre-commit

[.pre-commit-hooks.yaml](.pre-commit-hooks.yaml) ships a `forge-doctor`
hook; pre-commit feeds it the changed filenames via `--files`, so only the
files under review produce findings.

## Profiles

`--profile` re-maps severities per audience: `default`, `strict`
(info → warning), `security` (supply-chain findings escalate),
`spark-performance`, `glue-migration`, `production` (strict + security).

## Compatibility reports

```bash
forge-doctor compatibility . --to 6.0
forge-doctor migrate glue --from 4.0 --to 6.0
```

Detects the project's Glue/Spark/Python/Java/Iceberg environment and lists
migration risks from bundled **knowledge packs**
(`src/forge_doctor/knowledge/*/`), so version facts evolve without engine
changes. `migrate glue` adds real signals: version pins in code, DynamicFrame
usage, dependencies, and Terraform/CloudFormation pins.

## Runtime diagnosis

```bash
forge-doctor diagnose driver.log       # known-error fingerprinting
forge-doctor spark eventlog dir/       # executor loss, skew, spill, GC
forge-doctor spark plan plan.txt       # pathological physical-plan operators
forge-doctor spark logs executor.log   # signatures + runtime patterns
```

Deterministic and offline: error signatures live in
`knowledge/errors/*.json`; event logs/plans are parsed with the stdlib.

## Data intelligence

```bash
forge-doctor lineage                   # datasets read/written per job
forge-doctor lineage --format openlineage
forge-doctor schema diff old.avsc new.avsc
forge-doctor schema diff HEAD~1...HEAD --path .
forge-doctor graph --format mermaid    # jobs + datasets + infra + DAGs
```

Static lineage and the intelligence graph are built from the shared semantic
index — one `ast.parse` per file feeds Spark checks, Glue checks, lineage,
and `trace`. No target code is ever imported or executed.

## Incremental analysis & policy

`--cache` (default on locally, **off in CI** unless explicit) stores
per-file analysis facts in the platform user cache
(`%LOCALAPPDATA%\forge-doctor\cache`, `~/Library/Caches/forge-doctor`,
`$XDG_CACHE_HOME/forge-doctor`; override with `FORGE_DOCTOR_CACHE_DIR`) —
never inside the scanned repo, so a hostile checkout can't poison it.
Entries are keyed by repo + tool + analyzer schema version and carry
dependency provenance: editing a producer module re-analyzes its
importers. `forge-doctor cache` shows stats; `cache clean` removes it.
`--stats` reports per-check timings and cache hit rate on stderr.

```toml
[tool.forge-doctor.policy]
extends = "strict"          # base profile
[tool.forge-doctor.policy.rules.SPARK001]
severity = "error"          # per-rule override
[tool.forge-doctor.policy.rules.CI002]
enabled = false             # kill switch

[[tool.forge-doctor.suppressions]]
rule = "SPARK001"
path = "src/legacy/**"      # glob-scoped
reason = "migration in progress"
owner = "@data"
expires = "2026-12-31"      # past expiry reactivates the finding
approved_by = "@sec-lead"   # governance sign-off (POLICY011 if a pack requires it)
```

`forge-doctor suppressions` audits every exception as ACTIVE / EXPIRED /
UNUSED with owner and approver; expired ones emit `POLICY001` warnings
instead of suppressing. A policy pack with `require_approval: true`
makes unapproved suppressions emit `POLICY011` findings — and packs can
`extends:` a shared org pack (child rules override by id).

## Workspace

`forge-doctor workspace --path <dir>` discovers nested `pyproject.toml`
projects; `workspace scan` runs each and aggregates with a project prefix;
`workspace diff base...head` compares findings per subproject.

## Agent bundle

`--format agent` emits a minimal `{id, sev, loc, fp}` per finding —
built for LLM/agent consumers where every token counts; pair with
`forge-doctor explain <ID> --json` for full rule metadata on demand.

## Configuration

Zero configuration works. Optional, in the scanned project's `pyproject.toml`:

```toml
[tool.forge-doctor]
exclude = ["tests/fixtures/**"]
ignore = ["SPARK001"]

# Trust model: `trusted` gates BEFORE plugin code loads (distribution or
# entry-point names only - check ids can't gate code that hasn't run).
# `allow` additionally accepts check ids as a post-load filter.
[tool.forge-doctor.plugins]
trusted = ["forge-doctor-databricks"]

# Post-load per-check filter (requires the plugin to load first).
[tool.forge-doctor.plugins.checks]
enabled = ["DBX001", "DBX002"]

[tool.forge-doctor.aws]
ignore = ["AWS002"]
```

## Exit codes

`0` clean · `1` errors found (or `--fail-on warning` hit) · `2` internal/usage error.

## Integrations

- **MCP** (`forge-doctor mcp [--root DIR]`): zero-dependency JSON-RPC
  stdio server — `scan_project`, `explain_rule`, `check_compatibility`,
  `get_lineage`, `diagnose_log`, `diff_findings` tools plus
  `forge-doctor://rules/ID` and `forge-doctor://knowledge/D/N` resources.
  `--root` sandboxes every tool path argument to that tree.
- **LSP** (`pipx inject forge-doctor pygls lsprotocol`, `forge-doctor lsp`):
  publishes diagnostics on open/change/save, using the workspace root and
  unsaved-buffer contents (debounced; clears resolved findings).
- **SBOM** (`forge-doctor sbom`): CycloneDX 1.5 covering all locked
  dependencies (declared + transitive) with a dependency graph, plugins,
  knowledge packs and forge-doctor itself; deterministic serial number.

## Architecture

```
cli/ (Typer) → ScanService → runner → checks → semantic index/analyzers
     → cache → renderers (Rich/JSON/JSONL/HTML/SARIF/agent)

beside it: platform graph → capabilities / semantic diff + change intel /
migration / what-if / workspace / fleet / history / contracts / lab /
golden / bench / policy packs
```

Checks are small classes implementing a `Check` protocol
(`id`/`title`/`category`/`run(ctx) -> list[CheckResult]`). They never print and
never see the terminal. See [docs/architecture.md](docs/architecture.md).

## Plugins

External packages contribute checks via the `forge_doctor.checks` entry-point
group — install with `pipx inject forge-doctor forge-doctor-<ext>`. A broken
plugin degrades to a warning, never a crash.

Plugins run in-process with the same privileges as the CLI — treat them as
trusted code. `--no-plugins` disables them per run;
`[tool.forge-doctor.plugins].allow` restricts which ones may load.
Plugin findings carry a `source` field naming their distribution.

## Development

```bash
git clone https://github.com/EdgarSocrates98/forge-doctor
cd forge-doctor
poetry install
poetry run forge-doctor scan .
poetry run pytest
poetry run ruff check .
poetry run mypy
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). New checks: implement the `Check`
protocol, pick a stable id, register it in the category module's `CHECKS` list,
add tests and a `docs/checks.md` entry.

## License

MIT — see [LICENSE](LICENSE).
