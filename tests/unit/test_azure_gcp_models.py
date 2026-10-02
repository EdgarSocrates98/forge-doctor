"""Spec-233 tests: Azure/GCP platform models, checks, graph edges,
abstraction folding, capability packs, and runtime adapters."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.abstractions import abstractions_model
from forge_doctor.analyzers.azure_model import azure_model
from forge_doctor.analyzers.gcp_model import gcp_model
from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
from forge_doctor.analyzers.runtime_evidence import ADAPTERS
from forge_doctor.checks.azure import CHECKS as AZ_CHECKS
from forge_doctor.checks.gcp import CHECKS as GCP_CHECKS
from forge_doctor.core.capabilities import capability_registry
from forge_doctor.core.context import ProjectContext

# --- fixtures -------------------------------------------------------------

_AZ_BASIC = """
resource "azurerm_storage_account" "lake" {
  name           = "stlake"
  location       = "westeurope"
  is_hns_enabled = true
}
resource "azurerm_storage_data_lake_gen2_filesystem" "fs" {
  name               = "curated"
  storage_account_id = azurerm_storage_account.lake.id
}
resource "azurerm_eventhub_namespace" "bus" {
  name = "eh-ns"
}
resource "azurerm_eventhub" "orders" {
  name              = "orders"
  namespace_name    = "eh-ns"
  partition_count   = 8
  capture_description {
    enabled = true
  }
}
resource "azurerm_synapse_workspace" "syn" {
  name = "syn-prod"
}
resource "azurerm_synapse_sql_pool" "dw" {
  name = "dw-pool"
}
resource "azurerm_data_factory" "adf" {
  name = "adf-prod"
}
resource "azurerm_data_factory_pipeline" "copy" {
  name = "copy-raw"
}
resource "azurerm_purview_account" "pv" {
  name = "pv-prod"
}
"""

_GCP_BASIC = """
resource "google_storage_bucket" "raw" {
  name = "raw-b"
  versioning {
    enabled = true
  }
  lifecycle_rule {
    action {
      type = "Delete"
    }
  }
}
resource "google_pubsub_topic" "events" {
  name = "events"
}
resource "google_pubsub_subscription" "sub" {
  name = "sub-events"
  topic = google_pubsub_topic.events.id
  dead_letter_policy {
    dead_letter_topic     = "dlq"
    max_delivery_attempts = 5
  }
}
resource "google_dataflow_job" "etl" {
  name      = "etl"
  on_delete = "drain"
}
resource "google_dataplex_lake" "lake" {
  name = "lake1"
}
resource "google_dataplex_zone" "raw" {
  name = "rawzone"
  lake = google_dataplex_lake.lake.name
}
"""


def _ctx(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def _findings(ctx: ProjectContext, checks) -> dict[str, list]:
    out: dict[str, list] = {}
    for check in checks:
        out[check.id] = check.run(ctx)
    return out


# --- Azure model ----------------------------------------------------------


def test_azure_model_happy(tmp_path: Path) -> None:
    m = azure_model(_ctx(tmp_path, {"main.tf": _AZ_BASIC}))
    assert m.has_evidence
    assert set(m.services()) == {"adls_gen2", "adf", "eventhubs", "purview", "synapse"}
    acc = m.adls_accounts[0]
    assert acc.name == "stlake" and acc.hns_enabled == "true"
    assert acc.filesystems == ("curated",)
    hub = m.eventhubs[0]
    assert hub.capture is True and hub.partitions == "8"
    assert m.synapse_workspaces[0].sql_pools == ("dw-pool",)
    assert m.adf_factories[0].pipelines == ("copy-raw",)


def test_azure_model_empty(tmp_path: Path) -> None:
    m = azure_model(_ctx(tmp_path, {"a.py": "x = 1\n"}))
    assert not m.has_evidence
    assert m.services() == []


def test_azure_filesystem_unattributed_ambiguous(tmp_path: Path) -> None:
    tf = """
resource "azurerm_storage_account" "a" { name = "sta" }
resource "azurerm_storage_account" "b" { name = "stb" }
resource "azurerm_storage_data_lake_gen2_filesystem" "fs" {
  name = "orphan"
  storage_account_id = "unresolvable"
}
"""
    m = azure_model(_ctx(tmp_path, {"main.tf": tf}))
    assert all(acc.filesystems == () for acc in m.adls_accounts)


def test_azure_filesystem_single_account_attributed(tmp_path: Path) -> None:
    tf = """
