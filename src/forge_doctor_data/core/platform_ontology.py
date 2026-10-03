"""Deep platform ontology — abstract architectural semantics (spec 230).

``core/ontology.py`` is the documented *vocabulary*; this module is the
semantic model on top: what kind of platform a detected service is
(:class:`PlatformKind`), which workload the architecture serves
(:class:`WorkloadIntent`), how data is accessed
(:class:`DataAccessPattern`), physically designed
(:class:`PhysicalDesign`), materialized (:class:`MaterializationKind`),
moved (:class:`DataMovementMode`), owned (:class:`AssetOwnership`) and
retired (:class:`LifecycleState`).

Honesty contract (same as the capability engine): no mapping is ever
inferred from name similarity. ``implementation_for`` returns ``None``
for unknown platforms; ``logical_datasets`` groups only on explicit
``dataset_id`` evidence; ownership conflicts surface rather than resolve
silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


# ---------------------------------------------------------------------------
# PlatformKind — vendor-neutral implementation classes
# ---------------------------------------------------------------------------


class PlatformKind(Enum):
    """What *kind* of platform an implementation is — the class of problem
    it solves, not the vendor that sells it."""

    COMPUTE = "compute"
    WAREHOUSE = "warehouse"
    LAKEHOUSE = "lakehouse"
    OBJECT_STORAGE = "object_storage"
    TABLE_FORMAT = "table_format"
    STREAM = "stream"
    MESSAGE_BUS = "message_bus"
    ORCHESTRATOR = "orchestrator"
    TRANSFORMATION_ENGINE = "transformation_engine"
    QUERY_ENGINE = "query_engine"
    SEARCH_INDEX = "search_index"
    GRAPH_STORE = "graph_store"
    OPERATIONAL_STORE = "operational_store"
    CATALOG = "catalog"
    GOVERNANCE_PLANE = "governance_plane"
    METADATA_CATALOG = "metadata_catalog"
    QUALITY_SYSTEM = "quality_system"
    SERVING_LAYER = "serving_layer"
    OBSERVABILITY_SYSTEM = "observability_system"


_PLATFORM_KIND_DEFS: dict[PlatformKind, str] = {
    PlatformKind.COMPUTE: "General execution substrate (jobs, clusters, functions).",
    PlatformKind.WAREHOUSE: "Analytic warehouse (SQL, columnar, separated storage/compute).",
    PlatformKind.LAKEHOUSE: "Lake-storage analytic platform unifying tables, files, engines.",
    PlatformKind.OBJECT_STORAGE: "Durable object/blob storage (S3, ADLS, GCS).",
    PlatformKind.TABLE_FORMAT: "Table/layout contract over object storage (Iceberg, Delta).",
    PlatformKind.STREAM: "Ordered record streaming (Kinesis, Kafka, Event Hubs, Pub/Sub).",
    PlatformKind.MESSAGE_BUS: "Message delivery without ordered replay (SNS, SQS).",
    PlatformKind.ORCHESTRATOR: "Workflow scheduling/coordination (Airflow, Step Functions).",
    PlatformKind.TRANSFORMATION_ENGINE: "In-warehouse/declarative transformation (dbt).",
    PlatformKind.QUERY_ENGINE: "Federated/interactive SQL engine without storage (Trino).",
    PlatformKind.SEARCH_INDEX: "Inverted/vector search engine (OpenSearch, Elasticsearch).",
    PlatformKind.GRAPH_STORE: "Property-graph store (Neptune).",
    PlatformKind.OPERATIONAL_STORE: "Low-latency operational KV/document store (DynamoDB).",
    PlatformKind.CATALOG: "Technical metadata catalog (Glue Catalog, Data Catalog).",
    PlatformKind.GOVERNANCE_PLANE: "Access governance/policy plane (Lake Formation, Purview).",
    PlatformKind.METADATA_CATALOG: "Discovery/lineage metadata platform (DataHub, Dataplex).",
    PlatformKind.QUALITY_SYSTEM: "Data-quality expectations/gates (Deequ, GX, SodaCL).",
    PlatformKind.SERVING_LAYER: "Low-latency analytical serving (ClickHouse, Pinot, Druid).",
    PlatformKind.OBSERVABILITY_SYSTEM: "Pipeline/platform observability signals.",
}


# ---------------------------------------------------------------------------
# PlatformImplementation — concrete products mapped onto PlatformKind
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlatformImplementation:
    """A concrete product the analyzers may detect, with its neutral class.

    ``id`` is the stable identifier other models reference (matches the
    producer ``domain``/service strings already in use: ``glue``,
    ``snowflake``, ``pubsub``...). ``aliases`` map alternate spellings
    observed in config/code onto the same implementation.
    """

    id: str
    vendor: str  # aws | azure | gcp | snowflake | databricks | oss | ...
    product: str
    kind: PlatformKind
    capabilities: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    deployment_mode: str = ""  # serverless | managed | self_hosted | ""


_PLATFORM_REGISTRY: tuple[PlatformImplementation, ...] = (
    # -- AWS -------------------------------------------------------------
    PlatformImplementation(
        "athena",
        "aws",
        "Amazon Athena",
        PlatformKind.QUERY_ENGINE,
        aliases=("aws_athena",),
        deployment_mode="serverless",
    ),
    PlatformImplementation(
        "dynamodb",
        "aws",
        "Amazon DynamoDB",
        PlatformKind.OPERATIONAL_STORE,
        aliases=("aws_dynamodb_table",),
        deployment_mode="serverless",
    ),
    PlatformImplementation(
        "emr",
        "aws",
        "Amazon EMR",
        PlatformKind.COMPUTE,
        aliases=("aws_emr_cluster",),
        deployment_mode="managed",
    ),
    PlatformImplementation(
        "firehose", "aws", "Amazon Data Firehose", PlatformKind.STREAM, deployment_mode="serverless"
    ),
    PlatformImplementation(
        "glue",
        "aws",
        "AWS Glue",
        PlatformKind.COMPUTE,
        capabilities=("cataloging",),
        aliases=("aws_glue_job",),
        deployment_mode="serverless",
    ),
    PlatformImplementation(
        "kinesis",
        "aws",
        "Amazon Kinesis",
        PlatformKind.STREAM,
        aliases=("aws_kinesis_stream",),
        deployment_mode="managed",
    ),
    PlatformImplementation(
        "lakeformation",
        "aws",
        "AWS Lake Formation",
        PlatformKind.GOVERNANCE_PLANE,
        deployment_mode="managed",
    ),
    PlatformImplementation(
        "lambda",
        "aws",
        "AWS Lambda",
        PlatformKind.COMPUTE,
        aliases=("aws_lambda_function",),
        deployment_mode="serverless",
    ),
    PlatformImplementation(
        "msk",
        "aws",
        "Amazon MSK",
        PlatformKind.STREAM,
        aliases=("aws_msk_cluster",),
        deployment_mode="managed",
    ),
    PlatformImplementation(
        "neptune", "aws", "Amazon Neptune", PlatformKind.GRAPH_STORE, deployment_mode="managed"
    ),
    PlatformImplementation(
        "redshift",
        "aws",
        "Amazon Redshift",
        PlatformKind.WAREHOUSE,
        aliases=("aws_redshift_cluster", "aws_redshiftserverless_workgroup"),
    ),
    PlatformImplementation(
        "s3",
        "aws",
        "Amazon S3",
        PlatformKind.OBJECT_STORAGE,
        aliases=("aws_s3_bucket",),
        deployment_mode="serverless",
    ),
    PlatformImplementation(
        "sns", "aws", "Amazon SNS", PlatformKind.MESSAGE_BUS, deployment_mode="serverless"
    ),
    PlatformImplementation(
        "sqs", "aws", "Amazon SQS", PlatformKind.MESSAGE_BUS, deployment_mode="serverless"
    ),
    PlatformImplementation(
        "stepfunctions",
        "aws",
        "AWS Step Functions",
        PlatformKind.ORCHESTRATOR,
        aliases=("aws_sfn_state_machine",),
        deployment_mode="serverless",
    ),
    # -- Azure -----------------------------------------------------------
    PlatformImplementation(
        "adls_gen2",
        "azure",
        "Azure Data Lake Storage Gen2",
        PlatformKind.OBJECT_STORAGE,
        aliases=("azurerm_storage_data_lake_gen2_filesystem",),
    ),
    PlatformImplementation(
        "adf",
        "azure",
        "Azure Data Factory",
        PlatformKind.ORCHESTRATOR,
        aliases=("azurerm_data_factory",),
        deployment_mode="managed",
    ),
    PlatformImplementation(
        "cosmosdb",
        "azure",
        "Azure Cosmos DB",
        PlatformKind.OPERATIONAL_STORE,
        aliases=("azurerm_cosmosdb_account",),
        deployment_mode="managed",
    ),
    PlatformImplementation(
        "eventhubs",
        "azure",
        "Azure Event Hubs",
        PlatformKind.STREAM,
        aliases=("azurerm_eventhub", "azurerm_eventhub_namespace"),
    ),
    PlatformImplementation(
        "fabric", "azure", "Microsoft Fabric", PlatformKind.LAKEHOUSE, deployment_mode="managed"
    ),
    PlatformImplementation(
        "purview",
        "azure",
        "Microsoft Purview",
        PlatformKind.GOVERNANCE_PLANE,
        aliases=("azurerm_purview_account",),
    ),
    PlatformImplementation(
        "storage_account",
        "azure",
        "Azure Storage Account",
        PlatformKind.OBJECT_STORAGE,
        aliases=("azurerm_storage_account",),
    ),
    PlatformImplementation(
        "synapse",
        "azure",
        "Azure Synapse Analytics",
        PlatformKind.WAREHOUSE,
        aliases=("azurerm_synapse_workspace", "azurerm_synapse_sql_pool"),
    ),
    # -- GCP -------------------------------------------------------------
    PlatformImplementation(
        "bigquery",
        "gcp",
        "Google BigQuery",
        PlatformKind.WAREHOUSE,
        aliases=("google_bigquery_dataset",),
        deployment_mode="serverless",
    ),
    PlatformImplementation(
        "bigtable",
        "gcp",
        "Google Bigtable",
        PlatformKind.OPERATIONAL_STORE,
        aliases=("google_bigtable_instance",),
    ),
    PlatformImplementation(
        "composer",
        "gcp",
        "Cloud Composer",
        PlatformKind.ORCHESTRATOR,
        aliases=("google_composer_environment",),
    ),
    PlatformImplementation(
        "dataflow",
        "gcp",
        "Google Dataflow",
        PlatformKind.COMPUTE,
        aliases=("google_dataflow_job",),
        deployment_mode="managed",
    ),
    PlatformImplementation(
        "dataplex",
        "gcp",
        "Google Dataplex",
        PlatformKind.GOVERNANCE_PLANE,
        aliases=("google_dataplex_lake",),
    ),
    PlatformImplementation(
        "dataproc",
        "gcp",
        "Google Dataproc",
        PlatformKind.COMPUTE,
        aliases=("google_dataproc_cluster",),
    ),
    PlatformImplementation(
        "gcs",
        "gcp",
        "Google Cloud Storage",
        PlatformKind.OBJECT_STORAGE,
        aliases=("google_storage_bucket",),
        deployment_mode="serverless",
    ),
    PlatformImplementation(
        "datacatalog",
        "gcp",
        "Google Data Catalog",
        PlatformKind.METADATA_CATALOG,
        aliases=("google_data_catalog_entry_group",),
    ),
    PlatformImplementation(
        "pubsub",
        "gcp",
        "Google Pub/Sub",
        PlatformKind.STREAM,
        aliases=("google_pubsub_topic", "google_pubsub_subscription"),
        deployment_mode="serverless",
    ),
    # -- vendor / OSS ------------------------------------------------------
    PlatformImplementation(
        "airflow", "oss", "Apache Airflow", PlatformKind.ORCHESTRATOR, deployment_mode="self_hosted"
    ),
    PlatformImplementation(
        "clickhouse", "oss", "ClickHouse", PlatformKind.SERVING_LAYER, deployment_mode="self_hosted"
    ),
    PlatformImplementation("confluent", "confluent", "Confluent Platform", PlatformKind.STREAM),
    PlatformImplementation("controlm", "bmc", "Control-M", PlatformKind.ORCHESTRATOR),
    PlatformImplementation(
        "databricks",
        "databricks",
        "Databricks",
        PlatformKind.LAKEHOUSE,
        aliases=("databricks_workspace",),
        deployment_mode="managed",
    ),
    PlatformImplementation(
        "dbt",
        "oss",
        "dbt",
        PlatformKind.TRANSFORMATION_ENGINE,
        capabilities=("transformation", "testing"),
    ),
    PlatformImplementation("delta", "oss", "Delta Lake", PlatformKind.TABLE_FORMAT),
    PlatformImplementation("druid", "oss", "Apache Druid", PlatformKind.SERVING_LAYER),
    PlatformImplementation("elasticsearch", "elastic", "Elasticsearch", PlatformKind.SEARCH_INDEX),
    PlatformImplementation("flink", "oss", "Apache Flink", PlatformKind.COMPUTE),
    PlatformImplementation("iceberg", "oss", "Apache Iceberg", PlatformKind.TABLE_FORMAT),
    PlatformImplementation("kafka", "oss", "Apache Kafka", PlatformKind.STREAM),
    PlatformImplementation(
        "opensearch",
        "aws",
        "Amazon OpenSearch",
        PlatformKind.SEARCH_INDEX,
        deployment_mode="managed",
    ),
    PlatformImplementation("parquet", "oss", "Apache Parquet", PlatformKind.TABLE_FORMAT),
    PlatformImplementation("pinot", "oss", "Apache Pinot", PlatformKind.SERVING_LAYER),
    PlatformImplementation(
        "snowflake",
        "snowflake",
        "Snowflake",
        PlatformKind.WAREHOUSE,
        aliases=("snowflake_database", "snowflake_warehouse"),
        deployment_mode="managed",
    ),
    PlatformImplementation("spark", "oss", "Apache Spark", PlatformKind.COMPUTE),
    PlatformImplementation("spark_ss", "oss", "Spark Structured Streaming", PlatformKind.COMPUTE),
    PlatformImplementation(
        "trino", "oss", "Trino", PlatformKind.QUERY_ENGINE, capabilities=("federation",)
    ),
    PlatformImplementation("unity", "databricks", "Unity Catalog", PlatformKind.CATALOG),
    PlatformImplementation("datahub", "oss", "DataHub", PlatformKind.METADATA_CATALOG),
    PlatformImplementation("openmetadata", "oss", "OpenMetadata", PlatformKind.METADATA_CATALOG),
    PlatformImplementation("deequ", "oss", "AWS Deequ", PlatformKind.QUALITY_SYSTEM),
    PlatformImplementation(
        "great_expectations", "oss", "Great Expectations", PlatformKind.QUALITY_SYSTEM
    ),
    PlatformImplementation("sodacl", "oss", "SodaCL", PlatformKind.QUALITY_SYSTEM),
)


def _impl_index() -> dict[str, PlatformImplementation]:
    out: dict[str, PlatformImplementation] = {}
    for impl in _PLATFORM_REGISTRY:
        out[impl.id] = impl
        for alias in impl.aliases:
            out.setdefault(alias, impl)
    return out


def implementations() -> tuple[PlatformImplementation, ...]:
    return _PLATFORM_REGISTRY


def implementation_for(platform: str) -> PlatformImplementation | None:
    """Resolve a platform id or alias; ``None`` = unknown, never guessed."""
    return _impl_index().get(platform.strip().lower())


def platform_kind(platform: str) -> PlatformKind | None:
    impl = implementation_for(platform)
    return impl.kind if impl else None


# ---------------------------------------------------------------------------
# WorkloadIntent / DataAccessPattern — what the architecture serves
# ---------------------------------------------------------------------------


class WorkloadIntent(Enum):
    BATCH_ANALYTICS = "batch_analytics"
    INTERACTIVE_SQL = "interactive_sql"
    REALTIME_SERVING = "realtime_serving"
    EVENT_DRIVEN = "event_driven"
    TRANSFORMATION = "transformation"
    ORCHESTRATION = "orchestration"
    ML_PIPELINE = "ml_pipeline"
    GOVERNANCE = "governance"
    OBSERVABILITY = "observability"
    SEARCH_ANALYTICS = "search_analytics"
    VECTOR_SEARCH = "vector_search"
    GRAPH_ANALYTICS = "graph_analytics"


_WORKLOAD_DEFS: dict[WorkloadIntent, str] = {
    WorkloadIntent.BATCH_ANALYTICS: "Scheduled large-scale analytical scans/aggregations.",
    WorkloadIntent.INTERACTIVE_SQL: "Human-driven ad-hoc SQL over warehouses/lakes.",
    WorkloadIntent.REALTIME_SERVING: "Low-latency queries serving applications/dashboards.",
    WorkloadIntent.EVENT_DRIVEN: "Records processed as they arrive (streams, triggers).",
    WorkloadIntent.TRANSFORMATION: "Declarative/modelled data reshaping (dbt, SQL).",
    WorkloadIntent.ORCHESTRATION: "Coordinating pipelines, dependencies, schedules.",
    WorkloadIntent.ML_PIPELINE: "Feature/model pipelines and training workflows.",
    WorkloadIntent.GOVERNANCE: "Access policy, classification, compliance, audit.",
    WorkloadIntent.OBSERVABILITY: "Lineage, quality, freshness, runtime telemetry.",
    WorkloadIntent.SEARCH_ANALYTICS: "Full-text search and log/document analytics.",
    WorkloadIntent.VECTOR_SEARCH: "Similarity search over embeddings (kNN/ANN).",
    WorkloadIntent.GRAPH_ANALYTICS: "Traversal and pattern queries over property graphs.",
}


class DataAccessPattern(Enum):
    POINT_LOOKUP = "point_lookup"
    KEY_RANGE = "key_range"
    FULL_SCAN = "full_scan"
    FILTERED_SCAN = "filtered_scan"
    AGGREGATE_SCAN = "aggregate_scan"
    TIME_SERIES = "time_series"
    GRAPH_TRAVERSAL = "graph_traversal"
    FULL_TEXT = "full_text"
    VECTOR_SIMILARITY = "vector_similarity"
    MUTATION_HEAVY = "mutation_heavy"
    STREAMING_APPEND = "streaming_append"
    RANDOM_READ = "random_read"


_ACCESS_DEFS: dict[DataAccessPattern, str] = {
    DataAccessPattern.POINT_LOOKUP: "Single-record reads by key (DynamoDB GetItem).",
    DataAccessPattern.KEY_RANGE: "Bounded key-range reads (sort key, partition range).",
    DataAccessPattern.FULL_SCAN: "Unfiltered whole-dataset scans.",
    DataAccessPattern.FILTERED_SCAN: "Scans pruned by predicates/partitions.",
    DataAccessPattern.AGGREGATE_SCAN: "Rollups/aggregations over many rows.",
    DataAccessPattern.TIME_SERIES: "Append-ordered time-bucketed reads.",
    DataAccessPattern.GRAPH_TRAVERSAL: "Multi-hop edge traversal.",
    DataAccessPattern.FULL_TEXT: "Term/phrase search over tokenized fields.",
    DataAccessPattern.VECTOR_SIMILARITY: "Nearest-neighbour over vector embeddings.",
    DataAccessPattern.MUTATION_HEAVY: "High write/update/delete rate workloads.",
    DataAccessPattern.STREAMING_APPEND: "Continuous record ingestion.",
    DataAccessPattern.RANDOM_READ: "Unordered sparse reads (no key locality).",
}


# Which platform kinds can serve each workload — used by workload→capability
# mapping (spec 231) and by `ontology workloads` output.
_WORKLOAD_KINDS: dict[WorkloadIntent, tuple[PlatformKind, ...]] = {
    WorkloadIntent.BATCH_ANALYTICS: (
        PlatformKind.WAREHOUSE,
        PlatformKind.LAKEHOUSE,
        PlatformKind.COMPUTE,
    ),
    WorkloadIntent.INTERACTIVE_SQL: (
        PlatformKind.WAREHOUSE,
        PlatformKind.QUERY_ENGINE,
        PlatformKind.LAKEHOUSE,
    ),
    WorkloadIntent.REALTIME_SERVING: (
        PlatformKind.SERVING_LAYER,
        PlatformKind.OPERATIONAL_STORE,
        PlatformKind.SEARCH_INDEX,
    ),
    WorkloadIntent.EVENT_DRIVEN: (
        PlatformKind.STREAM,
        PlatformKind.MESSAGE_BUS,
        PlatformKind.COMPUTE,
    ),
    WorkloadIntent.TRANSFORMATION: (
        PlatformKind.TRANSFORMATION_ENGINE,
        PlatformKind.COMPUTE,
    ),
    WorkloadIntent.ORCHESTRATION: (PlatformKind.ORCHESTRATOR,),
    WorkloadIntent.ML_PIPELINE: (
        PlatformKind.COMPUTE,
        PlatformKind.LAKEHOUSE,
    ),
    WorkloadIntent.GOVERNANCE: (
        PlatformKind.GOVERNANCE_PLANE,
        PlatformKind.CATALOG,
        PlatformKind.METADATA_CATALOG,
    ),
    WorkloadIntent.OBSERVABILITY: (
        PlatformKind.OBSERVABILITY_SYSTEM,
        PlatformKind.METADATA_CATALOG,
        PlatformKind.QUALITY_SYSTEM,
    ),
    WorkloadIntent.SEARCH_ANALYTICS: (PlatformKind.SEARCH_INDEX,),
    WorkloadIntent.VECTOR_SEARCH: (
        PlatformKind.SEARCH_INDEX,
        PlatformKind.OPERATIONAL_STORE,
    ),
    WorkloadIntent.GRAPH_ANALYTICS: (PlatformKind.GRAPH_STORE,),
}


def workload_kinds(intent: WorkloadIntent) -> tuple[PlatformKind, ...]:
    return _WORKLOAD_KINDS.get(intent, ())


# ---------------------------------------------------------------------------
# PhysicalDesign — how the platform physically lays data out
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhysicalDesign:
    """Physical layout mechanisms a platform exposes (declared features,
    not observed config). Each field lists mechanism names the platform
    supports — empty means "no such mechanism declared"."""

    partitioning: tuple[str, ...] = ()
    clustering: tuple[str, ...] = ()
    ordering: tuple[str, ...] = ()
    distribution: tuple[str, ...] = ()
    sharding: tuple[str, ...] = ()
    replication: tuple[str, ...] = ()
    indexing: tuple[str, ...] = ()
    materialization: tuple[str, ...] = ()
    caching: tuple[str, ...] = ()
    retention: tuple[str, ...] = ()


_PHYSICAL_DESIGN: dict[str, PhysicalDesign] = {
    "bigquery": PhysicalDesign(
        partitioning=("ingestion_time", "column", "range", "integer_range"),
        clustering=("clustering_columns",),
        materialization=("materialized_view",),
        caching=("query_result_cache",),
        retention=("partition_expiration", "table_expiration"),
    ),
    "clickhouse": PhysicalDesign(
        partitioning=("partition_by",),
        ordering=("order_by", "primary_key"),
        sharding=("distributed_shard",),
        replication=("replicated_merge_tree",),
        indexing=("sparse_index", "skip_index", "bloom_filter"),
        materialization=("materialized_view", "projection"),
        retention=("ttl",),
    ),
    "dynamodb": PhysicalDesign(
        partitioning=("partition_key", "sort_key"),
        indexing=("gsi", "lsi"),
        replication=("global_tables",),
        caching=("dax",),
        retention=("ttl",),
    ),
    "opensearch": PhysicalDesign(
        sharding=("primary_shards", "replica_shards"),
        replication=("replicas",),
        indexing=("inverted_index", "doc_values", "knn_vector"),
        materialization=("index", "rollup_index"),
        retention=("ism_policy",),
    ),
    "redshift": PhysicalDesign(
        distribution=("distkey", "diststyle_all", "diststyle_even", "auto"),
        ordering=("compound_sortkey", "interleaved_sortkey"),
        materialization=("materialized_view",),
        caching=("result_cache",),
        retention=("snapshot_retention",),
    ),
    "snowflake": PhysicalDesign(
        clustering=("clustering_key", "auto_clustering"),
        materialization=("materialized_view", "dynamic_table"),
        caching=("result_cache", "warehouse_cache", "metadata_cache"),
        retention=("time_travel", "fail_safe"),
        replication=("database_replication", "account_replication"),
    ),
}


def physical_design(platform: str) -> PhysicalDesign | None:
    """Declared physical-design mechanisms for a platform; ``None`` when
    the platform is unknown or no design model is bundled."""
    return _PHYSICAL_DESIGN.get(platform.strip().lower())


# ---------------------------------------------------------------------------
# MaterializationKind — what a stored/derived thing physically is
# ---------------------------------------------------------------------------


class MaterializationKind(Enum):
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"
    INCREMENTAL_TABLE = "incremental_table"
    COPY = "copy"
    CACHE = "cache"
    PROJECTION = "projection"
    SEARCH_INDEX = "search_index"
    REPLICA = "replica"


_MATERIALIZATION_DEFS: dict[MaterializationKind, str] = {
    MaterializationKind.VIEW: "Logical query definition; computed at read time.",
    MaterializationKind.MATERIALIZED_VIEW: "Stored query result refreshed on a policy.",
    MaterializationKind.INCREMENTAL_TABLE: (
        "Table maintained by incremental apply (dbt incremental, dynamic tables)."
    ),
    MaterializationKind.COPY: "Materialized one-time physical copy.",
    MaterializationKind.CACHE: "Derived data held for latency (result/DAX/warehouse cache).",
    MaterializationKind.PROJECTION: (
        "Alternate physical layout of the same data (ClickHouse projection)."
    ),
    MaterializationKind.SEARCH_INDEX: "Tokenized/vector index derived from source data.",
    MaterializationKind.REPLICA: "Physical copy kept in sync (global tables, replication).",
}


# (platform, mechanism) -> kind; mechanism strings are the ones analyzers
# already see (dbt materialization names, service features).
_MATERIALIZATION_MAP: dict[tuple[str, str], MaterializationKind] = {
    ("dbt", "view"): MaterializationKind.VIEW,
    ("dbt", "materialized_view"): MaterializationKind.MATERIALIZED_VIEW,
    ("dbt", "incremental"): MaterializationKind.INCREMENTAL_TABLE,
    ("dbt", "ephemeral"): MaterializationKind.VIEW,
    ("snowflake", "dynamic_table"): MaterializationKind.MATERIALIZED_VIEW,
    ("snowflake", "materialized_view"): MaterializationKind.MATERIALIZED_VIEW,
    ("snowflake", "result_cache"): MaterializationKind.CACHE,
    ("bigquery", "materialized_view"): MaterializationKind.MATERIALIZED_VIEW,
    ("bigquery", "query_result_cache"): MaterializationKind.CACHE,
    ("redshift", "materialized_view"): MaterializationKind.MATERIALIZED_VIEW,
    ("redshift", "result_cache"): MaterializationKind.CACHE,
    ("clickhouse", "materialized_view"): MaterializationKind.MATERIALIZED_VIEW,
    ("clickhouse", "projection"): MaterializationKind.PROJECTION,
    ("clickhouse", "ttl"): MaterializationKind.COPY,
    ("opensearch", "index"): MaterializationKind.SEARCH_INDEX,
    ("opensearch", "rollup_index"): MaterializationKind.SEARCH_INDEX,
    ("elasticsearch", "index"): MaterializationKind.SEARCH_INDEX,
    ("dynamodb", "global_tables"): MaterializationKind.REPLICA,
    ("dynamodb", "dax"): MaterializationKind.CACHE,
    ("kafka", "replica"): MaterializationKind.REPLICA,
    ("iceberg", "snapshot"): MaterializationKind.COPY,
}


def materialization_kind(platform: str, mechanism: str) -> MaterializationKind | None:
    """Map a platform's mechanism name to a neutral kind; ``None`` unknown."""
    return _MATERIALIZATION_MAP.get((platform.strip().lower(), mechanism.strip().lower()))


