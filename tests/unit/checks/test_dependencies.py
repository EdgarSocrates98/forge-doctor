"""Unit tests for the dependency checks (DEP###)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from forge_doctor_data.checks.dependencies import (
    CHECKS,
    DevToolsAsRuntimeDeps,
    LockFile,
    LockFreshness,
    PoetryAvailable,
    RuntimeDevDuplication,
    UnrestrictedDependencies,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity

PLAIN_PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
dependencies = ["boto3>=1.28"]
"""

POETRY_PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["boto3>=1.28"]

[tool.poetry]
requires-poetry = ">=2.0"
"""


def make_context(
    tmp_path: Path,
    *,
    pyproject: str | None = None,
    files: dict[str, str] | None = None,
) -> ProjectContext:
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    for name, content in (files or {}).items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return ProjectContext(root=tmp_path)


class TestLockFile:
    """DEP001."""

    def test_no_pyproject_returns_nothing(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path)
        assert LockFile().run(ctx) == []

    def test_poetry_lock_present_passes(self, tmp_path: Path) -> None:
        ctx = make_context(
            tmp_path,
            pyproject=POETRY_PYPROJECT,
            files={"poetry.lock": "# locked\n"},
        )
        (result,) = LockFile().run(ctx)
        assert result.severity == Severity.PASS
        assert result.check_id == "DEP001"

    def test_poetry_lock_missing_warns(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, pyproject=POETRY_PYPROJECT)
        (result,) = LockFile().run(ctx)
        assert result.severity == Severity.WARNING
        assert result.recommendation is not None
        assert "poetry lock" in result.recommendation

    def test_requirements_txt_passes(self, tmp_path: Path) -> None:
        ctx = make_context(
            tmp_path,
            pyproject=PLAIN_PYPROJECT,
            files={"requirements.txt": "boto3==1.34.0\n"},
        )
        (result,) = LockFile().run(ctx)
        assert result.severity == Severity.PASS
        assert result.message == "requirements.txt present"

    def test_no_lock_file_info(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, pyproject=PLAIN_PYPROJECT)
        (result,) = LockFile().run(ctx)
        assert result.severity == Severity.INFO
        assert result.message == "no lock file detected"


class TestUnrestrictedDependencies:
    """DEP002."""

    def test_pep621_unrestricted_warns(self, tmp_path: Path) -> None:
        ctx = make_context(
            tmp_path,
            pyproject='[project]\ndependencies = ["boto3", "requests>=2"]\n',
        )
        (result,) = UnrestrictedDependencies().run(ctx)
        assert result.severity == Severity.WARNING
        assert result.message == "boto3 has no version constraint"

    def test_poetry_star_constraint_warns(self, tmp_path: Path) -> None:
        ctx = make_context(
            tmp_path,
            pyproject=('[tool.poetry.dependencies]\npython = ">=3.11"\nboto3 = "*"\n'),
        )
        (result,) = UnrestrictedDependencies().run(ctx)
        assert result.severity == Severity.WARNING
        assert result.message == "boto3 has no version constraint"

    def test_all_constrained_passes(self, tmp_path: Path) -> None:
        ctx = make_context(
            tmp_path,
            pyproject='[project]\ndependencies = ["boto3>=1.28", "requests~=2.31"]\n',
        )
        (result,) = UnrestrictedDependencies().run(ctx)
        assert result.severity == Severity.PASS
        assert result.message == "all 2 dependencies constrained"

    def test_no_pyproject_returns_nothing(self, tmp_path: Path) -> None:
        assert UnrestrictedDependencies().run(make_context(tmp_path)) == []


class TestDevToolsAsRuntimeDeps:
    """DEP003."""

    def test_dev_tools_warn(self, tmp_path: Path) -> None:
        ctx = make_context(
            tmp_path,
            pyproject=(
                "[project]\n"
                'dependencies = ["pytest", "pytest-mock>=3", '
                '"flake8-bugbear", "boto3>=1"]\n'
            ),
        )
        results = DevToolsAsRuntimeDeps().run(ctx)
        assert [r.severity for r in results] == [Severity.WARNING] * 3
        flagged = " ".join(r.message for r in results)
        assert "pytest" in flagged
        assert "pytest-mock" in flagged
        assert "flake8-bugbear" in flagged
        assert "boto3" not in flagged

    def test_clean_runtime_deps_pass(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, pyproject=PLAIN_PYPROJECT)
        (result,) = DevToolsAsRuntimeDeps().run(ctx)
        assert result.severity == Severity.PASS


class TestRuntimeDevDuplication:
    """DEP004."""

    def test_duplicate_reports_info(self, tmp_path: Path) -> None:
        ctx = make_context(
            tmp_path,
            pyproject=(
                "[project]\n"
                'dependencies = ["moto>=5", "boto3>=1"]\n'
                "[dependency-groups]\n"
                'dev = ["moto>=5", "pytest>=8"]\n'
            ),
        )
        (result,) = RuntimeDevDuplication().run(ctx)
        assert result.severity == Severity.INFO
        assert "moto" in result.message

    def test_no_overlap_no_results(self, tmp_path: Path) -> None:
        ctx = make_context(
            tmp_path,
            pyproject=(
                '[project]\ndependencies = ["boto3>=1"]\n[dependency-groups]\ndev = ["pytest>=8"]\n'
            ),
        )
        assert RuntimeDevDuplication().run(ctx) == []


def _no_poetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the mtime fallback in DEP005."""
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)