resource "azurerm_storage_account" "a" { name = "sta" }
resource "azurerm_storage_data_lake_gen2_filesystem" "fs" {
  name = "fs1"
}
"""
    m = azure_model(_ctx(tmp_path, {"main.tf": tf}))
    assert m.adls_accounts[0].filesystems == ("fs1",)


def test_azure_malformed_tf(tmp_path: Path) -> None:
    m = azure_model(_ctx(tmp_path, {"bad.tf": 'resource "azurerm_eventhub" {\n  name ='}))
    assert isinstance(m.eventhubs, list)  # malformed block must not crash


def test_azure_model_deterministic(tmp_path: Path) -> None:
    files = {"main.tf": _AZ_BASIC}
    a = azure_model(_ctx(tmp_path / "one", files))
    b = azure_model(_ctx(tmp_path / "two", files))
    assert [x.name for x in a.eventhubs] == [x.name for x in b.eventhubs]
    assert a.services() == b.services()


def test_azure_model_memoized(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"main.tf": _AZ_BASIC})
    assert azure_model(ctx) is azure_model(ctx)


# --- Azure checks ---------------------------------------------------------


def test_az001_fires_flat_namespace(tmp_path: Path) -> None:
    tf = """
resource "azurerm_storage_account" "bad" {
  name           = "stbad"
  is_hns_enabled = false
}
resource "azurerm_storage_data_lake_gen2_filesystem" "fs" {
  name               = "fs"
  storage_account_id = azurerm_storage_account.bad.id
}
"""
    f = _findings(_ctx(tmp_path, {"main.tf": tf}), AZ_CHECKS)
    assert any(r.check_id == "AZ001" for r in f["AZ001"])


def test_az001_silent_on_hns(tmp_path: Path) -> None:
    f = _findings(_ctx(tmp_path, {"main.tf": _AZ_BASIC}), AZ_CHECKS)
    assert f["AZ001"] == []


def test_az002_retention_and_capture(tmp_path: Path) -> None:
    f = _findings(_ctx(tmp_path, {"main.tf": _AZ_BASIC}), AZ_CHECKS)
    # capture present + retention unset -> the fixture still fires only
    # when capture is absent; hub has capture -> silent
    assert f["AZ002"] == []


def test_az002_fires_min_retention(tmp_path: Path) -> None:
    tf = 'resource "azurerm_eventhub" "h" {\n  name = "h"\n}\n'
    f = _findings(_ctx(tmp_path, {"main.tf": tf}), AZ_CHECKS)
    assert len(f["AZ002"]) == 1


def test_az003_silent_when_purview(tmp_path: Path) -> None:
    f = _findings(_ctx(tmp_path, {"main.tf": _AZ_BASIC}), AZ_CHECKS)
    assert f["AZ003"] == []


# --- GCP model ------------------------------------------------------------


def test_gcp_model_happy(tmp_path: Path) -> None:
    m = gcp_model(_ctx(tmp_path, {"main.tf": _GCP_BASIC}))
    assert m.has_evidence
    assert set(m.services()) == {"gcs", "dataflow", "pubsub", "dataplex"}
    b = m.gcs_buckets[0]
    assert b.versioning == "true" and b.has_lifecycle is True
    sub = m.pubsub_subscriptions[0]
    assert sub.has_dead_letter is True and sub.max_delivery_attempts == "5"
    assert m.dataplex_lakes[0].zones == ("rawzone",)
    assert m.dataflow_jobs[0].on_delete == "drain"


def test_gcp_model_empty(tmp_path: Path) -> None:
    m = gcp_model(_ctx(tmp_path, {"a.py": "x = 1\n"}))
    assert not m.has_evidence


def test_gcp_unknown_zone_unlinked(tmp_path: Path) -> None:
    tf = """