# ---------------------------------------------------------------------------
# ConsistencyModel
# ---------------------------------------------------------------------------


class ConsistencyModel(Enum):
    STRONG = "strong"
    EVENTUAL = "eventual"
    READ_AFTER_WRITE = "read_after_write"
    SNAPSHOT = "snapshot"
    REGION_LOCAL = "region_local"
    MULTI_REGION = "multi_region"
    UNKNOWN = "unknown"


_CONSISTENCY_DEFS: dict[ConsistencyModel, str] = {
    ConsistencyModel.STRONG: "Every read sees the latest committed write.",
    ConsistencyModel.EVENTUAL: "Reads may lag writes; replicas converge over time.",
    ConsistencyModel.READ_AFTER_WRITE: "Own writes are immediately visible; others may lag.",
    ConsistencyModel.SNAPSHOT: "Reads observe a consistent point-in-time snapshot.",
    ConsistencyModel.REGION_LOCAL: "Consistency guaranteed within a region only.",
    ConsistencyModel.MULTI_REGION: "Consistency guaranteed across regions.",
    ConsistencyModel.UNKNOWN: "No evidence — never inferred.",
}


# Conservative defaults — where the product's documented default is
# unambiguous. Anything else is UNKNOWN; callers may refine with evidence
# (e.g. a consistent-read flag observed in code).
_CONSISTENCY_DEFAULTS: dict[str, ConsistencyModel] = {
    "dynamodb": ConsistencyModel.EVENTUAL,  # default reads; strong is opt-in
    "snowflake": ConsistencyModel.STRONG,
    "bigquery": ConsistencyModel.STRONG,
    "redshift": ConsistencyModel.STRONG,
    "s3": ConsistencyModel.STRONG,  # strong read-after-write since 2020
    "gcs": ConsistencyModel.STRONG,
    "opensearch": ConsistencyModel.READ_AFTER_WRITE,  # refresh-interval bound
    "elasticsearch": ConsistencyModel.READ_AFTER_WRITE,
    "kafka": ConsistencyModel.EVENTUAL,  # consumer-group dependent
    "pubsub": ConsistencyModel.EVENTUAL,
    "kinesis": ConsistencyModel.EVENTUAL,
    "eventhubs": ConsistencyModel.EVENTUAL,
    "cosmosdb": ConsistencyModel.EVENTUAL,  # session default varies; conservative
}


