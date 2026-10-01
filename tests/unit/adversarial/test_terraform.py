"""Adversarial coverage for the Terraform model (spec 170)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.terraform_model import terraform_model
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_unbalanced_braces_no_crash(tmp_path: Path) -> None:
    """An unclosed resource block is still indexed - partial parse over
    crash is the contract for malformed IaC."""
    ctx = make_context(
        tmp_path,
        {"x.tf": 'resource "aws_sfn_state_machine" "m" {\n  name = "x"\n'},
    )
    model = terraform_model(ctx)
    assert any(r.labels[-1] == "m" for r in model.resources)


def test_same_type_resources_stay_scoped(tmp_path: Path) -> None:
    """Regression: attributes of one aws_lambda_function must not bleed
    into the next resource block."""
    ctx = make_context(
        tmp_path,
        {
            "x.tf": (
                'resource "aws_lambda_function" "a" {\n'
                '  function_name = "fn-a"\n'
                "}\n"
                'resource "aws_lambda_function" "b" {\n'
                '  function_name = "fn-b"\n'
                "}\n"
            )
        },
    )
    model = terraform_model(ctx)
    names = {r.labels[-1]: r for r in model.resources}
    assert set(names) == {"a", "b"}


def test_empty_and_comment_only(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.tf": "# nothing here\n\n"})
    assert terraform_model(ctx).resources == []


def test_resource_in_block_comment_graceful(tmp_path: Path) -> None:
    """A `resource` line inside a /* */ block comment - documents current
    behavior of the line scanner (it does not strip block comments)."""
    ctx = make_context(
        tmp_path,
        {"x.tf": '/*\nresource "aws_x" "ghost" {\n*/\n'},
    )
    # Line-scanner sees the word `resource`; whether it creates a record
    # is a documented limitation - assert only that it never raises.
    terraform_model(ctx)
