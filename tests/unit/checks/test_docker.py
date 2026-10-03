"""Unit tests for docker checks (DOCKER*)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.checks.docker import CHECKS
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity
from forge_doctor_data.plugins.protocol import Check


def get_check(check_id: str) -> Check:
    return next(check for check in CHECKS if check.id == check_id)


def make_context(root: Path, files: dict[str, str]) -> ProjectContext:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return ProjectContext(root=root)


# DOCKER001


def test_docker001_info_without_dockerfile(project: Path) -> None:
    ctx = make_context(project, {"pyproject.toml": "[project]\nname = 'x'\n"})
    results = get_check("DOCKER001").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.INFO
    assert results[0].recommendation


def test_docker001_pass_counts_dockerfiles(project: Path) -> None:
    ctx = make_context(
        project,
        {
            "Dockerfile": "FROM python:3.12\n",
            "deploy/Dockerfile.dev": "FROM python:3.12\n",
        },
    )
    results = get_check("DOCKER001").run(ctx)
    assert results[0].severity is Severity.PASS
    assert "2" in results[0].message


def test_docker001_ignores_non_dockerfile_names(project: Path) -> None:
    ctx = make_context(project, {"Dockerfiles.txt": "x\n", "README.md": "x\n"})
    results = get_check("DOCKER001").run(ctx)
    assert results[0].severity is Severity.INFO


# DOCKER002


def test_docker002_empty_without_dockerfile(project: Path) -> None:
    assert get_check("DOCKER002").run(make_context(project, {})) == []


def test_docker002_warning_without_tag(project: Path) -> None:
    ctx = make_context(project, {"Dockerfile": "FROM python\nUSER app\n"})
    results = get_check("DOCKER002").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.WARNING
    assert results[0].file == Path("Dockerfile")
    assert results[0].line == 1
    assert results[0].recommendation


def test_docker002_warning_for_latest_tag(project: Path) -> None:
    ctx = make_context(project, {"Dockerfile": "FROM python:latest\n"})
    results = get_check("DOCKER002").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.WARNING


def test_docker002_pass_pinned_tag_digest_scratch_and_stage_ref(project: Path) -> None:
    content = (
        "FROM python:3.12-slim AS build\nFROM build\nFROM scratch\nFROM repo/image@sha256:abcdef\n"
    )
    results = get_check("DOCKER002").run(make_context(project, {"Dockerfile": content}))
    assert len(results) == 1
    assert results[0].severity is Severity.PASS


def test_docker002_registry_port_is_not_a_tag(project: Path) -> None:
    ctx = make_context(project, {"Dockerfile": "FROM registry.local:5000/team/app\n"})
    results = get_check("DOCKER002").run(ctx)
    assert results[0].severity is Severity.WARNING


# DOCKER003


def test_docker003_empty_without_dockerfile(project: Path) -> None:
    assert get_check("DOCKER003").run(make_context(project, {})) == []


def test_docker003_info_without_user(project: Path) -> None:
    ctx = make_context(project, {"Dockerfile": "FROM python:3.12\nRUN pip install x\n"})
    results = get_check("DOCKER003").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.INFO
    assert results[0].file == Path("Dockerfile")
    assert results[0].recommendation


def test_docker003_pass_with_user(project: Path) -> None:
    ctx = make_context(project, {"Dockerfile": "FROM python:3.12\nUSER app\n"})
    results = get_check("DOCKER003").run(ctx)
    assert results[0].severity is Severity.PASS


# DOCKER004


def test_docker004_empty_without_dockerfile(project: Path) -> None:
    assert get_check("DOCKER004").run(make_context(project, {})) == []


def test_docker004_warning_for_secret_env_name(project: Path) -> None:
    ctx = make_context(project, {"Dockerfile": "FROM python:3.12\nENV DB_PASSWORD=hunter2\n"})
    results = get_check("DOCKER004").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.WARNING
    assert results[0].file == Path("Dockerfile")
    assert results[0].line == 2
    assert "DB_PASSWORD" in results[0].message


def test_docker004_arg_and_legacy_env_forms(project: Path) -> None:
    ctx = make_context(project, {"Dockerfile": "ARG SECRET_KEY=abc\nENV APP_SECRET banana\n"})
    results = get_check("DOCKER004").run(ctx)
    assert len(results) == 2
    assert all(result.severity is Severity.WARNING for result in results)
    assert "SECRET_KEY" in results[0].message
    assert "APP_SECRET" in results[1].message


def test_docker004_never_reports_secret_values(project: Path) -> None:
    value = "supersecret123"
    ctx = make_context(
        project,
        {"Dockerfile": f"FROM python:3.12\nENV API_TOKEN={value}\nARG PRIVATE_KEY={value}\n"},
    )
    results = get_check("DOCKER004").run(ctx)
    assert len(results) == 2
    for result in results:
        assert value not in result.message
        assert value not in (result.recommendation or "")
        assert value not in str(result.file)


def test_docker004_pass_when_clean(project: Path) -> None:
    ctx = make_context(project, {"Dockerfile": "ENV APP_ENV=prod\nARG PORT\n"})
    results = get_check("DOCKER004").run(ctx)
    assert len(results) == 1
    assert results[0].severity is Severity.PASS


def test_checks_expose_category_and_ids() -> None:
    assert {check.category for check in CHECKS} == {"docker"}
    assert [check.id for check in CHECKS] == [
        "DOCKER001",
        "DOCKER002",
        "DOCKER003",
        "DOCKER004",
    ]