def consistency_for(platform: str, evidence: str = "") -> ConsistencyModel:
    """Consistency model with an explicit evidence string, else the
    documented default, else UNKNOWN — never invented."""
    if evidence.strip().lower() in {m.value for m in ConsistencyModel}:
        return ConsistencyModel(evidence.strip().lower())
    return _CONSISTENCY_DEFAULTS.get(platform.strip().lower(), ConsistencyModel.UNKNOWN)


# ---------------------------------------------------------------------------
# ServingModel — representing serving platforms without flattening to table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServingModel:
    """What "serving" means for a platform — latency/freshness targets are
    strings (``"ms"``, ``"seconds"``) because they are *declared shapes*,
    not measured guarantees."""

    latency_target: str = ""
    freshness_target: str = ""
    availability_target: str = ""
    consistency: ConsistencyModel = ConsistencyModel.UNKNOWN
    query_pattern: DataAccessPattern | None = None
    workload_intent: WorkloadIntent | None = None


_SERVING_MODELS: dict[str, ServingModel] = {
    "clickhouse": ServingModel(
        latency_target="ms",
        freshness_target="seconds",
        consistency=ConsistencyModel.EVENTUAL,
        query_pattern=DataAccessPattern.AGGREGATE_SCAN,
        workload_intent=WorkloadIntent.REALTIME_SERVING,
    ),
    "druid": ServingModel(
        latency_target="ms",
        freshness_target="seconds",
        consistency=ConsistencyModel.EVENTUAL,
        query_pattern=DataAccessPattern.TIME_SERIES,
        workload_intent=WorkloadIntent.REALTIME_SERVING,
    ),
    "pinot": ServingModel(
        latency_target="ms",
        freshness_target="seconds",
        consistency=ConsistencyModel.EVENTUAL,
        query_pattern=DataAccessPattern.AGGREGATE_SCAN,
        workload_intent=WorkloadIntent.REALTIME_SERVING,
    ),
    "dynamodb": ServingModel(
        latency_target="ms",
        freshness_target="immediate",
        consistency=ConsistencyModel.EVENTUAL,
        query_pattern=DataAccessPattern.POINT_LOOKUP,
        workload_intent=WorkloadIntent.REALTIME_SERVING,
    ),
    "opensearch": ServingModel(
        latency_target="ms",
        freshness_target="seconds",
        consistency=ConsistencyModel.READ_AFTER_WRITE,
        query_pattern=DataAccessPattern.FULL_TEXT,
        workload_intent=WorkloadIntent.SEARCH_ANALYTICS,
    ),
    "elasticsearch": ServingModel(
        latency_target="ms",
        freshness_target="seconds",
        consistency=ConsistencyModel.READ_AFTER_WRITE,
        query_pattern=DataAccessPattern.FULL_TEXT,
        workload_intent=WorkloadIntent.SEARCH_ANALYTICS,
    ),
}


