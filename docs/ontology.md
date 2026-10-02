# Platform ontology

The canonical vocabulary every adapter, contract, and doc derives from.
`core/ontology.py` is the source of truth — this document's tables are
verified against it by tests, so the two cannot drift.

```bash
forge-doctor ontology                # print the vocabulary
forge-doctor ontology -f json        # stable machine-readable shape
forge-doctor ontology validate .     # conformance-check a project's graph
```

## Entity kinds

Entity ids are `kind:domain:identifier`. Kinds are enum-constrained at
construction (`EntityKind`) — producers cannot invent one silently.

<!-- BEGIN entity_kinds -->
| kind | meaning |
|---|---|
| workflow | An orchestrated pipeline of tasks (DAG, state machine, job flow). |
| task | A single unit of work inside a workflow. |
| compute_job | An execution target that runs code (Glue job, EMR cluster, Lambda). |
| query | A named or observed query (Athena named query, authored SQL). |
| dataset | A logical dataset independent of physical layout. |
| table | A physical or cataloged table (DynamoDB, Iceberg, Glue catalog). |
| stream | A streaming channel (Kafka topic, Kinesis stream, SNS/SQS). |
| catalog | A metadata catalog or namespace (Glue database, Databricks catalog). |
| storage_location | A storage endpoint (bucket/prefix, volume, external location). |
| principal | An identity that holds grants (role, storage credential, LF principal). |
| infrastructure_resource | An IaC-declared resource not covered by a narrower kind. |
| database | A database instance or cluster (Neptune, operational stores). |
| graph | A property-graph container (Neptune cluster/graph). |
| graph_node | A node inside a property graph. |
| graph_edge | An edge inside a property graph. |
| repo | A repository participating in a workspace. |
| capability | A platform capability fact evaluated against context. |
| knowledge_pack | A bundled knowledge pack (capabilities/errors/golden facts). |
| warehouse | An analytic warehouse platform (Snowflake, BigQuery, Redshift). |
| warehouse_compute | Warehouse execution resource (warehouse, cluster, workgroup, reservation). |
| view | A named view or materialized view. |
| schema | A schema-level namespace inside a warehouse database. |
| dbt_model | A dbt model/transformation node (ref/source edges). |
| data_contract | A producer data contract (schema, SLA, quality terms). |
<!-- END entity_kinds -->

## Relationship kinds

Edges are typed (`RelKind`) and carry the evidence plane they came from.

<!-- BEGIN relationship_kinds -->
| kind | meaning |
|---|---|
| INVOKES | src triggers execution of dst (job call, function invoke). |
| READS | src reads data from dst. |
| WRITES | src writes data to dst. |
| DEFINES | src declares dst (IaC defines resource, workspace defines repo). |
| GOVERNS | src governs access to dst (LF grant, catalog policy). |
| STORED_IN | src's data physically resides in dst. |
| DEPENDS_ON | src requires dst to exist/run first (ordering, dependency). |
| TRIGGERS | src event fires dst (schedule, event rule, notification). |
| PRODUCES | src emits records consumed downstream (stream producer). |
| CONSUMES | src consumes records produced upstream (stream consumer). |
| IMPLEMENTS | src provides the implementation dst declares (repo implements entity). |
| EVIDENCED_BY | src's claim/status is established by dst (knowledge pack, evidence). |
| CONTAINS | src contains dst (warehouse contains schema, schema contains table). |
| READS_FROM | src reads data from dst (query/view reads a table). |
| WRITES_TO | src writes data into dst (query writes a table, job writes a view). |
<!-- END relationship_kinds -->

## Evidence planes

Where a supporting fact was obtained (`EvidenceKind` on findings and
edges). Distinct from confidence: the plane classifies the *source*.

<!-- BEGIN evidence_planes -->
| plane | meaning |
|---|---|
| static | Parsed source code / AST facts. |
| config | Declarative config, manifests, IaC. |
| observed_metadata | Real metadata artifacts committed to the repo. |
| runtime | Runtime artifacts (progress logs, exported telemetry). |
| derived | Inferred by combining multiple facts. |
<!-- END evidence_planes -->

## Evidence domains

Channels a check can observe (`core/incremental.py`). File-family
domains invalidate on file events; host domains always rerun.

<!-- BEGIN evidence_domains -->
| domain | channel |
|---|---|
| ci | Workflow/pipeline definitions (GitHub Actions, GitLab CI…). |
| code | Other source languages — .scala/.sh/.bat/.cmd/.ps1. |
| config | .yml/.yaml/.json/.toml/.ini/.cfg/.conf/.properties/.xml. |
| contract | platform-contract.* files. |
| docker | Dockerfile, compose, .dockerignore. |
| env | Host environment variables. |
| files | File-tree enumeration — names and presence only. |
| git | Git index / tracked-file set. |
| graph | Cypher/gremlin/sparql/rdf graph files. |
| host | Host tools, home dir, PATH interpreters. |
| notebook | .ipynb notebooks. |
| packaging | pyproject.toml, requirements, lock files. |
| python | .py/.pyi — AST index, boto3, spark/glue/streaming models. |
| runtime | runtime/ evidence artifacts ingested by the project. |
| sql | .sql/.hql query files. |
| terraform | .tf/.tfvars/.hcl infrastructure definitions. |
| unbounded | Derived multi-domain state — cannot be invalidated selectively. |
<!-- END evidence_domains -->

## Producer domains

The `domain` segment of an entity id names the model/family that
produced it — free text at construction, so this list is what
`ontology validate` enforces.

<!-- BEGIN producer_domains -->
| domain | model |
|---|---|
| airflow | Apache Airflow orchestration model. |
| athena | Amazon Athena analytics model. |
| aws | Generic AWS provider resources not mapped to a narrower family. |
| controlm | Control-M scheduling model. |
| databricks | Databricks workspace model. |
| datacontract | Data contract model (declared schema/SLA promises). |
| dbt | dbt transformation-layer model (models, sources, tests). |
| delta | Delta Lake table model. |
| dynamodb | Amazon DynamoDB model. |
| emr | Amazon EMR model. |
| firehose | Amazon Data Firehose model. |
| flink | Apache Flink model. |
| glue | AWS Glue jobs/catalog model. |
| graph | Generic property-graph model. |
| graphdata | Authored graph data files. |
| iceberg | Apache Iceberg table model. |
| kafka | Apache Kafka model. |
| kinesis | Amazon Kinesis model. |
| knowledge | Bundled knowledge packs (capabilities/errors provenance). |
| lakeformation | AWS Lake Formation governance model. |
| lambda | AWS Lambda model. |
| neptune | Amazon Neptune model. |
| neptune_loader | Neptune bulk-loader task entities (loader functions). |
| parquet | Apache Parquet physical-layout model. |
| sns | Amazon SNS model. |
| spark_ss | Spark Structured Streaming model. |
| sql | First-class SQL model (sqlglot index). |
| sqs | Amazon SQS model. |
| stepfunctions | AWS Step Functions model. |
| terraform | Terraform IaC model. |
| trino | Trino federated-SQL model (catalogs, coordinator config, refs). |
| warehouse | Vendor-neutral warehouse model (compute, namespaces, tables, views). |
| workspace | Workspace/repo aggregation model. |
<!-- END producer_domains -->

## Capability families

Platform families with bundled capability packs (`capabilities list`).
Derived at runtime from `knowledge/capabilities/*.json` — additive as
packs land.

## Versioning

Ontology terms are stable public identifiers. Additions are minor
changes; renaming or removing a term is breaking and requires the
affected contract versions to bump (see `docs/contracts.md`).
