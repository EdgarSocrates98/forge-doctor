"""ScanService: the one pipeline shared by CLI, MCP, and LSP."""

from pathlib import Path

import pytest

from forge_doctor.core.service import (
    PLUGINS_ENV_VAR,
    ScanRequest,
    ScanRequestError,
    ScanService,
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    return tmp_path


def test_run_produces_report(tmp_path: Path):
    outcome = ScanService(warn=lambda m: None).run(ScanRequest(path=_project(tmp_path)))
    assert outcome.report.version
    assert outcome.selected > 0
    assert outcome.runner.timings  # every selected check got timed


def test_unknown_category_raises(tmp_path: Path):
    _project(tmp_path)
    with pytest.raises(ScanRequestError, match="Unknown categor"):
        ScanService(warn=lambda m: None).run(ScanRequest(path=tmp_path, categories=("bogus",)))


def test_policy_suppressions_applied(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n\n"
        "[[tool.forge-doctor.suppressions]]\n"
        'rule = "REP002"\nreason = "docs elsewhere"\nowner = "t"\n'
    )
    outcome = ScanService(warn=lambda m: None).run(ScanRequest(path=tmp_path))
    assert all(r.check_id != "REP002" for r in outcome.report.results)
    assert outcome.report.suppressions


def test_plugin_kill_switch_env(tmp_path: Path):
    _project(tmp_path)
    service = ScanService(env={PLUGINS_ENV_VAR: "1"}, warn=lambda m: None)
    assert service.plugins_enabled(no_plugins=False) is False
    registry, errors = service.build_registry(no_plugins=False)
    assert errors == []
    assert all("__fd_source__" not in vars(c) for c in registry.all())


def test_overlay_files_scanned(tmp_path: Path):
    _project(tmp_path)
    outcome = ScanService(warn=lambda m: None).run(
        ScanRequest(
            path=tmp_path,
            overlay={"unsaved.py": "import os\nx = 1\n"},
        )
    )
    assert Path("unsaved.py") in outcome.ctx.files
    assert outcome.ctx.read_text(Path("unsaved.py")) == "import os\nx = 1\n"


def test_files_filter(tmp_path: Path):
    _project(tmp_path)
    (tmp_path / "a.py").write_text("import pickle\npickle.loads(b'')\n")
    (tmp_path / "b.py").write_text("import pickle\npickle.loads(b'')\n")
    outcome = ScanService(warn=lambda m: None).run(ScanRequest(path=tmp_path, files=("a.py",)))
    files = {r.file.as_posix() for r in outcome.report.results if r.file}
    assert "b.py" not in files