def serving_model(platform: str) -> ServingModel | None:
    return _SERVING_MODELS.get(platform.strip().lower())


# ---------------------------------------------------------------------------
# DataMovement — sharing vs replication vs copy are never conflated
# ---------------------------------------------------------------------------


class DataMovementMode(Enum):
    SHARE = "share"  # zero-copy access grant (Snowflake share, Fabric shortcut)
    REPLICATE = "replicate"  # physical copy kept in sync (global tables, replication)
    COPY = "copy"  # one-time physical copy (snapshot export/load)
    FEDERATE = "federate"  # remote query, data never moves (Trino external catalog)
    MIRROR = "mirror"  # continuous byte/log-level mirroring (DR mirror)
    STREAM = "stream"  # record-level continuous delivery (CDC, firehose→s3)


_MOVEMENT_DEFS: dict[DataMovementMode, str] = {
    DataMovementMode.SHARE: "Consumer reads producer's storage in place — no copy exists.",
    DataMovementMode.REPLICATE: "A physical copy exists and is kept in sync continuously.",
    DataMovementMode.COPY: "A one-time physical copy; source changes do not propagate.",
    DataMovementMode.FEDERATE: "Queries run remotely; results move, data does not.",
    DataMovementMode.MIRROR: "Low-level byte/log mirroring for DR or failover.",
    DataMovementMode.STREAM: "Record stream delivered to a target as it is produced.",
}


