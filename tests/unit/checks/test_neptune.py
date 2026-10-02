"""Unit tests for NEP### checks."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.checks.neptune import (
    CHECKS,
    AnalyticsOnDatabase,
    BulkLoaderCandidate,
    BulkLoadIam,
    CartesianPattern,
    ClusterInstanceMismatch,
    GlobalRecoveryTopology,
    IamAuthMismatch,
    LanguageParadigmMismatch,
    LargeProjection,
    LateFilter,
    MalformedInput,
    ManualAlgorithmWithAnalytics,
    NeptuneUsage,
    PublicAccess,
    ReadReplicaCoverage,
    RowByRowIngestion,
    SecurityGroupTopology,
    StreamToNeptuneMutation,
    UnboundedVariableLength,
    WeakBackup,
    WeakSelectivity,
)
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


TF_CLUSTER = """
resource "aws_neptune_cluster" "graph" {
  cluster_identifier      = "app-graph"
  backup_retention_period = 7
}
resource "aws_neptune_cluster_instance" "w" {
  cluster_identifier = aws_neptune_cluster.graph.id
  instance_class     = "db.r6g.large"
}
"""

DATA = 'import boto3\ndata = boto3.client("neptunedata")\n'


def test_anchor(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_CLUSTER})
    r = NeptuneUsage().run(ctx)[0]
    assert r.severity == Severity.INFO
    assert "1 clusters" in r.message


def test_anchor_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"a.py": "x = 1\n"})
    assert NeptuneUsage().run(ctx)[0].severity == Severity.PASS


def test_nep010_rdf_plus_opencypher(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "d.ttl": "<a> <b> <c> .\n",
            "q.cypher": "MATCH (a:P) RETURN a LIMIT 1\n",
        },
    )
    out = LanguageParadigmMismatch().run(ctx)
    assert out
    assert out[0].severity == Severity.WARNING
    assert "rdf" in out[0].message


def test_nep010_same_paradigm_clean(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "q.cypher": "MATCH (a:P) RETURN a LIMIT 1\n",
            "g.gremlin": 'g.V().hasLabel("P").limit(1)\n',
        },
    )
    assert LanguageParadigmMismatch().run(ctx) == []


def test_nep020_unselective_start(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.py": 'g.V().out("k").has("x","y")\n'})
    assert WeakSelectivity().run(ctx)


def test_nep020_selective_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.py": 'g.V("1").out("k").limit(1)\n'})
    assert WeakSelectivity().run(ctx) == []


def test_nep021_unbounded_repeat(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.py": 'g.V().hasLabel("P").repeat(__.out("k")).path()\n'})
    assert UnboundedVariableLength().run(ctx)


def test_nep021_bounded_clean(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path, {"app.py": 'g.V().hasLabel("P").repeat(__.out("k")).times(2).path()\n'}
    )
    assert UnboundedVariableLength().run(ctx) == []


def test_nep022_cartesian(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.cypher": "MATCH (a:Person), (b:City) RETURN a,b\n"})
    assert CartesianPattern().run(ctx)


def test_nep022_connected_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.cypher": "MATCH (a:Person)-[:LIVES]->(b:City) RETURN a\n"})
    assert CartesianPattern().run(ctx) == []


def test_nep023_star_no_limit(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.cypher": "MATCH (a:P) RETURN *\n"})
    assert LargeProjection().run(ctx)


def test_nep023_limited_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.cypher": "MATCH (a:P) RETURN * LIMIT 5\n"})
    assert LargeProjection().run(ctx) == []


def test_nep024_late_filter(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.py": 'g.V().out("a").out("b").has("name","x")\n'})
    assert LateFilter().run(ctx)


def test_nep024_early_filter_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.py": 'g.V().has("name","x").out("a").out("b")\n'})
    assert LateFilter().run(ctx) == []


def test_nep030_row_by_row(tmp_path: Path) -> None:
    py = (
        'g.addV("A").property("k","1")\n'
        'g.addV("A").property("k","2")\n'
        'g.addV("A").property("k","3")\n'
    )
    ctx = make_context(tmp_path, {"load.py": py})
    assert RowByRowIngestion().run(ctx)


def test_nep030_with_loader_clean(tmp_path: Path) -> None:
    py = (
        'g.addV("A").property("k","1")\ng.addV("A").property("k","2")\n'
        'g.addV("A").property("k","3")\n'
        + DATA
        + 'data.start_loader_job(source="s3://b/x", iamRoleArn="r")\n'
    )
    ctx = make_context(tmp_path, {"load.py": py})
    assert RowByRowIngestion().run(ctx) == []


def test_nep031_no_loader(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.py": 'g.addV("A")\ng.addV("B")\n'})
    assert BulkLoaderCandidate().run(ctx)


def test_nep032_missing_role(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": DATA + 'data.start_loader_job(source="s3://b/x")\n'},
    )
    out = BulkLoadIam().run(ctx)
    assert out and out[0].severity == Severity.WARNING


def test_nep032_role_present_clean(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": DATA + 'data.start_loader_job(source="s3://b/x", iamRoleArn="r")\n'},
    )
    assert BulkLoadIam().run(ctx) == []


def test_nep033_malformed(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"bad.cypher": "RETURN garbage\n"})
    # A cypher file with no MATCH/CREATE parses as unparsed -> finding
    assert MalformedInput().run(ctx)


def test_nep040_read_heavy_no_replica(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": TF_CLUSTER,
            "app.py": 'g.V().out("a")\ng.V().out("b")\ng.V().out("c")\n',
        },
    )
    out = ReadReplicaCoverage().run(ctx)
    assert out and out[0].severity == Severity.INFO


def test_nep040_balanced_silent(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"infra.tf": TF_CLUSTER, "app.py": 'g.V().out("a")\n'},
    )
    assert ReadReplicaCoverage().run(ctx) == []


def test_nep041_no_backup(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"infra.tf": 'resource "aws_neptune_cluster" "g" { cluster_identifier="g" }\n'},
    )
    assert WeakBackup().run(ctx)


def test_nep041_backup_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_CLUSTER})
    assert WeakBackup().run(ctx) == []


def test_nep042_public_instance(tmp_path: Path) -> None:
    tf = TF_CLUSTER.replace(
        'instance_class     = "db.r6g.large"',
        'instance_class     = "db.r6g.large"\n  publicly_accessible = true',
    )
    ctx = make_context(tmp_path, {"infra.tf": tf})
    out = PublicAccess().run(ctx)
    assert out and out[0].severity == Severity.WARNING


def test_nep043_iam_mismatch(tmp_path: Path) -> None:
    tf = TF_CLUSTER.replace(
        'cluster_identifier      = "app-graph"',
        'cluster_identifier      = "app-graph"\n  iam_database_authentication_enabled = true',
    )
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": tf,
            "app.py": (
                'conn = DriverRemoteConnection("wss://app-graph.cluster-x.'
                'us-east-1.neptune.amazonaws.com:8182/gremlin", "g")\n'
            ),
        },
    )
    assert IamAuthMismatch().run(ctx)


def test_nep044_no_sgs(tmp_path: Path) -> None:
    tf = 'resource "aws_neptune_cluster" "g" { cluster_identifier="g" }\n'
    ctx = make_context(tmp_path, {"infra.tf": tf})
    assert SecurityGroupTopology().run(ctx)


def test_nep045_serverless_class_mismatch(tmp_path: Path) -> None:
    tf = """
