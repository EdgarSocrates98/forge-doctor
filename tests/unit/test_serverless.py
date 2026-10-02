"""Athena / Lambda / Step Functions deepening tests."""

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


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ctx(root: Path) -> ProjectContext:
    return ProjectContext(root=root)


def _results(ctx: ProjectContext, check_id: str) -> list:
    check = next(c for c in CHECKS if c.id == check_id)
    return check.run(ctx)


_ATHENA_TF = """
resource "aws_athena_workgroup" "wg" {
  name = "analytics-wg"
  configuration {
    enforce_workgroup_configuration = true
    bytes_scanned_cutoff_per_query  = 10737418240
    result_configuration {
      output_location = "s3://results/athena"
      encryption_configuration { encryption_option = "SSE_KMS" }
    }
    engine_version = "Athena engine version 2"
  }
}
resource "aws_athena_data_catalog" "lake" {
  name = "lake"
  type = "GLUE"
}
resource "aws_athena_prepared_statement" "q" {
  name            = "by-id"
  work_group      = aws_athena_workgroup.wg.id
  query_statement = "SELECT * FROM t WHERE id = ?"
}
"""

_LAMBDA_TF = """
resource "aws_lambda_function" "poller" {
  function_name = "athena-poller"
  runtime       = "python3.8"
  memory_size   = 128
  timeout       = 900
  vpc_config {
    subnet_ids = ["s-1"]
  }
}
resource "aws_lambda_event_source_mapping" "k" {
  function_name    = aws_lambda_function.poller.function_name
  event_source_arn = "arn:aws:kinesis:us-east-1:1:stream/events"
}
resource "aws_lambda_function" "worker" {
  function_name                  = "worker"
  runtime                        = "python3.12"
  timeout                        = 60
  reserved_concurrent_executions = 10
  dead_letter_config { target_arn = "arn:aws:sqs:us-east-1:1:dlq" }
}
"""

_ASL = """{
  "Comment": "pipeline",
  "StartAt": "Extract",
  "States": {
    "Extract": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {"FunctionName": "athena-poller"},
      "Next": "Fan"
    },
    "Fan": {
      "Type": "Map",
      "MaxConcurrency": 100,
      "ItemProcessor": {
        "ProcessorConfig": {"Mode": "DISTRIBUTED"},
        "StartAt": "Work",
        "States": {
          "Work": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {"FunctionName": "worker"},
            "End": true
          }
        }
      },
      "End": true
    }
  }
}
"""

_SQL = """
CREATE TABLE daily AS SELECT * FROM orders;
UNLOAD (SELECT * FROM events) TO 's3://out/unload/';
CREATE TABLE ice_t (id int) LOCATION 's3://lake/ice' TBLPROPERTIES ('table_type' = 'ICEBERG');
"""

_POLLER_PY = """
import boto3
athena = boto3.client("athena")

def handler(event, context):
    qid = athena.start_query_execution(QueryString="SELECT 1")["QueryExecutionId"]
    r = athena.get_query_execution(QueryExecutionId=qid)
    return r["QueryExecution"]["Status"]["State"]
"""


def test_athena_model_full(tmp_path: Path) -> None:
    _write(tmp_path, "athena.tf", _ATHENA_TF)
    _write(tmp_path, "q.sql", _SQL)
    _write(tmp_path, "p.py", _POLLER_PY)
    m = athena_model(_ctx(tmp_path))
    assert m.has_athena
    w = m.workgroups[0]
    assert w.name == "analytics-wg"
    assert w.engine_version == "Athena engine version 2"
    assert w.enforce_config and w.bytes_scanned_cutoff == 10737418240
    assert w.result_location == "s3://results/athena" and w.encryption == "SSE_KMS"
    assert m.catalogs[0].type == "GLUE"
    assert m.named_queries[0].prepared
    ops = {o.op for o in m.sql_ops}
    assert {"ctas", "unload"} <= ops
    assert m.iceberg_ddl
    assert set(m.boto3_calls) == {
        "athena.start_query_execution",
        "athena.get_query_execution",
    }


def test_athena_checks(tmp_path: Path) -> None:
    _write(tmp_path, "athena.tf", _ATHENA_TF)
    _write(tmp_path, "q.sql", _SQL)
    _write(tmp_path, "p.py", _POLLER_PY)
    ctx = _ctx(tmp_path)
    assert _results(ctx, "ATH000")
    # engine 2 + iceberg DDL -> ATH001
    res = _results(ctx, "ATH001")
    assert res and "engine" in res[0].message.lower()
    # workgroup enforces config + cutoff -> ATH002/ATH003 silent
    assert not _results(ctx, "ATH002")
    assert not _results(ctx, "ATH003")
    assert not _results(ctx, "ATH004")  # workgroup exists
    # boto3 poll pair -> ATH005
    assert _results(ctx, "ATH005")