# mechanism tokens observed in config/code -> mode. Deliberately explicit:
# anything unmapped stays unmapped (caller sees no DataMovement).
_MOVEMENT_MAP: dict[str, DataMovementMode] = {
    "snowflake_share": DataMovementMode.SHARE,
    "fabric_shortcut": DataMovementMode.SHARE,
    "zero_copy_clone": DataMovementMode.SHARE,
    "cross_region_replication": DataMovementMode.REPLICATE,
    "global_tables": DataMovementMode.REPLICATE,
    "read_replica": DataMovementMode.REPLICATE,
    "s3_replication": DataMovementMode.REPLICATE,
    "gcs_replication": DataMovementMode.REPLICATE,
    "snapshot_copy": DataMovementMode.COPY,
    "copy_command": DataMovementMode.COPY,
    "export_import": DataMovementMode.COPY,
    "external_table": DataMovementMode.FEDERATE,
    "trino_catalog": DataMovementMode.FEDERATE,
    "federated_query": DataMovementMode.FEDERATE,
    "biglake": DataMovementMode.FEDERATE,
    "dr_mirror": DataMovementMode.MIRROR,
    "block_public_mirror": DataMovementMode.MIRROR,
    "cdc_stream": DataMovementMode.STREAM,
    "firehose_delivery": DataMovementMode.STREAM,
    "pubsub_subscription": DataMovementMode.STREAM,
    "msk_replication": DataMovementMode.MIRROR,
}


