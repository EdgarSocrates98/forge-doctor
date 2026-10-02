# Checks

Every rule has a stable id (`--ignore <ID>`), a severity, and an honest
"when it is OK" — Forge Doctor educates, it doesn't just complain.

## Evidence kinds

Every finding also carries an `evidence_kind` classifying *where* its
supporting fact came from (JSON output field; shown by `explain`):

| kind | source plane | examples |
|---|---|---|
| `static` | parsed source code / AST | spark, sql, streaming, airflow, glue, most iceberg/parquet |
| `config` | declarative config/manifests/IaC | terraform, iac, stepfunctions, controlm, dependencies, ci, docker, python_env, repository, aws |
| `observed_metadata` | real metadata artifacts | PARQ040–042 on-disk file stats, git index |
| `derived` | inferred by combining facts | cross-fact checks (ICE001/002/012/013/022/023/025, ICE008–010, CTM003/004/009/010/028, PARQ021, STREAM013) |
| `runtime` | runtime artifacts | reserved — progress logs / explains (future) |

Tagging is per check, not per domain: an Iceberg finding about a
`MERGE` statement is `static`, while the same domain's runtime-compat
finding is `derived`. Untagged plugin findings omit the field, and the
field never participates in the fingerprint.

## Repository

### REP001 — pyproject.toml · warning
Missing PEP 621 packaging metadata.
**Why:** no `pyproject.toml` means no modern build/dependency metadata.
**When OK:** non-Python repos, or repos that are pure scripts by design.
**Fix:** add a `pyproject.toml` with a `[project]` table.

### REP002 — README · warning
No `README.{md,rst,txt}` found.
**When OK:** internal throwaway utilities.
**Fix:** even a five-line README pays for itself.

### REP003 — License · info
No `LICENSE`/`COPYING` file.
**When OK:** private/internal projects.
**Fix:** add one — open source without a license isn't usable.

### REP004 — .gitignore · info
**When OK:** non-git directories.
**Fix:** add one before the first `__pycache__` slips in.

### REP005 — Tests · info
No `tests/` directory or `test_*.py` files.
**When OK:** prototypes you intend to delete.

### REP006 — src layout · info
Package lives at repo root instead of `src/`.
**When OK:** small projects; src layout is a preference, not a defect.
**Why it helps:** prevents accidental imports of the working tree in tests.

### REP007 — Conflicting manifests · warning
`requirements.txt` + `poetry.lock`, or multiple lockfiles.
**Why:** two sources of truth will drift.
**Fix:** pick one dependency manager.

### REP008 — CI configuration · info
No GitHub Actions/GitLab CI/Azure/CircleCI config.
**When OK:** early prototypes.

## Python

### PY001 — Python version · pass/info
The `python` on PATH. Informational anchor for PY003.

### PY002 — requires-python · warning
`requires-python` (or `tool.poetry.dependencies.python`) not declared.
**Why:** "any Python" rots — the interpreter and the code diverge silently.
**When OK:** truly single-interpreter, pinned environments.

### PY003 — Version compatibility · error/pass
Current interpreter vs `requires-python`, evaluated with `packaging`.
**Fix:** align the interpreter or the constraint.

### PY004 — Virtual environment · info
`.venv`/`venv` dir or `VIRTUAL_ENV` detected.
**When OK:** containerized/global installs by design.

### PY005 — Test configuration · info
pytest config discovered in pyproject/pytest.ini/tox.ini.

### PY006 — Linter configuration · info
Ruff/flake8/pre-commit config detected.

### PY007 — Type checker · info
mypy/pyright config detected.

## Dependencies

### DEP001 — Lock file · warning
Poetry-managed project without `poetry.lock`.
**Why:** unresolvable, unrepeatable installs.
**When OK:** libraries pinned by consumers (still, commit a lock for dev).

### DEP002 — Unrestricted dependency · warning
`dep = "*"` or a bare name with no constraint.
**Why:** any future release can break the install.
**When OK:** private metapackages where you own every release.

### DEP003 — Dev tool as runtime dep · warning
pytest/ruff/mypy/etc. in runtime dependencies.
**Fix:** move to a dev group.

### DEP004 — Duplicated runtime/dev dep · info
Same name in both groups — usually harmless, worth knowing.

### DEP005 — Stale lock · info
`poetry.lock` older than `pyproject.toml` (mtime heuristic, weak signal).
**Fix:** `poetry lock --check` / `poetry lock`.

### DEP006 — Poetry available · pass/info
`poetry` on PATH for Poetry-managed projects.

## Git

### GIT001 — Repository initialized · info
**When OK:** tarballs, vendored trees.

### GIT002 — Sensitive file tracked · error
`.env*`, `credentials`, `secrets.*` in `git ls-files`.
**Fix:** `git rm --cached`, rotate the credential, add to `.gitignore`.
**Never OK.**

### GIT003 — Python artifacts tracked · warning
`__pycache__`/`.pyc`/`.egg-info` under version control.

## Spark (AST analysis — never imports PySpark)

### SPARK001 — collect() · warning
`df.collect()` moves all rows to the driver.
**When OK:** bounded reference data, tests, single-driver jobs.
**Fix:** write to storage, or bound with `limit()` first.

### SPARK002 — toPandas() · warning
Same driver-bound risk as collect, plus Arrow overhead.
**When OK:** small aggregates destined for plotting/reporting.

### SPARK003 — repartition(1)/coalesce(1) · warning
Forces all work onto a single task.
**When OK:** deliberately producing exactly one small output file.

### SPARK004 — Python UDF · info
Row-serializes through the Python interpreter.
**When OK:** unavoidable domain logic; prefer built-ins or `pandas_udf`.

### SPARK005 — Action inside a loop · warning
One job per iteration; lineage can explode.
**Fix:** restructure to a single action where possible.

### SPARK006 — cache() without unpersist() · info
Cached frames may never be released. Pairing is per-variable:
`df.cache()` is only covered by `df.unpersist()`, not any unpersist in the file.
**When OK:** short-lived sessions; still tidy to `unpersist()`.

### SPARK007 — PySpark usage · pass/info
Count of files importing `pyspark` — the scan surface anchor.

### SPARK008 — RDD access · info
`.rdd` drops out of the Catalyst/Tungsten-optimized world.
**When OK:** partition-level control unavailable in DataFrames.
**Fix:** prefer DataFrame/SQL APIs; isolate and document RDD code.

### SPARK009 — Cartesian join · warning
`crossJoin` or `join()` without a condition — quadratic row explosion.
**When OK:** intentional cross joins on tiny bounded frames.
**Fix:** add an `on` condition or confirm bounded inputs.

### SPARK010 — Global sort · info
`orderBy`/`sort` shuffles every row into a single ordering.
**When OK:** final reporting step on small aggregates.
**Fix:** `sortWithinPartitions` when global ordering is not needed.

### SPARK011 — withColumn() in loop · warning
Each iteration wraps the logical plan — optimization time explodes.
**When OK:** very few iterations on small frames.
**Fix:** accumulate expressions and `select()` once.

Spark checks report `confidence` — `high` when the receiver resolves to a
tracked DataFrame (`df = spark.read...` or chained derivations), `medium`
when the receiver is only DataFrame-probable.

## AWS (local-only, never prints values)

### AWS001 — AWS CLI · pass/info
`aws` on PATH + reported version.

### AWS002 — Region configured · warning
No `AWS_REGION`/`AWS_DEFAULT_REGION` env and no `region` in `~/.aws/config`.
**When OK:** tools that always receive `--region` explicitly.

### AWS003 — Credentials detected · pass/info
Env var presence or `~/.aws/credentials` existence — values never read.

### AWS004 — Profile · pass/info
`AWS_PROFILE`/`AWS_DEFAULT_PROFILE` set, or profile names listed from config.

## Docker (textual Dockerfile analysis)

### DOCKER001 — Dockerfile present · pass/info
Anchor: count of Dockerfile/Dockerfile.* files found.

