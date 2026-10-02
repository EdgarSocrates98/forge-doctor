"""Cross-domain integration: platform graph edges + capability joins.

Spec 180 + prompt_evo_capability1 sections 4-6: the canonical graph must
rebuild DynamoDB -> stream -> Lambda -> Neptune chains, and capability
evaluation must produce derived findings with provenance.
"""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.platform_graph_builder import (
    build_platform_graph,
    impact_reachable,
)
from forge_doctor.checks.dynamodb import GlobalMrscTransactions
from forge_doctor.core.capabilities import CapabilityStatus, capability_registry
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import EvidenceKind
from forge_doctor.core.platform_graph import EntityKind, RelKind


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


PIPELINE_TF = """
resource "aws_dynamodb_table" "orders" {
  name             = "orders"
  hash_key         = "pk"
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"
}
resource "aws_lambda_function" "sync" {
  function_name = "order-sync"
  handler       = "sync.handler"
}
resource "aws_lambda_event_source_mapping" "m" {
  event_source_arn = aws_dynamodb_table.orders.stream_arn
  function_name    = aws_lambda_function.sync.arn
}
resource "aws_neptune_cluster" "graph" {
  cluster_identifier = "app-graph"
}
resource "aws_neptune_cluster_instance" "w" {
  cluster_identifier = aws_neptune_cluster.graph.id
  instance_class     = "db.r6g.large"
}
"""


def test_stream_lambda_neptune_chain(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": PIPELINE_TF,
            "sync.py": 'g.addV("Order").property("id", x)\n',
        },
    )
    g = build_platform_graph(ctx)
    ids = {e.id for e in g.entities()}
    assert "table:dynamodb:orders" in ids
    assert "stream:dynamodb:orders" in ids
    assert "compute_job:lambda:order-sync" in ids
    assert "graph:neptune:app-graph" in ids
    rels = {(r.src, r.kind, r.dst) for r in g.relationships()}
    assert (
        "table:dynamodb:orders",
        RelKind.PRODUCES,
        "stream:dynamodb:orders",
    ) in rels
    assert (
        "stream:dynamodb:orders",
        RelKind.TRIGGERS,
        "compute_job:lambda:order-sync",
    ) in rels
    assert (
        "compute_job:lambda:order-sync",
        RelKind.WRITES,
        "graph:neptune:app-graph",
    ) in rels


def test_blast_radius_spans_domains(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": PIPELINE_TF,
            "sync.py": 'g.addV("Order").property("id", x)\n',
        },
    )
    g = build_platform_graph(ctx)
    reached = impact_reachable(g, "table:dynamodb:orders")
    assert "compute_job:lambda:order-sync" in reached


def test_terraform_defines_entities(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": PIPELINE_TF})
    g = build_platform_graph(ctx)
    defines = {(r.src, r.dst) for r in g.relationships() if r.kind is RelKind.DEFINES}
    assert any(dst == "table:dynamodb:orders" for _, dst in defines)
    assert any(dst == "graph:neptune:app-graph" for _, dst in defines)


def test_query_entities_read_tables_and_graphs(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": PIPELINE_TF,
            "app.py": (
                'import boto3\nd = boto3.resource("dynamodb")\n'
                't = d.Table("orders")\nt.get_item(Key={"pk": "1"})\n'
                'g.V().hasLabel("Order").out("placed")\n'
            ),
        },
    )
    g = build_platform_graph(ctx)
    reads = {(r.src, r.dst) for r in g.relationships() if r.kind is RelKind.READS}
    assert any(dst == "table:dynamodb:orders" for _, dst in reads)
    assert any(dst == "graph:neptune:app-graph" for _, dst in reads)


def test_edges_carry_evidence_kind(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": PIPELINE_TF})
    g = build_platform_graph(ctx)
    assert g.relationships()
    assert all(r.evidence_kind is not None for r in g.relationships())


def test_determinism(tmp_path: Path) -> None:
    files = {"infra.tf": PIPELINE_TF, "sync.py": 'g.addV("X")\n'}
    a = build_platform_graph(make_context(tmp_path, files)).to_dict()
    b = build_platform_graph(make_context(tmp_path, files)).to_dict()
    assert a == b


# --- data-model inspect ------------------------------------------------------


def test_access_style_breakdown(tmp_path: Path) -> None:
    from forge_doctor.cli.datamodel import _style_counts

    ctx = make_context(
        tmp_path,
        {
            "app.py": (
                'import boto3\nd = boto3.resource("dynamodb")\n'
                't = d.Table("orders")\n'
                't.get_item(Key={"pk": "1"})\nt.scan()\n'
                'g.V().hasLabel("P").out("a").out("b")\n'
            )
        },
    )
    counts = _style_counts(ctx)
    assert counts["key lookup"] == 1
    assert counts["scan"] == 1
    assert counts["multi-hop traversal"] == 1


# --- capability cross-domain tests (prompt section 5) -------------------------


def test_ddb_mrsc_transactions_unsupported() -> None:
    reg = capability_registry()
    res = reg.evaluate("DYNAMODB_TRANSACTIONS", platform="dynamodb_global_table", variant="MRSC")
    assert res.status is CapabilityStatus.UNSUPPORTED
    assert res.pack  # provenance


def test_ddb_mrsc_finding_is_derived(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": (
                'resource "aws_dynamodb_table" "t" {\n name="t"\n hash_key="pk"\n'
                ' global_table_witness { region_name = "us-west-2" }\n}\n'
            ),
            "app.py": (
                'import boto3\nd = boto3.resource("dynamodb")\n'
                't = d.Table("t")\nt.transact_write_items(TransactItems=[])\n'
            ),
        },
    )
    out = GlobalMrscTransactions().run(ctx)
    assert out
    assert out[0].severity.value == "error"
    assert out[0].evidence_kind is EvidenceKind.DERIVED


def test_neptune_opencypher_on_pg_supported() -> None:
    reg = capability_registry()
    res = reg.evaluate("NEPTUNE_OPENCYPHER", platform="neptune", graph_model="property_graph")
    assert res.status is CapabilityStatus.SUPPORTED


def test_neptune_opencypher_on_rdf_incompatible() -> None:
    reg = capability_registry()
    res = reg.evaluate("NEPTUNE_OPENCYPHER", platform="neptune", graph_model="rdf")
    assert res.status is CapabilityStatus.UNSUPPORTED
    assert res.source


def test_graph_entities_present(tmp_path: Path) -> None:
    """174 schema entities still land (graph_node/graph_edge)."""
    ctx = make_context(
        tmp_path,
        {"q.cypher": "MATCH (a:Person)-[:KNOWS]->(b:Person) RETURN a\n"},
    )
    g = build_platform_graph(ctx)
    kinds = {e.kind for e in g.entities()}
    assert EntityKind.GRAPH_NODE in kinds
    assert EntityKind.GRAPH_EDGE in kinds