def movement_mode(mechanism: str) -> DataMovementMode | None:
    return _MOVEMENT_MAP.get(mechanism.strip().lower())


@dataclass(frozen=True)
class DataMovement:
    """One observed data-movement edge with its semantic mode."""

    mode: DataMovementMode
    source: str
    target: str
    mechanism: str = ""
    evidence: str = ""  # file/attribute that establishes the movement

    @property
    def creates_copy(self) -> bool:
        """True only when a physical copy is produced (COPY/REPLICATE/MIRROR).
        SHARE and FEDERATE move no bytes; STREAM moves records but does not
        keep a synced copy."""
        return self.mode in {
            DataMovementMode.REPLICATE,
            DataMovementMode.COPY,
            DataMovementMode.MIRROR,
        }


# ---------------------------------------------------------------------------
# LogicalDataset / PhysicalRepresentation — unify only on explicit evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogicalDataset:
    """A dataset identity independent of where it is physically stored."""

    id: str
    name: str = ""
    domain: str = ""


@dataclass(frozen=True)
class PhysicalRepresentation:
    """One physical home of (possibly) a logical dataset.

    ``dataset_id`` is the *only* join key to a :class:`LogicalDataset`.
    Empty means unassigned — the model never infers membership from name
    similarity across platforms.
    """

    platform: str
    identifier: str
    kind: str = ""  # table | index | stream | file | ...
    dataset_id: str = ""
    materialization: MaterializationKind | None = None
    physical_design: tuple[str, ...] = ()  # mechanism names actually configured
    consistency: ConsistencyModel = ConsistencyModel.UNKNOWN
    evidence: str = ""


