# Platform ontology

The canonical vocabulary every adapter, contract, and doc derives from.
`core/ontology.py` is the source of truth — this document's tables are
verified against it by tests, so the two cannot drift.

```bash
forge-doctor-data ontology                # print the vocabulary
forge-doctor-data ontology -f json        # stable machine-readable shape
forge-doctor-data ontology validate .     # conformance-check a project's graph
forge-doctor-data ontology platform       # implementations -> vendor-neutral kinds
forge-doctor-data ontology platform glue  # one implementation in detail
forge-doctor-data ontology workloads      # workload intents + serving platform kinds
forge-doctor-data ontology access-patterns
```

The first six sections are the *vocabulary* (spec 225). The sections from
**Platform kinds** down are the *semantic model* (spec 230) —
`core/platform_ontology.py` — which classifies concrete platforms
(`PlatformImplementation`) into vendor-neutral kinds and models workload
intent, access patterns, physical design, materialization, data
movement, ownership and lifecycle. Mappings are explicit registries:
unmapped platforms return `None`/UNKNOWN, never guessed; logical and
physical identities join only on declared evidence, never name
similarity.

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
| clickhouse | ClickHouse real-time OLAP model. |
| cloud | Vendor-neutral cloud abstraction view (multi-cloud parity). |
| controlm | Control-M scheduling model. |
| databricks | Databricks workspace model. |
| datacontract | Data contract model (declared schema/SLA promises). |
| dbt | dbt transformation-layer model (models, sources, tests). |
| delta | Delta Lake table model. |
| druid | Apache Druid real-time OLAP model. |
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
| metadata | Metadata catalog declared estate (DataHub/OpenMetadata/Glue/Unity). |
| neptune | Amazon Neptune model. |
| neptune_loader | Neptune bulk-loader task entities (loader functions). |
| parquet | Apache Parquet physical-layout model. |
| pinot | Apache Pinot real-time OLAP model. |
| quality | Data-quality expectation suites and gates (Deequ/GX/SodaCL/dbt). |
| search | Search platform model (indices, lifecycle policies, domains). |
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

## Platform kinds

<!-- BEGIN platform_kinds -->
| kind | meaning |
|---|---|
| compute | General execution substrate (jobs, clusters, functions). |
| warehouse | Analytic warehouse (SQL, columnar, separated storage/compute). |
| lakehouse | Lake-storage analytic platform unifying tables, files, engines. |
| object_storage | Durable object/blob storage (S3, ADLS, GCS). |
| table_format | Table/layout contract over object storage (Iceberg, Delta). |
| stream | Ordered record streaming (Kinesis, Kafka, Event Hubs, Pub/Sub). |
| message_bus | Message delivery without ordered replay (SNS, SQS). |
| orchestrator | Workflow scheduling/coordination (Airflow, Step Functions). |
| transformation_engine | In-warehouse/declarative transformation (dbt). |
| query_engine | Federated/interactive SQL engine without storage (Trino). |
| search_index | Inverted/vector search engine (OpenSearch, Elasticsearch). |
| graph_store | Property-graph store (Neptune). |
| operational_store | Low-latency operational KV/document store (DynamoDB). |
| catalog | Technical metadata catalog (Glue Catalog, Data Catalog). |
| governance_plane | Access governance/policy plane (Lake Formation, Purview). |
| metadata_catalog | Discovery/lineage metadata platform (DataHub, Dataplex). |
| quality_system | Data-quality expectations/gates (Deequ, GX, SodaCL). |
| serving_layer | Low-latency analytical serving (ClickHouse, Pinot, Druid). |
| observability_system | Pipeline/platform observability signals. |
<!-- END platform_kinds -->

## Workload intents

<!-- BEGIN workload_intents -->
| intent | meaning |
|---|---|
| batch_analytics | Scheduled large-scale analytical scans/aggregations. |
| interactive_sql | Human-driven ad-hoc SQL over warehouses/lakes. |
| realtime_serving | Low-latency queries serving applications/dashboards. |
| event_driven | Records processed as they arrive (streams, triggers). |
| transformation | Declarative/modelled data reshaping (dbt, SQL). |
| orchestration | Coordinating pipelines, dependencies, schedules. |
| ml_pipeline | Feature/model pipelines and training workflows. |
| governance | Access policy, classification, compliance, audit. |
| observability | Lineage, quality, freshness, runtime telemetry. |
| search_analytics | Full-text search and log/document analytics. |
| vector_search | Similarity search over embeddings (kNN/ANN). |
| graph_analytics | Traversal and pattern queries over property graphs. |
<!-- END workload_intents -->

