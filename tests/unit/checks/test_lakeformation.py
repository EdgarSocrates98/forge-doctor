"""Unit tests for the LF### checks (Lake Formation IaC facts)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.checks.lakeformation import (
    CHECKS,
    HybridAccessAmbiguity,
    LakeFormationUsage,
    ResourceLinkWithoutShare,
)
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


TF_LF = 'resource "aws_lakeformation_permissions" "p" {\n  principal = "arn:aws:iam::1:role/j"\n}\n'
TF_LINK = (
    'resource "aws_glue_catalog_table" "linked" {\n'
    '  database_name = "shared"\n'
    '  target_table = { catalog_id = "123456789012" }\n'
    "}\n"
)
TF_RAM = 'resource "aws_ram_resource_share" "share" {\n  name = "catalog"\n}\n'
TF_LFTAG = 'resource "aws_lakeformation_lf_tag" "env" {\n  key = "env"\n}\n'
TF_PRINCIPALS = (
    'resource "aws_lakeformation_permissions" "iam" {\n'
    '  data_lake_principal_identifier = "IAM_ALLOWED_PRINCIPALS"\n'
    "}\n"
)

CFN_LINK = (
    '{"Resources": {"LinkDb": {"Type": "AWS::Glue::Database",'
    ' "Properties": {"TargetDatabase": "shared_db"}}}}'
)


def test_usage_anchor(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": TF_LF})
    results = LakeFormationUsage().run(ctx)
    assert results[0].severity == Severity.INFO
    assert "aws_lakeformation_permissions" in results[0].message


def test_usage_anchor_empty(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": "x = 1\n"})
    assert LakeFormationUsage().run(ctx)[0].severity == Severity.PASS


def test_resource_link_without_ram_hcl(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"links.tf": TF_LINK})
    results = ResourceLinkWithoutShare().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert results[0].file == Path("links.tf")


def test_resource_link_without_ram_cfn(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"template.json": CFN_LINK})
    assert len(ResourceLinkWithoutShare().run(ctx)) == 1


def test_resource_link_ok_when_ram_present(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"links.tf": TF_LINK, "ram.tf": TF_RAM})
    assert ResourceLinkWithoutShare().run(ctx) == []


def test_resource_link_ignores_plain_resources(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": TF_LF})
    assert ResourceLinkWithoutShare().run(ctx) == []


def test_hybrid_access_both_legs(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"grants.tf": TF_PRINCIPALS, "tags.tf": TF_LFTAG})
    results = HybridAccessAmbiguity().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "grants.tf" in results[0].message


def test_hybrid_access_needs_both_legs(tmp_path: Path) -> None:
    only_principals = make_context(tmp_path / "a", {"grants.tf": TF_PRINCIPALS})
    assert HybridAccessAmbiguity().run(only_principals) == []
    only_tags = make_context(tmp_path / "b", {"tags.tf": TF_LFTAG})
    assert HybridAccessAmbiguity().run(only_tags) == []


def test_checks_list_registered() -> None:
    ids = [c.id for c in CHECKS]
    assert ids == ["LF000", "LF001", "LF002"]
