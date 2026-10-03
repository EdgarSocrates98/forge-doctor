import json
from pathlib import Path

from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.graph import build_graph


def _project(tmp_path: Path) -> ProjectContext:
    (tmp_path / "job.py").write_text(
        "from pyspark.sql import SparkSession\n"
        "spark.read.table('staging.orders')\n"
        "df.write.saveAsTable('mart.out')\n",
        encoding="utf-8",
    )
    (tmp_path / "dag.py").write_text(
        "from airflow import DAG\n# runs job.py\nwith DAG('etl'):\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "infra.tf").write_text(
        'resource "aws_glue_job" "job_etl" {\n  glue_version = "4.0"\n}\n',
        encoding="utf-8",
    )
    return ProjectContext(root=tmp_path)


def test_graph_nodes_and_edges(tmp_path: Path):
    graph = build_graph(_project(tmp_path))
    kinds = {n.kind for n in graph.nodes.values()}
    assert {"repo", "job", "dataset", "orchestrator", "infra"} <= kinds
    edge_kinds = {e.kind for e in graph.edges}
    assert "reads" in edge_kinds and "writes" in edge_kinds
    assert "contains" in edge_kinds


def test_graph_json_contract(tmp_path: Path):
    payload = build_graph(_project(tmp_path)).to_dict()
    assert payload["schema_version"] == "1.0"
    assert all({"id", "kind", "label"} <= set(n) for n in payload["nodes"])
    assert all({"source", "target", "type"} <= set(e) for e in payload["edges"])
    json.dumps(payload)  # serializable


def test_graph_renderers(tmp_path: Path):
    graph = build_graph(_project(tmp_path))
    assert graph.to_dot().startswith("digraph")
    assert graph.to_mermaid().startswith("flowchart")


def test_empty_project(tmp_path: Path):
    graph = build_graph(ProjectContext(root=tmp_path))
    assert any(n.kind == "repo" for n in graph.nodes.values())
