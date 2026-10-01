"""Adversarial coverage for the Step Functions model (spec 170)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.stepfunctions_model import stepfunctions_model
from forge_doctor.checks.stepfunctions import UnreachableState
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_truncated_json_graceful(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path, {"m.asl.json": '{"StartAt": "A", "States": {"A": {"Type": "Task"}}'}
    )
    assert stepfunctions_model(ctx).machines == []


def test_states_as_list_graceful(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"m.asl.json": '{"StartAt": "A", "States": []}'})
    assert stepfunctions_model(ctx).machines == []


def test_ghost_start_at_flags_unreachable(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"m.asl.json": ('{"StartAt": "Ghost", "States": {"A": {"Type": "Task", "End": true}}}')},
    )
    model = stepfunctions_model(ctx)
    assert len(model.machines) == 1
    results = UnreachableState().run(ctx)
    assert any(r.severity is Severity.WARNING for r in results)


def test_heredoc_needs_newline_before_terminator(tmp_path: Path) -> None:
    """Documented quirk: ``{...}EOT`` glued to the terminator yields no
    machine - the heredoc regex requires a newline before ``EOT``."""
    ctx = make_context(
        tmp_path,
        {
            "x.tf": (
                'resource "aws_sfn_state_machine" "m" {\n'
                "  definition = <<EOT\n"
                '{"StartAt":"A","States":{"A":{"Type":"Pass","End":true}}}EOT\n'
                "}\n"
            )
        },
    )
    assert stepfunctions_model(ctx).machines == []

    ctx2 = make_context(
        tmp_path / "ok",
        {
            "x.tf": (
                'resource "aws_sfn_state_machine" "m" {\n'
                "  definition = <<EOT\n"
                '{"StartAt":"A","States":{"A":{"Type":"Pass","End":true}}}\n'
                "EOT\n}\n"
            )
        },
    )
    assert len(stepfunctions_model(ctx2).machines) == 1


def test_definition_scoped_per_resource(tmp_path: Path) -> None:
    """Regression: two state machines in one .tf must not cross-attribute
    definitions (was a real bug - whole-file StartAt search)."""
    ctx = make_context(
        tmp_path,
        {
            "x.tf": (
                'resource "aws_sfn_state_machine" "a" {\n'
                "  definition = <<EOT\n"
                '{"StartAt":"One","States":{"One":{"Type":"Pass","End":true}}}\n'
                "EOT\n}\n"
                'resource "aws_sfn_state_machine" "b" {\n'
                "  definition = <<EOT\n"
                '{"StartAt":"Two","States":{"Two":{"Type":"Pass","End":true}}}\n'
                "EOT\n}\n"
            )
        },
    )
    model = stepfunctions_model(ctx)
    starts = {m.name: m.start_at for m in model.machines}
    assert starts == {"a": "One", "b": "Two"}
