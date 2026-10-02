"""Lake Formation deep-intelligence tests: model, checks, cross-account."""

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


def _results(ctx: ProjectContext, check_id: str) -> list:
    check = next(c for c in CHECKS if c.id == check_id)
    return check.run(ctx)


_TF = """
resource "aws_lakeformation_data_lake_settings" "main" {
  admins = ["arn:aws:iam::111122223333:role/LFAdmin"]
  create_table_default_permissions {
    principal   = "IAM_ALLOWED_PRINCIPALS"
    permissions = ["ALL"]
  }
}
resource "aws_lakeformation_permissions" "analyst" {
  principal   = "arn:aws:iam::111122223333:role/AnalystRole"
  permissions = ["SELECT"]
  resource {
    table_with_columns {
      database_name = "analytics"
      name          = "events"
      column_names  = ["id", "ts"]
    }
  }
}
resource "aws_lakeformation_permissions" "external" {
  principal   = "999988887777"
  permissions = ["DESCRIBE"]
  resource {
    database { name = "shared_db" }
  }
}
resource "aws_lakeformation_resource" "lake" {
  arn      = "arn:aws:s3:::data-lake"
  role_arn = "arn:aws:iam::111122223333:role/LFReg"
}
resource "aws_ram_principal_association" "ext" {
  principal          = "999988887777"
  resource_share_arn = "arn:aws:ram:us-east-1:111122223333:resource-share/rs-1"
}
"""


def test_model_full_fixture(tmp_path: Path) -> None:
    _write(tmp_path, "main.tf", _TF)
    m = lakeformation_model(_ctx(tmp_path))
    assert m.has_lakeformation
    assert [a.arn for a in m.admins] == ["arn:aws:iam::111122223333:role/LFAdmin"]
    kinds = {g.resource_kind for g in m.grants}
    assert "columns" in kinds and "database" in kinds and "catalog" in kinds
    assert m.fgac is True
    assert m.hybrid_access is True  # IAM_ALLOWED_PRINCIPALS defaults
    assert m.external_accounts == {"999988887777"}
    loc_arns = [d.arn for d in m.data_locations]
    assert loc_arns == ["arn:aws:s3:::data-lake"]


def test_grant_cross_account_flagging(tmp_path: Path) -> None:
    _write(tmp_path, "main.tf", _TF)
    m = lakeformation_model(_ctx(tmp_path))
    cross = {g.principal for g in m.grants if g.cross_account}
    assert cross == {"999988887777"}
    # same-account role ARN is NOT cross-account
    local = [g for g in m.grants if "AnalystRole" in g.principal]
    assert local and not local[0].cross_account


def test_lf011_default_iam_allowed(tmp_path: Path) -> None:
    _write(tmp_path, "main.tf", _TF)
    results = _results(_ctx(tmp_path), "LF011")
    assert len(results) == 1
    assert "IAM_ALLOWED_PRINCIPALS" in results[0].message


def test_lf013_external_grant_covered_by_ram(tmp_path: Path) -> None:
    _write(tmp_path, "main.tf", _TF)
    assert _results(_ctx(tmp_path), "LF013") == []  # ram principal assoc present


def test_lf013_fires_without_ram(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "main.tf",
        'resource "aws_lakeformation_permissions" "g" {\n'
        '  principal   = "999988887777"\n'
        '  permissions = ["DESCRIBE"]\n'
        '  resource { database { name = "db" } }\n'
        "}\n",
    )
    results = _results(_ctx(tmp_path), "LF013")
    assert len(results) == 1


def test_lf012_dangling_link(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "link.tf",
        'resource "aws_glue_catalog_database" "lnk" {\n'
        '  name = "consumer_link"\n'
        "  target_database {\n"
        '    catalog_id    = "555566667777"\n'
        '    database_name = "prod_db"\n'
        "  }\n}\n",
    )
    results = _results(_ctx(tmp_path), "LF012")
    assert len(results) == 1 and "consumer_link" in results[0].message


def test_lf012_link_referenced_by_grant_is_ok(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "link.tf",
        'resource "aws_glue_catalog_database" "lnk" {\n'
        '  name = "consumer_link"\n'
        "  target_database {\n"
        '    catalog_id    = "555566667777"\n'
        '    database_name = "prod_db"\n'
        "  }\n}\n"
        'resource "aws_lakeformation_permissions" "g" {\n'
        '  principal   = "arn:aws:iam::111122223333:role/R"\n'
        '  permissions = ["SELECT"]\n'
        '  resource { database { name = "consumer_link" } }\n'
        "}\n",
    )
    assert _results(_ctx(tmp_path), "LF012") == []


