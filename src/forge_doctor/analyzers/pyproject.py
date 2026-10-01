"""Helpers for reading PEP 621 / Poetry fields out of a parsed pyproject."""

from __future__ import annotations

from typing import Any


def project_table(pyproject: dict[str, Any]) -> dict[str, Any]:
    project = pyproject.get("project")
    return project if isinstance(project, dict) else {}


def poetry_table(pyproject: dict[str, Any]) -> dict[str, Any]:
    poetry = pyproject.get("tool", {}).get("poetry")
    return poetry if isinstance(poetry, dict) else {}


def is_poetry_managed(pyproject: dict[str, Any]) -> bool:
    return "poetry" in pyproject.get("tool", {}) or "poetry" in str(
        pyproject.get("build-system", {}).get("requires", [])
    )


def requires_python(pyproject: dict[str, Any]) -> str | None:
    value = project_table(pyproject).get("requires-python")
    if isinstance(value, str):
        return value
    deps = poetry_table(pyproject).get("dependencies", {})
    if isinstance(deps, dict):
        python = deps.get("python")
        if isinstance(python, str):
            return python
        if isinstance(python, dict):
            version = python.get("version")
            if isinstance(version, str):
                return version
    return None


def declared_dependencies(pyproject: dict[str, Any]) -> dict[str, str]:
    """Map dependency name -> raw constraint string (PEP 621 + Poetry tables)."""
    deps: dict[str, str] = {}

    for item in project_table(pyproject).get("dependencies", []) or []:
        name, constraint = _split_pep508(item)
        if name:
            deps.setdefault(name, constraint)

    poetry_deps = poetry_table(pyproject).get("dependencies", {})
    if isinstance(poetry_deps, dict):
        for name, value in poetry_deps.items():
            if name.lower() == "python":
                continue
            deps.setdefault(name, _poetry_constraint(value))

    return deps


def dev_dependencies(pyproject: dict[str, Any]) -> dict[str, str]:
    """Dependencies declared in dev groups (PEP 735 + Poetry groups)."""
    deps: dict[str, str] = {}

    for item in pyproject.get("dependency-groups", {}).get("dev", []) or []:
        name, constraint = _split_pep508(item)
        if name:
            deps[name] = constraint

    groups = poetry_table(pyproject).get("group", {})
    if isinstance(groups, dict):
        for group in groups.values():
            group_deps = group.get("dependencies", {}) if isinstance(group, dict) else {}
            for name, value in group_deps.items():
                if name.lower() != "python":
                    deps[name] = _poetry_constraint(value)

    return deps


def _poetry_constraint(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        version = value.get("version")
        if isinstance(version, str):
            return version
    if isinstance(value, list):  # multiple constraints
        return ",".join(_poetry_constraint(v) for v in value)
    return ""


def _split_pep508(requirement: Any) -> tuple[str | None, str]:
    if not isinstance(requirement, str):
        return None, ""
    for marker in (";", "[", " ", "<", ">", "=", "!", "~"):
        if marker in requirement:
            name, _, rest = requirement.partition(marker)
            if marker == "[":
                return name.strip(), requirement
            return name.strip(), (marker + rest) if marker not in (";", " ", "[") else rest
    return requirement.strip(), ""
