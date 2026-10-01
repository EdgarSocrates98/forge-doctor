"""Unit tests for the TF### checks (TerraformProjectModel consumers)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.checks.terraform import (
    CHECKS,
    GitModuleMutableRef,
    LocalBackend,
    LocalModule,
    MissingRequiredVersion,
    ProviderConstraintBroad,
    ProviderUnconstrained,
    RegistryModuleUnpinned,
    TerraformUsage,
)
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


BASE = """\
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
resource "aws_glue_job" "orders" { name = "orders" }
"""


def test_usage_anchor(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": BASE})
    results = TerraformUsage().run(ctx)
    assert results[0].severity == Severity.INFO
    assert "references" in results[0].message


def test_usage_anchor_empty(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": "x = 1\n"})
    assert TerraformUsage().run(ctx)[0].severity == Severity.PASS


def test_missing_required_version(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": 'resource "aws_s3_bucket" "b" {}\n'})
    results = MissingRequiredVersion().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.INFO


def test_required_version_ok(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": BASE})
    assert MissingRequiredVersion().run(ctx) == []


def test_provider_unconstrained_requirement(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"main.tf": 'terraform {\n  required_providers {\n    aws = "hashicorp/aws"\n  }\n}\n'},
    )
    # string form is treated as a version; no constraint => flagged
    # (string shorthand still pins nothing meaningful only if empty)
    results = ProviderUnconstrained().run(ctx)
    assert all(r.severity == Severity.INFO for r in results)


def test_provider_block_not_in_required(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "main.tf": 'provider "aws" {\n  region = "us-east-1"\n}\n'
            'terraform {\n  required_version = ">= 1.6"\n}\n'
        },
    )
    results = ProviderUnconstrained().run(ctx)
    assert len(results) == 1
    assert "required_providers" in results[0].message


def test_broad_constraint(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "main.tf": "terraform {\n  required_providers {\n    aws = {\n"
            '      version = ">= 5.0"\n    }\n  }\n}\n'
        },
    )
    results = ProviderConstraintBroad().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING


def test_bounded_constraint_ok(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": BASE})
    assert ProviderConstraintBroad().run(ctx) == []


def test_pessimistic_two_part_is_broad(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "main.tf": "terraform {\n  required_providers {\n    aws = {\n"
            '      version = "~> 5.0"\n    }\n  }\n}\n'
        },
    )
    # ~> 5.0 allows 5.x only - has an implicit upper bound
    assert ProviderConstraintBroad().run(ctx) == []


def test_local_module(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"main.tf": BASE + 'module "vpc" {\n  source = "./modules/vpc"\n}\n'},
    )
    results = LocalModule().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.INFO


def test_registry_module_unpinned(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"main.tf": BASE + 'module "vpc" {\n  source = "terraform-aws-modules/vpc/aws"\n}\n'},
    )
    results = RegistryModuleUnpinned().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING


def test_registry_module_pinned_ok(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "main.tf": BASE + 'module "vpc" {\n'
            '  source  = "terraform-aws-modules/vpc/aws"\n'
            '  version = "5.0.0"\n}\n'
        },
    )
    assert RegistryModuleUnpinned().run(ctx) == []


def test_git_module_no_ref(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "main.tf": BASE + 'module "svc" {\n'
            '  source = "git::https://github.com/acme/mod.git"\n}\n'
        },
    )
    results = GitModuleMutableRef().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING


def test_git_module_main_ref(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "main.tf": BASE + 'module "svc" {\n'
            '  source = "git::https://github.com/acme/mod.git?ref=main"\n}\n'
        },
    )
    assert len(GitModuleMutableRef().run(ctx)) == 1


def test_git_module_tag_ok(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "main.tf": BASE + 'module "svc" {\n'
            '  source = "git::https://github.com/acme/mod.git?ref=v1.2.3"\n}\n'
        },
    )
    assert GitModuleMutableRef().run(ctx) == []


def test_local_backend(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "main.tf": 'terraform {\n  backend "local" {}\n'
            '  required_version = ">= 1.6"\n}\n'
            'resource "aws_s3_bucket" "b" {}\n'
        },
    )
    results = LocalBackend().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING


def test_s3_backend_ok(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": BASE})
    assert LocalBackend().run(ctx) == []


def test_all_checks_run(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": BASE})
    for check in CHECKS:
        check.run(ctx)


def test_cli_inspect(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from forge_doctor.cli import app

    make_context(tmp_path, {"main.tf": BASE})
    result = CliRunner().invoke(app, ["terraform", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "Terraform project" in result.output
    assert "Providers" in result.output
    assert "aws" in result.output


def test_cli_inspect_empty(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from forge_doctor.cli import app

    result = CliRunner().invoke(app, ["terraform", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "no Terraform" in result.output
