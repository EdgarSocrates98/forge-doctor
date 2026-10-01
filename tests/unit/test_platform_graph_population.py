"""Unit tests for the DataPlatformGraph domain adapters (spec 172)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import EvidenceKind

runner = CliRunner()


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_empty_project_empty_graph(tmp_path: Path) -> None:
    assert build_platform_graph(make_context(tmp_path, {})).entities() == []


def test_airflow_adapter(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "dag.py": (
                "from airflow import DAG\n"
                "from airflow.operators.empty import EmptyOperator\n"
                'with DAG("orders") as dag:\n'
                '    a = EmptyOperator(task_id="extract")\n'
                '    b = EmptyOperator(task_id="load")\n'
                "    a >> b\n"
            )
        },
    )
    g = build_platform_graph(ctx)
    assert g.entity("workflow:airflow:orders") is not None
    assert g.entity("task:airflow:extract") is not None
    # a >> b  =>  b DEPENDS_ON a
    deps = g.relationships()
    assert any(
        r.src == "task:airflow:load"
        and r.dst == "task:airflow:extract"
        and r.kind.value == "DEPENDS_ON"
        for r in deps
    )


def test_sql_adapter_read_write(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sql": "INSERT INTO mart.daily SELECT a FROM src.s;\n"},
    )
    g = build_platform_graph(ctx)
    writes = [r for r in g.relationships() if r.kind.value == "WRITES"]
    assert any(r.dst == "table:sql:mart.daily" for r in writes)
    reads = [r for r in g.relationships() if r.kind.value == "READS"]
    assert any(r.dst == "table:sql:src.s" for r in reads)
    assert all(r.evidence_kind is EvidenceKind.STATIC for r in g.relationships())


def test_streaming_adapter(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                'df = spark.readStream.format("kafka").load()\n'
                'q = df.writeStream.format("delta").start()\n'
            )
        },
    )
    g = build_platform_graph(ctx)
    kinds = {r.kind.value for r in g.relationships()}
    assert "CONSUMES" in kinds and "PRODUCES" in kinds
    assert any(e.kind.value == "stream" and e.domain == "kafka" for e in g.entities())
    # no identifier for the delta sink -> honest dataset marker
    assert g.entity("dataset:delta:delta") is not None


def test_terraform_defines_typed_entity(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"x.tf": ('resource "aws_dynamodb_table" "orders" {\n  name = "orders-table"\n}\n')},
    )
    g = build_platform_graph(ctx)
    assert g.entity("infrastructure_resource:aws:aws_dynamodb_table.orders")
    assert g.entity("table:dynamodb:orders-table")
    assert any(
        r.kind.value == "DEFINES" and r.dst == "table:dynamodb:orders-table"
        for r in g.relationships()
    )


def test_cross_domain_join_shared_id(tmp_path: Path) -> None:
    """A TF-defined SFN machine and its embedded ASL share the canonical
    ``workflow:stepfunctions:<label>`` id - no fuzzy matching - and the
    infra node reaches the Lambda the ASL invokes."""
    ctx = make_context(
        tmp_path,
        {
            "x.tf": (
                'resource "aws_sfn_state_machine" "pipe" {\n'
                "  definition = <<EOF\n"
                '{"StartAt": "A", "States": {"A": {"Type": "Task", '
                '"Resource": "arn:aws:lambda:us-east-1:1:function:fn", '
                '"End": true}}}\n'
                "EOF\n"
                "}\n"
            ),
        },
    )
    g = build_platform_graph(ctx)
    wf = g.entity("workflow:stepfunctions:pipe")
    assert wf is not None
    reached = g.reachable("infrastructure_resource:aws:aws_sfn_state_machine.pipe")
    assert "compute_job:lambda:fn" in reached


def test_sfn_integration_pattern_no_phantom(tmp_path: Path) -> None:
    """``arn:aws:states:::lambda:invoke`` names the pattern, not a fn -
    no entity should be minted for it."""
    ctx = make_context(
        tmp_path,
        {
            "m.asl.json": (
                '{"StartAt": "A", "States": {"A": {"Type": "Task", '
                '"Resource": "arn:aws:states:::lambda:invoke", "End": true}}}'
            )
        },
    )
    g = build_platform_graph(ctx)
    assert not any(e.id == "compute_job:lambda:invoke" for e in g.entities())


def test_controlm_event_correlation(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "defs.json": (
                '{"folder": {"Type": "Folder", '
                '"a": {"Type": "Job", "EventsToAdd": ["E1"]}, '
                '"b": {"Type": "Job", "InConditions": ["E1"]}}}'
            )
        },
    )
    g = build_platform_graph(ctx)
    deps = [r for r in g.relationships() if r.kind.value == "DEPENDS_ON"]
    assert deps and deps[0].evidence_kind is EvidenceKind.DERIVED
    assert deps[0].src == "task:controlm:folder.b"
    assert deps[0].dst == "task:controlm:folder.a"


def test_iceberg_catalog_governs(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "j.py": (
                'spark.conf.set("spark.sql.catalog.prod", '
                '"org.apache.iceberg.spark.SparkCatalog")\n'
                'spark.sql("CREATE TABLE prod.sales (id int) USING iceberg")\n'
            )
        },
    )
    g = build_platform_graph(ctx)
    assert any(
        r.kind.value == "GOVERNS"
        and r.src == "catalog:iceberg:prod"
        and r.dst == "table:iceberg:prod.sales"
        and r.evidence_kind is EvidenceKind.DERIVED
        for r in g.relationships()
    )


def test_cli_graph_json_and_counts(tmp_path: Path) -> None:
    make_context(
        tmp_path,
        {"q.sql": "INSERT INTO mart.daily SELECT a FROM src.s;\n"},
    )
    result = runner.invoke(app, ["platform", "graph", str(tmp_path)])
    assert result.exit_code == 0
    assert "entities" in result.stdout
    result = runner.invoke(app, ["platform", "graph", str(tmp_path), "--json"])
    assert result.exit_code == 0
    import json

    doc = json.loads(result.stdout)
    assert doc["entities"] and doc["relationships"]


def test_cli_blast_radius(tmp_path: Path) -> None:
    make_context(
        tmp_path,
        {"q.sql": "INSERT INTO mart.daily SELECT a FROM src.s;\n"},
    )
    result = runner.invoke(app, ["platform", "blast-radius", "mart.daily", str(tmp_path)])
    assert result.exit_code == 0
    result = runner.invoke(app, ["platform", "blast-radius", "nope", str(tmp_path)])
    assert result.exit_code == 1


# --- spec 181: graph identity hardening -----------------------------------


def test_sql_iceberg_catalog_convergence(tmp_path: Path) -> None:
    """SQL refs under a catalog configured as iceberg land on the same
    ``table:iceberg:`` node the iceberg model mints - same table, one
    entity, deterministic join."""
    ctx = make_context(
        tmp_path,
        {
            "conf.py": (
                'spark.conf.set("spark.sql.catalog.prod", '
                '"org.apache.iceberg.spark.SparkCatalog")\n'
                'spark.sql("CREATE TABLE prod.sales (id int) USING iceberg")\n'
            ),
            "q.sql": "SELECT a FROM prod.sales;\n",
        },
    )
    g = build_platform_graph(ctx)
    assert g.entity("table:sql:prod.sales") is None  # resolved, not literal
    table = g.entity("table:iceberg:prod.sales")
    assert table is not None
    assert any(
        r.kind.value == "READS" and r.dst == "table:iceberg:prod.sales" for r in g.relationships()
    )


def test_sql_non_iceberg_catalog_stays_sql(tmp_path: Path) -> None:
    """A catalog configured with a non-iceberg impl does not claim the
    table - deterministic facts only."""
    ctx = make_context(
        tmp_path,
        {
            "conf.py": (
                'spark.conf.set("spark.sql.catalog.hive", '
                '"org.apache.spark.sql.hive.HiveCatalog")\n'
            ),
            "q.sql": "SELECT a FROM hive.sales;\n",
        },
    )
    g = build_platform_graph(ctx)
    assert g.entity("table:iceberg:hive.sales") is None
    assert g.entity("table:sql:hive.sales") is not None


def test_streaming_sink_toTable_identity(tmp_path: Path) -> None:
    """`toTable("prod.sales")` gives the sink a real identity."""
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                'df = spark.readStream.format("kafka").option("subscribe", "orders").load()\n'
                'q = df.writeStream.format("iceberg").toTable("prod.sales")\n'
            )
        },
    )
    g = build_platform_graph(ctx)
    assert g.entity("stream:kafka:orders") is not None
    assert g.entity("table:iceberg:prod.sales") is not None
    # no phantom table minted from the format name alone
    assert g.entity("table:iceberg:iceberg") is None


def test_streaming_unidentified_sink_is_honest(tmp_path: Path) -> None:
    """`format("iceberg").start()` with no target -> dataset marker,
    never a fake ``table:iceberg:iceberg`` node."""
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                'df = spark.readStream.format("rate").load()\n'
                'q = df.writeStream.format("iceberg").start()\n'
            )
        },
    )
    g = build_platform_graph(ctx)
    assert g.entity("table:iceberg:iceberg") is None
    marker = g.entity("dataset:iceberg:iceberg")
    assert marker is not None
    assert ("identified", "no") in marker.attrs


def test_streaming_nonliteral_sink_mints_nothing_specific(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                'target = cfg["table"]\n'
                'df = spark.readStream.format("rate").load()\n'
                "q = df.writeStream.format('delta').toTable(target)\n"
            )
        },
    )
    g = build_platform_graph(ctx)
    # target is non-literal - no table entity may be minted for it
    assert not any(e.id.startswith("table:delta:") for e in g.entities())


def test_blast_radius_semantic_direction(tmp_path: Path) -> None:
    """DEPENDS_ON traverses inbound: changing a dependency impacts its
    dependents. The dep itself is NOT impacted by the dependent."""
    ctx = make_context(
        tmp_path,
        {
            "dag.py": (
                "from airflow import DAG\n"
                "from airflow.operators.empty import EmptyOperator\n"
                'with DAG("orders") as dag:\n'
                '    a = EmptyOperator(task_id="extract")\n'
                '    b = EmptyOperator(task_id="load")\n'
                "    a >> b\n"
            )
        },
    )
    g = build_platform_graph(ctx)
    from forge_doctor.analyzers.platform_graph_builder import impact_reachable

    assert "task:airflow:load" in impact_reachable(g, "task:airflow:extract")
    assert "task:airflow:extract" not in impact_reachable(g, "task:airflow:load")


def test_blast_radius_table_to_writer_and_reader(tmp_path: Path) -> None:
    """A changed table impacts readers (inbound READS) and writers
    (inbound WRITES) - data-flow edges carry impact both ways."""
    from forge_doctor.analyzers.platform_graph_builder import impact_reachable

    ctx = make_context(
        tmp_path,
        {"q.sql": "INSERT INTO mart.daily SELECT a FROM src.s;\nSELECT a FROM mart.daily;\n"},
    )
    g = build_platform_graph(ctx)
    reached = impact_reachable(g, "table:sql:mart.daily")
    assert any(rid.startswith("query:sql:") for rid in reached)


def test_determinism_full_build(tmp_path: Path) -> None:
    files = {
        "q.sql": "INSERT INTO mart.daily SELECT a FROM src.s;\n",
        "job.py": (
            'df = spark.readStream.format("kafka").load()\n'
            'q = df.writeStream.format("delta").start()\n'
        ),
        "x.tf": 'resource "aws_s3_bucket" "b" {\n  bucket = "raw"\n}\n',
    }
    d1 = build_platform_graph(make_context(tmp_path / "a", files)).to_dict()
    d2 = build_platform_graph(make_context(tmp_path / "b", files)).to_dict()
    # File paths differ (different roots) - compare structure sans paths.
    strip = lambda d: (  # noqa: E731
        sorted((e["id"], e["kind"], e["domain"]) for e in d["entities"]),
        sorted((r["src"], r["dst"], r["kind"]) for r in d["relationships"]),
    )
    assert strip(d1) == strip(d2)
