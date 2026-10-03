"""Unit tests for repository checks (REP*)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.checks.repository import CHECKS
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity
from forge_doctor_data.plugins.protocol import Check


def get_check(check_id: str) -> Check:
    return next(check for check in CHECKS if check.id == check_id)


def touch(root: Path, relative: str, content: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# REP001


def test_rep001_warning_when_pyproject_missing(project: Path) -> None:
    results = get_check("REP001").run(ProjectContext(root=project))
    assert len(results) == 1
    assert results[0].check_id == "REP001"
    assert results[0].severity is Severity.WARNING
    assert results[0].recommendation


def test_rep001_pass_when_pyproject_present(project: Path) -> None:
    touch(project, "pyproject.toml", "[project]\nname = 'x'\n")
    results = get_check("REP001").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


# REP002


def test_rep002_warning_when_readme_missing(project: Path) -> None:
    results = get_check("REP002").run(ProjectContext(root=project))
    assert results[0].severity is Severity.WARNING


def test_rep002_pass_for_each_readme_variant(project: Path) -> None:
    for name in ("README.md", "README.rst", "readme.txt"):
        touch(project, name)
        results = get_check("REP002").run(ProjectContext(root=project))
        assert results[0].severity is Severity.PASS
        (project / name).unlink()


# REP003


def test_rep003_info_when_license_missing(project: Path) -> None:
    results = get_check("REP003").run(ProjectContext(root=project))
    assert results[0].severity is Severity.INFO


def test_rep003_pass_for_license_variants(project: Path) -> None:
    for name in ("LICENSE", "LICENCE.txt", "COPYING.md"):
        touch(project, name)
        results = get_check("REP003").run(ProjectContext(root=project))
        assert results[0].severity is Severity.PASS
        (project / name).unlink()


# REP004


def test_rep004_info_when_gitignore_missing(project: Path) -> None:
    results = get_check("REP004").run(ProjectContext(root=project))
    assert results[0].severity is Severity.INFO


def test_rep004_pass_when_gitignore_present(project: Path) -> None:
    touch(project, ".gitignore", "__pycache__/\n")
    results = get_check("REP004").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


# REP005


def test_rep005_info_when_no_tests(project: Path) -> None:
    touch(project, "src/app.py")
    results = get_check("REP005").run(ProjectContext(root=project))
    assert results[0].severity is Severity.INFO


def test_rep005_pass_with_tests_dir(project: Path) -> None:
    touch(project, "tests/test_app.py")
    results = get_check("REP005").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_rep005_pass_with_test_file_at_root(project: Path) -> None:
    touch(project, "test_app.py")
    results = get_check("REP005").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_rep005_pass_with_suffix_test_file(project: Path) -> None:
    touch(project, "app_test.py")
    results = get_check("REP005").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


# REP006


def test_rep006_info_without_src(project: Path) -> None:
    touch(project, "app.py")
    results = get_check("REP006").run(ProjectContext(root=project))
    assert results[0].severity is Severity.INFO


def test_rep006_pass_with_src_package(project: Path) -> None:
    touch(project, "src/pkg/__init__.py")
    results = get_check("REP006").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_rep006_pass_with_any_src_python(project: Path) -> None:
    touch(project, "src/module.py")
    results = get_check("REP006").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


# REP007


def test_rep007_pass_without_manifests(project: Path) -> None:
    results = get_check("REP007").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_rep007_pass_with_single_manifest(project: Path) -> None:
    touch(project, "requirements.txt", "typer\n")
    results = get_check("REP007").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_rep007_warning_requirements_plus_poetry_lock(project: Path) -> None:
    touch(project, "requirements.txt", "typer\n")
    touch(project, "poetry.lock")
    results = get_check("REP007").run(ProjectContext(root=project))
    assert results[0].severity is Severity.WARNING
    assert "requirements.txt" in results[0].message


def test_rep007_warning_poetry_lock_plus_other_lock(project: Path) -> None:
    for other in ("Pipfile.lock", "uv.lock", "pdm.lock"):
        touch(project, "poetry.lock")
        touch(project, other)
        results = get_check("REP007").run(ProjectContext(root=project))
        assert results[0].severity is Severity.WARNING
        assert other in results[0].message
        (project / "poetry.lock").unlink()
        (project / other).unlink()


# REP008


def test_rep008_info_without_ci(project: Path) -> None:
    results = get_check("REP008").run(ProjectContext(root=project))
    assert results[0].severity is Severity.INFO


def test_rep008_pass_with_github_workflows(project: Path) -> None:
    touch(project, ".github/workflows/ci.yml", "name: ci\n")
    results = get_check("REP008").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_rep008_pass_with_gitlab_ci(project: Path) -> None:
    touch(project, ".gitlab-ci.yml", "stages: [test]\n")
    results = get_check("REP008").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_rep008_pass_with_azure_pipelines(project: Path) -> None:
    touch(project, "azure-pipelines.yml", "trigger: none\n")
    results = get_check("REP008").run(ProjectContext(root=project))
    assert results[0].severity is Severity.PASS


def test_checks_expose_category_and_ids() -> None:
    assert {check.category for check in CHECKS} == {"repository"}
    assert {check.id for check in CHECKS} == {
        "REP001",
        "REP002",
        "REP003",
        "REP004",
        "REP005",
        "REP006",
        "REP007",
        "REP008",
    }