### DOCKER002 — Base image tag · warning
`FROM <image>` with no tag, or `:latest`, without a `@sha256:` digest.
**Why:** untagged bases drift — builds are not repeatable.
**When OK:** `FROM scratch`, stage aliases, `$ARG` references.
**Fix:** pin a version tag or digest.

### DOCKER003 — Non-root user · info
No `USER` instruction — the container runs as root.
**When OK:** build-stage images, dev containers.
**Fix:** add a non-root `USER` in the runtime stage.

### DOCKER004 — Secret-looking env name · warning
`ENV`/`ARG` names matching password/secret/token/api-key patterns.
Only the variable **name** is reported — values never enter results.
**Never OK** for real secrets.
**Fix:** pass secrets at runtime or via a secrets manager.

## Glue (AST analysis — never imports awsglue)

### GLUE001 — Glue usage · pass/info
Anchor: files importing `awsglue` or calling `client("glue")`.

### GLUE002 — EOL Glue runtime · warning/info
`glue_version`/`GlueVersion`/`--glue-version` pins compared against the
bundled Glue knowledge pack: EOL runtimes warn, aging ones inform.
The pack currently tracks up to Glue 6.0 (Spark 4.1.1, Python 3.13,
Java 17, Iceberg 1.11.0).
**Fix:** migrate to a supported runtime — `forge-doctor compatibility`
shows the per-version risk matrix.

### GLUE003 — Job parameters · pass/info
`getResolvedOptions` usage — job parameters handled properly,
or a hint that params may be hardcoded when glue usage exists.

### GLUE004 — DynamicFrame/DataFrame mixing · info
File combines DynamicFrame API (`fromDF`/`DynamicFrame`) with
PySpark usage or `toDF` conversions.
**When OK:** deliberate conversion at job boundaries.
**Why:** mixing APIs complicates lineage and testing.

## CI (textual workflow analysis — no YAML dependency)

### CI001 — CI configured · pass/info
Anchor: detects GitHub Actions, GitLab CI, Azure Pipelines, CircleCI.

### CI002 — Unpinned actions · warning/info
`uses:` refs are graded: full commit SHA = quiet, version tag (`@v4`) =
info (tags are mutable), floating ref (`@main`/`release/v1`/no `@`) =
warning. The `security` profile upgrades tag findings to warnings.
**Why:** floating refs execute whatever the ref currently points to.
**Fix:** pin to a full-length commit SHA (tag is the minimum).

### CI003 — Python version · info
GHA workflows exist but never set `python-version`/use setup-python.
**When OK:** non-Python or container-based pipelines.

### CI004 — Test/lint steps · info
No workflow step mentions pytest, ruff, mypy, tox, lint or test.
**When OK:** release/deploy-only workflows.

## IaC (Terraform/CloudFormation — lightweight parser, no terraform lib)

### IAC001 — aws_glue_job glue_version · warning/info
Terraform `resource "aws_glue_job"` `glue_version` pins compared against the
bundled Glue knowledge pack — same lifecycle status as GLUE002.

### IAC002 — Glue worker sizing · info
`worker_type`/`number_of_workers` sanity: missing workers, worker_type
without count, and similar misconfigurations.

### IAC003 — CloudFormation GlueVersion · warning/info
`AWS::Glue::Job` `GlueVersion` lifecycle status via the knowledge pack.

### IAC004 — CFN Lambda/EMR runtimes · warning/info
`Runtime:`/`ReleaseLabel:` pins flagged when past end-of-life.

## SQL (requires the `[sql]` extra — sqlglot)

One `SqlIndex` is built per scan: every `*.sql` file is tokenized and split
into statements, and string literals passed to `*.sql(...)` call sites are
reused from the semantic index. Statements that fail to parse are counted,
never fatal.

### SQL000 — SQL surface · pass/info
Anchor: count of parsed SQL statements (plus any skipped as unparseable).

### SQL001 — SELECT * · warning
Wildcard projections pull every column — brittle on schema drift, heavier IO.
**When OK:** exploratory notebooks, throwaway queries.
**Fix:** project the columns you actually need.

### SQL002 — Cartesian join · warning
`CROSS JOIN` or implicit comma joins (`FROM a, b`) multiply rows without a
predicate.
**When OK:** deliberate products on tiny bounded inputs.
**Fix:** add the join predicate, or document why the product is intended.

### SQL003 — Non-sargable predicate · warning
`f(column) = value` in WHERE defeats indexes and partition pruning.
**When OK:** tiny tables, or predicates the engine rewrites internally.
**Fix:** rewrite on the constant side — e.g. range bounds instead of `f(col)`.

## Iceberg (IcebergProjectModel — fused from AST index, SQL index, configs, IaC)

One model per scan collects Iceberg evidence: catalog/config settings,
`USING iceberg` markers, table refs on configured catalogs, write/merge ops,
table properties (`format-version`, `PARTITIONED BY`), and `CALL
<cat>.system.<proc>` maintenance procedures. Works without the `[sql]`
extra (SQL-derived evidence is simply absent).

### ICE000 — Iceberg usage · pass/info
Anchor: counts of tables/operations/catalogs and evidence kinds.

### ICE001 — Format-version vs operations · warning
MERGE/UPDATE/DELETE while `format-version` is unset (=1) or explicitly `1`.
**Fix:** set TBLPROPERTIES `'format-version'='2'` or confirm append-only.

### ICE002 — MERGE without partition evidence · info
MERGE INTO on a table with no `PARTITIONED BY` anywhere in scanned code.

### ICE008 — No expire_snapshots · warning
Writes detected but the snapshot-expiry procedure is never called.

### ICE009 — No rewrite_data_files · info
Writes detected with no compaction strategy — small files accumulate.

### ICE010 — No rewrite_manifests · info
Writes detected with no manifest compaction — planning degrades.

### ICE012 — Conflicting catalog config · warning
Same `spark.sql.catalog.<name>` configured with different implementations.

### ICE013 — Iceberg runtime compatibility · warning
Iceberg usage while an IaC Glue pin is below the compatibility pack floor.

### ICE020 — Legacy writer API · info
`insertInto`/`saveAsTable`/`write.save` in an Iceberg-evidenced file — the
V1 writer APIs bypass Iceberg's explicit table API.
**Fix:** prefer `df.writeTo('<table>').using('iceberg').append()`.

### ICE021 — insertInto on cataloged Iceberg table · info
`insertInto` on a catalog-qualified table — session-catalog resolution can
differ from the intended catalog.

### ICE022 — MERGE without Iceberg extensions · warning
MERGE/UPDATE/DELETE detected but
`spark.sql.extensions=...IcebergSparkSessionExtensions` is never configured
in project sources.

### ICE023 — MERGE unlikely to prune partitions · info
The MERGE `ON` predicate never references a partition column — the target
scan cannot prune partitions.

### ICE024 — Possible small-file write pattern · info
`repartition()`/`coalesce()` in a file that also writes — possible
single-partition write trap (static heuristic).

### ICE025 — Feature vs format-version · warning
`write.delete.mode=merge-on-read` on a v1 table (needs v2), or
deletion-vector/row-lineage properties below their format floor. Driven by
`knowledge/iceberg/spec.json`.

`forge-doctor iceberg inspect|maintenance|compatibility|merge|files`
summarizes the same model without running a full scan.

## Control-M (ControlMModel — Automation API defs + ctm/API references)

One model per scan parses `*.json` workflows-as-code definitions (only when
the file carries a Control-M `"Type"` marker): folders, jobs, `Defaults`
inheritance, events produced/consumed, calendars, site standards — plus
`ctm` CLI calls and `automation-api` URLs in scripts/CI/code. Fully offline;
`ctm` is never executed.

### CTM000 — Control-M usage · pass/info
Anchor: counts of jobs/folders/events/calendars/CLI+API refs.

### CTM002 — Job without execution target · warning
Job with neither `Host` nor `HostGroup` in effective (defaults-merged) props.

### CTM003 — Duplicate job/folder name · warning
The same job or folder name defined more than once across definitions.

### CTM004 — Job can never fire · info
No scheduling criteria and no waited events — manual-only execution.