resource "google_dataplex_lake" "a" { name = "lake-a" }
resource "google_dataplex_zone" "z" {
  name = "z"
  lake = "some-other-lake"
}
"""
    m = gcp_model(_ctx(tmp_path, {"main.tf": tf}))
    assert m.dataplex_lakes[0].zones == ()


def test_gcp_model_deterministic(tmp_path: Path) -> None:
    files = {"main.tf": _GCP_BASIC}
    a = gcp_model(_ctx(tmp_path / "one", files))
    b = gcp_model(_ctx(tmp_path / "two", files))
    assert [s.name for s in a.pubsub_subscriptions] == [s.name for s in b.pubsub_subscriptions]


def test_gcp_model_memoized(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"main.tf": _GCP_BASIC})
    assert gcp_model(ctx) is gcp_model(ctx)


# --- GCP checks -----------------------------------------------------------


def test_gcp001_fires_and_silences(tmp_path: Path) -> None:
    f = _findings(_ctx(tmp_path, {"main.tf": _GCP_BASIC}), GCP_CHECKS)
    assert f["GCP001"] == []  # dead_letter_policy present

    bare = 'resource "google_pubsub_subscription" "s" {\n  name = "s"\n}\n'
    f2 = _findings(_ctx(tmp_path / "b", {"main.tf": bare}), GCP_CHECKS)
    assert len(f2["GCP001"]) == 1


def test_gcp002_versioning_saves(tmp_path: Path) -> None:
    f = _findings(_ctx(tmp_path, {"main.tf": _GCP_BASIC}), GCP_CHECKS)
    assert f["GCP002"] == []

    bad = 'resource "google_storage_bucket" "b" {\n  name = "b"\n  force_destroy = true\n}\n'
    f2 = _findings(_ctx(tmp_path / "b", {"main.tf": bad}), GCP_CHECKS)
    assert len(f2["GCP002"]) == 1


def test_gcp003_cancel_only(tmp_path: Path) -> None:
    f = _findings(_ctx(tmp_path, {"main.tf": _GCP_BASIC}), GCP_CHECKS)
    assert f["GCP003"] == []  # drain

    bad = 'resource "google_dataflow_job" "j" {\n  name = "j"\n  on_delete = "cancel"\n}\n'
    f2 = _findings(_ctx(tmp_path / "b", {"main.tf": bad}), GCP_CHECKS)
    assert len(f2["GCP003"]) == 1


# --- graph edges ----------------------------------------------------------


def _edge_keys(ctx: ProjectContext) -> set[str]:
    g = build_platform_graph(ctx)
    return {f"{r.kind.name.lower()}|{r.src}->{r.dst}" for r in g.relationships()}


def test_azure_graph_contains_edges(tmp_path: Path) -> None:
    edges = _edge_keys(_ctx(tmp_path, {"main.tf": _AZ_BASIC}))
    assert "contains|stream:eventhubs:eh-ns->stream:eventhubs:orders" in edges
    assert "contains|storage_location:adls:stlake->storage_location:adls:curated" in edges
    assert (
        "defines|infrastructure_resource:azure:azurerm_purview_account.pv"
        "->catalog:purview:pv-prod" in edges
    )
    # no azure resource lands under the aws domain
    assert not any("infrastructure_resource:aws:azurerm_" in e for e in edges)


def test_gcp_graph_edges(tmp_path: Path) -> None:
    edges = _edge_keys(_ctx(tmp_path, {"main.tf": _GCP_BASIC}))
    assert "consumes|stream:pubsub:sub-events->stream:pubsub:events" in edges
    assert "contains|catalog:dataplex:lake1->catalog:dataplex:rawzone" in edges
    assert not any("infrastructure_resource:aws:google_" in e for e in edges)


# --- abstractions ---------------------------------------------------------


def test_abstractions_fold_azure_gcp(tmp_path: Path) -> None:
    m = abstractions_model(_ctx(tmp_path, {"a.tf": _AZ_BASIC, "b.tf": _GCP_BASIC}))
    clouds = m.clouds()
    assert {"azure", "gcp"} <= clouds
    kinds = m.kinds()
    assert {"object_storage", "stream", "catalog", "orchestrator"} <= kinds


def test_abstractions_single_cloud_not_mixed(tmp_path: Path) -> None:
    m = abstractions_model(_ctx(tmp_path, {"main.tf": _GCP_BASIC}))
    assert m.clouds() == {"gcp"}


# --- capability packs -----------------------------------------------------


def test_packs_load_and_evaluate() -> None:
    reg = capability_registry()
    for plat, cap in (
        ("adls", "ADLS_POSIX_ACLS"),
        ("azure_fabric", "FABRIC_ONELAKE_SHORTCUTS"),
        ("synapse", "SYNAPSE_SERVERLESS_SQL"),
        ("eventhubs", "EVENTHUBS_KAFKA_ENDPOINT"),
        ("purview", "PURVIEW_LINEAGE"),
        ("dataflow", "DATAFLOW_DRAIN"),
        ("dataproc", "DATAPROC_AUTOSCALING"),
        ("pubsub", "PUBSUB_DEAD_LETTER"),
        ("dataplex", "DATAPLEX_LAKE_ZONES"),
    ):
        res = reg.evaluate(platform=plat, capability=cap)
        assert res.status.value == "supported", (plat, cap, res.status)
        assert res.source.startswith("https://")


def test_pack_unknown_capability_stays_unknown() -> None:
    reg = capability_registry()
    res = reg.evaluate(platform="adls", capability="ADLS_NONEXISTENT")
    assert res.status.value == "unknown"


def test_pack_dependency_facts() -> None:
    reg = capability_registry()
    deps = reg.dependencies("adls", "ADLS_POSIX_ACLS")
    assert "ADLS_HIERARCHICAL_NAMESPACE" in deps.requires


# --- runtime adapters -----------------------------------------------------


def _adapter_for(text: str, name: str):
    for ad in ADAPTERS:
        if ad.matches(Path("x.json"), text) and ad.name == name:
            return ad
    return None


def test_dataflow_metrics_adapter(tmp_path: Path) -> None:
    text = (
        '{"jobId": "2024-01-01_x", "name": "etl", "type": "JOB_TYPE_STREAMING",'
        ' "currentState": "JOB_STATE_RUNNING", "metrics": ['
        '{"name": "dataWatermark", "scalar": 42}]}'
    )
    ad = _adapter_for(text, "dataflow_metrics")
    assert ad is not None
    m = ad.parse(Path("x.json"), text)
    assert m.identifiers["execution_id"] == "2024-01-01_x"
    assert m.executions[0].state == "started"
    assert any(mt.name == "dataWatermark" for mt in m.metrics)


def test_pubsub_backlog_adapter() -> None:
    text = (
        '[{"subscription": "sub-events", "num_undelivered_messages": 12,'
        ' "oldest_unacked_message_age": 301.5}]'
    )
    ad = _adapter_for(text, "pubsub_backlog")
    assert ad is not None
    m = ad.parse(Path("x.json"), text)
    assert m.identifiers["subscription"] == "sub-events"
    assert any(mt.name == "num_undelivered_messages" for mt in m.lag)


def test_synapse_query_adapter() -> None:
    text = (
        '[{"request_id": "QID1", "status": "Failed",'
        ' "total_elapsed_time": 900, "command": "select * from t"}]'
    )
    ad = _adapter_for(text, "synapse_query")
    assert ad is not None
    m = ad.parse(Path("x.json"), text)
    assert m.executions[0].state == "failed"
    assert m.errors and m.errors[0].execution_id == "QID1"


def test_eventhub_metrics_adapter() -> None:
    text = (
        '[{"eventhub": "orders", "incomingMessages": 100,'
        ' "outgoingMessages": 95, "serverErrors": 2}]'
    )
    ad = _adapter_for(text, "eventhubs_metrics")
    assert ad is not None
    m = ad.parse(Path("x.json"), text)
    assert m.throughput[0].input_rows == 100
    assert m.errors and m.errors[0].count == 2


def test_fabric_pipeline_adapter() -> None:
    text = (
        '{"value": [{"runId": "r1", "pipelineName": "pl-daily",'
        ' "status": "Failed", "durationInMs": 1234,'
        ' "failureMessage": "activity X timed out"}]}'
    )
    ad = _adapter_for(text, "fabric_pipeline")
    assert ad is not None
    m = ad.parse(Path("x.json"), text)
    assert m.executions[0].state == "failed"
    assert "timed out" in m.errors[0].message


def test_runtime_adapters_reject_garbage() -> None:
    names = {
        "dataflow_metrics",
        "pubsub_backlog",
        "synapse_query",
        "eventhubs_metrics",
        "fabric_pipeline",
    }
    hits = [a.name for a in ADAPTERS if a.matches(Path("x.txt"), "not json at all")]
    assert not names & set(hits)
