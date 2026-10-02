"""Deep platform ontology: vendor mapping, semantics, CLI (spec 230)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from forge_doctor.cli.app import app
from forge_doctor.core.ontology import vocabulary
from forge_doctor.core.platform_ontology import (
    ConsistencyModel,
    DataAccessPattern,
    DataMovement,
    DataMovementMode,
    LifecycleState,
    LogicalDataset,
    MaterializationKind,
    OwnershipClaim,
    OwnershipSource,
    PhysicalRepresentation,
    PlatformKind,
    WorkloadIntent,
    access_patterns,
    consistency_for,
    data_movement_modes,
    implementation_for,
    implementations,
    lifecycle_for,
    logical_datasets,
    materialization_kind,
    materialization_kinds,
    movement_mode,
    physical_design,
    platform_kind,
    platform_kinds,
    resolve_ownership,
    semantic_vocabulary,
    serving_model,
    unassigned_representations,
    validate_implementations,
    workload_intents,
    workload_kinds,
)

runner = CliRunner()


# --- platform implementation registry ----------------------------------------


def test_registry_covers_known_platforms() -> None:
    cases = {
        "glue": PlatformKind.COMPUTE,
        "snowflake": PlatformKind.WAREHOUSE,
        "bigquery": PlatformKind.WAREHOUSE,
        "redshift": PlatformKind.WAREHOUSE,
        "trino": PlatformKind.QUERY_ENGINE,
        "opensearch": PlatformKind.SEARCH_INDEX,
        "dynamodb": PlatformKind.OPERATIONAL_STORE,
        "neptune": PlatformKind.GRAPH_STORE,
        "dbt": PlatformKind.TRANSFORMATION_ENGINE,
        "iceberg": PlatformKind.TABLE_FORMAT,
        "kafka": PlatformKind.STREAM,
        "pubsub": PlatformKind.STREAM,
        "eventhubs": PlatformKind.STREAM,
        "adls_gen2": PlatformKind.OBJECT_STORAGE,
        "gcs": PlatformKind.OBJECT_STORAGE,
        "synapse": PlatformKind.WAREHOUSE,
        "fabric": PlatformKind.LAKEHOUSE,
        "dataflow": PlatformKind.COMPUTE,
        "dataproc": PlatformKind.COMPUTE,
        "purview": PlatformKind.GOVERNANCE_PLANE,
        "dataplex": PlatformKind.GOVERNANCE_PLANE,
        "airflow": PlatformKind.ORCHESTRATOR,
        "clickhouse": PlatformKind.SERVING_LAYER,
    }
    for platform, expected in cases.items():
        impl = implementation_for(platform)
        assert impl is not None, platform
        assert impl.kind is expected, (platform, impl.kind)


def test_aliases_resolve_to_same_implementation() -> None:
    assert implementation_for("aws_glue_job") is implementation_for("glue")
    assert implementation_for("google_pubsub_topic") is implementation_for("pubsub")
    assert implementation_for("azurerm_synapse_sql_pool") is implementation_for("synapse")


def test_unknown_vendor_returns_none() -> None:
    assert implementation_for("asdf-cloud-db") is None
    assert platform_kind("asdf-cloud-db") is None
    assert physical_design("asdf-cloud-db") is None
    assert serving_model("asdf-cloud-db") is None


def test_registry_is_self_consistent() -> None:
    assert validate_implementations() == []
    assert implementations() == tuple(dict.fromkeys(implementations()))


def test_every_platform_kind_defined() -> None:
    names = {n for n, _ in platform_kinds()}
    assert names == {k.value for k in PlatformKind}
    assert all(d for _, d in platform_kinds())


# --- workload / access patterns ------------------------------------------------


def test_workload_intents_complete() -> None:
    names = {n for n, _ in workload_intents()}
    assert names == {w.value for w in WorkloadIntent}
    assert len(names) == 12


def test_access_patterns_complete() -> None:
    assert len(DataAccessPattern) == 12
    assert all(d for _, d in access_patterns())


def test_workload_kinds_map_to_registry_kinds() -> None:
    valid = set(PlatformKind)
    for intent in WorkloadIntent:
        kinds = workload_kinds(intent)
        assert kinds, intent
        assert set(kinds) <= valid
    assert PlatformKind.SEARCH_INDEX in workload_kinds(WorkloadIntent.VECTOR_SEARCH)


# --- physical design -------------------------------------------------------------


def test_physical_design_mapped_platforms() -> None:
    for platform in ("bigquery", "redshift", "clickhouse", "opensearch", "snowflake"):
        design = physical_design(platform)
        assert design is not None, platform
    assert "distkey" in physical_design("redshift").distribution  # type: ignore[union-attr]
    assert "projection" in physical_design("clickhouse").materialization  # type: ignore[union-attr]
    assert "partition_expiration" in physical_design("bigquery").retention  # type: ignore[union-attr]


# --- materialization -------------------------------------------------------------


def test_materialization_mapping() -> None:
    cases = {
        ("dbt", "incremental"): MaterializationKind.INCREMENTAL_TABLE,
        ("dbt", "view"): MaterializationKind.VIEW,
        ("snowflake", "dynamic_table"): MaterializationKind.MATERIALIZED_VIEW,
        ("bigquery", "materialized_view"): MaterializationKind.MATERIALIZED_VIEW,
        ("clickhouse", "projection"): MaterializationKind.PROJECTION,
        ("opensearch", "index"): MaterializationKind.SEARCH_INDEX,
        ("dynamodb", "global_tables"): MaterializationKind.REPLICA,
        ("iceberg", "snapshot"): MaterializationKind.COPY,
    }
    for (platform, mech), expected in cases.items():
        assert materialization_kind(platform, mech) is expected, (platform, mech)
    assert materialization_kind("dbt", "nonsense") is None
    assert len(materialization_kinds()) == len(MaterializationKind)


# --- consistency ------------------------------------------------------------------


def test_consistency_defaults_and_evidence() -> None:
    assert consistency_for("dynamodb") is ConsistencyModel.EVENTUAL
    assert consistency_for("snowflake") is ConsistencyModel.STRONG
    assert consistency_for("unknown-db") is ConsistencyModel.UNKNOWN
    assert consistency_for("custom", evidence="strong") is ConsistencyModel.STRONG
    assert consistency_for("custom", evidence="bogus") is ConsistencyModel.UNKNOWN


# --- serving model -----------------------------------------------------------------


def test_serving_models_represent_non_table_platforms() -> None:
    os_model = serving_model("opensearch")
    assert os_model is not None
    assert os_model.query_pattern is DataAccessPattern.FULL_TEXT
    assert os_model.workload_intent is WorkloadIntent.SEARCH_ANALYTICS
    assert serving_model("snowflake") is None  # warehouse ≠ serving layer


# --- data movement: sharing vs replication ------------------------------------------


def test_movement_modes_distinct_semantics() -> None:
    assert movement_mode("snowflake_share") is DataMovementMode.SHARE
    assert movement_mode("global_tables") is DataMovementMode.REPLICATE
    assert movement_mode("snapshot_copy") is DataMovementMode.COPY
    assert movement_mode("trino_catalog") is DataMovementMode.FEDERATE
    assert movement_mode("cdc_stream") is DataMovementMode.STREAM
    assert movement_mode("nonsense") is None


def test_sharing_is_not_replication() -> None:
    share = DataMovement(mode=DataMovementMode.SHARE, source="sf.acct", target="consumer")
    replica = DataMovement(mode=DataMovementMode.REPLICATE, source="ddb.us", target="ddb.eu")
    assert share.creates_copy is False
    assert replica.creates_copy is True
    assert share.mode is not replica.mode
    assert len(data_movement_modes()) == len(DataMovementMode)


# --- logical / physical mapping ------------------------------------------------------


def test_logical_physical_binding_explicit_only() -> None:
    ds = LogicalDataset(id="orders", name="orders")
    bound = PhysicalRepresentation(
        platform="snowflake",
        identifier="DB.PUBLIC.ORDERS",
        kind="table",
        dataset_id="orders",
    )
    same_name = PhysicalRepresentation(
        platform="bigquery",
        identifier="ds.orders",
        kind="table",
    )
    bindings = logical_datasets([ds], [bound, same_name])
    assert len(bindings) == 1
    assert bindings[0].dataset.id == "orders"
    assert [r.identifier for r in bindings[0].representations] == ["DB.PUBLIC.ORDERS"]
    # name-similar BQ table is NOT joined — unassigned, not silently fused
    assert unassigned_representations([ds], [bound, same_name]) == (same_name,)


def test_dangling_dataset_reference_not_bound() -> None:
    rep = PhysicalRepresentation(platform="trino", identifier="hive.x.t", dataset_id="ghost")
    assert logical_datasets([], [rep]) == ()
    assert unassigned_representations([], [rep]) == (rep,)


# --- ownership ------------------------------------------------------------------------


def test_ownership_single_claim() -> None:
    own = resolve_ownership(
        [OwnershipClaim("team-data", OwnershipSource.PLATFORM_CONTRACT, "contract:owner")]
    )
    assert own.confidence == "declared"
    assert own.team == "team-data"


def test_ownership_agreeing_claims() -> None:
    own = resolve_ownership(
        [
            OwnershipClaim("team-data", OwnershipSource.TERRAFORM_TAGS),
            OwnershipClaim("team-data", OwnershipSource.CODEOWNERS),
        ]
    )
    assert own.confidence == "declared"


def test_ownership_conflict_surfaces_all_claims() -> None:
    own = resolve_ownership(
        [
            OwnershipClaim("team-a", OwnershipSource.TERRAFORM_TAGS),
            OwnershipClaim("team-b", OwnershipSource.DATAHUB),
        ]
    )
    assert own.confidence == "conflicting"
    assert own.team == ""
    assert {c.team for c in own.conflicts} == {"team-a", "team-b"}


def test_ownership_none() -> None:
    own = resolve_ownership([])
    assert own.confidence == "none"


# --- lifecycle --------------------------------------------------------------------------


def test_lifecycle_mapping() -> None:
    cases = {
        "iceberg_expire_snapshots": LifecycleState.EXPIRED,
        "opensearch_ism": LifecycleState.RETAINED,
        "clickhouse_ttl": LifecycleState.EXPIRED,
        "bigquery_partition_expiration": LifecycleState.EXPIRED,
        "snowflake_time_travel": LifecycleState.RETAINED,
        "iceberg_compaction": LifecycleState.COMPACTED,
        "s3_lifecycle_glacier": LifecycleState.ARCHIVED,
    }
    for mech, state in cases.items():
        lc = lifecycle_for(mech, policy="90d", evidence="main.tf:3")
        assert lc is not None and lc.state is state, mech
    assert lifecycle_for("unknown_mechanism") is None


# --- serialization / conformance -------------------------------------------------------


def test_semantic_vocabulary_deterministic_serializable() -> None:
    a, b = semantic_vocabulary(), semantic_vocabulary()
    assert a == b and json.dumps(a) == json.dumps(b)


def test_vocabulary_includes_semantic_sections() -> None:
    vocab = vocabulary()
    for section in (
        "platform_kinds",
        "workload_intents",
        "data_access_patterns",
        "materialization_kinds",
        "consistency_models",
        "data_movement_modes",
        "lifecycle_states",
        "ownership_sources",
    ):
        assert vocab.get(section)


def test_all_registry_vendors_nonempty() -> None:
    for impl in implementations():
        assert impl.vendor and impl.product and impl.id


# --- CLI --------------------------------------------------------------------------------


def test_cli_ontology_platform_table() -> None:
    result = runner.invoke(app, ["ontology", "platform"])
    assert result.exit_code == 0, result.output
    assert "snowflake" in result.output and "warehouse" in result.output


def test_cli_ontology_platform_detail() -> None:
    result = runner.invoke(app, ["ontology", "platform", "aws_glue_job"])
    assert result.exit_code == 0, result.output
    assert "glue" in result.output and "compute" in result.output


def test_cli_ontology_platform_unknown() -> None:
    result = runner.invoke(app, ["ontology", "platform", "bogus-db"])
    assert result.exit_code == 1
    assert "unknown platform" in result.output


def test_cli_ontology_platform_json() -> None:
    result = runner.invoke(app, ["ontology", "platform", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert any(r["id"] == "snowflake" and r["kind"] == "warehouse" for r in rows)


def test_cli_ontology_workloads() -> None:
    result = runner.invoke(app, ["ontology", "workloads"])
    assert result.exit_code == 0, result.output
    assert "vector_search" in result.output


def test_cli_ontology_access_patterns() -> None:
    result = runner.invoke(app, ["ontology", "access-patterns"])
    assert result.exit_code == 0, result.output
    assert "point_lookup" in result.output