### CTM009 — Event consumed but never produced · warning
A `WaitForEvents`/`InCondition` name nothing in the project produces.
**When OK:** produced by another application outside this repo.

### CTM010 — Event produced but never consumed · info
An emitted event with no consumer — a likely broken or renamed chain.

### CTM028 — Calendar referenced but not defined · warning
A job references a calendar name no definition declares.

### CTM051 — Missing required metadata · warning
Effective props missing any of Application/SubApplication/Owner/RunAs
(list comes from `knowledge/controlm/objects.json`).

### CTM070 — Credential literal in definitions · warning
Credential-shaped property (`*password*`, `*token*`, `*secret*`, ...) holds
a literal value — the property NAME is reported, never the value.
`%%VAR%%`/`${...}` references don't count.

`forge-doctor controlm inspect` summarizes the same model without a scan.

## Airflow (AirflowModel — index-flagged files, one AST pass each)

Files importing `airflow*` get a single targeted AST walk: `with DAG(...)` /
`dag = DAG(...)` / `@dag` definitions, `XxxOperator`/`XxxSensor`/`@task`
tasks, edges via `>>`, `<<`, `set_upstream/downstream`, `chain`,
`cross_downstream`, TaskFlow wiring by call, provider imports, and
module-scope (parse-time) external calls. No `apache-airflow` dependency.

### AIR000 — Airflow usage · pass/info
Anchor: dags/tasks/sensors/edges/providers/parse-time call counts.

### AIR002 — Duplicate dag_id · warning
The same `dag_id` defined in more than one place.

### AIR003 — DAG with no tasks · warning
A DAG that declares zero tasks.

### AIR004 — Orphan task · info
Task never wired (`>>`, `set_*`, `chain`, TaskFlow call) in a multi-task DAG.

### AIR013 — Dynamic start_date · warning
`start_date` is a runtime call (`datetime.now()`, `days_ago()`, ...).
**Fix:** use a constant (`datetime(2024, 1, 1)` / `pendulum.datetime`).

### AIR021 — External call at parse time · warning
Module-scope `requests`/`boto3`/`urllib`/... call — runs every parse pass.

### AIR025 — Variable.get at parse time · warning
Top-level `Variable.get()` hits the metadata DB on every parse.

### AIR040 — Sensor in poke mode · info
`*Sensor` without `deferrable=True` — holds a worker slot while waiting.

### AIR042 — Sensor without timeout · info
No `timeout`/`execution_timeout` — can wait forever on a stuck dependency.

### AIR100 — Retries without retry_delay · info
`retries>0` with no `retry_delay` — immediate retries hammer the dependency.

### AIR130 — Imported provider not declared · warning
`airflow.providers.<x>` imported but `apache-airflow-providers-<x>` absent
from pyproject dependencies (skipped without a pyproject).

`forge-doctor airflow inspect` summarizes the same model without a scan.

## Terraform (TerraformProjectModel — `.tf` block scan on hcl_lite)

Semantic Terraform surface: `terraform`/`provider`/`module`/`resource`/
`data`/`variable`/`output`/`locals`/`moved`/`import`/`check` blocks plus a
reference graph between addresses. Dependency-free (hcl_lite machinery).

### TF000 — Terraform usage · pass/info
Anchor: `.tf` file/block counts and reference-edge count.

### TF001 — Missing required_version · info
Resources exist but no `terraform { required_version = ... }`.

### TF002 — Provider without version constraint · info
Provider required without a version, or a `provider` block whose name is
absent from `required_providers`.

### TF003 — Overly broad provider constraint · warning
`>=` with no upper bound (`~>` counts as bounded — caps at the last
component).

### TF020 — Local module detected · info
`source = "./..."` / `"../..."` modules — noted for ownership review.

### TF021 — Registry module unpinned · warning
Registry-shaped source (`namespace/name/provider`) with no `version`.

### TF022 — Git module not pinned to immutable ref · warning
`git::`/`github.com` source with no `?ref=` or `ref=main|master|HEAD|...`.
**Fix:** pin to a commit SHA or version tag.

### TF130 — Local backend · warning
`backend "local"` — no state locking or sharing.

`forge-doctor terraform inspect` summarizes the same model without a scan.

## Parquet (ParquetProjectModel — code evidence + on-disk file stats)

Two planes: writers/readers/configs from the AST index, and real
`*.parquet` files (`stat` sizes only — no footer parsing in stage 1).

### PARQ000 — Parquet usage · pass/info
Anchor: writers/readers/files/config counts.

### PARQ010 — Possible small-file write pattern · info
`repartition()`/`coalesce()` in a file that also writes parquet.

### PARQ020 — Uncompressed analytical dataset · warning
`compression` set to `none`/`uncompressed` via `option()` or conf.

### PARQ021 — Inconsistent parquet codecs · info
Different codecs configured across the project.

### PARQ040 — Small-file proliferation · info
Median on-disk file size below the pack floor
(`knowledge/parquet/format.json` `small_file_median_mb`).

### PARQ041 — Excessive file count · info
File count above the pack `excessive_files` threshold.

### PARQ042 — Inconsistent file-size distribution · info
p95/median ratio over the pack `size_skew_ratio` — skewed keys or mixed
writer configs.

`forge-doctor parquet inspect` summarizes the same model without a scan.

## Step Functions (StepFunctionsModel — ASL + IaC definitions)

ASL definitions are JSON — parsed stdlib-only from `*.asl.json` /
`*.states.json` / `*.sfn.json`, any `*.json` with `StartAt`+`States`,
Terraform `aws_sfn_state_machine` definitions (quoted or heredoc), and
CFN `AWS::StepFunctions::StateMachine` `DefinitionString`. Map
`Iterator`/`ItemProcessor` bodies are nested graphs for reachability.

### SFN000 — Step Functions usage · pass/info
Anchor: machines, states, IaC references.

### SFN002 — Unreachable state · warning
A state never reached from `StartAt` over `Next`/`Choices`/`Default`/
`Catch` edges.

### SFN003 — Dead-end path · warning
A non-terminal state with no `Next` and no `End: true`.

### SFN005 — Choice without Default · info
Unmatched input raises `States.NoChoiceMatched` at runtime.

### SFN010 — Sync task without timeout · info
A `.sync` or `.waitForTaskToken` integration with no `TimeoutSeconds`.

### SFN020 — Distributed Map in Express workflow · warning
`ProcessorConfig.Mode "DISTRIBUTED"` inside a `type = "EXPRESS"` machine —
Distributed Map requires Standard.

`forge-doctor stepfunctions inspect` summarizes the same model without a
scan.

## Streaming (StreamingProjectModel — platform-agnostic queries)

`readStream`/`writeStream` chains recovered from the AST index (never
executed), grouped into queries by stream-variable lineage
(`agg = orders.groupBy(…)` links both halves of one logical query).

### STREAM001 — Streaming workload detected · pass/info
Anchor: query/source/sink counts.

### STREAM002 — Streaming query without checkpoint · warning
A `writeStream` with no `checkpointLocation` observed statically —
restart cannot recover progress.

### STREAM003 — Checkpoint under temporary path · warning
`checkpointLocation` under `/tmp`-style volatile storage.

### STREAM013 — Shared checkpoint location · warning
Two queries bound to one checkpoint directory corrupt each other's
offsets/state.

### STREAM014 — Dynamic checkpoint location · warning
Path composed from f-string/datetime/uuid/env — changes between
restarts, abandoning recovery state.

### STREAM020 — Stateful operation without watermark · info
Watermark-required stateful ops (pack `needs_watermark`) with no
`withWatermark` observed.

### STREAM070 — foreachBatch sink · info
Arbitrary per-batch code — idempotency and `batch_id` usage need manual
verification.

`forge-doctor streaming inspect` summarizes the same model without a
scan.

## Graph (GraphProjectModel — query files, call sites, bulk-load headers)

The model separates property-graph evidence (Gremlin/openCypher:
vertices/edges, both carry properties) from RDF evidence (SPARQL:
subject/predicate/object) — the two paradigms are never conflated.
All GRAPH findings are INFO/WARNING with LOW/MEDIUM confidence: the
model reports structure and traversal shape, never estimated cost.

