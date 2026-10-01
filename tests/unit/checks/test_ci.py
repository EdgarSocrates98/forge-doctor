"""Unit tests for CI checks (CI*)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.checks.ci import CHECKS
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity
from forge_doctor.plugins.protocol import Check


def get_check(check_id: str) -> Check:
    return next(check for check in CHECKS if check.id == check_id)


def make_context(root: Path, files: dict[str, str]) -> ProjectContext:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return ProjectContext(root=root)


# CI001


def test_ci001_info_without_ci(project: Path) -> None:
    ctx = make_context(project, {"pyproject.toml": "[project]\nname = 'x'\n"})
    results = get_check("CI001").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.INFO
    assert results[0].recommendation


def test_ci001_pass_lists_github_actions(project: Path) -> None:
    ctx = make_context(project, {".github/workflows/ci.yml": "name: ci\n"})
    results = get_check("CI001").run(ctx)
    assert results[0].severity is Severity.PASS
    assert "GitHub Actions" in results[0].message


def test_ci001_pass_for_other_providers(project: Path) -> None:
    for relative, provider in (
        (".gitlab-ci.yml", "GitLab CI"),
        ("azure-pipelines.yml", "Azure Pipelines"),
        (".circleci/config.yml", "CircleCI"),
    ):
        ctx = make_context(project, {relative: "x\n"})
        results = get_check("CI001").run(ctx)
        assert results[0].severity is Severity.PASS
        assert provider in results[0].message
        (project / relative).unlink()


def test_ci001_ignores_nested_workflow_files(project: Path) -> None:
    ctx = make_context(project, {".github/workflows/deep/x.yml": "name: x\n"})
    results = get_check("CI001").run(ctx)
    assert results[0].severity is Severity.INFO


# CI002


def test_ci002_empty_without_gha_workflows(project: Path) -> None:
    ctx = make_context(project, {".gitlab-ci.yml": "stages: [test]\n"})
    assert get_check("CI002").run(ctx) == []


def test_ci002_warning_for_missing_ref(project: Path) -> None:
    ctx = make_context(
        project,
        {".github/workflows/ci.yml": "steps:\n  - uses: actions/checkout\n"},
    )
    results = get_check("CI002").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.WARNING
    assert results[0].file == Path(".github/workflows/ci.yml")
    assert results[0].line == 2
    assert "actions/checkout" in results[0].message
    assert "uses:" not in results[0].message  # reports the action name, not the raw line


def test_ci002_warning_for_main_ref(project: Path) -> None:
    ctx = make_context(
        project,
        {".github/workflows/ci.yml": "  - uses: actions/checkout@main\n"},
    )
    results = get_check("CI002").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.WARNING
    assert "'main'" in results[0].message


def test_ci002_sha_pins_produce_no_results(project: Path) -> None:
    content = "  - uses: actions/checkout@08c6903cd8c0fde910a37f88322edcfb5dd907a8\n"
    ctx = make_context(project, {".github/workflows/ci.yml": content})
    assert get_check("CI002").run(ctx) == []


def test_ci002_tag_pins_are_info_not_warning(project: Path) -> None:
    content = '  - uses: actions/setup-python@v4\n  - uses: "actions/cache@v4.2.0" # quoted\n'
    ctx = make_context(project, {".github/workflows/ci.yml": content})
    results = get_check("CI002").run(ctx)
    assert len(results) == 2
    assert all(r.severity is Severity.INFO for r in results)
    assert "mutable" in results[0].message


def test_ci002_branch_ref_is_warning(project: Path) -> None:
    content = "  - uses: actions/checkout@feature/x\n"
    ctx = make_context(project, {".github/workflows/ci.yml": content})
    results = get_check("CI002").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.WARNING


def test_ci002_ignores_local_and_docker_refs(project: Path) -> None:
    content = "  - uses: ./local/action\n  - uses: docker://alpine:3.19\n"
    ctx = make_context(project, {".github/workflows/ci.yml": content})
    assert get_check("CI002").run(ctx) == []


# CI003


def test_ci003_empty_without_gha_workflows(project: Path) -> None:
    ctx = make_context(project, {".gitlab-ci.yml": "x\n"})
    assert get_check("CI003").run(ctx) == []


def test_ci003_info_without_python_version(project: Path) -> None:
    ctx = make_context(
        project,
        {".github/workflows/ci.yml": "steps:\n  - uses: actions/checkout@v4\n"},
    )
    results = get_check("CI003").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.INFO
    assert results[0].recommendation


def test_ci003_pass_with_setup_python(project: Path) -> None:
    content = "  - uses: actions/setup-python@v4\n    with:\n      python-version: '3.12'\n"
    ctx = make_context(project, {".github/workflows/ci.yml": content})
    results = get_check("CI003").run(ctx)
    assert results[0].severity is Severity.PASS


# CI004


def test_ci004_empty_without_gha_workflows(project: Path) -> None:
    ctx = make_context(project, {"azure-pipelines.yml": "trigger: none\n"})
    assert get_check("CI004").run(ctx) == []


def test_ci004_info_without_test_steps(project: Path) -> None:
    content = "steps:\n  - uses: actions/checkout@v4\n  - run: echo hello\n"
    ctx = make_context(project, {".github/workflows/ci.yml": content})
    results = get_check("CI004").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.INFO


def test_ci004_word_boundary_ignores_latest(project: Path) -> None:
    content = "jobs:\n  b:\n    runs-on: ubuntu-latest\n    name: deploy latest image\n"
    ctx = make_context(project, {".github/workflows/ci.yml": content})
    results = get_check("CI004").run(ctx)
    assert results[0].severity is Severity.INFO


def test_ci004_pass_with_pytest(project: Path) -> None:
    content = "steps:\n  - uses: actions/checkout@v4\n  - run: pytest -q\n"
    ctx = make_context(project, {".github/workflows/ci.yml": content})
    results = get_check("CI004").run(ctx)
    assert results[0].severity is Severity.PASS


def test_checks_expose_category_and_ids() -> None:
    assert {check.category for check in CHECKS} == {"ci"}
    assert [check.id for check in CHECKS] == ["CI001", "CI002", "CI003", "CI004"]
