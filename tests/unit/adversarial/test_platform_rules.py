"""Adversarial cases for the cross-domain rule engine.

The engine must stay quiet when a prerequisite leg is absent, when the
"retries" literal is not a number, and when sinks are transactional.
"""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.checks.platform_rules import CHECKS
from forge_doctor_data.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def _run(check_id: str, ctx: ProjectContext):
    check = next(c for c in CHECKS if c.id == check_id)
    return check.run(ctx)


def test_retries_string_literal_not_counted(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "dags/x.py": (
                "from airflow import DAG\n"
                'with DAG("x", default_args={"retries": "many"}):\n'
                "    pass\n"
            ),
            "glue/jobs.py": 'df.writeTo("c.t").using("iceberg").append()\n',
        },
    )
    assert _run("PLAT001", ctx) == []


def test_glue_operator_without_retry_is_quiet(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "dags/x.py": (
                "from airflow import DAG\n"
                "from airflow.providers.amazon.aws.operators.glue import GlueJobOperator\n"
                'with DAG("x") as dag:\n'
                '    t = GlueJobOperator(task_id="t", job_name="j")\n'
            ),
            "glue/jobs.py": 'df.writeTo("c.t").using("iceberg").append()\n',
        },
    )
    assert _run("PLAT001", ctx) == []


def test_foreachbatch_lambda_wrapped_handler(tmp_path: Path) -> None:
    """foreachBatch(lambda) without sink writes in the file - no finding."""
    ctx = make_context(
        tmp_path,
        {
            "s.py": (
                "from pyspark.sql import SparkSession\n"
                "spark = SparkSession.builder.getOrCreate()\n"
                'df = spark.readStream.format("kafka").load()\n'
                "df.writeStream.foreachBatch(lambda d, b: d.show()).start()\n"
            )
        },
    )
    assert _run("PLAT007", ctx) == []


def test_controlm_only_no_invokes_no_plat004(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"controlm.json": ('{"Type":"Folder","order":{"Type":"Job","JobName":"j1"}}')},
    )
    assert _run("PLAT004", ctx) == []
