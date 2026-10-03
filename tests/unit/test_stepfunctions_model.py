"""Unit tests for the StepFunctionsModel (ASL parsing)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.stepfunctions_model import stepfunctions_model
from forge_doctor_data.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


MACHINE = """{
  "Comment": "orders pipeline",
  "StartAt": "Validate",
  "States": {
    "Validate": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Next": "RunJob",
      "TimeoutSeconds": 60
    },
    "RunJob": {
      "Type": "Task",
      "Resource": "arn:aws:states:::glue:startJobRun.sync",
      "Retry": [{"ErrorEquals": ["States.ALL"], "MaxAttempts": 2}],
      "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "Notify"}],
      "Next": "Done"
    },
    "Notify": {"Type": "Task", "Resource": "arn:aws:states:::sns:publish", "End": true},
    "Done": {"Type": "Succeed"}
  }
}
"""


def test_asl_machine(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"flow.states.json": MACHINE})
    model = stepfunctions_model(ctx)
    assert len(model.machines) == 1
    m = model.machines[0]
    assert m.start_at == "Validate"
    assert len(m.states) == 4
    by_name = {s.name: s for s in m.states}
    assert by_name["Validate"].integration == "lambda"
    assert by_name["RunJob"].integration == "glue:sync"
    assert by_name["RunJob"].retry_count == 1
    assert by_name["RunJob"].catches == ("Notify",)


def test_json_with_startat_marker(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"def.json": MACHINE})
    assert len(stepfunctions_model(ctx).machines) == 1


def test_plain_json_ignored(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"conf.json": '{"a": 1}'})
    assert not stepfunctions_model(ctx).has_machines


def test_terraform_definition(tmp_path: Path) -> None:
    tf = (
        'resource "aws_sfn_state_machine" "pipe" {\n'
        '  name = "pipe"\n'
        '  type = "EXPRESS"\n'
        "  definition = jsonencode({})\n"
        "}\n"
    )
    ctx = make_context(tmp_path, {"main.tf": tf})
    model = stepfunctions_model(ctx)
    assert "pipe" in model.iac_refs


def test_terraform_heredoc_definition(tmp_path: Path) -> None:
    tf = (
        'resource "aws_sfn_state_machine" "pipe" {\n'
        '  name = "pipe"\n'
        "  definition = <<EOT\n" + MACHINE + "EOT\n}\n"
    )
    ctx = make_context(tmp_path, {"main.tf": tf})
    model = stepfunctions_model(ctx)
    assert len(model.machines) == 1
    assert model.machines[0].name == "pipe"


def test_nested_map_states(tmp_path: Path) -> None:
    doc = """{
      "StartAt": "Fan",
      "States": {
        "Fan": {
          "Type": "Map",
          "ItemProcessor": {
            "ProcessorConfig": {"Mode": "DISTRIBUTED"},
            "StartAt": "Work",
            "States": {"Work": {"Type": "Pass", "End": true}}
          },
          "End": true
        }
      }
    }"""
    ctx = make_context(tmp_path, {"m.states.json": doc})
    m = stepfunctions_model(ctx).machines[0]
    assert m.states[0].map_mode == "DISTRIBUTED"
    assert m.nested and m.nested[0].states[0].name == "Work"


def test_deterministic(tmp_path: Path) -> None:
    files = {"m.states.json": MACHINE}
    first = stepfunctions_model(make_context(tmp_path / "p1", files))
    second = stepfunctions_model(make_context(tmp_path / "p2", files))
    key = lambda s: (s.name, s.type, s.next)  # noqa: E731
    assert sorted(map(key, first.all_states)) == sorted(map(key, second.all_states))