def test_lambda_model_full(tmp_path: Path) -> None:
    _write(tmp_path, "lambda.tf", _LAMBDA_TF)
    m = lambda_model(_ctx(tmp_path))
    assert m.has_lambda
    by_name = {f.name: f for f in m.functions}
    poller = by_name["athena-poller"]
    assert poller.runtime == "python3.8" and poller.memory_mb == 128
    assert poller.timeout_s == 900 and poller.vpc and not poller.dlq
    assert poller.reserved_concurrency == -1
    worker = by_name["worker"]
    assert worker.reserved_concurrency == 10 and worker.dlq
    srcs = [(s.kind, s.function) for s in m.event_sources]
    assert ("kinesis", "poller") in srcs  # tf label ref, not the arn tail


def test_lambda_checks(tmp_path: Path) -> None:
    _write(tmp_path, "lambda.tf", _LAMBDA_TF)
    ctx = _ctx(tmp_path)
    assert _results(ctx, "LAM000")
    res = _results(ctx, "LAM001")
    assert res and "python3.8" in res[0].message and "worker" not in str([r.message for r in res])
    assert _results(ctx, "LAM002")  # poller event-triggered, no DLQ/dest
    assert _results(ctx, "LAM003")  # kinesis trigger, no concurrency bound
    res4 = _results(ctx, "LAM004")
    assert res4 and "900" in res4[0].message
    assert _results(ctx, "LAM005")  # vpc + 128MB


def test_sfn_deepening_model(tmp_path: Path) -> None:
    _write(tmp_path, "m.asl.json", _ASL)
    m = stepfunctions_model(_ctx(tmp_path))
    machine = m.machines[0]
    assert machine.query_language == "JSONPath"
    extract = machine.states[0]
    assert extract.target == "athena-poller"
    assert "Parameters" in extract.payload_keys
    fan = next(s for s in machine.states if s.name == "Fan")
    assert fan.map_mode == "DISTRIBUTED" and fan.max_concurrency == 100
    inner = machine.nested[0]
    assert inner.states[0].target == "worker"


def test_sfn_deepening_checks(tmp_path: Path) -> None:
    _write(tmp_path, "m.asl.json", _ASL)
    ctx = _ctx(tmp_path)
    # DISTRIBUTED map, no retry/catch/tolerance -> SFN031
    assert _results(ctx, "SFN031")
    # JSONPath machine, no JSONata keys -> SFN030 silent
    assert not _results(ctx, "SFN030")
    # no Wait loop -> SFN032 silent
    assert not _results(ctx, "SFN032")


def test_sfn_jsonata_mixing(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "m.asl.json",
        """{
      "StartAt": "T", "States": {
        "T": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
              "Arguments": {"a": "{% $states.input %}"}, "End": true}
      }
    }""",
    )
    res = _results(_ctx(tmp_path), "SFN030")
    assert res and "Arguments" in res[0].message


def test_plat010_sfn_lambda_athena_poller(tmp_path: Path) -> None:
    _write(tmp_path, "m.asl.json", _ASL)
    _write(tmp_path, "lambda.tf", _LAMBDA_TF)
    _write(tmp_path, "p.py", _POLLER_PY)
    rc = rule_context(_ctx(tmp_path))
    rule = next(r for r in RULES if r.id == "PLAT010")
    hits = rule.evaluate(rc)
    assert hits and "sync" in hits[0].message


def test_plat011_map_concurrency_vs_reserved(tmp_path: Path) -> None:
    _write(tmp_path, "m.asl.json", _ASL)
    _write(tmp_path, "lambda.tf", _LAMBDA_TF)
    rc = rule_context(_ctx(tmp_path))
    rule = next(r for r in RULES if r.id == "PLAT011")
    hits = rule.evaluate(rc)
    assert hits and "100" in hits[0].message and "worker" in hits[0].message


def test_serverless_graph_entities(tmp_path: Path) -> None:
    _write(tmp_path, "lambda.tf", _LAMBDA_TF)
    _write(tmp_path, "athena.tf", _ATHENA_TF)
    g = build_platform_graph(_ctx(tmp_path))
    ids = {e.id for e in g.entities()}
    assert "compute_job:lambda:athena-poller" in ids
    assert "compute_job:athena:analytics-wg" in ids
    assert "catalog:athena:lake" in ids
    rels = {(r.kind.value.lower(), r.src, r.dst) for r in g.relationships()}
    # kinesis stream triggers the poller (resolved via tf label)
    assert (
        "triggers",
        "stream:kinesis:arn:aws:kinesis:us-east-1:1:stream/events",
        "compute_job:lambda:athena-poller",
    ) in rels
