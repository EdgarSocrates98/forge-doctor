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
Plans are advisory — Forge Doctor never edits code, generates patches,
commits, deploys, or runs Terraform/migrations. Findings without a
remediation mapping produce no plan.

## Policy

### POLICY001 — Expired suppression · warning
A `[[tool.forge-doctor.suppressions]]` entry past its `expires` date —
the underlying finding reactivates and this warning fires.

### POLICY002 — Unused suppression · info
A suppression that matched no finding — the exception may be dead weight
(or the suppressed check is gone).

See `forge-doctor suppressions` for the full audit.
