"""Cross-domain rule engine (PLAT###) tests - phase 1 of prompt_evo_next_step."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.checks.platform_rules import CHECKS, RULES, _PlatCheck
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.crossdomain import rule_context
from forge_doctor.core.models import EvidenceKind, Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def run_check(check_id: str, ctx: ProjectContext):
    check = next(c for c in CHECKS if c.id == check_id)
    return check.run(ctx)


# --- fixtures ---------------------------------------------------------------

DAG_RETRY = """
from airflow import DAG
from airflow.providers.amazon.aws.operators.glue import GlueJobOperator

with DAG("orders", schedule="@daily", default_args={"retries": 5}) as dag:
    load = GlueJobOperator(task_id="load_orders", job_name="orders-job")
"""

ICEBERG_APPEND = """
df.writeTo("mycat.orders").using("iceberg").append()
"""

ICEBERG_MERGE = """
df.writeTo("mycat.orders").using("iceberg").createOrReplace()
"""

CATALOG_CONF = """
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
spark.conf.set("spark.sql.catalog.mycat", "org.apache.iceberg.spark.SparkCatalog")
"""

ICEBERG_V2_SQL = """
CREATE TABLE mycat.orders (id bigint) USING iceberg
TBLPROPERTIES ('format-version'='2');
SELECT * FROM mycat.orders;
"""

TF_GLUE = """
resource "aws_glue_job" "orders" {
  name         = "orders-job"
  glue_version = "3.0"
}
"""

MERGE_SQL = (
    "MERGE INTO mycat.orders t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.id = s.id;"
)


# --- PLAT001: orchestration retry + non-idempotent sink ---------------------


def test_plat001_true_positive(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "dags/orders.py": DAG_RETRY,
            "glue/jobs.py": ICEBERG_APPEND,
            "glue/conf.py": CATALOG_CONF,
        },
    )
    results = run_check("PLAT001", ctx)
    assert len(results) == 1
    r = results[0]
    assert r.check_id == "PLAT001"
    assert r.severity == Severity.WARNING
    assert r.evidence_kind is EvidenceKind.DERIVED
    # Explainability: contributing facts listed, not a bare label.
    assert "retries=5" in r.message
    assert "append write" in r.message
    assert "no idempotent write evidence" in r.message


def test_plat001_no_finding_when_idempotent_sink(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "dags/orders.py": DAG_RETRY,
            "glue/jobs.py": ICEBERG_MERGE,
            "glue/conf.py": CATALOG_CONF,
        },
    )
    assert run_check("PLAT001", ctx) == []


def test_plat001_no_finding_without_retries(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "dags/orders.py": DAG_RETRY.replace('{"retries": 5}', "{}"),
            "glue/jobs.py": ICEBERG_APPEND,
            "glue/conf.py": CATALOG_CONF,
        },
    )
    assert run_check("PLAT001", ctx) == []


def test_plat001_missing_sink_no_finding(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"dags/orders.py": DAG_RETRY})
    assert run_check("PLAT001", ctx) == []


def test_plat001_task_level_retries(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "dags/orders.py": DAG_RETRY.replace('default_args={"retries": 5}', "").replace(
                'task_id="load_orders"',
                'task_id="load_orders", retries=3',
            ),
            "glue/jobs.py": ICEBERG_APPEND,
            "glue/conf.py": CATALOG_CONF,
        },
    )
    results = run_check("PLAT001", ctx)
    assert results and "retries=3" in results[0].message


# --- PLAT002: runtime/config feature incompatibility ------------------------


def test_plat002_glue3_cannot_merge(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"infra.tf": TF_GLUE, "q.sql": MERGE_SQL, "conf.py": CATALOG_CONF},
    )
    results = run_check("PLAT002", ctx)
    assert len(results) == 1
    assert "orders-job" in results[0].message
    assert "3.0" in results[0].message
    assert results[0].evidence_kind is EvidenceKind.DERIVED


def test_plat002_glue4_supported_no_finding(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": TF_GLUE.replace('"3.0"', '"4.0"'),
            "q.sql": (
                "CREATE TABLE mycat.orders (id bigint) USING iceberg "
                "TBLPROPERTIES ('format-version'='2');\n" + MERGE_SQL
            ),
            "conf.py": CATALOG_CONF,
        },
    )
    assert run_check("PLAT002", ctx) == []


def test_plat002_glue4_v1_table_still_fails(tmp_path: Path) -> None:
    """Glue 4.0 supports MERGE but the table must be format-version=2."""
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": TF_GLUE.replace('"3.0"', '"4.0"'),
            "q.sql": (
                "CREATE TABLE mycat.orders (id bigint) USING iceberg "
                "TBLPROPERTIES ('format-version'='1');\n" + MERGE_SQL
            ),
            "conf.py": CATALOG_CONF,
        },
    )
    results = run_check("PLAT002", ctx)
    assert results and results[0].severity == Severity.WARNING


# --- PLAT003: continuous writer + maintenance gap ---------------------------

STREAM_TO_ICEBERG = """
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
df = spark.readStream.format("kafka").load()
df.writeStream.format("iceberg").outputMode("append").trigger(
    processingTime="5 seconds"
).start("mycat.orders")
"""


def test_plat003_continuous_writer_no_maintenance(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"s.py": STREAM_TO_ICEBERG, "conf.py": CATALOG_CONF},
    )
    results = run_check("PLAT003", ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.INFO
    assert "maintenance" in results[0].message


def test_plat003_maintenance_observed_quiet(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "s.py": STREAM_TO_ICEBERG,
            "maint.sql": "CALL mycat.system.expire_snapshots('mycat.orders');",
            "conf.py": CATALOG_CONF,
        },
    )
    assert run_check("PLAT003", ctx) == []


# --- PLAT004: duplicate orchestration ownership -----------------------------

SFN_ASL = """
{
  "StartAt": "Run",
  "States": {
    "Run": {
      "Type": "Task",
      "Resource": "arn:aws:states:::glue:startJobRun.sync",
      "Parameters": {"JobName": "orders-job"},
      "End": true
    }
  }
}
"""


def test_plat004_two_orchestrators_same_job(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "dags/orders.py": DAG_RETRY,
            "machine.json": SFN_ASL.replace("glue:startJobRun.sync", "aws-sdk:glue:startJobRun"),
            "tf.tf": """
