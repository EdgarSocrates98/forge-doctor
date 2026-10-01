"""Unit tests for python environment checks (PY*)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_doctor.checks.python_env import CHECKS
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity
from forge_doctor.plugins.protocol import Check


def get_check(check_id: str) -> Check:
    return next(check for check in CHECKS if check.id == check_id)


def touch(root: Path, relative: str, content: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def set_python_version(ctx: ProjectContext, version: tuple[int, int, int] | None) -> None:
    ctx.__dict__["python_version"] = version


def set_env(ctx: ProjectContext, env: dict[str, str]) -> None:
    ctx.__dict__["env"] = env


# PY001


def test_py001_pass_with_interpreter(project: Path) -> None:
    ctx = ProjectContext(root=project)
    set_python_version(ctx, (3, 11, 8))
    results = get_check("PY001").run(ctx)
    assert results[0].severity is Severity.PASS
    assert "3.11.8" in results[0].message


def test_py001_info_without_interpreter(project: Path) -> None:
    ctx = ProjectContext(root=project)
    set_python_version(ctx, None)
    results = get_check("PY001").run(ctx)
    assert results[0].severity is Severity.INFO


# PY002


def test_py002_pass_when_requires_python_declared(project: Path) -> None:
    touch(project, "pyproject.toml", '[project]\nrequires-python = ">=3.11"\n')
    results = get_check("PY002").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS
    assert ">=3.11" in results[0].message


def test_py002_pass_with_poetry_python_constraint(project: Path) -> None:
    touch(
        project,
        "pyproject.toml",
        '[tool.poetry.dependencies]\npython = ">=3.11"\n',
    )
    results = get_check("PY002").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_py002_warning_without_requires_python(project: Path) -> None:
    touch(project, "pyproject.toml", '[project]\nname = "x"\n')
    results = get_check("PY002").run(ProjectContext(root=project))
    assert results[0].severity is Severity.WARNING


def test_py002_warning_without_pyproject(project: Path) -> None:
    results = get_check("PY002").run(ProjectContext(root=project))
    assert results[0].severity is Severity.WARNING


# PY003


def test_py003_empty_without_requires_python(project: Path) -> None:
    ctx = ProjectContext(root=project)
    set_python_version(ctx, (3, 12, 0))
    assert get_check("PY003").run(ctx) == []


def test_py003_pass_when_interpreter_satisfies(project: Path) -> None:
    touch(project, "pyproject.toml", '[project]\nrequires-python = ">=3.11"\n')
    ctx = ProjectContext(root=project)
    set_python_version(ctx, (3, 12, 0))
    results = get_check("PY003").run(ctx)
    assert results[0].severity is Severity.PASS
    assert "3.12.0" in results[0].message


def test_py003_error_when_interpreter_too_old(project: Path) -> None:
    touch(project, "pyproject.toml", '[project]\nrequires-python = ">=3.11"\n')
    ctx = ProjectContext(root=project)
    set_python_version(ctx, (3, 10, 0))
    results = get_check("PY003").run(ctx)
    assert results[0].severity is Severity.ERROR
    assert results[0].recommendation


def test_py003_info_when_interpreter_unknown(project: Path) -> None:
    touch(project, "pyproject.toml", '[project]\nrequires-python = ">=3.11"\n')
    ctx = ProjectContext(root=project)
    set_python_version(ctx, None)
    results = get_check("PY003").run(ctx)
    assert results[0].severity is Severity.INFO


def test_py003_info_when_specifier_invalid(project: Path) -> None:
    touch(project, "pyproject.toml", '[project]\nrequires-python = "not-a-spec"\n')
    ctx = ProjectContext(root=project)
    set_python_version(ctx, (3, 12, 0))
    results = get_check("PY003").run(ctx)
    assert results[0].severity is Severity.INFO


# PY004


def test_py004_info_without_virtualenv(project: Path) -> None:
    ctx = ProjectContext(root=project)
    set_env(ctx, {})
    results = get_check("PY004").run(ctx)
    assert results[0].severity is Severity.INFO


def test_py004_pass_with_dotvenv_dir(project: Path) -> None:
    (project / ".venv").mkdir()
    ctx = ProjectContext(root=project)
    set_env(ctx, {})
    results = get_check("PY004").run(ctx)
    assert results[0].severity is Severity.PASS


def test_py004_pass_with_venv_dir(project: Path) -> None:
    (project / "venv").mkdir()
    ctx = ProjectContext(root=project)
    set_env(ctx, {})
    results = get_check("PY004").run(ctx)
    assert results[0].severity is Severity.PASS


def test_py004_pass_with_virtual_env_var(project: Path) -> None:
    ctx = ProjectContext(root=project)
    set_env(ctx, {"VIRTUAL_ENV": "/some/venv"})
    results = get_check("PY004").run(ctx)
    assert results[0].severity is Severity.PASS


# PY005


def test_py005_info_without_test_config(project: Path) -> None:
    results = get_check("PY005").run(ProjectContext(root=project))
    assert results[0].severity is Severity.INFO


def test_py005_pass_with_pyproject_pytest_options(project: Path) -> None:
    touch(project, "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    results = get_check("PY005").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_py005_pass_with_pytest_ini(project: Path) -> None:
    touch(project, "pytest.ini", "[pytest]\n")
    results = get_check("PY005").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_py005_pass_with_tox_ini(project: Path) -> None:
    touch(project, "tox.ini", "[tox]\n")
    results = get_check("PY005").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


# PY006


def test_py006_info_without_linter_config(project: Path) -> None:
    results = get_check("PY006").run(ProjectContext(root=project))
    assert results[0].severity is Severity.INFO


def test_py006_pass_with_tool_ruff(project: Path) -> None:
    touch(project, "pyproject.toml", "[tool.ruff]\n")
    results = get_check("PY006").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_py006_pass_with_linter_files(project: Path) -> None:
    for name in ("ruff.toml", ".ruff.toml", ".flake8", ".pre-commit-config.yaml"):
        touch(project, name)
        results = get_check("PY006").run(ProjectContext(root=project))
        assert results[0].severity is Severity.PASS
        (project / name).unlink()


# PY007


def test_py007_info_without_type_checker(project: Path) -> None:
    results = get_check("PY007").run(ProjectContext(root=project))
    assert results[0].severity is Severity.INFO


def test_py007_pass_with_tool_mypy(project: Path) -> None:
    touch(project, "pyproject.toml", "[tool.mypy]\n")
    results = get_check("PY007").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_py007_pass_with_tool_pyright(project: Path) -> None:
    touch(project, "pyproject.toml", "[tool.pyright]\n")
    results = get_check("PY007").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_py007_pass_with_type_checker_files(project: Path) -> None:
    for name in ("mypy.ini", ".mypy.ini", "pyrightconfig.json"):
        touch(project, name)
        results = get_check("PY007").run(ProjectContext(root=project))
        assert results[0].severity is Severity.PASS
        (project / name).unlink()


def test_checks_expose_category_and_ids() -> None:
    assert {check.category for check in CHECKS} == {"python"}
    assert {check.id for check in CHECKS} == {
        "PY001",
        "PY002",
        "PY003",
        "PY004",
        "PY005",
        "PY006",
        "PY007",
    }


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.id)
def test_checks_return_typed_results(project: Path, check: Check) -> None:
    ctx = ProjectContext(root=project)
    set_env(ctx, {})
    set_python_version(ctx, (3, 11, 0))
    for result in check.run(ctx):
        assert result.check_id == check.id
        assert result.category == "python"
        assert result.message
