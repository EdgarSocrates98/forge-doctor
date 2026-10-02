"""Streams + global-tables checks (spec 177) incl. capability-driven MRSC."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.checks.dynamodb import (
    GlobalMrecTransactions,
    GlobalMrscTransactions,
    GlobalRegionRouting,
    GlobalWriteConflict,
    StreamDuplicateProcessing,
    StreamGlobalDuplicate,
    StreamNoConsumer,
    StreamNoIdempotency,
    StreamRecoveryMismatch,
)
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


TF_STREAM = """
resource "aws_dynamodb_table" "orders" {
  name         = "orders"
  hash_key     = "pk"
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"
}
"""

TF_STREAM_CONSUMER = (
    TF_STREAM
    + """
resource "aws_lambda_event_source_mapping" "cdc" {
  event_source_arn = aws_dynamodb_table.orders.stream_arn
  function_name    = aws_lambda_function.cdc.arn
}
"""
)

TF_STREAM_IDEMPOTENT = (
    TF_STREAM
    + """
resource "aws_lambda_event_source_mapping" "cdc" {
  event_source_arn = aws_dynamodb_table.orders.stream_arn
  function_name    = aws_lambda_function.cdc.arn
  function_response_types = ["ReportBatchItemFailures"]
}
"""
)

TF_GLOBAL = """
resource "aws_dynamodb_table" "orders" {
  name         = "orders"
  hash_key     = "pk"
  replica {
    region_name = "us-west-2"
  }
}
provider "aws" {
  region = "us-east-1"
}
"""

TF_GLOBAL_MRSC = """
resource "aws_dynamodb_table" "orders" {
  name         = "orders"
  hash_key     = "pk"
  replica {
    region_name = "us-west-2"
  }
  global_table_witness {
    region_name = "us-west-2"
  }
}
provider "aws" {
  region = "us-east-1"
}
"""

TXN_CODE = (
    'import boto3\nclient = boto3.client("dynamodb")\n'
    "client.transact_write_items(TransactItems=[])\n"
)

WRITE_CODE = (
    'import boto3\ndynamodb = boto3.resource("dynamodb")\n'
    'table = dynamodb.Table("orders")\n'
    'table.put_item(Item={"pk": f"USER#{uid}"})\n'
)


def test_stream_no_consumer(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_STREAM})
    results = StreamNoConsumer().run(ctx)
    assert results and "orders" in results[0].message


def test_stream_with_consumer_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_STREAM_IDEMPOTENT})
    assert StreamNoConsumer().run(ctx) == []


def test_consumer_no_idempotency(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_STREAM_CONSUMER})
    results = StreamNoIdempotency().run(ctx)
    assert results and results[0].severity == Severity.INFO


def test_consumer_idempotent_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_STREAM_IDEMPOTENT})
    assert StreamNoIdempotency().run(ctx) == []


def test_consumer_idempotent_via_code(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": TF_STREAM_CONSUMER,
            "handler.py": "def handler(e, c):\n    return {'batchItemFailures': []}\n",
        },
    )
    assert StreamNoIdempotency().run(ctx) == []


def test_duplicate_consumers(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": TF_STREAM_CONSUMER
            + """
resource "aws_lambda_event_source_mapping" "cdc2" {
  event_source_arn = aws_dynamodb_table.orders.stream_arn
  function_name    = aws_lambda_function.agg.arn
}
"""
        },
    )
    results = StreamDuplicateProcessing().run(ctx)
    assert results and "2 consumers" in results[0].message


def test_recovery_mismatch(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_STREAM})
    assert StreamRecoveryMismatch().run(ctx)


def test_recovery_clean_with_pitr(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": (
                'resource "aws_dynamodb_table" "t" {\n name="t"\n hash_key="pk"\n'
                ' stream_enabled=true\n stream_view_type="KEYS_ONLY"\n'
                " point_in_time_recovery {\n  enabled = true\n }\n}\n"
            )
        },
    )
    assert StreamRecoveryMismatch().run(ctx) == []


def test_global_write_conflict(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_GLOBAL, "job.py": WRITE_CODE})
    results = GlobalWriteConflict().run(ctx)
    assert results and "mrec" in results[0].message


def test_global_no_writes_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_GLOBAL})
    assert GlobalWriteConflict().run(ctx) == []


def test_mrec_transactions_info(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_GLOBAL, "job.py": TXN_CODE})
    results = GlobalMrecTransactions().run(ctx)
    assert results and results[0].severity == Severity.INFO
    assert "region" in results[0].message


def test_mrsc_transactions_error_via_registry(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_GLOBAL_MRSC, "job.py": TXN_CODE})
    results = GlobalMrscTransactions().run(ctx)
    assert results and results[0].severity == Severity.ERROR
    assert "MRSC" in results[0].message


def test_mrsc_no_transactions_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_GLOBAL_MRSC})
    assert GlobalMrscTransactions().run(ctx) == []


def test_region_routing_unclear(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": (
                'resource "aws_dynamodb_table" "t" {\n name="t"\n hash_key="pk"\n'
                ' replica {\n  region_name = "us-west-2"\n }\n}\n'
            ),
            "job.py": WRITE_CODE,
        },
    )
    assert GlobalRegionRouting().run(ctx)


def test_region_routing_clean(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"infra.tf": TF_GLOBAL, "job.py": WRITE_CODE},
    )
    assert GlobalRegionRouting().run(ctx) == []


def test_global_stream_duplicate(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": (
                'resource "aws_dynamodb_table" "orders" {\n name="orders"\n'
                ' hash_key="pk"\n stream_enabled=true\n'
                ' stream_view_type="NEW_IMAGE"\n'
                ' replica {\n  region_name = "us-west-2"\n }\n}\n'
                'resource "aws_lambda_event_source_mapping" "cdc" {\n'
                " event_source_arn = aws_dynamodb_table.orders.stream_arn\n"
                " function_name = fn\n}\n"
            )
        },
    )
    results = StreamGlobalDuplicate().run(ctx)
    assert results and results[0].severity == Severity.WARNING