def test_lf010_no_settings(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "g.tf",
        'resource "aws_lakeformation_permissions" "g" {\n'
        '  principal   = "arn:aws:iam::1:role/R"\n'
        '  permissions = ["SELECT"]\n'
        '  resource { database { name = "db" } }\n'
        "}\n",
    )
    results = _results(_ctx(tmp_path), "LF010")
    assert len(results) == 1


def test_lf016_unused_filter(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "f.tf",
        'resource "aws_lakeformation_data_cells_filter" "f" {\n'
        "  table_data {\n"
        '    database_name    = "analytics"\n'
        '    name             = "region_filter"\n'
        '    table_name       = "events"\n'
        '    column_names     = ["id"]\n'
        "    row_filter {\n"
        "      filter_expression = \"region = 'us'\"\n"
        "    }\n"
        "  }\n}\n",
    )
    m = lakeformation_model(_ctx(tmp_path))
    assert m.filters and m.filters[0].row_filter == "region = 'us'"
    results = _results(_ctx(tmp_path), "LF016")
    assert len(results) == 1 and "region_filter" in results[0].message


def test_lf014_unregistered_location(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "loc.tf",
        'resource "aws_lakeformation_resource" "a" {\n'
        '  arn = "arn:aws:s3:::registered-bucket"\n'
        "}\n"
        'resource "aws_lakeformation_permissions" "g" {\n'
        '  principal   = "arn:aws:iam::1:role/R"\n'
        '  permissions = ["DATA_LOCATION_ACCESS"]\n'
        '  resource { data_location { arn = "arn:aws:s3:::other-bucket" } }\n'
        "}\n",
    )
    results = _results(_ctx(tmp_path), "LF014")
    assert len(results) == 1 and "other-bucket" in results[0].message


def test_lf018_grant_option_external(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "g.tf",
        'resource "aws_lakeformation_permissions" "g" {\n'
        '  principal                     = "999988887777"\n'
        '  permissions                   = ["SELECT"]\n'
        '  permissions_with_grant_option = ["SELECT"]\n'
        '  resource { database { name = "db" } }\n'
        "}\n",
    )
    results = _results(_ctx(tmp_path), "LF018")
    assert len(results) == 1


def test_boto3_grant_permissions(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "admin.py",
        "import boto3\n"
        "lf = boto3.client('lakeformation')\n"
        "lf.grant_permissions(\n"
        "    Principal={'DataLakePrincipalIdentifier': 'arn:aws:iam::111122223333:role/R'},\n"
        "    Resource={'Table': {'DatabaseName': 'db', 'Name': 't'}},\n"
        "    Permissions=['SELECT'],\n"
        ")\n",
    )
    m = lakeformation_model(_ctx(tmp_path))
    grants = [g for g in m.grants if g.source == "boto3"]
    assert len(grants) == 1
    assert grants[0].resource_kind == "table"
    assert "111122223333" in grants[0].principal


def test_platform_graph_governs_edges(tmp_path: Path) -> None:
    """LF grants land as principal->catalog GOVERNS edges in the platform graph."""
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor.core.platform_graph import EntityKind, RelKind

    _write(tmp_path, "main.tf", _TF)
    graph = build_platform_graph(_ctx(tmp_path))
    principals = [e for e in graph.entities(EntityKind.PRINCIPAL) if e.domain == "lakeformation"]
    assert principals
    governs = [r for r in graph.relationships() if r.kind == RelKind.GOVERNS]
    assert any("AnalystRole" in r.src for r in governs)


def test_determinism(tmp_path: Path) -> None:
    _write(tmp_path, "a.tf", _TF)
    _write(
        tmp_path, "z.tf", 'resource "aws_lakeformation_lf_tag" "t" { key = "env" values = ["p"] }'
    )
    a = lakeformation_model(_ctx(tmp_path))
    b = lakeformation_model(_ctx(tmp_path))
    assert [(g.principal, g.resource_name) for g in a.grants] == [
        (g.principal, g.resource_name) for g in b.grants
    ]
    assert a.databases == b.databases
