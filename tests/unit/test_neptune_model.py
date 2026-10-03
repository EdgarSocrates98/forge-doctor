"""Unit tests for the NeptuneProjectModel."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.neptune_model import (
    NEPTUNE_ANALYTICS,
    NEPTUNE_DATABASE,
    neptune_model,
)
from forge_doctor_data.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


TF_CLUSTER = """
resource "aws_neptune_cluster" "graph" {
  cluster_identifier                  = "app-graph"
  engine_version                      = "1.3.2.1"
  iam_database_authentication_enabled = true
  backup_retention_period             = 5
  vpc_security_group_ids              = [aws_security_group.db.id]
}
resource "aws_neptune_cluster_instance" "w" {
  identifier         = "app-graph-0"
  cluster_identifier = aws_neptune_cluster.graph.id
  instance_class     = "db.r6g.large"
}
resource "aws_neptune_subnet_group" "db" {
  name = "db-subnets"
}
"""


def test_empty_project(tmp_path: Path) -> None:
    model = neptune_model(make_context(tmp_path, {"a.py": "x = 1\n"}))
    assert not model.has_neptune
    assert model.product == "unknown"


def test_terraform_cluster_and_instance(tmp_path: Path) -> None:
    model = neptune_model(make_context(tmp_path, {"infra.tf": TF_CLUSTER}))
    assert model.product == NEPTUNE_DATABASE
    c = model.clusters[0]
    assert c.name == "app-graph"
    assert c.engine_version == "1.3.2.1"
    assert c.iam_auth is True
    assert c.backup_retention == 5
    assert c.security_groups == ["aws_security_group.db.id"]
    assert len(c.instances) == 1
    assert c.instances[0].instance_class == "db.r6g.large"
    assert model.subnet_groups[0][0] == "db-subnets"


def test_serverless_cluster(tmp_path: Path) -> None:
    tf = """
resource "aws_neptune_cluster" "g" {
  cluster_identifier = "g"
  serverlessv2_scaling_configuration { min_capacity = 1.0 }
}
resource "aws_neptune_cluster_instance" "i" {
  cluster_identifier = aws_neptune_cluster.g.id
  instance_class     = "db.serverless"
}
"""
    model = neptune_model(make_context(tmp_path, {"infra.tf": tf}))
    assert model.clusters[0].serverless is True
    assert model.clusters[0].instances[0].instance_class == "db.serverless"


def test_global_cluster(tmp_path: Path) -> None:
    tf = (
        TF_CLUSTER
        + """
resource "aws_neptune_global_cluster" "g" {
  global_cluster_identifier = "app-global"
  source_cluster_identifier = aws_neptune_cluster.graph.id
}
"""
    )
    model = neptune_model(make_context(tmp_path, {"infra.tf": tf}))
    assert model.global_clusters[0].name == "app-global"


def test_boto3_data_client_and_loader(tmp_path: Path) -> None:
    py = """
import boto3
data = boto3.client("neptunedata")
data.start_loader_job(
    source="s3://bucket/p/", format="csv", iamRoleArn="arn:aws:iam::1:role/l",
    region="us-east-1", failOnError=True, parallelism="HIGH",
    updateSingleCardinalityProperties=False)
"""
    model = neptune_model(make_context(tmp_path, {"job.py": py}))
    assert model.product == NEPTUNE_DATABASE
    load = model.bulk_loads[0]
    assert load.source_s3 == "s3://bucket/p/"
    assert load.iam_role == "arn:aws:iam::1:role/l"
    assert load.fail_on_error is True
    assert load.parallelism == "HIGH"
    assert load.update_single_cardinality is False


def test_endpoint_detection(tmp_path: Path) -> None:
    py = (
        'conn = DriverRemoteConnection("wss://c.cluster-x.us-east-1.'
        'neptune.amazonaws.com:8182/gremlin", "g")\n'
    )
    model = neptune_model(make_context(tmp_path, {"app.py": py}))
    ep = model.endpoints[0]
    assert ep.port == 8182
    assert ep.ssl is True
    assert "gremlin" in ep.kind or "neptune" in ep.value


def test_language_apis_and_endpoints(tmp_path: Path) -> None:
    py = """
import boto3
data = boto3.client("neptunedata")
data.execute_gremlin_query("g.V().limit(1)")
data.execute_open_cypher_query("MATCH (n) RETURN n LIMIT 1")
"""
    model = neptune_model(make_context(tmp_path, {"app.py": py}))
    assert {"gremlin", "opencypher"} <= model.query_languages


def test_analytics_product_split(tmp_path: Path) -> None:
    tf = 'resource "aws_neptune_graph" "g" {\n  graph_name = "analytics-graph"\n}\n'
    py = 'import boto3\nag = boto3.client("neptune-graph")\n'
    model = neptune_model(make_context(tmp_path, {"infra.tf": tf, "app.py": py}))
    assert NEPTUNE_ANALYTICS in model.products
    assert model.product == NEPTUNE_ANALYTICS
    assert any(c.kind == "client" for c in model.analytics_calls)


def test_mixed_products_database_wins(tmp_path: Path) -> None:
    model = neptune_model(
        make_context(
            tmp_path,
            {
                "infra.tf": TF_CLUSTER + 'resource "aws_neptune_graph" "a" {}\n',
            },
        )
    )
    assert model.product == NEPTUNE_DATABASE
    assert NEPTUNE_ANALYTICS in model.products


def test_cfn_cluster(tmp_path: Path) -> None:
    tpl = """
Resources:
  Graph:
    Type: AWS::Neptune::DBCluster
    Properties:
      DBClusterIdentifier: cfn-graph
      IamAuthEnabled: true
      BackupRetentionPeriod: 7
"""
    model = neptune_model(make_context(tmp_path, {"stack.yaml": tpl}))
    c = model.clusters[0]
    assert c.name == "cfn-graph"
    assert c.iam_auth is True
    assert c.backup_retention == 7


def test_iam_hint_in_code(tmp_path: Path) -> None:
    py = 'from botocore.auth import SigV4Auth\nsigner = SigV4Auth("neptune-db")\n'
    model = neptune_model(make_context(tmp_path, {"app.py": py}))
    assert model.iam_in_code is True


def test_manual_algorithm_detected(tmp_path: Path) -> None:
    py = "def shortest_path(g, a, b):\n    ...\n"
    model = neptune_model(make_context(tmp_path, {"algo.py": py}))
    assert any(a.name == "shortest_path" for a in model.manual_algorithms)


def test_comment_only_no_detection(tmp_path: Path) -> None:
    py = "# this module talks to neptune eventually\nx = 1\n"
    model = neptune_model(make_context(tmp_path, {"a.py": py}))
    assert not model.has_neptune


def test_traversal_read_write_counts(tmp_path: Path) -> None:
    py = 'g.V().hasLabel("P").addV("X")\ng.V().out("k")\n'
    model = neptune_model(make_context(tmp_path, {"app.py": py}))
    assert model.write_traversals >= 1
    assert model.read_traversals >= 1