resource "aws_neptune_cluster" "g" {
  cluster_identifier = "g"
  serverlessv2_scaling_configuration { min_capacity = 1.0 }
}
resource "aws_neptune_cluster_instance" "i" {
  cluster_identifier = aws_neptune_cluster.g.id
  instance_class     = "db.r6g.large"
}
"""
    ctx = make_context(tmp_path, {"infra.tf": tf})
    out = ClusterInstanceMismatch().run(ctx)
    assert out and "db.serverless" in out[0].message


def test_nepgt003_no_secondary(tmp_path: Path) -> None:
    tf = (
        TF_CLUSTER
        + 'resource "aws_neptune_global_cluster" "g" { global_cluster_identifier="gl" }\n'
    )
    ctx = make_context(tmp_path, {"infra.tf": tf})
    assert GlobalRecoveryTopology().run(ctx)


def test_nepa001_manual_with_analytics(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": 'resource "aws_neptune_graph" "a" {}\n',
            "algo.py": "def pagerank(g):\n    ...\n",
        },
    )
    assert ManualAlgorithmWithAnalytics().run(ctx)


def test_nepa002_analytics_call_on_database(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"infra.tf": TF_CLUSTER, "algo.py": "def pagerank(g):\n    ...\n"},
    )
    assert AnalyticsOnDatabase().run(ctx)


def test_nepcd001_stream_to_neptune_write(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": (
                'resource "aws_dynamodb_table" "t" {\n name="t"\n hash_key="pk"\n'
                ' stream_enabled = true\n stream_view_type = "KEYS_ONLY"\n}\n'
                'resource "aws_lambda_event_source_mapping" "m" {\n'
                "  event_source_arn = aws_dynamodb_table.t.stream_arn\n"
                "  function_name = aws_lambda_function.f.arn\n}\n"
                'resource "aws_lambda_function" "f" {\n function_name="f"\n}\n' + TF_CLUSTER
            ),
            "f.py": 'g.addV("V")\n',
        },
    )
    out = StreamToNeptuneMutation().run(ctx)
    assert out and out[0].severity == Severity.WARNING


def test_registry_collects_spec_ids() -> None:
    ids = {c.id for c in CHECKS}
    assert {
        "NEP001",
        "NEP010",
        "NEP020",
        "NEP021",
        "NEP022",
        "NEP023",
        "NEP024",
        "NEP030",
        "NEP031",
        "NEP032",
        "NEP033",
        "NEP040",
        "NEP041",
        "NEP042",
        "NEP043",
        "NEP044",
        "NEP045",
        "NEPGT001",
        "NEPGT002",
        "NEPGT003",
        "NEPA001",
        "NEPA002",
        "NEPCD001",
    } <= ids
