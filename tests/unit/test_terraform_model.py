"""Unit tests for the TerraformProjectModel (hcl_lite extension)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.terraform_model import terraform_model
from forge_doctor_data.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


MAIN_TF = """\
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 6.0"
    }
  }
  backend "s3" {}
}

provider "aws" {
  region = "us-east-1"
  alias  = "consumer"
}

resource "aws_iam_role" "glue" {
  name = "glue-role"
}

resource "aws_glue_job" "orders" {
  name     = "orders"
  role_arn = aws_iam_role.glue.arn
}

data "aws_caller_identity" "current" {}

variable "env" { type = string }
output "role" { value = aws_iam_role.glue.name }

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"
}
"""


def test_block_facts(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": MAIN_TF})
    model = terraform_model(ctx)
    assert model.tf_files == 1
    assert model.required_version == ">= 1.6"
    assert model.backend == "s3"
    assert "aws" in model.required_providers
    assert model.required_providers["aws"].attrs["version"] == ">= 5.0, < 6.0"
    assert len(model.resources) == 2
    assert len(model.modules) == 1
    assert len(model.data_sources) == 1
    assert len(model.by_kind("variable")) == 1
    assert len(model.by_kind("output")) == 1


def test_provider_alias(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": MAIN_TF})
    model = terraform_model(ctx)
    provider = model.providers[0]
    assert provider.labels == ("aws",)
    assert provider.attrs["alias"] == "consumer"
    assert provider.attrs["region"] == "us-east-1"


def test_reference_edges(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": MAIN_TF})
    model = terraform_model(ctx)
    edge_set = set(model.edges)
    assert ("aws_glue_job.orders", "aws_iam_role.glue") in edge_set
    assert ("output.role", "aws_iam_role.glue") in edge_set


def test_no_terraform(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.py": "x = 1\n"})
    model = terraform_model(ctx)
    assert not model.has_terraform


def test_moved_import_check_blocks(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "main.tf": 'resource "aws_s3_bucket" "b" {}\n'
            "moved { from = aws_s3_bucket.old\n to = aws_s3_bucket.b }\n"
            'import { to = aws_s3_bucket.b\n id = "x" }\n'
            'check "health" { assert { condition = true } }\n'
        },
    )
    model = terraform_model(ctx)
    assert model.by_kind("moved")
    assert model.by_kind("import")
    assert model.by_kind("check")


def test_deterministic(tmp_path: Path) -> None:
    files = {"a.tf": MAIN_TF}
    first = terraform_model(make_context(tmp_path / "p1", files))
    second = terraform_model(make_context(tmp_path / "p2", files))
    key = lambda b: (b.kind, b.address, b.line)  # noqa: E731
    assert sorted(map(key, first.blocks)) == sorted(map(key, second.blocks))
    assert first.edges == second.edges
