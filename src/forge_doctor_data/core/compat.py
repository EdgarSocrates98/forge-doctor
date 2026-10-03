"""Environment detection + migration surface for ``forge-doctor-data compatibility``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from forge_doctor_data.core.knowledge import _version_sort, glue_current, glue_versions

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext

_DEP_SPEC_RE = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)\s*(?:==|>=|<=|~=|!=|>|<|\^)?\s*([0-9][0-9A-Za-z.*+,-]*)?"
)


@dataclass
class DetectedEnvironment:
    """What the project looks like today, best-effort."""

    glue: str | None = None
    spark: str | None = None
    python: str | None = None
    java: str | None = None
    iceberg: str | None = None
    sources: dict[str, str] = field(default_factory=dict)


def detect_environment(ctx: ProjectContext) -> DetectedEnvironment:
    """Infer runtimes from pyproject deps, requires-python, and Glue pins."""
    env = DetectedEnvironment()

    pyproject = ctx.pyproject or {}
    project = pyproject.get("project", {})
    requires = project.get("requires-python")
    if isinstance(requires, str) and requires.strip():
        env.python = _min_version(requires)
        env.sources["python"] = "requires-python"

    deps: list[str] = []
    raw_deps = project.get("dependencies", [])
    if isinstance(raw_deps, list):
        deps.extend(str(d) for d in raw_deps)
    poetry_deps = pyproject.get("tool", {}).get("poetry", {}).get("dependencies", {})
    if isinstance(poetry_deps, dict):
        deps.extend(f"{name} {spec}" for name, spec in poetry_deps.items() if isinstance(spec, str))
    for dep in deps:
        match = _DEP_SPEC_RE.match(dep)
        if match is None:
            continue
        name = match.group(1).lower().replace("-", "_")
        spec = match.group(2) or ""
        if name == "pyspark" and env.spark is None and spec:
            env.spark = _first_digits(spec)
            env.sources["spark"] = "pyspark dependency"
        elif name in {"pyiceberg", "iceberg"} and env.iceberg is None and spec:
            env.iceberg = _first_digits(spec)
            env.sources["iceberg"] = "pyiceberg dependency"

    glue_pins = _glue_version_pins(ctx)
    iac_pins = _iac_glue_pins(ctx)
    if glue_pins or iac_pins:
        env.glue = max(glue_pins | iac_pins, key=_version_sort)
        env.sources["glue"] = (
            "glue_version pin in code" if glue_pins else "terraform/cloudformation"
        )
        entry = glue_versions().get(env.glue, {})
        if env.spark is None and entry.get("spark"):
            env.spark = str(entry["spark"])
            env.sources["spark"] = f"Glue {env.glue} runtime"
        if env.java is None and entry.get("java"):
            env.java = str(entry["java"])
            env.sources["java"] = f"Glue {env.glue} runtime"
        if env.iceberg is None and entry.get("iceberg"):
            env.iceberg = str(entry["iceberg"])
            env.sources["iceberg"] = f"Glue {env.glue} runtime"
    return env


def _glue_version_pins(ctx: ProjectContext) -> set[str]:
    from forge_doctor_data.checks.glue import _VERSION_PREFIX, analyze_project

    buckets = analyze_project(ctx)
    return {
        pattern.removeprefix(_VERSION_PREFIX)
        for pattern in buckets
        if pattern.startswith(_VERSION_PREFIX)
    }


def _iac_glue_pins(ctx: ProjectContext) -> set[str]:
    """glue_version/GlueVersion pinned in Terraform or CloudFormation."""
    from forge_doctor_data.analyzers.hcl_lite import project_iac

    pins: set[str] = set()
    try:
        for resource in project_iac(ctx.files, ctx.root):
            if resource.type == "aws_glue_job" and resource.source == "terraform":
                value = resource.attrs.get("glue_version")
            elif resource.type == "AWS::Glue::Job":
                value = resource.attrs.get("GlueVersion")
            else:
                continue
            if value:
                pins.add(str(value).strip("\"'"))
    except Exception:
        return pins  # IaC pins are advisory - never break env detection
    return pins


def _min_version(specifier: str) -> str | None:
    """Lowest concrete version in a specifier like ``>=3.11,<4``."""
    versions = re.findall(r"\d+(?:\.\d+)*", specifier)
    return min(versions, key=_version_sort) if versions else None


def _first_digits(spec: str) -> str | None:
    match = re.search(r"\d+(?:\.\d+)*", spec)
    return match.group(0) if match else None


def migration_risks(
    source: str | None, target: str | None
) -> tuple[str, str, list[dict[str, str]]]:
    """Resolve ``(source, target, changes)`` with knowledge-pack defaults."""
    from forge_doctor_data.core.knowledge import glue_migration_changes

    known = sorted(glue_versions(), key=_version_sort)
    src = source or (known[0] if known else "4.0")
    dst = target or glue_current()
    return src, dst, glue_migration_changes(src, dst)
