"""Adversarial coverage for the Airflow model (spec 170).

Includes the regression for aliased ``DAG`` imports - ``from airflow
import DAG as Dag`` previously produced no DAG record.
"""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.airflow_model import airflow_model
from forge_doctor_data.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_aliased_dag_import_with_block(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"d.py": 'from airflow import DAG as Dag\nwith Dag("x"):\n    pass\n'},
    )
    assert len(airflow_model(ctx).dags) == 1


def test_aliased_dag_import_assignment(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"d.py": 'from airflow import DAG as Dag\nd = Dag("y")\n'},
    )
    assert len(airflow_model(ctx).dags) == 1


def test_aliased_dag_decorator(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"d.py": ("from airflow.decorators import dag as my_dag\n@my_dag()\ndef pipe(): ...\n")},
    )
    assert len(airflow_model(ctx).dags) == 1


def test_plain_dag_forms_still_work(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"d.py": 'import airflow\nwith airflow.DAG("y"):\n    pass\n'},
    )
    assert len(airflow_model(ctx).dags) == 1


def test_dag_name_without_airflow_import_no_fp(tmp_path: Path) -> None:
    """A file with a ``dag`` helper but no airflow import is not flagged
    as an airflow file - no DAG record."""
    ctx = make_context(tmp_path, {"d.py": "def dag(): ...\ndag()\n"})
    model = airflow_model(ctx)
    assert model.dags == []


def test_syntax_error_file_graceful(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"d.py": "import airflow\ndef broken(:\n"},
    )
    model = airflow_model(ctx)
    assert model.dags == []
