"""Adversarial Lake Formation tests: malformed IaC, spoofed principals,
cross-account boundary errors, non-LF noise."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.lakeformation_model import lakeformation_model
from forge_doctor.checks.lakeformation import CHECKS
from forge_doctor.core.context import ProjectContext


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ctx(root: Path) -> ProjectContext:
    return ProjectContext(root=root)


def test_no_lf_no_findings(tmp_path: Path) -> None:
    _write(tmp_path, "s3.tf", 'resource "aws_s3_bucket" "b" { bucket = "x" }')
    ctx = _ctx(tmp_path)
    m = lakeformation_model(ctx)
    assert not m.has_lakeformation
    for check in CHECKS:
        if check.id in ("LF000",):  # anchor always reports
            continue
        assert check.run(ctx) == []


def test_malformed_tf_block_does_not_crash(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "broken.tf",
        'resource "aws_lakeformation_permissions" "g" {\n'
        "  principal = \n"
        '  resource { database { name = "db" }\n'  # unbalanced
        "  permissions = [",
    )
    ctx = _ctx(tmp_path)
    m = lakeformation_model(ctx)  # must not raise
    for check in CHECKS:
        check.run(ctx)  # must not raise
    assert isinstance(m.grants, list)


def test_lf_grant_arn_not_misflagged_cross_account(tmp_path: Path) -> None:
    """An IAM-role ARN matching the admin's account is not external."""
    _write(
        tmp_path,
        "main.tf",
        'resource "aws_lakeformation_data_lake_settings" "s" {\n'
        '  admins = ["arn:aws:iam::111122223333:role/Admin"]\n'
        "}\n"
        'resource "aws_lakeformation_permissions" "g" {\n'
        '  principal   = "arn:aws:iam::111122223333:role/Worker"\n'
        '  permissions = ["SELECT"]\n'
        '  resource { table { database_name = "d" name = "t" } }\n'
        "}\n",
    )
    m = lakeformation_model(_ctx(tmp_path))
    assert m.external_accounts == set()
    assert all(not g.cross_account for g in m.grants)
    assert _results(ctx=_ctx(tmp_path), check_id="LF013") == []


def test_bare_account_id_is_always_external(tmp_path: Path) -> None:
    """LF grants to a bare 12-digit account are account-level shares."""
    _write(
        tmp_path,
        "g.tf",
        'resource "aws_lakeformation_permissions" "g" {\n'
        '  principal   = "444455556666"\n'
        '  permissions = ["DESCRIBE"]\n'
        '  resource { database { name = "db" } }\n'
        "}\n",
    )
    m = lakeformation_model(_ctx(tmp_path))
    assert m.external_accounts == {"444455556666"}
    assert m.grants[0].cross_account


def test_unbalanced_nested_blocks_do_not_leak_attrs(tmp_path: Path) -> None:
    """A nested-block parse failure must not fabricate grant fields."""
    _write(
        tmp_path,
        "weird.tf",
        'resource "aws_lakeformation_permissions" "g" {\n'
        '  principal = "arn:aws:iam::1:role/R"\n'
        "  resource {\n"
        "    table {\n"
        '      database_name = "d"\n'
        # block never closed - body ends mid-block
        "  \n",
    )
    m = lakeformation_model(_ctx(tmp_path))
    # grant may exist (flat attrs) but must not crash or invent resource names
    for g in m.grants:
        assert g.resource_kind in {
            "database",
            "table",
            "columns",
            "lf_tag",
            "lf_tag_expression",
            "data_location",
            "data_cells_filter",
            "catalog",
        }


def test_unknown_service_calls_not_attributed(tmp_path: Path) -> None:
    """``x.grant_permissions()`` on a non-lakeformation client is ignored."""
    _write(
        tmp_path,
        "app.py",
        "import boto3\n"
        "sfn = boto3.client('stepfunctions')\n"
        "sfn.grant_permissions(Principal='arn:aws:iam::999988887777:role/X')\n",
    )
    m = lakeformation_model(_ctx(tmp_path))
    assert not any(g.source == "boto3" for g in m.grants)
    assert m.external_accounts == set()


def test_iam_policy_actions_collected_not_governance(tmp_path: Path) -> None:
    """IAM policies mentioning lakeformation: are evidence, not LF grants."""
    _write(
        tmp_path,
        "p.tf",
        'resource "aws_iam_policy" "p" {\n'
        "  policy = jsonencode({\n"
        "    Statement = [{\n"
        '      Action = ["lakeformation:GetDataAccess", "glue:GetTable"]\n'
        '      Resource = "*"\n'
        "    }]\n"
        "  })\n}\n",
    )
    m = lakeformation_model(_ctx(tmp_path))
    assert "lakeformation:GetDataAccess" in m.iam_lf_actions
    assert m.grants == []  # IAM policy statements are not LF grants


def _results(ctx: ProjectContext, check_id: str) -> list:
    check = next(c for c in CHECKS if c.id == check_id)
    return check.run(ctx)