### GRAPH001 — Graph workload detected · info
Anchor: counts traversals, artifacts, languages, paradigms, labels.

### GRAPH002 — Disconnected graph components · info
Vertex labels cluster into >1 component by traversal evidence — the
model may be split or linking edges were not found.

### GRAPH003 — Likely orphan vertex type · info
A vertex label that never appears alongside an edge type.

### GRAPH004 — Edge references undefined vertex type · info
An edge traversed with no vertex type on at least one endpoint.

### GRAPH005 — Inconsistent relationship direction · info
The same edge type traversed both out and in across the project.

### GRAPH006 — Relationship represented redundantly · info
The same label appears as both an edge type and a vertex label.

### GRAPH007 — Overly generic relationship type · info
Edge labels like `RELATED`/`LINKS` carry no domain meaning.

### GRAPH008 — Wide property vocabulary · info
Data-dependent (spec-deferred to INFO): ≥8 distinct property keys —
a static fan-out hint, cardinality needs runtime data.

### GRAPH009 — Probable supernode pattern · info
Data-dependent (spec-deferred to INFO): a vertex type touching ≥5
distinct edge types — candidate only, not a confirmed hotspot.

### GRAPH010 — Graph modeled as relational rows · info
Bulk-load/data artifacts exist but no traversal/query usage was found.

### GRAPH020 — Traversal without selective starting point · warning
`g.V()` / bare `MATCH (n)` — cost scales with total graph size.

### GRAPH021 — Traversal without a result bound · info
Hops with no `LIMIT`/`limit()`/`tail()` — unbounded working set.

### GRAPH022 — Variable-length traversal without depth bound · warning
`[*]`/`[*1..]`/unbounded `repeat()` — paths of arbitrary depth.

### GRAPH023 — High-fanout traversal risk · info
≥3 hops with no filter — fan-out candidate, needs explain/profile.

### GRAPH024 — Late filtering · info
First filter lands at step ≥3 — the traversal fanned out first.

### GRAPH025 — Repeated identical traversal pattern · info
The same traversal shape at multiple call sites.

### GRAPH026 — Excessive full-graph starting traversals · info
Extension: ≥3 traversals project-wide start unselectively.

### GRAPH030 — Mixed graph paradigms · info/warning
Extension: property-graph and RDF evidence in the same project (INFO)
or same file (WARNING) with no explicit boundary.

`forge-doctor graph inspect`, `graph schema`, and `graph traversals`
summarize the model without a scan. The pre-existing project
intelligence dump remains available as `forge-doctor graph <path>`
(unchanged) and `forge-doctor graph project`.

## DynamoDB (DynamoDBProjectModel — IaC tables + boto3 call sites)

Tables come from `aws_dynamodb_table` / `AWS::DynamoDB::*` resources
(nested gsi/lsi/replica/ttl/pitr blocks included) and from boto3
bindings in code (`boto3.resource("dynamodb")`, `.Table("name")`);
access operations resolve TableName literals, bound table vars, key
conditions, projections, filters, and `ConsistentRead`. Everything is
static-risk framing — no capacity math, no throttling claims without
runtime metrics. Single-table vs multi-table is reported, never
recommended.

### DDB001 — DynamoDB workload detected · info
Anchor: tables, global tables, streams, op mix, index count.

### DDB002 — Scan on a latency-sensitive path · warning
`scan` inside a handler/route-shaped function.

### DDB003 — Scan without projection/filter strategy · info
Unfiltered, unprojected scan reads every item in full.

### DDB004 — Partition key likely poor cardinality · info
PK attribute named like a low-cardinality field (status/type/…).

### DDB005 — Hot-partition candidate · warning
The same static PK literal drives multiple write sites.

### DDB006 — Constant partition-key literal · info
A write whose partition key is a pure constant.

### DDB007 — Time-only sort key write pattern · info
`sk` bound by a bare timestamp-shaped variable.

### DDB008 — GSI duplicates base-table access · info
GSI partition key identical to the table's.

### DDB009 — GSI partition key likely hot · info
GSI on a low-cardinality attribute — index hotspot risk.

### DDB010 — GSI count vs observed access patterns · info
Declared-but-unqueried GSIs and code-referenced-undeclared indexes.

### DDBSTR001 — Stream enabled, no consumer detected · info
### DDBSTR002 — Stream consumer lacks idempotency signal · info
No `ReportBatchItemFailures`/`batchItemFailures` observed.
### DDBSTR003 — Duplicate-processing risk · info
One stream feeding ≥2 consumers — each replays every record.
### DDBSTR004 — Stream retention/recovery mismatch · info
No PITR/failure-destination; records expire after ~24h.
### DDBSTR005 — Replicated stream events on global table · warning
Per-region stream copies repeat downstream side effects.

### DDBGT001 — Multi-region write-conflict risk · info
Writes against a global table (last-writer-wins replication).
### DDBGT002 — MREC transaction semantics · info
Transactions are atomic only in the invoking region (registry-sourced).
### DDBGT003 — Transactions on MRSC global table · error
Resolved through the capability registry: MRSC transaction support is
UNSUPPORTED.
### DDBGT005 — Region routing strategy unclear · info
Global table with no visible region pinning in code or providers.

`forge-doctor dynamodb inspect|access-patterns|indexes|streams|
global-tables|capacity` summarize the model without a scan.

## Neptune (NeptuneProjectModel — IaC + client code + query shapes)

Separates Amazon Neptune Database (Gremlin/openCypher on property graph,
SPARQL on RDF) from Neptune Analytics (a distinct service with built-in
algorithms). Clusters/instances/subnet+parameter groups/global clusters
come from `aws_neptune_*` Terraform and `AWS::Neptune*` CloudFormation;
endpoints (`*.neptune.amazonaws.com:8182`, `DriverRemoteConnection`),
boto3 `neptune`/`neptunedata`/`neptune-graph` bindings, and
`start_loader_job` call sites come from code. Query shapes reuse the
Graph Intelligence extractors — nothing executes.

### NEP001 — Neptune workload detected · info
Anchor: product, clusters, instances, endpoints, bulk loads, languages.

### NEP010 — Query language incompatible with graph paradigm · warning
Registry-derived: openCypher/Gremlin vs RDF-loaded data, SPARQL vs
property-graph data.

### NEP020 — Traversal without selective start · warning
`g.V()`/`MATCH` with no bound start predicate.

### NEP021 — Unbounded variable-length traversal · warning
`[*]` / `repeat()` without `times()`/`until`/`LIMIT` bound.

### NEP022 — Cartesian graph pattern · warning
Disconnected `MATCH` patterns with no joining relationship.

### NEP023 — Large unbounded result projection · info
`RETURN *` / `SELECT *` with no `LIMIT`.

### NEP024 — Property filter applied post-traversal · warning
First filter lands after hop expansion — late-filtering risk.

### NEP030 — Row-by-row ingestion pattern · warning
Repeated write-per-item calls with no bulk-loader evidence.

### NEP031 — Bulk-loader candidate · info
Write-heavy workload where the S3 bulk loader is absent.

### NEP032 — Bulk-load IAM/S3 relationship incomplete · warning/info
`start_loader_job` without `iamRoleArn`, or a role whose S3 access is
not visible in the project.

### NEP033 — Malformed graph input risk · info
A query-language file/string that produced no parsed traversal.

### NEP040 — Read-heavy workload, no replica evidence · info
Only when read/write asymmetry is observable; otherwise silent.

### NEP041 — Weak backup/PITR posture · info/warning
No `backup_retention_period`, or `skip_final_snapshot=true`.

### NEP042 — Public-access assumption · warning
`publicly_accessible = true` on a cluster instance.

### NEP043 — IAM-auth configuration mismatch · info
Cluster `iam_database_authentication_enabled` vs client SigV4 evidence.

### NEP044 — Security-group topology risk · info
No attached SGs, or no SG opens the Neptune port.

### NEP045 — Cluster/instance configuration mismatch · warning/info
`db.serverless` required on serverless clusters; clusters without
instances.

