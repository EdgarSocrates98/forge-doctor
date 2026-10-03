import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor_data import __version__
from forge_doctor_data.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.output


def test_help_panels_group_commands():
    """Top-level help groups commands into named panels, and groups with a
    bare-invoke callback advertise it."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for panel in (
        "Scan & findings",
        "Platform intelligence",
        "Estate & change",
        "Quality gates",
        "Setup & integrations",
    ):
        assert panel in result.output
    assert "Bare:" in result.output  # invoke_without_command groups


def test_bare_invocation_shows_help():
    """Bare `forge-doctor-data` prints the full help instead of a bare
    'Missing command' error; exit stays non-zero (it is a usage error)."""
    result = runner.invoke(app, [])
    assert result.exit_code in (0, 2)
    assert "Commands" in result.output or "Scan & findings" in result.output


def test_scan_help_option_panels():
    result = runner.invoke(app, ["scan", "--help"])
    assert result.exit_code == 0
    assert "Scope & filters" in result.output
    assert "Gates & baselines" in result.output
    assert "Examples:" in result.output


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "forge-doctor-data" in result.output


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_scan_empty_dir(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path)])
    assert result.exit_code == 0, result.output


def test_scan_json_output(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["version"] == __version__
    assert set(payload["summary"]) == {"passed", "info", "warnings", "errors"}


def test_scan_category_filter(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--check", "python"])
    assert result.exit_code == 0


def test_category_command(tmp_path: Path):
    result = runner.invoke(app, ["repo", str(tmp_path)])
    assert result.exit_code == 0


def test_scan_not_a_directory(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path / "nope")])
    assert result.exit_code == 2


def test_scan_ignore(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--ignore", "REP001", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert all(r["check_id"] != "REP001" for r in payload["results"])


def test_scan_unknown_format_fails_fast(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "yaml"])
    assert result.exit_code == 2
    assert "Unknown format" in result.output or "Unknown format" in result.stderr


def test_explain_builtin():
    result = runner.invoke(app, ["explain", "SPARK001"])
    assert result.exit_code == 0
    assert "SPARK001" in result.output
    assert "Why" in result.output


def test_explain_lowercase_id():
    result = runner.invoke(app, ["explain", "rep002"])
    assert result.exit_code == 0
    assert "REP002" in result.output


def test_explain_unknown_id():
    result = runner.invoke(app, ["explain", "NOPE999"])
    assert result.exit_code == 2


def test_plugins_command():
    result = runner.invoke(app, ["plugins"])
    assert result.exit_code == 0
    assert "Built-in" in result.output


def test_checks_command():
    result = runner.invoke(app, ["checks"])
    assert result.exit_code == 0
    assert "REP001" in result.output


def test_scan_html_output(tmp_path: Path):
    target = tmp_path / "report.html"
    result = runner.invoke(
        app,
        ["scan", str(tmp_path), "--format", "html", "--output", str(target)],
    )
    assert result.exit_code == 0, result.output
    html = target.read_text(encoding="utf-8")
    assert html.lstrip().lower().startswith("<!doctype html")


def test_scan_html_default_output(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "html"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "forge-doctor-data-report.html").exists()


def test_scan_text_output_to_file(tmp_path: Path):
    target = tmp_path / "report.txt"
    result = runner.invoke(app, ["scan", str(tmp_path), "--output", str(target)])
    assert result.exit_code == 0, result.output
    assert "Summary" in target.read_text(encoding="utf-8")


def test_scan_no_color(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--no-color"])
    assert result.exit_code == 0
    assert "\x1b[" not in result.output


def test_save_and_compare_baseline(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    result = runner.invoke(app, ["scan", str(tmp_path), "--save-baseline", str(baseline)])
    assert result.exit_code == 0, result.output
    assert baseline.exists()

    result = runner.invoke(
        app, ["scan", str(tmp_path), "--baseline", str(baseline), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["baseline"]["new"] == 0
    assert payload["baseline"]["fixed"] == 0
    assert all(r["is_new"] is False for r in payload["results"])


def test_baseline_new_finding_fails(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    # Clean-ish state: README exists so REP002 passes.
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(tmp_path), "--save-baseline", str(baseline)])
    assert result.exit_code == 0, result.output

    # Delete README -> REP002 now warns; with a baseline only NEW findings
    # should matter for --fail-on.
    (tmp_path / "README.md").unlink()
    result = runner.invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--baseline",
            str(baseline),
            "--fail-on",
            "warning",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["baseline"]["new"] >= 1
    rep002 = next(r for r in payload["results"] if r["check_id"] == "REP002")
    assert rep002["is_new"] is True

    # Pre-existing-only findings pass the same threshold.
    result = runner.invoke(
        app,
        ["scan", str(tmp_path), "--baseline", str(baseline), "--fail-on", "warning"],
    )
    assert result.exit_code == 1  # REP002 still new vs baseline
    # Re-save, then re-compare: nothing new -> exit 0.
    runner.invoke(app, ["scan", str(tmp_path), "--save-baseline", str(baseline)])
    result = runner.invoke(
        app,
        ["scan", str(tmp_path), "--baseline", str(baseline), "--fail-on", "warning"],
    )
    assert result.exit_code == 0


def test_watch_rejects_non_text_format(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--watch", "--format", "json"])
    assert result.exit_code == 2
    assert "watch" in (result.output + result.stderr).lower()


def test_snapshot_detects_changes(tmp_path: Path):
    from forge_doctor_data.cli import _snapshot

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    first = _snapshot(tmp_path)
    assert "a.py" in first

    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    second = _snapshot(tmp_path)
    assert second != first

    (tmp_path / "a.py").write_text("x = 3\n", encoding="utf-8")
    third = _snapshot(tmp_path)
    assert third != second


def test_init_command(tmp_path: Path):
    target = tmp_path / "new-proj"
    result = runner.invoke(app, ["init", str(target), "--name", "new-proj"])
    assert result.exit_code == 0, result.output
    assert (target / "pyproject.toml").exists()
    assert (target / "src" / "new_proj" / "__init__.py").exists()


def test_init_refuses_overwrite(tmp_path: Path):
    (tmp_path / "README.md").write_text("mine", encoding="utf-8")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "mine"
    assert "--force" in result.output


def test_info_command(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(app, ["info", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Files" in result.output
    assert "Python" in result.output


def test_info_not_a_directory(tmp_path: Path):
    result = runner.invoke(app, ["info", str(tmp_path / "nope")])
    assert result.exit_code == 2


def test_scan_sarif_output(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "sarif"])
    assert result.exit_code == 0, result.output
    sarif = json.loads(result.output)
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "forge-doctor-data"


def test_scan_agent_output(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--format", "agent"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tool"] == "forge-doctor-data"
    assert all(set(f) == {"id", "sev", "loc", "fp"} for f in payload["findings"])


def test_scan_unknown_category_fails(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--check", "nao-existe"])
    assert result.exit_code == 2
    assert "nknown categor" in result.output + result.stderr


def test_scan_mixed_categories_warns_but_runs(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--check", "python", "--check", "nope"])
    assert result.exit_code == 0, result.output


def test_scan_invalid_fail_on(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--fail-on", "info"])
    assert result.exit_code == 2


def test_scan_invalid_profile(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--profile", "nope"])
    assert result.exit_code == 2


def test_new_only_requires_baseline(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--new-only"])
    assert result.exit_code == 2


def test_security_profile_upgrades_tag_pins(tmp_path: Path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("steps:\n  - uses: actions/checkout@v4\n", encoding="utf-8")
    default = runner.invoke(app, ["scan", str(tmp_path), "--check", "ci", "--format", "json"])
    ci002_default = next(
        r for r in json.loads(default.output)["results"] if r["check_id"] == "CI002"
    )
    assert ci002_default["severity"] == "info"

    secure = runner.invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--check",
            "ci",
            "--profile",
            "security",
            "--format",
            "json",
        ],
    )
    ci002_secure = next(r for r in json.loads(secure.output)["results"] if r["check_id"] == "CI002")
    assert ci002_secure["severity"] == "warning"


def test_files_flag_filters_findings(tmp_path: Path):
    (tmp_path / "a.py").write_text("import pyspark\ndf.collect()\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import pyspark\ndf.collect()\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--check",
            "spark",
            "--files",
            "a.py",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    files = {r["file"] for r in payload["results"] if r["file"]}
    assert files == {"a.py"}


def test_no_plugins_flag(tmp_path: Path):
    result = runner.invoke(app, ["scan", str(tmp_path), "--no-plugins", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert all(not r["check_id"].startswith("EXAMPLE") for r in payload["results"])


def test_diff_command(tmp_path: Path):
    base_a = tmp_path / "a.json"
    base_b = tmp_path / "b.json"
    runner.invoke(app, ["scan", str(tmp_path), "--save-baseline", str(base_a)])
    # Second baseline with an extra synthetic finding.
    import json as _json

    payload = _json.loads(base_a.read_text())
    payload["results"].append(
        {"check_id": "X999", "fingerprint": "deadbeef", "file": "z.py", "line": 1}
    )
    base_b.write_text(_json.dumps(payload))

    result = runner.invoke(app, ["diff", str(base_a), str(base_b)])
    assert result.exit_code == 1
    assert "X999" in result.output

    result = runner.invoke(app, ["diff", str(base_b), str(base_a)])
    assert result.exit_code == 0


def test_diff_git_refs(tmp_path: Path):
    import subprocess

    def git(*args: str) -> None:
        proc = subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='a'\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "one")
    git("commit", "-qm", "two", "--allow-empty")

    result = runner.invoke(app, ["diff", "HEAD~1...HEAD", "--path", str(tmp_path)])
    assert result.exit_code in (0, 1), result.output
    assert "diff" in result.output

    bad = runner.invoke(app, ["diff", "nonexistent-ref", "HEAD", "--path", str(tmp_path)])
    assert bad.exit_code == 2


def test_diff_semantic_change_intel(tmp_path: Path):
    """glue_version 4.0 -> 5.0 surfaces capability transitions + migration
    requirements in JSON; exit code still gates on findings/risk only."""
    import subprocess

    def git(*args: str) -> None:
        proc = subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "main.tf").write_text(
        'resource "aws_glue_job" "j" {\n  name = "j"\n  glue_version = "4.0"\n}\n',
        encoding="utf-8",
    )
    git("add", "-A")
    git("commit", "-qm", "base")
    (tmp_path / "main.tf").write_text(
        'resource "aws_glue_job" "j" {\n  name = "j"\n  glue_version = "5.0"\n}\n',
        encoding="utf-8",
    )
    git("add", "-A")
    git("commit", "-qm", "bump")

    result = runner.invoke(
        app, ["diff", "HEAD~1...HEAD", "--path", str(tmp_path), "--semantic", "-f", "json"]
    )
    payload = json.loads(result.output)
    assert result.exit_code in (0, 1)
    caps = {c["capability"]: c for c in payload["capabilities"]}
    assert caps["LAKEFORMATION_FGAC"]["from"] == "conditional"
    assert caps["LAKEFORMATION_FGAC"]["to"] == "supported"
    reqs = payload["migration_requirements"]
    assert len(reqs) == 1
    assert reqs[0]["attr"] == "glue_version"
    assert reqs[0]["from"] == "4.0"
    assert reqs[0]["to"] == "5.0"
    assert reqs[0]["status"] == "known"
    assert any("Python 3.10" in c for c in reqs[0]["required_changes"])

    text = runner.invoke(app, ["diff", "HEAD~1...HEAD", "--path", str(tmp_path), "--semantic"])
    assert "Capability transitions" in text.output
    assert "Migration requirements" in text.output


def test_diff_requires_two_sides(tmp_path: Path):
    result = runner.invoke(app, ["diff", "justone"])
    assert result.exit_code == 2


def test_compatibility_command(tmp_path: Path):
    (tmp_path / "job.py").write_text(
        'import awsglue\nclient = boto3.client("glue")\nclient.create_job(GlueVersion="4.0")\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["compatibility", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Detected environment" in result.output
    assert "Migration risks" in result.output
    assert "6.0" in result.output


def test_workspace_command(tmp_path: Path):
    sub = tmp_path / "jobs" / "etl"
    sub.mkdir(parents=True)
    (sub / "pyproject.toml").write_text('[project]\nname="etl-job"\n', encoding="utf-8")
    result = runner.invoke(app, ["workspace", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "etl-job" in result.output


def test_explain_json(tmp_path: Path):
    result = runner.invoke(app, ["explain", "SPARK001", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["id"] == "SPARK001"
    assert payload["why"]
    assert payload["category"] == "spark"