@dataclass(frozen=True)
class DatasetBinding:
    """Result of unification: a logical dataset plus the representations
    explicitly bound to it."""

    dataset: LogicalDataset
    representations: tuple[PhysicalRepresentation, ...]


def logical_datasets(
    datasets: Iterable[LogicalDataset],
    representations: Iterable[PhysicalRepresentation],
) -> tuple[DatasetBinding, ...]:
    """Bind representations to datasets via explicit ``dataset_id`` only.

    Representations naming a dataset that does not exist are ignored
    (dangling reference, reported separately by callers that care);
    datasets with no representations still appear (empty binding).
    Output is deterministic: sorted by dataset id then representation
    (platform, identifier).
    """
    by_id = {d.id: d for d in datasets}
    grouped: dict[str, list[PhysicalRepresentation]] = {d.id: [] for d in by_id.values()}
    for rep in representations:
        if rep.dataset_id and rep.dataset_id in by_id:
            grouped[rep.dataset_id].append(rep)
    return tuple(
        DatasetBinding(
            dataset=ds,
            representations=tuple(sorted(grouped[ds.id], key=lambda r: (r.platform, r.identifier))),
        )
        for ds in sorted(by_id.values(), key=lambda d: d.id)
    )


def unassigned_representations(
    datasets: Iterable[LogicalDataset],
    representations: Iterable[PhysicalRepresentation],
) -> tuple[PhysicalRepresentation, ...]:
    """Representations not bound to any declared dataset — visible, not
    silently dropped."""
    known = {d.id for d in datasets}
    return tuple(
        sorted(
            (r for r in representations if not r.dataset_id or r.dataset_id not in known),
            key=lambda r: (r.platform, r.identifier),
        )
    )


# ---------------------------------------------------------------------------
# AssetOwnership — declared teams, sources, and conflicts
# ---------------------------------------------------------------------------


class OwnershipSource(Enum):
    PLATFORM_CONTRACT = "platform_contract"
    TERRAFORM_TAGS = "terraform_tags"
    DBT_META = "dbt_meta"
    DATAHUB = "datahub"
    OPENMETADATA = "openmetadata"
    CODEOWNERS = "codeowners"
    UNKNOWN = "unknown"


_OWNERSHIP_SOURCE_DEFS: dict[OwnershipSource, str] = {
    OwnershipSource.PLATFORM_CONTRACT: "Declared in a platform-contract.* file.",
    OwnershipSource.TERRAFORM_TAGS: "Resource tags/labels in IaC.",
    OwnershipSource.DBT_META: "``meta:`` block on a dbt model/source.",
    OwnershipSource.DATAHUB: "DataHub ownership assertion (committed export).",
    OwnershipSource.OPENMETADATA: "OpenMetadata ownership assertion (committed export).",
    OwnershipSource.CODEOWNERS: "CODEOWNERS path rule.",
    OwnershipSource.UNKNOWN: "No attributable source.",
}


@dataclass(frozen=True)
class OwnershipClaim:
    """One observed ownership assertion."""

    team: str
    source: OwnershipSource
    evidence: str = ""


@dataclass(frozen=True)
class AssetOwnership:
    """Resolved ownership for an asset.

    ``confidence``: ``declared`` (one authoritative claim or several
    agreeing claims), ``conflicting`` (claims disagree — all surface in
    ``conflicts``), ``none`` (no claims)."""

    team: str = ""
    source: OwnershipSource = OwnershipSource.UNKNOWN
    declared_by: str = ""
    confidence: str = "none"  # declared | conflicting | none
    conflicts: tuple[OwnershipClaim, ...] = ()


def resolve_ownership(claims: Iterable[OwnershipClaim]) -> AssetOwnership:
    """Aggregate claims deterministically.

    A single claim (or several agreeing on the same team) wins. Two or
    more *distinct* teams are a conflict — surfaced verbatim, never
    resolved by precedence games.
    """
    ordered = sorted(set(claims), key=lambda c: (c.source.value, c.team, c.evidence))
    teams = {c.team for c in ordered}
    if not ordered:
        return AssetOwnership()
    if len(teams) == 1:
        first = ordered[0]
        return AssetOwnership(
            team=first.team,
            source=first.source,
            declared_by=first.source.value,
            confidence="declared",
        )
    return AssetOwnership(confidence="conflicting", conflicts=tuple(ordered))


