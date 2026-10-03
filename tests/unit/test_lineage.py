from pathlib import Path

from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.lineage import build_lineage

_SPARK_IMPORT = "from pyspark.sql import SparkSession\n\n"


def _project(tmp_path: Path, code: str) -> ProjectContext:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    (tmp_path / "job.py").write_text(code, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_table_read_and_write(tmp_path: Path):
    ctx = _project(
        tmp_path,
        _SPARK_IMPORT + "spark.read.table('staging.orders')\n"
        "df.write.saveAsTable('mart.orders_daily')\n",
    )
    graph = build_lineage(ctx)
    assert any(e.source == "staging.orders" and e.kind == "reads" for e in graph.edges)
    assert any(e.target == "mart.orders_daily" and e.kind == "writes" for e in graph.edges)


def test_reads_formats(tmp_path: Path):
    ctx = _project(
        tmp_path,
        _SPARK_IMPORT + "spark.read.parquet('s3://b/x')\ndf.write.parquet('s3://b/y')\n",
    )
    graph = build_lineage(ctx)
    datasets = set(graph.datasets)
    assert "s3://b/x" in datasets and "s3://b/y" in datasets


def test_sql_lineage(tmp_path: Path):
    ctx = _project(
        tmp_path,
        _SPARK_IMPORT + "spark.sql('INSERT INTO t2 SELECT * FROM t1 JOIN t3 ON t1.id = t3.id')",
    )
    graph = build_lineage(ctx)
    reads = {e.source for e in graph.edges if e.kind == "reads"}
    assert "t1" in reads and "t3" in reads
    assert any(e.target == "t2" and e.kind == "writes" for e in graph.edges)


def test_renderers(tmp_path: Path):
    ctx = _project(tmp_path, _SPARK_IMPORT + "spark.read.table('a')\ndf.write.saveAsTable('b')\n")
    graph = build_lineage(ctx)
    payload = graph.to_dict()
    assert payload["schema_version"] == "1.0"
    assert payload["edges"]
    assert "digraph" in graph.to_dot()
    assert "flowchart" in graph.to_mermaid()
    ol = graph.to_openlineage()
    assert ol["producer"].startswith("https://")
    events = ol["events"]
    assert events, "expected at least one RunEvent"
    event = events[0]
    for key in ("eventType", "eventTime", "producer", "schemaURL", "run", "job"):
        assert key in event, key
    assert event["job"]["namespace"] == "demo"
    assert event["run"]["runId"]
    # Deterministic: rebuilding yields identical runIds.
    assert graph.to_openlineage()["events"][0]["run"]["runId"] == event["run"]["runId"]
    inputs = {i["name"] for i in event["inputs"]}
    outputs = {o["name"] for o in event["outputs"]}
    assert "a" in inputs and "b" in outputs


def test_non_spark_file_ignored(tmp_path: Path):
    ctx = _project(tmp_path, "import os\nprint(os.listdir())\n")
    graph = build_lineage(ctx)
    assert graph.edges == []
