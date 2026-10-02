"""Adversarial tests for Athena / Lambda / SFN deepening.

False-positive resistance, missing evidence, spoofed names, determinism.
"""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.athena_model import athena_model
from forge_doctor.analyzers.lambda_model import lambda_model
from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
from forge_doctor.analyzers.stepfunctions_model import stepfunctions_model
from forge_doctor.checks.platform_rules import RULES
from forge_doctor.checks.serverless import CHECKS
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.crossdomain import rule_context
from forge_doctor.core.models import Severity


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ctx(root: Path) -> ProjectContext:
    return ProjectContext(root=root)


def _problems(ctx: ProjectContext, prefix: tuple[str, ...]) -> list:
    out = []
    for c in CHECKS:
        if c.id.startswith(prefix):
            out.extend(r for r in c.run(ctx) if r.severity != Severity.PASS)
    return out


def test_empty_project_no_findings(tmp_path: Path) -> None:
    _write(tmp_path, "readme.md", "# nothing\n")
    ctx = _ctx(tmp_path)
    assert not athena_model(ctx).has_athena
    assert not lambda_model(ctx).has_lambda
    assert not stepfunctions_model(ctx).has_machines
    assert not _problems(ctx, ("ATH", "LAM", "SFN03"))


def test_athena_named_substrings_no_fp(tmp_path: Path) -> None:
    """Names containing 'athena'/'lambda' must not mint platform entities."""
    _write(
        tmp_path,
        "x.tf",
        """
resource "aws_s3_bucket" "athena_results" { bucket = "not-athena" }
resource "aws_iam_role" "lambda_exec" { name = "lambda-exec-role" }
""",
    )
    ctx = _ctx(tmp_path)
    assert not athena_model(ctx).workgroups
    assert not lambda_model(ctx).functions


def test_generic_sql_not_athena_ops(tmp_path: Path) -> None:
    """Plain SELECT/INSERT statements are not Athena CTAS/UNLOAD evidence."""
    _write(
        tmp_path,
        "generic.sql",
        "SELECT * FROM t;\nINSERT INTO t VALUES (1);\nCREATE TABLE t (a INT);\n",
    )
    m = athena_model(_ctx(tmp_path))
    assert not m.sql_ops
    assert not m.iceberg_ddl


def test_lambda_boto3_wrong_service_no_fp(tmp_path: Path) -> None:
    """`boto3.client('lambda')` used where 'lambda' is a variable, not the
    service name, plus a lambda client that never calls anything."""
    _write(
        tmp_path,
        "job.py",
        """
import boto3
lam = boto3.client("lambda")   # never invoked
s3 = boto3.client("s3")
s3.list_buckets()
""",
    )
    m = lambda_model(_ctx(tmp_path))
    # a client binding alone is not an API call - no lambda.* calls recorded
    assert not m.boto3_calls


def test_function_ref_spoof(tmp_path: Path) -> None:
    """An event mapping referencing a function not in the project must not
    attach findings to unrelated functions."""
    _write(
        tmp_path,
        "x.tf",
        """
resource "aws_lambda_function" "local" {
  function_name = "local-fn"
  runtime       = "python3.12"
}
resource "aws_lambda_event_source_mapping" "ext" {
  function_name    = "arn:aws:lambda:us-east-1:9999:function/external"
  event_source_arn = "arn:aws:kinesis:us-east-1:1:stream/ext"
}
""",
    )
    ctx = _ctx(tmp_path)
    m = lambda_model(ctx)
    src = m.event_sources[0]
    assert src.function != "local"  # arn tail, not the local function
    # LAM002/003 must not flag local-fn: no matching trigger evidence
    assert not _results_by_id(ctx, "LAM002")
    assert not _results_by_id(ctx, "LAM003")


def _results_by_id(ctx: ProjectContext, check_id: str) -> list:
    check = next(c for c in CHECKS if c.id == check_id)
    return [r for r in check.run(ctx) if r.severity != Severity.PASS]


def test_plat011_absent_without_map(tmp_path: Path) -> None:
    """A reserved-concurrency cap alone never produces PLAT011."""
    _write(
        tmp_path,
        "x.tf",
        """
resource "aws_lambda_function" "f" {
  function_name                  = "f"
  runtime                        = "python3.12"
  reserved_concurrent_executions = 1
}
""",
    )
    rc = rule_context(_ctx(tmp_path))
    rule = next(r for r in RULES if r.id == "PLAT011")
    assert not rule.evaluate(rc)


def test_plat010_absent_without_poll_pair(tmp_path: Path) -> None:
    """SFN lambda tasks alone must not claim an Athena poller."""
    _write(
        tmp_path,
        "m.asl.json",
        """{
      "StartAt": "T", "States": {
        "T": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
              "Parameters": {"FunctionName": "f"}, "End": true}
      }
    }""",
    )
    _write(
        tmp_path,
        "p.py",
        'import boto3\nathena = boto3.client("athena")\nathena.list_named_queries()\n',
    )
    rc = rule_context(_ctx(tmp_path))
    rule = next(r for r in RULES if r.id == "PLAT010")
    assert not rule.evaluate(rc)


def test_jsonata_machine_not_flagged(tmp_path: Path) -> None:
    """JSONata keys under an explicit JSONata machine are legal -> silent."""
    _write(
        tmp_path,
        "m.asl.json",
        """{
      "StartAt": "T", "QueryLanguage": "JSONata", "States": {
        "T": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
              "Arguments": {"x": "{% $states.input %}"}, "End": true}
      }
    }""",
    )
    assert not _results_by_id(_ctx(tmp_path), "SFN030")


def test_determinism_graph_and_findings(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.tf",
        """
resource "aws_lambda_function" "f" {
  function_name = "f" runtime = "python3.9" timeout = 30
}
resource "aws_athena_workgroup" "w" { name = "w" }
""",
    )
    _write(tmp_path, "q.sql", "UNLOAD (SELECT 1) TO 's3://o/';\n")
    a = [(r.check_id, r.message) for c in CHECKS for r in c.run(_ctx(tmp_path))]
    b = [(r.check_id, r.message) for c in CHECKS for r in c.run(_ctx(tmp_path))]
    assert a == b
    g1 = build_platform_graph(_ctx(tmp_path))
    g2 = build_platform_graph(_ctx(tmp_path))
    assert sorted(e.id for e in g1.entities()) == sorted(e.id for e in g2.entities())