### NEPGT001 — Write expectation in a secondary region · warning
Writes aimed at a non-primary region of a global database.

### NEPGT002 — Multi-region active-active write assumption · info
Write traffic spanning multiple endpoint regions.

### NEPGT003 — Cross-region recovery topology incomplete · info
Global cluster with no secondary cluster evidence.

### NEPA001 — Manual algorithm with Analytics available · info
Hand-rolled graph algorithm while Neptune Analytics is configured.

### NEPA002 — Algorithm call incompatible with detected product · info
Algorithm-style usage with no Analytics evidence.

### NEPCD001 — Stream-fed Neptune mutation lacks idempotency · warning
DynamoDB stream consumer (no idempotency signal) plus graph writes —
replayed records may double-apply mutations.

`forge-doctor neptune inspect|schema|queries|ingest|explain|compatibility`
summarize the model; `neptune explain|analyze-explain <file>` reads an
exported explain/profile artifact offline (STATIC / OBSERVED_METADATA /
RUNTIME classification, large-intermediate / broad-start / late-filter
flags). `forge-doctor data-model inspect` reports the access-style
breakdown (key lookups vs bounded queries vs scans vs multi-hop
traversals) as facts only — no platform recommendation.

## Platform (cross-domain)

PLAT### findings are emitted by the cross-domain rule engine
(`core/crossdomain.py`): each rule declares the entity kinds,
relationship kinds, and capability ids it needs, and fires only when all
prerequisites are observably present. Findings list their contributing
facts instead of asserting a bare risk label.

### PLAT001 — Orchestration retry + non-idempotent sink · warning
A retried orchestration task (Airflow `retries>0`, DAG `default_args`
included) plus append-style Iceberg writes with no merge/overwrite/
createOrReplace evidence — retries can duplicate rows.

### PLAT002 — Runtime/config feature incompatibility · warning
Declared runtime version evaluates UNSUPPORTED for a capability the
source exercises (e.g. Glue 3.0 + Iceberg MERGE/UPDATE/DELETE).

### PLAT003 — Continuous writer + storage maintenance gap · info
A `processingTime`/`continuous` micro-batch sink to Iceberg/Delta with
no compaction or snapshot-expiry evidence in the project.

### PLAT004 — Duplicate orchestration ownership · warning
The same compute job is invoked from two orchestrator domains
(Airflow / Control-M / Step Functions) — double-run risk.

### PLAT005 — IaC runtime config vs source assumptions · warning/info
Terraform `aws_lambda_function.runtime` below `requires-python`, or a
`glue_version` pin in code that differs from the Terraform declaration.

### PLAT006 — Table format + consumer compatibility mismatch · info/warning
An Iceberg `format-version=2` table has consumers; severity upgrades to
warning when the capability engine proves the consumer unsupported.

### PLAT007 — Stream sink retry + side-effect idempotency risk · warning
A microbatch writer sinks into a non-transactional store
(DynamoDB/Neptune or `foreachBatch`) with no checkpoint or dedup
(`ConditionExpression`, `batch_id` in the item key) evidence.

### PLAT008 — EMR + Iceberg writes under Lake Formation · warning
An EMR cluster performs Iceberg row-level writes
(`merge`/`update`/`delete`/`overwrite*`) while Lake Formation governs
the catalog, but the cluster declares no `security_configuration` —
that path bypasses the LF grants.

### PLAT009 — Databricks runtime below Delta feature floor · warning
A Delta feature (deletion vectors, liquid clustering, CDF, column
mapping) is exercised on a Databricks cluster whose `spark_version`
predates the feature's protocol floor — the capability registry's
UNSUPPORTED verdict or the floor check produces the finding.

### PLAT010 — SFN + Lambda poller where native `.sync` exists · info
A state machine invokes a Lambda Task while the project contains a
client-side Athena poll pair (`start_query_execution` +
`get_query_execution`) — the Task is a candidate for
`states:::aws-sdk:athena:startQueryExecution.sync`.

### PLAT011 — Distributed Map concurrency > Lambda reserved concurrency · warning
A DISTRIBUTED Map whose ItemProcessor invokes a Lambda function is
configured with `MaxConcurrency` above the function's
`reserved_concurrent_executions` — items throttle instead of running.

`forge-doctor platform findings` renders only this category; the same
checks also run inside `forge-doctor scan` under `category=platform`.

## Runtime evidence (offline artifacts)

`forge-doctor runtime inspect <artifact>` normalizes a user-exported
runtime artifact into `RuntimeEvidenceModel` facts — executions,
metrics, errors, timings, throughput, lag, retries, resource usage —
without any cloud access. Auto-detected adapters:

- `spark_eventlog` — Spark History event log (NDJSON `SparkListener*`
  records): jobs/stages, shuffle/spill/GC/input-output metrics,
  executor loss, per-stage task skew.
- `spark_ss_progress` — Structured Streaming `StreamingQueryProgress`
  JSON: input vs processed rate, batch `durationMs` phases, state
  operator rows, source offsets, watermark.
- `athena_stats` — `GetQueryExecution` statistics JSON (nested or flat):
  DataScannedInBytes plus queue/planning/execution timings.
- `lambda_report` — CloudWatch `REPORT RequestId:` lines: duration,
  billed duration, memory size/used, init duration, timeouts.
- `sfn_history` — `GetExecutionHistory` JSON: state transitions,
  failures with causes, retry counts, execution duration.
- `glue_logs` — Glue job log text: JobRunId/Job Name identifiers plus
  conservative error extraction (OOM, executor loss, Spark/Glue
  exceptions).
- `neptune_explain` — the phase-4 explain/profile parser exposed as a
  runtime adapter (steps, max cardinality, flags).
- `flink_checkpoints` — Flink REST checkpoint history JSON
  (`checkpoints.counts` + `history`): per-checkpoint executions,
  completed/failed counts, `CheckpointFailed` errors.
- `stream_metrics` — generic metric-point exports (`{name, value,
  unit}` lists/maps): `MillisBehindLatest`, `records-lag`,
  consumer-lag, `IncomingRecords`.

`runtime diagnose <artifact>` additionally matches the artifact's text
and extracted errors against the known-error signature packs.
`streaming progress <file>` renders a progress artifact directly.
Artifacts join platform-graph entities only through demonstrable
identifiers (ARN, job name, query id, execution id) — never fuzzy.

## Root cause

`forge-doctor root-cause . [--runtime artifact.json ...]` correlates scan
findings with runtime evidence:

- **Promotions** — a `FindingPromotion` lifts a finding when runtime facts
  are consistent with it (e.g. `repartition(1)` + a stage that ran one
  task; streaming backlog when processed rate < input rate; retries
  observed for a retry+non-idempotent finding). Levels: CONFIRMED (exact
  identity join required), STRONGLY_SUPPORTED (targeted rule, domain
  only), POSSIBLE (shared-domain errors). The original finding and its
  fingerprint are never modified — `base_fingerprint` + deterministic
  `promotion_id` preserve correlation.
- **Causal clusters** — deterministic chains with evidenced nodes:
  `RC_STREAM_COMMITS` (micro-batch → commit amplification → small files
  → consumer planning/scan overhead) and `RC_SPARK_SKEW` (join/shuffle
  key → skew → spill → long stage). CONFIRMED needs every node evidenced
  with runtime facts; fewer nodes degrade to STRONGLY_SUPPORTED/POSSIBLE.
  A single evidenced node never forms a cluster.

## Remediation planning

`forge-doctor remediate . [--root-cause <id>]` prints deterministic
`RemediationPlan`s from `knowledge/remediation/` packs: ordered actions
with rationale, expected effect, per-action validation and `depends_on`
edges, plus prerequisites, risks, validation steps and rollback notes.
Plans are advisory — `remediate` itself never edits code, generates
patches, commits, deploys, or runs Terraform/migrations. Findings
without a remediation mapping produce no plan.

## Safe fixes

