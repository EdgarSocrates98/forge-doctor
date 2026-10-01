"""Unit tests for the AIR### checks (AirflowModel consumers)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.checks.airflow import (
    CHECKS,
    AirflowUsage,
    DuplicateDagId,
    DynamicStartDate,
    EmptyDag,
    OrphanTask,
    ParseTimeExternalCall,
    ParseTimeVariableGet,
    RetriesNoDelay,
    SensorNoTimeout,
    SensorPokeMode,
    UndeclaredProvider,
)
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


DAG = """\
from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime

with DAG("orders", schedule="@daily", start_date=datetime(2024, 1, 1)) as dag:
    extract = BashOperator(task_id="extract", bash_command="e.sh")
    load = BashOperator(task_id="load", bash_command="l.sh")
    extract >> load
"""


def test_anchor_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"a.py": "x = 1\n"})
    assert AirflowUsage().run(ctx)[0].severity == Severity.PASS


def test_anchor_counts(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"d.py": DAG})
    result = AirflowUsage().run(ctx)[0]
    assert result.severity == Severity.INFO
    assert "1 dags" in result.message
    assert "2 tasks" in result.message


def test_duplicate_dag_id(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"a.py": DAG, "b.py": DAG})
    results = DuplicateDagId().run(ctx)
    assert len(results) == 1
    assert "orders" in results[0].message


def test_empty_dag(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "d.py": "from airflow import DAG\nfrom datetime import datetime\n"
            "d = DAG('e', start_date=datetime(2024,1,1))\n"
        },
    )
    results = EmptyDag().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING


def test_dag_with_tasks_ok(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"d.py": DAG})
    assert EmptyDag().run(ctx) == []


def test_orphan_task(tmp_path: Path) -> None:
    src = DAG.replace(
        "    extract >> load\n",
        "    extract >> load\n    stray = BashOperator(task_id='stray', bash_command='s.sh')\n",
    )
    ctx = make_context(tmp_path, {"d.py": src})
    results = OrphanTask().run(ctx)
    assert any("stray" in r.message for r in results)


def test_all_wired_ok(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"d.py": DAG})
    assert OrphanTask().run(ctx) == []


def test_dynamic_start_date(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"d.py": DAG.replace("datetime(2024, 1, 1)", "datetime.now()")})
    results = DynamicStartDate().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING


def test_static_start_date_ok(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"d.py": DAG})
    assert DynamicStartDate().run(ctx) == []


def test_parse_time_external(tmp_path: Path) -> None:
    src = "import requests\n" + DAG.replace(
        "with DAG", "data = requests.get('http://api')\n\nwith DAG"
    )
    ctx = make_context(tmp_path, {"d.py": src})
    results = ParseTimeExternalCall().run(ctx)
    assert len(results) == 1
    assert "requests" in results[0].message


def test_call_inside_task_body_not_parse_time(tmp_path: Path) -> None:
    src = DAG + "\ndef _helper():\n    import requests\n    return requests.get('http://x')\n"
    ctx = make_context(tmp_path, {"d.py": src})
    assert ParseTimeExternalCall().run(ctx) == []


def test_variable_get_parse_time(tmp_path: Path) -> None:
    src = DAG.replace(
        "with DAG", "from airflow.models import Variable\nv = Variable.get('x')\n\nwith DAG"
    )
    ctx = make_context(tmp_path, {"d.py": src})
    results = ParseTimeVariableGet().run(ctx)
    assert len(results) == 1


def test_sensor_poke_mode(tmp_path: Path) -> None:
    src = DAG.replace(
        '    extract = BashOperator(task_id="extract", bash_command="e.sh")',
        '    extract = TimeSensor(task_id="extract")',
    )
    src = "from airflow.sensors.date_time import TimeSensor\n" + src
    ctx = make_context(tmp_path, {"d.py": src})
    assert len(SensorPokeMode().run(ctx)) == 1
    assert len(SensorNoTimeout().run(ctx)) == 1


def test_sensor_deferrable_ok(tmp_path: Path) -> None:
    src = "from airflow.sensors.date_time import TimeSensor\n" + DAG.replace(
        '    extract = BashOperator(task_id="extract", bash_command="e.sh")',
        '    extract = TimeSensor(task_id="extract", deferrable=True, timeout=60)',
    )
    ctx = make_context(tmp_path, {"d.py": src})
    assert SensorPokeMode().run(ctx) == []
    assert SensorNoTimeout().run(ctx) == []


def test_retries_no_delay(tmp_path: Path) -> None:
    src = DAG.replace(
        'bash_command="e.sh")',
        'bash_command="e.sh", retries=5)',
    )
    ctx = make_context(tmp_path, {"d.py": src})
    results = RetriesNoDelay().run(ctx)
    assert len(results) == 1
    assert "retries=5" in results[0].message


def test_retries_with_delay_ok(tmp_path: Path) -> None:
    src = DAG.replace(
        'bash_command="e.sh")',
        'bash_command="e.sh", retries=5, retry_delay=60)',
    )
    ctx = make_context(tmp_path, {"d.py": src})
    assert RetriesNoDelay().run(ctx) == []


def test_undeclared_provider(tmp_path: Path) -> None:
    src = "from airflow.providers.amazon.aws.operators.glue import GlueJobOperator\n" + DAG
    ctx = make_context(
        tmp_path,
        {
            "d.py": src,
            "pyproject.toml": '[project]\nname = "x"\ndependencies = ["apache-airflow"]\n',
        },
    )
    results = UndeclaredProvider().run(ctx)
    assert len(results) == 1
    assert "apache-airflow-providers-amazon" in results[0].message


def test_declared_provider_ok(tmp_path: Path) -> None:
    src = "from airflow.providers.amazon.aws.operators.glue import GlueJobOperator\n" + DAG
    ctx = make_context(
        tmp_path,
        {
            "d.py": src,
            "pyproject.toml": '[project]\nname = "x"\n'
            'dependencies = ["apache-airflow", "apache-airflow-providers-amazon"]\n',
        },
    )
    assert UndeclaredProvider().run(ctx) == []


def test_provider_no_pyproject_silent(tmp_path: Path) -> None:
    src = "from airflow.providers.amazon.aws.operators.glue import GlueJobOperator\n" + DAG
    ctx = make_context(tmp_path, {"d.py": src})
    assert UndeclaredProvider().run(ctx) == []


def test_all_checks_category(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"d.py": DAG})
    for check in CHECKS:
        for r in check.run(ctx):
            assert r.check_id == check.id
            assert r.category == "airflow"


def _cli(args: list[str]):
    from typer.testing import CliRunner

    from forge_doctor.cli import app

    return CliRunner().invoke(app, args)


def test_cli_inspect(tmp_path: Path) -> None:
    make_context(tmp_path, {"d.py": DAG})
    result = _cli(["airflow", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "Airflow" in result.output
    assert "orders" in result.output


def test_cli_inspect_empty(tmp_path: Path) -> None:
    result = _cli(["airflow", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "no Airflow evidence" in result.output


def test_cli_bare_group() -> None:
    result = _cli(["airflow"])
    assert result.exit_code == 2
