"""Unit tests for DDB001-DDB010 (access-pattern family)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.checks.dynamodb import (
    CHECKS,
    ConstantPKLiteral,
    DynamoUsage,
    GsiDuplicatesBase,
    GsiHotKey,
    GsiVsAccess,
    HotPartition,
    PoorCardinalityPK,
    ScanOnLatencyPath,
    ScanWithoutProjection,
    TimeOnlySortKey,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


BOTO = 'import boto3\ndynamodb = boto3.resource("dynamodb")\ntable = dynamodb.Table("orders")\n'
TF_TABLE = """
resource "aws_dynamodb_table" "orders" {
  name         = "orders"
  hash_key     = "pk"
  range_key    = "sk"
  billing_mode = "PAY_PER_REQUEST"
}
"""


def test_anchor(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_TABLE})
    results = DynamoUsage().run(ctx)
    assert results[0].severity == Severity.INFO
    assert "1 tables" in results[0].message


def test_anchor_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"a.py": "x = 1\n"})
    assert DynamoUsage().run(ctx)[0].severity == Severity.PASS


def test_scan_in_handler_warns(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": BOTO + "def handler(e, c):\n    table.scan()\n"},
    )
    results = ScanOnLatencyPath().run(ctx)
    assert results and results[0].severity == Severity.WARNING


def test_scan_outside_handler_no_ddb002(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": BOTO + "def export():\n    table.scan()\n"},
    )
    assert ScanOnLatencyPath().run(ctx) == []


def test_scan_no_projection(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": BOTO + "table.scan()\n"})
    assert ScanWithoutProjection().run(ctx)


def test_scan_with_filter_clean(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": BOTO + 'table.scan(FilterExpression="a = :x", ProjectionExpression="pk")\n'},
    )
    assert ScanWithoutProjection().run(ctx) == []


def test_low_cardinality_pk(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"infra.tf": 'resource "aws_dynamodb_table" "t" {\n name="t"\n hash_key="status"\n}\n'},
    )
    assert PoorCardinalityPK().run(ctx)


def test_hot_partition_two_writes(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": BOTO
            + (
                'table.put_item(Item={"pk": "CONFIG", "sk": f"{v}"})\n'
                'table.update_item(Key={"pk": "CONFIG", "sk": f"{w}"})\n'
            )
        },
    )
    results = HotPartition().run(ctx)
    assert results and results[0].severity == Severity.WARNING


def test_hot_partition_distinct_clean(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": BOTO
            + (
                'table.put_item(Item={"pk": f"USER#{a}", "sk": "x"})\n'
                'table.put_item(Item={"pk": f"USER#{b}", "sk": "y"})\n'
            )
        },
    )
    assert HotPartition().run(ctx) == []


def test_constant_pk_literal(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": BOTO + 'table.put_item(Item={"pk": "CONFIG", "sk": "x"})\n'},
    )
    assert ConstantPKLiteral().run(ctx)


def test_variable_pk_clean(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": BOTO + 'table.put_item(Item={"pk": f"USER#{uid}", "sk": "x"})\n'},
    )
    assert ConstantPKLiteral().run(ctx) == []


def test_time_only_sort_key(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": BOTO + 'table.put_item(Item={"pk": f"USER#{uid}", "sk": timestamp})\n'},
    )
    assert TimeOnlySortKey().run(ctx)


def test_gsi_duplicates_base(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": (
                'resource "aws_dynamodb_table" "t" {\n name="t"\n hash_key="pk"\n'
                ' global_secondary_index {\n  name="g1"\n  hash_key="pk"\n'
                '  range_key="other"\n  projection_type="ALL"\n }\n}\n'
            )
        },
    )
    assert GsiDuplicatesBase().run(ctx)


def test_gsi_hot_key(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": (
                'resource "aws_dynamodb_table" "t" {\n name="t"\n hash_key="pk"\n'
                ' global_secondary_index {\n  name="g1"\n  hash_key="status"\n'
                '  projection_type="ALL"\n }\n}\n'
            )
        },
    )
    results = GsiHotKey().run(ctx)
    assert results and "status" in results[0].message


def test_gsi_unused(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": (
                'resource "aws_dynamodb_table" "t" {\n name="t"\n hash_key="pk"\n'
                ' global_secondary_index {\n  name="g1"\n  hash_key="other"\n }\n}\n'
            ),
            "job.py": BOTO + 'table.query(KeyConditionExpression="pk=:p")\n',
        },
    )
    results = GsiVsAccess().run(ctx)
    assert results and "g1" in results[0].message


def test_registry_collects_spec_ids() -> None:
    ids = {c.id for c in CHECKS}
    assert {
        "DDB001",
        "DDB002",
        "DDB003",
        "DDB004",
        "DDB005",
        "DDB006",
        "DDB007",
        "DDB008",
        "DDB009",
        "DDB010",
        "DDBSTR001",
        "DDBSTR002",
        "DDBSTR003",
        "DDBSTR004",
        "DDBSTR005",
        "DDBGT001",
        "DDBGT002",
        "DDBGT003",
        "DDBGT005",
    } <= ids