`forge-doctor fix .` turns findings into **safety-classified** fix
proposals (`core/fixes.py`): `safe` transforms are pure, bounded,
idempotent text edits (e.g. declare `requires-python` in pyproject,
append ignore patterns to `.gitignore`, create a default `.gitignore`);
`review-required` proposals (e.g. dropping a duplicate
`requirements.txt` when `poetry.lock` exists) apply only with
`--apply --class review`; `manual-only` findings (IaC resource
semantics, IAM/Lake Formation, partition/table changes) print guidance
and have no code path that can write. Default is a dry run printing
unified diffs; `--apply` writes `safe` only, re-reading each file and
aborting on stale sources, with a JSON audit record under `--json`.
Nothing is committed or pushed — version control is the rollback.

## Architecture contract + drift

An optional `platform-contract.yml` at the project root declares the
*desired* architecture (versioned schema: `contract_version`,
`pipelines` with compute/storage/orchestration/sla/semantics/ownership/
capabilities, `datasets`, `governance.allowed_dependencies`).
`forge-doctor contract validate <file>` checks structure and schema
version; `forge-doctor architecture drift .` (or a plain `scan` when a
contract exists) compares it against declared (Terraform), implemented
(code), and runtime (`--runtime` artifacts) planes:

- **ARCH001** runtime/implemented platform differs from contract · warning
- **ARCH002** configured version differs from contract version · warning
- **ARCH003** implemented storage format differs from contract · warning
- **ARCH004** dependency used but absent from `allowed_dependencies` · warning
- **ARCH005** runtime execution duration violates contracted SLA · error
- **ARCH006** `idempotent: true` contract without write evidence · warning
- **ARCH007** same resource provisioned by multiple owners (Terraform vs
  manual `boto3 create_*`) · warning
- **ARCH008** implemented feature outside the pipeline's approved
  capabilities · info

Absent contract, missing runtime, or missing config evidence never
produces drift — unknown stays unknown.

## Lake Formation

`forge-doctor lakeformation` builds a `LakeFormationProjectModel` from
Terraform (`aws_lakeformation_*`, `aws_glue_catalog_*`, `aws_ram_*`,
IAM policies naming `lakeformation:`/`glue:` actions), CloudFormation
(`AWS::LakeFormation::*`, `AWS::Glue::*`, `AWS::RAM::*`), and boto3
`lakeformation`/`glue` call-sites — grants, admins, default permissions,
registered data locations, resource links, LF tags, data-cells filters,
RAM shares, and the consumer/producer cross-account paths. FGAC/FTA
support per engine is capability-driven
(`knowledge/capabilities/lakeformation.json`), evaluated through the
registry rather than hardcoded in checks. In the platform graph, grants
become `principal -[GOVERNS]-> catalog/location` edges and resource
links become `DEPENDS_ON` edges to the producer catalog.

- **LF000** Lake Formation usage census · info (anchor)
- **LF001** resource-link/cross-account target without RAM evidence · warning
- **LF002** `IAMAllowedPrincipals` alongside FGAC/LF-tag evidence · warning
- **LF010** grants/locations present, no `data_lake_settings` declared · info
- **LF011** `IAMAllowedPrincipals` retained in default permissions · warning
- **LF012** resource link referenced by no grant · warning
- **LF013** grant to external account without RAM principal association · warning
- **LF014** `data_location` grant on an unregistered S3 arn · warning
- **LF015** `lf_tag` grant on an undefined tag key / LF-TBAC summary · warning/info
- **LF016** data-cells filter referenced by no grant · info
- **LF017** hybrid access: IAM defaults retained while FGAC/LF-TBAC in use · warning
- **LF018** grant option delegated to an external account · warning

## EMR

The EMR, Databricks, and Delta Lake sections below all belong to the
`platforms` check category.

`forge-doctor emr` builds an `EmrProjectModel` from Terraform
(`aws_emr_cluster`, `aws_emrserverless_application`,
`aws_emrcontainers_virtual_cluster`, `aws_emr_step`,
`aws_emr_managed_scaling_policy`), CloudFormation (`AWS::EMR::*`,
`AWS::EMRServerless::*`, `AWS::EMRContainers::*`), and boto3
`emr`/`emr-serverless`/`emr-containers` call-sites — release labels,
instance fleets (spot/on-demand), autoscaling, dynamic allocation,
roles, bootstrap actions, logging, security configuration, and step
failure actions. Commands: `emr inspect`, `emr findings`.

- **EMR000** EMR usage census · info (anchor)
- **EMR001** release label below emr-6.x · warning
- **EMR002** EC2 cluster without scaling/dynamic allocation · info
- **EMR003** all-Spot instance fleets · warning
- **EMR004** cluster without `log_uri` · info
- **EMR005** cluster without security configuration · info
- **EMR006** step without `action_on_failure` · info
- **EMR007** serverless application without maximum capacity · info

## Databricks

`forge-doctor databricks` builds a `DatabricksProjectModel` from the
`databricks_*` Terraform provider (jobs, clusters, SQL warehouses,
pipelines, Unity Catalog objects, workspaces), `databricks.yml` asset
bundles, and Python sdk/dbutils/notebook evidence — DBR versions,
autoscale/spot/serverless posture, job-vs-existing-cluster usage, UC
coverage. Commands: `databricks inspect`, `databricks findings`.

- **DBX000** Databricks usage census · info (anchor)
- **DBX001** job task pinned to `existing_cluster_id` · warning
- **DBX002** fixed `num_workers` without autoscale · info
- **DBX003** cluster on pre-13.3-LTS DBR · warning
- **DBX004** Databricks IaC but no Unity Catalog objects · info
- **DBX005** external_location without a storage_credential · warning
- **DBX006** jobs/pipelines but no `databricks.yml` bundle · info

## Delta Lake

`forge-doctor delta` builds a `DeltaProjectModel` from SQL
(`USING DELTA`, `MERGE INTO`, `UPDATE`, `DELETE`, `OPTIMIZE`,
`VACUUM`, `RESTORE`, `CLUSTER BY`, `TBLPROPERTIES`), Python
`DeltaTable`/`spark.sql` call-sites, `.format("delta")` reads/writes,
and structured-streaming delta endpoints — table features (deletion
vectors, CDF, liquid clustering, column mapping, schema evolution,
identity columns) and reader/writer protocol floors. Commands:
`delta inspect`, `delta findings`, `delta features`.

- **DELTA000** Delta usage census · info (anchor)
- **DELTA001** MERGE/UPDATE/DELETE churn without OPTIMIZE · warning
- **DELTA002** deletion vectors — protocol/runtime floor warning · warning
- **DELTA003** auto-merge schema-evolution flags · info
- **DELTA004** change data feed enabled with no consumer · info

