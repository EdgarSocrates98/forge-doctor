"""Adversarial fixtures for the DynamoDB model - FP/FN/malformed."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.dynamodb_model import dynamodb_model
from forge_doctor_data.checks.dynamodb import (
    ConstantPKLiteral,
    ScanOnLatencyPath,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_comment_mention_not_evidence(tmp_path: Path) -> None:
    """'dynamodb'/'scan' in a comment must not create DynamoDB evidence."""
    ctx = make_context(
        tmp_path,
        {"job.py": "# we used to scan dynamodb here\ntotal = sum(xs)\n"},
    )
    model = dynamodb_model(ctx)
    assert not model.accesses and not model.has_dynamodb


def test_string_literal_not_evidence(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": 'note = "call table.scan() then put_item()"\n'},
    )
    assert not dynamodb_model(ctx).accesses


def test_non_dynamodb_query_call_not_captured(tmp_path: Path) -> None:
    """pandas/sqlalchemy ``query``/``scan``-shaped calls stay silent."""
    ctx = make_context(
        tmp_path,
        {"job.py": ("session = make_session()\nsession.query(User).all()\nscanner.scan()\n")},
    )
    assert dynamodb_model(ctx).accesses == []


def test_table_factory_on_foreign_receiver_silent(tmp_path: Path) -> None:
    """``x.Table(...)`` without dynamodb provenance does not bind."""
    ctx = make_context(
        tmp_path,
        {"job.py": 'meta = open_meta()\nt = meta.Table("t")\nt.get_item(Key={"k": "v"})\n'},
    )
    assert dynamodb_model(ctx).accesses == []


def test_dynamodb_attr_on_unrelated_object(tmp_path: Path) -> None:
    """A var named like a client but never bound to dynamodb: silent."""
    ctx = make_context(
        tmp_path,
        {"job.py": 'client = thing()\nclient.get_item(TableName="t")\n'},
    )
    # TableName kwarg alone is not enough without a bound dynamodb client.
    assert dynamodb_model(ctx).accesses == []


def test_malformed_tf_table_no_crash(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "infra.tf": (
                'resource "aws_dynamodb_table" "broken" {\n'
                '  name = "x"\n  hash_key = "pk"\n'
                '  global_secondary_index {\n    name = "unclosed"\n'
            )
        },
    )
    model = dynamodb_model(ctx)  # must not raise
    assert model.tables  # partial extraction is fine, crash is not


def test_malformed_python_no_crash(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": "def broken(:\n    table.scan(\n"},
    )
    model = dynamodb_model(ctx)
    assert model.accesses == []


def test_lowercase_entity_prefix_ignored(tmp_path: Path) -> None:
    """Lowercase strings like 'user#1' are not entity prefixes."""
    ctx = make_context(
        tmp_path,
        {"job.py": 'import boto3\nd = boto3.client("dynamodb")\nx = f"user#{i}"\n'},
    )
    assert "user" not in dynamodb_model(ctx).single_table.entities


def test_generic_table_name_not_confused(tmp_path: Path) -> None:
    """`table` receiver unbound to dynamodb must not register ops."""
    ctx = make_context(
        tmp_path,
        {"job.py": "table = get_table()\ntable.put_item(Item={'a': 1})\n"},
    )
    assert not dynamodb_model(ctx).accesses


def test_scan_handler_only_when_bound(tmp_path: Path) -> None:
    """scan() in a handler w/o dynamodb binding is not a DDB finding."""
    ctx = make_context(
        tmp_path,
        {"job.py": "def handler(e, c):\n    fs.scan()\n"},
    )
    assert ScanOnLatencyPath().run(ctx) == []


def test_constant_pk_ignores_reads(tmp_path: Path) -> None:
    """DDB006 targets writes - reads with constant keys are fine."""
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                'import boto3\ndynamodb = boto3.resource("dynamodb")\n'
                'table = dynamodb.Table("t")\n'
                'def handler(e, c):\n    table.get_item(Key={"pk": "CONFIG"})\n'
            )
        },
    )
    assert ConstantPKLiteral().run(ctx) == []


def test_no_error_severity_except_mrsc(tmp_path: Path) -> None:
    """Only DDBGT003 may emit ERROR (registry-proven unsupported)."""
    from forge_doctor_data.checks.dynamodb import CHECKS

    ctx = make_context(
        tmp_path,
        {
            "infra.tf": (
                'resource "aws_dynamodb_table" "t" {\n name="t"\n hash_key="pk"\n'
                ' stream_enabled=true\n replica {\n  region_name="us-west-2"\n }\n}\n'
            ),
            "job.py": (
                'import boto3\nd = boto3.client("dynamodb")\n'
                'd.scan()\nd.put_item(Item={"pk": "C"})\n'
            ),
        },
    )
    for check in CHECKS:
        for r in check.run(ctx):
            if check.id != "DDBGT003":
                assert r.severity != Severity.ERROR, check.id
