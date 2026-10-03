"""Unit tests for the Step Functions checks (SFN000-SFN020)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.checks.stepfunctions import (
    CHECKS,
    ChoiceNoDefault,
    DeadEndState,
    DistributedMapInExpress,
    SyncTaskNoTimeout,
    UnreachableState,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


SIMPLE = """{
  "StartAt": "A",
  "States": {
    "A": {"Type": "Pass", "Next": "B"},
    "B": {"Type": "Succeed"},
    "Orphan": {"Type": "Pass", "End": true}
  }
}
"""


def _results(check_cls, ctx):
    return check_cls().run(ctx)


def test_sfn000_anchor(tmp_path: Path) -> None:
    empty = make_context(tmp_path / "empty", {"x.txt": "hi"})
    assert _results(type(CHECKS[0]), empty)[0].severity == Severity.PASS
    ctx = make_context(tmp_path / "proj", {"m.states.json": SIMPLE})
    res = _results(type(CHECKS[0]), ctx)[0]
    assert res.severity == Severity.INFO and "1 machines" in res.message


def test_unreachable_state(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"m.states.json": SIMPLE})
    res = _results(UnreachableState, ctx)
    assert any("Orphan" in r.message for r in res)


def test_dead_end(tmp_path: Path) -> None:
    doc = """{
      "StartAt": "A",
      "States": {
        "A": {"Type": "Task", "Resource": "x"},
        "Done": {"Type": "Succeed"}
      }
    }"""
    ctx = make_context(tmp_path, {"m.states.json": doc})
    res = _results(DeadEndState, ctx)
    assert any("'A'" in r.message for r in res)


def test_choice_no_default(tmp_path: Path) -> None:
    doc = """{
      "StartAt": "C",
      "States": {
        "C": {
          "Type": "Choice",
          "Choices": [{"Variable": "$.x", "NumericEquals": 1, "Next": "B"}]
        },
        "B": {"Type": "Succeed"}
      }
    }"""
    ctx = make_context(tmp_path, {"m.states.json": doc})
    res = _results(ChoiceNoDefault, ctx)
    assert res and res[0].severity == Severity.INFO


def test_sync_no_timeout(tmp_path: Path) -> None:
    doc = """{
      "StartAt": "T",
      "States": {
        "T": {
          "Type": "Task",
          "Resource": "arn:aws:states:::glue:startJobRun.sync",
          "End": true
        }
      }
    }"""
    ctx = make_context(tmp_path, {"m.states.json": doc})
    res = _results(SyncTaskNoTimeout, ctx)
    assert len(res) == 1


def test_distributed_map_in_express(tmp_path: Path) -> None:
    doc = """{
      "StartAt": "M",
      "States": {
        "M": {
          "Type": "Map",
          "ItemProcessor": {
            "ProcessorConfig": {"Mode": "DISTRIBUTED"},
            "StartAt": "W",
            "States": {"W": {"Type": "Pass", "End": true}}
          },
          "End": true
        }
      }
    }"""
    tf = (
        'resource "aws_sfn_state_machine" "m" {\n'
        '  type = "EXPRESS"\n'
        "  definition = <<EOT\n" + doc + "\nEOT\n}\n"
    )
    ctx = make_context(tmp_path, {"main.tf": tf})
    res = _results(DistributedMapInExpress, ctx)
    assert len(res) == 1
    assert "EXPRESS" in res[0].message


def test_all_checks_run(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"m.states.json": SIMPLE})
    for check in CHECKS:
        check.run(ctx)


def test_cli_inspect(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from forge_doctor_data.cli import app

    make_context(tmp_path, {"m.states.json": SIMPLE})
    result = CliRunner().invoke(app, ["stepfunctions", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "Step Functions" in result.output
    assert "SFN002" in result.output  # Orphan is unreachable


def test_cli_inspect_empty(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from forge_doctor_data.cli import app

    result = CliRunner().invoke(app, ["stepfunctions", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "no state machines" in result.output