# ---------------------------------------------------------------------------
# LifecycleModel — created → retained → archived/compacted/expired/deleted
# ---------------------------------------------------------------------------


class LifecycleState(Enum):
    CREATED = "created"
    RETAINED = "retained"
    ARCHIVED = "archived"
    COMPACTED = "compacted"
    SNAPSHOTTED = "snapshotted"
    EXPIRED = "expired"
    DELETED = "deleted"


_LIFECYCLE_DEFS: dict[LifecycleState, str] = {
    LifecycleState.CREATED: "Object exists; no retention policy observed yet.",
    LifecycleState.RETAINED: "Held under an explicit retention policy/window.",
    LifecycleState.ARCHIVED: "Moved to cold/archive storage class.",
    LifecycleState.COMPACTED: "Small files/segments merged (table maintenance).",
    LifecycleState.SNAPSHOTTED: "Point-in-time snapshots taken/retained.",
    LifecycleState.EXPIRED: "Marked for automatic expiry (TTL/partition expiration).",
    LifecycleState.DELETED: "Removed/scheduled for removal.",
}


# mechanism tokens -> implied state (evidence-backed, not inferred)
_LIFECYCLE_MAP: dict[str, LifecycleState] = {
    "iceberg_expire_snapshots": LifecycleState.EXPIRED,
    "iceberg_compaction": LifecycleState.COMPACTED,
    "delta_optimize": LifecycleState.COMPACTED,
    "delta_vacuum": LifecycleState.EXPIRED,
    "opensearch_ism": LifecycleState.RETAINED,
    "clickhouse_ttl": LifecycleState.EXPIRED,
    "bigquery_partition_expiration": LifecycleState.EXPIRED,
    "bigquery_table_expiration": LifecycleState.EXPIRED,
    "snowflake_time_travel": LifecycleState.RETAINED,
    "snowflake_fail_safe": LifecycleState.ARCHIVED,
    "s3_lifecycle_glacier": LifecycleState.ARCHIVED,
    "s3_lifecycle_expiration": LifecycleState.EXPIRED,
    "dynamodb_ttl": LifecycleState.EXPIRED,
    "kafka_retention": LifecycleState.RETAINED,
    "pubsub_retention": LifecycleState.RETAINED,
}


@dataclass(frozen=True)
class LifecycleModel:
    """Observed lifecycle position for one object."""

    state: LifecycleState
    mechanism: str = ""  # e.g. "opensearch_ism"
    policy: str = ""  # e.g. "90 days" / "hot 7d -> delete"
    evidence: str = ""


def lifecycle_for(mechanism: str, policy: str = "", evidence: str = "") -> LifecycleModel | None:
    """Map an observed lifecycle mechanism to a state; ``None`` unknown."""
    state = _LIFECYCLE_MAP.get(mechanism.strip().lower())
    if state is None:
        return None
    return LifecycleModel(state=state, mechanism=mechanism, policy=policy, evidence=evidence)


# ---------------------------------------------------------------------------
# Public vocabulary surface — merged into ontology.vocabulary()
# ---------------------------------------------------------------------------


def platform_kinds() -> tuple[tuple[str, str], ...]:
    return tuple((k.value, _PLATFORM_KIND_DEFS[k]) for k in PlatformKind)


def workload_intents() -> tuple[tuple[str, str], ...]:
    return tuple((w.value, _WORKLOAD_DEFS[w]) for w in WorkloadIntent)


def access_patterns() -> tuple[tuple[str, str], ...]:
    return tuple((p.value, _ACCESS_DEFS[p]) for p in DataAccessPattern)


def materialization_kinds() -> tuple[tuple[str, str], ...]:
    return tuple((k.value, _MATERIALIZATION_DEFS[k]) for k in MaterializationKind)


def consistency_models() -> tuple[tuple[str, str], ...]:
    return tuple((m.value, _CONSISTENCY_DEFS[m]) for m in ConsistencyModel)


def data_movement_modes() -> tuple[tuple[str, str], ...]:
    return tuple((m.value, _MOVEMENT_DEFS[m]) for m in DataMovementMode)


def lifecycle_states() -> tuple[tuple[str, str], ...]:
    return tuple((s.value, _LIFECYCLE_DEFS[s]) for s in LifecycleState)


def ownership_sources() -> tuple[tuple[str, str], ...]:
    return tuple((s.value, _OWNERSHIP_SOURCE_DEFS[s]) for s in OwnershipSource)


def semantic_vocabulary() -> dict[str, list[dict[str, str]]]:
    """Deterministic JSON-serializable view of the semantic model."""
    return {
        "consistency_models": [{"name": n, "definition": d} for n, d in consistency_models()],
        "data_access_patterns": [{"name": n, "definition": d} for n, d in access_patterns()],
        "data_movement_modes": [{"name": n, "definition": d} for n, d in data_movement_modes()],
        "lifecycle_states": [{"name": n, "definition": d} for n, d in lifecycle_states()],
        "materialization_kinds": [{"name": n, "definition": d} for n, d in materialization_kinds()],
        "ownership_sources": [{"name": n, "definition": d} for n, d in ownership_sources()],
        "platform_kinds": [{"name": n, "definition": d} for n, d in platform_kinds()],
        "workload_intents": [{"name": n, "definition": d} for n, d in workload_intents()],
    }


def validate_implementations() -> list[str]:
    """Conformance issues in the bundled registry (deterministic, sorted)."""
    violations: set[str] = set()
    seen: set[str] = set()
    for impl in _PLATFORM_REGISTRY:
        if impl.id in seen:
            violations.add(f"duplicate platform id {impl.id!r}")
        seen.add(impl.id)
        if not impl.vendor or not impl.product:
            violations.add(f"platform {impl.id!r}: missing vendor/product")
        for alias in impl.aliases:
            if alias in seen:
                violations.add(f"platform {impl.id!r}: alias {alias!r} collides with an id")
    # every registered kind must be reachable from the registry
    return sorted(violations)