class TestLockFreshness:
    """DEP005."""

    def test_stale_lock_reports_info(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _no_poetry(monkeypatch)
        lock = tmp_path / "poetry.lock"
        lock.write_text("# old lock\n", encoding="utf-8")
        ctx = make_context(tmp_path, pyproject=POETRY_PYPROJECT)
        old = time.time() - 3600
        os.utime(lock, (old, old))
        (result,) = LockFreshness().run(ctx)
        assert result.severity == Severity.INFO
        assert "older than pyproject.toml" in result.message

    def test_fresh_lock_no_result(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _no_poetry(monkeypatch)
        ctx = make_context(
            tmp_path,
            pyproject=POETRY_PYPROJECT,
            files={"poetry.lock": "# fresh\n"},
        )
        assert LockFreshness().run(ctx) == []

    def test_poetry_check_lock_fresh_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = make_context(
            tmp_path,
            pyproject=POETRY_PYPROJECT,
            files={"poetry.lock": "# locked\n"},
        )
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="All set!\n", stderr=""),
        )
        (result,) = LockFreshness().run(ctx)
        assert result.severity == Severity.PASS
        assert "consistent" in result.message

    def test_poetry_check_lock_stale_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = make_context(
            tmp_path,
            pyproject=POETRY_PYPROJECT,
            files={"poetry.lock": "# locked\n"},
        )
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="lock file is not consistent"
            ),
        )
        (result,) = LockFreshness().run(ctx)
        assert result.severity == Severity.WARNING
        assert "inconsistent" in result.message

    def test_poetry_failure_falls_back_to_mtime(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = make_context(
            tmp_path,
            pyproject=POETRY_PYPROJECT,
            files={"poetry.lock": "# locked\n"},
        )
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")

        def _boom(*a: object, **kw: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired("poetry", 30)

        monkeypatch.setattr(subprocess, "run", _boom)
        assert LockFreshness().run(ctx) == []  # fresh mtime → no result

    def test_no_lock_no_result(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, pyproject=POETRY_PYPROJECT)
        assert LockFreshness().run(ctx) == []

    def test_no_pyproject_no_result(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, files={"poetry.lock": "# orphan\n"})
        assert LockFreshness().run(ctx) == []


class TestPoetryAvailable:
    """DEP006."""

    def test_not_poetry_managed_returns_nothing(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, pyproject=PLAIN_PYPROJECT)
        assert PoetryAvailable().run(ctx) == []

    def test_no_pyproject_returns_nothing(self, tmp_path: Path) -> None:
        assert PoetryAvailable().run(make_context(tmp_path)) == []

    def test_poetry_not_on_path_info(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = make_context(tmp_path, pyproject=POETRY_PYPROJECT)
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
        (result,) = PoetryAvailable().run(ctx)
        assert result.severity == Severity.INFO
        assert result.message == "poetry not on PATH"

    def test_poetry_version_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = make_context(tmp_path, pyproject=POETRY_PYPROJECT)
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, stdout="Poetry (version 2.1.3)\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        (result,) = PoetryAvailable().run(ctx)
        assert result.severity == Severity.PASS
        assert "2.1.3" in result.message


def test_checks_metadata() -> None:
    assert [check.id for check in CHECKS] == [
        "DEP001",
        "DEP002",
        "DEP003",
        "DEP004",
        "DEP005",
        "DEP006",
    ]
    assert all(check.category == "dependencies" for check in CHECKS)