## Data access patterns

<!-- BEGIN data_access_patterns -->
| pattern | meaning |
|---|---|
| point_lookup | Single-record reads by key (DynamoDB GetItem). |
| key_range | Bounded key-range reads (sort key, partition range). |
| full_scan | Unfiltered whole-dataset scans. |
| filtered_scan | Scans pruned by predicates/partitions. |
| aggregate_scan | Rollups/aggregations over many rows. |
| time_series | Append-ordered time-bucketed reads. |
| graph_traversal | Multi-hop edge traversal. |
| full_text | Term/phrase search over tokenized fields. |
| vector_similarity | Nearest-neighbour over vector embeddings. |
| mutation_heavy | High write/update/delete rate workloads. |
| streaming_append | Continuous record ingestion. |
| random_read | Unordered sparse reads (no key locality). |
<!-- END data_access_patterns -->

## Materialization kinds

<!-- BEGIN materialization_kinds -->
| kind | meaning |
|---|---|
| view | Logical query definition; computed at read time. |
| materialized_view | Stored query result refreshed on a policy. |
| incremental_table | Table maintained by incremental apply (dbt incremental, dynamic tables). |
| copy | Materialized one-time physical copy. |
| cache | Derived data held for latency (result/DAX/warehouse cache). |
| projection | Alternate physical layout of the same data (ClickHouse projection). |
| search_index | Tokenized/vector index derived from source data. |
| replica | Physical copy kept in sync (global tables, replication). |
<!-- END materialization_kinds -->

## Consistency models

<!-- BEGIN consistency_models -->
| model | meaning |
|---|---|
| strong | Every read sees the latest committed write. |
| eventual | Reads may lag writes; replicas converge over time. |
| read_after_write | Own writes are immediately visible; others may lag. |
| snapshot | Reads observe a consistent point-in-time snapshot. |
| region_local | Consistency guaranteed within a region only. |
| multi_region | Consistency guaranteed across regions. |
| unknown | No evidence — never inferred. |
<!-- END consistency_models -->

## Data movement modes

<!-- BEGIN data_movement_modes -->
| mode | meaning |
|---|---|
| share | Consumer reads producer's storage in place — no copy exists. |
| replicate | A physical copy exists and is kept in sync continuously. |
| copy | A one-time physical copy; source changes do not propagate. |
| federate | Queries run remotely; results move, data does not. |
| mirror | Low-level byte/log mirroring for DR or failover. |
| stream | Record stream delivered to a target as it is produced. |
<!-- END data_movement_modes -->

## Lifecycle states

<!-- BEGIN lifecycle_states -->
| state | meaning |
|---|---|
| created | Object exists; no retention policy observed yet. |
| retained | Held under an explicit retention policy/window. |
| archived | Moved to cold/archive storage class. |
| compacted | Small files/segments merged (table maintenance). |
| snapshotted | Point-in-time snapshots taken/retained. |
| expired | Marked for automatic expiry (TTL/partition expiration). |
| deleted | Removed/scheduled for removal. |
<!-- END lifecycle_states -->

## Ownership sources

<!-- BEGIN ownership_sources -->
| source | meaning |
|---|---|
| platform_contract | Declared in a platform-contract.* file. |
| terraform_tags | Resource tags/labels in IaC. |
| dbt_meta | ``meta:`` block on a dbt model/source. |
| datahub | DataHub ownership assertion (committed export). |
| openmetadata | OpenMetadata ownership assertion (committed export). |
| codeowners | CODEOWNERS path rule. |
| unknown | No attributable source. |
<!-- END ownership_sources -->

## Versioning

Ontology terms are stable public identifiers. Additions are minor
changes; renaming or removing a term is breaking and requires the
affected contract versions to bump (see `docs/contracts.md`).
