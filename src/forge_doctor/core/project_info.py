"""``forge-doctor info`` data collection - fast project stats, no checks."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_TOOL_MARKERS = {
    "poetry.lock": "Poetry",
    "requirements.txt": "pip",
    "uv.lock": "uv",
    "Pipfile": "Pipenv",
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker Compose",
    ".pre-commit-config.yaml": "pre-commit",
    "Makefile": "make",
    "tox.ini": "tox",
}


@dataclass
class ProjectInfo:
    name: str
    path: str
    python_version: str | None
    project_version: str | None
    requires_python: str | None
    file_count: int
    line_count: int
    by_extension: list[tuple[str, int]]
    git_repo: bool
    tracked_files: int | None
    tools: list[str] = field(default_factory=list)


def collect_info(ctx: ProjectContext) -> ProjectInfo:
    counter: Counter[str] = Counter()
    total_lines = 0
    for relative in ctx.files:
        counter[relative.suffix.lower() or "(none)"] += 1
        text = ctx.read_text(relative, limit=200_000)
        if text is not None:
            total_lines += len(text.splitlines())

    pyproject: dict[str, Any] = ctx.pyproject or {}
    project = pyproject.get("project", {})
    poetry = pyproject.get("tool", {}).get("poetry", {})

    name = project.get("name") or poetry.get("name") or ctx.root.name
    version = project.get("version") or poetry.get("version")

    requires = project.get("requires-python")
    if not requires:
        deps = poetry.get("dependencies", {})
        requires = deps.get("python") if isinstance(deps, dict) else None

    tools = [label for marker, label in _TOOL_MARKERS.items() if ctx.has_file(marker)]
    if ctx.has_dir(".github/workflows"):
        tools.append("GitHub Actions")

    python = ctx.python_version
    tracked = ctx.git.tracked_files
    return ProjectInfo(
        name=str(name),
        path=str(ctx.root),
        python_version=".".join(map(str, python)) if python else None,
        project_version=str(version) if version else None,
        requires_python=str(requires) if requires else None,
        file_count=len(ctx.files),
        line_count=total_lines,
        by_extension=counter.most_common(8),
        git_repo=ctx.git.is_repo,
        tracked_files=len(tracked) if tracked is not None else None,
        tools=tools,
    )
