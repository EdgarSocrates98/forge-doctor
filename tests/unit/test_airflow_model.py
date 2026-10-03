"""Unit tests for the AirflowModel analyzer."""

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


DAG_BASIC = """\
from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime

with DAG("orders_daily", schedule="@daily", start_date=datetime(2024, 1, 1)) as dag:
    extract = BashOperator(task_id="extract", bash_command="extract.sh")
    load = BashOperator(task_id="load", bash_command="load.sh")

    extract >> load
"""

TASKFLOW = """\
from airflow.decorators import dag, task
from datetime import datetime

@dag(schedule="@hourly", start_date=datetime(2024, 1, 1))
def pipeline():
    @task
    def extract():
        return 1

    @task
    def transform(x):
        return x

    transform(extract())
"""


def test_detects_dag_and_tasks(tmp_path: Path) -> None:
    model = airflow_model(make_context(tmp_path, {"dags/orders.py": DAG_BASIC}))
    assert model.has_airflow
    dag = model.dags[0]
    assert dag.dag_id == "orders_daily"
    assert dag.schedule == "@daily"
    assert dag.task_count == 2
    assert {t.task_id for t in model.tasks} == {"extract", "load"}
    assert all(t.dag for t in model.tasks)


def test_edges_from_shift_chain(tmp_path: Path) -> None:
    src = DAG_BASIC.replace("extract >> load", "extract >> load >> extract")
    model = airflow_model(make_context(tmp_path, {"d.py": src}))
    pairs = {(e.src, e.dst) for e in model.edges}
    assert ("extract", "load") in pairs
    assert ("load", "extract") in pairs


def test_edges_set_downstream_and_chain(tmp_path: Path) -> None:
    src = DAG_BASIC.replace(
        "    extract >> load\n",
        "    extract.set_downstream(load)\n"
        "    t3 = BashOperator(task_id='t3', bash_command='x')\n"
        "    chain(t3, load)\n",
    )
    model = airflow_model(make_context(tmp_path, {"d.py": src}))
    pairs = {(e.src, e.dst) for e in model.edges}
    assert ("extract", "load") in pairs
    assert ("t3", "load") in pairs


def test_taskflow_dag(tmp_path: Path) -> None:
    model = airflow_model(make_context(tmp_path, {"dags/pipe.py": TASKFLOW}))
    assert len(model.dags) == 1
    assert model.dags[0].dag_id == "pipeline"
    taskflow_tasks = [t for t in model.tasks if t.operator == "@task"]
    assert {t.task_id for t in taskflow_tasks} == {"extract", "transform"}
    assert all(t.wired for t in taskflow_tasks)


def test_dynamic_start_date(tmp_path: Path) -> None:
    src = DAG_BASIC.replace("datetime(2024, 1, 1)", "datetime.now()")
    model = airflow_model(make_context(tmp_path, {"d.py": src}))
    assert model.dags[0].start_date_dynamic


def test_static_start_date_not_dynamic(tmp_path: Path) -> None:
    model = airflow_model(make_context(tmp_path, {"d.py": DAG_BASIC}))
    assert not model.dags[0].start_date_dynamic


def test_parse_time_calls(tmp_path: Path) -> None:
    src = "import requests\nimport airflow\nx = requests.get('http://a')\n" + DAG_BASIC
    model = airflow_model(make_context(tmp_path, {"d.py": src}))
    roots = [r for r, _, _ in model.parse_calls]
    assert "requests" in roots


def test_variable_get_at_parse(tmp_path: Path) -> None:
    src = (
        "from airflow.models import Variable\nfrom airflow import DAG\n"
        "v = Variable.get('k')\n" + DAG_BASIC
    )
    model = airflow_model(make_context(tmp_path, {"d.py": src}))
    assert model.variable_gets


def test_sensor_flags(tmp_path: Path) -> None:
    src = DAG_BASIC.replace(
        '    extract = BashOperator(task_id="extract", bash_command="extract.sh")',
        '    extract = TimeSensor(task_id="extract")',
    )
    model = airflow_model(make_context(tmp_path, {"d.py": src}))
    sensor = next(t for t in model.tasks if t.task_id == "extract")
    assert sensor.is_sensor
    assert not sensor.deferrable


def test_providers_collected(tmp_path: Path) -> None:
    src = "from airflow.providers.amazon.aws.operators.glue import GlueJobOperator\n" + DAG_BASIC
    model = airflow_model(make_context(tmp_path, {"d.py": src}))
    assert "amazon" in model.providers


def test_non_airflow_file_ignored(tmp_path: Path) -> None:
    model = airflow_model(make_context(tmp_path, {"job.py": "x = 1\n"}))
    assert not model.has_airflow
    assert model.dags == []


def test_deterministic(tmp_path: Path) -> None:
    files = {"dags/orders.py": DAG_BASIC, "dags/pipe.py": TASKFLOW}
    m1 = airflow_model(make_context(tmp_path, files))
    m2 = airflow_model(make_context(tmp_path, files))
    assert [(d.dag_id, d.line) for d in m1.dags] == [(d.dag_id, d.line) for d in m2.dags]
    assert [(t.var, t.line) for t in m1.tasks] == [(t.var, t.line) for t in m2.tasks]