Cross-domain rules added by this stage: **PLAT008** (EMR Iceberg
writes under Lake Formation with no LF-integrated security
configuration) and **PLAT009** (Databricks runtime below a detected
Delta feature's protocol floor).

## Athena

`forge-doctor athena` builds an `AthenaProjectModel` from Terraform
(`aws_athena_workgroup`, `aws_athena_data_catalog`,
`aws_athena_database`, `aws_athena_named_query`,
`aws_athena_prepared_statement`), CloudFormation (`AWS::Athena::*`),
Athena-shaped SQL (CTAS, UNLOAD, PREPARE/EXECUTE, Iceberg DDL), and
boto3 `athena` call-sites — engine versions, result configuration,
bytes-scanned cutoffs, and query operations. Commands:
`athena inspect`, `athena findings`.

- **ATH000** Athena usage census · info (anchor)
- **ATH001** engine < 3 with Iceberg DDL evidence · warning
- **ATH002** workgroup without enforced result location · info
- **ATH003** workgroup without `bytes_scanned_cutoff_per_query` · info
- **ATH004** CTAS/UNLOAD/PREPARE SQL but no declared workgroup · info
- **ATH005** boto3 `start_query_execution` + `get_query_execution`
  polling pair · info

## Lambda

`forge-doctor lambda` builds a `LambdaProjectModel` from Terraform
(`aws_lambda_function`, `aws_lambda_event_source_mapping`,
`aws_lambda_permission`, `aws_lambda_function_event_invoke_config`,
`aws_lambda_provisioned_concurrency_config`, `aws_lambda_layer_version`,
S3/SNS/schedule trigger resources), CloudFormation (`AWS::Lambda::*`),
and boto3 `lambda` call-sites — runtime, architecture, memory, timeout,
ephemeral storage, concurrency controls, VPC, DLQ, layers, event
sources, destinations, and idempotency-library evidence. Commands:
`lambda inspect`, `lambda findings`.

- **LAM000** Lambda usage census · info (anchor)
- **LAM001** end-of-life runtime · warning
- **LAM002** event-triggered function without DLQ/destination · info
- **LAM003** stream-triggered function without concurrency bound · info
- **LAM004** timeout unset or at the 15-minute ceiling · info
- **LAM005** VPC-attached function on default 128MB memory · info

## Step Functions (deepened)

The `StepFunctionsModel` now captures per-machine `QueryLanguage`
(JSONPath default vs JSONata opt-in), per-state payload keys
(`InputPath`/`Parameters`/`ResultSelector`/`ResultPath`/`Arguments`/
`Output`/`Assign`/`ItemSelector`/`ItemBatcher`), retry semantics
(`MaxAttempts` sums, `ErrorEquals` sets on Retry and Catch), the
invoked Lambda `target` (`Parameters.FunctionName` or literal ARN), and
Distributed Map `MaxConcurrency`/`ToleratedFailurePercentage`.

- **SFN030** JSONata-only keys under a JSONPath machine · warning
- **SFN031** DISTRIBUTED Map with no retry/catch/tolerance · info
- **SFN032** Wait+poll loop around a `.sync`-capable integration · warning

Cross-domain rules added: **PLAT010** (SFN + Lambda poller → native
`.sync` candidate) and **PLAT011** (Distributed Map concurrency exceeds
the invoked Lambda's reserved concurrency).

## Streaming bus (Kafka / Kinesis / Flink)

Deep models fuse Terraform/CFN resources, Spark SS source options, and
Python client calls into per-domain project models:

- `KafkaProjectModel` — MSK clusters (encryption-in-transit, auth,
  broker count, public access), topics (partitions/replication/config),
  SS options (`subscribe`, `startingOffsets`, `maxOffsetsPerTrigger`,
  `failOnDataLoss`, `kafka.group.id`), Python clients
  (`KafkaConsumer`/`KafkaProducer`/`SchemaRegistryClient`), consumer
  groups, schema-registry presence, TLS/SASL evidence.
- `KinesisProjectModel` — streams (shards, stream_mode, retention,
  encryption), EFO consumers, Firehose streams, managed-Flink apps,
  SS kinesis options, boto3 `kinesis` calls (with StreamName/Consumer
  literals).
- `FlinkProjectModel` — jobs (code entrypoints + managed
  KinesisAnalyticsV2 apps), evidence kinds (env, source, keyed_op,
  window, timer, checkpoint, savepoint, parallelism, sink,
  delivery_mode, state_backend, watermark), checkpoint mode/interval.

Checks:

- **KFK000** anchor · info — cluster/topic/call census.
- **KFK001** MSK plaintext client-broker · warning
- **KFK002** single-partition topic · warning
- **KFK003** kafka source without `maxOffsetsPerTrigger` · warning
- **KFK004** kafka without schema-registry evidence · warning
- **KFK005** `KafkaConsumer` without `group.id` · warning
- **KFK006** kafka without any TLS/SASL evidence · warning
- **KIN000** anchor · info — stream/consumer/api census.
- **KIN001** single-shard provisioned stream · warning
- **KIN002** stream at default (≤24h) retention · warning
- **KIN003** multiple polling consumers without EFO · warning
- **FLK000** anchor · info — job/evidence census.
- **FLK001** flink job without checkpointing · warning
- **FLK002** keyed state without checkpointing · error
- **FLK003** managed app without autoscaling/parallelism · warning
- **FLK004** declared `AT_LEAST_ONCE` mode · warning
- **STREAM080** derived delivery semantics per query · info/warning —
  reports `at-most-once`/`at-least-once`/`effectively-once`/
  `exactly-once-claim`/`unknown` with the full basis tuple
  (source+checkpoint+engine+sink+idempotency). A checkpoint alone never
  yields exactly-once.

Runtime streaming diagnostics (`streaming diagnose <progress*.json>`)
derive from a `StreamingQueryProgress` batch series:

- **SRATE001** input rate exceeds processing rate → backlog growth
- **SSTATE002** monotonic state-row growth across ≥3 batches
- **SWM003** watermark far behind max event time (>60s)
- **SCKPT004** walCommit/commit phase instability (>3× baseline)
- **SKFK005** non-zero source partition backlog (kafka offsets)
- **SDUR006** slow micro-batch (>30s) — trigger-interval fit

New runtime adapters: `flink_checkpoints` (Flink REST checkpoint
history: counts/history/failed) and `stream_metrics` (generic metric
exports: `MillisBehindLatest`, `records-lag`, consumer lag).

`streaming semantics` renders the derived delivery claims;
`kafka|kinesis|flink inspect|findings` expose the models and risks.
Platform-graph integration adds `stream:kafka:*`, `stream:kinesis:*`,
`stream:firehose:*`, `compute_job:flink:*`, `principal:kafka:group:*`
and `principal:kinesis:*` (EFO consumer → stream `CONSUMES` edges).
Knowledge packs: `streaming/delivery`, `kafka/config`,
`kinesis/config`, `flink/config`, `capabilities/{kafka,kinesis,flink}`.

## What-if + migration planning

`forge-doctor what-if --change target=value .` simulates a property
change without executing anything. Known targets:
`glue-version`, `iceberg-format-version`, `databricks-runtime`,
`lambda-runtime`, `emr-release`. The evaluation reports:

- **affected entities** — platform-graph entities the change touches
- **capability transitions** — per-capability status at `from` → `to`
  (lost capabilities are blockers, gained are enablers)
- **compatibility notes** — domain-pack facts (`glue/compatibility`
  change lists, `iceberg/compatibility` runtime bundling,
  `databricks/runtime` DBR status, `lambda/runtimes` eol set,
  `iceberg/versions` format versions)
- **contract conflicts** — pipeline `compute.version` pins the change
  would violate (pre-drifted ARCH002)
- **unknowns** — dimensions the packs don't cover

`forge-doctor migrate plan .` enumerates applicable named paths:

- `glue-4-to-5`, `iceberg-v1-to-v2`, `databricks-runtime-upgrade`,
  `parquet-to-delta`, `parquet-to-iceberg`, `streaming-modernize`,
  `lambda-runtime-upgrade`

Each `MigrationPlan` carries source/target environments, affected
entities, blockers, warnings, required changes, validation steps, and
rollback considerations — generated from knowledge packs only, never
executed. Facts the packs lack surface as UNKNOWN entries.

## Forge Lab

`labs/<domain>/<scenario>/` holds reproducible mini-projects with a
declared `expected.json` ground truth:

```json
{
  "expected_findings": ["SPARK003", "PARQ040@jobs/etl.py"],
  "forbidden_findings": ["DELTA001"],
  "expected_graph_edges": ["writes|compute_job:glue:etl->dataset:parquet:out"],
  "expected_capabilities": ["iceberg:ICEBERG_MERGE_WRITE;format_version=2=supported"],
  "expected_root_causes": ["RC_STREAM_COMMITS"]
}
```

`forge-doctor lab run` executes the full engine per scenario and compares
against truth — missed expectations and forbidden hits fail; detected
but undeclared findings are reported as `extra` for FP analysis.
Optional `runtime/` artifact dirs feed root-cause clustering.
`lab list` / `lab report` / `--json` supported; exit code 1 on failure.

`forge-doctor lab metrics` rolls the comparisons into quality numbers
per domain plus a TOTAL row:

- **precision / recall / FPR** — FP candidates are undeclared
  WARNING+ findings not covered by `allowed_findings` (scenario-level)
  or `labs/_defaults.json` (lab-level noise budget). FPR is measured
  against `forbidden_findings` declarations.
- **parser coverage** — fraction of `.py` files with a parsed AST.
- **graph edge recall / capability accuracy / root-cause recall** —
  matched expectations per category.

`-` marks a metric with zero denominator (nothing to measure).
`--json` emits the same numbers for CI.

## Golden repositories

`golden/<name>/repo/` holds realistic mini-projects;
`golden/<name>/expected/` pins the *full* deterministic output —
findings (with fingerprints), platform graph, root-cause clusters,
remediation plans, migration plans — as sorted JSON snapshots.

```bash
forge-doctor golden list            # corpus inventory
forge-doctor golden run             # diff engine vs snapshots (CI gate)
forge-doctor golden update          # regenerate — review diff, then commit
```

Any semantic regression surfaces as an add/remove diff per artifact.
The scan is scoped to `repo/` so snapshot text can never contaminate
evidence. Seed corpus: `airflow-glue-athena`, `databricks-delta`,
`dynamodb-neptune`, `dynamodb-streams-lambda`, `emr-iceberg`,
`glue-4-to-5`, `kafka-spark-iceberg`, `lf-cross-account`.

## Performance benchmark

`forge-doctor bench run` measures the engine on a project — or a
deterministic synthetic corpus (`--files N --seed S`):

```text
files=200 py=122 findings=103
cold=13050ms warm=5538ms ratio=0.42 packs=74 (34ms)
ast=122/122 graph=1317ms (117 ent/63 rel) peak=12MB
```

- **cold / warm** — full check pass, cold vs disk-cached index
- **ast=N/M** — one AST parse per `.py` file is the ideal
- **graph** — platform-graph build time + size
- **packs** — knowledge-pack load, **peak** — tracemalloc MB

Budgets (`--budget b.json`) are portable ratios/counts, not wall clocks:
`warm_ratio_max`, `ast_parse_max_ratio`, `graph_ms_per_1k_files`,
`cold_ms_per_1k_files`. Violations print and exit 1.

## Workspace intelligence

`forge-doctor workspace inspect` merges per-repo platform graphs into a
`WorkspaceModel`: sibling sub-projects are discovered by marker files
(`pyproject.toml`, `*.tf`, `databricks.yml`, `airflow.cfg`, `dags/`
content — outermost marker dir wins), each repo's `DataPlatformGraph` is
built independently, and canonical entity ids converge so the same
`compute_job:glue:orders-etl` declared in `terraform-repo`, implemented
in `glue-jobs`, and invoked by `airflow-dags` becomes one node with
three `repo:workspace:<name>` edges:

| Edge | Meaning |
|------|---------|
| `DEFINES` | the repo's IaC/config declares the entity |
| `IMPLEMENTS` | a glue-code file whose normalized stem matches the job name |
| `INVOKES` | the repo's workflows invoke an entity defined elsewhere |

Internal `task:*` targets and same-repo invocations are not links.
`--format json` emits the full repo/link/graph model for tooling.

## Semantic diff

`forge-doctor diff <base>...<head> --semantic` upgrades the findings
diff into a PR-review report built on the platform graph:

```text
risk: HIGH   1 files changed   +0 new findings   -0 fixed
  - modified structural entity compute_job:glue:orders-etl impacts 2 dependents
| modified | compute_job:glue:orders-etl | glue_version | 2 |
blast radius -> task:airflow:load, workflow:airflow:daily_load
```

- **added / removed / modified / touched** — entity-level changes;
  `touched` means the file changed but extracted attrs are identical.
- **blast radius** — transitive dependents (callers count: inbound
  INVOKES/DEPENDS_ON/READS edges are followed, unlike impact-reach).
- **risk** — HIGH when a removed entity has dependents or a structural
  entity (table/stream/dataset/catalog/workflow/compute_job) with
  dependents is modified; MEDIUM for touched-with-dependents or bare
  removals; LOW otherwise. Reasons are printed per classification.
- Exit 1 on new findings or HIGH risk — CI-gateable.

Version attrs (`glue_version`, `runtime`, `engine_version`,
`release_label`, `format_version`, `spark_version`) propagate from
Terraform onto typed entities so a `4.0 → 5.0` bump registers as
`modified`, not just `touched`.

## Policy

### Organization policy packs

Org rules are data, not code — drop `*.yml|*.yaml|*.json` files in
`.forge-doctor/policy/` (or `policy.yml` / `org-policy.yml` at the
root, or `[tool.forge-doctor] policy_packs = ["org.yml"]`):

```yaml
pack: org-security
version: "1.0"
rules:
  - id: ORG001
    severity: error
    message: RDS instances must not be publicly accessible
    forbid:
      terraform:
        resource_type: aws_db_instance
        attr: publicly_accessible
        op: equals            # equals | matches | present
        value: "true"
  - id: ORG002
    severity: warning
    message: CODEOWNERS is required
    require:
      file: CODEOWNERS
```

- `forbid.pattern` + `file_glob` — per-line regex violations.
- `forbid.terraform` — per-resource attr checks.
- `require.file` — the glob must match ≥1 project file.
- `require.file_glob` + `contains` — every matching file must contain
  the regex (zero matches = no violation).
- `require.terraform` — every resource of the type must satisfy
  `attr`+`op` (`present` by default).

**Layering.** A pack may `extends` another pack (by name or
project-relative path). Parent rules merge into the child; on a rule-id
collision the child wins. Cycles and missing references become
`POLICY010` errors:

```yaml
pack: repo-rules
extends: org-base          # inherits all of org-base's rules
require_approval: true      # suppressions must carry approved_by
rules:
  - id: ORG001             # same id overrides the parent's rule
    severity: warning
    message: weaker for this repo
    ...
```

Findings carry the org rule ids (`ORG001`…) so severity policy and
suppressions govern them like built-ins. Broken packs surface as a
`POLICY010` error finding — never silent. Commands: `policy list`,
`policy eval [-f json]`, `policy report [-f json]` (compliance summary:
packs, violations by rule, suppression audit), `policy validate <file>`.

### POLICY001 — Expired suppression · warning

### POLICY001 — Expired suppression · warning
A `[[tool.forge-doctor.suppressions]]` entry past its `expires` date —
the underlying finding reactivates and this warning fires.

### POLICY002 — Unused suppression · info
A suppression that matched no finding — the exception may be dead weight
(or the suppressed check is gone).

### POLICY010 — Organization policy packs · error
Runs every discovered org pack; emits `POLICY010` itself only for an
invalid pack file — violations carry each rule's own id (`ORG###`).

### POLICY011 — Suppression lacks approval · warning
A pack with `require_approval: true` was loaded and a configured
suppression has no `approved_by`. Approvals live in
`[[tool.forge-doctor.suppressions]]` — add `approved_by = "name"`.

See `forge-doctor suppressions` for the full audit.

## Warehouse (vendor-neutral WarehouseProjectModel — Terraform + SQL evidence)

`warehouse_model` normalizes analytic-warehouse evidence before the
vendor adapters land: declarative `snowflake_*` / `google_bigquery_*` /
`aws_redshift*` Terraform resources map to compute, database, schema,
table, view, and workload-management facts; warehouse-dialect DDL in
`.sql` files (sqlglot extra) maps to tables, views, materialized views,
and external tables. Nothing here is vendor-specific — rows that only
make sense per-vendor live behind `attrs`.

### WARE001 — Warehouse surface · pass/info
Anchor: platform count, compute, namespaces, tables, views, queries.

### WARE010 — Unprofiled warehouse table · info
A declared table has no observed storage/statistics evidence — cost,
cardinality, and layout decisions run blind.
**Fix:** ingest table statistics (catalog exports) so checks can
profile them.

### WARE020 — View references unknown base table · warning
A view's `tables_read` includes a name absent from the model — either
an external dependency (undiagnosed) or a broken reference.
**Fix:** declare the base table or mark the dependency external.

### WARE030 — Compute without workload management · info
Warehouse/cluster compute exists with no queue, reservation, or WLM
config observed.
**Fix:** attach workload-management config to the compute resource.
