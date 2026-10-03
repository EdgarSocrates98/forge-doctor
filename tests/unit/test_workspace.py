"""WorkspaceModel (roadmap-2 phase 5) - multi-repo intelligence tests."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.platform_graph import RelKind
from forge_doctor_data.core.workspace import (
    build_workspace_model,
    discover_repos,
)

_TF_JOB = """
resource "aws_glue_job" "orders" {
  name     = "orders-etl"
  role_arn = "arn:aws:iam::123456789012:role/glue"
  command {
    script_location = "s3://jobs/orders-etl.py"
  }
}
"""

_GLUE_SRC = """
from awsglue.context import GlueContext
from pyspark.context import SparkContext

glueContext = GlueContext(SparkContext.getOrCreate())
"""

_DAG = """
from airflow import DAG
from airflow.providers.amazon.aws.operators.glue import GlueJobOperator

with DAG(dag_id="daily_load"):
    GlueJobOperator(task_id="load", job_name="orders-etl")
"""


def _three_repo_ws(tmp_path: Path) -> Path:
    (tmp_path / "terraform-repo").mkdir()
    (tmp_path / "terraform-repo" / "main.tf").write_text(_TF_JOB)
    (tmp_path / "glue-jobs").mkdir()
    (tmp_path / "glue-jobs" / "pyproject.toml").write_text('[project]\nname = "glue-jobs"\n')
    (tmp_path / "glue-jobs" / "orders-etl.py").write_text(_GLUE_SRC)
    (tmp_path / "airflow-dags" / "dags").mkdir(parents=True)
    (tmp_path / "airflow-dags" / "dags" / "daily.py").write_text(_DAG)
    return tmp_path


def _model(root: Path):
    return build_workspace_model(root, ProjectContext(root=root))


def test_discover_repos_by_marker(tmp_path: Path) -> None:
    root = _three_repo_ws(tmp_path)
    repos = discover_repos(root, ProjectContext(root=root))
    by_name = {r.name: r for r in repos}
    assert set(by_name) == {"terraform-repo", "glue-jobs", "airflow-dags"}
    assert "main.tf" in by_name["terraform-repo"].markers
    assert "dags/" in by_name["airflow-dags"].markers
    assert by_name["glue-jobs"].languages == ("python",)


def test_no_markers_falls_back_to_root_repo(tmp_path: Path) -> None:
    (tmp_path / "job.py").write_text("print('x')\n")
    repos = discover_repos(tmp_path, ProjectContext(root=tmp_path))
    assert len(repos) == 1
    assert repos[0].path == "."
    assert repos[0].markers == ()


def test_nested_marker_dirs_collapse_to_outermost(tmp_path: Path) -> None:
    (tmp_path / "infra" / "modules" / "vpc").mkdir(parents=True)
    (tmp_path / "infra" / "main.tf").write_text(_TF_JOB)
    (tmp_path / "infra" / "modules" / "vpc" / "vpc.tf").write_text('resource "aws_vpc" "x" {}\n')
    repos = discover_repos(tmp_path, ProjectContext(root=tmp_path))
    assert [r.path for r in repos] == ["infra"]


def test_workspace_model_cross_repo_links(tmp_path: Path) -> None:
    root = _three_repo_ws(tmp_path)
    model = _model(root)
    links = {(lnk.kind, lnk.source_repo, lnk.entity_id): lnk for lnk in model.links}
    defines = [lnk for lnk in model.links if lnk.kind is RelKind.DEFINES]
    assert any(
        lnk.source_repo == "terraform-repo" and lnk.entity_id == "compute_job:glue:orders-etl"
        for lnk in defines
    )
    impl = links.get((RelKind.IMPLEMENTS, "glue-jobs", "compute_job:glue:orders-etl"))
    assert impl is not None and impl.target_repo == "terraform-repo"
    inv = links.get((RelKind.INVOKES, "airflow-dags", "compute_job:glue:orders-etl"))
    assert inv is not None and inv.target_repo == "terraform-repo"


def test_workspace_graph_converges_on_shared_entity(tmp_path: Path) -> None:
    model = _model(_three_repo_ws(tmp_path))
    ents = [e.id for e in model.graph.entities()]
    assert ents.count("compute_job:glue:orders-etl") == 1
    assert "repo:workspace:terraform-repo" in ents
    # repo edges point at the shared entity
    repo_edges = [r for r in model.graph.relationships() if r.src.startswith("repo:workspace:")]
    assert {r.kind for r in repo_edges} == {RelKind.DEFINES, RelKind.IMPLEMENTS, RelKind.INVOKES}


def test_repo_of_lookup(tmp_path: Path) -> None:
    model = _model(_three_repo_ws(tmp_path))
    assert model.repo_of("compute_job:glue:orders-etl") == "terraform-repo"
    assert model.repo_of("compute_job:glue:missing") is None


def test_workspace_model_deterministic(tmp_path: Path) -> None:
    root = _three_repo_ws(tmp_path)
    a = _model(root)
    b = _model(root)
    assert [(lnk.kind.value, lnk.source_repo, lnk.entity_id) for lnk in a.links] == [
        (lnk.kind.value, lnk.source_repo, lnk.entity_id) for lnk in b.links
    ]
    assert [e.id for e in a.graph.entities()] == [e.id for e in b.graph.entities()]


def test_single_repo_workspace_no_links(tmp_path: Path) -> None:
    # Everything in one repo: defines + invokes the job itself.
    (tmp_path / "main.tf").write_text(_TF_JOB)
    (tmp_path / "dags").mkdir()
    (tmp_path / "dags" / "daily.py").write_text(_DAG)
    model = _model(tmp_path)
    assert len(model.repositories) == 1
    invokes = [lnk for lnk in model.links if lnk.kind is RelKind.INVOKES]
    assert invokes == []  # self-invocation is not a cross-repo link
