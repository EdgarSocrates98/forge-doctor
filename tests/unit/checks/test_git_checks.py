"""Unit tests for git checks (GIT*)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.checks.git_checks import CHECKS
from forge_doctor.core.context import GitInfo, ProjectContext
from forge_doctor.core.models import Severity
from forge_doctor.plugins.protocol import Check


def get_check(check_id: str) -> Check:
    return next(check for check in CHECKS if check.id == check_id)


def ctx_with_git(
    root: Path, is_repo: bool, tracked: frozenset[str] | None = None
) -> ProjectContext:
    ctx = ProjectContext(root=root)
    # cached_property stores values in instance __dict__.
    ctx.__dict__["git"] = GitInfo(is_repo=is_repo, tracked_files=tracked)
    return ctx


# GIT001


def test_git001_pass_when_repo(project: Path) -> None:
    ctx = ctx_with_git(project, is_repo=True, tracked=frozenset())
    results = get_check("GIT001").run(ctx)
    assert results[0].severity is Severity.PASS


def test_git001_info_when_not_repo(project: Path) -> None:
    ctx = ctx_with_git(project, is_repo=False)
    results = get_check("GIT001").run(ctx)
    assert results[0].severity is Severity.INFO
    assert results[0].recommendation


# GIT002


def test_git002_empty_when_not_repo(project: Path) -> None:
    ctx = ctx_with_git(project, is_repo=False)
    assert get_check("GIT002").run(ctx) == []


def test_git002_empty_when_tracked_unknown(project: Path) -> None:
    ctx = ctx_with_git(project, is_repo=True, tracked=None)
    assert get_check("GIT002").run(ctx) == []


def test_git002_pass_when_no_sensitive_files(project: Path) -> None:
    ctx = ctx_with_git(project, is_repo=True, tracked=frozenset({"src/app.py", "README.md"}))
    results = get_check("GIT002").run(ctx)
    assert results[0].severity is Severity.PASS


def test_git002_error_per_sensitive_file(project: Path) -> None:
    tracked = frozenset({".env", ".env.local", "config/secrets.yaml", "credentials", "src/app.py"})
    ctx = ctx_with_git(project, is_repo=True, tracked=tracked)
    results = get_check("GIT002").run(ctx)
    assert len(results) == 4
    assert all(result.severity is Severity.ERROR for result in results)
    assert all(result.check_id == "GIT002" for result in results)
    files = {result.file for result in results}
    assert Path(".env") in files
    assert Path("config/secrets.yaml") in files


def test_git002_matches_basename_case_insensitive(project: Path) -> None:
    ctx = ctx_with_git(project, is_repo=True, tracked=frozenset({"config/.ENV.prod"}))
    results = get_check("GIT002").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.ERROR


# GIT003


def test_git003_empty_when_not_repo(project: Path) -> None:
    ctx = ctx_with_git(project, is_repo=False)
    assert get_check("GIT003").run(ctx) == []


def test_git003_empty_when_tracked_unknown(project: Path) -> None:
    ctx = ctx_with_git(project, is_repo=True, tracked=None)
    assert get_check("GIT003").run(ctx) == []


def test_git003_pass_when_clean(project: Path) -> None:
    ctx = ctx_with_git(project, is_repo=True, tracked=frozenset({"src/app.py", "pyproject.toml"}))
    results = get_check("GIT003").run(ctx)
    assert results[0].severity is Severity.PASS


def test_git003_warning_lists_artifacts(project: Path) -> None:
    tracked = frozenset(
        {
            "src/app.py",
            "src/__pycache__/app.cpython-311.pyc",
            "pkg.egg-info/PKG-INFO",
            "out.pyo",
        }
    )
    ctx = ctx_with_git(project, is_repo=True, tracked=tracked)
    results = get_check("GIT003").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.WARNING
    assert "3" in results[0].message
    assert "__pycache__" in results[0].message
    assert results[0].recommendation


def test_git003_truncates_examples(project: Path) -> None:
    tracked = frozenset(
        {
            "a/__pycache__/x.pyc",
            "b/__pycache__/x.pyc",
            "c/__pycache__/x.pyc",
            "d/__pycache__/x.pyc",
            "e/__pycache__/x.pyc",
        }
    )
    ctx = ctx_with_git(project, is_repo=True, tracked=tracked)
    results = get_check("GIT003").run(ctx)
    assert results[0].severity is Severity.WARNING
    assert "5" in results[0].message
    assert "..." in results[0].message
    # At most 3 example paths are listed.
    assert results[0].message.count(".pyc") == 3


def test_checks_expose_category_and_ids() -> None:
    assert {check.category for check in CHECKS} == {"git"}
    assert {check.id for check in CHECKS} == {"GIT001", "GIT002", "GIT003"}