resource "aws_sfn_state_machine" "m" {
  name       = "m"
  definition = "x"
}
""",
        },
    )
    # StepFunctions adapter emits task->target only for integrations it
    # resolves to entity ids; the airflow leg invokes compute_job:glue.
    results = run_check("PLAT004", ctx)
    for r in results:
        assert "orchestrators" in r.message


def test_plat004_single_orchestrator_quiet(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"dags/orders.py": DAG_RETRY})
    assert run_check("PLAT004", ctx) == []


# --- PLAT005: IaC runtime vs source assumptions -----------------------------

PYPROJECT = """
[project]
requires-python = ">=3.10"
"""

TF_LAMBDA_OLD = """
resource "aws_lambda_function" "f" {
  function_name = "f"
  runtime       = "python3.9"
}
"""


def test_plat005_lambda_runtime_below_floor(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"infra.tf": TF_LAMBDA_OLD, "pyproject.toml": PYPROJECT},
    )
    results = run_check("PLAT005", ctx)
    assert len(results) == 1
    assert "python3.9" in results[0].message
    assert ">=3.10" in results[0].message


def test_plat005_aligned_quiet(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": TF_LAMBDA_OLD.replace("python3.9", "python3.11"),
            "pyproject.toml": PYPROJECT,
        },
    )
    assert run_check("PLAT005", ctx) == []


def test_plat005_glue_version_drift(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": TF_GLUE.replace('"3.0"', '"5.0"'),
            "job.py": "from awsglue.context import GlueContext\n"
            'glue.create_job(Name="x", glue_version="4.0")\n',
        },
    )
    results = run_check("PLAT005", ctx)
    assert any("drift" in r.message for r in results)


# --- PLAT006: table format + consumer mismatch -------------------------------


def test_plat006_v2_table_with_reader(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sql": ICEBERG_V2_SQL, "conf.py": CATALOG_CONF},
    )
    results = run_check("PLAT006", ctx)
    assert results and results[0].check_id == "PLAT006"


def test_plat006_v1_table_quiet(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "q.sql": ICEBERG_V2_SQL.replace("'2'", "'1'"),
            "conf.py": CATALOG_CONF,
        },
    )
    assert run_check("PLAT006", ctx) == []


# --- PLAT007: stream sink retry + side-effect idempotency -------------------

STREAM_FOREACH_DDB = """
import boto3
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
d = boto3.resource("dynamodb")
table = d.Table("orders")
df = spark.readStream.format("kafka").load()

def write_batch(bdf, batch_id):
    for row in bdf.collect():
        table.put_item(Item={"pk": row.id})

df.writeStream.foreachBatch(write_batch).start()
"""


def test_plat007_foreachbatch_ddb_no_dedup(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"s.py": STREAM_FOREACH_DDB})
    results = run_check("PLAT007", ctx)
    assert results and results[0].check_id == "PLAT007"


def test_plat007_checkpoint_quiet(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "s.py": STREAM_FOREACH_DDB.replace(
                ".start()",
                '.option("checkpointLocation", "s3://ck").start()',
            )
        },
    )
    assert run_check("PLAT007", ctx) == []


# --- engine mechanics --------------------------------------------------------


def test_rules_sorted_deterministic(tmp_path: Path) -> None:
    """Same inputs produce identical findings regardless of insertion order."""
    files_a = {
        "a.py": ICEBERG_APPEND,
        "conf.py": CATALOG_CONF,
        "z_dag.py": DAG_RETRY,
    }
    files_b = {
        "z_dag.py": DAG_RETRY,
        "conf.py": CATALOG_CONF,
        "a.py": ICEBERG_APPEND,
    }
    ra = [r.message for r in run_check("PLAT001", make_context(tmp_path / "a", files_a))]
    rb = [r.message for r in run_check("PLAT001", make_context(tmp_path / "b", files_b))]
    assert ra and rb
    assert [m.split("(", 1)[0] for m in ra] == [m.split("(", 1)[0] for m in rb]


def test_required_capability_missing_skips_rule(tmp_path: Path) -> None:
    """A rule whose required capability is absent never evaluates."""
    rc = rule_context(make_context(tmp_path, {"job.py": "x = 1"}))
    rule = RULES[1]
    assert rule.required_capabilities == ("ICEBERG_MERGE_WRITE",)
    assert rule.prerequisites_met(rc) is False  # no compute_job -> skip


def test_check_registry_ids() -> None:
    assert {c.id for c in CHECKS} >= {
        "PLAT001",
        "PLAT002",
        "PLAT003",
        "PLAT004",
        "PLAT005",
        "PLAT006",
        "PLAT007",
    }
    assert all(isinstance(c, _PlatCheck) for c in CHECKS)
