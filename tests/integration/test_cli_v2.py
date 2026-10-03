import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor_data.cli import app

runner = CliRunner()


def test_emit_multiple_formats(tmp_path: Path):
    (tmp_path / "job.py").write_text("import pyspark\n", encoding="utf-8")
    sarif = tmp_path / "out.sarif"
    json_out = tmp_path / "out.json"
    runner.invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--emit",
            "text",
            "--emit",
            f"sarif:{sarif}",
            "--emit",
            f"json:{json_out}",
        ],
    )
    assert sarif.exists() and json_out.exists()
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "3.0"
    assert payload["tool"]["name"] == "forge-doctor-data"


def test_emit_rejects_two_stdout_targets(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--emit", "text", "--emit", "json"])
    assert result.exit_code != 0


def test_jsonl_output(tmp_path: Path):
    (tmp_path / "x.py").write_text("import pyspark\nspark.read.parquet('s3://a')\n")
    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "jsonl"])
    lines = [ln for ln in result.output.splitlines() if ln.strip().startswith("{")]
    assert lines
    for ln in lines:
        item = json.loads(ln)
        assert "check_id" in item


def test_doctor(tmp_path: Path):
    result = runner.invoke(app, ["doctor", str(tmp_path)])
    assert result.exit_code == 0
    assert "git" in result.output


def test_suppressions_command(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.forge-doctor-data]\n"
        "[[tool.forge-doctor-data.suppressions]]\n"
        'rule = "SPARK001"\nreason = "legacy"\nowner = "team"\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["suppressions", str(tmp_path)])
    assert result.exit_code == 0
    assert "SPARK001" in result.output


def test_diagnose_cli(tmp_path: Path):
    log = tmp_path / "driver.log"
    log.write_text("java.lang.OutOfMemoryError: Java heap space\n", encoding="utf-8")
    result = runner.invoke(app, ["diagnose", str(log)])
    assert result.exit_code == 1  # findings exit non-zero
    result_json = runner.invoke(app, ["diagnose", str(log), "--format", "json"])
    assert result_json.exit_code == 0
    assert json.loads(result_json.output)["findings"]


def test_spark_eventlog_cli(tmp_path: Path):
    log = tmp_path / "eventlog"
    log.write_text('{"Event": "SparkListenerExecutorRemoved"}\n', encoding="utf-8")
    result = runner.invoke(app, ["spark", "eventlog", str(log), "--format", "json"])
    assert result.exit_code == 0
    assert "RT001" in result.output


def test_trace_cli(tmp_path: Path):
    (tmp_path / "job.py").write_text(
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "df = spark.read.parquet('s3://bucket/x')\n"
        "df.collect()\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["trace", "SPARK002", "job.py:4", "--path", str(tmp_path), "--json"]
    )
    # either traced or no finding at that line - the command must not crash
    assert result.exit_code in (0, 1)


def test_lineage_cli(tmp_path: Path):
    (tmp_path / "job.py").write_text(
        "from pyspark.sql import SparkSession\n"
        "spark.read.table('staging.orders')\n"
        "df.write.saveAsTable('mart.out')\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["lineage", str(tmp_path)])
    assert result.exit_code == 0
    assert "staging.orders" in result.output


def test_graph_cli(tmp_path: Path):
    (tmp_path / "job.py").write_text(
        "from pyspark.sql import SparkSession\nspark.read.table('t1')\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["graph", str(tmp_path)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema_version"] == "1.0"
    assert payload["nodes"]


def test_schema_diff_cli(tmp_path: Path):
    old = tmp_path / "a.avsc"
    new = tmp_path / "b.avsc"
    old.write_text(
        json.dumps({"type": "record", "name": "T", "fields": [{"name": "id", "type": "int"}]}),
        encoding="utf-8",
    )
    new.write_text(
        json.dumps(
            {
                "type": "record",
                "name": "T",
                "fields": [{"name": "id", "type": "int"}, {"name": "n", "type": "string"}],
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["schema", "diff", str(old), str(new), "--format", "json"])
    # added required column = "potentially breaking" -> non-breaking exit
    assert result.exit_code == 0
    changes = json.loads(result.output)["changes"]
    assert changes[0]["classification"] == "potentially breaking"


def test_knowledge_list_verify():
    result = runner.invoke(app, ["knowledge", "list"])
    assert result.exit_code == 0
    assert "glue" in result.output


def test_sbom_cli(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=["requests"]\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["sbom", str(tmp_path)])
    assert result.exit_code == 0
    bom = json.loads(result.output)
    assert bom["bomFormat"] == "CycloneDX"


def test_migrate_glue_cli(tmp_path: Path):
    result = runner.invoke(app, ["migrate", "glue", str(tmp_path), "--from", "3.0", "--to", "4.0"])
    assert result.exit_code == 0
    assert "Runtime" in result.output


def test_cache_command(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    runner.invoke(app, ["scan", str(tmp_path)])
    result = runner.invoke(app, ["cache", "--path", str(tmp_path)])
    assert result.exit_code == 0
    clean = runner.invoke(app, ["cache", "clean", "--path", str(tmp_path)])
    assert clean.exit_code == 0


def test_stats_flag(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(tmp_path), "--stats"])
    assert "ms" in result.output or "cache" in result.output


def test_plugins_commands():
    assert runner.invoke(app, ["plugins", "list"]).exit_code == 0
    assert runner.invoke(app, ["plugins", "validate"]).exit_code == 0
    assert runner.invoke(app, ["plugins", "doctor"]).exit_code == 0
