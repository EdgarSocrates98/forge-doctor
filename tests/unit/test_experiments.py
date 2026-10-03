"""Unit tests for the experiment engine (core/experiments.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor_data.cli import app
from forge_doctor_data.core.experiments import (
    HYPOTHESES,
    Hypothesis,
    run_experiment,
)

runner = CliRunner()

_STREAM_NO_CHECKPOINT = """from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
df = spark.readStream.format("kafka").load()
q = df.writeStream.format("delta").start("s3://out/orders")
"""


def _scenario(root: Path, files: dict[str, str]) -> Path:
    scenario = root / "exp-scenario"
    for rel, text in files.items():
        path = scenario / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return scenario


def test_add_checkpoint_improves(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, {"stream.py": _STREAM_NO_CHECKPOINT})
    result = run_experiment(scenario, "add-checkpoint")
    assert result.verdict == "improved"
    assert result.changed_files == ["stream.py"]
    assert any(r["check_id"] == "STREAM002" for r in result.resolved_findings)
    # info-level fingerprint shifts are allowed; no warning/error regressions
    assert all(r["severity"] == "info" for r in result.introduced_findings)


def test_noop_hypothesis_reports_neutral(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, {"jobs.py": "x = 1\n"})
    result = run_experiment(scenario, "add-checkpoint")
    assert result.verdict == "neutral"
    assert result.changed_files == []
    assert "no files" in result.reasons[0]


def test_already_checkpointed_scenario_is_neutral(tmp_path: Path) -> None:
    scenario = _scenario(
        tmp_path,
        {
            "stream.py": _STREAM_NO_CHECKPOINT.replace(
                '.writeStream.format("delta")',
                '.writeStream.option("checkpointLocation", "s3://ck").format("delta")',
            )
        },
    )
    result = run_experiment(scenario, "add-checkpoint")
    assert result.verdict == "neutral"


def test_unknown_hypothesis_errors(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, {"jobs.py": "x = 1\n"})
    with pytest.raises(KeyError, match="unknown hypothesis"):
        run_experiment(scenario, "does-not-exist")


def test_regressing_hypothesis_reports_regressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _remove_checkpoint(root: Path) -> list[str]:
        path = root / "stream.py"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace('.option("checkpointLocation", "s3://ck")', ""), encoding="utf-8"
        )
        return ["stream.py"]

    monkeypatch.setitem(
        HYPOTHESES, "drop-checkpoint", Hypothesis("drop-checkpoint", "t", _remove_checkpoint)
    )
    scenario = _scenario(
        tmp_path,
        {
            "stream.py": _STREAM_NO_CHECKPOINT.replace(
                '.writeStream.format("delta")',
                '.writeStream.option("checkpointLocation", "s3://ck").format("delta")',
            )
        },
    )
    result = run_experiment(scenario, "drop-checkpoint")
    assert result.verdict == "regressed"
    assert any(r["check_id"] == "STREAM002" for r in result.introduced_findings)


def test_experiment_never_mutates_fixture(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, {"stream.py": _STREAM_NO_CHECKPOINT})
    before = (scenario / "stream.py").read_bytes()
    run_experiment(scenario, "add-checkpoint")
    assert (scenario / "stream.py").read_bytes() == before


def test_experiment_is_deterministic(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, {"stream.py": _STREAM_NO_CHECKPOINT})
    a = run_experiment(scenario, "add-checkpoint").to_dict()
    b = run_experiment(scenario, "add-checkpoint").to_dict()
    assert a == b


def test_bump_glue_version_rewrites_tf(tmp_path: Path) -> None:
    scenario = _scenario(
        tmp_path,
        {"main.tf": 'resource "aws_glue_job" "etl" {\n  glue_version = "4.0"\n}\n'},
    )
    result = run_experiment(scenario, "bump-glue-version")
    assert result.changed_files == ["main.tf"]
    assert result.stats["files_before"] == result.stats["files_after"]


def test_result_json_shape(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, {"stream.py": _STREAM_NO_CHECKPOINT})
    data = run_experiment(scenario, "add-checkpoint").to_dict()
    for key in (
        "scenario",
        "hypothesis",
        "verdict",
        "resolved_findings",
        "introduced_findings",
        "stats",
        "changed_files",
    ):
        assert key in data


# --- CLI --------------------------------------------------------------------


def test_cli_experiment_improved(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, {"stream.py": _STREAM_NO_CHECKPOINT})
    result = runner.invoke(
        app, ["lab", "experiment", str(scenario), "--hypothesis", "add-checkpoint"]
    )
    assert result.exit_code == 0, result.output
    assert "IMPROVED" in result.output


def test_cli_experiment_unknown_hypothesis(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, {"jobs.py": "x = 1\n"})
    result = runner.invoke(app, ["lab", "experiment", str(scenario), "--hypothesis", "bogus"])
    assert result.exit_code == 1
    assert "unknown hypothesis" in result.output


def test_cli_experiment_json(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, {"stream.py": _STREAM_NO_CHECKPOINT})
    result = runner.invoke(
        app,
        ["lab", "experiment", str(scenario), "--hypothesis", "add-checkpoint", "--json"],
    )
    assert result.exit_code == 0, result.output
    import json

    data = json.loads(result.output)
    assert data["verdict"] == "improved"
    assert data["resolved_findings"]
