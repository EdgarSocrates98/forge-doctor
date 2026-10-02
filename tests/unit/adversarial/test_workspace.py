"""WorkspaceModel adversarial tests - spoofed links, collisions, empties."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.core.context import ProjectContext
from forge_doctor.core.platform_graph import RelKind
from forge_doctor.core.workspace import build_workspace_model, discover_repos

_TF_JOB = """
resource "aws_glue_job" "orders" {
  name     = "orders-etl"
  role_arn = "arn:aws:iam::123456789012:role/glue"
}
"""


def _model(root: Path):
    return build_workspace_model(root, ProjectContext(root=root))


def test_spoofed_filename_without_glue_code_no_implements(tmp_path: Path) -> None:
    """A file named like the job but with no glue evidence must not link."""
    (tmp_path / "terraform-repo").mkdir()
    (tmp_path / "terraform-repo" / "main.tf").write_text(_TF_JOB)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "pyproject.toml").write_text('[project]\nname = "s"\n')
    (tmp_path / "scripts" / "orders-etl.py").write_text("import os\nprint(os.getcwd())\n")
    model = _model(tmp_path)
    assert not any(lnk.kind is RelKind.IMPLEMENTS for lnk in model.links)


def test_self_defines_and_invokes_produces_no_self_link(tmp_path: Path) -> None:
    """One repo declaring and orchestrating the same job is internal only."""
    (tmp_path / "platform" / "dags").mkdir(parents=True)
    (tmp_path / "platform" / "main.tf").write_text(_TF_JOB)
    (tmp_path / "platform" / "dags" / "d.py").write_text(
        "from airflow import DAG\n"
        "from airflow.providers.amazon.aws.operators.glue import GlueJobOperator\n"
        "with DAG(dag_id='d'):\n"
        "    GlueJobOperator(task_id='t', job_name='orders-etl')\n"
    )
    model = _model(tmp_path)
    assert not any(
        lnk.source_repo == lnk.target_repo and lnk.kind is RelKind.INVOKES for lnk in model.links
    )


def test_empty_workspace_is_safe(tmp_path: Path) -> None:
    model = _model(tmp_path)
    assert len(model.repositories) == 1
    assert model.links == ()
    assert model.graph.entities() != []  # the root repo entity exists


def test_underscore_hyphen_name_normalization(tmp_path: Path) -> None:
    """``orders_etl.py`` implements glue job ``orders-etl`` (normalized)."""
    (tmp_path / "terraform-repo").mkdir()
    (tmp_path / "terraform-repo" / "main.tf").write_text(_TF_JOB)
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "pyproject.toml").write_text('[project]\nname = "j"\n')
    (tmp_path / "jobs" / "orders_etl.py").write_text("from awsglue.context import GlueContext\n")
    model = _model(tmp_path)
    assert any(
        lnk.kind is RelKind.IMPLEMENTS
        and lnk.source_repo == "jobs"
        and lnk.entity_id == "compute_job:glue:orders-etl"
        for lnk in model.links
    )


def test_duplicate_defines_first_repo_wins(tmp_path: Path) -> None:
    """Two repos declaring the same job id must not crash or duplicate."""
    for name in ("a-infra", "b-infra"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "main.tf").write_text(_TF_JOB)
    model = _model(tmp_path)
    defines = [lnk for lnk in model.links if lnk.kind is RelKind.DEFINES]
    assert len(defines) == 1
    assert defines[0].source_repo == "a-infra"  # deterministic: sorted order
    assert [e.id for e in model.graph.entities()].count("compute_job:glue:orders-etl") == 1


def test_marker_file_at_root_names_repo_after_root(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_JOB)
    repos = discover_repos(tmp_path, ProjectContext(root=tmp_path))
    assert len(repos) == 1
    assert repos[0].path == "."
    assert repos[0].name == tmp_path.name


def test_internal_task_edges_not_repo_links(tmp_path: Path) -> None:
    """A repo's own airflow tasks must not become repo-level INVOKES links."""
    (tmp_path / "dags").mkdir()
    (tmp_path / "dags" / "d.py").write_text(
        "from airflow import DAG\n"
        "from airflow.operators.bash import BashOperator\n"
        "with DAG(dag_id='d'):\n"
        "    a = BashOperator(task_id='a', bash_command='true')\n"
        "    b = BashOperator(task_id='b', bash_command='true')\n"
        "    a >> b\n"
    )
    model = _model(tmp_path)
    assert not any(
        lnk.entity_id.startswith("task:") and lnk.kind is RelKind.INVOKES for lnk in model.links
    )
